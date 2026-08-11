"""Shared job filtering helpers."""

import re


HR_ACTIVITY_CATEGORIES = {"recent_3d", "week", "month", "older", "unknown"}


def matching_deal_breaker(text: str, deal_breakers: list[str]) -> str | None:
    """Return the first deal-breaker keyword found in text."""
    text_lower = text.lower()
    for keyword in deal_breakers:
        cleaned_keyword = keyword.strip()
        if cleaned_keyword and cleaned_keyword.lower() in text_lower:
            return keyword
    return None


def parse_monthly_salary_k(salary: str) -> tuple[float, float] | None:
    """Parse common monthly K salary labels into a comparable range."""
    normalized = str(salary or "").strip()
    range_match = re.search(
        r"(\d+(?:\.\d+)?)\s*[kK]?\s*-\s*(\d+(?:\.\d+)?)\s*[kK]",
        normalized,
    )
    if range_match:
        low, high = (float(value) for value in range_match.groups())
        return (min(low, high), max(low, high))

    single_match = re.search(r"(\d+(?:\.\d+)?)\s*[kK](?!\w)", normalized)
    if single_match:
        value = float(single_match.group(1))
        return value, value
    return None


def classify_hr_activity(activity: str) -> str:
    """Group BOSS recruiter activity labels into stable filter buckets."""
    normalized = str(activity or "").strip()
    if not normalized:
        return "unknown"
    if any(keyword in normalized for keyword in ("在线", "刚刚", "今日", "昨日")):
        return "recent_3d"

    day_match = re.search(r"(\d+)\s*日内活跃", normalized)
    if day_match:
        days = int(day_match.group(1))
        if days <= 3:
            return "recent_3d"
        if days <= 7:
            return "week"
        if days <= 31:
            return "month"
        return "older"

    if "本周活跃" in normalized:
        return "week"

    week_match = re.search(r"(\d+)\s*周内活跃", normalized)
    if week_match:
        return "week" if int(week_match.group(1)) <= 1 else "month"

    if "本月活跃" in normalized:
        return "month"
    if re.search(r"\d+\s*月内活跃", normalized) or "年前活跃" in normalized or "半年前活跃" in normalized:
        return "older"
    return "unknown"
