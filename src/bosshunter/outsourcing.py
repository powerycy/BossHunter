"""Heuristic detection of outsourcing signals on scraped BOSS直聘 jobs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# Strong outsourcing signals — these phrases and the curated vendor list are
# treated as "confirmed outsourcing" by the UI (darker badge).
OUTSOURCING_KEYWORDS_HARD: tuple[str, ...] = (
    "外包",
    "驻场",
    "派遣",
    "劳务派遣",
    "人力外包",
    "IT外包",
    "人力资本",
    "人力咨询",
    "人力服务",
    "人力资源外包",
    "IT人力",
    "人力外包公司",
)

# Weaker signals — common in vendor copy but also in legitimate in-house roles.
# They still surface an orange "可能外包" badge but don't trigger the confirmed
# color unless a hard signal also fires.
OUTSOURCING_KEYWORDS_SOFT: tuple[str, ...] = (
    "解决方案",
    "技术服务",
    "出差",
    "现场",
    "国内领先",
    "服务提供商",
    "数字化转型",
    "定制化系统",
    "一站式服务",
    "为客户提供",
    "一体化",
    "晋升途径",
    "工作地点",
    "外派",
    "学信网",
)

# Known outsourcing / 人力外派 vendors. Matched against the `company` field only —
# these are unambiguous vendor names, not generic phrases, so we don't need to
# scan the JD.
OUTSOURCING_COMPANIES: tuple[str, ...] = (
    "博朗软件",
    "中软国际",
    "东软集团",
    "博彦科技",
    "中电金信",
    "法本信息",
    "浙大网新",
    "奥博杰天",
    "浪潮",
    "软通动力",
    "福瑞博德",
    "信必优",
    "大展科技",
    "恒生电子",
    "日电卓越软件",
    "大连华信",
    "中和软件",
    "新致软件",
    "艾斯克雷",
    "海隆软件",
    "大宇宙信息",
    "晟峰软件",
    "富士通信息",
    "NTTDATA",
    "宏智科技",
    "神州数码通用软件",
    "凌志软件",
    "音泰思",
    "微创软件",
    "开目佰钧成",
    "浩鲸智能",
    "诚迈科技",
    "润和软件",
    "ST 新海",
    "慧博云通",
    "天源迪科",
    "上海思芮",
    "塔塔",
    # 2026-07-31 社区补充：覆盖华南/华北/华东常见 IT 外包厂商
    "上海易立德信息技术股份有限公司",
    "博悦科创",
    "海万科技",
    "深圳市先进数通融安信息技术",
    "深圳宝润兴业",
    "润杨金融",
    "深圳雁联技术",
    "上海易宝软件有限公司",
    "睿信天和",
    "小草互联",
    "拓维信息",
    "上海中软华腾软件系统有限公司",
    "懿华软件",
    "深圳市金卫信",
    "深圳市集益创新信息技术有限公司",
    "深圳市德科信息技术有限公司",
    "深圳市布雷泽科技有限公司",
    "博彦科技（深圳）有限公司",
    "深圳市法本信息技术股份有限公司",
    "申朴信息",
    "深圳索信达数据技术有限公司",
    "纬创软件（北京）有限公司",
    "纬创软件（武汉）有限公司",
    "北京长亮合度信息技术有限公司",
    "深圳市长亮保泰信息科技有限公司",
    "深圳市长亮核心科技有限公司",
    "深圳市长亮科技股份有限公司",
    "深圳四方精创资讯股份有限公司北京分公司",
    "深圳四方精创资讯股份有限公司",
    "北京百胜扬软件技术有限公司",
    "北京新思软件技术有限公司",
    "马衡达信息技术（上海）有限公司",
    "上海彧求信息科技有限公司",
    "上海微创软件股份有限公司",
    "上海海万信息科技股份有限公司",
    "上海汉得信息技术股份有限公司",
    "上海艾融软件股份有限公司",
    "广州赛意信息科技股份有限公司",
    "深圳中科软科技信息系统有限公司",
    "深圳市易思博软件技术有限公司",
    "深圳市雁联计算系统有限公司",
    "深圳鹏开信息技术有限公司",
    "深圳市博奥特科技有限公司",
    "深圳银兴科技开发有限公司",
    "深圳兴融联科技有限公司",
    "深圳市紫川软件有限公司",
    "博彦科技股份有限公司",
    "华通科技有限公司",
    "中软国际有限公司",
    "前海泰坦科技（深圳）有限公司",
    "大展信息科技（深圳）有限公司",
    "软通动力信息技术（集团）有限公司",
    "南京绛门信息科技股份有限公司",
    "武汉佰钧成技术有限责任公司",
    "大连文思海辉信息技术有限公司",
    "西安华炎信息科技有限公司",
    "亿达信息技术有限公司 YIDATEC",
    "信必优(深圳)信息技术有限公司",
    "凯捷咨询(中国)有限公司",
    "印孚瑟斯技术（中国）有限公司",
    "信雅达系统工程股份有限公司",
    "深圳市脉山龙信息技术股份有限公司",
    "宇信科技",
    "厦门云之颠",
    "厦门诚迈科技",
    "京北方",
    "厦门真人力",
    "厦门赛意信息",
    "云锐人才服务",
    # 2026-07-31 社区补充（第二批）：按用户原文保留
    "外企德科",
    "文思海辉",
    "纬创软件",
    "科锐国际",
    "橙色魔方",
    "讯锡科技",
    "七凌科技",
    "网新新思",
    "人瑞集团",
    "神州新桥",
    "汉克时代",
    "旭阳软件",
    "金证股份",
    "法本",
    "神州信息",
    "龙通科技",
    "亿达信息",
    "拓维云创",
    "赛意信息",
    "汉得信息",
    "长亮科技",
    "睿服科技",
    "海万信息",
    "先进数通",
    "云盈网络",
    "上海微创",
    "易商数科",
    "华创云鼎",
    "紫川软件",
    "拓保软件",
    "新炬网络",
    "合生科技",
    "博奥特",
    "中科软",
    "源创科技",
    "兴融联",
    "亚信科技",
    "法本信息",
    "深圳易宝",
    "彩讯科技",
    "深圳拓保",
    "绛门科技",
    "合肥凯捷",
    "北京宇信科技集团",
    "江苏润和软件",
    "深圳大展信息科技",
    "深圳德科信息技术",
    "广州凯泽利",
    "武汉软帝联合科技",
    "深圳智慧盾",
    "浩鲸科技",
)

# Regex patterns lifted from the boss-outsourcing-tips project. Each entry is
# (compiled_pattern, display_name). The display name is what shows up in the
# matches list and the badge label when the pattern fires.
# Pattern source: 匹配od，但是忽略node. Preserved verbatim even though its `\b`
# boundaries make it mostly a no-op on Chinese text — kept for parity with the
# upstream rule set.
_OUTSOURCING_REGEX_RAW: tuple[tuple[str, str], ...] = (
    (r"\b(?!(?:node\b))w*odw*\b", "od"),
)
OUTSOURCING_REGEX: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), name)
    for pattern, name in _OUTSOURCING_REGEX_RAW
)

_SCAN_FIELDS = ("title", "company", "jd", "company_industry")


def match_keywords(text: str, keywords) -> list[str]:
    """Return the keywords that appear in text (case-insensitive substring).

    Only outer whitespace is stripped from each keyword — interior spaces are
    preserved (e.g. ``"ST 新海"`` stays two tokens apart).
    """
    if not text:
        return []
    haystack = str(text).lower()
    hits: list[str] = []
    for kw in keywords or ():
        cleaned = (kw or "").strip()
        if cleaned and cleaned.lower() in haystack:
            hits.append(cleaned)
    return hits


def match_regex(text: str, patterns) -> list[str]:
    """Return display names of regex patterns that match anywhere in text."""
    if not text:
        return []
    haystack = str(text)
    hits: list[str] = []
    for pattern, name in patterns or ():
        if pattern.search(haystack) and name not in hits:
            hits.append(name)
    return hits


def detect_outsourcing_signals(job: dict, deal_breakers: list[str] | None = None) -> dict:
    """Inspect a job record and return the outsourcing/deal_breaker signal bundle.

    Returns:
        outsourcing: True if any soft or hard signal fired
        outsourcing_confirmed: True if a hard signal (company name or hard
            keyword) also fired — the UI uses this to switch to a darker pill
        outsourcing_matches: ordered list — company hits first, then hard
            keywords, soft keywords, regex hits
        deal_breaker: True if any user-defined exclusion word hit
        deal_breaker_matches: sorted list of matched exclusion words
    """
    company_hits: list[str] = []
    hard_hits: list[str] = []
    soft_hits: list[str] = []
    regex_hits: list[str] = []
    db_hits: set[str] = set()

    company_value = job.get("company", "")
    for hit in match_keywords(company_value, OUTSOURCING_COMPANIES):
        if hit not in company_hits:
            company_hits.append(hit)

    for field in _SCAN_FIELDS:
        value = job.get(field, "")
        for hit in match_keywords(value, OUTSOURCING_KEYWORDS_HARD):
            if hit not in hard_hits:
                hard_hits.append(hit)
        for hit in match_keywords(value, OUTSOURCING_KEYWORDS_SOFT):
            if hit not in soft_hits:
                soft_hits.append(hit)
        for hit in match_regex(value, OUTSOURCING_REGEX):
            if hit not in regex_hits:
                regex_hits.append(hit)
        for hit in match_keywords(value, deal_breakers or ()):
            db_hits.add(hit)

    all_os_hits = company_hits + hard_hits + soft_hits + regex_hits
    confirmed = bool(company_hits) or bool(hard_hits)
    return {
        "outsourcing": bool(all_os_hits),
        "outsourcing_confirmed": confirmed,
        "outsourcing_matches": all_os_hits,
        "deal_breaker": bool(db_hits),
        "deal_breaker_matches": sorted(db_hits),
    }


# ---------------------------------------------------------------------------
# v1.9 multi-layer outsourcing detection
# ---------------------------------------------------------------------------
# Signal layers (ROADMAP §6 D15):
#   L0 company-name hit (strict whole-token)        → confirmed
#   L1 hard-keyword hit                              → confirmed
#   L2 soft-keyword hit                              → suspected
#   L3 structural clue (JD/title patterns)          → suspected (opt-in)
#   L4 HR reply-text evidence                        → confirmed (writes
#                                                       risk_events + user_marks;
#                                                       opt-in, default off)
#   L5 user mark (button)                            → confirmed (Phase 2)
#   L6 cross-field contradiction (industry 自研 vs  → silent demotion
#       signals)                                       (suspected→clean or
#                                                       confirmed→suspected)
#
# Precedence: L5 > L4 > L0/L1 > L3 > L2, with L6 able to demote one step.
# L4 forward propagation requires ≥ N independent sources (see
# ``should_forward_propagate_outsourcing`` in db.py).


# L3 structural clue vocabulary. JD-only: "驻场补贴/项目奖金/出差补贴" are
# pay-structure tells that don't appear in in-house JD copy.
_L3_STRUCTURAL_JD: tuple[str, ...] = (
    "驻场补贴",
    "项目奖金",
    "出差补贴",
    "客户现场",
)

# L3 employment-mode tells on title. BOSS exposes ``employment_type`` only
# on some jobs; when absent we apply the heuristic, otherwise we skip the
# title check to avoid double-counting.
_L3_EMPLOYMENT_TITLE: tuple[str, ...] = (
    "6个月",
    "项目周期",
    "短期",
    "兼职",
)

# L6 industry whitelist — companies that claim a self-developed product
# line. Any industry token in this list triggers a demotion when the
# job's own detection is only suspected or when an industry claim
# contradicts a confirmed verdict.
_L6_SELF_DEVELOPED_TOKENS: tuple[str, ...] = (
    "互联网",
    "自研",
    "产品",
)


@dataclass(frozen=True)
class Rules:
    """Merged detection rules (built-in defaults + user config override).

    Built by :func:`load_rules`. The dataclass is frozen so callers can
    treat it as a stable cache key (the scraper computes signals once per
    ``Rules`` instance and reuses the result across thousands of jobs).
    """

    enabled: bool
    companies: tuple[str, ...]
    keywords_hard: tuple[str, ...]
    keywords_soft: tuple[str, ...]
    detect_structural: bool
    use_reply_history: bool
    use_user_marks: bool
    forward_propagate_n: int


def load_rules(config: dict | None) -> Rules:
    """Merge ``config['outsourcing_rules']`` with the built-in defaults.

    User lists are appended (not replaced) so the community-built vendor
    registry keeps growing. Disabled rule returns the same shape with
    empty keyword/vendor lists and ``enabled=False`` — callers short-
    circuit cleanly.
    """
    cfg = config or {}
    rules_cfg = cfg.get("outsourcing_rules", {}) if isinstance(cfg, dict) else {}
    if not isinstance(rules_cfg, dict):
        rules_cfg = {}
    enabled = bool(rules_cfg.get("enabled", True))

    user_companies = tuple(rules_cfg.get("companies_user", []) or ())
    user_hard = tuple(rules_cfg.get("keywords_hard_user", []) or ())
    user_soft = tuple(rules_cfg.get("keywords_soft_user", []) or ())

    companies = _dedup_preserve_order(OUTSOURCING_COMPANIES + user_companies)
    keywords_hard = _dedup_preserve_order(OUTSOURCING_KEYWORDS_HARD + user_hard)
    keywords_soft = _dedup_preserve_order(OUTSOURCING_KEYWORDS_SOFT + user_soft)

    return Rules(
        enabled=enabled,
        companies=companies,
        keywords_hard=keywords_hard,
        keywords_soft=keywords_soft,
        detect_structural=bool(rules_cfg.get("detect_structural", True)),
        use_reply_history=bool(rules_cfg.get("use_reply_history", False)),
        use_user_marks=bool(rules_cfg.get("use_user_marks", True)),
        forward_propagate_n=int(rules_cfg.get("forward_propagate_n", 2)),
    )


def _dedup_preserve_order(items: tuple[str, ...]) -> tuple[str, ...]:
    """De-duplicate while keeping the first occurrence's position."""
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if not it:
            continue
        if it in seen:
            continue
        seen.add(it)
        out.append(it)
    return tuple(out)


