"""AI scorer with explicit, retryable job selection and safe task outcomes."""

import json
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from bosshunter.ai.credentials import AIRequestError, call_anthropic_text, get_ai_api_key
from bosshunter.ai.prefilter import quick_score
from bosshunter.candidate_context import build_candidate_context
from bosshunter.cancellation import run_cancellable
from bosshunter.db import (
	add_history,
	clear_job_filter,
	clear_job_score_failure,
	get_db,
	get_jobs_by_status,
	mark_job_filtered,
	reset_ai_filtered_jobs,
	update_job_quick_score,
	update_job_score,
	update_job_score_failure,
	update_job_status,
)
from bosshunter.scoring_selection import serialize_score_failure, select_scoring_jobs, validate_options


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
	return build_candidate_context(config, purpose="scoring")


def _call_claude(prompt: str, config: dict, max_tokens: int | None = None) -> str | None:
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
	text = str(text or "")
	if len(text) <= limit:
		return text
	marker = "\n...[为适配模型上下文已裁剪]...\n"
	available = max(limit - len(marker), 2)
	head = max(int(available * 0.7), 1)
	return f"{text[:head]}{marker}{text[-(available - head):]}"


def _build_scoring_prompt(job: dict, resume: str, *, compact: bool = False) -> str:
	return SCORING_PROMPT.format(
		resume=_truncate_prompt_text(resume, 1400 if compact else 3000),
		title=job.get("title", ""),
		company=job.get("company", ""),
		salary=job.get("salary", ""),
		experience=job.get("experience", ""),
		jd=_truncate_prompt_text(job.get("jd", ""), 900 if compact else 2000),
	)


def _notify(config: dict, message: str, *, error: bool = False) -> None:
	console.print(f"[{'red' if error else 'yellow'}]{message}[/{'red' if error else 'yellow'}]")
	callback = config.get("_workbench_log")
	if callable(callback):
		callback(message)


def _parse_score_response(text: str) -> dict | None:
	try:
		start = text.find("{")
		end = text.rfind("}") + 1
		if start >= 0 and end > start:
			return json.loads(text[start:end])
	except (TypeError, json.JSONDecodeError):
		pass
	return None


def _validated_score_result(text: str | None) -> tuple[int, str] | None:
	result = _parse_score_response(text or "")
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


def _report_progress(config: dict, completed: int, total: int, scored: int, filtered: int, failed: int) -> None:
	callback = config.get("_workbench_score_progress")
	if callable(callback):
		callback({"completed": completed, "total": total, "scored": scored, "filtered": filtered, "failed": failed})


@dataclass
class ScoreResult:
	"""Structured score outcome with legacy ``(passed, filtered)`` unpacking."""

	selected: int = 0
	completed: int = 0
	passed: int = 0
	filtered: int = 0
	failed: int = 0
	skipped: int = 0
	remaining: int = 0
	outcome: str = "completed"
	pause_reason: str = ""

	def __iter__(self):
		yield self.passed
		yield self.filtered

	def __getitem__(self, key: str):
		return self.to_dict()[key]

	def to_dict(self) -> dict:
		return {
			"selected": self.selected,
			"completed": self.completed,
			"passed": self.passed,
			"filtered": self.filtered,
			"failed": self.failed,
			"skipped": self.skipped,
			"remaining": self.remaining,
			"outcome": self.outcome,
			"pause_reason": self.pause_reason or None,
		}


def _record_score_failure(
	db,
	job: dict,
	detail: str,
	*,
	kind: str = "invalid_response",
	stage: str = "parse_score",
	status_code: int | None = None,
) -> None:
	safe_detail = str(detail or "AI 未返回完整评分").strip()[:240]
	failure = serialize_score_failure(kind, stage, safe_detail, status_code=status_code)
	update_job_score(db, job["id"], 0, f"AI评分失败: {safe_detail}")
	update_job_status(db, job["id"], "pending")
	update_job_score_failure(db, job["id"], failure)
	add_history(db, job["id"], "score_failed", failure)


def _score_one_job(
	job: dict,
	resume: str,
	config: dict,
	max_attempts: int,
) -> tuple[str, tuple[int, str] | None, AIRequestError | None]:
	ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
	last_error: AIRequestError | None = None
	compact = False
	token_limit: int | None = None
	for attempt in range(1, max_attempts + 1):
		try:
			response = _call_claude(_build_scoring_prompt(job, resume, compact=compact), config, token_limit)
			result = _validated_score_result(response)
			if result is not None:
				return "ok", result, None
			last_error = AIRequestError("invalid_response", "AI 未返回完整、可解析的评分 JSON")
		except AIRequestError as exc:
			if exc.kind in {"auth", "token_quota", "rate_limit", "model_not_found"}:
				return "pause", None, exc
			last_error = exc
			if exc.kind == "context_limit" and not compact:
				compact = True
				token_limit = 128
				_notify(config, f"{job['company']}｜{job['title']} 内容较长，正在压缩后重试评分。")
				continue
			if exc.kind == "output_truncated":
				try:
					configured_tokens = int(ai_cfg.get("scoring_max_tokens", 8192) or 8192)
				except (TypeError, ValueError):
					configured_tokens = 8192
				token_limit = min(max(configured_tokens * 2, 512), 65536)
				_notify(config, f"{job['company']}｜{job['title']} 的评分回答被截断，正在增大输出 Token 上限后重试。")
				continue
			if exc.kind == "output_limit":
				token_limit = 128
				_notify(config, f"{job['company']}｜{job['title']} 正在降低输出 Token 上限后重试评分。")
				continue
		if attempt < max_attempts:
			_notify(config, f"{job['company']}｜{job['title']} 未返回完整评分，正在重试（{attempt + 1}/{max_attempts}）。")
	return "failed", None, last_error


