"""Pre-filter module - hard filtering before LLM evaluation."""

import re

from bosshunter.job_filters import classify_hr_activity, matching_deal_breaker


_INTERNSHIP_KEYWORDS = ("实习", "intern", "internship", "管培")
_ANONYMOUS_COMPANY_PATTERN = re.compile(
    r"^(?:[\u4e00-\u9fff]{2,4})?某.+(?:公司|企业|集团)$"
)


def quick_score(job: dict, config: dict) -> tuple[int, str]:
    """Apply hard filters before LLM scoring."""
    profile = config.get("profile", {})
    deal_breakers = profile.get("deal_breakers", [])
    jd_deal_breakers = profile.get("jd_deal_breakers", [])
    title = job.get("title") or ""
    jd = job.get("jd") or ""
    company = str(job.get("company") or "").strip()

    if _ANONYMOUS_COMPANY_PATTERN.search(company):
        return 0, "匿名公司岗位"

    breaker = matching_deal_breaker(title, deal_breakers)
    if breaker:
        return 0, f"触发排除词: {breaker}"

    jd_breaker = matching_deal_breaker(jd, jd_deal_breakers)
    if jd_breaker:
        return 0, f"触发JD排除词: {jd_breaker}"

    if not profile.get("allow_internship", False) and _contains_internship_signal(job):
        return 0, "实习/管培岗位"

    salary_min = _as_number(profile.get("salary_min", 0))
    salary_max = _parse_salary_max_k(job.get("salary") or "")
    if salary_min > 0 and salary_max is not None and salary_max < salary_min:
        return 0, f"薪资低于硬性要求: {_format_k(salary_max)}K < {_format_k(salary_min)}K"

    scoring = config.get("scoring", {})
    if scoring.get("require_recent_hr_activity", True):
        activity = str(job.get("hr_active") or "").strip()
        activity_category = classify_hr_activity(activity)
        allow_unknown = scoring.get("allow_unknown_hr_activity", True)
        if activity_category != "recent_3d" and not (activity_category == "unknown" and allow_unknown):
            display_activity = activity or "活跃度未知"
            return 0, f"招聘者不符合近3日活跃要求: {display_activity}"

    return 100, "预筛通过"


def _contains_internship_signal(job: dict) -> bool:
    title = (job.get("title") or "").lower()
    return any(keyword.lower() in title for keyword in _INTERNSHIP_KEYWORDS)


def _parse_salary_max_k(salary: str) -> float | None:
    range_match = re.search(r"(\d+(?:\.\d+)?)\s*[kK]?\s*-\s*(\d+(?:\.\d+)?)\s*[kK]", salary)
    if range_match:
        return max(float(range_match.group(1)), float(range_match.group(2)))

    single_match = re.search(r"(\d+(?:\.\d+)?)\s*[kK]", salary)
    if single_match:
        return float(single_match.group(1))

    return None


def _as_number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def _format_k(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)
