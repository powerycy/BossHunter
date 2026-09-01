"""Fail-closed 猎聘 (Liepin) collector for the shared multi-platform pipeline.

Collection only. It does not solve verification challenges, imitate human
behaviour, send messages, or resume automatically after a risk signal. Any
verification wall, silent throttle, or unexpected page state stops the current
platform run (fail-closed), following the same boundary as the 51job collector.

Verified live 2026-08-26:
- Search URL filters by city via the ``dqs`` parameter (the ``city`` parameter
  is ignored by Liepin and returns nationwide results).
- Job cards: ``a[data-nick="job-detail-job-info"]`` inside ``div.job-detail-box``.
  The job id is on the anchor's ``data-jobid`` attribute (fallback: the number
  in ``/job/<id>.shtml``).
- Liepin ships CSS-module hashed class names (e.g. ``_40108E8PWS``) that change
  on every deploy, so extraction relies on stable attributes / text structure,
  never on those hashed classes.
- Detail JD lives in ``section.job-intro-container`` when the session is shared
  with the ``wow.liepin.com`` subdomain; title in ``.job-title-box .ellipsis-1``;
  salary in ``.job-salary``; city in ``.job-dq-box .ellipsis-1``.
- 2026-08 起 ``www.liepin.com/job/<id>.shtml`` 会 302 跳转到 ``wow.liepin.com``，
  未在该子域登录时只返回登录墙。因此详情页 JD 为 best-effort：拿不到时仅保留
  列表已采集的线索（标题/公司/薪资/城市/链接），不中断整个平台任务。
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote

from bosshunter.browser import close_tab, evaluate, navigate as browser_navigate, new_tab, scroll, wait_for_load
from bosshunter.collection.base import CollectionError, CollectorHooks
from bosshunter.collection.models import JobCandidate, PlatformCollectionRequest, PlatformCollectionResult
from bosshunter.job_filters import matching_blocked_company, matching_deal_breaker
from bosshunter.throttle import SendWindowChecker, should_take_day_off


SEARCH_URL = "https://www.liepin.com/zhaopin/?key={keyword}&dqs={code}"
DETAIL_DELAY_MIN_SECONDS = 10.0
DETAIL_DELAY_MAX_SECONDS = 18.0
PAGE_DELAY_MIN_SECONDS = 20.0
PAGE_DELAY_MAX_SECONDS = 35.0
RENDER_POLL_INTERVAL_SECONDS = 0.75
RENDER_POLL_ATTEMPTS = 10

_INTERNSHIP_TITLE_TERMS = ("实习", "intern", "internship", "管培")

WAVE_SHORT_PROB = 0.60
WAVE_MEDIUM_PROB = 0.85

# Liepin uses 3-digit city codes passed via the ``dqs`` parameter. Only codes
# verified live are bundled; unknown cities are rejected instead of guessing.
CITY_SNAPSHOT = (
    {"name": "北京", "code": "010"},
    {"name": "上海", "code": "020"},
    {"name": "广州", "code": "050"},
    {"name": "深圳", "code": "060"},
    {"name": "杭州", "code": "070"},
    {"name": "成都", "code": "080"},
    {"name": "南京", "code": "090"},
    {"name": "苏州", "code": "100"},
    {"name": "武汉", "code": "110"},
    {"name": "天津", "code": "130"},
    {"name": "西安", "code": "270"},
    {"name": "重庆", "code": "040"},
    {"name": "长沙", "code": "190"},
    {"name": "郑州", "code": "170"},
    {"name": "青岛", "code": "200"},
    {"name": "厦门", "code": "230"},
    {"name": "合肥", "code": "250"},
    {"name": "大连", "code": "210"},
)


def load_liepin_city_snapshot() -> dict[str, Any]:
    return {
        "schema": "bosshunter.liepin_cities.v1",
        "source": "verified_snapshot",
        "note": "当前内置已核验的猎聘 3 位城市编码（dqs 参数）；其他城市需核验后再加入。",
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
    var pageText = text + ' ' + (document.title || '');
    if (/滑动验证|安全验证|访问过于频繁|请完成安全验证|人机验证|网络异常/.test(pageText)) {
        return JSON.stringify({status: 'blocked', jobs: []});
    }
    var anchors = Array.prototype.slice.call(document.querySelectorAll('a[data-nick="job-detail-job-info"]'));
    if (!anchors.length) {
        return JSON.stringify({status: /没有找到|暂无满足|未找到/.test(text) ? 'empty' : 'waiting', jobs: []});
    }
    var jobs = [];
    for (var i = 0; i < anchors.length; i++) {
        var a = anchors[i];
        var href = (a.href || '').trim();
        var id = (a.getAttribute('data-jobid') || '').trim();
        if (!id) { var m = href.match(/job\/(\d+)\.shtml/); id = m ? m[1] : ''; }
        if (!id || !/^https:\/\/www\.liepin\.com\/job\//.test(href)) continue;
        var titleEl = a.querySelector('.ellipsis-1');
        var title = titleEl ? String(titleEl.innerText || '').replace(/^招聘/, '').trim() : '';
        if (!title) continue;
        var box = a.closest('.job-detail-box') || a.parentElement;
        var compEl = box ? box.querySelector("div[data-nick='job-detail-company-info'] .ellipsis-1") : null;
        var company = compEl ? String(compEl.innerText || '').trim() : '';
        var atext = (a.innerText || '').replace(/\s+/g, ' ');
        var cityM = atext.match(/【\s*([^】]+?)\s*】/);
        var city = cityM ? cityM[1].trim() : '';
        var after = atext.split('】').slice(1).join('】').trim();
        var salM = after.match(/(\d+\s*-\s*\d+\s*k[·\d\s薪]*|\d+\s*k[·\d\s薪]*|面议|薪资面议)/i);
        var salary = salM ? salM[0].replace(/\s+/g, '') : '';
        var rest = salM ? after.slice(after.indexOf(salM[0]) + salM[0].length).trim() : after;
        var parts = rest.split(/\s+/).filter(Boolean);
        jobs.push({
            source_job_id: id,
            title: title,
            company: company,
            salary: salary,
            city: city,
            experience: parts[0] || '',
            education: parts[1] || '',
            url: href
        });
    }
    return JSON.stringify({status: jobs.length ? 'ready' : 'selector_changed', jobs: jobs});
})()
"""


