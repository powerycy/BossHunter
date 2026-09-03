"""AI Scorer - Match jobs against resume using Claude API."""

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
import json
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from bosshunter.ai.credentials import AIRequestError, call_anthropic_text, get_ai_api_key
from bosshunter.cancellation import OperationCancelled, run_cancellable
from bosshunter.collection.text import clean_job_description
from bosshunter.db import (
    add_history,
    get_db,
    get_jobs_by_status,
    reset_ai_filtered_jobs,
    update_job_score,
    update_job_status,
    update_job_quick_score,
)
from bosshunter.ai.prefilter import quick_score
from bosshunter.scoring_selection import select_scoring_jobs, validate_options

console = Console()

def get_scoring_concurrency(config: dict) -> int:
    """Return a conservative, user-configurable AI scoring worker count."""
    raw_value = config.get("ai", {}).get("scoring_concurrency", 1)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = 1
    return max(1, min(value, 3))


SCORING_PROMPT = """你是一位严谨的招聘匹配评估员。请只依据简历与岗位JD中明确出现的事实进行评估，不补全、不猜测候选人能力。

## 候选人简历
{resume}

## 候选人个人信息
- 最高学历：{candidate_education}
- 求职招聘类型：{candidate_recruitment_type}
- 期望薪资区间：{salary_min}K-{salary_max}K
- 薪资上限放宽线：{salary_ceil}K

## 岗位信息
- 职位：{title}
- 公司：{company}
- 薪资：{salary}
- 要求：{experience}
- 学历要求：{education}
- 招聘类型：{recruitment_type}
- JD：{jd}

## 统一评分维度
逐项给分，不要自行输出总分；程序会统一求和：
1. 核心职责匹配（0-40分）：简历中有明确证据覆盖JD主要日常职责。
2. 可迁移证据（0-25分）：过往成果、工作方法和相邻经验能否迁移到该岗位。
3. 硬性要求（0-15分）：年限、学历、必备技能等明确硬要求的满足程度。
4. 工具与行业（0-10分）：工具、产品类型、客户类型或行业背景；JD仅写“优先/加分”时不能当作硬缺口。
5. 实际条件（0-10分）：城市、薪资、工作方式和稳定性等可判断条件。

## 薪资判断
若用户填写了期望薪资上限，岗位薪资在放宽线以内时不要因“略高”扣重分；明显高出时只影响“实际条件”，不要覆盖核心职责与硬性要求判断。

## 封顶规则
仅在JD把相关内容作为核心职责或明确必备条件，且简历没有相应证据时填写caps：
- technical_required：必须掌握SQL、Linux、编程、服务器/私有化部署等硬技术，最终最高55分。
- sales_acquisition_core：岗位核心是销售获客、业绩指标或陌生开发，但简历没有对应证据，最终最高65分。
- weak_core_transfer：只有少量辅助职责可迁移，核心工作缺少直接或相邻证据，最终最高70分。
行业“优先”、工具可入职后学习、普通协作事项均不得触发封顶。hard_gaps只写JD明确要求且简历确实缺失的内容。

请严格输出一个JSON对象，不要Markdown，不要额外说明。五个score必须是整数且不得超过各自上限：
{{
  "role_summary": "岗位核心工作概括（40字内）",
  "core_duties": {{"score": 0, "evidence": "简历证据或差距（50字内）"}},
  "transferable_evidence": {{"score": 0, "evidence": "简历证据或差距（50字内）"}},
  "hard_requirements": {{"score": 0, "evidence": "满足情况（50字内）"}},
  "tools_industry": {{"score": 0, "evidence": "匹配情况（50字内）"}},
  "practical_fit": {{"score": 0, "evidence": "匹配情况（50字内）"}},
  "caps": [],
  "hard_gaps": [],
  "reason": "最关键的匹配判断（60字内）",
  "missing": "最关键缺失（40字内，没有则为空）"
}}
"""

REVIEW_PROMPT_SUFFIX = """

## 独立复核
下面是第一次评估结果。请重新核对简历证据与JD，不要迎合第一次结果；仍按上面的同一JSON结构输出各维度分数，不要输出总分。
第一次评估：{first_result}
"""

