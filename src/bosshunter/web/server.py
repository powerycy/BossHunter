"""BossHunter Web Server - Bottle HTTP service + API routes.

Serves:
- /api/* → JSON data endpoints
- /* → Frontend static files (dist/)
"""

import json
import time
from copy import deepcopy
from pathlib import Path
from threading import Event

from bottle import Bottle, request, response, static_file, abort

from bosshunter import __version__
from bosshunter.ai.credentials import get_ai_api_key
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
	get_jobs_with_send_errors,
	get_recent_history,
	get_unresolved_resume_failures,
	get_stats,
	get_top_companies,
	update_job_status,
)
from bosshunter.web.preflight import check_ai_connection, collect_preflight_checks, error_messages
from bosshunter.web.resume_upload import ResumeUploadError, prepare_resume_content
from bosshunter.web.tasks import TaskAlreadyRunningError, WorkbenchTask, WorkbenchTaskRunner

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


def set_base_dir(base_dir: Path | str) -> None:
	"""Set the runtime directory used for config.yaml, data, and uploads."""
	global BASE_DIR, DATA_DIR, RESUME_DIR, CONFIG_PATH
	BASE_DIR = Path(base_dir).resolve()
	DATA_DIR = BASE_DIR / "data"
	RESUME_DIR = DATA_DIR / "resumes"
	CONFIG_PATH = BASE_DIR / "config.yaml"


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
	if mode not in {"full", "collect", "monitor"}:
		messages.append(f"不支持的任务模式：{mode}")

	profile = config.get("profile", {})
	resume_path = profile.get("resume_path", "")
	if not resume_path or not Path(str(resume_path)).exists():
		messages.append("请先在配置页上传 .md 或 .docx 简历。")

	if mode in {"full", "collect"} and not config.get("search", {}).get("keywords"):
		messages.append("请先在配置页填写搜索关键词。")

	if mode in {"full", "collect"} and not get_ai_api_key(config):
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
	from bosshunter.ai.scorer import score_jobs
	from bosshunter.scraper.jobs import scrape_jobs

	keywords = config.get("search", {}).get("keywords", [])
	_log(task, "开始采集岗位")
	scrape_jobs(config, keywords)
	if task.stop_requested.is_set():
		return
	_log(task, "开始 AI 评分")
	score_config = dict(config)
	score_config["_workbench_log"] = lambda message: _log(task, message)
	score_jobs(score_config)


def _execute_monitor(task: WorkbenchTask, config: dict) -> None:
	from bosshunter.executor.monitor import monitor_and_send_resumes

	monitor_config = dict(config)
	monitor_config["_workbench_stop_event"] = task.stop_requested
	interval_min = int(config.get("monitor", {}).get("interval", 30) or 30)
	interval_sec = max(interval_min * 60, 1)
	while not task.stop_requested.is_set():
		_log(task, "执行一轮监测")
		monitor_and_send_resumes(monitor_config)
		if task.stop_requested.is_set():
			return
		_log(task, f"本轮监测完成，{interval_min} 分钟后再次检查")
		task.stop_requested.wait(interval_sec)


def _execute_full(task: WorkbenchTask, config: dict) -> None:
	_execute_collect(task, config)
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
		_log(task, "没有待确认岗位，流程结束")
		return

	confirmation_event = Event()
	task.context["waiting_confirmation"] = True
	task.context["confirmation_event"] = confirmation_event
	_log(task, "等待前端确认投递")
	while not task.stop_requested.is_set() and not confirmation_event.wait(0.5):
		pass
	if task.stop_requested.is_set():
		return

	job_ids = [str(job_id) for job_id in task.context.get("confirmed_job_ids", []) if str(job_id)]
	if not job_ids:
		_log(task, "未收到前端确认岗位，流程结束")
		return

	task.context["waiting_confirmation"] = False
	_log(task, f"前端已确认 {len(job_ids)} 个岗位，继续投递")
	deliver_config = dict(config)
	deliver_config["_workbench_job_ids"] = job_ids
	_execute_deliver(task, deliver_config)
	if task.stop_requested.is_set():
		return
	_execute_monitor(task, config)


def _execute_deliver(task: WorkbenchTask, config: dict) -> None:
	from bosshunter.ai.greeter import generate_greetings
	from bosshunter.executor.sender import send_greetings

	config = dict(config)
	config["_workbench_stop_event"] = task.stop_requested
	config["_workbench_log"] = lambda message: _log(task, message)
	if not config.get("_workbench_skip_greeting"):
		_log(task, "生成招呼语")
		generate_greetings(config)
		if task.stop_requested.is_set():
			return
	_log(task, "发送招呼语")
	send_greetings(config, force=True)


task_runner._executors.update({
	"full": _execute_full,
	"collect": _execute_collect,
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
	limit = int(request.params.get("limit", 100))
	offset = int(request.params.get("offset", 0))

	db = _get_web_db()
	try:
		query = "SELECT * FROM jobs"
		params = []
		if status_filter:
			query += " WHERE status = ?"
			params.append(status_filter)
		query += " ORDER BY score DESC, created_at DESC LIMIT ? OFFSET ?"
		params.extend([limit, offset])

		rows = db.execute(query, params).fetchall()
		jobs = [dict(row) for row in rows]
		return _json_response(jobs)
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
				if int(job.get("score") or 0) >= threshold
			],
			"pending_greetings": get_jobs_ready_to_send(db),
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
		checks = check_ai_connection(load_config(CONFIG_PATH), required=True)
		messages = error_messages(checks)
		return _json_response({"ok": not messages, "messages": messages, "checks": checks})
	except Exception as e:
		return _json_response({"ok": False, "messages": [str(e)]}, 500)


@app.route("/api/workbench/task", method="POST")
def api_workbench_task_start():
	try:
		body = request.json or {}
		mode = body.get("mode", "")
		messages = _preflight_messages(mode, load_config(CONFIG_PATH))
		if messages:
			return _json_response({"error": "请先处理启动前检查", "messages": messages}, 400)
		task = task_runner.start(mode, _task_config())
		return _json_response(task)
	except TaskAlreadyRunningError as e:
		return _json_response({"error": str(e)}, 409)
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
	return _json_response(CITY_CODES)


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
		with open(CONFIG_PATH, "w", encoding="utf-8") as f:
			yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

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
		with open(CONFIG_PATH, "w", encoding="utf-8") as f:
			yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

		return _json_response({"success": True})
	except Exception as e:
		return _json_response({"error": str(e)}, 500)


# ─── Static Files + SPA Fallback ─────────────────────────

@app.route("/assets/<filepath:path>")
def serve_assets(filepath):
	return static_file(filepath, root=str(FRONTEND_DIR / "assets"))


@app.route("/")
@app.route("/<filepath:path>")
def serve_spa(filepath="index.html"):
	if str(filepath).startswith("api/"):
		return _json_response({"error": "Not found"}, 404)

	# Try serving the exact file first
	file_path = FRONTEND_DIR / filepath
	if file_path.is_file():
		return static_file(filepath, root=str(FRONTEND_DIR))
	# SPA fallback: return index.html for all non-API routes
	return static_file("index.html", root=str(FRONTEND_DIR))


# ─── Error Handlers ──────────────────────────────────────

@app.error(404)
def error404(error):
	if request.path.startswith("/api/"):
		response.content_type = "application/json; charset=utf-8"
		return json.dumps({"error": "Not found"}, ensure_ascii=False)
	# SPA fallback for non-API 404s
	return static_file("index.html", root=str(FRONTEND_DIR))


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
