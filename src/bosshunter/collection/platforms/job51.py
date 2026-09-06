"""51job collector — API-fetch 优先 + 组合式策略 + 断点续采 + 分级风控。

实现官方多平台采集管线（``collection/``）的 ``Collector`` 协议。

与官方 v2.3.1 自带的 DOM 解析版相比，本实现的核心差异与增值点：

1. **数据获取**：用 51job 页面上下文内的 ``fetch`` 直接调搜索 API
   （``we.51job.com/api/job/search-pc``），一次拿全列表 + 岗位描述（``jobDescribe``），
   无需逐条开详情页，速度提升一个数量级，且不受 DOM 改版影响。
2. **组合式采样策略**（侦察兵 + 地毯队）：每个关键词先做「分布式探针」随机采样
   定位有新增岗位的热区页，探针全空（饱和）则整词跳过；否则对热区 ±3 做定向扫描，
   并用「保底针」补足 70% 覆盖，用「随机插一针」堵热区外扩缺口。
3. **两级断点续采**：词级（``collect_progress``，词完成整词跳过）+ 页级
   （``collect_progress_page``，中途停止从 N+1 页续采）。
4. **L0-L3 风控分级**：对 API 返回做信号分析（HTTP 状态 / 非 JSON / status!=1 /
   空 items），命中硬风控或确认降级立即通过 ``CollectionBlockedError`` 停止整个平台
   任务（不硬闯、不重试），与官方「fail-closed」原则一致，但信号识别更细。
5. **拟人化节奏**：分簇节奏器（簇内快 + 簇间长停）+ 波浪停顿（短/中/长三档随机）
   + 每分钟滑动窗口硬上限，统一在一个全局限速器内计数，堵住「多阶段独立计数叠加
   放大总频率」的漏洞。

风控铁律：任何分级信号一旦确认，立即停止本平台任务并上抛，绝不降级重试、绝不
切换代理续采。这是累积性单向收紧的账号级风险，只能用冷却化解。
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote

from bosshunter.browser import close_tab, evaluate, get_page_targets, new_tab
from bosshunter.collection.base import CollectionBlockedError, CollectorHooks
from bosshunter.collection.models import JobCandidate, PlatformCollectionRequest, PlatformCollectionResult
from bosshunter.db import (
    delete_page_progress,
    get_collected_combos,
    get_page_progress,
    job_identity_exists,
    mark_combo_collected,
    prune_collected_combos,
    prune_page_progress,
    upsert_page_progress,
)
from bosshunter.job_filters import matching_blocked_company, matching_deal_breaker
from bosshunter.throttle import SendWindowChecker, should_take_day_off


SEARCH_URL = "https://we.51job.com/pc/search?jobArea={area}&keyword={keyword}"
API_SEARCH_URL = "https://we.51job.com/api/job/search-pc"
API_PAGE_SIZE = 20        # 每页条数（API 上限 20，实测稳定）
API_FETCH_TIMEOUT = 25.0  # 单次 fetch 超时（秒）

# ---- 速率四层防护（共享一个 60s 窗口，堵「多阶段独立计数叠加」漏洞）----
API_RATE_MAX_PER_MIN = 30        # 每分钟绝对上限（永不超）
API_RATE_GAP_MIN = 2.0           # 相邻两次请求随机间隔下限（秒）—— 底线 2s
API_RATE_GAP_MAX = 3.0           # 相邻两次请求随机间隔上限（秒）
API_BURST_SIZE_MIN = 4           # 簇内连续请求数下限（页）
API_BURST_SIZE_MAX = 7           # 簇内连续请求数上限（页）
API_BURST_BREAK_MIN = 20.0       # 簇间休息下限（秒）
API_BURST_BREAK_MAX = 45.0       # 簇间休息上限（秒）

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  ★★★ API 自适应速率档位（风控核心，禁止修改）★★★                          ║
# ╚══════════════════════════════════════════════════════════════════════════╝
# 【为什么需要自适应】51job 风控是「账号级累积性单向收紧」——不是单次请求快不快，
# 而是「同账号一段时间内打了多少请求」这个总量。固定节奏有两个致命问题：
#   ① 批量词多、页多时（如 50页×13词=650 次请求），总量一大，即便单次间隔合规，
#      累计频率仍会越过风控的「每分钟/每小时」阈值，导致中途被限流甚至封号。
#   ② 固定 2-3s 间隔会形成可被行为审计识别的规律数字（方差小=机器特征）。
# 因此按「总请求数」动态升档：请求越多 → 间隔越大 / 簇间休息越长 / 每分钟上限越低，
# 用「更保守的节奏」对冲「更大的总量」，把整批采集的总风险压在可控范围内。
# 【为什么禁止修改】这三个阈值（65 / 130）与三档参数是踩线调优的产物：
#   调大阈值 = 更多请求仍走快节奏 = 直接增加封号风险；
#   调快参数 = 单档节奏变激进的后果同上。
#   除非同步引入新的风控对冲手段并有实测数据支撑，否则一律保持现状。
API_ADAPTIVE_LIGHT = 65      # ≤65 次：标准节奏（间隔 2-3s，簇间 20-45s，每分钟 ≤30）
API_ADAPTIVE_MEDIUM = 130    # 66~130 次：中等保守（间隔 3-5s，簇间 40-80s，每分钟 ≤20）
                             # >130 次：重度保守（间隔 5-8s，簇间 90-180s，每分钟 ≤12）

# ---------------------------------------------------------------------------
# 51job 采集页面上限（硬上限，不可超过）
# ---------------------------------------------------------------------------
# 51job 搜索结果原生封顶 50 页（每页 20 条 → 最多 1000 条岗位）。超过 50 页的
# 关键词本质上是「范围过宽」的泛词（单字/通用词），继续采只会：
#   ① 大量返回无关行业岗位，新增命中率断崖式下跌，白白消耗请求配额；
#   ② 翻页越深越像机器，触发 51job WAF 风控的概率急剧上升。
# 因此把防御上限从「防死循环」的 200 页，收紧到贴合平台真实行为的 50 页。
# 需要扩大单关键词翻页数时，改配置里的 search.max_pages（但永不会超过这里的 50 页）。
HARD_MAX_PAGES = 50     # 51job 搜索结果封顶 50 页（页面上限，非防御值）

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  ★★★ 采集策略说明（维护者必读，防误删）★★★                                ║
# ╚══════════════════════════════════════════════════════════════════════════╝
#
# 【组合式采样策略 = 侦察兵 + 地毯队】—— 两级兜底是刻意设计，不是 BUG
#   ① 分布式探针（侦察兵）：前密后疏随机采样，快速定位「有新增岗位」的页面区间。
#   ② 保底针（地毯队）    ：探针全空（疑似饱和）时，从非探针页补足到约 70% 覆盖，
#                           防止「探针恰好没命中」造成的假饱和漏检。
#        → 其中「固定插针第 2 页」是 ★★ 故意设定 ★★：探针第 1 页必采，第 2 页又
#          被探针的「不相邻约束」排除，若无固定 p2 针，第 2 页会变成永久漏检夹缝。
#          【严禁把「固定 p2」当成 hardcode 的 BUG 删除或改写！】
#   ③ 热区 ±3 扫描        ：命中页邻域定向扫描，集中火力扫高概率新增区。
#   ④ 随机插一针          ：从非热区随机抽 1 页加入热区，堵「热区外扩不足」的缺口。
#   ⑤ 续采起点/邻位强制纳入：防止断点续采后出现永久性空白页。
#
# 【风控规则 = L0-L3 分级 + fail-closed 铁律】—— 累积性单向收紧，只能用冷却化解
#   L0 正常 / L1 疑似 / L2 确认降级（限流、items 空但 total>0）/ L3 硬风控封禁
#   任何分级信号一旦确认 → 立即抛 CollectionBlockedError 停掉整个平台任务：
#     ★ 绝不降级重试 ★ 绝不切换代理续采 ★ 绝不在风控页上硬闯重试 ★
#   风控信号分析见下文 _analyze_api_response()，由 _reason_code_for() 映射 reason_code。
#
# ---- 波浪停顿概率分布（模拟真人「正常翻页—多看几眼—走开一下」）----
WAVE_SHORT_PROB = 0.60         # 短波：3-5s
WAVE_MEDIUM_PROB = 0.85        # 中波累计：8-15s（0.60~0.85 区间）
                                # 长波：20-40s（0.85~1.0 区间）

# ---- 关键词相关性校验（方案 C）----
# 51job 搜索是分词模糊匹配，会把关键词里的通用职位词（负责人/主管/经理...）
# 当成独立词匹配，导致其他行业的岗位混入。方案：剥离通用职位词，得到领域词根，
# 岗位标题必须命中至少一个领域词根才放行。换行业只需改关键词，无需硬编码。
_GENERIC_ROLE_WORDS = [
    "主管工程师", "负责人工程师", "副总裁", "总经理", "副总经理", "项目经理",
    "部门经理", "团队经理", "负责人", "主管", "经理", "总监", "专员",
    "工程师", "管理员", "顾问", "助理", "实习生", "管培生", "培训生",
    "专家", "主任", "副总裁", "总裁", "高级", "资深", "初级", "中级", "师",
]
_INTERNSHIP_TITLE_TERMS = ("实习", "intern", "internship", "管培")


# 城市快照沿用官方已核验编码（直辖市/新一线/主要二线城市）；其余城市由 get_51job_city_code 拒绝，
# 不猜测编码。此处保留与官方相同的快照与解析函数（orchestrator 校验与 server 依赖）。
CITY_SNAPSHOT = (
    {"name": "北京", "code": "010000"},
    {"name": "上海", "code": "020000"},
    {"name": "广州", "code": "030200"},
    {"name": "深圳", "code": "040000"},
    {"name": "杭州", "code": "080200"},
    {"name": "成都", "code": "090200"},
    {"name": "重庆", "code": "060000"},
    {"name": "武汉", "code": "070200"},
    {"name": "西安", "code": "200200"},
    {"name": "苏州", "code": "040700"},
    {"name": "南京", "code": "040400"},
    {"name": "长沙", "code": "180200"},
    {"name": "天津", "code": "050000"},
    {"name": "郑州", "code": "170200"},
    {"name": "青岛", "code": "120300"},
    {"name": "宁波", "code": "080300"},
    {"name": "合肥", "code": "150200"},
    {"name": "厦门", "code": "110200"},
    {"name": "福州", "code": "110300"},
    {"name": "济南", "code": "120200"},
    {"name": "大连", "code": "230300"},
    {"name": "沈阳", "code": "230200"},
    {"name": "无锡", "code": "040500"},
    {"name": "东莞", "code": "030800"},
    {"name": "佛山", "code": "030600"},
    {"name": "珠海", "code": "030500"},
    {"name": "温州", "code": "080700"},
    {"name": "嘉兴", "code": "080600"},
    {"name": "绍兴", "code": "080500"},
    {"name": "金华", "code": "080800"},
    {"name": "常州", "code": "040600"},
)


def load_51job_city_snapshot() -> dict[str, Any]:
    return {
        "schema": "bosshunter.51job_cities.v1",
        "source": "verified_snapshot",
        "note": "当前内置已核验的直辖市、新一线及主要二线城市编码；其他城市需核验后再加入。",
        "cities": [dict(item) for item in CITY_SNAPSHOT],
    }


def get_51job_city_code(city: str) -> str | None:
    normalized = str(city or "").strip().removesuffix("市")
    for item in CITY_SNAPSHOT:
        if item["name"].removesuffix("市") == normalized:
            return item["code"]
    return None


# 在 51job 页面上下文用 fetch 调用搜索 API（同源请求，Referer/cookie 自动正确）。
# 占位符：__KW__（URL 编码关键词）、__AREA__（城市代码）、__PAGE__（页码）。
JS_FETCH_API_PAGE = r"""
(async function () {
    try {
        var url = '""" + API_SEARCH_URL + r"""?api_key=51job&timestamp=' + Date.now() +
            '&keyword=__KW__&searchType=2&jobArea=__AREA__&pageNum=__PAGE__&pageSize=""" + str(API_PAGE_SIZE) + r"""&sortType=0&source=1&scene=7';
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


def _wait_or_stop(stop_event, seconds: float) -> bool:
    """等待指定秒数；返回 True 表示被停止。"""
    if stop_event is not None:
        return stop_event.wait(max(0.0, seconds))
    time.sleep(max(0.0, seconds))
    return False


def _parse_salary_range(salary: str) -> tuple[float, float] | None:
    """解析薪资，返回可比较的月薪区间 (minK, maxK)，单位 K/月，失败返回 None。

    内置在本模块内不影响官方 salary 语义；支持 BOSS「15-25K」、51job 中文
    「8千-1.2万」「1-2万」「1.5-2万·13薪」以及年薪「25-38万/年」（换算月薪）。
    """
    import re

    normalized = str(salary or "").strip()

    yearly_match = re.search(
        r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*万\s*/\s*年", normalized,
    )
    if yearly_match:
        lo = float(yearly_match.group(1)) * 10000 / 12 / 1000
        hi = float(yearly_match.group(2)) * 10000 / 12 / 1000
        return (round(min(lo, hi), 1), round(max(lo, hi), 1))

    cn_range = re.search(
        r"(\d+(?:\.\d+)?)\s*([千万])\s*-\s*(\d+(?:\.\d+)?)\s*万", normalized,
    )
    if cn_range:
        lo_val = float(cn_range.group(1))
        lo_unit = cn_range.group(2)
        hi_val = float(cn_range.group(3))
        lo_k = lo_val * (10 if lo_unit == "万" else 1)
        hi_k = hi_val * 10
        return (round(min(lo_k, hi_k), 1), round(max(lo_k, hi_k), 1))

    cn_range_plain = re.search(
        r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*万", normalized,
    )
    if cn_range_plain:
        lo_k = float(cn_range_plain.group(1)) * 10
        hi_k = float(cn_range_plain.group(2)) * 10
        return (round(min(lo_k, hi_k), 1), round(max(lo_k, hi_k), 1))

    cn_single_wan = re.search(r"(\d+(?:\.\d+)?)\s*万", normalized)
    if cn_single_wan:
        value = round(float(cn_single_wan.group(1)) * 10, 1)
        return value, value
    cn_single_qian = re.search(r"(\d+(?:\.\d+)?)\s*千", normalized)
    if cn_single_qian:
        value = round(float(cn_single_qian.group(1)), 1)
        return value, value

    range_match = re.search(
        r"(\d+(?:\.\d+)?)\s*[kK]?\s*-\s*(\d+(?:\.\d+)?)\s*[kK]", normalized,
    )
    if range_match:
        low, high = (float(value) for value in range_match.groups())
        return (min(low, high), max(low, high))

    single_match = re.search(r"(\d+(?:\.\d+)?)\s*[kK](?!\w)", normalized)
    if single_match:
        value = float(single_match.group(1))
        return value, value
    return None


def _extract_core_terms(keyword: str) -> list[str]:
    """从搜索关键词提取领域词根（剥离通用职位词后剩余部分）。"""
    import re

    kw = (keyword or "").strip()
    for w in _GENERIC_ROLE_WORDS:
        kw = kw.replace(w, " ")
    return [p.strip() for p in re.split(r"[\s/·\-—,，、:：()（）]+", kw) if p.strip()]


def _is_relevant_to_keyword(title: str, jd: str, kw: str) -> bool:
    """岗位标题是否与搜索关键词实质相关（通用版，方案 C）。

    从关键词提取领域词根，岗位标题必须命中至少一个词根才放行；JD 不参与匹配
    （JD 里的「系统」「it」等通用词会导致溢出放行）。英文/数字词根用词边界，
    中文用子串。若关键词全是通用词（如「主管」），则不做校验直接放行。
    """
    import re

    cores = _extract_core_terms(kw)
    if not cores:
        return True
    t = (title or "").lower()
    for core in cores:
        core_l = core.lower()
        if not core_l:
            continue
        if core_l.isascii() and any(c.isalpha() for c in core_l):
            if re.search(rf"(?<![a-z0-9]){re.escape(core_l)}(?![a-z0-9])", t):
                return True
        elif core_l in t:
            return True
    return False


def _is_internship(title: str, experience: str) -> bool:
    """判断岗位是否为实习/管培（只查标题，experience 的「无需经验」会误伤 entry-level）。"""
    t = (title or "").lower()
    return any(s in t for s in _INTERNSHIP_TITLE_TERMS)


def _salary_within_range(salary: str, salary_min: float, salary_max: float) -> bool:
    """薪资过滤：仅当薪资能解析出区间才对边界做校验（解析失败放行，不误伤）。"""
    if salary_min <= 0 and salary_max <= 0:
        return True
    rng = _parse_salary_range(salary)
    if rng is None:
        return True
    lo, hi = rng
    if salary_min > 0 and lo < salary_min:
        return False
    if salary_max > 0 and hi > salary_max:
        return False
    return True


def _analyze_api_response(http_status: int, content_type: str, body: str) -> dict:
    """分析 51job API 原始返回，输出 {ok, level, signal, note, jobs, total}。

    异常分级：
    - L3 硬风控/封禁：非 200 / 返回 HTML 而非 JSON / status!=1 且含风控词
    - L2 确认降级：status!=1（限流）或 items 为空但 total>0
    - L1 疑似：status=1 但 JSON 解析失败 / 字段缺失
    - L0 正常：status=1 且有 items
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

    status = str(data.get("status", data.get("code", "1")))
    message = str(data.get("message") or data.get("msg") or "")
    resultbody = data.get("resultbody")
    if not isinstance(resultbody, dict):
        resultbody = {}
    job_block = resultbody.get("job")
    if not isinstance(job_block, dict):
        job_block = {}
    items = job_block.get("items")
    if not isinstance(items, list):
        items = []
    total = job_block.get("totalCount") or job_block.get("total") or len(items)

    if status != "1":
        combined = (message + " " + str(resultbody)[:200]).lower()
        hard_signals = ("验证", "滑块", "captcha", "verify", "登录", "login", "封", "ban", "风控", "异常")
        level = 3 if any(s in combined for s in hard_signals) else 2
        signal = "hard_risk" if level == 3 else "api_limited"
        return {"ok": False, "level": level, "signal": signal,
                "note": f"status={status} message={message or '(空)'}",
                "jobs": [], "total": int(total or 0)}

    if not items:
        total_value = int(total or 0)
        if total_value == 0:
            return {"ok": True, "level": 0, "signal": "no_results",
                    "note": "status=1 且搜索结果为空",
                    "jobs": [], "total": 0}
        return {"ok": False, "level": 2, "signal": "empty_items",
                "note": f"status=1 但 items 为空（total={total_value}）",
                "jobs": [], "total": total_value}

    return {"ok": True, "level": 0, "signal": "ok",
            "note": "正常", "jobs": items, "total": int(total or len(items))}


def _reason_code_for(analysis: dict) -> str:
    """把风控分析结果映射到官方的 reason_code。"""
    if analysis.get("signal") == "parse_error":
        return "selector_changed"
    return "rate_limit"


class _ApiRateLimiter:
    """51job API 分簇节奏限制器（防高频触发风控 + 拟人化节奏 + 自适应调参）。

    三个维度约束：
    1. 簇内：相邻间隔随机取 [gap_min, gap_max]（底线 2s）
    2. 簇间：连续翻 burst_size 页后歇 break 秒（模拟真人离开）
    3. 每分钟总量 ≤ per_min_limit（滑动窗口兜底）

    自适应根据「总请求数」动态调参；探测阶段（no_burst）跳过簇间休息，只保留
    间隔 + 每分钟上限，加快启动。
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
class Job51Browser:
    """浏览器依赖注入点：测试用假实现替换，正式运行用 module 级函数。"""

    get_page_targets: Callable[[], list[dict]] = get_page_targets
    new_tab: Callable[..., str | None] = new_tab
    close_tab: Callable[[str], bool] = close_tab
    evaluate: Callable[..., Any] = evaluate
    sleep: Callable[[float], None] = time.sleep


def _payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


class Job51Collector:
    platform = "51job"

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        safety_conn: Any | None = None,
        browser: Job51Browser | None = None,
        sleep: Callable[[float], None] = time.sleep,
        uniform: Callable[[float, float], float] | None = None,
    ):
        self.config = config or {}
        # safety_conn 由 orchestrator 注入（学 BossCollector）：用于断点读写与去重判断。
        # 为 None 时（registry 无参实例化/部分测试）断点与去重退化为不校验。
        self.safety_conn = safety_conn
        self.browser = browser or Job51Browser()
        self.sleep = sleep
        self.uniform = uniform or random.SystemRandom().uniform

    # ------------------------------------------------------------------ 工具
    def _log(self, hooks: CollectorHooks, message: str, **values: Any) -> None:
        try:
            hooks.on_event(message=message, **values)
        except Exception:
            pass

    def _would_be_collected(
        self,
        candidate: JobCandidate,
        kw: str,
        *,
        deal_breakers: list[str],
        jd_deal_breakers: list[str],
        blocked_companies: list[str],
        allow_internship: bool,
        salary_min: float,
        salary_max: float,
    ) -> bool:
        """探针口径：判断该岗位是否「会被最终入库」。

        与正式入库的过滤链保持一致（去重 + 官方三层 + collector 三层），确保
        探针判定的「新增」就是真正会入库的新岗，避免对饱和词误判导致全量翻页。
        """
        # 去重（官方 _SharedProcessor.inspect 会做；探针阶段自身先判断，口径一致）
        if self.safety_conn is not None and job_identity_exists(
            self.safety_conn, candidate.platform, candidate.source_job_id
        ):
            return False
        # 官方统一层：title deal_breakers / blocked_company（inspect）
        if matching_deal_breaker(candidate.title, deal_breakers):
            return False
        if matching_blocked_company(candidate.company, blocked_companies):
            return False
        # 官方统一层：jd deal_breakers（save）
        if matching_deal_breaker(candidate.jd, jd_deal_breakers):
            return False
        # collector 增值层：实习过滤
        if not allow_internship and _is_internship(candidate.title, candidate.experience):
            return False
        # collector 增值层：关键词相关性（方案 C）
        if not _is_relevant_to_keyword(candidate.title, candidate.jd, kw):
            return False
        # collector 增值层：薪资范围
        if not _salary_within_range(candidate.salary, salary_min, salary_max):
            return False
        return True

    def _passes_collector_filters(
        self,
        candidate: JobCandidate,
        kw: str,
        *,
        allow_internship: bool,
        salary_min: float,
        salary_max: float,
    ) -> bool:
        """正式入库阶段 collector 自己做的那部分过滤（官方不重复的增值层）。

        deal_breakers / blocked_company / jd_deal_breakers 在正式入库时交给官方
        ``_SharedProcessor`` 的 hooks 回调处理，这里只做官方没有的增值过滤。
        """
        if not allow_internship and _is_internship(candidate.title, candidate.experience):
            return False
        if not _is_relevant_to_keyword(candidate.title, candidate.jd, kw):
            return False
        if not _salary_within_range(candidate.salary, salary_min, salary_max):
            return False
        return True

    @staticmethod
    def _item_to_candidate(
        item: dict, city: str, code: str, keyword: str
    ) -> JobCandidate | None:
        """把 51job API item 转成平台中立的 JobCandidate（含 jobDescribe 作为 jd）。"""
        job_id = str(item.get("jobId") or "").strip()
        title = (item.get("jobName") or "").strip()
        if not job_id or not title:
            return None
        year = (item.get("workYearString") or "").strip()
        degree = (item.get("degreeString") or "").strip()
        experience = (year + "·" + degree) if (year and degree) else (year or degree)
        area_str = str(item.get("jobAreaString") or "").strip()
        resolved_city = (area_str.split("·")[0].strip() if area_str else city)
        return JobCandidate(
            platform="51job",
            source_job_id=job_id,
            title=title,
            company=(item.get("fullCompanyName") or item.get("companyName") or "").strip(),
            salary=(item.get("provideSalaryString") or "").strip(),
            city=resolved_city,
            city_code=code,
            experience=experience,
            jd=(item.get("jobDescribe") or "").strip(),
            hr_name=(item.get("hrName") or "").strip(),
            hr_title=(item.get("hrPosition") or "").strip(),
            company_size=(item.get("companySizeString") or "").strip(),
            company_industry=(item.get("industryType1Str") or "").strip(),
            url=(item.get("jobHref") or "").strip(),
            source_keyword=keyword,
        )

    # ---------------------------------------------------------------- 宿主页
    def _ensure_host_tab(self, request: PlatformCollectionRequest) -> tuple[str, bool]:
        """找一个 51job 搜索页作为 fetch 宿主（与 API 同源）。

        ⚠️ 必须精确匹配 we.51job.com：www/jobs 子域名跨域，fetch 会被 CORS 拦截。
        ⚠️ 不盲目复用旧宿主页：旧 tab 可能后台节流/冻结导致 fetch 挂起；复用前先
        「fetch 探活」，能真正拿到 API 响应的才复用，否则新开前台 tab。

        返回 ``(target_id, owned)``；复用用户已有页面时 ``owned=False``，采集结束
        不能把用户的页面关闭。
        """
        keyword = request.keywords[0]
        code = request.city_codes.get(request.cities[0]) if request.cities else "020000"

        for t in self.browser.get_page_targets():
            if "we.51job.com" in str(t.get("url", "")):
                candidate_target = t.get("targetId")
                if not candidate_target:
                    continue
                try:
                    alive_js = JS_FETCH_API_PAGE.replace("__KW__", quote(keyword)).replace("__AREA__", code).replace("__PAGE__", "1")
                    alive = self.browser.evaluate(candidate_target, alive_js, timeout=8)
                    if alive and "error" not in str(alive):
                        return str(candidate_target), False
                except Exception:
                    continue

        tmp_url = SEARCH_URL.format(area=code, keyword=quote(keyword))
        host_target = self.browser.new_tab(tmp_url, background=False)
        if not host_target:
            raise CollectionBlockedError("rate_limit", "无法打开 51job 页面作为 API 宿主")
        self.sleep(5)  # 等宿主页加载出 cookie
        return host_target, True

    def _fetch_page(self, host_target: str, kw_encoded: str, area: str, page: int) -> dict:
        """在宿主页上下文发一次 API 请求并分析返回，返回 _analyze_api_response 结果。"""
        js = JS_FETCH_API_PAGE.replace("__KW__", kw_encoded).replace("__AREA__", area).replace("__PAGE__", str(page))
        raw = self.browser.evaluate(host_target, js, timeout=API_FETCH_TIMEOUT)
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

    # ---------------------------------------------------------------- collect
    def _resume_ttl_hours(self) -> int:
        """57hybrid：断点续采有效期。同时兼容旧 config.search 与新 config.platforms.51job.search。"""
        search_cfg = {}
        platforms = self.config.get("platforms", {}) if isinstance(self.config.get("platforms"), dict) else {}
        pf = platforms.get("51job", {}) if isinstance(platforms.get("51job"), dict) else {}
        if isinstance(pf.get("search"), dict):
            search_cfg = pf["search"]
        elif isinstance(self.config.get("search"), dict):
            search_cfg = self.config["search"]
        raw = search_cfg.get("resume_ttl_hours", 24)
        try:
            return max(1, min(int(raw or 24), 720))
        except (TypeError, ValueError):
            return 24

    def collect(
        self, request: PlatformCollectionRequest, hooks: CollectorHooks
    ) -> PlatformCollectionResult:
        # 城市编码前置校验（fail-closed，与官方一致）
        for city in request.cities:
            if not request.city_codes.get(city):
                return PlatformCollectionResult(self.platform, "failed", "no_valid_city",
                                                f"51job 城市编码未配置：{city}")

        # 词级断点续采：跳过最近 N 小时内已完成的 (city, keyword) 组合。
        # 超过有效期（默认 24h）的旧断点视为过期，重新采集（招聘岗位每天都有新增）。
        # 孤儿断点（关键词被增删后残留）顺带清理，避免脏数据累积。
        collected_combos: set[tuple[str, str]] = set()
        if self.safety_conn is not None:
            all_keywords = set(request.keywords)
            prune_collected_combos(self.safety_conn, "51job", all_keywords)
            prune_page_progress(self.safety_conn, "51job", all_keywords)
            collected_combos = get_collected_combos(
                self.safety_conn, "51job", within_hours=self._resume_ttl_hours()
            )

        # 反检测前置：时间窗口 + 随机休息日（复用 BossHunter throttle 配置）
        throttle_cfg = self.config.get("throttle", {}) if isinstance(self.config.get("throttle"), dict) else {}
        send_windows = throttle_cfg.get("send_windows", ["09:00-16:00"])
        if not SendWindowChecker(send_windows).is_active():
            return PlatformCollectionResult(self.platform, "completed", "outside_window",
                                            f"当前不在采集时间窗口内（{send_windows}）")
        if should_take_day_off(float(throttle_cfg.get("day_off_probability", 0.05))):
            return PlatformCollectionResult(self.platform, "completed", "day_off",
                                            "今日随机休息，跳过 51job 采集")

        profile = self.config.get("profile", {}) if isinstance(self.config.get("profile"), dict) else {}
        deal_breakers = profile.get("deal_breakers") or []
        jd_deal_breakers = profile.get("jd_deal_breakers") or []
        blocked_companies = profile.get("blocked_companies") or []
        allow_internship = bool(profile.get("allow_internship", False))
        try:
            salary_min = float(profile.get("salary_min") or 0)
            salary_max = float(profile.get("salary_max") or 0)
        except (TypeError, ValueError):
            salary_min = salary_max = 0.0

        max_pages = min(max(1, int(request.max_pages or 1)), HARD_MAX_PAGES)

        try:
            host_target, owns_host_target = self._ensure_host_tab(request)
        except CollectionBlockedError:
            raise
        except Exception as exc:  # pragma: no cover - 防御性兜底
            raise CollectionBlockedError("browser_disconnected", f"打开 51job 宿主页失败：{exc}")

        # ★ 全局限速器：探针/保底针/热区扫描三阶段共享同一个 60s 窗口，统一计数。
        rate_limiter = _ApiRateLimiter(total_requests=API_ADAPTIVE_MEDIUM, no_burst=False)

        try:
            for city in request.cities:
                area = str(request.city_codes.get(city) or "").strip()
                for kw in request.keywords:
                    if hooks.stop_event is not None and hooks.stop_event.is_set():
                        return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                    # 词级断点跳过：24h 内已完成的组合整词跳过
                    if (city, kw) in collected_combos:
                        self._log(hooks, f"51job {kw} 断点续采：24h 内已完成，整词跳过",
                                  phase="completed_keyword", keyword=kw, city=city)
                        continue
                    self._collect_keyword(
                        request, hooks, host_target, rate_limiter,
                        city=city, area=area, kw=kw, max_pages=max_pages,
                        deal_breakers=deal_breakers, jd_deal_breakers=jd_deal_breakers,
                        blocked_companies=blocked_companies, allow_internship=allow_internship,
                        salary_min=salary_min, salary_max=salary_max,
                    )
        finally:
            if owns_host_target:
                self.browser.close_tab(host_target)

        return PlatformCollectionResult(self.platform, "completed", "search_exhausted",
                                        "51job 搜索结果已采集完毕")

    def _collect_keyword(
        self,
        request: PlatformCollectionRequest,
        hooks: CollectorHooks,
        host_target: str,
        rate_limiter: _ApiRateLimiter,
        *,
        city: str,
        area: str,
        kw: str,
        max_pages: int,
        deal_breakers: list[str],
        jd_deal_breakers: list[str],
        blocked_companies: list[str],
        allow_internship: bool,
        salary_min: float,
        salary_max: float,
    ) -> None:
        """单个 (城市, 关键词) 的完整闭环：探针 → 保底针 → 饱和跳过 / 热区扫描。"""
        kw_encoded = quote(kw)

        # 页级断点续采：读上次采到页码，从 N+1 页继续（中途停止→续采不重翻）
        saved_page = get_page_progress(self.safety_conn, "51job", city, kw) if self.safety_conn is not None else 0
        start_page = saved_page + 1 if saved_page > 0 else 1
        # 边界守卫：续采起点已超过最大页（理论上该词已采完），直接标记完成并跳过
        if start_page > max_pages:
            if self.safety_conn is not None:
                mark_combo_collected(self.safety_conn, "51job", city, kw)
                delete_page_progress(self.safety_conn, "51job", city, kw)
            self._log(hooks, f"51job {kw} 页级断点已超最大页（{saved_page}/{max_pages}），视为已采完，跳过",
                      phase="completed_keyword", keyword=kw, city=city)
            return
        self._log(hooks, f"51job {kw} 采集（共 {max_pages} 页" + (f"，从第 {start_page} 页续采" if saved_page > 0 else "") + "）",
                  phase="loading_list", keyword=kw, city=city)

        # 有效页数（初始 = 设定上限）：探针拿到 total 后会被主动末页判定收窄。
        # 例：设定 50 页但该词实际只 30 页，探针第 1 页返回 total=600 → 收窄到 30，
        # 热区 ±3 外扩、随机插一针、保底针都不会再翻到「不存在页」，杜绝 non_json 误判。
        effective_max = max_pages

        # ★ 分布式探针（Phase 1）：随机采样定位有新增岗位的页面区间。
        probe_pages = self._plan_probe_pages(start_page, max_pages)
        hot_pages: set[int] = set()
        probe_cache: dict[int, dict] = {}
        probe_hits: list[str] = []

        self._log(hooks, f"51job {kw} 分布式探针采样（{len(probe_pages)} 个点）", phase="procuring", keyword=kw, city=city)
        for pp in probe_pages:
            if hooks.stop_event is not None and hooks.stop_event.is_set():
                return
            if pp > effective_max:
                continue
            if not rate_limiter.wait_before_request(hooks.stop_event):
                return
            analysis = self._fetch_page(host_target, kw_encoded, area, pp)
            if not analysis["ok"]:
                if analysis.get("signal") == "empty_items":
                    real_last = self._real_last_page(analysis, effective_max)
                    if pp > real_last:
                        effective_max = real_last
                        self._log(hooks, f"51job {kw} p{pp} 超出真实末页 {real_last}，跳过",
                                  phase="procuring", keyword=kw, city=city, page=pp)
                        continue
                raise CollectionBlockedError(
                    _reason_code_for(analysis),
                    f"51job API 异常（L{analysis['level']} {analysis['signal']}）：{analysis['note']}",
                )
            if not analysis["jobs"]:
                self._log(hooks, f"51job {kw} p{pp} 抽查无数据 signal={analysis.get('signal','?')}",
                          phase="procuring", keyword=kw, city=city, page=pp)
                continue
            probe_cache[pp] = analysis
            # 主动末页判定：任意一页正常返回都能用 total 反推真实末页，收窄有效页数
            effective_max = self._real_last_page(analysis, effective_max)
            new_on_page = 0
            for item in analysis["jobs"]:
                cand = self._item_to_candidate(item, city, area, kw)
                if cand is None:
                    continue
                if self._would_be_collected(
                    cand, kw,
                    deal_breakers=deal_breakers, jd_deal_breakers=jd_deal_breakers,
                    blocked_companies=blocked_companies, allow_internship=allow_internship,
                    salary_min=salary_min, salary_max=salary_max,
                ):
                    new_on_page += 1
            if new_on_page > 0:
                hot_pages.update(range(max(1, pp - 3), min(effective_max, pp + 3) + 1))  # ±3 闭区间
                probe_hits.append(f"p{pp}(+{new_on_page})")
                self._log(hooks, f"51job {kw} p{pp} 抽查命中新增 {new_on_page} 个 → 热区 ±3",
                          phase="procuring", keyword=kw, city=city, page=pp)
            else:
                self._log(hooks, f"51job {kw} p{pp} 抽查无新增（全重复/已入库）",
                          phase="procuring", keyword=kw, city=city, page=pp)
            # 波浪停顿：短 60% / 中 25% / 长 15%
            wave_r = random.random()
            if wave_r < WAVE_SHORT_PROB:
                wave_wait = self.uniform(3, 5)
            elif wave_r < WAVE_MEDIUM_PROB:
                wave_wait = self.uniform(8, 15)
            else:
                wave_wait = self.uniform(20, 40)
            if _wait_or_stop(hooks.stop_event, wave_wait):
                return

        # ★ 保底针（地毯队）：探针全空时，从非探针页补足到 70% 覆盖，兜底防漏检
        if not hot_pages:
            final_pool = [p for p in range(start_page, effective_max + 1) if p not in probe_pages]
            final_target = max(3, round(effective_max * 0.7) - len(probe_pages))
            final_pages = random.sample(final_pool, min(final_target, len(final_pool))) if final_pool else []
            # ★★ 固定插针第 2 页（故意设定，不是 BUG，禁止删除）★★
            # 探针第 1 页必采（front_pages 恒含 start_page=1），而第 2 页被探针的
            # 「不相邻约束」排除，若不固定补 p2，第 2 页会成为永久漏检夹缝。
            if 2 in final_pool and 2 not in final_pages and final_pages:
                final_pages[random.randint(0, len(final_pages) - 1)] = 2
            for fp in final_pages:
                if hooks.stop_event is not None and hooks.stop_event.is_set():
                    return
                if not rate_limiter.wait_before_request(hooks.stop_event):
                    return
                fp_a = self._fetch_page(host_target, kw_encoded, area, fp)
                if not fp_a["ok"]:
                    if fp_a.get("signal") == "empty_items":
                        real_last = self._real_last_page(fp_a, effective_max)
                        if fp > real_last:
                            effective_max = real_last
                            continue
                    raise CollectionBlockedError(
                        _reason_code_for(fp_a),
                        f"51job API 异常（L{fp_a['level']} {fp_a['signal']}）：{fp_a['note']}",
                    )
                if not fp_a["jobs"]:
                    continue
                probe_cache[fp] = fp_a
                fp_new = 0
                for fp_item in fp_a["jobs"]:
                    cand = self._item_to_candidate(fp_item, city, area, kw)
                    if cand is None:
                        continue
                    if self._would_be_collected(
                        cand, kw,
                        deal_breakers=deal_breakers, jd_deal_breakers=jd_deal_breakers,
                        blocked_companies=blocked_companies, allow_internship=allow_internship,
                        salary_min=salary_min, salary_max=salary_max,
                    ):
                        fp_new += 1
                if fp_new > 0:
                    hot_pages.update(range(max(1, fp - 3), min(effective_max, fp + 3) + 1))
                    probe_hits.append(f"p{fp}(+{fp_new})")

        # 探针全空 → 饱和，整词跳过（记词级断点 + 清页级断点，避免下次重复探测）
        if not hot_pages:
            if self.safety_conn is not None:
                mark_combo_collected(self.safety_conn, "51job", city, kw)
                delete_page_progress(self.safety_conn, "51job", city, kw)
            self._log(hooks, f"51job {kw} 分布式探针全空（已饱和）：命中 {probe_hits or '无'}，跳过 {effective_max} 页",
                      phase="completed_keyword", keyword=kw, city=city)
            return

        # ★ 随机插一针（兜底抽查）：从非热区页随机挑 1 页加入热区，防热区外扩不足
        non_hot = [p for p in range(1, effective_max + 1) if p not in hot_pages]
        if non_hot:
            hot_pages.add(random.choice(non_hot))

        # 续采起点强制纳入热区，防永久漏采
        if start_page not in hot_pages:
            hot_pages.add(start_page)

        # 探针起点邻位强制纳入（堵孤立夹缝：探针第 1 页必采 → 第 2 页被不相邻约束漏掉）
        stitch_gap = (1 if start_page == 1 else start_page + 1)
        if 1 <= stitch_gap <= effective_max and stitch_gap not in hot_pages:
            hot_pages.add(stitch_gap)

        # 热区按升序扫描，使单页码断点始终代表“此前页码均已处理”。探针本身仍随机，
        # 请求节奏仍有抖动；这里不能再 shuffle，否则中断后从 N+1 续采会漏掉小于 N
        # 但尚未扫描的页面，并可能先遇到末页后提前结束整个热区。
        hot_scan_pages = sorted(p for p in hot_pages if start_page <= p <= effective_max)

        self._log(hooks, f"51job {kw} 探针命中 {len(probe_hits)} 个点，热区扫描范围 {sorted(hot_pages)[:20]}",
                  phase="loading_list", keyword=kw, city=city)

        for page in hot_scan_pages:
            if hooks.stop_event is not None and hooks.stop_event.is_set():
                return

            if page in probe_cache:
                analysis = probe_cache[page]
            else:
                if not rate_limiter.wait_before_request(hooks.stop_event):
                    return
                analysis = self._fetch_page(host_target, kw_encoded, area, page)

            # 异常分级：末页（翻过第 1 页后为空）正常结束本词，其余立即停止
            if not analysis["ok"]:
                if analysis.get("signal") == "empty_items" and page > 1:
                    self._log(hooks, f"51job {kw} 已到末页（第 {page} 页为空），结束本词",
                              phase="loading_list", keyword=kw, city=city, page=page)
                    break
                raise CollectionBlockedError(
                    _reason_code_for(analysis),
                    f"51job API 异常（L{analysis['level']} {analysis['signal']}）：{analysis['note']}",
                )

            jobs_list = analysis["jobs"]
            self._log(hooks, f"51job {kw} 第 {page} 页：{len(jobs_list)} 条（共 {analysis.get('total', 0)}）",
                      phase="saving", keyword=kw, city=city, page=page)

            for item in jobs_list:
                cand = self._item_to_candidate(item, city, area, kw)
                if cand is None:
                    continue
                # collector 增值层过滤（官方无：实习 / 相关性 / 薪资）
                if not self._passes_collector_filters(cand, kw, allow_internship=allow_internship,
                                                      salary_min=salary_min, salary_max=salary_max):
                    continue
                # 官方统一层：去重 + title deal_breakers + blocked_company
                if not hooks.on_list_candidate(cand):
                    continue
                # 官方统一层：jd deal_breakers + 入库；返回 False = 应停止
                if not hooks.on_candidate(cand):
                    raise CollectionBlockedError("callback_stopped", "采集回调已停止")

            # 页级断点：每采完一页立即记录页码
            if self.safety_conn is not None:
                upsert_page_progress(self.safety_conn, "51job", city, kw, page)

            # 末页判定（可靠版）：本页条数 < 每页上限说明已到末页
            if len(jobs_list) < API_PAGE_SIZE:
                self._log(hooks, f"51job {kw} 已到末页（本页 {len(jobs_list)} 条 < {API_PAGE_SIZE}），结束本词",
                          phase="loading_list", keyword=kw, city=city, page=page)
                break

        # 词结束：标记词级断点 + 清页级断点
        if self.safety_conn is not None:
            mark_combo_collected(self.safety_conn, "51job", city, kw)
            delete_page_progress(self.safety_conn, "51job", city, kw)

    @staticmethod
    def _real_last_page(analysis: dict, fallback: int) -> int:
        """主动末页判定：用 API 返回的 total（全词命中总数）反推真实末页。

        51job search-pc 的 totalCount 与 pageNum 无关（是「该词+城市」的总岗位数），
        因此任意一页的正常返回都能算出真实页数 = ceil(total / API_PAGE_SIZE)。

        这是「设定 50 页、实际只 30 页」场景的第一道防线：把热区 ±3 外扩收窄到
        真实末页内，避免翻到「不存在的页」时拿到 HTML → 被误判 non_json（L3）→
        误停整个平台任务。fallback 为拿不到 total 时的兜底页数（保持原上限）。
        """
        total = int(analysis.get("total") or 0)
        if total <= 0:
            return fallback
        total_pages = (total + API_PAGE_SIZE - 1) // API_PAGE_SIZE
        return min(fallback, max(1, total_pages))

    @staticmethod
    def _plan_probe_pages(start_page: int, max_pages: int) -> list[int]:
        """规划探针采样点：前密后疏 + 随机化 + 不相邻约束。

        51job 结果按时间/相关性排序，新增岗位大概率在前几页，所以前段（起点 ~ +9）
        密度高，后段稀疏（最多 3 点）。采样点随机化避免规律数字被风控识别。
        """
        if max_pages < start_page:
            return []
        if max_pages - start_page + 1 <= 2:
            return list(range(start_page, max_pages + 1))

        probe_spacing = max(2, max_pages // 8)
        target_count = max(3, min(max_pages, max_pages // probe_spacing + 1))

        front_end = min(max_pages, start_page + 9)
        back_start = front_end + 1
        front_want = max(2, round(target_count * 0.7))

        front_pool = random.sample(list(range(start_page + 1, front_end + 1)),
                                   k=len(range(start_page + 1, front_end + 1)))
        front_mid: list[int] = []
        front_forbidden: set[int] = {start_page + 1}
        for p in front_pool:
            if p in front_forbidden:
                continue
            front_mid.append(p)
            front_forbidden.add(p - 1)
            front_forbidden.add(p + 1)
            if len(front_mid) >= front_want - 1:
                break
        front_pages = sorted([start_page] + front_mid)
        front_actual = len(front_pages)

        if back_start > max_pages:
            return front_pages

        back_want = min(3, max(1, target_count - front_actual))
        back_pool = random.sample(list(range(back_start, max_pages)),
                                  k=len(range(back_start, max_pages)))
        back_mid: list[int] = []
        back_forbidden: set[int] = set()
        for p in back_pool:
            if p in back_forbidden:
                continue
            back_mid.append(p)
            back_forbidden.add(p - 1)
            back_forbidden.add(p + 1)
            if len(back_mid) >= back_want - 1:
                break
        back_pages = sorted(back_mid + [max_pages])

        pages = list(dict.fromkeys(front_pages + back_pages))
        random.shuffle(pages)
        return pages
