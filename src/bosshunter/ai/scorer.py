"""AI Scorer - Match jobs against resume using Claude API."""

import json
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from bosshunter.ai.credentials import AIRequestError, call_anthropic_text, get_ai_api_key
from bosshunter.cancellation import run_cancellable
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

console = Console()

SCORING_PROMPT = """你是一位专业的求职顾问。请根据以下简历和岗位JD，评估候选人与该岗位的匹配度。

## 候选人简历
{resume}

## 岗位信息
- 职位：{title}
- 公司：{company}
- 薪资：{salary}
- 要求：{experience}
- JD：{jd}

## 评估要求
请从以下维度评估匹配度，给出0-100的综合评分：
1. 职能技能匹配度（最重要）：候选人的核心职能技能是否覆盖岗位要求，这是评分的最主要依据
2. 工作年限匹配度：工作年限是否符合要求
3. 薪资合理性：期望薪资与岗位薪资是否匹配
4. 行业背景相关性（加分项，非必须）：有相关行业经验可以加分，但行业不同不应大幅扣分——职能能力可以跨行业迁移

**重要原则**：行业背景属于加分项而非硬性门槛。如果JD中某项行业要求标注为"优先"、"加分"或"更佳"而非"必须"，请不要将其作为扣分依据。候选人的职能技能和过往工作中接触到的行业数据/技术背景（如卫星遥感、GIS、科技行业推广经验）应被视为相关经验。

**平台内容证据识别**：如果简历中已经出现小红书/抖音/短视频/新媒体相关数据、爆款内容、单篇阅读/观看、点赞收藏、账号从0到1起号、平台运营或用户群运营，请把这些视为平台内容运营与增长证据，不要在missing中写"未提及抖音/小红书平台案例"或类似缺失。候选人刚开始运营的新账号，已有单篇阅读/观看数据，也应视为早期起号验证，而不是完全缺乏平台经验。

请严格按以下JSON格式输出，不要输出其他内容：
{{"score": 75, "reason": "匹配理由简述（50字内）", "missing": "缺失的关键技能或经验（30字内）"}}
"""


def _load_resume(config: dict) -> str:
    """Load resume from configured path."""
    resume_path = Path(config.get("profile", {}).get("resume_path", "./resume.md"))
    if not resume_path.exists():
        return ""
    return resume_path.read_text(encoding="utf-8")


def _call_claude(prompt: str, config: dict, max_tokens: int | None = None) -> str | None:
    """Call Claude API and return response text."""
    if not get_ai_api_key(config):
        console.print("[red]未设置当前 AI 服务所需的 API Key 环境变量或 config.yaml ai.api_key[/red]")
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


