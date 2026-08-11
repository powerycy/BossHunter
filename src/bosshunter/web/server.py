"""BossHunter Web Server - Bottle HTTP service + API routes.

Serves:
- /api/* → JSON data endpoints
- /* → Frontend static files (dist/)
"""

import json
import mimetypes
import time
from copy import deepcopy
from pathlib import Path
from threading import Event, Lock

from bottle import Bottle, request, response, static_file, abort

from bosshunter import __version__
from bosshunter.ai.credentials import get_ai_api_key, normalize_openai_base_url
from bosshunter.ai.diagnostics import advanced_diagnose_ai, diagnose_ai
from bosshunter.cities import CityRefreshError, get_city_map, load_city_snapshot, refresh_city_cache
from bosshunter.candidate_profiles import derive_pending_facts, ensure_profile_for_resume, profile_payload
from bosshunter.config import AI_SERVICE_PRESETS, CITY_CODES, load_config
from bosshunter.db import (
	add_history,
	count_unresolved_monitor_items,
	get_daily_activity,
	get_db,
	get_funnel_stats,
	get_jobs_needing_resume,
	get_jobs_pending_confirmation,
	get_jobs_ready_to_send,
	get_jobs_greeting_generation_items,
	get_jobs_with_send_errors,
	get_job_permanent_delete_reasons,
	get_recent_history,
	get_unresolved_resume_failures,
	get_stats,
	get_top_companies,
	JobDeletionConflictError,
	JobDeletionConfirmationError,
	normalize_job_filters,
	permanent_delete_jobs,
	query_jobs,
	restore_jobs,
	soft_delete_jobs,
	get_candidate_profile,
	list_candidate_profiles,
	list_candidate_documents,
	list_candidate_facts,
	set_candidate_document_primary,
	update_candidate_profile,
	update_candidate_fact,
	override_filtered_job,
	mark_job_filtered,
	update_job_status,
)
from bosshunter.job_export import InvalidJobSelectionError, export_jobs, export_row_count
from bosshunter.scoring_selection import preview_scoring, validate_options
from bosshunter.web.preflight import check_ai_connection, collect_preflight_checks, error_messages
from bosshunter.web.resume_upload import ResumeUploadError, prepare_resume_content
from bosshunter.web.tasks import TaskAlreadyRunningError, WorkbenchTask, WorkbenchTaskRunner

mimetypes.add_type("application/javascript", ".js", strict=True)
mimetypes.add_type("application/javascript", ".mjs", strict=True)
mimetypes.add_type("text/javascript", ".cjs", strict=True)
mimetypes.add_type("text/css", ".css", strict=True)

app = Bottle()
task_runner = WorkbenchTaskRunner()

# Paths
FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"
SCHEMA_PATH = Path(__file__).parent / "config_schema.json"


def _default_base_dir() -> Path:
	"""Resolve the runtime project directory even when launched outside repo root."""
	source_root = Path(__file__).resolve().parents[3]
	if (source_root / "config.yaml").exists():
		return source_root

	cwd = Path.cwd()
	if (cwd / "config.yaml").exists():
		return cwd

	return cwd


BASE_DIR = _default_base_dir()
DATA_DIR = BASE_DIR / "data"
RESUME_DIR = DATA_DIR / "resumes"
CONFIG_PATH = BASE_DIR / "config.yaml"
task_runner.set_store_path(DATA_DIR / "bosshunter.db")


def set_base_dir(base_dir: Path | str) -> None:
	"""Set the runtime directory used for config.yaml, data, and uploads."""
	global BASE_DIR, DATA_DIR, RESUME_DIR, CONFIG_PATH
	BASE_DIR = Path(base_dir).resolve()
	DATA_DIR = BASE_DIR / "data"
	RESUME_DIR = DATA_DIR / "resumes"
	CONFIG_PATH = BASE_DIR / "config.yaml"
	task_runner.set_store_path(DATA_DIR / "bosshunter.db")


def _get_web_db():
	"""Open the dashboard database from the resolved runtime data directory."""
	return get_db(DATA_DIR / "bosshunter.db")


def _json_response(data, status_code=200):
	"""Return JSON response with proper headers."""
	response.content_type = "application/json; charset=utf-8"
	response.status = status_code
	return json.dumps(data, ensure_ascii=False, default=str)


def _serialize_history_items(items):
	"""Expose structured history details while retaining the legacy detail field."""
	serialized = []
	for item in items:
		record = dict(item)
		detail = record.get("detail")
		if isinstance(detail, str) and detail.lstrip().startswith("{"):
			try:
				payload = json.loads(detail)
			except (json.JSONDecodeError, TypeError):
				payload = None
			if isinstance(payload, dict):
				record["detail_payload"] = payload
		record["resolved"] = bool(record.get("resolved"))
		serialized.append(record)
	return serialized


def _mask_api_key(key):
	"""Return a display-safe API key marker."""
	if not key:
		return ""
	if len(key) > 8:
		return key[:4] + "***" + key[-4:]
	return "***"


def _redact_config_for_response(config):
	"""Hide secrets before returning config to the browser."""
	redacted = deepcopy(config)
	ai_cfg = redacted.get("ai")
	if isinstance(ai_cfg, dict):
		key = ai_cfg.pop("api_key", None)
		if key:
			ai_cfg["api_key_masked"] = _mask_api_key(str(key))
		auth_token = ai_cfg.pop("auth_token", None)
		if auth_token:
			ai_cfg["auth_token_masked"] = _mask_api_key(str(auth_token))
	return redacted


def _sanitize_config_for_write(data):
	"""Remove browser-only fields and preserve existing secrets on blank posts."""
	cleaned = deepcopy(data)
	ai_cfg = cleaned.get("ai")
	if not isinstance(ai_cfg, dict):
		return cleaned

	ai_cfg.pop("api_key_masked", None)
	ai_cfg.pop("has_api_key", None)
	ai_cfg.pop("auth_token_masked", None)
	ai_cfg.pop("has_auth_token", None)

	existing_ai = load_config(CONFIG_PATH).get("ai", {})
	service = ai_cfg.get("service") or existing_ai.get("service")
	if service not in AI_SERVICE_PRESETS:
		provider = ai_cfg.get("provider") or existing_ai.get("provider") or "anthropic"
		service = "custom" if provider == "openai_compatible" else "anthropic"
	ai_cfg["service"] = service
	ai_cfg["provider"] = AI_SERVICE_PRESETS[service]["provider"]
	if service == "lulucoding":
		raw_base_url = ai_cfg.get("base_url") or AI_SERVICE_PRESETS[service]["base_url"]
		try:
			ai_cfg["base_url"] = normalize_openai_base_url(raw_base_url, service)
		except ValueError as exc:
			raise ValueError("LuluCoding Base URL 无效，请填写根地址或 /v1 地址。") from exc

	clear_credentials = bool(ai_cfg.pop("clear_credentials", False))

	for field in ("api_key", "auth_token"):
		if clear_credentials:
			posted_value = ai_cfg.get(field)
			if posted_value is None or str(posted_value).strip() == "":
				ai_cfg.pop(field, None)
			continue
		posted_value = ai_cfg.get(field)
		existing_value = existing_ai.get(field)
		existing_mask = _mask_api_key(str(existing_value)) if existing_value else ""
		should_preserve = (
			posted_value is None
			or str(posted_value).strip() == ""
			or (existing_mask and posted_value == existing_mask)
		)

		if should_preserve:
			if existing_value:
				ai_cfg[field] = existing_value
			else:
				ai_cfg.pop(field, None)

	return cleaned


