"""BOSS直聘 collector kept separate from the shared collection layer."""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote, urlencode

from bosshunter.ai.prefilter import quick_score
from bosshunter.browser import close_tab, evaluate, navigate, new_tab, scroll, wait_for_load
from bosshunter.collection.base import CollectorHooks
from bosshunter.collection.models import JobCandidate, PlatformCollectionRequest, PlatformCollectionResult
from bosshunter.config import CITY_CODES
from bosshunter.db import (
    add_risk_event,
    delete_page_progress,
    get_collected_combos,
    get_page_progress,
    mark_combo_collected,
    prune_collected_combos,
    prune_page_progress,
    upsert_page_progress,
)
from bosshunter.platform_safety import PlatformAccessGuard, PlatformSafetyStop
from bosshunter.throttle import PageThrottle


SEARCH_URL = "https://www.zhipin.com/web/geek/job?query={keyword}&city={city_code}"

BOSS_FILTER_OPTIONS: dict[str, dict[str, str]] = {
    "job_type": {"全职": "0", "兼职": "1", "实习": "2"},
    "experience": {
        "经验不限": "101", "应届生": "102", "1年以内": "103", "1-3年": "104",
        "3-5年": "105", "5-10年": "106", "10年以上": "107", "在校生": "108",
    },
    "degree": {
        "学历不限": "201", "大专": "202", "本科": "203", "硕士": "204",
        "博士": "205", "高中": "206", "中专/中技": "208", "初中及以下": "209",
    },
    "scale": {
        "0-20人": "301", "20-99人": "302", "100-499人": "303",
        "500-999人": "304", "1000-9999人": "305", "10000人以上": "306",
    },
    "salary": {
        "3K以下": "402", "3-5K": "403", "5-10K": "404",
        "10-20K": "405", "20-50K": "406", "50K以上": "407",
    },
}
BOSS_FILTER_PARAMS = {
    "job_type": "jobType",
    "experience": "experience",
    "degree": "degree",
    "scale": "scale",
    "salary": "salary",
    "industry": "industry",
}
_FILTER_SEPARATOR = re.compile(r"[,，、;；]")