COMPONENT_LIMITS = {
    "core_duties": 40,
    "transferable_evidence": 25,
    "hard_requirements": 15,
    "tools_industry": 10,
    "practical_fit": 10,
}
# 部分模型（实测 minimaxi M3）会把 transferable_evidence 简写为 transferable，
# 校验前按别名归一，避免有效评分被误判为解析失败（issue #107）。
FIELD_ALIASES = {
    "transferable": "transferable_evidence",
}
CAP_LIMITS = {
    "technical_required": (55, "硬技术缺口封顶55"),
    "sales_acquisition_core": (65, "核心销售获客封顶65"),
    "weak_core_transfer": (70, "核心职责迁移较弱封顶70"),
}


@dataclass(frozen=True)
class ScoreResult:
    score: int
    raw_score: int
    reason: str
    components: dict[str, int]
    caps: tuple[str, ...]
    summary_reason: str
    missing: str
    structured: bool


@dataclass(frozen=True)
class ScoreOutcome:
    result: ScoreResult | None = None
    failure_detail: str = ""
    pause_reason: str = ""


def _load_resume(config: dict) -> str:
    """Load resume from configured path."""
    resume_path = Path(config.get("profile", {}).get("resume_path", "./resume.md"))
    if not resume_path.exists():
        return ""
    return resume_path.read_text(encoding="utf-8")


def _call_claude(prompt: str, config: dict, max_tokens: int | None = None) -> str | None:
    """Call Claude API and return response text."""
    if not get_ai_api_key(config):
        console.print("[red]未设置当前 AI 服务所需的 API Key 环境变量或本地凭据[/red]")
        return None
    ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
    token_limit = max_tokens if max_tokens is not None else ai_cfg.get("scoring_max_tokens", 8192)
    try:
        token_limit = max(128, min(int(token_limit or 8192), 65536))
    except (TypeError, ValueError):
        token_limit = 8192
    return run_cancellable(
        lambda: call_anthropic_text(
            prompt,
            config,
            token_limit,
            timeout=ai_cfg.get("scoring_timeout_seconds", ai_cfg.get("timeout_seconds", 180)),
            purpose="scoring",
        ),
        config,
    )


