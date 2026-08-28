"""Fail-closed 猎聘 collector for the shared multi-platform pipeline.

The collector intentionally implements collection only.  It does not attempt
to solve verification challenges, imitate human behaviour, send messages, or
resume automatically after a risk signal.  Any WAF, slider, silent throttle,
or unexpected page state stops the current platform run.

The selectors and URL shape below are candidate site patterns and must be
rechecked against an authorized, current browser session before being treated
as stable platform facts.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from bosshunter.browser import close_tab, evaluate, new_tab, scroll, wait_for_load
from bosshunter.browser import navigate as browser_navigate
from bosshunter.collection.base import CollectionError, CollectorHooks
from bosshunter.collection.models import JobCandidate, PlatformCollectionRequest, PlatformCollectionResult

SEARCH_URL = "https://www.liepin.com/zhaopin/?key={keyword}&dqs={city_code}"
DETAIL_DELAY_MIN_SECONDS = 12.0
DETAIL_DELAY_MAX_SECONDS = 20.0
PAGE_DELAY_MIN_SECONDS = 30.0
PAGE_DELAY_MAX_SECONDS = 45.0
RENDER_POLL_INTERVAL_SECONDS = 0.75
RENDER_POLL_ATTEMPTS = 10

CITY_SNAPSHOT = (
    {"name": "北京", "code": "010"},
    {"name": "上海", "code": "020"},
)


def load_liepin_city_snapshot() -> dict[str, Any]:
    return {
        "schema": "bosshunter.liepin_cities.v1",
        "source": "verified_snapshot",
        "note": "当前内置已核验的北京、上海城市编码；其他城市需核验后再加入。",
        "cities": [dict(item) for item in CITY_SNAPSHOT],
    }


def get_liepin_city_code(city: str) -> str | None:
    normalized = str(city or "").strip().removesuffix("市")
    for item in CITY_SNAPSHOT:
        if item["name"].removesuffix("市") == normalized:
            return item["code"]
    return None


JS_EXTRACT_LIST = r"""
(function () {
    var text = (document.body && document.body.innerText) || '';
    var blocked = /验证码|滑块|访问频繁|频率限制|账号异常|拒绝访问|请稍后再试/.test(text + ' ' + document.title);
    if (blocked) return JSON.stringify({status: 'blocked', jobs: []});
    var cards = Array.prototype.slice.call(document.querySelectorAll(
        '.job-info, .sidebar-job-list .job-info, .list-item-job, [data-job-id]'
    ));
    if (!cards.length) {
        var empty = /暂无相关职位|没有找到/.test(document.title || text);
        return JSON.stringify({status: empty ? 'empty' : 'waiting', jobs: []});
    }
    var jobs = [];
    for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var id = String(card.getAttribute('data-job-id') || '').trim();
        var titleNode = card.querySelector('.job-info h3 a, .job-title a, a.job-name, .job-info .job-title');
        var title = titleNode ? String(titleNode.innerText || '').trim() : '';
        var linkNode = card.querySelector('a[href*="/job/"], a[href*="liepin.com/job/"]');
        var jobUrl = linkNode ? String(linkNode.href || '').trim() : '';
        if (!id && linkNode) {
            var match = String(linkNode.getAttribute('href') || '').match(/\/job\/(\d+)\.shtml/);
            if (match) id = match[1];
        }
        if (!id || !title || !/^https:\/\/www\.liepin\.com\/job\//.test(jobUrl)) continue;
        var companyNode = card.querySelector('.company-info .company-name, .job-info .company-name, .company-name a, .comp-name');
        var company = companyNode ? String(companyNode.innerText || '').trim().split('\n')[0] : '';
        var salaryNode = card.querySelector('.job-info .condition .salary, .job-salary, .salary, .condition .salary');
        var salary = salaryNode ? String(salaryNode.innerText || '').trim() : '';
        var areaNode = card.querySelector('.job-info .condition .area, .job-area, .area, .condition .area');
        var city = areaNode ? String(areaNode.innerText || '').trim() : '';
        jobs.push({
            source_job_id: id,
            title: title,
            company: company,
            salary: salary,
            city: city,
            url: jobUrl
        });
    }
    return JSON.stringify({status: jobs.length ? 'ready' : 'selector_changed', jobs: jobs});
})()
"""


JS_EXTRACT_DETAIL = r"""
(function () {
    var body = (document.body && document.body.innerText) || '';
    var pageText = body + ' ' + (document.title || '');
    if (/验证码|滑块|访问频繁|频率限制|账号异常|拒绝访问|请稍后再试/.test(pageText)) {
        return JSON.stringify({status: 'blocked'});
    }
    if (/职位已下线|职位已关闭|职位不存在/.test(pageText)) {
        return JSON.stringify({status: 'offline'});
    }
    var jdNode = document.querySelector(
        '.job-intro-container .content, .job-description, .job-detail-content, '
        + '.content .job-detail, .job-intro, .responsibilities'
    );
    var jd = jdNode ? String(jdNode.innerText || '').replace(/\s+/g, ' ').trim() : '';
    var titleNode = document.querySelector('.job-title h1, .job-header h1, h1.job-name, h1');
    var salaryNode = document.querySelector('.job-title .salary, .job-header .salary, .salary');
    var companyNode = document.querySelector('.company-info .company-name, .company-name a, .comp-name a, .company-name');
    var areaNode = document.querySelector('.job-title .area, .job-header .area, .area, .location');
    return JSON.stringify({
        status: jd ? 'ready' : 'selector_changed',
        title: titleNode ? String(titleNode.innerText || '').trim() : '',
        salary: salaryNode ? String(salaryNode.innerText || '').trim() : '',
        company: companyNode ? String(companyNode.innerText || '').trim() : '',
        city: areaNode ? String(areaNode.innerText || '').trim() : '',
        jd: jd,
        url: location.href
    });
})()
"""


JS_CLICK_NEXT = r"""
(function () {
    var button = document.querySelector('.pager a.next, .pagination .next, a[data-page="next"], .page-next');
    if (!button) return false;
    var disabled = /disabled|is-disabled|norecord/.test(button.className || '') || button.getAttribute('aria-disabled') === 'true';
    if (disabled) return false;
    button.click();
    return true;
})()
"""


@dataclass
class LiepinBrowser:
    new_tab: Callable[..., str | None] = new_tab
    close_tab: Callable[[str], bool] = close_tab
    evaluate: Callable[..., Any] = evaluate
    scroll: Callable[..., bool] = scroll
    wait_for_load: Callable[..., bool] = wait_for_load
    navigate_action: Callable[[str, str], bool] | None = None


def _payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


class LiepinCollector:
    platform = "liepin"

    def __init__(
        self,
        *,
        browser: LiepinBrowser | None = None,
        sleep: Callable[[float], None] = time.sleep,
        uniform: Callable[[float, float], float] = random.SystemRandom().uniform,
        detail_delay_range: tuple[float, float] = (DETAIL_DELAY_MIN_SECONDS, DETAIL_DELAY_MAX_SECONDS),
        page_delay_range: tuple[float, float] = (PAGE_DELAY_MIN_SECONDS, PAGE_DELAY_MAX_SECONDS),
    ):
        self.browser = browser or LiepinBrowser(navigate_action=browser_navigate)
        self.sleep = sleep
        self.uniform = uniform
        self.detail_delay_range = detail_delay_range
        self.page_delay_range = page_delay_range

    @staticmethod
    def build_search_url(request: PlatformCollectionRequest, city: str, keyword: str) -> str:
        code = str(request.city_codes.get(city) or "").strip()
        if not code:
            raise CollectionError("no_valid_city", f"未配置猎聘城市编码：{city}")
        return SEARCH_URL.format(city_code=quote(code), keyword=quote(keyword))

    def _wait(self, hooks: CollectorHooks, seconds: float) -> bool:
        if hooks.stop_event is not None:
            return hooks.stop_event.wait(max(0.0, seconds))
        self.sleep(max(0.0, seconds))
        return False

    def collect(self, request: PlatformCollectionRequest, hooks: CollectorHooks) -> PlatformCollectionResult:
        detail_requests = 0
        for city in request.cities:
            if not request.city_codes.get(city):
                return PlatformCollectionResult(self.platform, "failed", "no_valid_city", f"猎聘城市编码未配置：{city}")
            for keyword in request.keywords:
                search_url = self.build_search_url(request, city, keyword)
                initial_url = "about:blank" if self.browser.navigate_action is not None else search_url
                target_id = self.browser.new_tab(initial_url, background=True)
                if not target_id:
                    return PlatformCollectionResult(self.platform, "failed", "browser_disconnected", "无法打开猎聘搜索页")
                if self.browser.navigate_action is not None and not self.browser.navigate_action(target_id, search_url):
                    return PlatformCollectionResult(self.platform, "failed", "browser_disconnected", "猎聘搜索页导航失败")
                try:
                    for page in range(1, request.max_pages + 1):
                        if hooks.stop_event is not None and hooks.stop_event.is_set():
                            return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                        if page > 1:
                            delay = self.uniform(*self.page_delay_range)
                            hooks.on_event(phase="pacing", keyword=keyword, city=city, page=page, message=f"翻页安全间隔 {delay:.1f} 秒")
                            if self._wait(hooks, delay):
                                return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                            if self.browser.evaluate(target_id, JS_CLICK_NEXT) is not True:
                                return PlatformCollectionResult(self.platform, "completed", "search_exhausted", "猎聘已到最后一页")
                        hooks.on_event(phase="loading_list", keyword=keyword, city=city, page=page)
                        self.browser.wait_for_load(target_id, timeout=15)
                        self.browser.scroll(target_id, y=2200)
                        payload: dict[str, Any] = {}
                        status = "waiting"
                        for attempt in range(RENDER_POLL_ATTEMPTS):
                            payload = _payload(self.browser.evaluate(target_id, JS_EXTRACT_LIST))
                            status = str(payload.get("status") or "selector_changed")
                            if status != "waiting":
                                break
                            if attempt + 1 < RENDER_POLL_ATTEMPTS and self._wait(hooks, RENDER_POLL_INTERVAL_SECONDS):
                                return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                        if status in {"blocked", "throttled"}:
                            return PlatformCollectionResult(self.platform, "blocked", "rate_limit", "猎聘出现验证或限流信号，已停止整个平台任务")
                        if status == "waiting":
                            return PlatformCollectionResult(self.platform, "blocked", "render_timeout", "猎聘列表未稳定渲染，已安全停止")
                        if status != "ready" or not isinstance(payload.get("jobs"), list):
                            return PlatformCollectionResult(self.platform, "blocked", "selector_changed", "猎聘列表页结构与预期不一致")

                        for raw_item in payload["jobs"]:
                            candidate = self._candidate_from_list(raw_item, city, keyword)
                            if candidate is None or not hooks.on_list_candidate(candidate):
                                continue
                            if detail_requests:
                                delay = self.uniform(*self.detail_delay_range)
                                hooks.on_event(phase="pacing", keyword=keyword, city=city, page=page, message=f"详情页安全间隔 {delay:.1f} 秒")
                                if self._wait(hooks, delay):
                                    return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                            hooks.on_event(phase="loading_detail", keyword=keyword, city=city, page=page)
                            detail_initial_url = "about:blank" if self.browser.navigate_action is not None else candidate.url
                            detail_target = self.browser.new_tab(detail_initial_url, background=True)
                            if not detail_target:
                                hooks.on_parse_failed("无法打开猎聘详情页")
                                continue
                            if self.browser.navigate_action is not None and not self.browser.navigate_action(detail_target, candidate.url):
                                self.browser.close_tab(detail_target)
                                hooks.on_parse_failed("猎聘详情页导航失败")
                                continue
                            detail_requests += 1
                            try:
                                self.browser.wait_for_load(detail_target, timeout=15)
                                detail: dict[str, Any] = {}
                                detail_status = "selector_changed"
                                for attempt in range(RENDER_POLL_ATTEMPTS):
                                    detail = _payload(self.browser.evaluate(detail_target, JS_EXTRACT_DETAIL))
                                    detail_status = str(detail.get("status") or "selector_changed")
                                    if detail_status in {"ready", "blocked", "offline"}:
                                        break
                                    if attempt + 1 < RENDER_POLL_ATTEMPTS and self._wait(hooks, RENDER_POLL_INTERVAL_SECONDS):
                                        return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                            finally:
                                self.browser.close_tab(detail_target)
                            if detail_status == "blocked":
                                return PlatformCollectionResult(self.platform, "blocked", "rate_limit", "猎聘详情页出现验证或限流，已停止整个平台任务")
                            if detail_status == "offline":
                                hooks.on_parse_failed("猎聘岗位已下线")
                                continue
                            if detail_status != "ready" or not str(detail.get("jd") or "").strip():
                                return PlatformCollectionResult(self.platform, "blocked", "selector_changed", "猎聘详情页结构变化，已安全停止")
                            final = self._candidate_from_detail(detail, candidate)
                            if not hooks.on_candidate(final):
                                return PlatformCollectionResult(self.platform, "completed", "callback_stopped", "采集回调已停止")
                finally:
                    self.browser.close_tab(target_id)
        return PlatformCollectionResult(self.platform, "completed", "search_exhausted", "猎聘搜索结果已采集完毕")

    @staticmethod
    def _candidate_from_list(raw: Any, city: str, keyword: str) -> JobCandidate | None:
        if not isinstance(raw, dict):
            return None
        source_id = str(raw.get("source_job_id") or "").strip()
        title = str(raw.get("title") or "").strip()
        url = str(raw.get("url") or "").strip()
        if not source_id or not title or not url.startswith("https://www.liepin.com/job/"):
            return None
        return JobCandidate(
            platform="liepin",
            source_job_id=source_id,
            title=title,
            company=str(raw.get("company") or "").strip(),
            salary=str(raw.get("salary") or "").strip(),
            city=str(raw.get("city") or city).strip(),
            url=url,
            source_keyword=keyword,
        )

    @staticmethod
    def _candidate_from_detail(detail: dict[str, Any], base: JobCandidate) -> JobCandidate:
        return JobCandidate(
            platform="liepin",
            source_job_id=base.source_job_id,
            title=str(detail.get("title") or base.title).strip(),
            company=str(detail.get("company") or base.company).strip(),
            salary=str(detail.get("salary") or base.salary).strip(),
            city=str(detail.get("city") or base.city).strip(),
            experience=base.experience,
            jd=str(detail.get("jd") or "").strip(),
            url=base.url,
            source_keyword=base.source_keyword,
        )