def _preflight_messages(mode: str, config: dict) -> list[str]:
	"""Return user-actionable blockers before starting a dashboard task."""
	messages: list[str] = []
	if mode not in {"full", "collect", "score", "rescore", "greet", "monitor", "deliver"}:
		messages.append(f"不支持的任务模式：{mode}")

	profile = config.get("profile", {})
	resume_path = profile.get("resume_path", "")
	if not resume_path or not Path(str(resume_path)).exists():
		messages.append("请先在配置页上传 .md 或 .docx 简历。")

	if mode in {"full", "collect"} and not config.get("search", {}).get("keywords"):
		messages.append("请先在配置页填写搜索关键词。")

	if mode in {"full", "score", "rescore", "greet"} and not get_ai_api_key(config):
		messages.append("请先在配置页填写当前 AI 服务的 API Key，或设置对应的标准环境变量。")

	return messages


def _task_config(extra: dict | None = None) -> dict:
	config = load_config(CONFIG_PATH)
	if extra:
		config.update(extra)
	return config


def _log(task: WorkbenchTask, message: str) -> None:
	task.logs.append(message)


def _execute_collect(task: WorkbenchTask, config: dict) -> None:
	from bosshunter.scraper.jobs import scrape_jobs

	keywords = config.get("search", {}).get("keywords", [])
	task_runner.set_stage(task, "采集岗位", {"combos": 0, "inserted": 0})
	_log(task, "开始采集岗位（单独采集不会自动评分、发送或监测）")
	config["_workbench_log"] = lambda message: _log(task, message)
	config["_workbench_collect_progress"] = lambda state: _update_collect_progress(task, state)
	scrape_jobs(config, keywords)
	if task.stop_requested.is_set():
		return
	if task.pause_requested.is_set():
		task.context["outcome"] = "paused"
		return
	task_runner.set_stage(task, "采集完成", {"combos": len(config.get("_workbench_collect_report", [])), "inserted": sum(int(item.get("inserted", 0) or 0) for item in config.get("_workbench_collect_report", []))})


def _execute_rescore(task: WorkbenchTask, config: dict) -> None:
	from bosshunter.ai.scorer import score_jobs

	score_config = dict(config)
	score_config["_workbench_stop_event"] = task.stop_requested
	score_config["_workbench_log"] = lambda message: _log(task, message)
	score_config["_workbench_score_progress"] = lambda state: _log(
		task,
		f"AI 评分进度 {state['completed']}/{state['total']}：通过 {state['scored']}，过滤 {state['filtered']}，失败 {state['failed']}",
	)
	_log(task, "开始重新评分")
	result = score_jobs(score_config, rescore_filtered=True)
	task.context["outcome"] = result.outcome


def _execute_score(task: WorkbenchTask, config: dict) -> None:
	from bosshunter.ai.scorer import score_jobs

	score_config = dict(config)
	score_config["_workbench_stop_event"] = task.stop_requested
	score_config["_workbench_log"] = lambda message: _log(task, message)
	score_config["_workbench_score_progress"] = lambda state: _log(
		task,
		f"AI 评分进度 {state['completed']}/{state['total']}：通过 {state['scored']}，过滤 {state['filtered']}，失败 {state['failed']}",
	)
	task_runner.set_stage(task, "AI 评分", {})
	options = dict(config.get("_score_options") or {})
	_log(task, f"开始单独 AI 评分：{options.get('scope', 'pending')}，数量 {options.get('limit') or '全部'}")
	result = score_jobs(score_config, **options)
	task.context["outcome"] = result.outcome
	task.progress = result.to_dict()
	if result.outcome in {"paused", "stopped"}:
		task.checkpoint = {"stage": "score", "remaining_job_ids": []}
	_log(
		task,
		f"单独 AI 评分完成：通过 {result.passed}，过滤 {result.filtered}，失败 {result.failed}，剩余 {result.remaining}",
	)


def _update_collect_progress(task: WorkbenchTask, state: dict) -> None:
	report = {
		"keyword": state.get("keyword"),
		"city": state.get("city"),
		"target": state.get("target"),
		"inserted": state.get("inserted"),
		"pages_scanned": state.get("pages_scanned"),
		"shortfall_reason": state.get("shortfall_reason"),
	}
	task.progress = {**state, "combo": report}
	task.stage = f"采集：{state.get('city', '')} × {state.get('keyword', '')} {state.get('inserted', 0)}/{state.get('target', 0)}"
	task.checkpoint = {
		"stage": "collect",
		"combo_index": state.get("combo_index", 0),
		"current_combo": report,
		"combo_reports": task.context.get("combo_reports", []),
	}
	task.context.setdefault("combo_reports", []).append(report)
	task.updated_at = __import__("datetime").datetime.now().isoformat(timespec="seconds")
	_log(task, f"{task.stage}，不足原因：{state.get('shortfall_reason') or '无'}")
	task_runner._persist(task)


def _queue_monitor_delivery(
	task: WorkbenchTask,
	job_ids: list[str],
	*,
	direct_send: bool = False,
) -> dict:
	"""Queue confirmed jobs on a task that is already in its monitor loop."""
	queue_lock = task.context.get("monitor_queue_lock")
	if queue_lock is None:
		queue_lock = Lock()
		task.context["monitor_queue_lock"] = queue_lock
	with queue_lock:
		pending = task.context.setdefault("pending_deliveries", [])
		queued_ids = {
			str(job_id)
			for batch in pending
			for job_id in batch.get("job_ids", [])
		}
		new_ids = [job_id for job_id in job_ids if job_id not in queued_ids]
		if new_ids:
			pending.append({"job_ids": new_ids, "direct_send": direct_send})
			_log(task, f"监测期间新增 {len(new_ids)} 个确认投递岗位，已加入发送队列")
	wakeup_event = task.context.get("monitor_wakeup_event")
	if isinstance(wakeup_event, Event):
		wakeup_event.set()
	return task.snapshot()


def _take_monitor_deliveries(task: WorkbenchTask) -> list[dict]:
	queue_lock = task.context.get("monitor_queue_lock")
	if queue_lock is None:
		return []
	with queue_lock:
		pending = list(task.context.get("pending_deliveries", []))
		task.context["pending_deliveries"] = []
	return pending


