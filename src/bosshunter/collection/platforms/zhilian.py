"""智联招聘 collector and offline HTML parser.

The selectors and URL shape in this module are intentionally isolated. They are
candidate site patterns and must be rechecked against an authorized, current
browser session before being treated as stable platform facts.
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urljoin, urlparse

from bosshunter.browser import (
    click as browser_click,
    close_tab,
    evaluate,
    get_page_targets,
    navigate as browser_navigate,
    new_tab,
    press_key as browser_press_key,
    scroll,
    type_text as browser_type_text,
    wait_for_load,
)
from bosshunter.collection.base import CollectionBlockedError, CollectionError, CollectorHooks
from bosshunter.collection.models import JobCandidate, PlatformCollectionRequest, PlatformCollectionResult
from bosshunter.db import (
    delete_page_progress,
    get_collected_combos,
    get_page_progress,
    mark_combo_collected,
    prune_collected_combos,
    prune_page_progress,
    upsert_page_progress,
)
from bosshunter.job_filters import matching_blocked_company, matching_deal_breaker
from bosshunter.throttle import SendWindowChecker, should_take_day_off


SEARCH_URL = "https://www.zhaopin.com/sou/jl{city_code}/"
DETAIL_BASE_URL = "https://www.zhaopin.com"
CITY_SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "data" / "zhilian_cities.json"
_INTERNSHIP_TITLE_TERMS = ("实习", "intern", "internship", "管培")
LIST_ITEM_CLASSES = ("joblist-box__item", "joblist-item", "job-card")
TITLE_CLASSES = ("summary-planes__title", "jobinfo__name", "job-name", "job-title")
SALARY_CLASSES = ("summary-planes__salary", "jobinfo__salary", "job-salary", "salary")
COMPANY_CLASSES = ("company-info__name", "companyinfo__name", "company-name", "company")
CITY_CLASSES = ("jobinfo__city", "job-city", "city", "summary-planes__info", "address-info__content")
JD_CLASSES = (
    "describtion-card__detail-content",
    "describtion__detail-content",
    "job-detail",
    "jobdetail",
    "job-intro",
    "job-description",
)
DETAIL_PATH_PATTERN = re.compile(r"/(?:jobdetail|job|position|detail)/[^/?]+", re.IGNORECASE)
DETAIL_DELAY_MIN_SECONDS = 8.0
DETAIL_DELAY_MAX_SECONDS = 15.0
ZHILIAN_SEARCH_INPUT_SELECTOR = (
    'input[placeholder="输入职位、公司等搜索"], '
    'input[placeholder="搜索职位、公司"], '
    'input[placeholder*="职位、公司"]'
)

# ---------------------------------------------------------------------------
# API-fetch 模式常量（参考 51job API-fetch，适配智联 fe-api.zhaopin.com）
# ---------------------------------------------------------------------------
API_SEARCH_URL = "https://fe-api.zhaopin.com/c/i/sou"
API_PAGE_SIZE = 20
API_FETCH_TIMEOUT = 25.0

API_RATE_MAX_PER_MIN = 30
API_RATE_GAP_MIN = 2.0
API_RATE_GAP_MAX = 3.0
API_BURST_SIZE_MIN = 4
API_BURST_SIZE_MAX = 7
API_BURST_BREAK_MIN = 20.0
API_BURST_BREAK_MAX = 45.0

API_ADAPTIVE_LIGHT = 65
API_ADAPTIVE_MEDIUM = 130

HARD_MAX_PAGES = 50


def load_zhilian_city_snapshot() -> dict[str, Any]:
    """Load the isolated, local-only 智联 city snapshot without network access."""
    try:
        payload = json.loads(CITY_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        payload = {}
    cities = payload.get("cities") if isinstance(payload, dict) and isinstance(payload.get("cities"), list) else []
    return {
        "schema": "bosshunter.zhilian_cities.v1",
        "source": str(payload.get("source") or "manual_authorized_snapshot_required") if isinstance(payload, dict) else "manual_authorized_snapshot_required",
        "fetched_at": payload.get("fetched_at") if isinstance(payload, dict) else None,
        "note": payload.get("note", "智联城市编码需要人工核验") if isinstance(payload, dict) else "智联城市编码需要人工核验",
        "cities": cities,
    }


def _is_internship(title: str) -> bool:
    """判断岗位是否为实习/管培（只查标题）。"""
    t = (title or "").lower()
    return any(s in t for s in _INTERNSHIP_TITLE_TERMS)


def _city_name_variants(city: str) -> set[str]:
    """Match normal user input without treating BOSS codes as Zhilian codes."""
    value = str(city or "").strip()
    if not value:
        return set()
    variants = {value}
    for suffix in ("市", "地区", "自治州"):
        if value.endswith(suffix) and len(value) > len(suffix):
            variants.add(value[: -len(suffix)])
    return variants


def get_zhilian_city_code(city: str) -> str | None:
    """Resolve a city name from the local Zhilian-only snapshot."""
    variants = _city_name_variants(city)
    if not variants:
        return None
    for item in load_zhilian_city_snapshot()["cities"]:
        if not isinstance(item, dict):
            continue
        names = _city_name_variants(str(item.get("name") or ""))
        if variants & names and str(item.get("code") or "").strip():
            return str(item["code"]).strip()
    return None

JS_SUBMIT_SEARCH = """
(() => {
  const input = document.querySelector('input[placeholder="输入职位、公司等搜索"], input[placeholder*="职位、公司"]');
  if (!input) return JSON.stringify({ok: false, reason: 'search_input_missing'});
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  if (setter) setter.call(input, __KEYWORD__);
  else input.value = __KEYWORD__;
  input.focus();
  input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: __KEYWORD__}));
  input.dispatchEvent(new Event('change', {bubbles: true}));
  const button = document.querySelector('.query-search__content-button, .query-sug__button, button[class*="query-search"]');
  if (button) {
    button.click();
    return JSON.stringify({ok: true, value: input.value, submitted_by: 'button'});
  }
  input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
  input.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
  return JSON.stringify({ok: true, value: input.value, submitted_by: 'enter'});
})()
"""

JS_FOCUS_SEARCH_INPUT = """
(() => {
  const input = document.querySelector('input[placeholder="输入职位、公司等搜索"], input[placeholder*="职位、公司"]');
  if (!input) return JSON.stringify({ok: false, reason: 'search_input_missing'});
  input.focus();
  return JSON.stringify({ok: true, value: input.value || '', active: document.activeElement === input});
})()
"""

JS_CLICK_SEARCH_BUTTON = """
(() => {
  const input = document.querySelector('input[placeholder="输入职位、公司等搜索"], input[placeholder*="职位、公司"]');
  const button = document.querySelector('.query-search__content-button, .query-sug__button, button[class*="query-search"]');
  if (!button) return JSON.stringify({ok: false, reason: 'search_button_missing'});
  button.click();
  return JSON.stringify({ok: true, value: input ? input.value || '' : '', submitted_by: 'button'});
})()
"""

JS_READ_SEARCH_STATE = """
(() => {
  const input = document.querySelector('input[placeholder="输入职位、公司等搜索"], input[placeholder*="职位、公司"]');
  const items = Array.from(document.querySelectorAll(
    'div.joblist-box__item, div.joblist-item, article.job-card, div.job-card'
  )).slice(0, 8);
  const signature = items.map((item) => {
    const link = item.querySelector('a[href*="/jobdetail/"], a[href*="/job/"], a[href*="/position/"], a[href*="/detail/"]');
    return (link?.getAttribute('href') || item.textContent || '').trim().slice(0, 240);
  }).join('|');
  return JSON.stringify({
    url: window.location.href,
    input: input ? input.value || '' : '',
    signature,
    item_count: items.length,
  });
})()
"""

JS_NAVIGATE_PAGE = """
(() => {
  const url = new URL(window.location.href);
  const page = String(__PAGE__);
  if (/\\/p\\d+\\/?$/.test(url.pathname)) {
    url.pathname = url.pathname.replace(/\\/p\\d+\\/?$/, `/p${page}`);
  } else {
    url.pathname = `${url.pathname.replace(/\\/?$/, '')}/p${page}`;
  }
  window.location.href = url.toString();
  return JSON.stringify({ok: true, url: url.toString()});
})()
"""

JS_EXTRACT_LIST = """
(() => {
  const text = document.body ? document.body.innerText : '';
  const expectedCity = "__EXPECTED_CITY__";
  const searchInput = document.querySelector('input[placeholder="输入职位、公司等搜索"], input[placeholder*="职位、公司"]');
  const blockedMatch = text.match(/验证码|滑块|访问频繁|频率限制|账号异常|拒绝访问/);
  const items = Array.from(document.querySelectorAll(
    'div.joblist-box__item, div.joblist-item, article.job-card, div.job-card'
  )).map((item, cardIndex) => {
    const first = (selectors) => selectors.map((s) => item.querySelector(s)).find(Boolean);
    const title = first(['a.jobinfo__name', '.job-card__title-text', '.job-name', '.job-title']);
    const salary = first(['p.jobinfo__salary', '.job-card__salary', '.job-salary', '.salary']);
    const company = first(['.companyinfo__name', '.job-card__company-name', '.company-name', '.company']);
    const info = Array.from(item.querySelectorAll('.jobinfo__other-info-item')).map((node) => node.textContent.trim()).filter(Boolean);
    const cityNode = first(['.jobinfo__city', '.job-card__location', '.job-city', '.city']);
    const city = info.find(value => expectedCity && value.includes(expectedCity)) || (cityNode ? cityNode.textContent.trim() : '') || info[0] || expectedCity;
    const detailLink = item.querySelector('a[href*="/jobdetail/"], a[href*="/job/"], a[href*="/position/"], a[href*="/detail/"]');
    const href = [
      title ? title.getAttribute('href') : '',
      detailLink ? detailLink.getAttribute('href') : ''
    ].find(Boolean) || '';
    let matchedId = null;
    try {
      const detailPath = new URL(href, window.location.href).pathname;
      matchedId = detailPath.match(/\\/(?:jobdetail|job|position|detail)\\/([^/?]+?)(?:\\.html?)?$/i);
    } catch (_) {}
    return {
      card_index: item.matches('div.job-card') ? cardIndex : null,
      source_job_id: item.getAttribute('data-positionid') || item.getAttribute('data-job-id') || item.getAttribute('data-id')
        || (title ? title.getAttribute('data-positionid') : '') || (detailLink ? detailLink.getAttribute('data-positionid') : '')
        || (matchedId ? matchedId[1] : ''),
      title: title ? title.textContent.trim() : '',
      salary: salary ? salary.textContent.trim() : '',
      company: company ? company.textContent.trim() : '',
      city,
      url: href
    };
  });
  const hasListRegion = Boolean(document.querySelector('.positionlist__list, [class*="positionlist"], .job-list-panel'));
  const strongLoginWallText = /登录查看更多|登录查看全部|立即登录/.test(text);
  const loginWallText = /请先登录|请登录|登录后(?:查看|继续|获取)|登录失效|账号登录|扫码登录/.test(text);
  const loginDialog = Boolean(document.querySelector('[role="dialog"], .login-dialog, [class*="login-modal"], [class*="login-dialog"]'));
  const loginRequired = strongLoginWallText || (loginWallText && (!searchInput || loginDialog || !items.length));
  const status = blockedMatch ? 'blocked' : loginRequired ? 'login_required' : items.length ? 'ready' : hasListRegion ? 'empty' : 'selector_changed';
  return JSON.stringify({status, blocked_code: blockedMatch ? blockedMatch[0] : '', items, has_search_input: Boolean(searchInput)});
})()
"""

JS_EXTRACT_DETAIL = """
(() => {
  const pageText = (document.body ? document.body.innerText : '') + ' ' + document.title;
  const blockedMatch = pageText.match(/验证码|滑块|访问频繁|频率限制|账号异常|拒绝访问/);
  const loginTextPattern = /请先登录|请登录|登录后(?:查看|继续|获取)|登录失效|账号登录|扫码登录|登录查看更多|登录查看全部|立即登录/;
  const expectedCity = "__EXPECTED_CITY__";
  const first = (selectors) => selectors.map((s) => document.querySelector(s)).find(Boolean);
  const descriptionCard = Array.from(document.querySelectorAll('.job-detail-card')).find((card) =>
    /职位描述/.test(card.querySelector('.job-detail-card__title')?.textContent || '')
  );
  const jd = (descriptionCard ? descriptionCard.querySelector('.job-detail-card__body') : null)
    || first(['.describtion-card__detail-content', '.describtion__detail-content', '.job-detail', '.jobdetail', '.job-intro', '.job-description']);
  const title = first(['.job-detail-summary__title-text', '.summary-planes__title', 'h1.jobinfo__name', '.job-name', '.job-title', 'h1']);
  const salary = first(['.job-detail-summary__salary', '.summary-planes__salary', 'p.jobinfo__salary', '.job-salary', '.salary']);
  const company = first(['.job-card--active .job-card__company-name', '.job-detail-summary__company-name', '.company-info__name', 'div.companyinfo__name', '.company-name', '.company']);
  const cityNode = first(['.job-detail-summary__tag', '.address-info__content', '.summary-planes__info', '.jobinfo__city', '.job-city', '.city']);
  const detailLink = first(['a.job-company-info__view-all', 'a[href*="/jobdetail/"]']);
  const jdText = jd ? jd.textContent.trim() : '';
  const loginPage = /(?:^|\\/)(?:passport|login)(?:\\/|$)/i.test(window.location.pathname);
  const loginDialog = Array.from(document.querySelectorAll(
    '[role="dialog"], .login-dialog, [class*="login-modal"], [class*="login-dialog"]'
  )).some((node) => loginTextPattern.test(node.textContent || ''));
  const loginRequired = loginPage || loginDialog || (
    loginTextPattern.test(pageText) && !title && !company && !jdText
  );
  const cityText = cityNode ? cityNode.textContent.trim() : '';
  const city = expectedCity && cityText.includes(expectedCity) ? expectedCity : cityText;
  return JSON.stringify({
    // Normal logged-in pages can contain incidental login copy in headers,
    // footers or ads. A readable JD always wins; otherwise require structural
    // login evidence (login URL/dialog) or a page with no job identity at all.
    status: blockedMatch ? 'blocked' : jdText ? 'ready' : loginRequired ? 'login_required' : 'selector_changed',
    title: title ? title.textContent.trim() : '', salary: salary ? salary.textContent.trim() : '',
    company: company ? company.textContent.trim() : '', city,
    jd: jdText, url: detailLink ? detailLink.href : window.location.href
  });
})()
"""

JS_CLICK_JOB_CARD = """
(() => {
  const card = document.querySelectorAll('div.job-card')[__CARD_INDEX__];
  if (!card) return JSON.stringify({ok: false, reason: 'job_card_missing'});
  card.click();
  return JSON.stringify({ok: true});
})()
"""

# 在智联页面上下文用 fetch 调用搜索 API（fe-api.zhaopin.com/c/i/sou）。
# 占位符：__KW__（关键词）、__CITY_ID__（智联城市编码）、__START__（偏移量，0-based）。
JS_FETCH_API_PAGE = r"""
(async function () {
    try {
        var url = '""" + API_SEARCH_URL + r"""?keyword=__KW__&cityId=__CITY_ID__&start=__START__&count=""" + str(API_PAGE_SIZE) + r"""';
        var resp = await fetch(url, {
            headers: { 'Accept': 'application/json, text/plain, */*' },
            credentials: 'include'
        });
        var text = await resp.text();
        return JSON.stringify({
            http_status: resp.status,
            content_type: (resp.headers.get('content-type') || ''),
            body: text
        });
    } catch (e) {
        return JSON.stringify({ error: String(e) });
    }
})()
"""


def _js_literal(value: Any) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _build_submit_search_script(keyword: str) -> str:
    return JS_SUBMIT_SEARCH.replace("__KEYWORD__", _js_literal(keyword))


def _build_page_script(page: int) -> str:
    return JS_NAVIGATE_PAGE.replace("__PAGE__", str(int(page)))


def _build_list_script(city: str) -> str:
    return JS_EXTRACT_LIST.replace('"__EXPECTED_CITY__"', _js_literal(city))


def _build_detail_script(city: str) -> str:
    return JS_EXTRACT_DETAIL.replace('"__EXPECTED_CITY__"', _js_literal(city))


def _build_click_card_script(card_index: int) -> str:
    return JS_CLICK_JOB_CARD.replace("__CARD_INDEX__", str(int(card_index)))


def _wait_or_stop(stop_event, seconds: float) -> bool:
    """等待指定秒数；返回 True 表示被停止。"""
    if stop_event is not None:
        return stop_event.wait(max(0.0, seconds))
    time.sleep(max(0.0, seconds))
    return False


def _payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _analyze_api_response(http_status: int, content_type: str, body: str) -> dict:
    """分析智联 API 原始返回，输出 {ok, level, signal, note, jobs, total}。

    智联 fe-api.zhaopin.com/c/i/sou 响应结构：
    ``{code:200, apiCode:200, data:{results:[...], numTotal:N, numFound:N, taskId:"..."}}``

    异常分级：
    - L3 硬风控/封禁：非 200 / 返回 HTML 而非 JSON / code!=200 且含风控词
    - L2 确认降级：code!=200（限流）或 results 为空但 numTotal>0
    - L1 疑似：JSON 解析失败 / 字段缺失
    - L0 正常：code=200 且有 results
    """
    if http_status != 200:
        return {"ok": False, "level": 3, "signal": "http_error",
                "note": f"HTTP {http_status}", "jobs": [], "total": 0}

    stripped = (body or "").strip()
    if not stripped.startswith("{") and not stripped.startswith("["):
        hint = ""
        low = stripped[:200].lower()
        if "verify" in low or "captcha" in low or "滑动" in stripped or "验证" in stripped:
            hint = "（疑似滑块/验证墙）"
        elif "login" in low or "登录" in stripped:
            hint = "（疑似登录态失效）"
        return {"ok": False, "level": 3, "signal": "non_json",
                "note": f"返回非 JSON{hint}，len={len(stripped)}", "jobs": [], "total": 0}

    try:
        data = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "level": 1, "signal": "parse_error",
                "note": "JSON 解析失败", "jobs": [], "total": 0}

    code = str(data.get("code", data.get("apiCode", "200")))
    message = str(data.get("message") or data.get("msg") or "")
    data_block = data.get("data")
    if not isinstance(data_block, dict):
        data_block = {}
    results = data_block.get("results")
    if not isinstance(results, list):
        results = []
    total = data_block.get("numTotal") or data_block.get("numFound") or len(results)

    if code != "200":
        combined = (message + " " + str(data_block)[:200]).lower()
        hard_signals = ("验证", "滑块", "captcha", "verify", "登录", "login", "封", "ban", "风控", "异常")
        level = 3 if any(s in combined for s in hard_signals) else 2
        signal = "hard_risk" if level == 3 else "api_limited"
        return {"ok": False, "level": level, "signal": signal,
                "note": f"code={code} message={message or '(空)'}",
                "jobs": [], "total": int(total or 0)}

    if not results:
        total_value = int(total or 0)
        if total_value == 0:
            return {"ok": True, "level": 0, "signal": "no_results",
                    "note": "code=200 且搜索结果为空",
                    "jobs": [], "total": 0}
        return {"ok": False, "level": 2, "signal": "empty_items",
                "note": f"code=200 但 results 为空（numTotal={total_value}）",
                "jobs": [], "total": total_value}

    return {"ok": True, "level": 0, "signal": "ok",
            "note": "正常", "jobs": results, "total": int(total or len(results))}


def _reason_code_for(analysis: dict) -> str:
    """把风控分析结果映射到官方的 reason_code。"""
    if analysis.get("signal") == "parse_error":
        return "selector_changed"
    return "rate_limit"


class _ApiRateLimiter:
    """智联 API 分簇节奏限制器（防高频触发风控 + 拟人化节奏 + 自适应调参）。

    与 51job _ApiRateLimiter 同结构：簇内间隔 + 簇间休息 + 每分钟滑动窗口上限。
    """

    def __init__(self, total_requests: int | None = None, no_burst: bool = False) -> None:
        self._window: list[float] = []
        self._in_burst = 0
        self._burst_size = random.randint(API_BURST_SIZE_MIN, API_BURST_SIZE_MAX)
        self._no_burst = no_burst
        self._set_tier(total_requests)

    def _set_tier(self, total_requests: int | None) -> None:
        n = int(total_requests or 0)
        if n <= API_ADAPTIVE_LIGHT:
            self._gap_range = (API_RATE_GAP_MIN, API_RATE_GAP_MAX)
            self._break_range = (API_BURST_BREAK_MIN, API_BURST_BREAK_MAX)
            self._per_min_limit = API_RATE_MAX_PER_MIN
        elif n <= API_ADAPTIVE_MEDIUM:
            self._gap_range = (3.0, 5.0)
            self._break_range = (40.0, 80.0)
            self._per_min_limit = 20
        else:
            self._gap_range = (5.0, 8.0)
            self._break_range = (90.0, 180.0)
            self._per_min_limit = 12

    def set_tier(self, total_requests: int) -> None:
        self._set_tier(total_requests)
        self._no_burst = False

    def wait_before_request(self, stop_event) -> bool:
        """发请求前调用：确保满足约束。返回 False 表示被停止。"""
        now = time.time()
        self._window = [ts for ts in self._window if now - ts < 60.0]

        if not self._no_burst and self._in_burst >= self._burst_size:
            break_secs = random.uniform(*self._break_range)
            if _wait_or_stop(stop_event, break_secs):
                return False
            self._in_burst = 0
            self._burst_size = random.randint(API_BURST_SIZE_MIN, API_BURST_SIZE_MAX)
            now = time.time()

        if self._window:
            since_last = now - self._window[-1]
            target_gap = random.uniform(*self._gap_range)
            if since_last < target_gap:
                if _wait_or_stop(stop_event, target_gap - since_last):
                    return False
                now = time.time()

        self._window = [ts for ts in self._window if now - ts < 60.0]
        while len(self._window) >= self._per_min_limit:
            oldest = self._window[0]
            if _wait_or_stop(stop_event, max(0.5, oldest + 60.0 - now)):
                return False
            now = time.time()
            self._window = [ts for ts in self._window if now - ts < 60.0]

        self._window.append(time.time())
        self._in_burst += 1
        return True

    @property
    def per_min_limit(self) -> int:
        return self._per_min_limit

    @property
    def gap_range(self) -> tuple:
        return self._gap_range


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    parent: "_Node | None" = None
    children: list["_Node | str"] = field(default_factory=list)

    def text(self) -> str:
        return " ".join(
            item.text() if isinstance(item, _Node) else str(item)
            for item in self.children
        ).strip()

    def has_class(self, class_name: str) -> bool:
        return class_name in set(self.attrs.get("class", "").split())

    def descendants(self) -> list["_Node"]:
        result: list[_Node] = []
        for item in self.children:
            if isinstance(item, _Node):
                result.append(item)
                result.extend(item.descendants())
        return result

    def find_class(self, names: tuple[str, ...]) -> "_Node | None":
        nodes = [self, *self.descendants()]
        for name in names:
            for node in nodes:
                if node.has_class(name):
                    return node
        return None


class _TreeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = {key: str(value or "") for key, value in attrs}
        node = _Node(tag.lower(), normalized, self.current)
        self.current.children.append(node)
        if tag.lower() not in {"br", "img", "input", "meta", "link", "hr"}:
            self.current = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.current.parent is not None:
            self.current = self.current.parent

    def handle_endtag(self, tag: str) -> None:
        node = self.current
        while node is not self.root:
            if node.tag == tag.lower():
                self.current = node.parent or self.root
                return
            node = node.parent or self.root

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.current.children.append(data)


def _parse_tree(html: str) -> _Node:
    parser = _TreeParser()
    parser.feed(str(html or ""))
    parser.close()
    return parser.root


def _blocked_reason(html: str) -> tuple[str, str] | None:
    text = re.sub(r"\s+", " ", str(html or "")).strip()
    if "验证码" in text or "滑块" in text:
        return "captcha", "智联页面要求完成验证码，已停止当前平台"
    if "账号异常" in text:
        return "login_required", "智联页面报告账号异常，请人工检查登录状态"
    if "访问频繁" in text or "频率限制" in text:
        return "rate_limit", "智联页面报告访问频率受限，已停止当前平台"
    if re.search(r"请(?:先)?登录|登录后(?:查看|继续)|登录失效|账号登录|扫码登录|登录查看更多|登录查看全部|立即登录", text):
        return "login_required", "智联页面要求登录，已停止当前平台"
    return None


def _source_job_id(url: str, attrs: dict[str, str] | None = None) -> str:
    attrs = attrs or {}
    for key in ("source_job_id", "data-positionid", "data-job-id", "data-id", "positionid", "jobid"):
        if attrs.get(key):
            return str(attrs[key]).strip()
    parsed = urlparse(str(url or ""))
    for key in ("positionid", "jobid", "id"):
        value = parse_qs(parsed.query).get(key, [""])[0].strip()
        if value:
            return value
    match = re.search(r"/(?:jobdetail|job|position|detail)/([^/?]+?)(?:\.html?)?$", parsed.path)
    return match.group(1) if match else ""


def _detail_url(url: str, source_job_id: str) -> str:
    """Return a direct detail URL, falling back to the card's platform ID."""
    candidate_url = urljoin(DETAIL_BASE_URL, str(url or "").strip())
    parsed = urlparse(candidate_url)
    if parsed.netloc.lower().endswith("zhaopin.com") and DETAIL_PATH_PATTERN.search(parsed.path):
        return candidate_url
    source_job_id = str(source_job_id or "").strip()
    if source_job_id:
        return urljoin(DETAIL_BASE_URL, f"/jobdetail/{quote(source_job_id, safe='')}.htm")
    return ""