JS_EXTRACT_DETAIL = r"""
(function () {
    var body = (document.body && document.body.innerText) || '';
    var pageText = body + ' ' + (document.title || '');
    if (/滑动验证|安全验证|访问过于频繁|请完成安全验证|人机验证/.test(pageText)) {
        return JSON.stringify({status: 'blocked'});
    }
    if (/职位不存在|职位已下线|该职位已关闭|页面不存在|职位已暂停/.test(pageText)) {
        return JSON.stringify({status: 'offline'});
    }
    // Liepin 302-redirects detail URLs to the wow.liepin.com subdomain, which
    // serves a login/interstitial wall unless the session is shared there.
    // Treat that as a login gate (soft skip), not a page-structure break.
    if (location.hostname.indexOf('wow.liepin.com') >= 0) {
        return JSON.stringify({status: 'login_required'});
    }
    if (document.title === '猎聘' && (body || '').length < 400) {
        return JSON.stringify({status: 'login_required'});
    }
    var jdNode = document.querySelector('section.job-intro-container');
    var jd = jdNode ? String(jdNode.innerText || '').replace(/\s+/g, ' ').trim() : '';
    jd = jd.replace(/^职位介绍/, '').trim();
    var titleNode = document.querySelector('.job-title-box .ellipsis-1') || document.querySelector('h1');
    var salaryNode = document.querySelector('.job-salary');
    var cityNode = document.querySelector('.job-dq-box .ellipsis-1');
    var companyNode = document.querySelector('.company-info-container .ellipsis-1') || document.querySelector("div[data-nick='job-detail-company-info'] .ellipsis-1");
    return JSON.stringify({
        status: jd ? 'ready' : 'selector_changed',
        title: titleNode ? String(titleNode.innerText || '').trim() : '',
        salary: salaryNode ? String(salaryNode.innerText || '').replace(/\s+/g, '').trim() : '',
        company: companyNode ? String(companyNode.innerText || '').trim() : '',
        city: cityNode ? String(cityNode.innerText || '').trim() : '',
        jd: jd,
        url: location.href
    });
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


def _is_internship(title: str) -> bool:
    """判断岗位是否为实习/管培（只查标题）。"""
    t = (title or "").lower()
    return any(s in t for s in _INTERNSHIP_TITLE_TERMS)


class LiepinCollector:
    platform = "liepin"

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        safety_conn: Any | None = None,
        browser: LiepinBrowser | None = None,
        sleep: Callable[[float], None] = time.sleep,
        uniform: Callable[[float, float], float] = random.SystemRandom().uniform,
        detail_delay_range: tuple[float, float] = (DETAIL_DELAY_MIN_SECONDS, DETAIL_DELAY_MAX_SECONDS),
        page_delay_range: tuple[float, float] = (PAGE_DELAY_MIN_SECONDS, PAGE_DELAY_MAX_SECONDS),
    ):
        self.config = config or {}
        self.safety_conn = safety_conn
        self.browser = browser or LiepinBrowser(navigate_action=browser_navigate)
        self.sleep = sleep
        self.uniform = uniform
        self.detail_delay_range = detail_delay_range
        self.page_delay_range = page_delay_range

    @staticmethod
    def build_search_url(request: PlatformCollectionRequest, city: str, keyword: str, page: int = 1) -> str:
        code = str(request.city_codes.get(city) or "").strip()
        if not code:
            raise CollectionError("no_valid_city", f"未配置猎聘城市编码：{city}")
        url = SEARCH_URL.format(keyword=quote(keyword), code=quote(code))
        if page and page > 1:
            url += f"&curPage={page - 1}"
        if request.sort == "newest":
            # Liepin's "最新" ordering. Harmless if the param is ignored.
            url += "&sortType=2"
        return url

    def _wait(self, hooks: CollectorHooks, seconds: float) -> bool:
        if hooks.stop_event is not None:
            return hooks.stop_event.wait(max(0.0, seconds))
        self.sleep(max(0.0, seconds))
        return False

    def _navigate(self, hooks: CollectorHooks, target_id: str, url: str, label: str) -> bool:
        """Navigate with bounded retries so a transient CDP/page hiccup does not
        abort the entire platform run. Returns True when navigation commits."""
        nav = self.browser.navigate_action
        if nav is None:
            return True
        for attempt in range(1, 4):
            if hooks.stop_event is not None and hooks.stop_event.is_set():
                return False
            if nav(target_id, url):
                return True
            if attempt < 3:
                hooks.on_event(phase="pacing", message=f"{label}导航重试 {attempt}/3")
                self._wait(hooks, 2.0)
        return False

    def _passes_filters(self, candidate: JobCandidate) -> bool:
        """collector 增值过滤：deal_breakers / blocked_company / 实习 / 薪资。"""
        profile = self.config.get("profile", {}) if isinstance(self.config.get("profile"), dict) else {}
        if matching_deal_breaker(candidate.title, profile.get("deal_breakers") or []):
            return False
        if matching_blocked_company(candidate.company, profile.get("blocked_companies") or []):
            return False
        if matching_deal_breaker(candidate.jd, profile.get("jd_deal_breakers") or []):
            return False
        allow_internship = bool(profile.get("allow_internship", False))
        if not allow_internship and _is_internship(candidate.title):
            return False
        return True

    def collect(self, request: PlatformCollectionRequest, hooks: CollectorHooks) -> PlatformCollectionResult:
        for city in request.cities:
            if not request.city_codes.get(city):
                return PlatformCollectionResult(self.platform, "failed", "no_valid_city", f"猎聘城市编码未配置：{city}")

        throttle_cfg = self.config.get("throttle", {}) if isinstance(self.config.get("throttle"), dict) else {}
        send_windows = throttle_cfg.get("send_windows", ["09:00-16:00"])
        if not SendWindowChecker(send_windows).is_active():
            return PlatformCollectionResult(self.platform, "completed", "outside_window",
                                            f"当前不在采集时间窗口内（{send_windows}）")
        if should_take_day_off(float(throttle_cfg.get("day_off_probability", 0.05))):
            return PlatformCollectionResult(self.platform, "completed", "day_off",
                                            "今日随机休息，跳过猎聘采集")

        for city in request.cities:
            for keyword in request.keywords:
                search_url = self.build_search_url(request, city, keyword, page=1)
                initial_url = "about:blank" if self.browser.navigate_action is not None else search_url
                target_id = self.browser.new_tab(initial_url, background=True)
                if not target_id:
                    return PlatformCollectionResult(self.platform, "failed", "browser_disconnected", "无法打开猎聘搜索页")
                if not self._navigate(hooks, target_id, search_url, "猎聘搜索页"):
                    return PlatformCollectionResult(self.platform, "failed", "browser_disconnected", "猎聘搜索页导航失败")
                try:
                    for page in range(1, request.max_pages + 1):
                        if hooks.stop_event is not None and hooks.stop_event.is_set():
                            return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                        if page > 1:
                            detail_req = self.uniform(*self.page_delay_range)
                            hooks.on_event(phase="pacing", keyword=keyword, city=city, page=page, message=f"翻页安全间隔 {detail_req:.1f} 秒")
                            if self._wait(hooks, detail_req):
                                return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                            if not self._navigate(hooks, target_id, self.build_search_url(request, city, keyword, page=page), "猎聘翻页"):
                                return PlatformCollectionResult(self.platform, "failed", "browser_disconnected", "猎聘翻页导航失败")
                        hooks.on_event(phase="loading_list", keyword=keyword, city=city, page=page)
                        self.browser.wait_for_load(target_id, timeout=15)
                        self.browser.scroll(target_id, y=1800)
                        payload: dict[str, Any] = {}
                        status = "waiting"
                        for attempt in range(RENDER_POLL_ATTEMPTS):
                            payload = _payload(self.browser.evaluate(target_id, JS_EXTRACT_LIST))
                            status = str(payload.get("status") or "selector_changed")
                            if status != "waiting":
                                break
                            if attempt + 1 < RENDER_POLL_ATTEMPTS and self._wait(hooks, RENDER_POLL_INTERVAL_SECONDS):
                                return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                        if status == "blocked":
                            return PlatformCollectionResult(self.platform, "blocked", "rate_limit", "猎聘出现验证或限流信号，已停止整个平台任务")
                        if status in {"empty", "waiting"}:
                            return PlatformCollectionResult(self.platform, "completed", "search_exhausted", "猎聘已无更多结果")
                        if status != "ready" or not isinstance(payload.get("jobs"), list):
                            return PlatformCollectionResult(self.platform, "blocked", "selector_changed", "猎聘列表页结构与预期不一致")

                        for raw_item in payload["jobs"]:
                            candidate = self._candidate_from_list(raw_item, city, keyword)
                            if candidate is None:
                                continue
                            if not self._passes_filters(candidate):
                                continue
                            if not hooks.on_list_candidate(candidate):
                                continue
                            detail_req = self.uniform(*self.detail_delay_range)
                            hooks.on_event(phase="pacing", keyword=keyword, city=city, page=page, message=f"详情页安全间隔 {detail_req:.1f} 秒")
                            if self._wait(hooks, detail_req):
                                return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                            hooks.on_event(phase="loading_detail", keyword=keyword, city=city, page=page)
                            detail_initial_url = "about:blank" if self.browser.navigate_action is not None else candidate.url
                            detail_target = self.browser.new_tab(detail_initial_url, background=True)
                            if not detail_target:
                                hooks.on_parse_failed("无法打开猎聘详情页")
                                continue
                            if not self._navigate(hooks, detail_target, candidate.url, "猎聘详情页"):
                                self.browser.close_tab(detail_target)
                                hooks.on_parse_failed("猎聘详情页导航失败")
                                continue
                            try:
                                self.browser.wait_for_load(detail_target, timeout=15)
                                self.browser.scroll(detail_target, y=1200)
                                detail: dict[str, Any] = {}
                                detail_status = "selector_changed"
                                for attempt in range(RENDER_POLL_ATTEMPTS):
                                    detail = _payload(self.browser.evaluate(detail_target, JS_EXTRACT_DETAIL))
                                    detail_status = str(detail.get("status") or "selector_changed")
                                    if detail_status in {"ready", "blocked", "offline", "login_required"}:
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
                            if detail_status == "login_required":
                                hooks.on_parse_failed("猎聘详情页需登录(wow.liepin.com)，仅保存列表信息")
                                if not hooks.on_candidate(candidate):
                                    return PlatformCollectionResult(self.platform, "completed", "callback_stopped", "采集回调已停止")
                                continue
                            if detail_status == "ready" and str(detail.get("jd") or "").strip():
                                final = self._candidate_from_detail(detail, candidate)
                                if not hooks.on_candidate(final):
                                    return PlatformCollectionResult(self.platform, "completed", "callback_stopped", "采集回调已停止")
                                continue
                            # 详情页 JD 不可用（职位簇/跳转登录墙）：保留列表已采集的
                            # 岗位线索（标题/公司/薪资/城市），不中断整个平台任务。
                            hooks.on_parse_failed("猎聘详情页JD不可用，仅保存列表信息")
                            if not hooks.on_candidate(candidate):
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
        url = str(raw.get("url") or "").strip().split("?")[0]
        if not source_id or not title or not url.startswith("https://www.liepin.com/job/"):
            return None
        return JobCandidate(
            platform="liepin",
            source_job_id=source_id,
            title=title,
            company=str(raw.get("company") or "").strip(),
            salary=str(raw.get("salary") or "").strip(),
            city=str(raw.get("city") or city).strip(),
            experience=str(raw.get("experience") or "").strip(),
            education=str(raw.get("education") or "").strip(),
            url=url,
            source_keyword=keyword,
        )

    @staticmethod
    def _candidate_from_detail(detail: dict[str, Any], base: JobCandidate) -> JobCandidate:
        # Liepin detail pages are "job clusters": the rendered detail identity
        # (title/company/salary) can rotate per load and disagree with the search
        # card. The search card is the stable identity; the detail page is only
        # trusted for the JD body (section.job-intro-container is unique to the
        # main job). Keep every other field from the list candidate.
        return JobCandidate(
            platform="liepin",
            source_job_id=base.source_job_id,
            title=base.title,
            company=base.company,
            salary=base.salary,
            city=base.city,
            experience=base.experience,
            education=base.education,
            jd=str(detail.get("jd") or base.jd or "").strip(),
            url=base.url,
            source_keyword=base.source_keyword,
        )
