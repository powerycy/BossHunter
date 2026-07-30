"""AI Scorer - Match jobs against resume using Claude API."""

import json
import os
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from bosshunter.ai.credentials import call_anthropic_text, get_anthropic_api_key
from bosshunter.db import (
    get_db,
    get_jobs_by_status,
    reset_ai_filtered_jobs,
    update_job_quick_score,
    update_job_score,
    update_job_status,
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


def _call_claude(prompt: str, config: dict) -> str | None:
    """Call Claude API and return response text."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        raise RuntimeError("需要安装 anthropic 包: pip install anthropic")

    if not get_anthropic_api_key(config):
        raise RuntimeError("未设置 AI API Key")

    ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
    base_url = str(os.environ.get("ANTHROPIC_BASE_URL") or ai_cfg.get("base_url") or "")
    model = str(ai_cfg.get("model") or "")
    deepseek_compatible = "deepseek" in f"{base_url} {model}".lower()
    thinking_mode = str(ai_cfg.get("scoring_thinking_mode", "enabled" if deepseek_compatible else "default")).lower()
    max_tokens = max(256, min(int(ai_cfg.get("scoring_max_tokens", 8192) or 8192), 65536))
    timeout = max(5, min(float(ai_cfg.get("scoring_timeout_seconds", 180) or 180), 600))
    return call_anthropic_text(
        prompt,
        config,
        max_tokens,
        timeout=timeout,
        disable_thinking=thinking_mode == "disabled",
        enable_thinking=thinking_mode == "enabled",
    )


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
    """Return a normalized score and reason only for a complete JSON result."""
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
    missing = str(result.get("missing") or "").strip()
    if not reason:
        return None
    return score, f"{reason} | 缺失: {missing}" if missing else reason


def _short_error(error: Exception | str) -> str:
    text = " ".join(str(error).split()) or "未知错误"
    return text[:240]


def _report_progress(config: dict, completed: int, total: int, scored: int, filtered: int, failed: int) -> None:
    callback = config.get("_workbench_score_progress")
    if not callable(callback):
        return
    callback({
        "completed": completed,
        "total": total,
        "scored": scored,
        "filtered": filtered,
        "failed": failed,
    })


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
            console.print(f"[dim]已将 {reset_count} 个 AI 低分岗位加入重新评分队列[/dim]")

        threshold = config.get("scoring", {}).get("threshold", 60)
        pending_jobs = get_jobs_by_status(db, "pending")

        if not pending_jobs:
            console.print("[yellow]没有待评分的岗位[/yellow]")
            return 0, 0

        ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
        max_attempts = max(1, min(int(ai_cfg.get("scoring_max_attempts", 2) or 2), 3))
        stop_event = config.get("_workbench_stop_event")
        scored = 0
        filtered = 0
        prefiltered = 0
        failed = 0

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task(f"评分中 (0/{len(pending_jobs)})", total=len(pending_jobs))

            for job in pending_jobs:
                if stop_event is not None and stop_event.is_set():
                    break
                # Stage 1: Keyword pre-filter (free, no API calls)
                qs, qs_reason = quick_score(job, config)
                update_job_quick_score(db, job["id"], qs)

                if qs == 0:
                    update_job_score(db, job["id"], qs, f"预筛不通过: {qs_reason}")
                    update_job_status(db, job["id"], "filtered")
                    filtered += 1
                    prefiltered += 1
                    completed = scored + filtered + failed
                    progress.update(task, advance=1, description=f"评分中 ({completed}/{len(pending_jobs)}) [失败{failed}]")
                    _report_progress(config, completed, len(pending_jobs), scored, filtered, failed)
                    continue

            # Stage 2: LLM deep evaluation
                prompt = SCORING_PROMPT.format(
                    resume=resume[:3000],
                    title=job["title"],
                    company=job["company"],
                    salary=job["salary"],
                    experience=job["experience"],
                    jd=job["jd"][:2000],
                )

                normalized = None
                last_error = "AI 未返回内容"
                for attempt in range(1, max_attempts + 1):
                    if stop_event is not None and stop_event.is_set():
                        break
                    try:
                        response = _call_claude(prompt, config)
                        if not response:
                            last_error = "AI 未返回内容"
                            continue
                        normalized = _validated_score_result(response)
                        if normalized:
                            break
                        last_error = "AI 返回内容不完整或不是有效评分 JSON"
                    except Exception as exc:
                        last_error = _short_error(exc)
                        console.print(f"[yellow]评分调用失败（{attempt}/{max_attempts}）: {last_error}[/yellow]")

                if normalized is None:
                    if stop_event is not None and stop_event.is_set():
                        break
                    update_job_score(db, job["id"], 0, f"AI评分失败: {last_error}")
                    failed += 1
                    completed = scored + filtered + failed
                    progress.update(task, advance=1, description=f"评分中 ({completed}/{len(pending_jobs)}) [失败{failed}]")
                    _report_progress(config, completed, len(pending_jobs), scored, filtered, failed)
                    continue

                score, full_reason = normalized
                update_job_score(db, job["id"], score, full_reason)

                if score >= threshold:
                    update_job_status(db, job["id"], "ready")
                    scored += 1
                else:
                    update_job_status(db, job["id"], "filtered")
                    filtered += 1

                completed = scored + filtered + failed
                progress.update(task, advance=1, description=f"评分中 ({completed}/{len(pending_jobs)}) [失败{failed}]")
                _report_progress(config, completed, len(pending_jobs), scored, filtered, failed)

        if prefiltered > 0:
            console.print(f"[dim]  预筛阶段淘汰 {prefiltered} 个岗位（节省 {prefiltered} 次 API 调用）[/dim]")
        if failed > 0:
            console.print(f"[yellow]  {failed} 个岗位评分失败并保留在待评分队列，可直接重试[/yellow]")
        return scored, filtered
    finally:
        db.close()