def _execute_monitor(task: WorkbenchTask, config: dict) -> None:
	from bosshunter.executor.monitor import monitor_and_send_resumes

	monitor_config = dict(config)
	monitor_config["_workbench_stop_event"] = task.stop_requested
	interval_min = int(config.get("monitor", {}).get("interval", 30) or 30)
	interval_sec = max(interval_min * 60, 1)
	queue_lock = task.context.setdefault("monitor_queue_lock", Lock())
	wakeup_event = task.context.setdefault("monitor_wakeup_event", Event())
	task.context["monitoring"] = True
	try:
		while not task.stop_requested.is_set():
			for batch in _take_monitor_deliveries(task):
				deliver_config = dict(config)
				deliver_config["_workbench_job_ids"] = batch.get("job_ids", [])
				if batch.get("direct_send"):
					deliver_config["_workbench_skip_greeting"] = True
				_log(task, f"处理监测期间新增的 {len(deliver_config['_workbench_job_ids'])} 个投递岗位")
				_execute_deliver(task, deliver_config)
				if task.stop_requested.is_set():
					return
			_log(task, "执行一轮监测")
			monitor_and_send_resumes(monitor_config)
			if task.stop_requested.is_set():
				return
			_log(task, f"本轮监测完成，{interval_min} 分钟后再次检查")
			wakeup_event.wait(interval_sec)
			wakeup_event.clear()
	finally:
		task.context["monitoring"] = False
		task.context.pop("monitor_wakeup_event", None)
		task.context.pop("monitor_queue_lock", None)


def _execute_full(task: WorkbenchTask, config: dict) -> None:
	db = _get_web_db()
	try:
		deferred_job_ids = [str(job["id"]) for job in get_jobs_ready_to_send(db)]
	finally:
		db.close()
	if deferred_job_ids:
		_log(task, f"优先续发上次已确认但未完成的 {len(deferred_job_ids)} 个岗位")
		deferred_config = load_config(CONFIG_PATH)
		deferred_config["_workbench_stop_event"] = task.stop_requested
		deferred_config["_workbench_pause_event"] = task.pause_requested
		deferred_config["_workbench_job_ids"] = deferred_job_ids
		deferred_config["_workbench_skip_greeting"] = True
		_execute_deliver(task, deferred_config)
		if task.stop_requested.is_set() or task.pause_requested.is_set() or task.context.get("outcome") == "paused":
			return

	_execute_collect(task, config)
	if task.stop_requested.is_set() or task.pause_requested.is_set() or task.context.get("outcome") == "paused":
		return
	# Full flow explicitly chains scoring after the collection-only executor.
	# Empty configs are kept compatible with lightweight executor tests; normal
	# full-flow preflight always supplies keywords and a resume.
	if config.get("search", {}).get("keywords") or config.get("profile", {}).get("resume_path"):
		_execute_score(task, config)
	if task.stop_requested.is_set() or task.pause_requested.is_set() or task.context.get("outcome") == "paused":
		return

	db = _get_web_db()
	try:
		threshold = int(config.get("scoring", {}).get("threshold", 60) or 60)
		pending_confirmation = [
			job for job in get_jobs_pending_confirmation(db)
			if int(job.get("score") or 0) >= threshold or bool(job.get("manual_override"))
		]
	finally:
		db.close()
	if not pending_confirmation:
		task.context["waiting_confirmation"] = False
		_log(task, "没有待确认岗位，流程结束")
		return

	confirmation_event = Event()
	task.context["waiting_confirmation"] = True
	task.context["confirmation_event"] = confirmation_event
	_log(task, "等待前端确认投递")
	while not task.stop_requested.is_set() and not confirmation_event.wait(0.5):
		pass
	if task.stop_requested.is_set() or task.pause_requested.is_set() or task.context.get("outcome") == "paused":
		return

	job_ids = [str(job_id) for job_id in task.context.get("confirmed_job_ids", []) if str(job_id)]
	if not job_ids:
		_log(task, "未收到前端确认岗位，流程结束")
		return

	task.context["waiting_confirmation"] = False
	_log(task, f"前端已确认 {len(job_ids)} 个岗位，继续投递")
	# The user may adjust the daily limit or other send settings while reviewing
	# jobs. Reload immediately before delivery instead of using the task-start snapshot.
	deliver_config = dict(config)
	# Keep the original task's stage/options, while refreshing only user-facing
	# send settings is intentionally left to the explicit send executor.
	latest_config = load_config(CONFIG_PATH)
	deliver_config["throttle"] = latest_config.get("throttle", deliver_config.get("throttle", {}))
	deliver_config["ai"] = latest_config.get("ai", deliver_config.get("ai", {}))
	deliver_config["_workbench_job_ids"] = job_ids
	_execute_deliver(task, deliver_config)
	if task.stop_requested.is_set() or task.pause_requested.is_set() or task.context.get("outcome") == "paused":
		return
	monitor_config = dict(config)
	latest_config = load_config(CONFIG_PATH)
	monitor_config["monitor"] = latest_config.get("monitor", monitor_config.get("monitor", {}))
	_execute_monitor(task, monitor_config)


def _execute_deliver(task: WorkbenchTask, config: dict) -> None:
	from bosshunter.ai.greeter import generate_greetings
	from bosshunter.executor.sender import send_greetings

	config = dict(config)
	config["_workbench_stop_event"] = task.stop_requested
	config["_workbench_pause_event"] = task.pause_requested
	config["_workbench_log"] = lambda message: _log(task, message)
	selected_job_ids = [str(job_id) for job_id in config.get("_workbench_job_ids", []) if str(job_id)]
	generated_job_ids = selected_job_ids
	if not config.get("_workbench_skip_greeting"):
		task_runner.set_stage(task, "生成招呼语", {"selected": len(selected_job_ids)})
		_log(task, "生成招呼语")
		greeting_result = generate_greetings(config, job_ids=selected_job_ids or None)
		if isinstance(greeting_result, int):
			from bosshunter.ai.greeter import GreetingResult
			generated_number = max(0, min(int(greeting_result), len(selected_job_ids)))
			greeting_result = GreetingResult(
				selected=len(selected_job_ids),
				generated=generated_number,
				failed=max(len(selected_job_ids) - generated_number, 0),
				outcome="completed_with_errors" if generated_number < len(selected_job_ids) else "completed",
				generated_job_ids=selected_job_ids[:generated_number],
			)
		_log(task, f"招呼语生成完成：成功 {greeting_result.generated}，失败 {greeting_result.failed}，剩余 {greeting_result.remaining}")
		task.progress = greeting_result.to_dict()
		if greeting_result.outcome == "paused":
			task.context["outcome"] = "paused"
			task.checkpoint = {
				"stage": "greet",
				"remaining_job_ids": list(greeting_result.remaining_job_ids),
				"failed_job_ids": list(greeting_result.failed_job_ids),
			}
			return
		if greeting_result.outcome == "stopped" or task.stop_requested.is_set():
			return
		# Partial job-level greeting failures intentionally do not fail the task.
		# Sender queries only ready jobs, so failed approved jobs remain visible and
		# are never sent accidentally.
	if not config.get("_workbench_skip_greeting"):
		generated_job_ids = greeting_result.generated_job_ids
		if generated_job_ids:
			config["_workbench_job_ids"] = generated_job_ids
	_log(task, "发送招呼语")
	sent_count = send_greetings(config, force=True)
	report = config.get("_workbench_send_report", {})
	failed_count = int(report.get("failed_count", 0) or 0)
	deferred_count = int(report.get("deferred_count", 0) or 0)
	quota_deferred_count = min(
		int(report.get("quota_deferred_count", 0) or 0),
		deferred_count,
	)
	paused_count = max(deferred_count - quota_deferred_count, 0)
	total_count = len(selected_job_ids) or sent_count + failed_count + deferred_count
	_log(
		task,
		f"招呼语发送结果：成功 {sent_count}，失败 {failed_count}，待下次发送 {deferred_count}（共 {total_count}）",
	)
	if failed_count:
		_log(task, f"{failed_count} 个岗位发送失败已单独记录，继续后续流程")
	if quota_deferred_count:
		_log(task, f"{quota_deferred_count} 个岗位因今日发送额度未执行，已保留在“待发送招呼语”")
	if paused_count:
		_log(task, f"{paused_count} 个岗位本轮未执行，已保留在“待发送招呼语”")

	stop_reason = report.get("stop_reason")
	if stop_reason in {"captcha", "rate_limit", "blocked", "consecutive_errors"}:
		reason_labels = {
			"captcha": "验证码",
			"rate_limit": "频率限制",
			"blocked": "账号或请求被拦截",
			"consecutive_errors": "连续错误过多",
		}
		raise RuntimeError(f"发送已安全暂停：检测到{reason_labels[stop_reason]}")