def _truncate_prompt_text(text: str, limit: int) -> str:
    """Keep both ends of long source text so compact retries retain key context."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    marker = "\n...[为适配模型上下文已裁剪]...\n"
    available = max(limit - len(marker), 2)
    head = max(int(available * 0.7), 1)
    return f"{text[:head]}{marker}{text[-(available - head):]}"


def _build_scoring_prompt(job: dict, resume: str, config: dict | None = None, *, compact: bool = False) -> str:
    config = config or {}
    profile = config.get("profile", {}) if isinstance(config.get("profile"), dict) else {}
    salary_min = _as_number(profile.get("salary_min", 0))
    salary_max = _as_number(profile.get("salary_max", 0))
    salary_ceil_ratio = max(_as_number(profile.get("salary_ceil_ratio", 1.5)), 1.0)
    salary_ceil = salary_max * salary_ceil_ratio if salary_max > 0 else 0
    resume_limit = 1400 if compact else 3000
    jd_limit = 900 if compact else 2000
    return SCORING_PROMPT.format(
        resume=_truncate_prompt_text(resume, resume_limit),
        title=job["title"],
        company=job["company"],
        salary=job["salary"],
        experience=job["experience"],
        education=job.get("education", "") or "未识别",
        recruitment_type={"campus": "校招", "experienced": "社招"}.get(
            job.get("recruitment_type", ""), "未识别"
        ),
        candidate_education=config.get("profile", {}).get("education", "") or "未填写",
        candidate_recruitment_type={
            "campus": "校招",
            "experienced": "社招",
            "both": "校招/社招均可",
        }.get(config.get("profile", {}).get("recruitment_type", ""), "未填写"),
        salary_min=_format_salary_k(salary_min),
        salary_max=_format_salary_k(salary_max),
        salary_ceil=_format_salary_k(salary_ceil),
        jd=_truncate_prompt_text(clean_job_description(job.get("jd", "")), jd_limit),
    )


def _as_number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def _format_salary_k(value: float) -> str:
    value = float(value or 0)
    return str(int(value)) if value.is_integer() else str(value)


def _build_review_prompt(job: dict, resume: str, first: ScoreResult, config: dict | None = None) -> str:
    first_result = {
        "components": first.components,
        "caps": list(first.caps),
        "reason": first.summary_reason,
        "missing": first.missing,
    }
    return _build_scoring_prompt(job, resume, config) + REVIEW_PROMPT_SUFFIX.format(
        first_result=json.dumps(first_result, ensure_ascii=False),
    )


def _notify(config: dict, message: str, *, error: bool = False) -> None:
    console.print(f"[{'red' if error else 'yellow'}]{message}[/{'red' if error else 'yellow'}]")
    callback = config.get("_workbench_log")
    if callable(callback):
        callback(message)


def _parse_score_response(text: str) -> dict | None:
    """Parse JSON response from Claude."""
    try:
        # Try to find JSON in response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except json.JSONDecodeError:
        pass
    return None


def _format_structured_reason(
    components: dict[str, int],
    caps: tuple[str, ...],
    summary_reason: str,
    missing: str,
    *,
    reviewed: bool = False,
) -> tuple[int, int, str]:
    raw_score = sum(components.values())
    cap_details = [CAP_LIMITS[cap] for cap in caps if cap in CAP_LIMITS]
    score = min([raw_score, *(limit for limit, _ in cap_details)])
    labels = (
        f"职责{components['core_duties']}/40 · "
        f"证据{components['transferable_evidence']}/25 · "
        f"硬要求{components['hard_requirements']}/15 · "
        f"工具行业{components['tools_industry']}/10 · "
        f"实际条件{components['practical_fit']}/10"
    )
    parts = [f"二次复核后：{labels}" if reviewed else labels]
    if cap_details:
        parts.append("、".join(detail for _, detail in cap_details))
    if summary_reason:
        parts.append(summary_reason)
    reason = "；".join(parts)
    if missing:
        reason = f"{reason} | 缺失: {missing}"
    return score, raw_score, reason


def _structured_score_result(result: dict, *, reviewed: bool = False) -> ScoreResult | None:
    components: dict[str, int] = {}
    for key, limit in COMPONENT_LIMITS.items():
        value = result.get(key)
        if not isinstance(value, dict) or "score" not in value:
            return None
        raw_value = value["score"]
        if isinstance(raw_value, bool):
            return None
        try:
            score = int(raw_value)
        except (TypeError, ValueError):
            return None
        if score != raw_value or not 0 <= score <= limit:
            return None
        components[key] = score

    raw_caps = result.get("caps", [])
    if not isinstance(raw_caps, list):
        return None
    caps = tuple(dict.fromkeys(str(cap) for cap in raw_caps if str(cap) in CAP_LIMITS))
    summary_reason = str(result.get("reason") or "").strip()
    if not summary_reason:
        return None
    missing = str(result.get("missing") or "").strip()
    score, raw_score, reason = _format_structured_reason(
        components,
        caps,
        summary_reason,
        missing,
        reviewed=reviewed,
    )
    return ScoreResult(
        score=score,
        raw_score=raw_score,
        reason=reason,
        components=components,
        caps=caps,
        summary_reason=summary_reason,
        missing=missing,
        structured=True,
    )


def _apply_field_aliases(result: dict) -> dict:
    """Normalize common model-side field shortenings (e.g. minimaxi M3's `transferable`)."""
    for alias, canonical in FIELD_ALIASES.items():
        if alias in result and canonical not in result:
            result[canonical] = result[alias]
    return result


def _validated_score_result(text: str) -> ScoreResult | None:
    """Accept only complete structured evidence scores."""
    result = _parse_score_response(text)
    if not isinstance(result, dict):
        return None
    _apply_field_aliases(result)
    if all(key in result for key in COMPONENT_LIMITS):
        return _structured_score_result(result)
    return None


def _score_validation_failure_reason(text: str | None) -> str:
    """Explain why a scoring response failed validation, for failure records."""
    if not text or not str(text).strip():
        return "AI 未返回评分内容"
    result = _parse_score_response(text)
    if not isinstance(result, dict):
        return "AI 返回内容无法解析为 JSON"
    _apply_field_aliases(result)
    missing = [key for key in COMPONENT_LIMITS if key not in result]
    if missing:
        return "AI 评分 JSON 缺少字段: " + ", ".join(missing)
    return "AI 评分 JSON 字段值无效（分数或理由不符合格式要求）"


def _merge_review_results(first: ScoreResult, review: ScoreResult) -> ScoreResult:
    """Average two independent structured assessments and keep the stricter cap."""
    components = {
        key: (first.components[key] + review.components[key]) // 2
        for key in COMPONENT_LIMITS
    }
    target_raw_score = (first.raw_score + review.raw_score + 1) // 2
    remainder = target_raw_score - sum(components.values())
    for key in COMPONENT_LIMITS:
        if remainder <= 0:
            break
        if (first.components[key] + review.components[key]) % 2:
            components[key] += 1
            remainder -= 1
    caps = tuple(dict.fromkeys((*first.caps, *review.caps)))
    missing = review.missing or first.missing
    score, raw_score, reason = _format_structured_reason(
        components,
        caps,
        review.summary_reason,
        missing,
        reviewed=True,
    )
    return ScoreResult(
        score=score,
        raw_score=raw_score,
        reason=reason,
        components=components,
        caps=caps,
        summary_reason=review.summary_reason,
        missing=missing,
        structured=True,
    )


def _report_progress(
    config: dict,
    completed: int,
    total: int,
    scored: int,
    filtered: int,
    failed: int,
) -> None:
    callback = config.get("_workbench_score_progress")
    if callable(callback):
        callback({
            "completed": completed,
            "total": total,
            "scored": scored,
            "filtered": filtered,
            "failed": failed,
        })


def _report_checkpoint(
    config: dict,
    remaining_job_ids: list[str],
    *,
    status: str,
    pause_reason: str = "",
    error: str | None = None,
) -> None:
    callback = config.get("_workbench_score_checkpoint")
    if callable(callback):
        callback({
            "remaining_job_ids": list(remaining_job_ids),
            "status": status,
            "pause_reason": pause_reason,
            # 只有 AI 失败导致的暂停才带 error；用户手动暂停不应记为错误（issue #100）。
            "error": error or "",
        })


def _record_score_failure(db, job: dict, detail: str) -> None:
    """Keep a failed job pending while exposing a safe, retryable failure reason."""
    safe_detail = str(detail or "AI 未返回完整评分").strip()[:240]
    update_job_score(db, job["id"], 0, f"AI评分失败: {safe_detail}")
    add_history(db, job["id"], "score_failed", safe_detail)


def _request_score(
    job: dict,
    resume: str,
    config: dict,
    max_attempts: int,
) -> ScoreOutcome:
    """Request and validate one primary assessment without touching the database."""
    ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
    response: str | None = None
    try:
        response = _call_claude(_build_scoring_prompt(job, resume, config), config)
    except AIRequestError as exc:
        if exc.kind == "output_truncated":
            _notify(config, f"{job['company']}｜{job['title']} 的评分回答被截断，正在增大输出 Token 上限后重试。")
            try:
                configured_tokens = int(ai_cfg.get("scoring_max_tokens", 8192) or 8192)
            except (TypeError, ValueError):
                configured_tokens = 8192
            retry_tokens = min(max(configured_tokens * 2, 512), 65536)
            try:
                response = _call_claude(_build_scoring_prompt(job, resume, config), config, retry_tokens)
            except AIRequestError as retry_exc:
                if retry_exc.kind == "empty_response":
                    # 空响应用保持"空结果"语义：落入下方按配置重试，仍空则岗位级失败（#101 回归）。
                    response = None
                elif retry_exc.kind in {"output_truncated", "output_limit", "context_limit"}:
                    return ScoreOutcome(failure_detail="调整输出 Token 后仍未获得完整评分")
                else:
                    # 带上"因截断进入重试"的上下文，否则只看得到重试时的错误（issue #101）。
                    return ScoreOutcome(pause_reason=f"增大输出 Token 重试后失败：{retry_exc}")
        elif exc.kind == "output_limit":
            _notify(config, f"{job['company']}｜{job['title']} 正在降低输出 Token 上限后重试评分。")
            try:
                response = _call_claude(_build_scoring_prompt(job, resume, config), config, 128)
            except AIRequestError as retry_exc:
                if retry_exc.kind == "empty_response":
                    response = None
                elif retry_exc.kind == "output_limit":
                    return ScoreOutcome(failure_detail="当前模型不接受调整后的输出 Token 设置")
                else:
                    return ScoreOutcome(pause_reason=f"降低输出 Token 重试后失败：{retry_exc}")
        elif exc.kind == "context_limit":
            _notify(config, f"{job['company']}｜{job['title']} 内容较长，正在压缩后重试评分。")
            try:
                response = _call_claude(_build_scoring_prompt(job, resume, config, compact=True), config, 128)
            except AIRequestError as retry_exc:
                if retry_exc.kind == "empty_response":
                    response = None
                elif retry_exc.kind == "context_limit":
                    return ScoreOutcome(failure_detail="压缩请求后仍超过模型上下文限制")
                else:
                    return ScoreOutcome(pause_reason=f"压缩请求重试后失败：{retry_exc}")
        elif exc.kind == "empty_response":
            # 空响应用保持"空结果"语义：按 max_attempts 走下方重试，仍为空则只记当前岗位失败，
            # 不中断整批（#101 回归：整批暂停仅留给鉴权/额度/限流/网络等服务级故障）。
            _notify(config, f"{job['company']}｜{job['title']} 的 AI 回答没有文本内容，正在重试。")
            response = None
        else:
            # str(exc) 现在带 kind/status_code，UI 才能区分限流/鉴权/额度等失败原因（issue #101）。
            return ScoreOutcome(pause_reason=str(exc))

    result = _validated_score_result(response) if response else None
    for attempt in range(2, max_attempts + 1):
        if result is not None:
            break
        _notify(
            config,
            f"{job['company']}｜{job['title']} 未返回完整评分，正在重试（{attempt}/{max_attempts}）。",
        )
        try:
            response = _call_claude(_build_scoring_prompt(job, resume, config), config)
        except AIRequestError as retry_exc:
            if retry_exc.kind in {"token_quota", "rate_limit", "auth", "network", "request_failed"}:
                return ScoreOutcome(pause_reason=str(retry_exc))
            response = None
        result = _validated_score_result(response) if response else None

    if result is None:
        return ScoreOutcome(failure_detail=_score_validation_failure_reason(response))
    return ScoreOutcome(result=result)


def _score_job_with_ai(
    job: dict,
    resume: str,
    config: dict,
    max_attempts: int,
) -> ScoreOutcome:
    """Run primary scoring and an optional independent review for borderline results."""
    outcome = _request_score(job, resume, config, max_attempts)
    first = outcome.result
    if first is None or outcome.pause_reason:
        return outcome

    ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
    review_enabled = ai_cfg.get("scoring_second_review", False) is True
    if not review_enabled or not first.structured or not 68 <= first.score <= 79:
        return outcome

    try:
        response = _call_claude(_build_review_prompt(job, resume, first, config), config)
    except AIRequestError as exc:
        if exc.kind in {"token_quota", "rate_limit", "auth", "network", "request_failed"}:
            return ScoreOutcome(result=first, pause_reason=str(exc))
        _notify(config, f"{job['company']}｜{job['title']} 二次复核未完成，保留第一次评分。")
        return outcome

    review = _validated_score_result(response) if response else None
    if review is None or not review.structured:
        _notify(config, f"{job['company']}｜{job['title']} 二次复核格式无效，保留第一次评分。")
        return outcome
    return ScoreOutcome(result=_merge_review_results(first, review))


def score_jobs(
    config: dict,
    *,
    scope: str = "pending",
    limit: int | None = None,
    job_ids: list[str] | None = None,
    force_rescore: bool = False,
    rescore_filtered: bool = False,
) -> tuple[int, int]:
    """Score every unscored pending job; previously scored jobs keep their result."""
    db = get_db()
    try:
        resume = _load_resume(config)
        if not resume:
            console.print("[red]无法读取简历文件[/red]")
            return 0, 0

        if rescore_filtered:
            reset_count = reset_ai_filtered_jobs(db)
            _notify(config, f"已将 {reset_count} 个 AI 低分岗位加入重新评分队列。")

        options = validate_options(scope, limit, job_ids, force_rescore)
        pending_jobs = select_scoring_jobs(db, **options)
        # Preserve the lightweight mocked database seam used by legacy tests.
        if not pending_jobs and scope == "pending" and not job_ids:
            legacy_jobs = get_jobs_by_status(db, "pending")
            pending_jobs = legacy_jobs[:limit] if limit is not None else legacy_jobs
        if not pending_jobs:
            console.print("[yellow]没有待评分的岗位[/yellow]")
            return 0, 0

        threshold = config.get("scoring", {}).get("threshold", 60)
        remaining_job_ids = [str(job["id"]) for job in pending_jobs]
        _report_checkpoint(config, remaining_job_ids, status="running")

        def mark_completed(job_id: str) -> None:
            if job_id in remaining_job_ids:
                remaining_job_ids.remove(job_id)
            _report_checkpoint(config, remaining_job_ids, status="running")

        ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
        try:
            max_attempts = max(1, min(int(ai_cfg.get("scoring_max_attempts", 2) or 2), 3))
        except (TypeError, ValueError):
            max_attempts = 2
        concurrency = get_scoring_concurrency(config)
        stop_event = config.get("_workbench_stop_event")
        scored = 0
        filtered = 0
        prefiltered = 0
        processed = 0
        failed = 0
        pause_reason = ""

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task(f"评分中 (0/{len(pending_jobs)})", total=len(pending_jobs))
            ai_jobs: list[dict] = []
            for job in pending_jobs:
                if stop_event is not None and stop_event.is_set():
                    break
                qs, qs_reason = quick_score(job, config)
                update_job_quick_score(db, job["id"], qs)
                if qs == 0:
                    update_job_score(db, job["id"], qs, f"预筛不通过: {qs_reason}")
                    update_job_status(db, job["id"], "filtered")
                    filtered += 1
                    prefiltered += 1
                    processed += 1
                    mark_completed(str(job["id"]))
                    progress.update(
                        task,
                        advance=1,
                        description=f"评分中 ({processed}/{len(pending_jobs)}) [预筛淘汰{prefiltered}]",
                    )
                    _report_progress(
                        config,
                        processed,
                        len(pending_jobs),
                        scored,
                        filtered,
                        failed,
                    )
                else:
                    ai_jobs.append(job)

            executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="bosshunter-score")
            futures: dict[Future[ScoreOutcome], dict] = {}
            job_iter = iter(ai_jobs)

            def submit_next() -> bool:
                try:
                    next_job = next(job_iter)
                except StopIteration:
                    return False
                future = executor.submit(_score_job_with_ai, next_job, resume, config, max_attempts)
                futures[future] = next_job
                return True

            for _ in range(min(concurrency, len(ai_jobs))):
                submit_next()

            interrupted = False
            while futures:
                if stop_event is not None and stop_event.is_set():
                    interrupted = True
                    break
                done, _ = wait(futures, timeout=0.1, return_when=FIRST_COMPLETED)
                if not done:
                    continue
                for future in done:
                    job = futures.pop(future)
                    try:
                        outcome = future.result()
                    except OperationCancelled:
                        interrupted = True
                        break
                    except Exception as exc:
                        outcome = ScoreOutcome(failure_detail=f"评分任务异常: {type(exc).__name__}")

                    result = outcome.result
                    completed_job = False
                    if result is not None:
                        update_job_score(db, job["id"], result.score, result.reason)
                        if result.score >= threshold:
                            update_job_status(db, job["id"], "ready")
                            scored += 1
                        else:
                            update_job_status(db, job["id"], "filtered")
                            filtered += 1
                        completed_job = True
                    elif outcome.failure_detail:
                        failed += 1
                        _record_score_failure(db, job, outcome.failure_detail)
                        _notify(config, f"已跳过 {job['company']}｜{job['title']}：{outcome.failure_detail}。")
                        completed_job = True

                    if completed_job:
                        processed += 1
                        mark_completed(str(job["id"]))
                        progress.update(
                            task,
                            advance=1,
                            description=f"评分中 ({processed}/{len(pending_jobs)}) [预筛淘汰{prefiltered}]",
                        )
                        _report_progress(config, processed, len(pending_jobs), scored, filtered, failed)

                    if outcome.pause_reason:
                        pause_reason = outcome.pause_reason
                        interrupted = True
                        break
                    submit_next()
                if interrupted:
                    break

            if interrupted:
                for future in futures:
                    future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
            else:
                executor.shutdown(wait=True)

        if prefiltered > 0:
            console.print(f"[dim]  预筛阶段淘汰 {prefiltered} 个岗位（节省 {prefiltered} 次 API 调用）[/dim]")
        if pause_reason:
            _notify(
                config,
                f"AI 评分已安全暂停：{pause_reason}。已完成结果已保存，剩余 {len(remaining_job_ids)} 个岗位下次运行会继续处理。",
                error=True,
            )
        if failed:
            _notify(config, f"本轮有 {failed} 个岗位评分失败并保留为待处理，可稍后重试。")
        if remaining_job_ids:
            _report_checkpoint(
                config,
                remaining_job_ids,
                status="paused",
                pause_reason=pause_reason or "用户暂停或任务中断",
                # pause_reason 非空即 AI 失败暂停（用户停止走 stop_event，reason 为空）。
                error=pause_reason or None,
            )
        else:
            _report_checkpoint(
                config,
                [],
                status="completed_with_errors" if failed else "completed",
            )
        return scored, filtered
    finally:
        db.close()