def _city_node(node: _Node, city: str = "") -> _Node | None:
    """Find the current list location field without mistaking experience for city."""
    expected = str(city or "").replace("市", "").strip()
    location_nodes = [item for item in [node, *node.descendants()] if item.has_class("jobinfo__other-info-item")]
    for item in location_nodes:
        if expected and expected in item.text().replace("市", ""):
            return item
    return node.find_class(CITY_CLASSES)


def _detail_href(node: _Node) -> str:
    title_node = node.find_class(TITLE_CLASSES)
    title_href = str((title_node.attrs if title_node else {}).get("href") or "").strip()
    if title_href:
        return title_href
    for item in [node, *node.descendants()]:
        if item.tag != "a":
            continue
        href = str(item.attrs.get("href") or "").strip()
        if href and DETAIL_PATH_PATTERN.search(urlparse(urljoin(DETAIL_BASE_URL, href)).path):
            return href
    return ""


def parse_zhilian_list_html(html: str, *, city: str = "", source_keyword: str = "") -> list[dict[str, str]]:
    """Parse a saved search-page fixture without opening a browser."""
    blocked = _blocked_reason(html)
    if blocked:
        raise CollectionBlockedError(*blocked)
    root = _parse_tree(html)
    nodes = [
        node for node in root.descendants()
        if any(node.has_class(name) for name in LIST_ITEM_CLASSES)
        and not any(node.parent and node.parent.has_class(name) for name in LIST_ITEM_CLASSES)
    ]
    if not nodes:
        visible = root.text()
        if visible and len(visible) > 40:
            raise CollectionError("selector_changed", "智联列表选择器未命中，可能是页面结构变化")
        return []
    result: list[dict[str, str]] = []
    for node in nodes:
        title_node = node.find_class(TITLE_CLASSES)
        salary_node = node.find_class(SALARY_CLASSES)
        company_node = node.find_class(COMPANY_CLASSES)
        city_node = _city_node(node, city)
        href = _detail_href(node)
        source_id = _source_job_id(href, node.attrs)
        title = title_node.text() if title_node else ""
        company = company_node.text() if company_node else ""
        detail_url = _detail_url(href, source_id)
        if not source_id or not title or not company or not detail_url:
            continue
        result.append({
            "source_job_id": source_id,
            "title": title,
            "company": company,
            "salary": salary_node.text() if salary_node else "",
            "city": city_node.text() if city_node else city,
            "url": detail_url,
            "source_keyword": source_keyword,
        })
    return result


