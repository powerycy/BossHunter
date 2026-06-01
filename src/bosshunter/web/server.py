"""BossHunter Web Server - Bottle HTTP service + API routes.

Serves:
- /api/* → JSON data endpoints
- /* → Frontend static files (dist/)
"""

import json
import time
from pathlib import Path

from bottle import Bottle, request, response, static_file, abort

from bosshunter.config import load_config, CITY_CODES
from bosshunter.db import (
	get_db, get_stats, get_funnel_stats, get_daily_activity,
	get_top_companies, get_recent_history
)

app = Bottle()

# Paths
BASE_DIR = Path.cwd()
FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"
DATA_DIR = BASE_DIR / "data"
RESUME_DIR = DATA_DIR / "resumes"
CONFIG_PATH = BASE_DIR / "config.yaml"
SCHEMA_PATH = Path(__file__).parent / "config_schema.json"


def _json_response(data, status_code=200):
	"""Return JSON response with proper headers."""
	response.content_type = "application/json; charset=utf-8"
	response.status = status_code
	return json.dumps(data, ensure_ascii=False, default=str)


# ─── Health ───────────────────────────────────────────────

@app.route("/api/health")
def health():
	return _json_response({"status": "ok", "version": "1.1.0"})


# ─── Dashboard APIs ──────────────────────────────────────

@app.route("/api/funnel")
def api_funnel():
	db = get_db()
	try:
		data = get_funnel_stats(db)
		return _json_response(data)
	finally:
		db.close()


@app.route("/api/stats")
def api_stats():
	db = get_db()
	try:
		data = get_stats(db)
		return _json_response(data)
	finally:
		db.close()


@app.route("/api/activity")
def api_activity():
	days = int(request.params.get("days", 7))
	db = get_db()
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

	db = get_db()
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
	db = get_db()
	try:
		data = get_top_companies(db, limit)
		return _json_response(data)
	finally:
		db.close()


@app.route("/api/history")
def api_history():
	limit = int(request.params.get("limit", 15))
	db = get_db()
	try:
		data = get_recent_history(db, limit)
		return _json_response(data)
	finally:
		db.close()


# ─── Config APIs ─────────────────────────────────────────

@app.route("/api/config")
def api_config_get():
	try:
		config = load_config(CONFIG_PATH)
		# Mask api_key for security
		if "ai" in config and "api_key" in config["ai"]:
			key = config["ai"]["api_key"]
			if key and len(key) > 8:
				config["ai"]["api_key_masked"] = key[:4] + "***" + key[-4:]
			else:
				config["ai"]["api_key_masked"] = "***"
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

		# Validate extension
		name = upload.filename
		if not name.endswith(".md"):
			return _json_response({"error": "仅支持 .md 格式"}, 400)

		# Validate size (10MB max)
		content = upload.file.read()
		if len(content) > 10 * 1024 * 1024:
			return _json_response({"error": "文件大小超过 10MB 限制"}, 400)

		# Sanitize filename
		safe_name = Path(name).name.replace("..", "").replace("/", "").replace("\\", "")
		RESUME_DIR.mkdir(parents=True, exist_ok=True)
		dest = RESUME_DIR / safe_name
		dest.write_bytes(content)

		# Update config
		config = load_config(CONFIG_PATH)
		config.setdefault("profile", {})["resume_path"] = str(dest)
		with open(CONFIG_PATH, "w", encoding="utf-8") as f:
			yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

		return _json_response({
			"success": True,
			"filename": safe_name,
			"size": len(content),
			"path": str(dest)
		})
	except Exception as e:
		return _json_response({"error": str(e)}, 500)


@app.route("/api/resume", method="DELETE")
def api_resume_delete():
	try:
		import yaml
		config = load_config(CONFIG_PATH)
		resume_path = config.get("profile", {}).get("resume_path", "")

		if resume_path:
			p = Path(resume_path)
			if p.exists():
				p.unlink()

		# Clear config path
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