def _execute_greet(task: WorkbenchTask, config: dict) -> None:
	from bosshunter.ai.greeter import generate_greetings

	config = dict(config)
	config["_workbench_stop_event"] = task.stop_requested
	config["_workbench_pause_event"] = task.pause_requested
	config["_workbench_log"] = lambda message: _log(task, message)
	options = dict(config.get("_greet_options") or {})
	job_ids = [str(value) for value in options.get("job_ids", config.get("_workbench_job_ids", [])) if str(value)]
	result = generate_greetings(
		config,
		job_ids=job_ids or None,
		force_regenerate=bool(options.get("force_regenerate", False)),
	)
	task.progress = result.to_dict()
	task.context["outcome"] = result.outcome
	if result.outcome == "paused":
		task.checkpoint = {
			"stage": "greet",
			"remaining_job_ids": list(result.remaining_job_ids),
			"failed_job_ids": result.failed_job_ids,
		}
	_log(task, f"招呼语任务完成：成功 {result.generated}，失败 {result.failed}，剩余 {result.remaining}")


task_runner._executors.update({
	"full": _execute_full,
	"collect": _execute_collect,
	"score": _execute_score,
	"rescore": _execute_rescore,
	"greet": _execute_greet,
	"monitor": _execute_monitor,
	"deliver": _execute_deliver,
})


# ─── Health ───────────────────────────────────────────────

@app.route("/api/health")
def health():
	return _json_response({"status": "ok", "version": __version__})


# ─── Dashboard APIs ──────────────────────────────────────

@app.route("/api/funnel")
def api_funnel():
	db = _get_web_db()
	try:
		data = get_funnel_stats(db)
		return _json_response(data)
	finally:
		db.close()


@app.route("/api/stats")
def api_stats():
	db = _get_web_db()
	try:
		data = get_stats(db)
		return _json_response(data)
	finally:
		db.close()


@app.route("/api/activity")
def api_activity():
	days = int(request.params.get("days", 7))
	db = _get_web_db()
	try:
		data = get_daily_activity(db, days)
		return _json_response(data)
	finally:
		db.close()


@app.route("/api/jobs")
def api_jobs():
	status_filter = request.params.get("status", None)
	city_filter = str(request.params.get("city", "") or "").strip()
	search = str(request.params.get("q", "") or "").strip()
	deleted = request.params.get("deleted", "active")
	try:
		limit = int(request.params.get("limit", 200))
		offset = int(request.params.get("offset", 0))
		if not 1 <= limit <= 500 or offset < 0:
			raise ValueError
		filters = {"q": search, "status": status_filter or "", "city": city_filter}
		# Validate the shared filter contract before opening the database.
		normalize_job_filters(filters)
		if not isinstance(deleted, str) or deleted not in {"active", "only", "all"}:
			raise ValueError
	except (TypeError, ValueError):
		return _json_response({"error": "岗位列表参数无效"}, 400)

	db = _get_web_db()
	try:
		jobs, total = query_jobs(db, deleted=deleted, filters=filters, limit=limit, offset=offset)
		if deleted == "only":
			for job in jobs:
				reasons = get_job_permanent_delete_reasons(db, str(job["id"]))
				job["permanent_delete_allowed"] = not reasons
				job["permanent_delete_reasons"] = reasons
		response.headers["X-Total-Count"] = str(total)
		return _json_response(jobs)
	except ValueError as exc:
		return _json_response({"error": str(exc)}, 400)
	finally:
		db.close()


@app.route("/api/top-companies")
def api_top_companies():
	limit = int(request.params.get("limit", 5))
	db = _get_web_db()
	try:
		data = get_top_companies(db, limit)
		return _json_response(data)
	finally:
		db.close()


@app.route("/api/history")
def api_history():
	limit = int(request.params.get("limit", 15))
	include_unresolved = request.params.get("include_unresolved", "").lower() in ("1", "true", "yes")
	db = _get_web_db()
	try:
		data = get_recent_history(db, limit)
		if include_unresolved:
			seen_ids = {item["id"] for item in data}
			data.extend(
				item
				for item in get_unresolved_resume_failures(db)
				if item["id"] not in seen_ids
			)
			data.sort(
				key=lambda item: (str(item.get("created_at") or ""), int(item.get("id") or 0)),
				reverse=True,
			)
		return _json_response(_serialize_history_items(data))
	finally:
		db.close()


@app.route("/api/history/unresolved-replies/count")
def api_history_unresolved_replies_count():
	db = _get_web_db()
	try:
		return _json_response({"count": count_unresolved_monitor_items(db)})
	finally:
		db.close()


@app.route("/api/workbench")
def api_workbench():
	db = _get_web_db()
	try:
		threshold = load_config(CONFIG_PATH).get("scoring", {}).get("threshold", 60)
		status = task_runner.status()
		return _json_response({
			"funnel": get_funnel_stats(db),
			"pending_confirmation": [
				job for job in get_jobs_pending_confirmation(db)
				if int(job.get("score") or 0) >= threshold or bool(job.get("manual_override"))
			],
			# Legacy field retained for older clients; new clients use the explicit
			# generation/ready collections below.
			"pending_greetings": get_jobs_ready_to_send(db),
			"greeting_generation_items": get_jobs_greeting_generation_items(db),
			"ready_to_send": get_jobs_ready_to_send(db),
			"send_errors": get_jobs_with_send_errors(db),
			"needs_resume": get_jobs_needing_resume(db),
			"task": status["active"],
			"last_task": status["last_task"],
		})
	finally:
		db.close()


@app.route("/api/workbench/preflight")
def api_workbench_preflight():
	mode = request.params.get("mode", "")
	try:
		config = load_config(CONFIG_PATH)
		checks = collect_preflight_checks(mode, config)
		messages = error_messages(checks)
		return _json_response({"ok": not messages, "messages": messages, "checks": checks})
	except Exception as e:
		return _json_response({"ok": False, "messages": [str(e)]}, 500)