def score_jobs(
	config: dict,
	*,
	scope: str = "pending",
	limit: int | None = 20,
	job_ids: list[str] | None = None,
	force_rescore: bool = False,
	rescore_filtered: bool = False,
) -> ScoreResult:
	"""Score selected existing jobs; no scraping is performed here."""
	db = get_db()
	result = ScoreResult()
	try:
		resume = _load_resume(config)
		if not resume:
			console.print("[red]无法读取简历文件[/red]")
			result.outcome = "failed"
			return result
		if rescore_filtered:
			reset_count = reset_ai_filtered_jobs(db)
			_notify(config, f"已将 {reset_count} 个 AI 低分岗位加入重新评分队列。")
		options = validate_options(scope, limit, job_ids, force_rescore)
		selected_jobs = select_scoring_jobs(db, **options)
		# Preserve the old unit/CLI seam while real connections use the explicit
		# selection query above.
		if not selected_jobs and scope == "pending" and not job_ids:
			legacy_jobs = get_jobs_by_status(db, "pending")
			selected_jobs = legacy_jobs[:limit] if limit is not None else legacy_jobs
		result.selected = len(selected_jobs)
		if not selected_jobs:
			console.print("[yellow]没有待评分的岗位[/yellow]")
			return result

		ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
		try:
			max_attempts = max(1, min(int(ai_cfg.get("scoring_max_attempts", 2) or 2), 3))
		except (TypeError, ValueError):
			max_attempts = 2
		threshold = int(config.get("scoring", {}).get("threshold", 60) or 60)
		stop_event = config.get("_workbench_stop_event")
		processed = 0
		prefiltered = 0
		transient_streak: tuple[str, int] | None = None

		with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
			task = progress.add_task(f"评分中 (0/{len(selected_jobs)})", total=len(selected_jobs))
			for job in selected_jobs:
				if stop_event is not None and stop_event.is_set():
					break
				try:
					qs, qs_reason = quick_score(job, config)
					update_job_quick_score(db, job["id"], qs)
					if qs == 0:
						update_job_score(db, job["id"], qs, f"预筛不通过: {qs_reason}")
						mark_job_filtered(db, job["id"], "prefilter", qs_reason)
						result.filtered += 1
						result.completed += 1
						prefiltered += 1
						continue

					outcome, score_result, error = _score_one_job(job, resume, config, max_attempts)
					if outcome == "pause" and error:
						result.outcome = "paused"
						result.pause_reason = error.user_message
						break
					if outcome == "failed":
						result.failed += 1
						kind = error.kind if error else "invalid_response"
						detail = error.user_message if error else "AI 未返回完整评分"
						_record_score_failure(db, job, detail, kind=kind, status_code=error.status_code if error else None)
						_notify(config, f"已跳过 {job['company']}｜{job['title']}：{detail}。")
						if error and error.kind in {"network", "timeout"}:
							previous_kind, count = transient_streak or (error.kind, 0)
							transient_streak = (error.kind, count + 1) if previous_kind == error.kind else (error.kind, 1)
							if transient_streak[1] >= 3:
								result.outcome = "paused"
								result.pause_reason = "连续 3 个岗位出现网络或超时错误"
								break
						else:
							transient_streak = None
						continue

					transient_streak = None
					score, reason = score_result or (0, "AI 未返回完整评分")
					update_job_score(db, job["id"], score, reason)
					clear_job_score_failure(db, job["id"])
					if score >= threshold:
						update_job_status(db, job["id"], "ready")
						clear_job_filter(db, job["id"])
						result.passed += 1
					else:
						mark_job_filtered(db, job["id"], "ai_score", reason)
						result.filtered += 1
					result.completed += 1
				finally:
					processed += 1
					progress.update(task, advance=1, description=f"评分中 ({processed}/{len(selected_jobs)}) [预筛淘汰{prefiltered}]")
					_report_progress(config, processed, len(selected_jobs), result.passed, result.filtered, result.failed)

		if result.outcome == "paused":
			result.remaining = max(len(selected_jobs) - processed, 0)
			_notify(config, f"AI 评分已安全暂停：{result.pause_reason}。已完成结果已保存，剩余 {result.remaining} 个岗位下次运行会继续处理。", error=True)
		elif result.failed:
			result.outcome = "completed_with_errors"
			_notify(config, f"本轮有 {result.failed} 个岗位评分失败并保留为待处理，可稍后重试。")
		elif stop_event is not None and stop_event.is_set():
			result.outcome = "stopped"
			result.remaining = max(len(selected_jobs) - processed, 0)
		result.remaining = max(result.remaining, len(selected_jobs) - result.completed - result.failed)
		return result
	finally:
		db.close()