# Token-stripping regex for whole-token company matching: drop everything
# that's not a Chinese ideograph, a letter, or a digit so vendor names
# like "博朗软件" don't substring-match a legal name like "上海博朗实业".
_TOKEN_RE = re.compile(r"[^一-鿿\w]+", re.UNICODE)


def _match_company_strict(company: str, vendors: tuple[str, ...]) -> list[str]:
    """Whole-token vendor match (L0).

    Returns the vendor names whose normalised form appears as a whole
    token inside the normalised company field. The substring fallback is
    unsafe for company names because Chinese vendor names share many
    short suffixes ("软件/科技/信息/技术") with legitimate employers —
    "上海博朗实业" would otherwise match "博朗软件".
    """
    if not company or not vendors:
        return []
    haystack = _TOKEN_RE.sub("", str(company)).lower()
    if not haystack:
        return []
    hits: list[str] = []
    for vendor in vendors:
        needle = _TOKEN_RE.sub("", str(vendor)).lower()
        if not needle:
            continue
        if needle in haystack and vendor not in hits:
            hits.append(vendor)
    return hits


def _match_structural(job: dict) -> list[dict]:
    """L3 structural clues (suspected only).

    JD-side pay-structure tells always fire. Title-side employment-mode
    tells only fire when ``employment_type`` is missing on the row (BOSS
    surfaces it inconsistently, and we don't want to contradict an
    explicit ``full-time`` claim).
    """
    hits: list[dict] = []
    jd = job.get("jd", "") or ""
    for kw in _L3_STRUCTURAL_JD:
        if kw in jd:
            hits.append({"layer": "L3", "keyword": kw, "field": "jd"})
    if not job.get("employment_type"):
        title = job.get("title", "") or ""
        for kw in _L3_EMPLOYMENT_TITLE:
            if kw in title:
                hits.append({"layer": "L3", "keyword": kw, "field": "title"})
    return hits


