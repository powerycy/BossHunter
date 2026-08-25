"""Job scraping module - Extract jobs from BOSS直聘 search results."""

import json
import random
import re
import time
import hashlib
from urllib.parse import quote

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from bosshunter.browser import (
    new_tab, close_tab, evaluate, scroll, wait_for_load
)
from bosshunter.config import CITY_CODES
from bosshunter.cancellation import get_stop_event
from bosshunter.db import get_db, job_exists, insert_job
from bosshunter.job_filters import matching_blocked_company, matching_deal_breaker
from bosshunter.throttle import PageThrottle

console = Console()

# BOSS直聘搜索页 URL 模板
SEARCH_URL = "https://www.zhipin.com/web/geek/job?query={keyword}&city={city_code}"


def _resolve_city_code(city: str, config: dict) -> str | None:
    custom_codes = config.get("search", {}).get("city_codes", {})
    if isinstance(custom_codes, dict) and custom_codes.get(city):
        return str(custom_codes[city])
    return CITY_CODES.get(city)


# JS: 从搜索列表页提取岗位卡片数据
JS_EXTRACT_LIST = """
(() => {
    const wraps = document.querySelectorAll('.job-card-wrap');
    const jobs = [];
    wraps.forEach((wrap) => {
        const box = wrap.querySelector('.job-card-box') || wrap;
        const nameEl = box.querySelector('.job-name');
        const salaryEl = box.querySelector('.job-salary');
        const tags = box.querySelectorAll('.tag-list li');
        const companyEl = box.querySelector('.boss-name') || box.querySelector('.company-name');
        const locationEl = box.querySelector('.company-location');
        const href = nameEl ? nameEl.getAttribute('href') : '';

        if (!nameEl || !href) return;

        jobs.push({
            title: nameEl.textContent.trim(),
            salary: salaryEl ? salaryEl.textContent.trim() : '',
            experience: tags[0] ? tags[0].textContent.trim() : '',
            education: tags[1] ? tags[1].textContent.trim() : '',
            company: companyEl ? companyEl.textContent.trim() : '',
            location: locationEl ? locationEl.textContent.trim() : '',
            url: href
        });
    });
    return JSON.stringify(jobs);
})()
"""

# JS: 从详情页提取完整岗位信息
JS_EXTRACT_DETAIL = """
(() => {
    const info = {};
    // Title and salary
    info.title = document.querySelector('.info-primary .name h1')?.textContent?.trim()
        || document.querySelector('.name h1')?.textContent?.trim()
        || document.title.split('-')[0]?.trim();
    info.salary = document.querySelector('.info-primary .salary')?.textContent?.trim()
        || document.querySelector('.salary')?.textContent?.trim() || '';

    // Tags (experience, education, etc)
    const tagItems = document.querySelectorAll('.info-primary .tag-list span');
    const tagTexts = Array.from(tagItems).map(t => t.textContent.trim());
    info.experience = tagTexts[0] || '';
    info.education = tagTexts[1] || '';

    // JD
    info.jd = document.querySelector('.job-sec-text')?.textContent?.trim() || '';

    // Company info - try multiple selectors
    const companyLinks = document.querySelectorAll('.sider-company .company-info a');
    info.company = '';
    for (const link of companyLinks) {
        const text = link.textContent.trim();
        if (text && text.length > 0 && !text.includes('http')) {
            info.company = text;
            break;
        }
    }
    if (!info.company) {
        // Fallback: extract from page title "「职位」_公司名招聘"
        const titleMatch = document.title.match(/_(.+?)招聘/);
        info.company = titleMatch ? titleMatch[1] : '';
    }

    // Company details
    const companyTags = document.querySelectorAll('.sider-company .res-industry-item, .company-info-item');
    info.company_size = '';
    info.company_industry = '';
    companyTags.forEach(tag => {
        const text = tag.textContent.trim();
        if (text.includes('人')) info.company_size = text;
        else if (!info.company_industry) info.company_industry = text;
    });

    // HR info
    const bossSection = document.querySelector('.boss-info-attr') || document.querySelector('.job-boss-info');
    if (bossSection) {
        const nameEl = bossSection.querySelector('.name');
        const titleEl = bossSection.querySelector('.title');
        info.hr_name = nameEl?.textContent?.trim() || '';
        info.hr_title = titleEl?.textContent?.trim() || '';
    } else {
        info.hr_name = '';
        info.hr_title = '';
    }
    info.hr_active = document.querySelector('.boss-active-time')?.textContent?.trim() || '';

    // URL
    info.url = window.location.pathname;

    return JSON.stringify(info);
})()
"""