@app.route("/api/diagnostics/ai")
def api_ai_diagnostics():
	try:
		result = diagnose_ai(load_config(CONFIG_PATH))
		checks = [
			{
				"id": stage.get("id", "ai"),
				"title": stage.get("title") or ("AI API Key" if stage.get("id") in {"credentials", "ai_credentials"} else "AI 连接检测"),
				"status": "pass" if stage.get("status") == "pass" else "error",
				"message": stage.get("message", "检测失败"),
				"detail": stage.get("detail", ""),
				"action": "config",
			}
			for stage in result.get("stages", [])
		]
		messages = [f"{item['message']}：{item['detail']}" for item in checks if item["status"] == "error"]
		return _json_response({**result, "messages": messages, "checks": checks})
	except Exception as e:
		return _json_response({"ok": False, "billable": False, "messages": ["AI 连接检测失败"], "error_kind": "request_failed"}, 500)


@app.route("/api/diagnostics/ai/advanced", method="POST")
def api_ai_diagnostics_advanced():
	try:
		body = request.json or {}
		if body.get("confirmed") is not True:
			return _json_response({"error": "高级 AI 测试需要再次确认，并会产生少量 Token。"}, 400)
		return _json_response(advanced_diagnose_ai(load_config(CONFIG_PATH), confirmed=True))
	except ValueError as e:
		return _json_response({"error": str(e)}, 400)
	except Exception:
		return _json_response({"error": "高级 AI 测试失败", "error_kind": "request_failed"}, 502)


@app.route("/api/scoring/preview", method="POST")
def api_scoring_preview():
	try:
		body = request.json or {}
		options = body.get("options") if isinstance(body.get("options"), dict) else body
		config = load_config(CONFIG_PATH)
		validated = validate_options(
			options.get("scope", "pending"),
			options.get("limit", 20),
			options.get("job_ids", []),
			options.get("force_rescore", False),
		)
		db = _get_web_db()
		try:
			preview = preview_scoring(
				db,
				**validated,
				max_attempts_per_job=config.get("ai", {}).get("scoring_max_attempts", 2),
			)
		finally:
			db.close()
		return _json_response(preview)
	except ValueError as e:
		return _json_response({"error": str(e)}, 400)
	except Exception:
		return _json_response({"error": "无法生成评分范围预览"}, 500)


@app.route("/api/workbench/task", method="POST")
def api_workbench_task_start():
	try:
		body = request.json or {}
		mode = body.get("mode", "")
		status = task_runner.status()
		conflict_task = status.get("active")
		if conflict_task:
			return _json_response({
				"error": "当前存在可恢复或正在运行的后台任务",
				"conflict": {
					"task": conflict_task,
					"choices": [
						{"id": "resume", "label": "继续旧任务"},
						{"id": "end_and_start", "label": "结束旧任务并开始新任务"},
						{"id": "cancel", "label": "取消"},
					],
				},
			}, 409)
		config = load_config(CONFIG_PATH)
		extra = {}
		if mode == "score":
			options = body.get("options") if isinstance(body.get("options"), dict) else {}
			validated = validate_options(
				options.get("scope", "pending"),
				options.get("limit", 20),
				options.get("job_ids", []),
				options.get("force_rescore", False),
			)
			extra["_score_options"] = validated
		elif mode == "greet":
			options = body.get("options") if isinstance(body.get("options"), dict) else {}
			ids = [str(value) for value in options.get("job_ids", []) if str(value)]
			if len(ids) > 5000:
				return _json_response({"error": "一次最多重新生成 5000 个岗位"}, 400)
			extra["_greet_options"] = {"job_ids": list(dict.fromkeys(ids)), "force_regenerate": bool(options.get("force_regenerate", False))}
		messages = _preflight_messages(mode, config)
		if messages:
			return _json_response({"error": "请先处理启动前检查", "messages": messages}, 400)
		task = task_runner.start(mode, _task_config(extra))
		return _json_response(task)
	except TaskAlreadyRunningError as e:
		return _json_response({"error": str(e), "conflict": {"task": e.existing_task.snapshot() if e.existing_task else None}}, 409)
	except ValueError as e:
		return _json_response({"error": str(e)}, 400)
	except Exception as e:
		return _json_response({"error": str(e)}, 500)


@app.route("/api/workbench/task/<task_id>/stop", method="POST")
def api_workbench_task_stop(task_id):
	try:
		return _json_response(task_runner.stop(task_id))
	except KeyError:
		return _json_response({"error": "任务不存在"}, 404)
	except Exception as e:
		return _json_response({"error": str(e)}, 500)


@app.route("/api/workbench/task/<task_id>/pause", method="POST")
def api_workbench_task_pause(task_id):
	try:
		return _json_response(task_runner.pause(task_id))
	except KeyError:
		return _json_response({"error": "任务不存在"}, 404)
	except Exception as e:
		return _json_response({"error": str(e)}, 500)


@app.route("/api/workbench/task/<task_id>/resume", method="POST")
def api_workbench_task_resume(task_id):
	try:
		# Resume uses the task's original non-secret settings and obtains only
		# current credentials from the in-memory config.
		return _json_response(task_runner.resume(task_id, load_config(CONFIG_PATH)))
	except KeyError:
		return _json_response({"error": "任务不存在"}, 404)
	except TaskAlreadyRunningError as e:
		return _json_response({"error": str(e)}, 409)
	except Exception as e:
		return _json_response({"error": str(e)}, 500)


@app.route("/api/workbench/task/<task_id>/end", method="POST")
def api_workbench_task_end(task_id):
	try:
		return _json_response(task_runner.end(task_id))
	except KeyError:
		return _json_response({"error": "任务不存在"}, 404)
	except Exception as e:
		return _json_response({"error": str(e)}, 500)


@app.route("/api/workbench/recoverable")
def api_workbench_recoverable():
	return _json_response({"tasks": task_runner.recoverable()})


@app.route("/api/workbench/deliver", method="POST")
def api_workbench_deliver():
	try:
		body = request.json or {}
		job_ids = [str(job_id) for job_id in body.get("job_ids", []) if str(job_id)]
		if not job_ids:
			return _json_response({"error": "请选择要投递的岗位"}, 400)

		direct_send = bool(body.get("direct_send"))
		db = _get_web_db()
		try:
			for job_id in job_ids:
				update_job_status(db, job_id, "approved")
				if not direct_send:
					add_history(db, job_id, "approved", "Web Dashboard 确认投递")
		finally:
			db.close()

		if not direct_send:
			status = task_runner.status()
			active_task = status.get("active") or {}
			waiting_task = task_runner._tasks.get(active_task.get("id"))
			if (
				waiting_task
				and waiting_task.mode == "full"
				and waiting_task.status == "running"
				and waiting_task.context.get("waiting_confirmation")
			):
				waiting_task.context["confirmed_job_ids"] = job_ids
				confirmation_event = waiting_task.context.get("confirmation_event")
				if isinstance(confirmation_event, Event):
					confirmation_event.set()
				return _json_response(waiting_task.snapshot())

		status = task_runner.status()
		active_task = status.get("active") or {}
		monitoring_task = task_runner._tasks.get(active_task.get("id"))
		if (
			monitoring_task
			and monitoring_task.status == "running"
			and monitoring_task.context.get("monitoring")
		):
			return _json_response(
				_queue_monitor_delivery(
					monitoring_task,
					job_ids,
					direct_send=direct_send,
				)
			)

		deliver_options = {"_workbench_job_ids": job_ids}
		if direct_send:
			deliver_options["_workbench_skip_greeting"] = True
		task = task_runner.start("deliver", _task_config(deliver_options))
		return _json_response(task)
	except TaskAlreadyRunningError as e:
		return _json_response({"error": str(e)}, 409)
	except Exception as e:
		return _json_response({"error": str(e)}, 500)


