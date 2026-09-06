"""Pre-filter module - hard filtering before LLM evaluation."""

import re

from bosshunter.job_filters import matching_blocked_company, matching_deal_breaker


_INTERNSHIP_KEYWORDS = ("实习", "intern", "internship", "管培")
_ANONYMOUS_COMPANY_PATTERN = re.compile(
    r"^(?:[\u4e00-\u9fff]{2,4})?某.+(?:公司|企业|集团)$"
)


def quick_score(job: dict, config: dict) -> tuple[int, str]:
    """Apply hard filters before LLM scoring."""
    profile = config.get("profile", {})
    deal_breakers = profile.get("deal_breakers", [])
    jd_deal_breakers = profile.get("jd_deal_breakers", [])
    blocked_companies = profile.get("blocked_companies", [])
    title = job.get("title") or ""
    jd = job.get("jd") or ""
    company = str(job.get("company") or "").strip()

    blocked_company = matching_blocked_company(company, blocked_companies)
    if blocked_company:
        return 0, f"触发公司屏蔽: {blocked_company}"

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
    salary_max = _as_number(profile.get("salary_max", 0))
    parsed_salary = _parse_salary_range_k(job.get("salary") or "")
    if parsed_salary is None:
        if _as_bool(profile.get("filter_unparsed_salary", True)):
            return 0, "薪资面议/无法解析，已过滤"
        return 100, "薪资面议/无法解析（已关闭过滤，交由 AI 判断）"

    job_salary_min, job_salary_max = parsed_salary
    if salary_min > 0 and job_salary_max < salary_min:
        return 0, f"薪资低于硬性要求: {_format_k(job_salary_max)}K < {_format_k(salary_min)}K"

    salary_ceil_ratio = max(_as_number(profile.get("salary_ceil_ratio", 1.5)), 1.0)
    if salary_max > 0 and job_salary_min > salary_max * salary_ceil_ratio:
        return 0, (
            f"薪资远超期望上限: 报价下限 {_format_k(job_salary_min)}K > "
            f"{_format_k(salary_max)}K × {salary_ceil_ratio:g}"
        )

    return 100, "预筛通过"


def _contains_internship_signal(job: dict) -> bool:
    title = (job.get("title") or "").lower()
    return any(keyword.lower() in title for keyword in _INTERNSHIP_KEYWORDS)


def _parse_salary_range_k(salary: str) -> tuple[float, float] | None:
    range_match = re.search(r"(\d+(?:\.\d+)?)\s*[kK]?\s*-\s*(\d+(?:\.\d+)?)\s*[kK]", salary)
    if range_match:
        low = float(range_match.group(1))
        high = float(range_match.group(2))
        return low, max(low, high)

    single_match = re.search(r"(\d+(?:\.\d+)?)\s*[kK]", salary)
    if single_match:
        value = float(single_match.group(1))
        return value, value

    return None


def _as_number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "是"}
    return True


def _format_k(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)