def parse_zhilian_detail_html(
    html: str,
    *,
    source_job_id: str = "",
    list_candidate: dict[str, str] | None = None,
) -> dict[str, str]:
    root = _parse_tree(html)
    title_node = root.find_class(TITLE_CLASSES)
    company_node = root.find_class(COMPANY_CLASSES)
    salary_node = root.find_class(SALARY_CLASSES)
    city_node = _city_node(root, str((list_candidate or {}).get("city") or ""))
    jd_node = root.find_class(JD_CLASSES)
    blocked = _blocked_reason(html)
    if blocked:
        login_dialog = next(
            (
                node for node in root.descendants()
                if (
                    node.attrs.get("role") == "dialog"
                    or any(
                        marker in node.attrs.get("class", "")
                        for marker in ("login-dialog", "login-modal")
                    )
                )
                and re.search(
                    r"请(?:先)?登录|登录后(?:查看|继续|获取)|登录失效|账号登录|扫码登录|立即登录",
                    node.text(),
                )
            ),
            None,
        )
        readable_jd_without_login_wall = (
            blocked[0] == "login_required"
            and jd_node is not None
            and bool(jd_node.text().strip())
            and login_dialog is None
        )
        if not readable_jd_without_login_wall:
            raise CollectionBlockedError(*blocked)
    candidate = list_candidate or {}
    result = {
        "source_job_id": source_job_id or candidate.get("source_job_id", ""),
        "title": (title_node.text() if title_node else "") or candidate.get("title", ""),
        "company": (company_node.text() if company_node else "") or candidate.get("company", ""),
        "salary": (salary_node.text() if salary_node else "") or candidate.get("salary", ""),
        "city": (
            candidate.get("city", "")
            if city_node and candidate.get("city") and (
                candidate["city"] in city_node.text()
                or re.split(r"[·\s]", candidate["city"])[0] in city_node.text()
            )
            else (city_node.text() if city_node else "") or candidate.get("city", "")
        ),
        "jd": jd_node.text() if jd_node else "",
        "url": _detail_url(candidate.get("url", ""), source_job_id or candidate.get("source_job_id", "")),
    }
    if not result["source_job_id"] or not result["title"] or not result["company"] or not result["url"] or not result["city"]:
        raise CollectionError("parse_failed", "智联详情缺少岗位身份、职位、公司、城市或链接")
    if not result["jd"]:
        raise CollectionError("parse_failed", "智联详情未提取到完整 JD")
    return result