@app.route("/api/workbench/reject", method="POST")
def api_workbench_reject():
	try:
		body = request.json or {}
		job_ids = [str(job_id) for job_id in body.get("job_ids", []) if str(job_id)]
		if not job_ids:
			return _json_response({"error": "请选择要放弃的岗位"}, 400)

		db = _get_web_db()
		try:
			for job_id in job_ids:
				update_job_status(db, job_id, "rejected")
				add_history(db, job_id, "rejected", "Web Dashboard 放弃投递")
		finally:
			db.close()

		return _json_response({"success": True, "count": len(job_ids)})
	except Exception as e:
		return _json_response({"error": str(e)}, 500)


@app.route("/api/jobs/<job_id>")
def api_job_detail(job_id):
	db = _get_web_db()
	try:
		row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
		if not row:
			return _json_response({"error": "岗位不存在"}, 404)
		return _json_response(dict(row))
	finally:
		db.close()


@app.route("/api/jobs/<job_id>/override-filter", method="POST")
def api_job_override_filter(job_id):
	try:
		body = request.json or {}
		if body.get("confirmed") is not True:
			return _json_response({"error": "人工推进前必须明确确认忽略该岗位偏好过滤"}, 400)
		db = _get_web_db()
		try:
			job = override_filtered_job(db, job_id)
			if not job:
				return _json_response({"error": "岗位不存在"}, 404)
			return _json_response({"success": True, "job": job})
		finally:
			db.close()
	except ValueError as exc:
		return _json_response({"error": str(exc)}, 409)
	except Exception:
		return _json_response({"error": "该岗位不能人工推进"}, 400)


@app.route("/api/profiles")
def api_profiles_get():
	db = _get_web_db()
	try:
		config = load_config(CONFIG_PATH)
		profile = ensure_profile_for_resume(db, config.get("profile", {}).get("resume_path"))
		return _json_response({"profiles": [profile_payload(db, str(item["id"])) for item in list_candidate_profiles(db)]})
	finally:
		db.close()


@app.route("/api/profiles", method="POST")
def api_profiles_post():
	import uuid

	body = request.json or {}
	name = str(body.get("name") or "未命名求职资料").strip()[:100]
	if not name:
		return _json_response({"error": "资料方案名称不能为空"}, 400)
	db = _get_web_db()
	try:
		profile_id = str(uuid.uuid4())
		is_default = bool(body.get("is_default", False))
		if is_default:
			db.execute("UPDATE candidate_profiles SET is_default = 0")
		db.execute("INSERT INTO candidate_profiles (id, name, is_default) VALUES (?, ?, ?)", (profile_id, name, int(is_default)))
		db.commit()
		return _json_response(profile_payload(db, profile_id), 201)
	finally:
		db.close()


@app.route("/api/profiles/<profile_id>")
def api_profile_get(profile_id):
	db = _get_web_db()
	try:
		payload = profile_payload(db, profile_id)
		return _json_response(payload or {"error": "求职资料不存在"}, 200 if payload else 404)
	finally:
		db.close()


@app.route("/api/profiles/<profile_id>", method="PATCH")
def api_profile_patch(profile_id):
	body = request.json or {}
	db = _get_web_db()
	try:
		if not get_candidate_profile(db, profile_id):
			return _json_response({"error": "求职资料不存在"}, 404)
		name = body.get("name")
		if name is not None and not str(name).strip():
			return _json_response({"error": "资料方案名称不能为空"}, 400)
		profile = update_candidate_profile(db, profile_id, name=str(name).strip() if name is not None else None, is_default=body.get("is_default"))
		return _json_response(profile_payload(db, profile["id"]))
	finally:
		db.close()


@app.route("/api/profiles/<profile_id>/documents/<document_id>", method="PATCH")
def api_profile_document_patch(profile_id, document_id):
	body = request.json or {}
	db = _get_web_db()
	try:
		if body.get("is_primary") is not True:
			return _json_response({"error": "只能通过明确选择设置主简历"}, 400)
		document = set_candidate_document_primary(db, profile_id, document_id)
		if not document:
			return _json_response({"error": "资料文件不存在"}, 404)
		return _json_response(profile_payload(db, profile_id))
	finally:
		db.close()


@app.route("/api/profiles/<profile_id>/facts")
def api_profile_facts_get(profile_id):
	db = _get_web_db()
	try:
		if not get_candidate_profile(db, profile_id):
			return _json_response({"error": "求职资料不存在"}, 404)
		return _json_response({"facts": list_candidate_facts(db, profile_id)})
	finally:
		db.close()


@app.route("/api/profiles/<profile_id>/facts", method="POST")
def api_profile_fact_post(profile_id):
	body = request.json or {}
	content = str(body.get("content") or "").strip()
	if not content:
		return _json_response({"error": "事实内容不能为空"}, 400)
	db = _get_web_db()
	try:
		if not get_candidate_profile(db, profile_id):
			return _json_response({"error": "求职资料不存在"}, 404)
		facts = derive_pending_facts(db, profile_id=profile_id, document_id=None, text=content)
		return _json_response({"facts": facts}, 201)
	finally:
		db.close()


@app.route("/api/profiles/<profile_id>/facts/<fact_id>", method="PATCH")
def api_profile_fact_patch(profile_id, fact_id):
	body = request.json or {}
	status = body.get("status")
	if status is not None and status not in {"pending", "confirmed", "rejected", "conflict"}:
		return _json_response({"error": "事实状态无效"}, 400)
	db = _get_web_db()
	try:
		fact = update_candidate_fact(db, fact_id, status=status, content=body.get("content"))
		if not fact or str(fact.get("profile_id")) != str(profile_id):
			return _json_response({"error": "事实不存在"}, 404)
		return _json_response(fact)
	finally:
		db.close()


@app.route("/api/jobs/<job_id>/mark-resume-sent", method="POST")
def api_job_mark_resume_sent(job_id):
	db = _get_web_db()
	try:
		update_job_status(db, job_id, "resume_sent")
		add_history(db, job_id, "resume_sent", "Web Dashboard 标记定制简历已发送")
		return _json_response({"success": True})
	finally:
		db.close()


@app.route("/api/jobs/<job_id>/resume/download")
def api_job_resume_download(job_id):
	db = _get_web_db()
	try:
		row = db.execute("SELECT resume_path FROM jobs WHERE id = ?", (job_id,)).fetchone()
		if not row or not row["resume_path"]:
			return _json_response({"error": "定制简历不存在"}, 404)
		resume_path = Path(row["resume_path"])
		if not resume_path.exists():
			return _json_response({"error": "定制简历文件不存在"}, 404)
		return static_file(resume_path.name, root=str(resume_path.parent), download=resume_path.name)
	finally:
		db.close()