def _match_cross_field_contradiction(job: dict) -> bool:
    """L6 industry-claims-self-developed trigger."""
    industry = job.get("company_industry", "") or ""
    if not industry:
        return False
    return any(tok in industry for tok in _L6_SELF_DEVELOPED_TOKENS)


def _dedup_layers(layers: list[dict]) -> list[str]:
    """De-duplicate layer ids in first-seen order."""
    seen: list[str] = []
    for m in layers:
        lid = m.get("layer")
        if lid and lid not in seen:
            seen.append(lid)
    return seen


def compute_outsourcing_columns(job: dict, rules: Rules) -> dict:
    """Compute the five jobs-table columns to persist at insert time.

    Returns a dict with the column names (``outsourcing_level``,
    ``outsourcing_confirmed``, ``outsourcing_matches``,
    ``outsourcing_layers``) plus an explicit ``outsourcing_updated_at``
    set to ``CURRENT_TIMESTAMP`` (the DB default would also work, but
    returning the value lets ``insert_job`` write it verbatim when needed
    by tests or batch scripts).

    The matchers intentionally do not consume ``deal_breakers`` — those
    are user-config exclusion words independent of the outsourcing enum
    (kept orthogonal in the schema: a job can be ``deal_breaker=True``
    AND ``outsourcing_level=clean``).

    L4 reply-text and L5 user-mark are NOT applied here — those are
    cross-job signals that the scraper wires in via the forward-prop
    gate (``db.should_forward_propagate_outsourcing``).
    """
    if not rules.enabled:
        return {
            "outsourcing_level": "clean",
            "outsourcing_confirmed": 0,
            "outsourcing_matches": None,
            "outsourcing_layers": None,
            "outsourcing_updated_at": None,
        }

    layers: list[dict] = []

    # L0 — strict company match
    for hit in _match_company_strict(job.get("company", ""), rules.companies):
        layers.append({"layer": "L0", "keyword": hit, "field": "company"})

    # L1 / L2 — keyword hit on the four scan fields
    for field in _SCAN_FIELDS:
        value = job.get(field, "") or ""
        for hit in match_keywords(value, rules.keywords_hard):
            if not _has_match(layers, field, hit, ("L0", "L1")):
                layers.append({"layer": "L1", "keyword": hit, "field": field})
        for hit in match_keywords(value, rules.keywords_soft):
            if not _has_match(layers, field, hit, ("L2",)):
                layers.append({"layer": "L2", "keyword": hit, "field": field})
        for hit in match_regex(value, OUTSOURCING_REGEX):
            layers.append({"layer": "L2", "keyword": hit, "field": field})

    # L3 — structural clues (opt-in)
    if rules.detect_structural:
        layers.extend(_match_structural(job))

    has_confirmed = any(m["layer"] in ("L0", "L1") for m in layers)
    level = "confirmed" if has_confirmed else ("suspected" if layers else "clean")

    # L6 — industry-claims-self-developed silent demotion (one step down).
    if level in ("confirmed", "suspected") and _match_cross_field_contradiction(job):
        if level == "confirmed":
            level = "suspected"
        else:
            level = "clean"
            layers = []

    return {
        "outsourcing_level": level,
        "outsourcing_confirmed": 1 if has_confirmed else 0,
        "outsourcing_matches": json.dumps(layers, ensure_ascii=False) if layers else None,
        "outsourcing_layers": json.dumps(_dedup_layers(layers), ensure_ascii=False) if layers else None,
        "outsourcing_updated_at": "CURRENT_TIMESTAMP",
    }


