"""Pre-filter module - hard filtering before LLM evaluation."""

import re

from bosshunter.job_filters import matching_blocked_company, matching_deal_breaker, parse_monthly_salary_k


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

    user_salary_min = _as_number(profile.get("salary_min", 0))
    user_salary_max = _as_number(profile.get("salary_max", 0))
    job_salary = parse_monthly_salary_k(job.get("salary") or "")
    if job_salary is not None:
        job_min, job_max = job_salary
        if user_salary_min > 0 and job_max < user_salary_min:
            return 0, f"薪资低于硬性要求: {_format_k(job_max)}K < {_format_k(user_salary_min)}K"
        if user_salary_max > 0 and job_min > user_salary_max:
            return 0, f"薪资高于预期范围: {_format_k(job_min)}K > {_format_k(user_salary_max)}K"

    return 100, "预筛通过"


def _contains_internship_signal(job: dict) -> bool:
    title = (job.get("title") or "").lower()
    return any(keyword.lower() in title for keyword in _INTERNSHIP_KEYWORDS)


def _as_number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def _format_k(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)