@app.route("/api/history/<history_id>/reply", method="POST")
def api_history_reply(history_id):
	db = _get_web_db()
	try:
		body = request.json or {}
		message = str(body.get("message", "")).strip()
		if not message:
			return _json_response({"error": "回复内容不能为空"}, 400)

		row = db.execute(
			"SELECT id, job_id, action, detail FROM history WHERE id = ?",
			(history_id,),
		).fetchone()
		if not row:
			return _json_response({"error": "待回复记录不存在"}, 404)
		if row["action"] != "reply_pending":
			return _json_response({"error": "只能确认待回复记录"}, 400)

		from bosshunter.executor.monitor import _build_reply_resolution_detail

		add_history(
			db,
			row["job_id"],
			"replied",
			_build_reply_resolution_detail(
				"replied.v1",
				"Web Dashboard 确认回复",
				row["detail"],
				message,
				int(row["id"]),
			),
		)
		update_job_status(db, row["job_id"], "replied")
		return _json_response({"success": True, "message": "回复已记录，请在招聘平台手动发送。"})
	except Exception as e:
		return _json_response({"error": str(e)}, 500)
	finally:
		db.close()


@app.route("/api/history/<history_id>/dismiss", method="POST")
def api_history_dismiss(history_id):
	db = _get_web_db()
	try:
		row = db.execute(
			"SELECT id, job_id, action, detail FROM history WHERE id = ?",
			(history_id,),
		).fetchone()
		if not row:
			return _json_response({"error": "待回复记录不存在"}, 404)
		if row["action"] != "reply_pending":
			return _json_response({"error": "只能放弃待回复记录"}, 400)

		from bosshunter.executor.monitor import _build_reply_resolution_detail

		add_history(
			db,
			row["job_id"],
			"reply_dismissed",
			_build_reply_resolution_detail(
				"reply_dismissed.v1",
				"Web Dashboard 放弃回复建议",
				row["detail"],
				pending_history_id=int(row["id"]),
			),
		)
		return _json_response({"success": True})
	finally:
		db.close()


# ─── Config APIs ─────────────────────────────────────────

@app.route("/api/config")
def api_config_get():
	try:
		config = _redact_config_for_response(load_config(CONFIG_PATH))
		return _json_response(config)
	except Exception as e:
		return _json_response({"error": str(e)}, 500)


@app.route("/api/config", method="POST")
def api_config_post():
	try:
		import yaml
		data = request.json
		if not data:
			return _json_response({"error": "Empty body"}, 400)
		if not isinstance(data, dict):
			return _json_response({"error": "Config body must be an object"}, 400)
		data = _sanitize_config_for_write(data)

		# Basic validation
		profile = data.get("profile", {})
		if profile.get("salary_min", 0) > profile.get("salary_max", 0) and profile.get("salary_max", 0) > 0:
			return _json_response({"error": "salary_min must be <= salary_max"}, 400)

		# Write YAML (backend exclusively owns YAML serialization)
		with open(CONFIG_PATH, "w", encoding="utf-8") as f:
			yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

		return _json_response({"success": True, "message": "配置已保存"})
	except Exception as e:
		return _json_response({"error": str(e)}, 500)


@app.route("/api/config/schema")
def api_config_schema():
	try:
		with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
			schema = json.load(f)
		return _json_response(schema)
	except Exception as e:
		return _json_response({"error": str(e)}, 500)


@app.route("/api/config/download")
def api_config_download():
	if CONFIG_PATH.exists():
		response.content_type = "application/x-yaml; charset=utf-8"
		response.headers["Content-Disposition"] = "attachment; filename=config.yaml"
		return CONFIG_PATH.read_text(encoding="utf-8")
	abort(404, "config.yaml not found")


@app.route("/api/config/cities")
def api_cities():
	return _json_response(get_city_map(BASE_DIR))


@app.route("/api/cities")
def api_city_snapshot():
	try:
		snapshot = load_city_snapshot(BASE_DIR)
		return _json_response({
			"ok": True,
			"source": snapshot.get("source", "bundled"),
			"count": len(snapshot.get("cities", [])),
			"updated_at": snapshot.get("fetched_at"),
			"cities": snapshot.get("cities", []),
		})
	except Exception:
		return _json_response({"ok": False, "source": "bundled", "count": 0, "cities": [], "error": "本地城市列表不可用"}, 500)


@app.route("/api/cities/refresh", method="POST")
def api_city_refresh():
	try:
		snapshot = refresh_city_cache(DATA_DIR / "cities.cache.json")
		return _json_response({
			"ok": True,
			"source": "cache",
			"count": len(snapshot.get("cities", [])),
			"updated_at": snapshot.get("fetched_at"),
			"cities": snapshot.get("cities", []),
		})
	except CityRefreshError as exc:
		return _json_response({
			"ok": False,
			"source": "local",
			"using_local_data": True,
			"error": str(exc),
		}, 502)


@app.route("/api/jobs/export", method="POST")
def api_jobs_export():
	db = _get_web_db()
	try:
		body = request.json or {}
		if not isinstance(body, dict):
			return _json_response({"error": "请求体必须是对象"}, 400)
		format_value = body.get("format", "xlsx")
		scope = body.get("scope", "all")
		job_ids = body.get("job_ids", [])
		filters = body.get("filters", {})
		if not isinstance(format_value, str) or format_value.strip().lower() not in {"xlsx", "csv"}:
			return _json_response({"error": "导出格式只支持 xlsx 或 csv"}, 400)
		if not isinstance(scope, str) or scope.strip().lower() not in {"all", "filtered", "selected"}:
			return _json_response({"error": "导出范围无效"}, 400)
		if not isinstance(job_ids, list):
			return _json_response({"error": "岗位 ID 必须是数组"}, 400)
		if not isinstance(filters, dict):
			return _json_response({"error": "筛选条件必须是对象"}, 400)
		content, content_type, filename = export_jobs(
			db,
			format=format_value,
			scope=scope,
			job_ids=job_ids,
			filters=filters,
		)
		# The export builder reads the same database rows and this count is the
		# exact count of rows written, not a front-end page estimate.
		exported_count = export_row_count(db, scope=scope, job_ids=job_ids, filters=filters)
		response.content_type = content_type
		response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
		response.headers["Content-Length"] = str(len(content))
		response.headers["X-Exported-Count"] = str(exported_count)
		return content
	except InvalidJobSelectionError as exc:
		return _json_response({"error": str(exc), "code": "invalid_job_ids", "invalid_ids": exc.invalid_ids}, 400)
	except ValueError as exc:
		return _json_response({"error": str(exc)}, 400)
	except Exception:
		return _json_response({"error": "岗位导出失败"}, 500)
	finally:
		db.close()


def _job_action_payload():
	body = request.json or {}
	if not isinstance(body, dict):
		raise ValueError("请求体必须是对象")
	job_ids = body.get("job_ids")
	if not isinstance(job_ids, list):
		raise ValueError("岗位 ID 必须是数组")
	return body, job_ids