def _build_scoring_prompt(job: dict, resume: str, *, compact: bool = False) -> str:
    resume_limit = 1400 if compact else 3000
    jd_limit = 900 if compact else 2000
    return SCORING_PROMPT.format(
        resume=_truncate_prompt_text(resume, resume_limit),
        title=job["title"],
        company=job["company"],
        salary=job["salary"],
        experience=job["experience"],
        jd=_truncate_prompt_text(job.get("jd", ""), jd_limit),
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


def _validated_score_result(text: str) -> tuple[int, str] | None:
    """Accept only complete scoring JSON with a bounded numeric score."""
    result = _parse_score_response(text)
    if not isinstance(result, dict) or "score" not in result:
        return None
    try:
        score = int(result["score"])
    except (TypeError, ValueError):
        return None
    if not 0 <= score <= 100:
        return None
    reason = str(result.get("reason") or "").strip()
    if not reason:
        return None
    missing = str(result.get("missing") or "").strip()
    return score, f"{reason} | 缺失: {missing}" if missing else reason


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


def _record_score_failure(db, job: dict, detail: str) -> None:
    """Keep a failed job pending while exposing a safe, retryable failure reason."""
    safe_detail = str(detail or "AI 未返回完整评分").strip()[:240]
    update_job_score(db, job["id"], 0, f"AI评分失败: {safe_detail}")
    add_history(db, job["id"], "score_failed", safe_detail)


def score_jobs(config: dict, *, rescore_filtered: bool = False) -> tuple[int, int]:
    """Score all pending jobs. Returns (scored_count, filtered_count)."""
    db = get_db()
    try:
        resume = _load_resume(config)
        if not resume:
            console.print("[red]无法读取简历文件[/red]")
            return 0, 0

        if rescore_filtered:
            reset_count = reset_ai_filtered_jobs(db)
            _notify(config, f"已将 {reset_count} 个 AI 低分岗位加入重新评分队列。")

        threshold = config.get("scoring", {}).get("threshold", 60)
        pending_jobs = get_jobs_by_status(db, "pending")
        if not pending_jobs:
            console.print("[yellow]没有待评分的岗位[/yellow]")
            return 0, 0

        ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
        try:
            max_attempts = max(1, min(int(ai_cfg.get("scoring_max_attempts", 2) or 2), 3))
        except (TypeError, ValueError):
            max_attempts = 2
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

            for job in pending_jobs:
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    # Stage 1: Keyword pre-filter (free, no API calls)
                    qs, qs_reason = quick_score(job, config)
                    update_job_quick_score(db, job["id"], qs)

                    if qs == 0:
                        update_job_score(db, job["id"], qs, f"预筛不通过: {qs_reason}")
                        update_job_status(db, job["id"], "filtered")
                        filtered += 1
                        prefiltered += 1
                        continue

                    # Stage 2: LLM deep evaluation
                    try:
                        response = _call_claude(_build_scoring_prompt(job, resume), config)
                    except AIRequestError as exc:
                        if exc.kind == "output_truncated":
                            _notify(config, f"{job['company']}｜{job['title']} 的评分回答被截断，正在增大输出 Token 上限后重试。")
                            try:
                                configured_tokens = int(ai_cfg.get("scoring_max_tokens", 8192) or 8192)
                            except (TypeError, ValueError):
                                configured_tokens = 8192
                            retry_tokens = min(
                                max(configured_tokens * 2, 512),
                                65536,
                            )
                            try:
                                response = _call_claude(_build_scoring_prompt(job, resume), config, retry_tokens)
                            except AIRequestError as retry_exc:
                                if retry_exc.kind in {"output_truncated", "output_limit", "context_limit"}:
                                    failed += 1
                                    _record_score_failure(db, job, "调整输出 Token 后仍未获得完整评分")
                                    _notify(
                                        config,
                                        f"已跳过 {job['company']}｜{job['title']}：调整单次 Token 请求后仍无法获得完整评分。",
                                    )
                                    continue
                                pause_reason = retry_exc.user_message
                                break
                        elif exc.kind == "output_limit":
                            _notify(config, f"{job['company']}｜{job['title']} 正在降低输出 Token 上限后重试评分。")
                            try:
                                response = _call_claude(_build_scoring_prompt(job, resume), config, 128)
                            except AIRequestError as retry_exc:
                                if retry_exc.kind == "output_limit":
                                    failed += 1
                                    _record_score_failure(db, job, "当前模型不接受调整后的输出 Token 设置")
                                    _notify(
                                        config,
                                        f"已跳过 {job['company']}｜{job['title']}：当前模型仍不接受输出 Token 设置。",
                                    )
                                    continue
                                pause_reason = retry_exc.user_message
                                break
                        elif exc.kind != "context_limit":
                            pause_reason = exc.user_message
                            break
                        else:
                            _notify(config, f"{job['company']}｜{job['title']} 内容较长，正在压缩后重试评分。")
                            try:
                                response = _call_claude(_build_scoring_prompt(job, resume, compact=True), config, 128)
                            except AIRequestError as retry_exc:
                                if retry_exc.kind != "context_limit":
                                    pause_reason = retry_exc.user_message
                                    break
                                failed += 1
                                _record_score_failure(db, job, "压缩请求后仍超过模型上下文限制")
                                _notify(config, f"已跳过 {job['company']}｜{job['title']}：压缩后仍超过模型上下文限制。")
                                continue

                    if not response:
                        result = None
                    else:
                        result = _validated_score_result(response)

                    for attempt in range(2, max_attempts + 1):
                        if result is not None:
                            break
                        if stop_event is not None and stop_event.is_set():
                            break
                        _notify(
                            config,
                            f"{job['company']}｜{job['title']} 未返回完整评分，正在重试（{attempt}/{max_attempts}）。",
                        )
                        try:
                            response = _call_claude(_build_scoring_prompt(job, resume), config)
                        except AIRequestError as retry_exc:
                            if retry_exc.kind in {"token_quota", "rate_limit", "auth", "network", "request_failed"}:
                                pause_reason = retry_exc.user_message
                                break
                            response = None
                        result = _validated_score_result(response) if response else None

                    if pause_reason:
                        break
                    if result is None:
                        failed += 1
                        _record_score_failure(db, job, "AI 未返回完整、可解析的评分 JSON")
                        _notify(config, f"已跳过 {job['company']}｜{job['title']}：AI 返回的评分格式无法解析。")
                        continue

                    score, full_reason = result
                    update_job_score(db, job["id"], score, full_reason)

                    if score >= threshold:
                        update_job_status(db, job["id"], "ready")
                        scored += 1
                    else:
                        update_job_status(db, job["id"], "filtered")
                        filtered += 1
                finally:
                    processed += 1
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

        if prefiltered > 0:
            console.print(f"[dim]  预筛阶段淘汰 {prefiltered} 个岗位（节省 {prefiltered} 次 API 调用）[/dim]")
        if pause_reason:
            remaining = max(len(pending_jobs) - scored - filtered, 0)
            _notify(
                config,
                f"AI 评分已安全暂停：{pause_reason}。已完成结果已保存，剩余 {remaining} 个岗位下次运行会继续处理。",
                error=True,
            )
        if failed:
            _notify(config, f"本轮有 {failed} 个岗位评分失败并保留为待处理，可稍后重试。")
        return scored, filtered
    finally:
        db.close()
