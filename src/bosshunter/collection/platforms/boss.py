"""BOSS直聘 collector kept separate from the shared collection layer."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote

from bosshunter.browser import close_tab, evaluate, new_tab, scroll, wait_for_load
from bosshunter.collection.base import CollectionError, CollectorHooks
from bosshunter.collection.models import JobCandidate, PlatformCollectionRequest, PlatformCollectionResult
from bosshunter.config import CITY_CODES
from bosshunter.throttle import PageThrottle


SEARCH_URL = "https://www.zhipin.com/web/geek/job?query={keyword}&city={city_code}"

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
            title: nameEl.textContent.trim(), salary: salaryEl ? salaryEl.textContent.trim() : '',
            experience: tags[0] ? tags[0].textContent.trim() : '',
            education: tags[1] ? tags[1].textContent.trim() : '',
            company: companyEl ? companyEl.textContent.trim() : '',
            location: locationEl ? locationEl.textContent.trim() : '', url: href
        });
    });
    return JSON.stringify(jobs);
})()
"""

JS_EXTRACT_DETAIL = """
(() => {
    const info = {};
    info.title = document.querySelector('.info-primary .name h1')?.textContent?.trim()
        || document.querySelector('.name h1')?.textContent?.trim()
        || document.title.split('-')[0]?.trim();
    info.salary = document.querySelector('.info-primary .salary')?.textContent?.trim()
        || document.querySelector('.salary')?.textContent?.trim() || '';
    const tagItems = document.querySelectorAll('.info-primary .tag-list span');
    const tagTexts = Array.from(tagItems).map(t => t.textContent.trim());
    info.experience = tagTexts[0] || '';
    info.education = tagTexts[1] || '';
    info.jd = document.querySelector('.job-sec-text')?.textContent?.trim() || '';
    const companyLinks = document.querySelectorAll('.sider-company .company-info a');
    info.company = '';
    for (const link of companyLinks) {
        const text = link.textContent.trim();
        if (text && !text.includes('http')) { info.company = text; break; }
    }
    if (!info.company) {
        const titleMatch = document.title.match(/_(.+?)招聘/);
        info.company = titleMatch ? titleMatch[1] : '';
    }
    const companyTags = document.querySelectorAll('.sider-company .res-industry-item, .company-info-item');
    info.company_size = ''; info.company_industry = '';
    companyTags.forEach(tag => {
        const text = tag.textContent.trim();
        if (text.includes('人')) info.company_size = text;
        else if (!info.company_industry) info.company_industry = text;
    });
    const bossSection = document.querySelector('.boss-info-attr') || document.querySelector('.job-boss-info');
    info.hr_name = bossSection?.querySelector('.name')?.textContent?.trim() || '';
    info.hr_title = bossSection?.querySelector('.title')?.textContent?.trim() || '';
    info.hr_active = document.querySelector('.boss-active-time')?.textContent?.trim() || '';
    info.url = window.location.pathname;
    return JSON.stringify(info);
})()
"""


def generate_boss_job_id(url: str) -> str:
    match = re.search(r"/job_detail/([^.]+)", str(url or ""))
    if match:
        return match.group(1)
    return hashlib.md5(str(url or "").encode()).hexdigest()[:16]


def _wait_or_stop(stop_event, seconds: float, sleep: Callable[[float], None] = time.sleep) -> bool:
    if stop_event is not None:
        return stop_event.wait(seconds)
    sleep(seconds)
    return False


@dataclass
class BossBrowser:
    new_tab: Callable[..., str | None] = new_tab
    close_tab: Callable[[str], bool] = close_tab
    evaluate: Callable[..., Any] = evaluate
    scroll: Callable[..., bool] = scroll
    wait_for_load: Callable[..., bool] = wait_for_load