def _job_action_error(exc: ValueError):
	payload = {"error": str(exc), "code": getattr(exc, "code", "invalid_request")}
	if isinstance(exc, JobDeletionConflictError):
		payload["blocked"] = exc.blocked
		payload["not_found"] = exc.not_found
	return _json_response(payload, 409 if isinstance(exc, JobDeletionConflictError) else 400)


@app.route("/api/jobs/soft-delete", method="POST")
def api_jobs_soft_delete():
	db = _get_web_db()
	try:
		body, job_ids = _job_action_payload()
		result = soft_delete_jobs(
			db,
			job_ids,
			confirmed=body.get("confirmed") is True,
			reason=str(body.get("reason") or "用户移入回收站"),
		)
		return _json_response(result)
	except (ValueError, JobDeletionConflictError) as exc:
		return _job_action_error(exc)
	finally:
		db.close()


@app.route("/api/jobs/restore", method="POST")
def api_jobs_restore():
	db = _get_web_db()
	try:
		body, job_ids = _job_action_payload()
		result = restore_jobs(db, job_ids, confirmed=body.get("confirmed") is True)
		return _json_response(result)
	except (ValueError, JobDeletionConflictError) as exc:
		return _job_action_error(exc)
	finally:
		db.close()


@app.route("/api/jobs/permanent-delete", method="POST")
def api_jobs_permanent_delete():
	db = _get_web_db()
	try:
		body, job_ids = _job_action_payload()
		result = permanent_delete_jobs(
			db,
			job_ids,
			confirmed=body.get("confirmed") is True,
			confirmation=body.get("confirmation", ""),
		)
		return _json_response(result)
	except (ValueError, JobDeletionConflictError) as exc:
		return _job_action_error(exc)
	finally:
		db.close()


# ─── Resume APIs ─────────────────────────────────────────

@app.route("/api/resume")
def api_resume_get():
	try:
		config = load_config(CONFIG_PATH)
		resume_path = config.get("profile", {}).get("resume_path", "")
		if resume_path and Path(resume_path).exists():
			p = Path(resume_path)
			stat = p.stat()
			return _json_response({
				"filename": p.name,
				"size": stat.st_size,
				"uploaded_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
				"path": str(p)
			})
		return _json_response(None)
	except Exception as e:
		return _json_response({"error": str(e)}, 500)


@app.route("/api/resume/upload", method="POST")
def api_resume_upload():
	try:
		import yaml
		upload = request.files.get("file")
		if not upload:
			return _json_response({"error": "No file uploaded"}, 400)

		# Validate size (10MB max)
		content = upload.file.read()
		if len(content) > 10 * 1024 * 1024:
			return _json_response({"error": "文件大小超过 10MB 限制"}, 400)

		# Bottle's normalized `filename` strips non-ASCII characters. Use the
		# raw browser filename and apply our own Unicode-safe sanitization.
		raw_name = upload.raw_filename or upload.filename
		safe_name, stored_content = prepare_resume_content(raw_name, content)
		RESUME_DIR.mkdir(parents=True, exist_ok=True)
		dest = RESUME_DIR / safe_name
		dest.write_bytes(stored_content)

		# Update config
		config = load_config(CONFIG_PATH)
		config.setdefault("profile", {})["resume_path"] = str(dest)
		# A single uploaded resume immediately becomes the primary document of the
		# default local profile. Facts remain optional and never block preflight.
		db = _get_web_db()
		try:
			ensure_profile_for_resume(db, dest)
		finally:
			db.close()
		with open(CONFIG_PATH, "w", encoding="utf-8") as f:
			yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

		return _json_response({
			"success": True,
			"filename": safe_name,
			"size": len(stored_content),
			"path": str(dest),
			"profile_id": "default",
		})
	except ResumeUploadError as e:
		return _json_response({"error": str(e)}, 400)
	except Exception as e:
		return _json_response({"error": str(e)}, 500)


@app.route("/api/resume", method="DELETE")
def api_resume_delete():
	try:
		import yaml
		config = load_config(CONFIG_PATH)

		# Never delete the master resume from disk; only detach it from config.
		config.setdefault("profile", {})["resume_path"] = ""
		with open(CONFIG_PATH, "w", encoding="utf-8") as f:
			yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

		return _json_response({"success": True})
	except Exception as e:
		return _json_response({"error": str(e)}, 500)


# ─── Static Files + SPA Fallback ─────────────────────────

_STATIC_MIME_TYPES = {
	".css": "text/css; charset=utf-8",
	".cjs": "text/javascript; charset=utf-8",
	".html": "text/html; charset=utf-8",
	".js": "application/javascript; charset=utf-8",
	".json": "application/json; charset=utf-8",
	".mjs": "application/javascript; charset=utf-8",
	".svg": "image/svg+xml",
}


def _serve_static(filename: str, root: Path):
	"""Serve static assets with stable MIME types while retaining range/cache support."""
	mimetype = _STATIC_MIME_TYPES.get(Path(filename).suffix.lower(), "auto")
	# Reading the small bundled SPA assets inside a context manager avoids
	# leaving Bottle's file wrapper open on Windows after a test/client consumes
	# the response. Missing paths retain Bottle's normal 404 behavior.
	path = (root / filename).resolve()
	try:
		path.relative_to(root.resolve())
	except ValueError:
		return _json_response({"error": "Not found"}, 404)
	if path.is_file():
		if mimetype != "auto":
			response.content_type = mimetype
		response.headers["Content-Length"] = str(path.stat().st_size)
		return path.read_bytes()
	return static_file(filename, root=str(root), mimetype=mimetype)


@app.route("/assets/<filepath:path>")
def serve_assets(filepath):
	return _serve_static(filepath, FRONTEND_DIR / "assets")


@app.route("/")
@app.route("/<filepath:path>")
def serve_spa(filepath="index.html"):
	if str(filepath).startswith("api/"):
		return _json_response({"error": "Not found"}, 404)

	# Try serving the exact file first
	file_path = FRONTEND_DIR / filepath
	if file_path.is_file():
		return _serve_static(filepath, FRONTEND_DIR)
	# SPA fallback: return index.html for all non-API routes
	return _serve_static("index.html", FRONTEND_DIR)


# ─── Error Handlers ──────────────────────────────────────

@app.error(404)
def error404(error):
	if request.path.startswith("/api/"):
		response.content_type = "application/json; charset=utf-8"
		return json.dumps({"error": "Not found"}, ensure_ascii=False)
	# SPA fallback for non-API 404s
	return _serve_static("index.html", FRONTEND_DIR)


@app.error(500)
def error500(error):
	response.content_type = "application/json; charset=utf-8"
	return json.dumps({"error": str(error.body)}, ensure_ascii=False)


# ─── Run ─────────────────────────────────────────────────

def run_server(host: str = "127.0.0.1", port: int = 8686, open_browser: bool = True):
	"""Start the web server."""
	if open_browser:
		import webbrowser
		import threading
		def _open():
			time.sleep(1)
			webbrowser.open(f"http://{host}:{port}")
		threading.Thread(target=_open, daemon=True).start()

	app.run(host=host, port=port, quiet=False, reloader=False)