def _has_match(
    layers: list[dict], field: str, keyword: str, layer_ids: tuple[str, ...]
) -> bool:
    """True if a (layer, field, keyword) triple already exists."""
    return any(
        m.get("layer") in layer_ids
        and m.get("field") == field
        and m.get("keyword") == keyword
        for m in layers
    )


def classify_reply_text(text: str, rules: Rules) -> list[dict]:
    """L4: scan an HR reply body for outsourcing signals.

    Returns ``[{layer: 'L4', keyword: '...'}]`` ordered by appearance.
    Empty list when ``rules.use_reply_history`` is False (the default —
    L4 is opt-in because a single HR mis-reply can poison a legitimate
    employer's record; the caller wires in the forward-propagation N-gate
    to mitigate).
    """
    if not rules.enabled or not rules.use_reply_history:
        return []
    if not text:
        return []
    hits: list[dict] = []
    for kw in rules.keywords_hard:
        if kw in text and not any(h["keyword"] == kw for h in hits):
            hits.append({"layer": "L4", "keyword": kw, "field": "reply"})
    return hits


def parse_persisted_columns(row: dict) -> dict:
    """Hydrate persisted JSON columns back into dict/list shape.

    Used by the read path so ``JobsTable`` / ``JobDetailModal`` can keep
    consuming a flat dict without parsing JSON themselves. JSON decode
    errors fall back to empty lists so a corrupted row never breaks the
    API.
    """
    matches_raw = row.get("outsourcing_matches")
    layers_raw = row.get("outsourcing_layers")
    try:
        matches = json.loads(matches_raw) if matches_raw else []
    except (TypeError, ValueError):
        matches = []
    try:
        layers = json.loads(layers_raw) if layers_raw else []
    except (TypeError, ValueError):
        layers = []
    return {
        "outsourcing": bool(matches),
        "outsourcing_confirmed": bool(row.get("outsourcing_confirmed")),
        "outsourcing_matches": [m.get("keyword", "") for m in matches if isinstance(m, dict)],
        "outsourcing_layers": layers,
        "outsourcing_level": row.get("outsourcing_level") or "clean",
        "outsourcing_updated_at": row.get("outsourcing_updated_at"),
    }