def _generate_job_id(url: str) -> str:
    """Generate a unique job ID from URL path."""
    # Extract the unique part from /job_detail/xxx.html
    match = re.search(r'/job_detail/([^.]+)', url)
    if match:
        return match.group(1)
    return hashlib.md5(url.encode()).hexdigest()[:16]


def _wait_or_stop(stop_event, seconds: float) -> bool:
    if stop_event is not None:
        return stop_event.wait(seconds)
    time.sleep(seconds)
    return False


def scrape_jobs(
    config: dict,
    keywords: list[str],
    limit: int | None = None,
    *,
    collected_job_ids: list[str] | None = None,
) -> int:
    """Scrape jobs from BOSS直聘 and store in database.

    Supports multi-keyword × multi-city combinations with pagination.
    When limit is None, collection is bounded only by city × keyword × max_pages.
    Returns the number of new jobs added.
    """
    db = get_db()
    stop_event = get_stop_event(config)
    throttle = PageThrottle(delay_min=2.0, delay_max=5.0)
    deal_breakers = config.get("profile", {}).get("deal_breakers", [])
    jd_deal_breakers = config.get("profile", {}).get("jd_deal_breakers", [])
    progress_callback = config.get("_workbench_collect_progress")
    seen_count = 0
    blocked_companies = config.get("profile", {}).get("blocked_companies", [])
    new_count = 0
    duplicate_count = 0

    def report_progress() -> None:
        if callable(progress_callback):
            progress_callback({"seen": seen_count, "new": new_count, "duplicate": duplicate_count})

    # Pagination config
    search_config = config.get("search", {})
    max_pages = min(search_config.get("max_pages", 3), 10)  # Hard cap: 10 pages

    # Resolve cities: search.cities > profile.target_cities > ["北京"]
    cities = search_config.get("cities", [])
    if not cities:
        cities = config.get("profile", {}).get("target_cities", ["北京"])

    # Build search combinations: city × keyword
    search_combos = []
    for city in cities:
        city_code = _resolve_city_code(city, config)
        if not city_code:
            console.print(f"[yellow]⚠ 未识别的城市: {city}，已跳过[/yellow]")
            continue
        for keyword in keywords:
            search_combos.append((city, city_code, keyword))

    if not search_combos:
        console.print("[red]没有有效的搜索组合（检查城市配置）[/red]")
        db.close()
        return 0

    if stop_event is not None and stop_event.is_set():
        db.close()
        return 0

    console.print(f"[dim]搜索组合: {len(search_combos)} 个 ({len(cities)}城市 × {len(keywords)}关键词 × {max_pages}页)[/dim]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        for city, city_code, keyword in search_combos:
            if stop_event is not None and stop_event.is_set():
                break
            if limit is not None and new_count >= limit:
                break

            label = f"{city}/{keyword}" if len(cities) > 1 else keyword
            task = progress.add_task(f"搜索: {label}", total=None)
            keyword_new = 0

            for page in range(1, max_pages + 1):
                if stop_event is not None and stop_event.is_set():
                    break
                if limit is not None and new_count >= limit:
                    break

                # Build paginated URL
                search_url = SEARCH_URL.format(keyword=quote(keyword), city_code=city_code)
                sort_mode = search_config.get("sort", "")
                if sort_mode == "newest":
                    search_url += "&sortType=2"
                if page > 1:
                    search_url += f"&page={page}"

                # Open search page
                target_id = new_tab(search_url, background=True)
                if not target_id:
                    if page == 1:
                        progress.update(task, description=f"[red]✗ 无法打开搜索页: {label}[/red]")
                    break

                if _wait_or_stop(stop_event, 3):
                    close_tab(target_id)
                    break
                wait_for_load(target_id, timeout=10)
                if stop_event is not None and stop_event.is_set():
                    close_tab(target_id)
                    break

                # Scroll to load all results on this page
                scroll(target_id, y=2000)
                if _wait_or_stop(stop_event, 1.5):
                    close_tab(target_id)
                    break
                scroll(target_id, y=4000)
                if _wait_or_stop(stop_event, 1.5):
                    close_tab(target_id)
                    break

                # Extract job list
                result = evaluate(target_id, JS_EXTRACT_LIST)
                if not result:
                    close_tab(target_id)
                    break

                try:
                    jobs_list = json.loads(result)
                except (json.JSONDecodeError, TypeError):
                    close_tab(target_id)
                    break

                close_tab(target_id)

                # No results on this page, stop pagination
                if not jobs_list:
                    break

                progress.update(task, description=f"搜索: {label} 第{page}页 ({len(jobs_list)}条)")

                # Process each job
                for job_data in jobs_list:
                    if stop_event is not None and stop_event.is_set():
                        break
                    if limit is not None and new_count >= limit:
                        break

                    seen_count += 1
                    report_progress()
                    job_url = job_data.get("url", "")
                    job_id = _generate_job_id(job_url)

                    # Skip if already exists
                    if job_exists(db, job_id):
                        duplicate_count += 1
                        report_progress()
                        continue

                    # Skip deal breakers
                    if matching_deal_breaker(job_data.get("title", ""), deal_breakers):
                        continue
                    if matching_blocked_company(job_data.get("company", ""), blocked_companies):
                        continue

                    # Open detail page for full JD
                    if throttle.wait(stop_event):
                        break
                    detail_url = f"https://www.zhipin.com{job_url}"
                    detail_target = new_tab(detail_url, background=True)
                    if not detail_target:
                        continue

                    if _wait_or_stop(stop_event, 2):
                        close_tab(detail_target)
                        break
                    wait_for_load(detail_target, timeout=10)
                    if stop_event is not None and stop_event.is_set():
                        close_tab(detail_target)
                        break

                    # Extract detail
                    detail_result = evaluate(detail_target, JS_EXTRACT_DETAIL)
                    close_tab(detail_target)

                    if not detail_result:
                        continue

                    try:
                        detail = json.loads(detail_result)
                    except (json.JSONDecodeError, TypeError):
                        continue

                    # Build job record
                    job_record = {
                        "id": job_id,
                        "title": detail.get("title", job_data.get("title", "")),
                        "company": detail.get("company", job_data.get("company", "")),
                        "salary": detail.get("salary", job_data.get("salary", "")),
                        "city": city,
                        "experience": detail.get("experience", job_data.get("experience", "")),
                        "jd": detail.get("jd", ""),
                        "hr_name": detail.get("hr_name", ""),
                        "hr_title": detail.get("hr_title", ""),
                        "hr_active": detail.get("hr_active", ""),
                        "company_size": detail.get("company_size", ""),
                        "company_industry": detail.get("company_industry", ""),
                        "url": detail_url,
                    }

                    if matching_deal_breaker(job_record["jd"], jd_deal_breakers):
                        continue

                    insert_job(db, job_record)
                    if collected_job_ids is not None:
                        collected_job_ids.append(job_id)
                    new_count += 1
                    keyword_new += 1
                    report_progress()
                    progress.update(task, description=f"搜索: {label} 第{page}页 (新增 {keyword_new})")

                # Anti-scraping: pause between pages
                if page < max_pages:
                    if _wait_or_stop(stop_event, random.uniform(3.0, 6.0)):
                        break

            progress.update(task, description=f"搜索: {label} (新增 {keyword_new})")

    report_progress()
    db.close()
    return new_count
