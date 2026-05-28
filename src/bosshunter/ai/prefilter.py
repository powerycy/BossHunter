"""Pre-filter module - Keyword-based quick scoring before LLM evaluation."""

import re


def quick_score(job: dict, config: dict) -> tuple[int, str]:
    """Compute a quick keyword-based score (0-100) without LLM.

    Two hard rejections:
    - Deal-breaker keywords in title/JD → instant 0
    - Salary has no overlap with user range → instant 0

    Two scoring dimensions (50/50):
    - Experience years match: max 50
    - JD search keyword hits: max 50 (+10 per hit)

    Returns (score, reason_string).
    """
    profile = config.get("profile", {})
    deal_breakers = profile.get("deal_breakers", [])
    salary_min = profile.get("salary_min", 0)
    salary_max = profile.get("salary_max", 0)

    keywords = config.get("search", {}).get("keywords", [])

    title = (job.get("title") or "").lower()
    jd = (job.get("jd") or "").lower()
    salary_text = job.get("salary") or ""

    reasons = []

    # === Hard rejections ===

    # Deal-breaker check: title only, not JD
    # (user may have relevant skills in these areas without wanting pure roles)
    for breaker in deal_breakers:
        if breaker.lower() in title:
            return 0, f"触发排除词: {breaker}"

    # Internship rejection: unless user is looking for internships (salary_min < 5K)
    if salary_min >= 5:
        if "实习" in title or "元/天" in salary_text or "元/日" in salary_text:
            return 0, "实习岗位"

    # Salary overlap check: no overlap = instant reject
    if salary_text and salary_min > 0:
        parsed_min, parsed_max = _parse_salary(salary_text)
        if parsed_min > 0:
            user_max = salary_max if salary_max > 0 else 999
            if not (parsed_max >= salary_min and parsed_min <= user_max):
                return 0, f"薪资不匹配({salary_text})"
            else:
                reasons.append("薪资匹配")

    # === Scoring dimensions ===

    score = 0

    # Dimension 1: Experience years match (max 50)
    exp_text = job.get("experience") or ""
    exp_score = _score_experience(exp_text, profile)
    score += exp_score
    if exp_score >= 50:
        reasons.append("经验精确匹配")
    elif exp_score >= 35:
        reasons.append("经验基本匹配")
    elif exp_score >= 20:
        reasons.append("经验不限/无信息")
    else:
        reasons.append("经验差距较大")

    # Dimension 2: JD search keyword hits (max 50, +10 per hit)
    jd_hits = 0
    hit_words = []
    for kw in keywords:
        if kw.lower() in jd:
            jd_hits += 1
            hit_words.append(kw)
    jd_bonus = min(jd_hits * 10, 50)
    score += jd_bonus
    if jd_hits > 0:
        reasons.append(f"JD命中{jd_hits}个({','.join(hit_words[:3])})")

    reason_str = "; ".join(reasons) if reasons else "无匹配信号"
    return min(score, 100), reason_str


def _parse_salary(salary_text: str) -> tuple[int, int]:
    """Parse salary text like '15-25K' or '15-25K·14薪' into (min_k, max_k)."""
    match = re.search(r'(\d+)\s*[-~]\s*(\d+)\s*[kK]', salary_text)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r'(\d+)\s*[kK]', salary_text)
    if match:
        val = int(match.group(1))
        return val, val
    return 0, 0


def _score_experience(exp_text: str, profile: dict) -> int:
    """Score experience years match. Returns 0-50.

    - Exact match: 50
    - Slightly over or under: 35
    - 不限 or no info: 20
    - Way off: 5
    """
    if not exp_text:
        return 20  # No info

    if "不限" in exp_text:
        return 20

    match = re.search(r'(\d+)\s*[-~]\s*(\d+)', exp_text)
    if not match:
        match = re.search(r'(\d+)', exp_text)
        if not match:
            return 20  # Can't parse
        req_min = int(match.group(1))
        req_max = req_min + 3
    else:
        req_min = int(match.group(1))
        req_max = int(match.group(2))

    # Estimate user's years from salary expectation
    salary_min = profile.get("salary_min", 0)
    if salary_min >= 30:
        user_exp_est = 10
    elif salary_min >= 25:
        user_exp_est = 8
    elif salary_min >= 20:
        user_exp_est = 7
    elif salary_min >= 15:
        user_exp_est = 5
    else:
        user_exp_est = 3

    # Exact: user falls within required range
    if req_min <= user_exp_est <= req_max:
        return 50
    # Slightly over (overqualified by <=3 years)
    elif user_exp_est > req_max and user_exp_est - req_max <= 3:
        return 35
    # Slightly under (underqualified by <=2 years)
    elif user_exp_est < req_min and req_min - user_exp_est <= 2:
        return 35
    # Way off
    else:
        return 5
