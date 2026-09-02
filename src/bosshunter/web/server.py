"""BossHunter Web Server - Bottle HTTP service + API routes.

Serves:
- /api/* → JSON data endpoints
- /* → Frontend static files (dist/)
"""

import json
import math
import mimetypes
import random
import time
from copy import deepcopy
from pathlib import Path
from socketserver import ThreadingMixIn
from threading import Event, Lock
from uuid import uuid4
from wsgiref.simple_server import WSGIServer

import yaml
from bottle import Bottle, request, response, static_file, abort

from bosshunter import __version__
from bosshunter.ai.credentials import get_ai_api_key
from bosshunter.cities import CityRefreshError, get_city_map, load_city_snapshot, refresh_city_cache
from bosshunter.config import AI_SERVICE_PRESETS, load_config, remove_retired_collection_settings, save_config
from bosshunter.db import (
	JobDeletionConflictError,
	JobManualSentConflictError,
	add_history,
	count_unresolved_monitor_items,
	get_active_platform_safety_lock,
	get_daily_activity,
	get_db,
	get_funnel_stats,
	get_jobs_needing_resume,
	get_jobs_pending_confirmation,
	get_jobs_ready_to_send,
	get_jobs_with_send_errors,
	get_recent_history,
	get_recent_monitor_replies,
	get_unresolved_reply_pending,
	get_unresolved_resume_failures,
	get_stats,
	get_top_companies,
	mark_external_jobs_sent,
	permanent_delete_jobs,
	query_jobs,
	restore_jobs,
	soft_delete_jobs,
	update_job_status,
)
from bosshunter.collection.capabilities import platform_supports
from bosshunter.collection.orchestrator import CollectionOrchestrator, normalize_collection_options
from bosshunter.collection.platforms.zhilian import load_zhilian_city_snapshot
from bosshunter.collection.platforms.job51 import load_51job_city_snapshot
from bosshunter.collection_run_store import (
	get_collection_run,
	list_collection_runs,
	mark_orphaned_collection_runs_stopped,
)
from bosshunter.job_filters import parse_monthly_salary_k
from bosshunter.job_export import InvalidJobSelectionError, export_jobs, export_row_count
from bosshunter.scoring_run_store import (
	create_scoring_run,
	get_scoring_run,
	list_scoring_runs,
	mark_orphaned_scoring_runs_paused,
	update_scoring_run,
)
from bosshunter.scoring_selection import preview_scoring, select_scoring_jobs, validate_options
from bosshunter.web.preflight import check_ai_connection, collect_preflight_checks, error_messages
from bosshunter.web.resume_upload import ResumeUploadError, prepare_resume_content
from bosshunter.web.city_lookup import CityLookupError, lookup_city
from bosshunter.web.tasks import (
	TaskAlreadyRunningError,
	WorkbenchTask,
	WorkbenchTaskRunner,
	wait_for_initial_monitor_cooldown,
)

mimetypes.add_type("application/javascript", ".js", strict=True)
mimetypes.add_type("application/javascript", ".mjs", strict=True)
mimetypes.add_type("text/javascript", ".cjs", strict=True)
mimetypes.add_type("text/css", ".css", strict=True)

app = Bottle()
task_runner = WorkbenchTaskRunner()
job_mutation_lock = Lock()


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
	"""Handle Chrome preconnect sockets without blocking other requests."""

	daemon_threads = True
	allow_reuse_address = True

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


def set_base_dir(base_dir: Path | str) -> None:
	"""Set the runtime directory used for config.yaml, data, and uploads."""
	global BASE_DIR, DATA_DIR, RESUME_DIR, CONFIG_PATH
	BASE_DIR = Path(base_dir).resolve()
	DATA_DIR = BASE_DIR / "data"
	RESUME_DIR = DATA_DIR / "resumes"
	CONFIG_PATH = BASE_DIR / "config.yaml"
	mark_orphaned_scoring_runs_paused(DATA_DIR / "bosshunter.db")
	mark_orphaned_collection_runs_stopped(DATA_DIR / "bosshunter.db")


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