@dataclass
class ZhilianBrowser:
    new_tab: Callable[..., str | None] = new_tab
    close_tab: Callable[[str], bool] = close_tab
    evaluate: Callable[..., Any] = evaluate
    scroll: Callable[..., bool] = scroll
    wait_for_load: Callable[..., bool] = wait_for_load
    get_page_targets: Callable[[], list[dict]] = get_page_targets
    # Optional so existing offline fakes can continue to exercise evaluate-only
    # parsing. The real collector wires these to the shared Browser Runtime.
    click_action: Callable[..., bool] | None = None
    type_text_action: Callable[..., bool] | None = None
    press_key_action: Callable[..., bool] | None = None
    navigate_action: Callable[[str, str], bool] | None = None


class ZhilianCollector:
    platform = "zhilian"

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        safety_conn: Any | None = None,
        browser: ZhilianBrowser | None = None,
        sleep: Callable[[float], None] = time.sleep,
        delay_range: tuple[float, float] = (DETAIL_DELAY_MIN_SECONDS, DETAIL_DELAY_MAX_SECONDS),
        uniform: Callable[[float, float], float] = random.SystemRandom().uniform,
    ):
        self.config = config or {}
        self.safety_conn = safety_conn
        self.sleep = sleep
        self.delay_range = delay_range
        self.uniform = uniform
        if browser is not None:
            self.browser = browser
        else:
            self.browser = ZhilianBrowser(
                click_action=browser_click,
                type_text_action=browser_type_text,
                press_key_action=browser_press_key,
                navigate_action=browser_navigate,
            )

    def _passes_filters(self, candidate: JobCandidate) -> bool:
        """collector 增值过滤：deal_breakers / blocked_company / 实习。"""
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

    def _resume_ttl_hours(self) -> int:
        """断点续采有效期（默认 24h），与 51job/liepin 同规则。"""
        search_cfg: dict[str, Any] = {}
        platforms = self.config.get("platforms", {})
        if isinstance(platforms, dict) and isinstance(platforms.get("zhilian"), dict):
            pf = platforms["zhilian"]
            if isinstance(pf.get("search"), dict):
                search_cfg = pf["search"]
        elif isinstance(self.config.get("search"), dict):
            search_cfg = self.config["search"]
        raw = search_cfg.get("resume_ttl_hours", 24)
        try:
            return max(1, min(int(raw or 24), 720))
        except (TypeError, ValueError):
            return 24

    def _submit_keyword(self, target_id: str, keyword: str) -> None:
        before_state = self._search_state(target_id)
        actions = (
            self.browser.click_action,
            self.browser.type_text_action,
            self.browser.press_key_action,
        )
        if all(action is not None for action in actions):
            # The runtime click endpoint uses DOM click(), which does not
            # reliably focus an input on the current 智联 SPA. Focus it first
            # and verify the controlled value after CDP typing.
            focused = self._parse_payload(self.browser.evaluate(target_id, JS_FOCUS_SEARCH_INPUT))
            if focused.get("ok") is False:
                raise CollectionError("selector_changed", "智联搜索框选择器未命中，可能是页面结构变化")
            if not self.browser.click_action(target_id, ZHILIAN_SEARCH_INPUT_SELECTOR):
                raise CollectionError("selector_changed", "智联搜索框选择器未命中，可能是页面结构变化")
            # The city landing page normally starts with an empty search box.
            # Clear it first when the platform restores a previous query.
            if not self.browser.press_key_action(target_id, "SelectAll"):
                raise CollectionError("selector_changed", "智联搜索框无法聚焦")
            if not self.browser.press_key_action(target_id, "Backspace"):
                raise CollectionError("selector_changed", "智联搜索框无法清空")
            if not self.browser.type_text_action(target_id, keyword, human=True):
                raise CollectionError("selector_changed", "智联搜索框无法输入关键词")
            typed_state = self._search_state(target_id)
            if typed_state and typed_state.get("input") != keyword:
                # Keep a DOM-backed fallback for runtimes whose CDP keyboard
                # path still fails to update a controlled input.
                submitted = self.browser.evaluate(target_id, _build_submit_search_script(keyword))
            else:
                # The current 智联 page does not consistently handle the raw
                # CDP Enter event. Click its real search button, which follows
                # the platform's own submission path.
                submitted = self.browser.evaluate(target_id, JS_CLICK_SEARCH_BUTTON)
            submit_payload = self._parse_payload(submitted)
            if submit_payload.get("ok") is False:
                raise CollectionError("selector_changed", "智联搜索按钮选择器未命中，可能是页面结构变化")
            if submit_payload.get("ok") and submit_payload.get("value") != keyword:
                raise CollectionError("search_not_applied", "智联搜索框未写入目标关键词")
            self._wait_for_search_results(target_id, keyword, before_state)
            return

        # Keep the evaluate seam for unit-test fakes and older Browser Runtime
        # adapters. It uses the same DOM setter and real search button as the
        # current live page instead of relying on synthetic key events alone.
        submitted = self.browser.evaluate(target_id, _build_submit_search_script(keyword))
        submit_payload = self._parse_payload(submitted)
        if isinstance(submit_payload, dict) and submit_payload.get("ok") is False:
            raise CollectionError("selector_changed", "智联搜索框选择器未命中，可能是页面结构变化")
        if submit_payload.get("ok") and submit_payload.get("value") != keyword:
            raise CollectionError("search_not_applied", "智联搜索框未写入目标关键词")
        self._wait_for_search_results(target_id, keyword, before_state)

    @staticmethod
    def _parse_payload(value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return {}
        return value if isinstance(value, dict) else {}

    def _search_state(self, target_id: str) -> dict[str, Any]:
        payload = self._parse_payload(self.browser.evaluate(target_id, JS_READ_SEARCH_STATE))
        # Older fakes/adapters may return the list payload for every evaluate
        # call. Only treat the dedicated state shape as live search state.
        if not any(key in payload for key in ("url", "input", "signature", "item_count")):
            return {}
        return payload

    def _read_detail_with_retry(
        self,
        target_id: str,
        city: str,
        *,
        attempts: int = 3,
        retry_delay: float = 0.8,
    ) -> dict[str, Any]:
        """Read a SPA detail panel after allowing short render transitions."""
        detail: dict[str, Any] = {}
        for attempt in range(max(1, attempts)):
            detail = self._parse_payload(
                self.browser.evaluate(target_id, _build_detail_script(city))
            )
            if not detail:
                raise CollectionError("parse_failed", "智联详情返回格式无效")
            if detail.get("status") not in {"login_required", "selector_changed"}:
                return detail
            if attempt + 1 < attempts:
                self.sleep(retry_delay)
        return detail

    def _wait_for_search_results(
        self,
        target_id: str,
        keyword: str,
        before_state: dict[str, Any],
        timeout: float = 8.0,
    ) -> None:
        """Wait until the keyword route and refreshed list are both observable.

        ``wait_for_load`` is insufficient for this SPA: the document remains
        ``complete`` while the old city list is still on screen. If an adapter
        cannot expose search state (for example an older offline fake), leave
        the compatibility seam untouched.
        """
        if not before_state:
            return
        old_signature = str(before_state.get("signature") or "")
        deadline = time.monotonic() + timeout
        last_state: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last_state = self._search_state(target_id)
            if not last_state:
                return
            url = str(last_state.get("url") or "")
            value = str(last_state.get("input") or "")
            query_keyword = (parse_qs(urlparse(url).query).get("kw") or [""])[0]
            keyword_route = bool(re.search(r"/kw[^/]+(?:/|$)", url, re.IGNORECASE)) or query_keyword == keyword
            refreshed = str(last_state.get("signature") or "") != old_signature
            if value == keyword and keyword_route and refreshed:
                return
            time.sleep(0.5)
        if last_state and str(last_state.get("input") or "") != keyword:
            raise CollectionError("search_not_applied", "智联搜索框关键词未生效，已停止采集以避免采到无关岗位")
        raise CollectionError("search_not_applied", "智联搜索结果未按目标关键词刷新，已停止采集以避免采到无关岗位")

    @staticmethod
    def build_search_url(request: PlatformCollectionRequest, city: str, keyword: str, page: int) -> str:
        code = str(request.city_codes.get(city) or "").strip()
        if not code:
            raise CollectionError("no_valid_city", f"未配置智联城市编码：{city}")
        # 智联当前把关键词编码到 /kw.../ 路径中，编码规则由页面脚本生成；
        # 先打开城市搜索页，再通过搜索框提交关键词，避免猜测私有编码。
        return SEARCH_URL.format(city_code=quote(code))

    # ------------------------------------------------------------------ API-fetch
    def _evaluate_with_timeout(self, target_id: str, js: str, timeout: float) -> Any:
        """evaluate 兼容包装：支持不接受 timeout 的旧 fake。"""
        try:
            return self.browser.evaluate(target_id, js, timeout=timeout)
        except TypeError:
            return self.browser.evaluate(target_id, js)

    def _ensure_host_tab(self, request: PlatformCollectionRequest) -> tuple[str, bool]:
        """找一个智联搜索页作为 fetch 宿主（与 fe-api.zhaopin.com 同域 cookie）。

        返回 ``(target_id, owned)``；复用用户已有页面时 ``owned=False``。
        """
        keyword = request.keywords[0] if request.keywords else ""
        code = ""
        if request.cities:
            code = str(request.city_codes.get(request.cities[0]) or "").strip()

        for t in self.browser.get_page_targets():
            if "zhaopin.com" in str(t.get("url", "")):
                candidate_target = t.get("targetId")
                if not candidate_target:
                    continue
                try:
                    alive_js = (
                        JS_FETCH_API_PAGE
                        .replace("__KW__", quote(keyword))
                        .replace("__CITY_ID__", code or "530")
                        .replace("__START__", "0")
                    )
                    alive = self._evaluate_with_timeout(candidate_target, alive_js, 8)
                    if alive and "error" not in str(alive):
                        return str(candidate_target), False
                except Exception:
                    continue

        tmp_url = SEARCH_URL.format(city_code=quote(code or "530"))
        host_target = self.browser.new_tab(tmp_url, background=False)
        if not host_target:
            raise CollectionBlockedError("rate_limit", "无法打开智联页面作为 API 宿主")
        self.sleep(5)
        return host_target, True

    def _fetch_page(self, host_target: str, kw_encoded: str, city_id: str, start: int) -> dict:
        """在宿主页上下文发一次 API 请求并分析返回。"""
        js = (
            JS_FETCH_API_PAGE
            .replace("__KW__", kw_encoded)
            .replace("__CITY_ID__", city_id)
            .replace("__START__", str(start))
        )
        raw = self._evaluate_with_timeout(host_target, js, API_FETCH_TIMEOUT)
        if raw is None:
            return {"ok": False, "level": 3, "signal": "empty_result",
                    "note": "evaluate 返回空（超时/断连）", "jobs": [], "total": 0}
        wrapper = _payload(raw)
        if not isinstance(wrapper, dict) or "error" in wrapper:
            return {"ok": False, "level": 3, "signal": "fetch_error",
                    "note": f"API fetch 执行出错: {str(wrapper)[:120]}", "jobs": [], "total": 0}
        return _analyze_api_response(
            int(wrapper.get("http_status") or 0),
            str(wrapper.get("content_type") or ""),
            str(wrapper.get("body") or ""),
        )

    @staticmethod
    def _item_to_candidate(item: dict, city: str, code: str, keyword: str) -> JobCandidate | None:
        """把智联 API item 转成 JobCandidate。

        ⚠️ 字段名基于智联前端 ``__INITIAL_STATE__`` 推断，需用真实登录态响应校准。
        当前覆盖已知字段名的多种可能命名，未命中的字段退化为空字符串。
        """
        if not isinstance(item, dict):
            return None
        job_id = str(
            item.get("number") or item.get("jobId") or item.get("positionId") or ""
        ).strip()
        title = str(
            item.get("jobName") or item.get("positionName") or item.get("name") or ""
        ).strip()
        if not job_id or not title:
            return None
        company = str(
            item.get("companyName") or item.get("company")
            or item.get("fullCompanyName") or ""
        ).strip()
        salary = str(
            item.get("salary") or item.get("salaryStr")
            or item.get("jobSalary") or ""
        ).strip()
        city_str = str(
            item.get("cityName") or item.get("workCity")
            or item.get("city") or city
        ).strip()
        jd = str(
            item.get("jobDesc") or item.get("positionDesc")
            or item.get("jobDescribe") or ""
        ).strip()
        experience = str(
            item.get("workingExp") or item.get("workYear") or ""
        ).strip()
        education = str(
            item.get("education") or item.get("degree") or ""
        ).strip()
        url_str = str(item.get("url") or item.get("positionURL") or "").strip()
        if not url_str:
            url_str = _detail_url("", job_id)
        return JobCandidate(
            platform="zhilian",
            source_job_id=job_id,
            title=title,
            company=company,
            salary=salary,
            city=city_str,
            city_code=code,
            experience=experience,
            education=education,
            jd=jd,
            url=url_str,
            source_keyword=keyword,
        )

    def _collect_api(
        self,
        request: PlatformCollectionRequest,
        hooks: CollectorHooks,
        collected_combos: set[tuple[str, str]],
    ) -> PlatformCollectionResult | None:
        """API-fetch 采集模式。

        返回 None 表示 API 模式不可用（宿主页打不开 / fetch 全失败），
        调用方应降级到 DOM 模式。返回 PlatformCollectionResult 表示采集完成或被风控。
        """
        max_pages = min(max(1, int(request.max_pages or 1)), HARD_MAX_PAGES)

        try:
            host_target, owns_host = self._ensure_host_tab(request)
        except CollectionBlockedError:
            return None
        except Exception:
            return None

        rate_limiter = _ApiRateLimiter(total_requests=API_ADAPTIVE_MEDIUM, no_burst=False)
        profile = self.config.get("profile", {}) if isinstance(self.config.get("profile"), dict) else {}
        deal_breakers = profile.get("deal_breakers") or []
        jd_deal_breakers = profile.get("jd_deal_breakers") or []
        blocked_companies = profile.get("blocked_companies") or []
        allow_internship = bool(profile.get("allow_internship", False))

        api_failed = False
        try:
            for city in request.cities:
                city_id = str(request.city_codes.get(city) or "").strip()
                for kw in request.keywords:
                    if hooks.stop_event is not None and hooks.stop_event.is_set():
                        return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                    if (city, kw) in collected_combos:
                        self._log(hooks, f"智联 {kw} 断点续采：已完成后跳过",
                                  phase="completed_keyword", keyword=kw, city=city)
                        continue
                    saved_page = get_page_progress(self.safety_conn, "zhilian", city, kw) if self.safety_conn is not None else 0
                    start_page = saved_page + 1 if saved_page > 0 else 1
                    if start_page > max_pages:
                        if self.safety_conn is not None:
                            mark_combo_collected(self.safety_conn, "zhilian", city, kw)
                            delete_page_progress(self.safety_conn, "zhilian", city, kw)
                        continue

                    kw_encoded = quote(kw)
                    api_failed = self._collect_keyword_api(
                        request, hooks, host_target, rate_limiter,
                        city=city, city_id=city_id, kw=kw, kw_encoded=kw_encoded,
                        start_page=start_page, max_pages=max_pages,
                        deal_breakers=deal_breakers, jd_deal_breakers=jd_deal_breakers,
                        blocked_companies=blocked_companies, allow_internship=allow_internship,
                    )
                    if api_failed:
                        return None
        finally:
            if owns_host:
                self.browser.close_tab(host_target)

        return PlatformCollectionResult(self.platform, "completed", "search_exhausted",
                                        "智联 API 采集完毕")

    def _collect_keyword_api(
        self,
        request: PlatformCollectionRequest,
        hooks: CollectorHooks,
        host_target: str,
        rate_limiter: _ApiRateLimiter,
        *,
        city: str,
        city_id: str,
        kw: str,
        kw_encoded: str,
        start_page: int,
        max_pages: int,
        deal_breakers: list[str],
        jd_deal_breakers: list[str],
        blocked_companies: list[str],
        allow_internship: bool,
    ) -> bool:
        """单个 (城市, 关键词) 的 API 采集闭环。

        返回 True 表示 API 不可用（应降级到 DOM），False 表示正常完成或被风控停止。
        """
        self._log(hooks, f"智联 {kw} API 采集（共 {max_pages} 页"
                  + (f"，从第 {start_page} 页续采" if start_page > 1 else "") + "）",
                  phase="loading_list", keyword=kw, city=city)

        for page in range(start_page, max_pages + 1):
            if hooks.stop_event is not None and hooks.stop_event.is_set():
                return False
            if not rate_limiter.wait_before_request(hooks.stop_event):
                return False

            start_offset = (page - 1) * API_PAGE_SIZE
            analysis = self._fetch_page(host_target, kw_encoded, city_id, start_offset)

            if not analysis["ok"]:
                signal = analysis.get("signal", "")
                if signal in ("non_json", "fetch_error", "http_error", "empty_result"):
                    self._log(hooks, f"智联 {kw} API 不可用（{signal}），降级到 DOM",
                              phase="loading_list", keyword=kw, city=city, page=page)
                    return True
                raise CollectionBlockedError(
                    _reason_code_for(analysis),
                    f"智联 API 异常（L{analysis['level']} {signal}）：{analysis['note']}",
                )

            jobs_list = analysis["jobs"]
            self._log(hooks, f"智联 {kw} 第 {page} 页：{len(jobs_list)} 条（共 {analysis.get('total', 0)}）",
                      phase="saving", keyword=kw, city=city, page=page)

            for item in jobs_list:
                cand = self._item_to_candidate(item, city, city_id, kw)
                if cand is None:
                    continue
                if not self._passes_filters(cand):
                    continue
                if not hooks.on_list_candidate(cand):
                    continue
                if not hooks.on_candidate(cand):
                    return False

            if self.safety_conn is not None:
                upsert_page_progress(self.safety_conn, "zhilian", city, kw, page)

            if len(jobs_list) < API_PAGE_SIZE:
                self._log(hooks, f"智联 {kw} 已到末页（本页 {len(jobs_list)} 条 < {API_PAGE_SIZE}），结束本词",
                          phase="loading_list", keyword=kw, city=city, page=page)
                break

        if self.safety_conn is not None:
            mark_combo_collected(self.safety_conn, "zhilian", city, kw)
            delete_page_progress(self.safety_conn, "zhilian", city, kw)
        return False

    def _api_fetch_enabled(self) -> bool:
        """是否启用 API-fetch 模式（默认关闭，需手动开启）。"""
        platforms = self.config.get("platforms", {}) if isinstance(self.config.get("platforms"), dict) else {}
        pf = platforms.get("zhilian", {}) if isinstance(platforms.get("zhilian"), dict) else {}
        search_cfg = pf.get("search", {}) if isinstance(pf.get("search"), dict) else {}
        return bool(search_cfg.get("api_fetch", False))

    def _log(self, hooks: CollectorHooks, message: str, **values: Any) -> None:
        try:
            hooks.on_event(message=message, **values)
        except Exception:
            pass

    def collect(self, request: PlatformCollectionRequest, hooks: CollectorHooks) -> PlatformCollectionResult:
        if any(not str(request.city_codes.get(city) or "").strip() for city in request.cities):
            return PlatformCollectionResult(self.platform, "failed", "no_valid_city", "智联城市编码未配置")

        throttle_cfg = self.config.get("throttle", {}) if isinstance(self.config.get("throttle"), dict) else {}
        send_windows = throttle_cfg.get("send_windows", ["09:00-16:00"])
        if not SendWindowChecker(send_windows).is_active():
            return PlatformCollectionResult(self.platform, "completed", "outside_window",
                                            f"当前不在采集时间窗口内（{send_windows}）")
        if should_take_day_off(float(throttle_cfg.get("day_off_probability", 0.05))):
            return PlatformCollectionResult(self.platform, "completed", "day_off",
                                            "今日随机休息，跳过智联采集")

        # 词级断点续采：跳过最近 N 小时内已完成的 (city, keyword) 组合。
        collected_combos: set[tuple[str, str]] = set()
        if self.safety_conn is not None:
            all_keywords = set(request.keywords)
            prune_collected_combos(self.safety_conn, "zhilian", all_keywords)
            prune_page_progress(self.safety_conn, "zhilian", all_keywords)
            collected_combos = get_collected_combos(
                self.safety_conn, "zhilian", within_hours=self._resume_ttl_hours()
            )

        # API-fetch 优先：尝试 API 模式，不可用时降级到 DOM 模式
        if self._api_fetch_enabled():
            api_result = self._collect_api(request, hooks, collected_combos)
            if api_result is not None:
                return api_result
            hooks.on_event(message="智联 API-fetch 不可用，降级到 DOM 模式")

        detail_requests = 0
        for city in request.cities:
            for keyword in request.keywords:
                if (city, keyword) in collected_combos:
                    hooks.on_event(phase="completed_keyword", keyword=keyword, city=city,
                                   message=f"智联 {keyword} 断点续采：{self._resume_ttl_hours()}h 内已完成，整词跳过")
                    continue
                # 页级断点：从上次采到页码的下一页继续
                saved_page = get_page_progress(self.safety_conn, "zhilian", city, keyword) if self.safety_conn is not None else 0
                start_page = saved_page + 1 if saved_page > 0 else 1
                if start_page > request.max_pages:
                    if self.safety_conn is not None:
                        mark_combo_collected(self.safety_conn, "zhilian", city, keyword)
                        delete_page_progress(self.safety_conn, "zhilian", city, keyword)
                    hooks.on_event(phase="completed_keyword", keyword=keyword, city=city,
                                   message=f"智联 {keyword} 页级断点已超最大页（{saved_page}/{request.max_pages}），视为已采完，跳过")
                    continue
                target_id: str | None = None
                try:
                    try:
                        search_url = self.build_search_url(request, city, keyword, 1)
                    except CollectionError as exc:
                        return PlatformCollectionResult(self.platform, "failed", exc.code, exc.message)
                    initial_url = "about:blank" if self.browser.navigate_action is not None else search_url
                    target_id = self.browser.new_tab(initial_url, background=True)
                    if not target_id:
                        return PlatformCollectionResult(self.platform, "failed", "browser_disconnected", "无法打开智联搜索页")
                    if self.browser.navigate_action is not None and not self.browser.navigate_action(target_id, search_url):
                        return PlatformCollectionResult(self.platform, "failed", "browser_disconnected", "智联搜索页导航失败")
                    for page in range(start_page, request.max_pages + 1):
                        if hooks.stop_event is not None and hooks.stop_event.is_set():
                            return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                        hooks.on_event(phase="loading_list", keyword=keyword, city=city, page=page)
                        try:
                            self.browser.wait_for_load(target_id, timeout=10)
                            if page == start_page:
                                self._submit_keyword(target_id, keyword)
                                if start_page > 1:
                                    self.browser.evaluate(target_id, _build_page_script(page))
                            else:
                                self.browser.evaluate(target_id, _build_page_script(page))
                            self.browser.wait_for_load(target_id, timeout=10)
                        except CollectionError as exc:
                            return PlatformCollectionResult(self.platform, "blocked", exc.code, exc.message)
                        except Exception:
                            return PlatformCollectionResult(self.platform, "blocked", "selector_changed", "智联搜索页未能正常加载")
                        try:
                            self.browser.scroll(target_id, y=2600)
                            raw = self.browser.evaluate(target_id, _build_list_script(city))
                            payload = json.loads(raw) if isinstance(raw, str) else raw
                            if isinstance(payload, list):
                                items = payload
                                status = "ready" if items else "empty"
                            elif isinstance(payload, dict):
                                items = payload.get("items", [])
                                status = str(payload.get("status") or ("ready" if items else "empty"))
                            else:
                                items = []
                                status = "selector_changed"
                            if status == "blocked":
                                raise CollectionBlockedError("blocked", "智联页面受到验证码、频率限制或账号异常拦截，已停止当前平台")
                            if status == "login_required":
                                raise CollectionBlockedError("login_required", "智联页面要求登录，已停止当前平台")
                            if status == "selector_changed":
                                raise CollectionError("selector_changed", "智联列表选择器未命中，可能是页面结构变化")
                            if not isinstance(items, list):
                                raise CollectionError("selector_changed", "智联列表返回格式无效")
                        except CollectionBlockedError as exc:
                            return PlatformCollectionResult(self.platform, "blocked", exc.code, exc.message)
                        except (CollectionError, json.JSONDecodeError, TypeError) as exc:
                            code = exc.code if isinstance(exc, CollectionError) else "selector_changed"
                            message = str(exc) or "智联列表解析失败，可能是页面结构变化"
                            return PlatformCollectionResult(self.platform, "blocked", code, message)
                        if not items:
                            break
                        for raw_item in items:
                            card_index = raw_item.get("card_index") if isinstance(raw_item, dict) else None
                            if card_index is not None:
                                if detail_requests:
                                    delay = max(0.0, self.uniform(*self.delay_range))
                                    hooks.on_event(
                                        phase="pacing", keyword=keyword, city=city, page=page,
                                        message=f"详情页安全间隔 {delay:.1f} 秒",
                                    )
                                    if hooks.stop_event is not None:
                                        if hooks.stop_event.wait(delay):
                                            return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                                    else:
                                        self.sleep(delay)
                                hooks.on_event(phase="loading_detail", keyword=keyword, city=city, page=page)
                                clicked = self._parse_payload(
                                    self.browser.evaluate(target_id, _build_click_card_script(int(card_index)))
                                )
                                if clicked.get("ok") is not True:
                                    return PlatformCollectionResult(
                                        self.platform, "blocked", "selector_changed", "智联职位卡结构变化，已安全停止"
                                    )
                                detail_requests += 1
                                if hooks.stop_event is not None:
                                    if hooks.stop_event.wait(1.5):
                                        return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                                else:
                                    self.sleep(1.5)
                                try:
                                    detail = self._read_detail_with_retry(target_id, city)
                                    if detail.get("status") == "blocked":
                                        return PlatformCollectionResult(self.platform, "blocked", "rate_limit", "智联页面出现验证或限流，已停止整个采集队列")
                                    if detail.get("status") == "login_required":
                                        return PlatformCollectionResult(self.platform, "blocked", "login_required", "智联页面要求重新登录，已停止智联当前任务；后续平台将继续")
                                    if detail.get("status") == "selector_changed":
                                        return PlatformCollectionResult(self.platform, "blocked", "selector_changed", "智联侧栏详情结构变化，已安全停止")
                                    base = self._candidate_from_list(detail, city, keyword)
                                    if base is None:
                                        raise CollectionError("parse_failed", "智联侧栏详情缺少岗位身份、职位或链接")
                                    if not hooks.on_list_candidate(base):
                                        continue
                                    final = self._candidate_from_detail(detail, base)
                                    if not final.title or not final.company or not final.url or not final.jd:
                                        raise CollectionError("parse_failed", "智联侧栏详情缺少职位、公司、链接或 JD")
                                except (CollectionError, json.JSONDecodeError, TypeError, ValueError) as exc:
                                    hooks.on_parse_failed(str(exc) or "智联侧栏详情解析失败")
                                    continue
                                if not hooks.on_candidate(final):
                                    return PlatformCollectionResult(self.platform, "completed", "callback_stopped", "采集回调已停止")
                                continue
                            candidate = self._candidate_from_list(raw_item, city, keyword)
                            if candidate is None:
                                continue
                            if not self._passes_filters(candidate):
                                continue
                            if not hooks.on_list_candidate(candidate):
                                continue
                            if detail_requests:
                                delay = max(0.0, self.uniform(*self.delay_range))
                                hooks.on_event(
                                    phase="pacing",
                                    keyword=keyword,
                                    city=city,
                                    page=page,
                                    message=f"详情页安全间隔 {delay:.1f} 秒",
                                )
                                if hooks.stop_event is not None:
                                    if hooks.stop_event.wait(delay):
                                        return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                                else:
                                    self.sleep(delay)
                            hooks.on_event(phase="loading_detail", keyword=keyword, city=city, page=page)
                            detail_initial_url = "about:blank" if self.browser.navigate_action is not None else candidate.url
                            detail_target = self.browser.new_tab(detail_initial_url, background=True)
                            if not detail_target:
                                hooks.on_parse_failed("无法打开智联详情页")
                                continue
                            if self.browser.navigate_action is not None and not self.browser.navigate_action(detail_target, candidate.url):
                                self.browser.close_tab(detail_target)
                                hooks.on_parse_failed("智联详情页导航失败")
                                continue
                            detail_requests += 1
                            try:
                                self.browser.wait_for_load(detail_target, timeout=10)
                                detail = self._read_detail_with_retry(detail_target, city)
                            finally:
                                self.browser.close_tab(detail_target)
                            try:
                                if detail.get("status") == "blocked":
                                    return PlatformCollectionResult(self.platform, "blocked", "rate_limit", "智联详情页出现验证或限流，已停止整个采集队列")
                                if detail.get("status") == "login_required":
                                    return PlatformCollectionResult(self.platform, "blocked", "login_required", "智联详情页要求重新登录，已停止智联当前任务；后续平台将继续")
                                if detail.get("status") == "selector_changed":
                                    return PlatformCollectionResult(self.platform, "blocked", "selector_changed", "智联详情页结构变化，已安全停止")
                                if not detail.get("source_job_id"):
                                    detail["source_job_id"] = candidate.source_job_id
                                if not detail.get("url"):
                                    detail["url"] = candidate.url
                                if not detail.get("city"):
                                    detail["city"] = candidate.city
                                if not detail.get("jd"):
                                    raise CollectionError("parse_failed", "智联详情未提取到完整 JD")
                                final = self._candidate_from_detail(detail, candidate)
                                if not final.title or not final.company or not final.url or not final.jd:
                                    raise CollectionError("parse_failed", "智联详情缺少职位、公司、链接或 JD")
                            except CollectionBlockedError as exc:
                                return PlatformCollectionResult(self.platform, "blocked", exc.code, exc.message)
                            except (CollectionError, json.JSONDecodeError, TypeError) as exc:
                                hooks.on_parse_failed(str(exc) or "智联详情解析失败")
                                continue
                            if not hooks.on_candidate(final):
                                return PlatformCollectionResult(self.platform, "completed", "callback_stopped", "采集回调已停止")
                        # 页级断点：每采完一页立即记录页码
                        if self.safety_conn is not None:
                            upsert_page_progress(self.safety_conn, "zhilian", city, keyword, page)
                finally:
                    if target_id:
                        self.browser.close_tab(target_id)
                # 词结束：标记词级断点 + 清页级断点
                if self.safety_conn is not None:
                    mark_combo_collected(self.safety_conn, "zhilian", city, keyword)
                    delete_page_progress(self.safety_conn, "zhilian", city, keyword)
        return PlatformCollectionResult(self.platform, "completed", "search_exhausted", "智联搜索结果已采集完毕")

    @staticmethod
    def _candidate_from_list(raw: Any, city: str, keyword: str) -> JobCandidate | None:
        if not isinstance(raw, dict):
            return None
        raw_url = str(raw.get("url") or "").strip()
        source_id = str(raw.get("source_job_id") or raw.get("id") or _source_job_id(raw_url) or "").strip()
        url = _detail_url(raw_url, source_id)
        title = str(raw.get("title") or "").strip()
        company = str(raw.get("company") or "").strip()
        # Company data can be rendered after the title/link on the live page.
        # Defer the required company validation to the detail page, matching
        # the BOSS collector's candidate-selection boundary.
        if not source_id or not url or not title:
            return None
        return JobCandidate(
            platform="zhilian", source_job_id=source_id, title=title, company=company,
            salary=str(raw.get("salary") or "").strip(), city=str(raw.get("city") or city).strip(),
            url=url, source_keyword=keyword,
        )

    @staticmethod
    def _candidate_from_detail(detail: dict[str, Any], base: JobCandidate) -> JobCandidate:
        source_job_id = str(detail.get("source_job_id") or base.source_job_id)
        detail_url = _detail_url(str(detail.get("url") or ""), source_job_id) or base.url
        return JobCandidate(
            platform="zhilian", source_job_id=source_job_id,
            title=str(detail.get("title") or base.title).strip(), company=str(detail.get("company") or base.company).strip(),
            salary=str(detail.get("salary") or base.salary).strip(), city=str(detail.get("city") or base.city).strip(),
            experience=str(detail.get("experience") or "").strip(), education=str(detail.get("education") or "").strip(),
            jd=str(detail.get("jd") or "").strip(), url=detail_url,
            source_keyword=base.source_keyword,
        )