def _filter_values(value: Any) -> list[str]:
    values = value if isinstance(value, list) else _FILTER_SEPARATOR.split(value) if isinstance(value, str) else []
    result: list[str] = []
    for item in values:
        cleaned = str(item or "").strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def normalize_boss_search_filters(filters: Any) -> dict[str, list[str]]:
    """Keep only documented BOSS filter labels and numeric industry codes."""
    if not isinstance(filters, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for key in BOSS_FILTER_PARAMS:
        values = _filter_values(filters.get(key))
        if key == "industry":
            values = [value for value in values if re.fullmatch(r"\d{1,12}", value)]
        else:
            values = [value for value in values if value in BOSS_FILTER_OPTIONS[key]]
        if values:
            normalized[key] = values
    return normalized


def build_boss_filter_query(filters: Any) -> str:
    """Build an encoded query fragment without allowing arbitrary parameters."""
    query: dict[str, str] = {}
    for key, values in normalize_boss_search_filters(filters).items():
        encoded_values = values if key == "industry" else [BOSS_FILTER_OPTIONS[key][value] for value in values]
        query[BOSS_FILTER_PARAMS[key]] = ",".join(encoded_values)
    return urlencode(query)

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
    const pageText = document.body?.innerText || '';
    info.recruitment_type = /校招|校园招聘|应届|毕业生|管培生|实习生/.test(pageText)
        ? 'campus'
        : (/社招|社会招聘/.test(pageText) ? 'experienced' : 'unknown');
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

JS_DETECT_COLLECTION_RISK = """
(() => {
    const text = (document.body?.innerText || '').slice(0, 10000);
    const url = String(location.href || '');
    const title = String(document.title || '');
    const hasExpectedContent = Boolean(document.querySelector(
        '.job-card-wrap, .job-sec-text, .job-detail, .job-primary'
    ));
    const captcha = document.querySelector(
        '.geetest_panel, .captcha, [class*="captcha"], [id*="captcha"], iframe[src*="captcha"], iframe[src*="verify"]'
    );
    if (captcha) return JSON.stringify({risk: 'captcha', evidence: 'captcha_element'});
    if (/captcha|security-check|\\/verify/i.test(url)) return JSON.stringify({risk: 'captcha', evidence: 'captcha_url'});
    if (/\\/web\\/user\\/(?:login|\\?ka=header-login)/i.test(url)) return JSON.stringify({risk: 'login_required', evidence: 'login_url'});
    if (/(?:^|[\\/?#=_-])(?:403|forbidden|access-denied)(?:$|[\\/?#=&_-])/i.test(url)) {
        return JSON.stringify({risk: 'blocked', evidence: 'blocked_url'});
    }
    if (/^(?:403(?:\\s+forbidden)?|forbidden|access denied|访问被拒绝|账号异常|账号受限)/i.test(title.trim())) {
        return JSON.stringify({risk: 'blocked', evidence: 'blocked_title'});
    }
    if (!hasExpectedContent && /验证码|安全验证|完成验证/.test(text)) {
        return JSON.stringify({risk: 'captcha', evidence: 'captcha_page'});
    }
    if (!hasExpectedContent && /(?:^|\\n)\\s*403(?:\\s+forbidden)?\\s*(?:\\n|$)|访问被拒绝|账号异常|账号受限/i.test(text)) {
        return JSON.stringify({risk: 'blocked', evidence: 'blocked_page'});
    }
    if (!hasExpectedContent && /操作频繁|访问频繁|请求频繁|稍后再试|频率限制/.test(text)) {
        return JSON.stringify({risk: 'rate_limit', evidence: 'rate_limit_page'});
    }
    return JSON.stringify({risk: null});
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


def _positive_int(value: object, default: int) -> int:
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return default


def _bounded_float(value: object, default: float, minimum: float, maximum: float) -> float:
    try:
        return min(max(float(value), minimum), maximum)
    except (TypeError, ValueError):
        return default


@dataclass
class BossBrowser:
    new_tab: Callable[..., str | None] = new_tab
    close_tab: Callable[[str], bool] = close_tab
    evaluate: Callable[..., Any] = evaluate
    navigate: Callable[..., bool] = navigate
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
        randint: Callable[[int, int], int] | None = None,
        config: dict[str, Any] | None = None,
        safety_conn: Any | None = None,
    ):
        self.browser = browser or BossBrowser()
        self.throttle_factory = throttle_factory
        self.sleep = sleep
        self.randint = randint or random.SystemRandom().randint
        self.config = config or {}
        self.safety_conn = safety_conn

    def _resume_ttl_hours(self) -> int:
        """断点续采有效期（默认 24h），与 51job/zhilian/liepin 同规则。"""
        search_cfg: dict[str, Any] = {}
        platforms = self.config.get("platforms", {})
        if isinstance(platforms, dict) and isinstance(platforms.get("boss"), dict):
            pf = platforms["boss"]
            if isinstance(pf.get("search"), dict):
                search_cfg = pf["search"]
        elif isinstance(self.config.get("search"), dict):
            search_cfg = self.config["search"]
        raw = search_cfg.get("resume_ttl_hours", 24)
        try:
            return max(1, min(int(raw or 24), 720))
        except (TypeError, ValueError):
            return 24

    @staticmethod
    def resolve_city_code(city: str, request: PlatformCollectionRequest) -> str | None:
        return str(request.city_codes.get(city) or CITY_CODES.get(city) or "") or None

    def collect(self, request: PlatformCollectionRequest, hooks: CollectorHooks) -> PlatformCollectionResult:
        collection_cfg = self.config.get("collection", {}) if isinstance(self.config.get("collection"), dict) else {}
        delay_multiplier = _bounded_float(
            collection_cfg.get("collection_delay_multiplier", 1.5),
            1.5,
            1.0,
            5.0,
        )
        throttle = self.throttle_factory(
            delay_min=2.0 * delay_multiplier,
            delay_max=5.0 * delay_multiplier,
        )
        guard = PlatformAccessGuard(self.safety_conn, self.config, "collection", "boss") if self.safety_conn is not None else None
        search_limit = _positive_int(collection_cfg.get("daily_search_page_limit", 60), 60)
        detail_limit = _positive_int(collection_cfg.get("daily_detail_page_limit", 150), 150)
        failure_limit = _positive_int(collection_cfg.get("max_consecutive_page_failures", 3), 3)
        risk_pause_min = _positive_int(collection_cfg.get("risk_pause_min_minutes", 5), 5)
        risk_pause_max = max(
            risk_pause_min,
            _positive_int(collection_cfg.get("risk_pause_max_minutes", 10), 10),
        )
        worker_target: str | None = None
        page_failures = 0

        def limited(reason: str) -> PlatformCollectionResult:
            return PlatformCollectionResult(
                self.platform, "completed_with_shortage", reason,
                f"BOSS 采集已达安全上限：{reason}",
            )

        def risk(kind: str, evidence: str = "") -> PlatformCollectionResult:
            labels = {
                "captcha": "BOSS 采集检测到验证码",
                "blocked": "BOSS 当前采集页连续检测到请求拦截（不代表账号封禁）",
                "rate_limit": "BOSS 采集检测到频率限制",
                "login_required": "BOSS 登录状态已失效",
            }
            pause_minutes = self.randint(risk_pause_min, risk_pause_max)
            label = labels.get(kind, "BOSS 采集检测到风险")
            evidence_note = f"；证据 {evidence}" if evidence else ""
            if self.safety_conn is not None:
                add_risk_event(
                    self.safety_conn,
                    f"collection_{kind}",
                    f"{label}{evidence_note}；冷却 {pause_minutes} 分钟",
                )
            if guard is not None:
                guard.lock(kind, minutes=pause_minutes)
            return PlatformCollectionResult(
                self.platform,
                "blocked",
                kind,
                f"{label}{evidence_note}；本轮已停止，冷却 {pause_minutes} 分钟后可重新开始",
            )

        def inspect_risk(target_id: str) -> dict[str, str] | None:
            raw = self.browser.evaluate(target_id, JS_DETECT_COLLECTION_RISK)
            try:
                value = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (json.JSONDecodeError, TypeError):
                value = {}
            if not isinstance(value, dict) or not value.get("risk"):
                return None
            return {
                "kind": str(value.get("risk")),
                "evidence": str(value.get("evidence") or "unspecified"),
            }

        def confirm_risk(target_id: str) -> dict[str, str] | None:
            first = inspect_risk(target_id)
            if first is None:
                return None
            if _wait_or_stop(hooks.stop_event, 1.0 * delay_multiplier, self.sleep):
                return {"kind": "user_stopped", "evidence": ""}
            second = inspect_risk(target_id)
            if second is None or second["kind"] != first["kind"]:
                return None
            return second

        def page_failure_stop() -> PlatformCollectionResult:
            if self.safety_conn is not None:
                add_risk_event(
                    self.safety_conn,
                    "collection_consecutive_page_failures",
                    "BOSS 连续页面失败，本轮采集已结束；未写入账号风险锁",
                )
            return PlatformCollectionResult(
                self.platform,
                "completed_with_shortage",
                "consecutive_page_failures",
                "BOSS 连续页面失败，本轮采集已结束；其他平台可继续",
            )

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

        # 词级断点续采：跳过最近 N 小时内已完成的 (city, keyword) 组合。
        collected_combos: set[tuple[str, str]] = set()
        if self.safety_conn is not None:
            all_keywords = set(request.keywords)
            prune_collected_combos(self.safety_conn, "boss", all_keywords)
            prune_page_progress(self.safety_conn, "boss", all_keywords)
            collected_combos = get_collected_combos(
                self.safety_conn, "boss", within_hours=self._resume_ttl_hours()
            )

        if guard is not None:
            try:
                guard.ensure_unlocked()
            except PlatformSafetyStop as exc:
                return limited(exc.reason)

        try:
            for city, city_code, keyword in combos:
                if hooks.stop_event is not None and hooks.stop_event.is_set():
                    return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                # 词级断点跳过：TTL 内已完成的组合整词跳过
                if (city, keyword) in collected_combos:
                    hooks.on_event(phase="completed_keyword", keyword=keyword, city=city,
                                   message=f"BOSS {keyword} 断点续采：{self._resume_ttl_hours()}h 内已完成，整词跳过")
                    continue
                # 页级断点：从上次采到页码的下一页继续
                saved_page = get_page_progress(self.safety_conn, "boss", city, keyword) if self.safety_conn is not None else 0
                start_page = saved_page + 1 if saved_page > 0 else 1
                if start_page > request.max_pages:
                    if self.safety_conn is not None:
                        mark_combo_collected(self.safety_conn, "boss", city, keyword)
                        delete_page_progress(self.safety_conn, "boss", city, keyword)
                    hooks.on_event(phase="completed_keyword", keyword=keyword, city=city,
                                   message=f"BOSS {keyword} 页级断点已超最大页（{saved_page}/{request.max_pages}），视为已采完，跳过")
                    continue
                for page in range(start_page, request.max_pages + 1):
                    if hooks.stop_event is not None and hooks.stop_event.is_set():
                        return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                    hooks.on_event(phase="loading_list", keyword=keyword, city=city, page=page)
                    search_url = SEARCH_URL.format(keyword=quote(keyword), city_code=city_code)
                    filter_query = build_boss_filter_query(request.filters)
                    if filter_query: search_url += f"&{filter_query}"
                    if request.sort == "newest": search_url += "&sortType=2"
                    if page > 1: search_url += f"&page={page}"
                    try:
                        if guard is not None: guard.reserve("search_page", daily_limit=search_limit)
                    except PlatformSafetyStop as exc:
                        return limited(exc.reason)
                    opened = self.browser.new_tab(search_url, background=True) if worker_target is None else self.browser.navigate(worker_target, search_url)
                    if worker_target is None and opened:
                        worker_target = str(opened)
                    if not opened or worker_target is None:
                        page_failures += 1
                        if page_failures >= failure_limit: return page_failure_stop()
                        continue
                    if _wait_or_stop(hooks.stop_event, 3 * delay_multiplier, self.sleep):
                        return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                    self.browser.wait_for_load(worker_target, timeout=10)
                    signal = confirm_risk(worker_target)
                    if signal and signal["kind"] == "user_stopped":
                        return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                    if signal: return risk(signal["kind"], signal["evidence"])
                    self.browser.scroll(worker_target, y=2000)
                    if _wait_or_stop(hooks.stop_event, 1.5 * delay_multiplier, self.sleep):
                        return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                    self.browser.scroll(worker_target, y=4000)
                    result = self.browser.evaluate(worker_target, JS_EXTRACT_LIST)
                    try:
                        jobs = json.loads(result) if result else []
                    except (json.JSONDecodeError, TypeError):
                        jobs = None
                    if not isinstance(jobs, list):
                        page_failures += 1
                        if page_failures >= failure_limit: return page_failure_stop()
                        hooks.on_parse_failed("BOSS 列表解析失败")
                        continue
                    page_failures = 0
                    if not jobs: break
                    for raw in jobs:
                        if hooks.stop_event is not None and hooks.stop_event.is_set():
                            return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                        candidate = self._list_candidate(raw, city, city_code, keyword)
                        if not candidate or not hooks.on_list_candidate(candidate): continue
                        if self.config and quick_score(raw if isinstance(raw, dict) else {}, self.config)[0] <= 0:
                            hooks.on_event(message="BOSS 列表预筛不通过", increment_filtered=True)
                            continue
                        if throttle.wait(hooks.stop_event):
                            return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                        detail_url = f"https://www.zhipin.com{candidate.url}"
                        try:
                            if guard is not None: guard.reserve("detail_page", daily_limit=detail_limit)
                        except PlatformSafetyStop as exc:
                            return limited(exc.reason)
                        if not self.browser.navigate(worker_target, detail_url):
                            page_failures += 1
                            hooks.on_parse_failed("无法打开 BOSS 详情页")
                            if page_failures >= failure_limit: return page_failure_stop()
                            continue
                        if _wait_or_stop(hooks.stop_event, 2 * delay_multiplier, self.sleep):
                            return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                        self.browser.wait_for_load(worker_target, timeout=10)
                        signal = confirm_risk(worker_target)
                        if signal and signal["kind"] == "user_stopped":
                            return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                        if signal: return risk(signal["kind"], signal["evidence"])
                        detail_result = self.browser.evaluate(worker_target, JS_EXTRACT_DETAIL)
                        try:
                            detail = json.loads(detail_result) if detail_result else None
                        except (json.JSONDecodeError, TypeError):
                            detail = None
                        if not isinstance(detail, dict):
                            page_failures += 1
                            hooks.on_parse_failed("BOSS 详情解析失败")
                            if page_failures >= failure_limit: return page_failure_stop()
                            continue
                        page_failures = 0
                        merged = self._merge_detail(candidate, detail, detail_url)
                        if not merged.title or not merged.company or not merged.url or not merged.jd:
                            hooks.on_parse_failed("BOSS 详情缺少职位、公司、链接或 JD")
                            continue
                        if not hooks.on_candidate(merged):
                            return PlatformCollectionResult(self.platform, "completed", "callback_stopped", "采集回调已停止")
                    # 页级断点：每采完一页立即记录页码
                    if self.safety_conn is not None:
                        upsert_page_progress(self.safety_conn, "boss", city, keyword, page)
                    if page < request.max_pages and _wait_or_stop(hooks.stop_event, 0.2 * delay_multiplier, self.sleep):
                        return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                # 词结束：标记词级断点 + 清页级断点
                if self.safety_conn is not None:
                    mark_combo_collected(self.safety_conn, "boss", city, keyword)
                    delete_page_progress(self.safety_conn, "boss", city, keyword)
        finally:
            if worker_target:
                self.browser.close_tab(worker_target)
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
            recruitment_type=str(detail.get("recruitment_type") or "unknown").strip(),
            jd=str(detail.get("jd") or "").strip(),
            hr_name=str(detail.get("hr_name") or "").strip(),
            hr_title=str(detail.get("hr_title") or "").strip(),
            hr_active=str(detail.get("hr_active") or "").strip(),
            company_size=str(detail.get("company_size") or "").strip(),
            company_industry=str(detail.get("company_industry") or "").strip(),
            url=detail_url,
            source_keyword=candidate.source_keyword,
        )