def _config_download_payload(config: dict) -> str:
	"""Serialize a shareable config backup without credentials."""
	redacted = _redact_config_for_response(config)
	ai_cfg = redacted.get("ai")
	if isinstance(ai_cfg, dict):
		ai_cfg.pop("api_key_masked", None)
		ai_cfg.pop("auth_token_masked", None)
	return yaml.dump(redacted, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _write_config(config: dict) -> None:
	"""Persist public settings separately from local AI credentials."""
	save_config(config, CONFIG_PATH)


def _sanitize_config_for_write(data):
	"""Remove browser-only fields and preserve existing secrets on blank posts."""
	cleaned = remove_retired_collection_settings(deepcopy(data))
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


def _preflight_messages(mode: str, config: dict, options: dict | None = None) -> list[str]:
	"""Return user-actionable blockers before starting a dashboard task."""
	messages: list[str] = []
	if mode not in {"full", "collect", "rescore", "monitor"}:
		messages.append(f"不支持的任务模式：{mode}")
	if mode == "collect":
		try:
			collection_options = normalize_collection_options(config, options)
		except ValueError as exc:
			messages.append(str(exc))
			return messages
		if collection_options.get("auto_score"):
			resume_path = config.get("profile", {}).get("resume_path", "")
			if not resume_path or not Path(str(resume_path)).exists():
				messages.append("自动评分前请先在配置页上传 .md、.docx 或 .pdf 简历。")
			if not get_ai_api_key(config):
				messages.append("选择自动评分后，请先配置当前 AI 服务的 API Key。")
			return messages

	profile = config.get("profile", {})
	resume_path = profile.get("resume_path", "")
	if mode in {"full", "rescore"} and (not resume_path or not Path(str(resume_path)).exists()):
		messages.append("请先在配置页上传 .md、.docx 或 .pdf 简历。")

	if mode == "full":
		try:
			full_options = normalize_collection_options(config, options)
		except ValueError as exc:
			messages.append(str(exc))
		else:
			if not full_options.get("platform_order"):
				messages.append("运行全流程至少需要选择一个采集平台。")

	if mode in {"full", "rescore"} and not get_ai_api_key(config):
		messages.append("请先在配置页填写当前 AI 服务的 API Key，或设置对应的标准环境变量。")

	return messages


def _task_config(extra: dict | None = None) -> dict:
	config = load_config(CONFIG_PATH)
	if extra:
		config.update(extra)
	return config


def _log(task: WorkbenchTask, message: str) -> None:
	task.logs.append(message)


def _record_collect_progress(task: WorkbenchTask, state: dict) -> None:
	task.metrics.update({
		"collect_seen": int(state.get("seen") or 0),
		"collect_new": int(state.get("new") or 0),
		"collect_duplicate": int(state.get("duplicate") or 0),
		"collect_filtered": int(state.get("filtered") or 0),
		"collect_parse_failed": int(state.get("parse_failed") or 0),
		"collect_save_failed": int(state.get("save_failed") or 0),
		"collect_search_pages": int(state.get("search_pages") or 0),
	})
	if isinstance(state.get("progress"), dict):
		task.progress = deepcopy(state["progress"])


def _record_score_progress(task: WorkbenchTask, state: dict) -> None:
	task.metrics.update({
		"ai_completed": int(state.get("completed") or 0),
		"ai_total": int(state.get("total") or 0),
		"ai_passed": int(state.get("scored") or 0),
		"ai_filtered": int(state.get("filtered") or 0),
		"ai_failed": int(state.get("failed") or 0),
	})
	_log(
		task,
		f"AI 评分进度 {state['completed']}/{state['total']}：通过 {state['scored']}，过滤 {state['filtered']}，失败 {state['failed']}",
	)


def _execute_collect(task: WorkbenchTask, config: dict) -> None:
	_log(task, "开始采集岗位")
	collect_config = dict(config)
	collect_config["_workbench_stop_event"] = task.stop_requested
	collect_config["_workbench_collect_progress"] = lambda state: _record_collect_progress(task, state)
	if "_collection_options" not in config:
		# Preserve the old private executor seam used by legacy callers. New Web
		# collection tasks always inject normalized options before starting.
		from bosshunter.ai.scorer import score_jobs
		from bosshunter.scraper.jobs import scrape_jobs
		keywords = config.get("search", {}).get("keywords", [])
		collected_job_ids: list[str] = []
		scrape_jobs(collect_config, keywords, collected_job_ids=collected_job_ids)
		collect_report = collect_config.get("_workbench_collect_report", {})
		_stop_or_log_boss_collection_reason(task, str(collect_report.get("stop_reason") or ""))
		if task.stop_requested.is_set():
			return
		task.context["boss_collection_completed_monotonic"] = time.monotonic()
		_log(task, f"本轮采集完成：扫描 {task.metrics.get('collect_seen', 0)}，新增 {task.metrics.get('collect_new', 0)}，重复 {task.metrics.get('collect_duplicate', 0)}")
		_log(task, f"开始 AI 评分：处理全部未评分岗位（本轮新增 {len(collected_job_ids)} 个）")
		score_config = dict(config)
		score_config["_workbench_stop_event"] = task.stop_requested
		score_config["_workbench_log"] = lambda message: _log(task, message)
		score_config["_workbench_score_progress"] = lambda state: _record_score_progress(task, state)
		score_jobs(score_config)
		return

	result = CollectionOrchestrator(
		collect_config,
		db_path=DATA_DIR / "bosshunter.db",
		task_id=task.id,
	).run(config.get("_collection_options"))
	task.progress = {
		"run_id": result.get("run_id", ""),
		"outcome": result.get("status", "completed"),
		"platforms": result.get("platforms", {}),
		"collected_job_ids": result.get("collected_job_ids", []),
	}
	boss_state = result.get("platforms", {}).get("boss", {})
	if isinstance(boss_state, dict):
		_stop_or_log_boss_collection_reason(task, str(boss_state.get("reason_code") or ""))
		if boss_state.get("status") not in {None, "queued"}:
			task.context["boss_collection_completed_monotonic"] = time.monotonic()
	_log(task, f"本轮采集完成：新增 {len(result.get('collected_job_ids', []))}，状态 {result.get('status', 'completed')}")
	if task.stop_requested.is_set():
		return


def _stop_or_log_boss_collection_reason(task: WorkbenchTask, stop_reason: str) -> None:
	limit_labels = {
		"daily_search_page_limit": "BOSS 单日搜索页上限",
		"daily_detail_page_limit": "BOSS 单日详情页上限",
		"daily_platform_page_limit": "BOSS 单日页面访问总上限",
		"persistent_risk_lock": "BOSS 风险冷却锁",
	}
	risk_labels = {
		"captcha": "BOSS 验证码",
		"blocked": "BOSS 账号或请求拦截",
		"rate_limit": "BOSS 频率限制",
		"login_required": "BOSS 登录状态失效",
		"consecutive_page_failures": "BOSS 连续页面失败",
	}
	if stop_reason in limit_labels:
		_log(task, f"为了账户安全，已达到{limit_labels[stop_reason]}，仅停止 BOSS 访问；智联和 51job 不占用该额度")
	elif stop_reason in risk_labels:
		reason = f"为了账户安全，检测到{risk_labels[stop_reason]}，已立即停止并进入安全冷却"
		task.stop_reason = reason
		task.stop_requested.set()
		_log(task, reason)


def _execute_rescore(task: WorkbenchTask, config: dict) -> None:
	from bosshunter.ai.scorer import score_jobs

	score_config = dict(config)
	score_config["_workbench_stop_event"] = task.stop_requested
	score_config["_workbench_log"] = lambda message: _log(task, message)
	score_config["_workbench_score_progress"] = lambda state: _record_score_progress(task, state)
	_log(task, "开始重新评分")
	score_jobs(score_config, rescore_filtered=True)


def _execute_score(task: WorkbenchTask, config: dict) -> None:
	from bosshunter.ai.scorer import score_jobs

	run_id = str(config.get("_score_run_id") or "")
	options = config.get("_score_options", {}) if isinstance(config.get("_score_options"), dict) else {}
	db_path = DATA_DIR / "bosshunter.db"

	def checkpoint(state: dict) -> None:
		remaining = [str(job_id) for job_id in state.get("remaining_job_ids", []) if str(job_id)]
		status = str(state.get("status") or "running")
		pause_reason = str(state.get("pause_reason") or "") if status == "paused" else None
		# AI 失败暂停时同步写入 error 列与 task.error，前端任务列表才能看到真实原因（issue #100）。
		error = str(state.get("error") or "") if status == "paused" else None
		update_scoring_run(
			db_path,
			run_id,
			status=status,
			remaining_job_ids=remaining,
			progress={**task.metrics, "remaining": len(remaining)},
			pause_reason=pause_reason,
			error=error or None,
		)
		if status == "paused":
			task.stop_reason = str(state.get("pause_reason") or "评分任务已暂停")
			if error:
				task.error = error
			task.stop_requested.set()

	score_config = dict(config)
	score_config["_workbench_stop_event"] = task.stop_requested
	score_config["_workbench_log"] = lambda message: _log(task, message)
	score_config["_workbench_score_progress"] = lambda state: _record_score_progress(task, state)
	score_config["_workbench_score_checkpoint"] = checkpoint
	_log(task, f"开始单独 AI 评分：{len(options.get('job_ids', []))} 个岗位")
	try:
		score_jobs(
			score_config,
			scope="selected",
			limit=None,
			job_ids=list(options.get("job_ids", [])),
			force_rescore=bool(options.get("force_rescore")),
		)
	except Exception as exc:
		update_scoring_run(db_path, run_id, status="failed", error=str(exc)[:1000])
		raise


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


def _execute_monitor(task: WorkbenchTask, config: dict, *, initial_cooldown: bool = False) -> None:
	from bosshunter.executor.monitor import (
		get_effective_monitor_interval_minutes,
		monitor_and_send_resumes,
	)
	if _stop_for_active_platform_lock(task):
		return

	monitor_config = dict(config)
	monitor_config["_workbench_stop_event"] = task.stop_requested
	monitor_config["_monitor_reuse_chat_tab"] = True
	monitor_config["_monitor_runtime_state"] = {}
	interval_min = get_effective_monitor_interval_minutes(config)
	interval_sec = max(interval_min * 60, 1)
	queue_lock = task.context.setdefault("monitor_queue_lock", Lock())
	wakeup_event = task.context.setdefault("monitor_wakeup_event", Event())
	task.context["monitoring"] = True
	try:
		if initial_cooldown and wait_for_initial_monitor_cooldown(task, config, _log):
			return
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
			summary = monitor_and_send_resumes(monitor_config)
			if task.stop_requested.is_set():
				return
			stop_reason = summary.get("stop_reason")
			if stop_reason:
				reason_labels = {
					"captcha": "验证码",
					"rate_limit": "频率限制",
					"blocked": "账号或请求被拦截",
					"consecutive_page_failures": "连续页面失败",
					"daily_platform_page_limit": "单日平台页面访问上限",
					"persistent_risk_lock": "平台安全锁冷却",
				}
				reason = f"监测已安全停止：检测到{reason_labels.get(stop_reason, '风险信号')}"
				task.stop_reason = reason
				task.stop_requested.set()
				_log(task, reason)
				return
			_log(task, f"本轮监测完成，{interval_min:g} 分钟后再次检查")
			wakeup_event.wait(interval_sec)
			wakeup_event.clear()
	finally:
		from bosshunter.executor.monitor import close_monitor_chat_target
		close_monitor_chat_target(monitor_config)
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
		deferred_config["_workbench_job_ids"] = deferred_job_ids
		deferred_config["_workbench_skip_greeting"] = True
		_execute_deliver(task, deferred_config)
		if task.stop_requested.is_set():
			return

	full_collection_config = dict(config)
	try:
		configured_options = full_collection_config.get("_collection_options")
		if not isinstance(configured_options, dict):
			configured_options = normalize_collection_options(full_collection_config, None)
		full_collection_config["_collection_options"] = {
			**configured_options,
			"auto_score": True,
		}
	except ValueError as exc:
		if "不支持已启用的智联招聘" in str(exc):
			raise
		# Keep the legacy executor path available for callers/tests that supply
		# an intentionally minimal config and replace collection externally.
		full_collection_config.pop("_collection_options", None)
	_execute_collect(task, full_collection_config)
	if task.stop_requested.is_set():
		return

	db = _get_web_db()
	try:
		threshold = int(config.get("scoring", {}).get("threshold", 60) or 60)
		pending_confirmation = [
			job for job in get_jobs_pending_confirmation(db)
			if int(job.get("score") or 0) >= threshold
		]
	finally:
		db.close()
	if not pending_confirmation:
		task.context["waiting_confirmation"] = False
		task.context["confirmation_complete"] = True
		_log(task, "没有待确认岗位，流程结束")
		return

	confirmation_event = Event()
	task.context["confirmation_event"] = confirmation_event
	task.context["waiting_confirmation"] = True
	if task.context.get("delivery_requested"):
		confirmation_event.set()
	_log(task, "等待前端确认投递")
	while not task.stop_requested.is_set() and not confirmation_event.wait(0.5):
		pass
	if task.stop_requested.is_set():
		return

	job_ids = [str(job_id) for job_id in task.context.get("confirmed_job_ids", []) if str(job_id)]
	task.context["waiting_confirmation"] = False
	task.context["confirmation_complete"] = True
	task.context["delivery_requested"] = False
	if not job_ids:
		_log(task, "未收到前端确认岗位，流程结束")
		return

	_log(task, f"前端已确认 {len(job_ids)} 个岗位，继续投递")
	if _wait_for_collection_delivery_cooldown(task, config):
		return
	# The user may adjust the daily limit or other send settings while reviewing
	# jobs. Reload immediately before delivery instead of using the task-start snapshot.
	deliver_config = load_config(CONFIG_PATH)
	deliver_config["_workbench_job_ids"] = job_ids
	_execute_deliver(task, deliver_config)
	if task.stop_requested.is_set():
		return
	_execute_monitor(task, load_config(CONFIG_PATH), initial_cooldown=True)


def _queue_active_delivery(
	task: WorkbenchTask,
	job_ids: list[str],
	*,
	direct_send: bool,
) -> tuple[dict, list[str]]:
	"""Append jobs to the currently running single delivery worker."""
	queue_lock = task.context.setdefault("delivery_queue_lock", Lock())
	with queue_lock:
		if not task.context.get("delivering"):
			raise TaskAlreadyRunningError("当前发送任务即将结束，请稍后重试")
		scheduled_ids = task.context.setdefault("delivery_scheduled_ids", set())
		new_ids = [job_id for job_id in job_ids if job_id not in scheduled_ids]
		already_queued_count = len(job_ids) - len(new_ids)
		if new_ids:
			task.context.setdefault("pending_deliveries", []).append({
				"job_ids": new_ids,
				"direct_send": direct_send,
			})
			scheduled_ids.update(new_ids)
			_log(task, f"新增 {len(new_ids)} 个岗位，已加入当前发送队列")
		elif already_queued_count:
			_log(task, f"所选 {already_queued_count} 个岗位已在当前发送队列中")
	payload = task.snapshot()
	payload["queued_count"] = len(new_ids)
	payload["already_queued_count"] = already_queued_count
	return payload, new_ids


def _take_active_delivery(task: WorkbenchTask) -> dict | None:
	queue_lock = task.context.get("delivery_queue_lock")
	if queue_lock is None:
		return None
	with queue_lock:
		pending = task.context.setdefault("pending_deliveries", [])
		if pending:
			return pending.pop(0)
		task.context["delivering"] = False
		return None


def _execute_deliver(task: WorkbenchTask, config: dict) -> None:
	"""Run one delivery worker and drain batches queued while it is active."""
	if _stop_for_active_platform_lock(task):
		return
	queue_lock = task.context.setdefault("delivery_queue_lock", Lock())
	with queue_lock:
		task.context["delivering"] = True
		task.context.setdefault("pending_deliveries", [])
		task.context.setdefault("delivery_scheduled_ids", set()).update(
			str(job_id) for job_id in config.get("_workbench_job_ids", []) if str(job_id)
		)

	current_config = config
	try:
		while not task.stop_requested.is_set():
			_execute_deliver_batch(task, current_config)
			if task.stop_requested.is_set():
				return
			batch = _take_active_delivery(task)
			if not batch:
				return
			current_config = dict(config)
			current_config["_workbench_job_ids"] = batch.get("job_ids", [])
			if batch.get("direct_send"):
				current_config["_workbench_skip_greeting"] = True
			else:
				current_config.pop("_workbench_skip_greeting", None)
			_log(task, f"继续处理队列中的 {len(current_config['_workbench_job_ids'])} 个岗位")
	finally:
		with queue_lock:
			task.context["delivering"] = False
			task.context.pop("delivery_scheduled_ids", None)
			task.context.pop("pending_deliveries", None)


def _stop_for_active_platform_lock(task: WorkbenchTask) -> bool:
	db = _get_web_db()
	try:
		lock = get_active_platform_safety_lock(db)
	finally:
		db.close()
	if not lock:
		return False
	reason = "为了账户安全，平台风险冷却尚未结束，已停止本次平台访问"
	task.stop_reason = reason
	task.stop_requested.set()
	_log(task, reason)
	return True


def _wait_for_collection_delivery_cooldown(task: WorkbenchTask, config: dict) -> bool:
	completed_at = task.context.get("boss_collection_completed_monotonic")
	if not isinstance(completed_at, (int, float)):
		return False
	collection_config = config.get("collection", {})
	selected_minutes = task.context.get("boss_delivery_cooldown_minutes")
	if not isinstance(selected_minutes, (int, float)):
		if (
			"delivery_cooldown_min_minutes" in collection_config
			or "delivery_cooldown_max_minutes" in collection_config
		):
			try:
				minimum = max(float(collection_config.get("delivery_cooldown_min_minutes", 5)), 0)
			except (TypeError, ValueError):
				minimum = 5
			try:
				maximum = max(float(collection_config.get("delivery_cooldown_max_minutes", 15)), 0)
			except (TypeError, ValueError):
				maximum = 15
			minimum, maximum = sorted((minimum, maximum))
			selected_minutes = random.uniform(minimum, maximum)
		else:
			# Compatibility with configurations saved before random cooldown ranges.
			try:
				selected_minutes = max(float(collection_config.get("delivery_cooldown_minutes", 10)), 0)
			except (TypeError, ValueError):
				selected_minutes = 10
		task.context["boss_delivery_cooldown_minutes"] = selected_minutes
	cooldown_seconds = float(selected_minutes) * 60
	remaining = max(cooldown_seconds - (time.monotonic() - completed_at), 0)
	if remaining <= 0:
		return False
	_log(task, f"为了账户安全，BOSS 采集结束后冷却 {remaining / 60:.1f} 分钟再开始 BOSS 投递")
	if task.stop_requested.wait(remaining):
		_log(task, "采集到投递的安全冷却已取消")
		return True
	return False


def _execute_deliver_batch(task: WorkbenchTask, config: dict) -> None:
	from bosshunter.ai.greeter import generate_greetings
	from bosshunter.executor.sender import send_greetings

	config = dict(config)
	config["_workbench_stop_event"] = task.stop_requested
	config["_workbench_log"] = lambda message: _log(task, message)
	selected_job_ids = [str(job_id) for job_id in config.get("_workbench_job_ids", []) if str(job_id)]
	if not config.get("_workbench_skip_greeting"):
		_log(task, "生成招呼语")
		generated_count = generate_greetings(config)
		greeting_report = config.get("_workbench_greeting_report", {})
		skipped_existing = int(greeting_report.get("skipped_existing", 0) or 0)
		ready_count = generated_count + skipped_existing
		_log(task, f"招呼语准备完成：{ready_count}/{len(selected_job_ids) or ready_count}（新生成 {generated_count}）")
		if task.stop_requested.is_set():
			return
		if selected_job_ids and ready_count < len(selected_job_ids):
			missing_count = len(selected_job_ids) - ready_count
			# 生成失败的岗位保留为待生成且无招呼语文本，本就不会进入发送；其余岗位继续走
			# 现有人工确认、发送窗口与风控规则（#101 回归：不再因部分失败放弃整个批次）。
			_log(
				task,
				f"{missing_count} 个岗位未生成招呼语，已保留为待生成，请在 BOSS 中手动填写；"
				"其余岗位继续进入发送流程。",
			)
	_log(task, "发送招呼语")
	# The workbench must obey the same send window and day-off guard as the CLI.
	# ``force`` remains an explicit CLI-only override and is never implied by a
	# browser button click.
	sent_count = send_greetings(config, force=False)
	report = config.get("_workbench_send_report", {})
	failed_count = int(report.get("failed_count", 0) or 0)
	deferred_count = int(report.get("deferred_count", 0) or 0)
	quota_deferred_count = min(
		int(report.get("quota_deferred_count", 0) or 0),
		deferred_count,
	)
	paused_count = max(deferred_count - quota_deferred_count, 0)
	task.metrics.update({
		"send_requested": int(report.get("requested_count", len(selected_job_ids)) or 0),
		"send_success": int(report.get("sent_count", sent_count) or 0),
		"send_failed": failed_count,
		"send_deferred": deferred_count,
		"send_quota_deferred": quota_deferred_count,
		"send_already_today": int(report.get("already_sent", 0) or 0),
		"send_daily_limit": int(report.get("daily_limit", 0) or 0),
		"send_remaining_quota": int(report.get("remaining_quota", 0) or 0),
	})
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
	if stop_reason:
		task.stop_reason = str(stop_reason)
	if stop_reason in {"captcha", "rate_limit", "blocked", "consecutive_errors"}:
		reason_labels = {
			"captcha": "验证码",
			"rate_limit": "频率限制",
			"blocked": "账号或请求被拦截",
			"consecutive_errors": "连续错误过多",
		}
		raise RuntimeError(f"发送已安全暂停：检测到{reason_labels[stop_reason]}")


task_runner._executors.update({
	"full": _execute_full,
	"collect": _execute_collect,
	"rescore": _execute_rescore,
	"score": _execute_score,
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
	try:
		deleted = request.params.get("deleted", "active").strip()
		limit = int(request.params.get("limit", 100))
		offset = int(request.params.get("offset", 0))
		if deleted not in {"active", "only", "all"} or not 1 <= limit <= 500 or offset < 0:
			raise ValueError("岗位查询参数无效")
	except (TypeError, ValueError) as exc:
		return _json_response({"error": str(exc)}, 400)

	db = _get_web_db()
	try:
		jobs, total = query_jobs(db, deleted=deleted, limit=limit, offset=offset)
		response.headers["X-Total-Count"] = str(total)
		return _json_response(jobs)
	finally:
		db.close()


def _optional_float_param(name: str, *, minimum: float = 0, maximum: float | None = None):
	raw_value = request.params.get(name)
	if raw_value in (None, ""):
		return None
	try:
		value = float(raw_value)
	except (TypeError, ValueError) as exc:
		raise ValueError(f"{name} 必须是数字") from exc
	if not math.isfinite(value):
		raise ValueError(f"{name} 必须是有限数字")
	if value < minimum or (maximum is not None and value > maximum):
		raise ValueError(f"{name} 超出允许范围")
	return value


def _integer_param(name: str, default: int, *, minimum: int, maximum: int | None = None):
	raw_value = request.params.get(name, str(default))
	try:
		value = int(raw_value)
	except (TypeError, ValueError) as exc:
		raise ValueError(f"{name} 必须是整数") from exc
	if value < minimum or (maximum is not None and value > maximum):
		raise ValueError(f"{name} 超出允许范围")
	return value


@app.route("/api/jobs/search")
def api_job_search():
	try:
		minimum_score = _optional_float_param("min_score", maximum=100)
		salary_min = _optional_float_param("salary_min")
		salary_max = _optional_float_param("salary_max")
		limit = _integer_param("limit", 15, minimum=1, maximum=100)
		offset = _integer_param("offset", 0, minimum=0)
		if salary_min is not None and salary_max is not None and salary_min > salary_max:
			raise ValueError("最低薪资不能高于最高薪资")
		created_within = request.params.get("created_within", "").strip()
		if created_within and created_within not in {"today", "3d", "7d"}:
			raise ValueError("created_within 参数无效")
		recruitment_type = request.params.get("recruitment_type", "").strip()
		if recruitment_type and recruitment_type not in {"campus", "experienced", "unknown"}:
			raise ValueError("recruitment_type 参数无效")
		education_filter = (request.query.getunicode("education") or "").strip()
		if education_filter and education_filter not in {"博士", "硕士", "本科", "大专", "不限", "其他", "unknown"}:
			raise ValueError("education 参数无效")
		sort_by = request.params.get("sort_by", "created_at").strip()
		if sort_by not in {"salary", "education", "score", "status", "hr_active", "created_at"}:
			raise ValueError("sort_by 参数无效")
		sort_order = request.params.get("sort_order", "desc").strip().lower()
		if sort_order not in {"asc", "desc"}:
			raise ValueError("sort_order 参数无效")
	except ValueError as exc:
		return _json_response({"error": str(exc)}, 400)

	query = "SELECT * FROM jobs"
	conditions = ["deleted_at IS NULL"]
	params = []
	keyword = (request.query.getunicode("q") or "").strip()
	status_filter = request.params.get("status", "").strip()
	if keyword:
		conditions.append("(title LIKE ? OR company LIKE ? OR jd LIKE ? OR score_reason LIKE ?)")
		keyword_param = f"%{keyword}%"
		params.extend([keyword_param] * 4)
	if minimum_score is not None:
		conditions.append("score >= ?")
		params.append(minimum_score)
	if status_filter:
		conditions.append("status = ?")
		params.append(status_filter)
	source_platform = request.params.get("source_platform", "").strip()
	if source_platform:
		if source_platform not in {"boss", "zhilian", "51job"}:
			return _json_response({"error": "source_platform 参数无效"}, 400)
		conditions.append("COALESCE(source_platform, 'boss') = ?")
		params.append(source_platform)
	if recruitment_type:
		conditions.append("COALESCE(recruitment_type, 'unknown') = ?")
		params.append(recruitment_type)
	if education_filter:
		if education_filter == "unknown":
			conditions.append("COALESCE(TRIM(education), '') = ''")
		else:
			conditions.append("education LIKE ?")
			params.append(f"%{education_filter}%")
	if created_within == "today":
		conditions.append("created_at >= datetime('now', 'localtime', 'start of day', 'utc')")
	elif created_within == "3d":
		conditions.append("created_at >= datetime('now', '-3 days')")
	elif created_within == "7d":
		conditions.append("created_at >= datetime('now', '-7 days')")
	if conditions:
		query += " WHERE " + " AND ".join(conditions)
	sort_expressions = {
		"salary": "CAST(REPLACE(substr(COALESCE(salary, ''), 1, CASE WHEN instr(salary, 'K') > 0 THEN instr(salary, 'K') - 1 ELSE length(salary) END), ',', '') AS REAL)",
		"education": "CASE TRIM(COALESCE(education, '')) WHEN '博士' THEN 5 WHEN '硕士' THEN 4 WHEN '本科' THEN 3 WHEN '大专' THEN 2 WHEN '不限' THEN 1 ELSE 0 END",
		"score": "COALESCE(score, 0)",
		"status": "COALESCE(status, '')",
		"hr_active": "COALESCE(hr_active, '')",
		"created_at": "COALESCE(created_at, '')",
	}
	query += f" ORDER BY {sort_expressions[sort_by]} {sort_order.upper()}, created_at DESC, score DESC"

	db = _get_web_db()
	try:
		all_total = db.execute("SELECT COUNT(*) FROM jobs WHERE deleted_at IS NULL").fetchone()[0]
		rows = [dict(row) for row in db.execute(query, params).fetchall()]
		if salary_min is not None or salary_max is not None:
			filtered_rows = []
			for row in rows:
				salary_range = parse_monthly_salary_k(row.get("salary", ""))
				if salary_range is None:
					continue
				job_min, job_max = salary_range
				if salary_min is not None and job_max < salary_min:
					continue
				if salary_max is not None and job_min > salary_max:
					continue
				filtered_rows.append(row)
			rows = filtered_rows
		total = len(rows)
		return _json_response({
			"items": rows[offset:offset + limit],
			"total": total,
			"all_total": all_total,
			"limit": limit,
			"offset": offset,
		})
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
	include_monitor_conversations = request.params.get("include_monitor_conversations", "").lower() in ("1", "true", "yes")
	db = _get_web_db()
	try:
		data = get_recent_history(db, limit)
		if include_unresolved or include_monitor_conversations:
			monitor_replies = get_recent_monitor_replies(db) if include_monitor_conversations else []
			if include_monitor_conversations:
				retained_reply_ids = {item["id"] for item in monitor_replies}
				data = [
					item for item in data
					if item["action"] not in ("replied", "auto_replied", "resume_sent") or item["id"] in retained_reply_ids
				]
			seen_ids = {item["id"] for item in data}
			extra_groups = []
			if include_unresolved:
				extra_groups.extend((
					get_unresolved_reply_pending(db),
					get_unresolved_resume_failures(db),
				))
			if include_monitor_conversations:
				extra_groups.append(monitor_replies)
			for extra_items in extra_groups:
				data.extend(item for item in extra_items if item["id"] not in seen_ids)
				seen_ids.update(item["id"] for item in extra_items)
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


@app.route("/api/history/<history_id>/open-chat", method="POST")
def api_history_open_chat(history_id):
	with job_mutation_lock:
		conflict = _active_task_mutation_error()
		if conflict is not None:
			return conflict
		db = _get_web_db()
		try:
			row = db.execute(
				"""
				SELECT j.*
				FROM history h
				JOIN jobs j ON j.id = h.job_id
				WHERE h.id = ? AND j.deleted_at IS NULL
				""",
				(history_id,),
			).fetchone()
			if not row:
				return _json_response({"error": "监测记录或岗位不存在"}, 404)
			job = dict(row)
		finally:
			db.close()

		if str(job.get("source_platform") or "boss") != "boss":
			return _json_response({"error": "该平台不支持聊天定位"}, 400)

		from bosshunter.executor.monitor import MonitorRiskDetected, _open_conversation_from_chat_list

		try:
			target_id = _open_conversation_from_chat_list(
				job,
				load_config(CONFIG_PATH),
				background=False,
			)
		except MonitorRiskDetected as exc:
			return _json_response({"error": f"BOSS 页面出现风险提示：{exc.kind}"}, 409)
		if not target_id:
			return _json_response({"error": "已打开 BOSS 聊天页，但没有找到对应联系人"}, 404)
		return _json_response({"success": True, "message": "已定位到对应聊天对话"})


@app.route("/api/history/<history_id>/prepare-reply", method="POST")
def api_history_prepare_reply(history_id):
	with job_mutation_lock:
		conflict = _active_task_mutation_error()
		if conflict is not None:
			return conflict
		db = _get_web_db()
		try:
			row = db.execute(
				"SELECT id, job_id, action FROM history WHERE id = ?",
				(history_id,),
			).fetchone()
			if not row:
				return _json_response({"error": "待处理记录不存在"}, 404)
			if row["action"] != "hr_reply_detected":
				return _json_response({"error": "只能处理刚检测到的 HR 消息"}, 400)
			resolved = db.execute(
				"""
				SELECT 1 FROM history
				WHERE job_id = ? AND id > ?
				  AND action IN (
				    'reply_pending', 'needs_resume', 'resume_failed', 'resume_sent',
				    'reply_dismissed', 'replied', 'auto_replied', 'rejected'
				  )
				LIMIT 1
				""",
				(row["job_id"], row["id"]),
			).fetchone()
			if resolved:
				return _json_response({"success": True, "already_processed": True})
			job_id = str(row["job_id"])
		finally:
			db.close()

		from bosshunter.executor.monitor import process_detected_reply

		summary = process_detected_reply(job_id, load_config(CONFIG_PATH))
		if summary.get("stop_reason"):
			return _json_response({"error": "读取 BOSS 对话时触发安全停止", "summary": summary}, 409)
		processed = sum(
			int(summary.get(key, 0) or 0)
			for key in ("skipped", "pending", "needs_resume", "rejected", "replied")
		)
		if not processed and summary.get("failed"):
			return _json_response({"error": "读取或处理对话失败，请先打开聊天对话检查", "summary": summary}, 502)
		if not processed:
			return _json_response({"error": "没有找到对应的新 HR 对话，请先用“打开聊天对话”检查"}, 404)
		return _json_response({"success": True, "summary": summary})


@app.route("/api/workbench")
def api_workbench():
	db = _get_web_db()
	try:
		config = load_config(CONFIG_PATH)
		threshold = config.get("scoring", {}).get("threshold", 60)
		daily_limit = int(config.get("throttle", {}).get("daily_limit", 30) or 30)
		today_sent_row = db.execute(
			"SELECT COUNT(*) AS cnt FROM history WHERE action='sent' AND date(created_at)=date('now')"
		).fetchone()
		today_sent = int(today_sent_row["cnt"] if today_sent_row else 0)
		status = task_runner.status()
		return _json_response({
			"funnel": get_funnel_stats(db),
			"funnel_today": get_funnel_stats(db, today=True),
			"pending_confirmation": [
				job for job in get_jobs_pending_confirmation(db)
				if int(job.get("score") or 0) >= threshold
				and platform_supports(str(job.get("source_platform") or "boss"), "deliver")
			],
			"pending_greetings": [
				job for job in get_jobs_ready_to_send(db)
				if platform_supports(str(job.get("source_platform") or "boss"), "deliver")
			],
			"send_errors": [
				job for job in get_jobs_with_send_errors(db)
				if platform_supports(str(job.get("source_platform") or "boss"), "deliver")
			],
			"needs_resume": [
				job for job in get_jobs_needing_resume(db)
				if platform_supports(str(job.get("source_platform") or "boss"), "deliver")
			],
			"send_quota": {
				"daily_limit": daily_limit,
				"sent": today_sent,
				"remaining": max(daily_limit - today_sent, 0),
				"exhausted": today_sent >= daily_limit,
			},
			"task": status["active"],
			"last_task": status["last_task"],
		})
	finally:
		db.close()


@app.route("/api/workbench/preflight", method=["GET", "POST"])
def api_workbench_preflight():
	body = request.json if request.method == "POST" else {}
	body = body if isinstance(body, dict) else {}
	mode = str(body.get("mode") or request.params.get("mode", ""))
	options = body.get("options") if isinstance(body.get("options"), dict) else None
	try:
		config = load_config(CONFIG_PATH)
		checks = collect_preflight_checks(mode, config, options)
		messages = error_messages(checks)
		return _json_response({"ok": not messages, "messages": messages, "checks": checks})
	except Exception as e:
		return _json_response({"ok": False, "messages": [str(e)]}, 500)


@app.route("/api/diagnostics/ai")
def api_ai_diagnostics():
	try:
		checks = check_ai_connection(load_config(CONFIG_PATH), required=True)
		messages = error_messages(checks)
		return _json_response({"ok": not messages, "messages": messages, "checks": checks})
	except Exception as e:
		return _json_response({"ok": False, "messages": [str(e)]}, 500)


def _scoring_options_from_body(body: dict) -> dict:
	raw_options = body.get("options", body)
	if not isinstance(raw_options, dict):
		raise ValueError("评分参数必须是对象")
	return validate_options(
		raw_options.get("scope", "pending"),
		raw_options.get("limit"),
		raw_options.get("job_ids", []),
		raw_options.get("force_rescore", False),
	)


@app.route("/api/scoring/preview", method="POST")
def api_scoring_preview():
	try:
		body = request.json or {}
		if not isinstance(body, dict):
			raise ValueError("请求体必须是对象")
		options = _scoring_options_from_body(body)
		config = load_config(CONFIG_PATH)
		max_attempts = config.get("ai", {}).get("scoring_max_attempts", 2)
		db = _get_web_db()
		try:
			return _json_response(preview_scoring(db, **options, max_attempts_per_job=max_attempts))
		finally:
			db.close()
	except ValueError as exc:
		return _json_response({"error": str(exc)}, 400)


@app.route("/api/scoring/start", method="POST")
def api_scoring_start():
	run_id = str(uuid4())
	db_path = DATA_DIR / "bosshunter.db"
	try:
		body = request.json or {}
		if not isinstance(body, dict):
			raise ValueError("请求体必须是对象")
		options = _scoring_options_from_body(body)
		# force=true 允许结束已暂停的旧评分记录后强制开新任务；running 任务不在此列（issue #100）。
		# 只接受真正的布尔 true：bool("false") 也为真，异常请求会误触发强制重启（#117 review）。
		force = body.get("force", False) is True
		config = load_config(CONFIG_PATH)
		messages = _preflight_messages("rescore", config)
		if messages:
			return _json_response({"error": "请先处理评分启动检查", "messages": messages}, 400)
		active_runs = [run for run in list_scoring_runs(db_path, limit=100) if run.get("status") in {"running", "paused"}]
		running_runs = [run for run in active_runs if run.get("status") == "running"]
		paused_runs = [run for run in active_runs if run.get("status") == "paused"]
		if running_runs:
			return _json_response({"error": "已有独立评分任务正在运行，请等待其结束或先停止该任务"}, 409)
		if paused_runs:
			if not force:
				return _json_response({
					"error": "已有等待恢复的评分任务，请先继续或结束该任务；确认问题已修复后也可强制开始新任务",
					"code": "scoring_run_paused",
				}, 409)
			for run in paused_runs:
				update_scoring_run(
					db_path,
					str(run.get("id") or ""),
					status="stopped",
					error="已被新的评分任务强制结束",
				)
		db = _get_web_db()
		try:
			selected = select_scoring_jobs(db, **options)
		finally:
			db.close()
		job_ids = [str(job["id"]) for job in selected]
		if not job_ids:
			return _json_response({"error": "没有符合条件的待评分岗位"}, 400)
		stored_options = {
			"scope": options["scope"],
			"limit": options["limit"],
			"force_rescore": options["force_rescore"],
		}
		create_scoring_run(db_path, run_id=run_id, options=stored_options, job_ids=job_ids)
		runtime_options = {"job_ids": job_ids, "force_rescore": options["force_rescore"]}
		with job_mutation_lock:
			update_scoring_run(db_path, run_id, status="running")
			task = task_runner.start("score", _task_config({
				"_score_run_id": run_id,
				"_score_options": runtime_options,
			}))
		update_scoring_run(db_path, run_id, task_id=str(task["id"]))
		return _json_response({"run": get_scoring_run(db_path, run_id), "task": task})
	except TaskAlreadyRunningError as exc:
		update_scoring_run(db_path, run_id, status="stopped", error=str(exc))
		return _json_response({"error": str(exc)}, 409)
	except ValueError as exc:
		return _json_response({"error": str(exc)}, 400)
	except Exception as exc:
		update_scoring_run(db_path, run_id, status="failed", error=str(exc)[:1000])
		return _json_response({"error": "启动评分失败"}, 500)


@app.route("/api/scoring/runs")
def api_scoring_runs():
	return _json_response(list_scoring_runs(DATA_DIR / "bosshunter.db"))


@app.route("/api/scoring/runs/<run_id>/pause", method="POST")
def api_scoring_pause(run_id):
	db_path = DATA_DIR / "bosshunter.db"
	run = get_scoring_run(db_path, run_id)
	if not run:
		return _json_response({"error": "评分任务不存在"}, 404)
	if run.get("status") != "running":
		return _json_response(run)
	try:
		task = task_runner.stop(str(run.get("task_id") or ""), "用户暂停独立评分")
	except KeyError:
		task = {"status": "stopped"}
	latest = get_scoring_run(db_path, run_id) or run
	if task.get("status") not in {"completed", "failed"} and latest.get("remaining_job_ids"):
		latest = update_scoring_run(db_path, run_id, status="paused", pause_reason="用户暂停独立评分") or latest
	return _json_response(latest)


@app.route("/api/scoring/runs/<run_id>/resume", method="POST")
def api_scoring_resume(run_id):
	db_path = DATA_DIR / "bosshunter.db"
	run = get_scoring_run(db_path, run_id)
	if not run:
		return _json_response({"error": "评分任务不存在"}, 404)
	if run.get("status") != "paused":
		return _json_response({"error": "只有已暂停的评分任务可以恢复"}, 409)
	remaining = [str(job_id) for job_id in run.get("remaining_job_ids", []) if str(job_id)]
	if not remaining:
		return _json_response({"error": "该评分任务没有剩余岗位"}, 400)
	config = load_config(CONFIG_PATH)
	messages = _preflight_messages("rescore", config)
	if messages:
		return _json_response({"error": "请先处理评分启动检查", "messages": messages}, 400)
	force_rescore = bool(run.get("options", {}).get("force_rescore"))
	db = _get_web_db()
	try:
		eligible = select_scoring_jobs(
			db,
			scope="selected",
			limit=None,
			job_ids=remaining,
			force_rescore=force_rescore,
		)
	finally:
		db.close()
	remaining = [str(job["id"]) for job in eligible]
	if not remaining:
		completed = update_scoring_run(db_path, run_id, status="completed", remaining_job_ids=[])
		return _json_response(completed)
	try:
		with job_mutation_lock:
			update_scoring_run(db_path, run_id, status="running", remaining_job_ids=remaining)
			task = task_runner.start("score", _task_config({
				"_score_run_id": run_id,
				"_score_options": {"job_ids": remaining, "force_rescore": force_rescore},
			}))
		update_scoring_run(db_path, run_id, task_id=str(task["id"]))
		return _json_response({"run": get_scoring_run(db_path, run_id), "task": task})
	except TaskAlreadyRunningError as exc:
		update_scoring_run(db_path, run_id, status="paused", pause_reason=str(exc))
		return _json_response({"error": str(exc)}, 409)


@app.route("/api/scoring/runs/<run_id>/end", method="POST")
def api_scoring_end(run_id):
	db_path = DATA_DIR / "bosshunter.db"
	run = get_scoring_run(db_path, run_id)
	if not run:
		return _json_response({"error": "评分任务不存在"}, 404)
	if run.get("status") == "running" and run.get("task_id"):
		try:
			task_runner.stop(str(run["task_id"]), "用户结束独立评分")
		except KeyError:
			pass
	ended = update_scoring_run(db_path, run_id, status="stopped", remaining_job_ids=[])
	return _json_response(ended)


@app.route("/api/workbench/task", method="POST")
def api_workbench_task_start():
	try:
		body = request.json or {}
		if not isinstance(body, dict):
			return _json_response({"error": "请求体必须是对象"}, 400)
		mode = str(body.get("mode", ""))
		base_config = load_config(CONFIG_PATH)
		options = body.get("options") if isinstance(body.get("options"), dict) else None
		collection_options = None
		if mode == "collect":
			try:
				collection_options = normalize_collection_options(base_config, options)
			except ValueError as exc:
				return _json_response({"error": str(exc)}, 400)
		elif mode == "full":
			try:
				collection_options = normalize_collection_options(base_config, options)
			except ValueError as exc:
				return _json_response({"error": str(exc)}, 400)
			collection_only = [
				platform for platform in collection_options["platform_order"]
				if not platform_supports(platform, "deliver")
			]
			if collection_only:
				return _json_response({
					"error": "智联和前程无忧当前只支持单独采集，不能进入发送全流程",
					"collection_only_platforms": collection_only,
				}, 400)
			collection_options["auto_score"] = True
		messages = _preflight_messages(mode, base_config, collection_options)
		if messages:
			return _json_response({"error": "请先处理启动前检查", "messages": messages}, 400)
		extra = {"_collection_options": collection_options} if collection_options is not None else {}
		if collection_options is not None:
			# Persist only non-secret collection preferences so the next dialog can
			# restore each platform's independent fields and queue order.
			base_config["collection"] = {
				**(base_config.get("collection") if isinstance(base_config.get("collection"), dict) else {}),
				"default_order": collection_options["platform_order"],
				"auto_score_default": collection_options["auto_score"],
			}
			platform_configs = deepcopy(base_config.get("platforms")) if isinstance(base_config.get("platforms"), dict) else {}
			selected_platforms = set(collection_options["platform_order"])
			for platform, value in collection_options["platforms"].items():
				platform_configs[platform] = {
					**(platform_configs.get(platform) if isinstance(platform_configs.get(platform), dict) else {}),
					"enabled": platform in selected_platforms,
					"search": value,
				}
			for platform in ("boss", "zhilian", "51job"):
				if platform not in selected_platforms and isinstance(platform_configs.get(platform), dict):
					platform_configs[platform]["enabled"] = False
			base_config["platforms"] = platform_configs
			_write_config(base_config)
		with job_mutation_lock:
			task = task_runner.start(mode, _task_config(extra))
		return _json_response(task)
	except TaskAlreadyRunningError as e:
		return _json_response({"error": str(e)}, 409)
	except Exception as e:
		return _json_response({"error": str(e)}, 500)


@app.route("/api/collection/runs")
def api_collection_runs():
	try:
		limit = int(request.params.get("limit", 20))
		return _json_response(list_collection_runs(DATA_DIR / "bosshunter.db", limit=limit))
	except (TypeError, ValueError) as exc:
		return _json_response({"error": str(exc)}, 400)


@app.route("/api/collection/runs/<run_id>")
def api_collection_run_detail(run_id):
	run = get_collection_run(DATA_DIR / "bosshunter.db", run_id)
	if not run:
		return _json_response({"error": "采集运行记录不存在"}, 404)
	return _json_response(run)


@app.route("/api/workbench/task/<task_id>/stop", method="POST")
def api_workbench_task_stop(task_id):
	try:
		return _json_response(task_runner.stop(task_id))
	except KeyError:
		return _json_response({"error": "任务不存在"}, 404)
	except Exception as e:
		return _json_response({"error": str(e)}, 500)


@app.route("/api/workbench/deliver", method="POST")
def api_workbench_deliver():
	try:
		body = request.json or {}
		job_ids = [str(job_id) for job_id in body.get("job_ids", []) if str(job_id)]
		if not job_ids:
			return _json_response({"error": "请选择要投递的岗位"}, 400)
		direct_send = bool(body.get("direct_send"))
		validation_db = _get_web_db()
		try:
			placeholders = ",".join("?" for _ in job_ids)
			active_ids = {
				str(row["id"])
				for row in validation_db.execute(
					f"SELECT id FROM jobs WHERE deleted_at IS NULL AND id IN ({placeholders})",
					job_ids,
				).fetchall()
			}
			platform_rows = validation_db.execute(
				f"SELECT id, status, greeting, COALESCE(source_platform, 'boss') AS source_platform FROM jobs WHERE deleted_at IS NULL AND id IN ({placeholders})",
				job_ids,
			).fetchall()
		finally:
			validation_db.close()
		invalid_ids = [job_id for job_id in job_ids if job_id not in active_ids]
		if invalid_ids:
			return _json_response({"error": "所选岗位不存在或已进入回收站", "invalid_ids": invalid_ids}, 409)
		unsupported = [
			str(row["id"])
			for row in platform_rows
			if not platform_supports(str(row["source_platform"] or "boss"), "deliver")
		]
		if unsupported:
			return _json_response({
				"error": "所选岗位的平台暂不支持投递动作",
				"unsupported_platform": "unknown",
				"invalid_ids": unsupported,
			}, 403)
		allowed_statuses = {"ready", "approved", "error"} if direct_send else {"ready", "approved"}
		completed_statuses = {"sent", "replied", "resume_sent", "needs_resume", "follow_up_sent"}
		already_sent_ids = {
			str(row["id"])
			for row in platform_rows
			if str(row["status"] or "") in completed_statuses
		}
		missing_greeting_ids = {
			str(row["id"])
			for row in platform_rows
			if direct_send
			and str(row["status"] or "") in allowed_statuses
			and not str(row["greeting"] or "").strip()
		}
		not_ready_ids = {
			str(row["id"])
			for row in platform_rows
			if str(row["status"] or "") not in allowed_statuses
			and str(row["status"] or "") not in completed_statuses
		}
		invalid_status_ids = [
			job_id
			for job_id in job_ids
			if job_id in already_sent_ids or job_id in missing_greeting_ids or job_id in not_ready_ids
		]
		if invalid_status_ids:
			if already_sent_ids and not missing_greeting_ids and not not_ready_ids:
				error = "所选岗位已经投递，不能重复发送"
			elif missing_greeting_ids and not already_sent_ids and not not_ready_ids:
				error = "所选岗位尚未生成招呼语，不能直接发送"
			elif not_ready_ids and not already_sent_ids and not missing_greeting_ids:
				error = "所选岗位尚未完成评分筛选或人工确认，暂不能投递"
			else:
				error = "所选岗位包含尚未准备好、缺少招呼语或已经投递的岗位，暂不能投递"
			return _json_response({
				"error": error,
				"invalid_ids": invalid_status_ids,
				"already_sent_ids": [job_id for job_id in job_ids if job_id in already_sent_ids],
				"not_ready_ids": [job_id for job_id in job_ids if job_id in not_ready_ids],
				"missing_greeting_ids": [job_id for job_id in job_ids if job_id in missing_greeting_ids],
			}, 409)

		status = task_runner.status()
		active_task = status.get("active") or {}
		active_runtime_task = task_runner._tasks.get(active_task.get("id"))
		monitoring_task = None
		if (
			active_runtime_task
			and active_runtime_task.status == "running"
			and active_runtime_task.context.get("monitoring")
		):
			monitoring_task = active_runtime_task
		delivery_task = None
		if (
			active_runtime_task
			and active_runtime_task.status == "running"
			and active_runtime_task.context.get("delivering")
		):
			delivery_task = active_runtime_task
		waiting_task = None
		if (
			not direct_send
			and active_runtime_task
			and active_runtime_task.mode == "full"
			and active_runtime_task.status == "running"
			and not monitoring_task
			and not delivery_task
			and not active_runtime_task.context.get("confirmation_complete")
		):
			waiting_task = active_runtime_task
		if active_task and not waiting_task and not monitoring_task and not delivery_task:
			raise TaskAlreadyRunningError(
				f"当前已有后台任务「{active_task.get('label', '未知任务')}」正在运行或停止中，请等待其完全结束"
			)

		queued_payload = None
		status_job_ids = job_ids
		if delivery_task:
			queued_payload, status_job_ids = _queue_active_delivery(
				delivery_task,
				job_ids,
				direct_send=direct_send,
			)

		db = _get_web_db()
		try:
			for job_id in status_job_ids:
				update_job_status(db, job_id, "approved")
				if not direct_send:
					add_history(db, job_id, "approved", "Web Dashboard 确认投递")
		finally:
			db.close()

		if queued_payload is not None:
			return _json_response(queued_payload)

		if waiting_task:
			waiting_task.context["confirmed_job_ids"] = job_ids
			waiting_task.context["delivery_requested"] = True
			confirmation_event = waiting_task.context.get("confirmation_event")
			if isinstance(confirmation_event, Event):
				confirmation_event.set()
			return _json_response(waiting_task.snapshot())

		if monitoring_task:
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
			placeholders = ",".join("?" for _ in job_ids)
			active_ids = {
				str(row["id"])
				for row in db.execute(
					f"SELECT id FROM jobs WHERE deleted_at IS NULL AND id IN ({placeholders})",
					job_ids,
				).fetchall()
			}
			invalid_ids = [job_id for job_id in job_ids if job_id not in active_ids]
			if invalid_ids:
				return _json_response({"error": "所选岗位不存在或已进入回收站", "invalid_ids": invalid_ids}, 409)
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
		row = db.execute("SELECT * FROM jobs WHERE id = ? AND deleted_at IS NULL", (job_id,)).fetchone()
		if not row:
			return _json_response({"error": "岗位不存在"}, 404)
		return _json_response(dict(row))
	finally:
		db.close()


@app.route("/api/jobs/<job_id>/mark-resume-sent", method="POST")
def api_job_mark_resume_sent(job_id):
	db = _get_web_db()
	try:
		row = db.execute("SELECT source_platform FROM jobs WHERE id = ? AND deleted_at IS NULL", (job_id,)).fetchone()
		if not row:
			return _json_response({"error": "岗位不存在或已进入回收站"}, 404)
		if not platform_supports(str(row["source_platform"] or "boss"), "deliver"):
			return _json_response({"error": "该岗位来源平台当前不支持投递或简历发送链路"}, 403)
		update_job_status(db, job_id, "resume_sent")
		add_history(db, job_id, "resume_sent", "Web Dashboard 标记定制简历已发送")
		return _json_response({"success": True})
	finally:
		db.close()


@app.route("/api/jobs/<job_id>/resume/download")
def api_job_resume_download(job_id):
	db = _get_web_db()
	try:
		row = db.execute("SELECT resume_path FROM jobs WHERE id = ? AND deleted_at IS NULL", (job_id,)).fetchone()
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
	with job_mutation_lock:
		return _api_history_reply_locked(history_id)


def _api_history_reply_locked(history_id):
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
		latest_pending = db.execute(
			"SELECT MAX(id) AS id FROM history WHERE job_id = ? AND action = 'reply_pending'",
			(row["job_id"],),
		).fetchone()
		if not latest_pending or int(latest_pending["id"] or 0) != int(row["id"]):
			return _json_response({"error": "这条建议已不是最新一轮，请刷新后处理最新消息"}, 409)
		already_resolved = db.execute(
			"""
			SELECT 1
			FROM history
			WHERE job_id = ?
			  AND id > ?
			  AND action IN ('reply_dismissed', 'replied', 'auto_replied')
			LIMIT 1
			""",
			(row["job_id"], row["id"]),
		).fetchone()
		if already_resolved:
			return _json_response({"success": True, "already_resolved": True, "message": "这轮回复已处理。"})

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
	with job_mutation_lock:
		return _api_history_dismiss_locked(history_id)


def _api_history_dismiss_locked(history_id):
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
		latest_pending = db.execute(
			"SELECT MAX(id) AS id FROM history WHERE job_id = ? AND action = 'reply_pending'",
			(row["job_id"],),
		).fetchone()
		if not latest_pending or int(latest_pending["id"] or 0) != int(row["id"]):
			return _json_response({"error": "这条建议已不是最新一轮，请刷新后处理最新消息"}, 409)
		already_resolved = db.execute(
			"""
			SELECT 1
			FROM history
			WHERE job_id = ?
			  AND id > ?
			  AND action IN ('reply_dismissed', 'replied', 'auto_replied')
			LIMIT 1
			""",
			(row["job_id"], row["id"]),
		).fetchone()
		if already_resolved:
			return _json_response({"success": True, "already_resolved": True, "message": "这轮回复已处理。"})

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


@app.route("/api/history/<history_id>/resume-retry", method="POST")
def api_history_resume_retry(history_id):
	db = _get_web_db()
	try:
		row = db.execute(
			"SELECT id, job_id, action, detail FROM history WHERE id = ?",
			(history_id,),
		).fetchone()
		if not row:
			return _json_response({"error": "简历失败记录不存在"}, 404)
		if row["action"] != "resume_failed":
			return _json_response({"error": "只能重试简历生成失败记录"}, 400)

		job = db.execute(
			"SELECT * FROM jobs WHERE id = ? AND deleted_at IS NULL",
			(row["job_id"],),
		).fetchone()
		if not job:
			return _json_response({"error": "对应岗位不存在或已删除"}, 404)

		from bosshunter.ai.resume import generate_tailored_resume, get_last_resume_failure_reason

		try:
			resume_path = generate_tailored_resume(job["id"], _task_config())
		except Exception as exc:
			return _json_response({"error": f"重新生成失败：{exc}"}, 500)

		if not resume_path:
			reason = get_last_resume_failure_reason(job["id"]) or "定制简历生成失败，未获得更具体的错误信息"
			add_history(
				db,
				job["id"],
				"resume_failed",
				json.dumps({
					"schema": "resume_failed.v2",
					"system_reason": reason,
					"hr_question": "",
					"conversation_tail": [],
				}, ensure_ascii=False),
			)
			return _json_response({"error": reason}, 400)

		current_status = str(job["status"] or "").strip()
		if current_status not in {"replied", "resume_sent", "needs_resume", "follow_up_sent"}:
			update_job_status(db, job["id"], "needs_resume")
		history_detail = json.dumps({
			"schema": "needs_resume.v1",
			"message": f"Web Dashboard 重试生成定制简历成功，待手动发送: {resume_path}",
			"resume_path": str(resume_path),
		}, ensure_ascii=False)
		add_history(db, job["id"], "needs_resume", history_detail)
		return _json_response({"success": True, "resume_path": str(resume_path)})
	except Exception as exc:
		return _json_response({"error": str(exc)}, 500)
	finally:
		db.close()


@app.route("/api/history/<history_id>/resume-dismiss", method="POST")
def api_history_resume_dismiss(history_id):
	db = _get_web_db()
	try:
		row = db.execute(
			"SELECT id, job_id, action, detail FROM history WHERE id = ?",
			(history_id,),
		).fetchone()
		if not row:
			return _json_response({"error": "简历失败记录不存在"}, 404)
		if row["action"] != "resume_failed":
			return _json_response({"error": "只能忽略简历生成失败记录"}, 400)

		add_history(
			db,
			row["job_id"],
			"resume_failed_dismissed",
			json.dumps({"schema": "resume_failed_dismissed.v1", "message": "Web Dashboard 忽略简历生成失败记录"}, ensure_ascii=False),
		)
		return _json_response({"success": True})
	except Exception as exc:
		return _json_response({"error": str(exc)}, 500)
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
		_write_config(data)

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
		return _config_download_payload(load_config(CONFIG_PATH))
	abort(404, "config.yaml not found")


@app.route("/api/config/cities")
def api_cities():
	return _json_response(get_city_map(BASE_DIR))


@app.route("/api/config/cities/lookup", method="POST")
def api_city_lookup():
	try:
		body = request.json or {}
		city = str(body.get("city") or "").strip()
		city_code = get_city_map(BASE_DIR).get(city)
		if city_code:
			return _json_response({"name": city, "code": city_code})
		return _json_response(lookup_city(city))
	except CityLookupError as exc:
		return _json_response({"error": str(exc)}, 400)


@app.route("/api/cities")
def api_city_snapshot():
	platform = request.params.get("platform", "").strip().lower()
	if platform == "zhilian":
		snapshot = load_zhilian_city_snapshot()
		return _json_response({
			"ok": True,
			"source": snapshot["source"],
			"count": len(snapshot["cities"]),
			"updated_at": snapshot.get("fetched_at"),
			"note": snapshot.get("note", ""),
			"cities": snapshot["cities"],
		})
	if platform == "51job":
		snapshot = load_51job_city_snapshot()
		return _json_response({
			"ok": True,
			"source": snapshot["source"],
			"count": len(snapshot["cities"]),
			"note": snapshot.get("note", ""),
			"cities": snapshot["cities"],
		})
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
		return _json_response({
			"ok": False,
			"source": "bundled",
			"count": 0,
			"cities": [],
			"error": "本地城市列表不可用",
		}, 500)


@app.route("/api/cities/refresh", method="POST")
def api_city_refresh():
	platform = request.params.get("platform", "").strip().lower()
	if platform in {"zhilian", "51job"}:
		label = "智联" if platform == "zhilian" else "51job"
		return _json_response({
			"ok": False,
			"source": "local",
			"using_local_data": True,
			"error": f"{label}使用内置城市目录，不执行联网刷新；岗位采集窗口会根据城市名称自动匹配编码。",
		}, 409)
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
		exported_count = export_row_count(db, scope=scope, job_ids=job_ids, filters=filters)
		response.content_type = content_type
		response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
		response.headers["Content-Length"] = str(len(content))
		response.headers["X-Exported-Count"] = str(exported_count)
		return content
	except InvalidJobSelectionError as exc:
		return _json_response({
			"error": str(exc),
			"code": "invalid_job_ids",
			"invalid_ids": exc.invalid_ids,
		}, 400)
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
	if isinstance(exc, (JobDeletionConflictError, JobManualSentConflictError)):
		payload["blocked"] = exc.blocked
		payload["not_found"] = exc.not_found
	return _json_response(payload, 409 if isinstance(exc, (JobDeletionConflictError, JobManualSentConflictError)) else 400)


def _active_task_mutation_error():
	active = task_runner.status().get("active")
	if not active:
		return None
	return _json_response({
		"error": f"当前后台任务「{active.get('label', '未知任务')}」仍在运行，请停止或等待结束后再修改岗位状态",
		"code": "active_task_conflict",
		"task_id": active.get("id"),
	}, 409)


@app.route("/api/jobs/soft-delete", method="POST")
def api_jobs_soft_delete():
	db = _get_web_db()
	try:
		body, job_ids = _job_action_payload()
		with job_mutation_lock:
			conflict = _active_task_mutation_error()
			if conflict is not None:
				return conflict
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
		with job_mutation_lock:
			conflict = _active_task_mutation_error()
			if conflict is not None:
				return conflict
			result = restore_jobs(db, job_ids, confirmed=body.get("confirmed") is True)
		return _json_response(result)
	except (ValueError, JobDeletionConflictError) as exc:
		return _job_action_error(exc)
	finally:
		db.close()


@app.route("/api/jobs/manual-sent", method="POST")
def api_jobs_manual_sent():
	db = _get_web_db()
	try:
		body, job_ids = _job_action_payload()
		with job_mutation_lock:
			conflict = _active_task_mutation_error()
			if conflict is not None:
				return conflict
			result = mark_external_jobs_sent(
				db,
				job_ids,
				confirmed=body.get("confirmed") is True,
			)
		return _json_response(result)
	except (ValueError, JobManualSentConflictError) as exc:
		return _job_action_error(exc)
	finally:
		db.close()


@app.route("/api/jobs/permanent-delete", method="POST")
def api_jobs_permanent_delete():
	db = _get_web_db()
	try:
		body, job_ids = _job_action_payload()
		with job_mutation_lock:
			conflict = _active_task_mutation_error()
			if conflict is not None:
				return conflict
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
		_write_config(config)

		return _json_response({
			"success": True,
			"filename": safe_name,
			"size": len(stored_content),
			"path": str(dest)
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
		_write_config(config)

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

	app.run(
		host=host,
		port=port,
		quiet=False,
		reloader=False,
		server_class=ThreadingWSGIServer,
	)