class BossCollector:
    platform = "boss"

    def __init__(
        self,
        *,
        browser: BossBrowser | None = None,
        throttle_factory: Callable[..., Any] = PageThrottle,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.browser = browser or BossBrowser()
        self.throttle_factory = throttle_factory
        self.sleep = sleep

    @staticmethod
    def resolve_city_code(city: str, request: PlatformCollectionRequest) -> str | None:
        return str(request.city_codes.get(city) or CITY_CODES.get(city) or "") or None

    def collect(self, request: PlatformCollectionRequest, hooks: CollectorHooks) -> PlatformCollectionResult:
        throttle = self.throttle_factory(delay_min=2.0, delay_max=5.0)
        combos: list[tuple[str, str, str]] = []
        for city in request.cities:
            city_code = self.resolve_city_code(city, request)
            if not city_code:
                hooks.on_event(phase="searching", city=city, reason_code="no_valid_city", message=f"未识别的 BOSS 城市：{city}")
                continue
            for keyword in request.keywords:
                combos.append((city, city_code, keyword))
        if not combos:
            return PlatformCollectionResult(
                self.platform, "completed_with_shortage", "no_valid_city", "没有有效的 BOSS 搜索组合"
            )

        for city, city_code, keyword in combos:
            if hooks.stop_event is not None and hooks.stop_event.is_set():
                return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
            for page in range(1, request.max_pages + 1):
                if hooks.stop_event is not None and hooks.stop_event.is_set():
                    return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                hooks.on_event(phase="loading_list", keyword=keyword, city=city, page=page)
                search_url = SEARCH_URL.format(keyword=quote(keyword), city_code=city_code)
                if request.sort == "newest":
                    search_url += "&sortType=2"
                if page > 1:
                    search_url += f"&page={page}"
                target_id = self.browser.new_tab(search_url, background=True)
                if not target_id:
                    return PlatformCollectionResult(self.platform, "failed", "browser_disconnected", "无法打开 BOSS 搜索页")
                try:
                    if _wait_or_stop(hooks.stop_event, 3, self.sleep):
                        return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                    self.browser.wait_for_load(target_id, timeout=10)
                    self.browser.scroll(target_id, y=2000)
                    if _wait_or_stop(hooks.stop_event, 1.5, self.sleep):
                        return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                    self.browser.scroll(target_id, y=4000)
                    result = self.browser.evaluate(target_id, JS_EXTRACT_LIST)
                finally:
                    self.browser.close_tab(target_id)
                try:
                    jobs = json.loads(result) if result else []
                except (json.JSONDecodeError, TypeError):
                    return PlatformCollectionResult(self.platform, "blocked", "selector_changed", "BOSS 列表解析失败，可能是页面结构变化")
                if not jobs:
                    break
                for raw in jobs:
                    if hooks.stop_event is not None and hooks.stop_event.is_set():
                        return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                    candidate = self._list_candidate(raw, city, city_code, keyword)
                    if not candidate or not hooks.on_list_candidate(candidate):
                        continue
                    if throttle.wait(hooks.stop_event):
                        return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                    detail_url = f"https://www.zhipin.com{candidate.url}"
                    detail_target = self.browser.new_tab(detail_url, background=True)
                    if not detail_target:
                        hooks.on_parse_failed("无法打开 BOSS 详情页")
                        continue
                    try:
                        if _wait_or_stop(hooks.stop_event, 2, self.sleep):
                            return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                        self.browser.wait_for_load(detail_target, timeout=10)
                        detail_result = self.browser.evaluate(detail_target, JS_EXTRACT_DETAIL)
                    finally:
                        self.browser.close_tab(detail_target)
                    try:
                        detail = json.loads(detail_result) if detail_result else None
                    except (json.JSONDecodeError, TypeError):
                        detail = None
                    if not isinstance(detail, dict):
                        hooks.on_parse_failed("BOSS 详情解析失败")
                        continue
                    merged = self._merge_detail(candidate, detail, detail_url)
                    if not merged.title or not merged.company or not merged.url or not merged.jd:
                        hooks.on_parse_failed("BOSS 详情缺少职位、公司、链接或 JD")
                        continue
                    if not hooks.on_candidate(merged):
                        return PlatformCollectionResult(self.platform, "completed", "target_reached", "已达到目标新增数量")
                else:
                    if page < request.max_pages and not (hooks.stop_event and hooks.stop_event.is_set()):
                        if _wait_or_stop(hooks.stop_event, 0.2, self.sleep):
                            return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                    continue
                if hooks.stop_event is not None and hooks.stop_event.is_set():
                    return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                break
        if request.target_count is not None:
            # The shared callback stops the adapter exactly at target_count.
            return PlatformCollectionResult(self.platform, "completed_with_shortage", "max_pages_reached", "已达到最大页数，新增岗位不足目标")
        return PlatformCollectionResult(self.platform, "completed", "search_exhausted", "BOSS 搜索结果已采集完毕")

    @staticmethod
    def _list_candidate(raw: Any, city: str, city_code: str, keyword: str) -> JobCandidate | None:
        if not isinstance(raw, dict):
            return None
        url = str(raw.get("url") or "").strip()
        if not url:
            return None
        return JobCandidate(
            platform="boss",
            source_job_id=generate_boss_job_id(url),
            title=str(raw.get("title") or "").strip(),
            company=str(raw.get("company") or "").strip(),
            salary=str(raw.get("salary") or "").strip(),
            city=city,
            city_code=city_code,
            experience=str(raw.get("experience") or "").strip(),
            education=str(raw.get("education") or "").strip(),
            url=url,
            source_keyword=keyword,
        )

    @staticmethod
    def _merge_detail(candidate: JobCandidate, detail: dict[str, Any], detail_url: str) -> JobCandidate:
        return JobCandidate(
            platform="boss",
            source_job_id=candidate.source_job_id,
            title=str(detail.get("title") or candidate.title).strip(),
            company=str(detail.get("company") or candidate.company).strip(),
            salary=str(detail.get("salary") or candidate.salary).strip(),
            city=candidate.city,
            city_code=candidate.city_code,
            experience=str(detail.get("experience") or candidate.experience).strip(),
            education=str(detail.get("education") or candidate.education).strip(),
            jd=str(detail.get("jd") or "").strip(),
            hr_name=str(detail.get("hr_name") or "").strip(),
            hr_title=str(detail.get("hr_title") or "").strip(),
            hr_active=str(detail.get("hr_active") or "").strip(),
            company_size=str(detail.get("company_size") or "").strip(),
            company_industry=str(detail.get("company_industry") or "").strip(),
            url=detail_url,
            source_keyword=candidate.source_keyword,
        )
