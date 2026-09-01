import io
import json
import tempfile
import time
import unittest
from socketserver import ThreadingMixIn
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from unittest.mock import MagicMock, patch
from wsgiref.simple_server import WSGIServer
from zipfile import ZipFile

import yaml
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from bosshunter.db import (
    add_history,
    get_db,
    insert_job,
    update_job_greeting,
    update_job_score,
    update_job_status,
)
from bosshunter.throttle import SendWindowChecker
from bosshunter.web import server
from threading import Event, Lock

from bosshunter.scoring_run_store import create_scoring_run, get_scoring_run, update_scoring_run
from bosshunter.web.tasks import TaskAlreadyRunningError, WorkbenchTask, WorkbenchTaskRunner


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": "Product Manager",
        "company": "Example",
        "salary": "20-30K",
        "city": "Shanghai",
        "experience": "1-3 years",
        "jd": "Build AI product features",
        "hr_name": "HR",
        "hr_title": "Recruiter",
        "hr_active": "",
        "company_size": "",
        "company_industry": "",
        "url": "https://example.com/job",
    }


class WebApiRouteTests(unittest.TestCase):
    def setUp(self):
        # Arrange
        self.original_base_dir = server.BASE_DIR

    def tearDown(self):
        # Cleanup
        server.set_base_dir(self.original_base_dir)

    def _request(self, path: str, method: str = "GET", json_body: dict | None = None):
        if "?" in path:
            path_info, query_string = path.split("?", 1)
        else:
            path_info, query_string = path, ""

        status_headers = {}

        def start_response(status, headers, exc_info=None):
            status_headers["status"] = status
            status_headers["headers"] = dict(headers)

        request_body = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path_info,
            "QUERY_STRING": query_string,
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": "8686",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(request_body),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        if json_body is not None:
            environ["CONTENT_LENGTH"] = str(len(request_body))
            environ["CONTENT_TYPE"] = "application/json"

        response_iter = server.app(environ, start_response)
        try:
            body = b"".join(
                chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                for chunk in response_iter
            ).decode("utf-8")
        finally:
            close = getattr(response_iter, "close", None)
            if close:
                close()
        return status_headers["status"], status_headers["headers"], body

    def _upload_resume(self, filename: str, content: bytes, content_type: str):
        boundary = "----BossHunterResumeUpload"
        body = (
            (
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
            + content
            + f"\r\n--{boundary}--\r\n".encode("utf-8")
        )
        status_headers = {}

        def start_response(status, headers, exc_info=None):
            status_headers["status"] = status
            status_headers["headers"] = dict(headers)

        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/api/resume/upload",
            "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(body)),
            "CONTENT_TYPE": f"multipart/form-data; boundary={boundary}",
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": "8686",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(body),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }

        response_body = b"".join(
            chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
            for chunk in server.app(environ, start_response)
        ).decode("utf-8")
        return status_headers["status"], status_headers["headers"], response_body

    @patch.object(server.app, "run")
    def test_run_server_uses_threaded_wsgi_server(self, run):
        # Act
        server.run_server(open_browser=False)

        # Assert
        server_class = run.call_args.kwargs["server_class"]
        self.assertTrue(issubclass(server_class, ThreadingMixIn))
        self.assertTrue(issubclass(server_class, WSGIServer))
        self.assertTrue(server_class.daemon_threads)

    def test_web_api_missing_api_route_returns_json_404_not_spa_html(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            server.set_base_dir(Path(tmp))

            # Act
            status, headers, body = self._request("/api/does-not-exist")

        # Assert
        self.assertTrue(status.startswith("404"))
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(json.loads(body), {"error": "Not found"})
        self.assertNotIn("<!doctype html", body.lower())

    def test_web_assets_serve_javascript_with_windows_safe_mime_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            frontend_dir = Path(tmp)
            assets_dir = frontend_dir / "assets"
            assets_dir.mkdir()
            (assets_dir / "app.js").write_text("console.log('ok')\n", encoding="utf-8")

            with patch.object(server, "FRONTEND_DIR", frontend_dir):
                status, headers, body = self._request("/assets/app.js")

        self.assertTrue(status.startswith("200"))
        self.assertTrue(headers["Content-Type"].startswith("application/javascript"))
        self.assertIn("console.log", body)

    def test_web_api_workbench_preflight_full_returns_json_payload(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            resume_path = base_dir / "resume.md"
            resume_path.write_text("# Resume", encoding="utf-8")
            (base_dir / "config.yaml").write_text(
                yaml.dump(
                    {
                        "profile": {"resume_path": str(resume_path)},
                        "search": {"keywords": ["AI产品经理"]},
                        "ai": {"api_key": "test-api-key"},
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            server.set_base_dir(base_dir)

            # Act
            ready_checks = [
                {
                    "id": "environment",
                    "title": "运行环境",
                    "status": "pass",
                    "message": "启动检查已通过",
                    "detail": "测试环境已就绪",
                    "action": "",
                }
            ]
            with patch.object(server, "collect_preflight_checks", return_value=ready_checks):
                status, headers, body = self._request("/api/workbench/preflight?mode=full")

        # Assert
        self.assertTrue(status.startswith("200"))
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(json.loads(body), {"ok": True, "messages": [], "checks": ready_checks})

    def test_web_api_workbench_preflight_supports_rescore_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            resume_path = base_dir / "resume.md"
            resume_path.write_text("# Resume", encoding="utf-8")
            (base_dir / "config.yaml").write_text(
                yaml.dump(
                    {
                        "profile": {"resume_path": str(resume_path)},
                        "ai": {"api_key": "test-api-key"},
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            server.set_base_dir(base_dir)
            ready_checks = [
                {
                    "id": "ai_credentials",
                    "title": "AI API",
                    "status": "pass",
                    "message": "AI 已连接",
                    "detail": "",
                    "action": "",
                }
            ]

            with patch.object(server, "collect_preflight_checks", return_value=ready_checks) as collect:
                status, headers, body = self._request("/api/workbench/preflight?mode=rescore")

        self.assertTrue(status.startswith("200"))
        self.assertIn("application/json", headers["Content-Type"])
        self.assertTrue(json.loads(body)["ok"])
        self.assertEqual(collect.call_args.args[0], "rescore")

    def test_web_api_workbench_preflight_full_requires_ai_key(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            resume_path = base_dir / "resume.md"
            resume_path.write_text("# Resume", encoding="utf-8")
            (base_dir / "config.yaml").write_text(
                yaml.dump(
                    {
                        "profile": {"resume_path": str(resume_path)},
                        "search": {"keywords": ["AI产品经理"]},
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            server.set_base_dir(base_dir)

            # Act
            browser_ready = {
                "node": {"available": True, "version": "v22"},
                "runtime": True,
                "chrome": True,
                "targets": [],
                "boss_tab": None,
                "errors": [],
                "runtime_url": "http://127.0.0.1:3456",
                "health": {"runtime": "bosshunter"},
                "browser_product": "Chrome/138.0",
            }
            with (
                patch.dict("os.environ", {}, clear=True),
                patch("bosshunter.web.preflight.run_browser_diagnostics", return_value=browser_ready),
            ):
                status, headers, body = self._request("/api/workbench/preflight?mode=full")

        # Assert
        self.assertTrue(status.startswith("200"))
        self.assertIn("application/json", headers["Content-Type"])
        payload = json.loads(body)
        self.assertFalse(payload["ok"])
        self.assertTrue(any("尚未填写 AI API Key" in message for message in payload["messages"]))
        self.assertTrue(any(check["id"] == "ai_credentials" for check in payload["checks"]))

    def test_web_api_ai_diagnostics_returns_structured_feedback(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            (base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
            server.set_base_dir(base_dir)
            checks = [
                {
                    "id": "ai_credentials",
                    "title": "AI API Key",
                    "status": "error",
                    "message": "尚未填写 AI API Key",
                    "detail": "请填写 API Key。",
                    "action": "config",
                }
            ]

            # Act
            with patch.object(server, "check_ai_connection", return_value=checks):
                status, headers, body = self._request("/api/diagnostics/ai")

        # Assert
        payload = json.loads(body)
        self.assertTrue(status.startswith("200"), body)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["checks"], checks)
        self.assertIn("尚未填写 AI API Key", payload["messages"][0])

    def test_web_api_activity_returns_json_without_runtime_name_error(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            server.set_base_dir(Path(tmp))

            # Act
            status, headers, body = self._request("/api/activity?days=7")

        # Assert
        self.assertTrue(status.startswith("200"))
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(json.loads(body), [])

    def test_job_search_filters_keyword_score_salary_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                matching = _job("matching")
                matching.update({"title": "实施顾问", "salary": "5-8K", "jd": "负责 SQL 系统实施"})
                insert_job(db, matching)
                update_job_score(db, "matching", 82, "数据库技能匹配")
                update_job_status(db, "matching", "ready")

                wrong_status = _job("wrong-status")
                wrong_status.update({"title": "实施工程师", "salary": "8-13K", "jd": "需要 SQL"})
                insert_job(db, wrong_status)
                update_job_score(db, "wrong-status", 85, "数据库技能匹配")
                update_job_status(db, "wrong-status", "filtered")

                low_score = _job("low-score")
                low_score.update({"salary": "10-15K", "jd": "需要 SQL"})
                insert_job(db, low_score)
                update_job_score(db, "low-score", 60, "数据库技能匹配")
                update_job_status(db, "low-score", "ready")

                unrelated = _job("unrelated")
                unrelated.update({"salary": "10-15K", "jd": "负责客户培训"})
                insert_job(db, unrelated)
                update_job_score(db, "unrelated", 88, "沟通能力匹配")
                update_job_status(db, "unrelated", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            status, headers, body = self._request(
                "/api/jobs/search?q=SQL&min_score=71&salary_min=7&salary_max=13&status=ready&limit=15&offset=0"
            )

        payload = json.loads(body)
        self.assertTrue(status.startswith("200"), body)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual([job["id"] for job in payload["items"]], ["matching"])
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["all_total"], 4)
        self.assertEqual(payload["limit"], 15)
        self.assertEqual(payload["offset"], 0)

    def test_job_search_salary_overlap_excludes_unparseable_and_paginates(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                fixtures = [
                    ("high", "12K", 90),
                    ("middle", "10-15K", 85),
                    ("low", "5-8K", 80),
                    ("daily", "150-200元/天", 95),
                ]
                for job_id, salary, score in fixtures:
                    job = _job(job_id)
                    job["salary"] = salary
                    insert_job(db, job)
                    update_job_score(db, job_id, score, "匹配")
                    update_job_status(db, job_id, "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            status, _, body = self._request(
                "/api/jobs/search?salary_min=7&salary_max=13&limit=2&offset=1"
            )

        payload = json.loads(body)
        self.assertTrue(status.startswith("200"), body)
        self.assertEqual(payload["total"], 3)
        self.assertEqual([job["id"] for job in payload["items"]], ["middle", "low"])

    def test_job_search_supports_whitelisted_column_sorting(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                for job_id, score, education in (("low", 60, "本科"), ("high", 90, "博士")):
                    job = _job(job_id)
                    job["education"] = education
                    insert_job(db, job)
                    update_job_score(db, job_id, score, "评分")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            status, _, body = self._request("/api/jobs/search?sort_by=score&sort_order=asc")
            invalid_sort_status, _, invalid_sort_body = self._request(
                "/api/jobs/search?sort_by=score%20DESC&sort_order=asc"
            )
            invalid_order_status, _, invalid_order_body = self._request(
                "/api/jobs/search?sort_by=score&sort_order=sideways"
            )

        self.assertTrue(status.startswith("200"), body)
        self.assertEqual([job["id"] for job in json.loads(body)["items"]], ["low", "high"])
        self.assertTrue(invalid_sort_status.startswith("400"), invalid_sort_body)
        self.assertTrue(invalid_order_status.startswith("400"), invalid_order_body)

    def test_job_search_decodes_chinese_keyword_as_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                job = _job("chinese-keyword")
                job["company"] = "网易"
                insert_job(db, job)
            finally:
                db.close()
            server.set_base_dir(base_dir)

            status, _, body = self._request(f"/api/jobs/search?q={quote('网易')}")

        payload = json.loads(body)
        self.assertTrue(status.startswith("200"), body)
        self.assertEqual([job["id"] for job in payload["items"]], ["chinese-keyword"])

    def test_job_search_orders_newest_jobs_before_higher_scored_older_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("older-high-score"))
                update_job_score(db, "older-high-score", 95, "高分旧岗位")
                insert_job(db, _job("newer-low-score"))
                update_job_score(db, "newer-low-score", 60, "低分新岗位")
                now = datetime.now(UTC).replace(tzinfo=None)
                db.execute(
                    "UPDATE jobs SET created_at = ? WHERE id = ?",
                    ((now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"), "older-high-score"),
                )
                db.execute(
                    "UPDATE jobs SET created_at = ? WHERE id = ?",
                    (now.strftime("%Y-%m-%d %H:%M:%S"), "newer-low-score"),
                )
                db.commit()
            finally:
                db.close()
            server.set_base_dir(base_dir)

            status, _, body = self._request("/api/jobs/search")

        payload = json.loads(body)
        self.assertTrue(status.startswith("200"), body)
        self.assertEqual(
            [job["id"] for job in payload["items"]],
            ["newer-low-score", "older-high-score"],
        )

    def test_job_search_filters_jobs_by_collection_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                now = datetime.now(UTC).replace(tzinfo=None)
                fixtures = (
                    ("today", now),
                    ("recent", now - timedelta(days=2)),
                    ("older", now - timedelta(days=8)),
                )
                for job_id, created_at in fixtures:
                    insert_job(db, _job(job_id))
                    db.execute(
                        "UPDATE jobs SET created_at = ? WHERE id = ?",
                        (created_at.strftime("%Y-%m-%d %H:%M:%S"), job_id),
                    )
                db.commit()
            finally:
                db.close()
            server.set_base_dir(base_dir)

            status, _, body = self._request("/api/jobs/search?created_within=3d")
            today_status, _, today_body = self._request("/api/jobs/search?created_within=today")

        payload = json.loads(body)
        self.assertTrue(status.startswith("200"), body)
        self.assertEqual([job["id"] for job in payload["items"]], ["today", "recent"])
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["all_total"], 3)
        today_payload = json.loads(today_body)
        self.assertTrue(today_status.startswith("200"), today_body)
        self.assertEqual([job["id"] for job in today_payload["items"]], ["today"])

    def test_job_search_rejects_invalid_numeric_ranges(self):
        with tempfile.TemporaryDirectory() as tmp:
            server.set_base_dir(Path(tmp))
            paths = [
                "/api/jobs/search?min_score=not-a-number",
                "/api/jobs/search?salary_min=14&salary_max=7",
                "/api/jobs/search?limit=0",
                "/api/jobs/search?created_within=30d",
            ]

            for path in paths:
                status, headers, body = self._request(path)
                self.assertTrue(status.startswith("400"), body)
                self.assertIn("application/json", headers["Content-Type"])
                self.assertIn("error", json.loads(body))

    def test_legacy_jobs_endpoint_still_returns_an_array(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("legacy"))
            finally:
                db.close()
            server.set_base_dir(base_dir)

            status, _, body = self._request("/api/jobs?limit=100")

        self.assertTrue(status.startswith("200"), body)
        self.assertIsInstance(json.loads(body), list)

    def test_web_api_workbench_pending_confirmation_returns_ready_jobs(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("ready-job"))
                update_job_score(db, "ready-job", 82, "good match")
                update_job_status(db, "ready-job", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            # Act
            status, headers, body = self._request("/api/workbench")

        # Assert
        payload = json.loads(body)
        self.assertTrue(status.startswith("200"))
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual([job["id"] for job in payload["pending_confirmation"]], ["ready-job"])

    def test_workbench_excludes_collection_only_platforms_from_automatic_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                external = _job("zhilian-ready")
                external.update({"source_platform": "zhilian", "source_job_id": "zhilian-ready"})
                insert_job(db, external)
                update_job_score(db, "zhilian-ready", 90, "匹配")
                update_job_status(db, "zhilian-ready", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            status, _, body = self._request("/api/workbench")

        payload = json.loads(body)
        self.assertTrue(status.startswith("200"), body)
        self.assertEqual(payload["pending_confirmation"], [])

    def test_web_api_workbench_reports_daily_send_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("sent-today"))
                add_history(db, "sent-today", "sent", "已发送")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            status, _, body = self._request("/api/workbench")

        payload = json.loads(body)
        self.assertTrue(status.startswith("200"), body)
        self.assertEqual(payload["send_quota"], {
            "daily_limit": 30,
            "sent": 1,
            "remaining": 29,
            "exhausted": False,
        })

    def test_web_api_workbench_shows_approved_job_when_greeting_was_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("approved-without-greeting"))
                update_job_score(db, "approved-without-greeting", 84, "good match")
                update_job_status(db, "approved-without-greeting", "approved")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            status, _, body = self._request("/api/workbench")

        payload = json.loads(body)
        self.assertTrue(status.startswith("200"), body)
        self.assertEqual(
            [job["id"] for job in payload["pending_confirmation"]],
            ["approved-without-greeting"],
        )

    def test_workbench_returns_today_and_cumulative_funnel_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                # Funnel "today" uses the machine's local calendar day. Keep fixtures
                # in the same clock so this remains stable around local midnight.
                now = datetime.now()
                fixtures = (
                    ("today-ready", "ready", now),
                    ("today-sent", "sent", now - timedelta(hours=1)),
                    ("older-sent", "sent", now - timedelta(days=2)),
                )
                for job_id, job_status, created_at in fixtures:
                    insert_job(db, _job(job_id))
                    update_job_score(db, job_id, 80, "匹配")
                    update_job_status(db, job_id, job_status)
                    db.execute(
                        "UPDATE jobs SET created_at = ? WHERE id = ?",
                        (created_at.strftime("%Y-%m-%d %H:%M:%S"), job_id),
                    )
                db.commit()
            finally:
                db.close()
            server.set_base_dir(base_dir)

            status, _, body = self._request("/api/workbench")

        payload = json.loads(body)
        self.assertTrue(status.startswith("200"), body)
        self.assertEqual(payload["funnel"]["采集总数"], 3)
        self.assertEqual(payload["funnel"]["发送"], 2)
        self.assertEqual(payload["funnel_today"]["采集总数"], 2)
        self.assertEqual(payload["funnel_today"]["AI评分"], 2)
        self.assertEqual(payload["funnel_today"]["发送"], 1)
        self.assertEqual(len(payload["pending_confirmation"]), 1)

    def test_web_api_full_task_stays_running_while_waiting_for_frontend_confirmation(self):
        # Arrange
        confirmation_reached = False

        def fake_collect(task, config):
            nonlocal confirmation_reached
            confirmation_reached = True

        runner = WorkbenchTaskRunner()
        runner._executors["full"] = lambda task, config: server._execute_full(task, config)

        # Act
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("ready-job"))
                update_job_score(db, "ready-job", 82, "good match")
                update_job_status(db, "ready-job", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            with patch.object(server, "_execute_collect", side_effect=fake_collect):
                task = runner.start("full", {})
                for _ in range(20):
                    status = runner.status()
                    active = status["active"]
                    if active and "等待前端确认投递" in active["logs"]:
                        break
                    time.sleep(0.01)
                time.sleep(0.05)
                status = runner.status()
                active = status["active"]
                if active:
                    runner._tasks[task["id"]].stop_requested.set()
                    runner.wait(timeout=1)

        # Assert
        self.assertTrue(confirmation_reached)
        self.assertIsNotNone(active)
        self.assertEqual(active["id"], task["id"])
        self.assertEqual(active["status"], "running")
        self.assertIn("等待前端确认投递", active["logs"])

    def test_task_stop_keeps_active_slot_until_executor_really_returns(self):
        # Arrange
        started = Event()
        release = Event()

        def blocking_executor(task, config):
            started.set()
            release.wait(timeout=1)

        runner = WorkbenchTaskRunner({
            "collect": blocking_executor,
            "monitor": lambda task, config: None,
        })
        task = runner.start("collect", {})
        self.assertTrue(started.wait(timeout=1))

        try:
            # Act
            stopped = runner.stop(task["id"])
            status_after_stop = runner.status()
            with self.assertRaises(TaskAlreadyRunningError):
                runner.start("monitor", {})
            release.set()
            runner.wait(timeout=1)
            second_task = runner.start("monitor", {})
            runner.wait(timeout=1)
        finally:
            release.set()
            runner.wait(timeout=1)

        # Assert
        self.assertEqual(stopped["status"], "stopping")
        self.assertEqual(status_after_stop["active"]["id"], task["id"])
        self.assertEqual(status_after_stop["active"]["status"], "stopping")
        self.assertEqual(second_task["mode"], "monitor")

    def test_task_stop_wakes_monitor_interval_wait(self):
        # Arrange
        waiting = Event()
        release = Event()

        def monitor_executor(task, config):
            task.context["monitor_wakeup_event"] = release
            waiting.set()
            release.wait(timeout=1)

        runner = WorkbenchTaskRunner({"monitor": monitor_executor})
        task = runner.start("monitor", {})
        self.assertTrue(waiting.wait(timeout=1))

        # Act
        runner.stop(task["id"])
        runner.wait(timeout=0.5)
        result = runner.status()["last_task"]

        # Assert
        self.assertTrue(release.is_set())
        self.assertEqual(result["status"], "stopped")
        self.assertIsNone(runner.status()["active"])

    def test_task_runner_automatically_stops_at_send_window_deadline(self):
        # Arrange
        def wait_for_stop(task, config):
            task.stop_requested.wait(timeout=1)

        runner = WorkbenchTaskRunner({"monitor": wait_for_stop})
        deadline = datetime.now() + timedelta(milliseconds=50)

        # Act
        with patch("bosshunter.web.tasks._deadline_from_config", return_value=deadline):
            task = runner.start("monitor", {"throttle": {"send_windows": ["09:00-16:00"]}})
            runner.wait(timeout=1)
        result = runner.status()["last_task"]

        # Assert
        self.assertEqual(task["deadline_at"], deadline.isoformat(timespec="seconds"))
        self.assertEqual(result["status"], "stopped")
        self.assertTrue(result["stop_requested"])
        self.assertEqual(result["stop_reason"], "已到发送时间窗口截止时间，后台自动停止")
        self.assertIn(result["stop_reason"], result["logs"])

    def test_task_runner_does_not_start_after_today_deadline(self):
        # Arrange
        executed = Event()
        runner = WorkbenchTaskRunner({"monitor": lambda task, config: executed.set()})
        deadline = datetime.now() - timedelta(minutes=1)

        # Act
        with patch("bosshunter.web.tasks._deadline_from_config", return_value=deadline):
            task = runner.start("monitor", {"throttle": {"send_windows": ["09:00-16:00"]}})

        # Assert
        self.assertEqual(task["status"], "stopped")
        self.assertEqual(task["stop_reason"], "今日发送时间窗口已截止，后台未启动")
        self.assertFalse(executed.is_set())
        self.assertIsNone(runner.status()["active"])

    def test_send_window_checker_uses_last_window_end_as_daily_deadline(self):
        checker = SendWindowChecker(["09:00-12:00", "14:00-17:30", "99:00-100:00"])

        deadline = checker.latest_end_datetime(datetime(2026, 7, 28, 10, 15, 45))

        self.assertEqual(deadline, datetime(2026, 7, 28, 17, 30))

    def test_web_api_full_task_completes_when_no_jobs_need_confirmation(self):
        # Arrange
        calls = []

        def fake_collect(task, config):
            calls.append("collect")

        runner = WorkbenchTaskRunner()
        runner._executors["full"] = lambda task, config: server._execute_full(task, config)

        # Act
        with tempfile.TemporaryDirectory() as tmp:
            server.set_base_dir(Path(tmp))
            with patch.object(server, "_execute_collect", side_effect=fake_collect):
                task = runner.start("full", {})
                runner.wait(timeout=1)
                status = runner.status()
                last_task = status["last_task"]

        # Assert
        self.assertEqual(calls, ["collect"])
        self.assertIsNone(status["active"])
        self.assertEqual(last_task["id"], task["id"])
        self.assertEqual(last_task["status"], "completed")
        self.assertIn("没有待确认岗位，流程结束", last_task["logs"])

    def test_web_api_deliver_hands_selected_jobs_to_waiting_full_task(self):
        # Arrange
        confirmation_event = Event()
        full_task = WorkbenchTask(id="full-task", mode="full", label="运行全流程")
        full_task.context["waiting_confirmation"] = True
        full_task.context["confirmation_event"] = confirmation_event
        runner = WorkbenchTaskRunner()
        runner._tasks[full_task.id] = full_task

        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("ready-job"))
                update_job_status(db, "ready-job", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            body = json.dumps({"job_ids": ["ready-job"]}).encode("utf-8")
            status_headers = {}

            def start_response(status, headers, exc_info=None):
                status_headers["status"] = status
                status_headers["headers"] = dict(headers)

            environ = {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": "/api/workbench/deliver",
                "QUERY_STRING": "",
                "CONTENT_LENGTH": str(len(body)),
                "CONTENT_TYPE": "application/json",
                "SERVER_NAME": "127.0.0.1",
                "SERVER_PORT": "8686",
                "wsgi.version": (1, 0),
                "wsgi.url_scheme": "http",
                "wsgi.input": io.BytesIO(body),
                "wsgi.errors": io.StringIO(),
                "wsgi.multithread": False,
                "wsgi.multiprocess": False,
                "wsgi.run_once": False,
            }

            # Act
            with patch.object(server, "task_runner", runner):
                response_body = b"".join(
                    chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                    for chunk in server.app(environ, start_response)
                ).decode("utf-8")

        # Assert
        self.assertTrue(status_headers["status"].startswith("200"), response_body)
        self.assertTrue(confirmation_event.is_set())
        self.assertEqual(full_task.context["confirmed_job_ids"], ["ready-job"])
        self.assertEqual(json.loads(response_body)["id"], "full-task")

    def test_web_api_deliver_batch_continues_send_when_some_greetings_fail(self):
        task = WorkbenchTask(id="deliver-partial", mode="full", label="运行全流程")
        config = {
            "_workbench_job_ids": ["job-1", "job-2"],
            "_workbench_send_report": {
                "requested_count": 1,
                "sent_count": 1,
                "failed_count": 0,
                "deferred_count": 0,
                "quota_deferred_count": 0,
                "already_sent": 0,
                "daily_limit": 0,
                "remaining_quota": 0,
            },
        }
        logs: list[str] = []
        task.logs = logs

        with (
            patch("bosshunter.ai.greeter.generate_greetings", return_value=1) as generate,
            patch("bosshunter.executor.sender.send_greetings", return_value=1) as send,
        ):
            server._execute_deliver_batch(task, config)

        generate.assert_called_once()
        send.assert_called_once()
        self.assertTrue(any("未生成招呼语" in message and "手动填写" in message for message in logs))
        self.assertTrue(any("继续进入发送流程" in message for message in logs))
        self.assertEqual(task.metrics.get("send_success"), 1)

    def test_web_api_manual_sent_records_external_send_without_using_boss_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                external = _job("51job-manual")
                external.update({"source_platform": "51job", "source_job_id": "51job-manual"})
                insert_job(db, external)
            finally:
                db.close()
            server.set_base_dir(base_dir)

            status, _, body = self._request(
                "/api/jobs/manual-sent",
                method="POST",
                json_body={"job_ids": ["51job-manual"], "confirmed": True},
            )
            workbench_status, _, workbench_body = self._request("/api/workbench")
            verify_db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                row = verify_db.execute(
                    "SELECT status FROM jobs WHERE id = ?",
                    ("51job-manual",),
                ).fetchone()
                history = verify_db.execute(
                    "SELECT action FROM history WHERE job_id = ?",
                    ("51job-manual",),
                ).fetchall()
            finally:
                verify_db.close()

        self.assertTrue(status.startswith("200"), body)
        self.assertEqual(json.loads(body)["affected_count"], 1)
        self.assertTrue(workbench_status.startswith("200"), workbench_body)
        self.assertEqual(json.loads(workbench_body)["send_quota"]["sent"], 0)
        self.assertEqual(row["status"], "sent")
        self.assertEqual([item["action"] for item in history], ["manual_sent"])

    def test_web_api_deliver_rejects_already_sent_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("already-sent"))
                update_job_greeting(db, "already-sent", "已经发送过的招呼语")
                update_job_status(db, "already-sent", "sent")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            status, _, body = self._request(
                "/api/workbench/deliver",
                method="POST",
                json_body={"job_ids": ["already-sent"]},
            )

        self.assertTrue(status.startswith("409"), body)
        self.assertEqual(json.loads(body)["invalid_ids"], ["already-sent"])

    def test_web_api_direct_send_requires_a_retryable_greeting(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("error-without-greeting"))
                update_job_status(db, "error-without-greeting", "error")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            status, _, body = self._request(
                "/api/workbench/deliver",
                method="POST",
                json_body={"job_ids": ["error-without-greeting"], "direct_send": True},
            )

        self.assertTrue(status.startswith("409"), body)
        self.assertEqual(json.loads(body)["invalid_ids"], ["error-without-greeting"])

    def test_web_api_deliver_queues_confirmation_before_full_task_event_exists(self):
        full_task = WorkbenchTask(id="full-before-event", mode="full", label="运行全流程")
        runner = WorkbenchTaskRunner()
        runner._tasks[full_task.id] = full_task

        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("ready-job"))
                update_job_status(db, "ready-job", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            with patch.object(server, "task_runner", runner):
                status, _, body = self._request(
                    "/api/workbench/deliver",
                    method="POST",
                    json_body={"job_ids": ["ready-job"]},
                )

            verify_db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                job_status = verify_db.execute(
                    "SELECT status FROM jobs WHERE id = ?",
                    ("ready-job",),
                ).fetchone()["status"]
            finally:
                verify_db.close()

        self.assertTrue(status.startswith("200"), body)
        self.assertEqual(json.loads(body)["id"], "full-before-event")
        self.assertEqual(full_task.context["confirmed_job_ids"], ["ready-job"])
        self.assertTrue(full_task.context["delivery_requested"])
        self.assertEqual(job_status, "approved")

    def test_web_api_deliver_queues_jobs_while_full_task_is_monitoring(self):
        # Arrange
        wakeup_event = Event()
        full_task = WorkbenchTask(id="full-monitoring", mode="full", label="运行全流程")
        full_task.context.update({
            "monitoring": True,
            "monitor_queue_lock": Lock(),
            "monitor_wakeup_event": wakeup_event,
            "pending_deliveries": [],
        })
        runner = WorkbenchTaskRunner()
        runner._tasks[full_task.id] = full_task

        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("ready-job"))
                update_job_status(db, "ready-job", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            body = json.dumps({"job_ids": ["ready-job"]}).encode("utf-8")
            status_headers = {}

            def start_response(status, headers, exc_info=None):
                status_headers["status"] = status
                status_headers["headers"] = dict(headers)

            environ = {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": "/api/workbench/deliver",
                "QUERY_STRING": "",
                "CONTENT_LENGTH": str(len(body)),
                "CONTENT_TYPE": "application/json",
                "SERVER_NAME": "127.0.0.1",
                "SERVER_PORT": "8686",
                "wsgi.version": (1, 0),
                "wsgi.url_scheme": "http",
                "wsgi.input": io.BytesIO(body),
                "wsgi.errors": io.StringIO(),
                "wsgi.multithread": False,
                "wsgi.multiprocess": False,
                "wsgi.run_once": False,
            }

            # Act
            with patch.object(server, "task_runner", runner):
                response_body = b"".join(
                    chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                    for chunk in server.app(environ, start_response)
                ).decode("utf-8")

            verify_db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                status = verify_db.execute(
                    "SELECT status FROM jobs WHERE id = ?",
                    ("ready-job",),
                ).fetchone()["status"]
            finally:
                verify_db.close()

        # Assert
        self.assertTrue(status_headers["status"].startswith("200"), response_body)
        self.assertEqual(json.loads(response_body)["id"], "full-monitoring")
        self.assertEqual(status, "approved")
        self.assertTrue(wakeup_event.is_set())
        self.assertEqual(
            full_task.context["pending_deliveries"],
            [{"job_ids": ["ready-job"], "direct_send": False}],
        )

    def test_web_api_deliver_reuses_active_delivery_queue_instead_of_conflict(self):
        active_task = WorkbenchTask(id="active-delivery", mode="deliver", label="确认投递")
        active_task.context.update({
            "delivering": True,
            "delivery_queue_lock": Lock(),
            "delivery_scheduled_ids": {"already-scheduled"},
            "pending_deliveries": [],
        })
        runner = WorkbenchTaskRunner()
        runner._tasks[active_task.id] = active_task

        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                for job_id in ("already-scheduled", "new-ready"):
                    insert_job(db, _job(job_id))
                    update_job_status(db, job_id, "ready")
                    update_job_greeting(db, job_id, f"{job_id} 的待发送招呼语")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            with patch.object(server, "task_runner", runner):
                status, _, body = self._request(
                    "/api/workbench/deliver",
                    method="POST",
                    json_body={"job_ids": ["already-scheduled", "new-ready"], "direct_send": True},
                )

        payload = json.loads(body)
        self.assertTrue(status.startswith("200"), body)
        self.assertEqual(payload["id"], "active-delivery")
        self.assertEqual(payload["queued_count"], 1)
        self.assertEqual(payload["already_queued_count"], 1)
        self.assertEqual(
            active_task.context["pending_deliveries"],
            [{"job_ids": ["new-ready"], "direct_send": True}],
        )

    def test_execute_deliver_drains_batches_added_to_active_queue(self):
        task = WorkbenchTask(id="delivery-task", mode="deliver", label="确认投递")
        task.context["pending_deliveries"] = [
            {"job_ids": ["queued-job"], "direct_send": True}
        ]
        config = {"_workbench_job_ids": ["initial-job"], "throttle": {}}

        with patch.object(server, "_execute_deliver_batch") as execute_batch:
            server._execute_deliver(task, config)

        self.assertEqual(execute_batch.call_count, 2)
        self.assertEqual(execute_batch.call_args_list[0].args[1]["_workbench_job_ids"], ["initial-job"])
        self.assertEqual(execute_batch.call_args_list[1].args[1]["_workbench_job_ids"], ["queued-job"])
        self.assertTrue(execute_batch.call_args_list[1].args[1]["_workbench_skip_greeting"])

    def test_monitor_loop_processes_queued_delivery_before_next_check(self):
        # Arrange
        task = WorkbenchTask(id="monitoring-task", mode="full", label="运行全流程")
        task.context.update({
            "monitor_queue_lock": Lock(),
            "monitor_wakeup_event": Event(),
            "pending_deliveries": [
                {"job_ids": ["approved-a", "approved-b"], "direct_send": False}
            ],
        })

        def stop_after_monitor(_config):
            task.stop_requested.set()

        # Act
        with patch.object(server, "_execute_deliver") as execute_deliver, \
             patch(
                 "bosshunter.executor.monitor.monitor_and_send_resumes",
                 side_effect=stop_after_monitor,
             ):
            server._execute_monitor(task, {"monitor": {"interval": 30}})

        # Assert
        execute_deliver.assert_called_once()
        deliver_config = execute_deliver.call_args.args[1]
        self.assertEqual(
            deliver_config["_workbench_job_ids"],
            ["approved-a", "approved-b"],
        )

    def test_web_api_deliver_ignores_stale_stopped_full_task_waiting_context(self):
        # Arrange
        stale_event = Event()
        stale_task = WorkbenchTask(id="stale-full-task", mode="full", label="运行全流程", status="stopped")
        stale_task.context["waiting_confirmation"] = True
        stale_task.context["confirmation_event"] = stale_event

        active_event = Event()
        active_task = WorkbenchTask(id="active-full-task", mode="full", label="运行全流程")
        active_task.context["waiting_confirmation"] = True
        active_task.context["confirmation_event"] = active_event

        runner = WorkbenchTaskRunner()
        runner._tasks[stale_task.id] = stale_task
        runner._tasks[active_task.id] = active_task

        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("ready-job"))
                update_job_status(db, "ready-job", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            body = json.dumps({"job_ids": ["ready-job"]}).encode("utf-8")
            status_headers = {}

            def start_response(status, headers, exc_info=None):
                status_headers["status"] = status
                status_headers["headers"] = dict(headers)

            environ = {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": "/api/workbench/deliver",
                "QUERY_STRING": "",
                "CONTENT_LENGTH": str(len(body)),
                "CONTENT_TYPE": "application/json",
                "SERVER_NAME": "127.0.0.1",
                "SERVER_PORT": "8686",
                "wsgi.version": (1, 0),
                "wsgi.url_scheme": "http",
                "wsgi.input": io.BytesIO(body),
                "wsgi.errors": io.StringIO(),
                "wsgi.multithread": False,
                "wsgi.multiprocess": False,
                "wsgi.run_once": False,
            }

            # Act
            with patch.object(server, "task_runner", runner):
                response_body = b"".join(
                    chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                    for chunk in server.app(environ, start_response)
                ).decode("utf-8")

        # Assert
        self.assertTrue(status_headers["status"].startswith("200"), response_body)
        self.assertFalse(stale_event.is_set())
        self.assertTrue(active_event.is_set())
        self.assertNotIn("confirmed_job_ids", stale_task.context)
        self.assertEqual(active_task.context["confirmed_job_ids"], ["ready-job"])
        self.assertEqual(json.loads(response_body)["id"], "active-full-task")

    def test_web_api_deliver_conflict_does_not_change_job_or_history(self):
        active_task = WorkbenchTask(id="active-delivery", mode="deliver", label="确认投递")
        runner = WorkbenchTaskRunner()
        runner._tasks[active_task.id] = active_task

        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("ready-job"))
                update_job_status(db, "ready-job", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            with patch.object(server, "task_runner", runner):
                response_status, _, response_body = self._request(
                    "/api/workbench/deliver",
                    method="POST",
                    json_body={"job_ids": ["ready-job"]},
                )

            verify_db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                job_status = verify_db.execute(
                    "SELECT status FROM jobs WHERE id = ?",
                    ("ready-job",),
                ).fetchone()["status"]
                approved_history = verify_db.execute(
                    "SELECT COUNT(*) FROM history WHERE job_id = ? AND action = ?",
                    ("ready-job", "approved"),
                ).fetchone()[0]
            finally:
                verify_db.close()

        self.assertTrue(response_status.startswith("409"), response_body)
        self.assertEqual(job_status, "ready")
        self.assertEqual(approved_history, 0)

    def test_web_api_full_task_continues_delivery_and_monitoring_after_confirmation(self):
        # Arrange
        calls = []

        def fake_collect(task, config):
            calls.append("collect")

        def fake_deliver(task, config):
            calls.append((
                "deliver",
                config.get("_workbench_job_ids"),
                config.get("throttle", {}).get("daily_limit"),
            ))

        def fake_monitor(task, config, **kwargs):
            calls.append("monitor")

        runner = WorkbenchTaskRunner()
        runner._executors["full"] = lambda task, config: server._execute_full(task, config)

        # Act
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("ready-a"))
                update_job_score(db, "ready-a", 88, "good match")
                update_job_status(db, "ready-a", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            with patch.object(server, "_execute_collect", side_effect=fake_collect), \
                 patch.object(server, "_execute_deliver", side_effect=fake_deliver), \
                 patch.object(server, "_execute_monitor", side_effect=fake_monitor), \
                 patch.object(server, "load_config", return_value={"throttle": {"daily_limit": 40}}):
                task = runner.start("full", {})
                for _ in range(50):
                    running_task = runner._tasks[task["id"]]
                    confirmation_event = running_task.context.get("confirmation_event")
                    if isinstance(confirmation_event, Event):
                        running_task.context["confirmed_job_ids"] = ["ready-a", "ready-b"]
                        confirmation_event.set()
                        break
                    time.sleep(0.01)
                runner.wait(timeout=1)

        # Assert
        self.assertEqual(
            calls,
            ["collect", ("deliver", ["ready-a", "ready-b"], 40), "monitor"],
        )

    def test_full_task_consumes_confirmation_queued_before_event_creation(self):
        calls = []
        task = WorkbenchTask(id="queued-before-event", mode="full", label="运行全流程")
        task.context.update({
            "confirmed_job_ids": ["approved-a"],
            "delivery_requested": True,
        })

        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("approved-a"))
                update_job_score(db, "approved-a", 88, "good match")
                update_job_status(db, "approved-a", "approved")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            with patch.object(server, "_execute_collect", side_effect=lambda *_: calls.append("collect")), \
                 patch.object(
                     server,
                     "_execute_deliver",
                     side_effect=lambda _task, config: calls.append(("deliver", config["_workbench_job_ids"])),
                 ), \
                 patch.object(server, "_execute_monitor", side_effect=lambda *_, **__: calls.append("monitor")), \
                 patch.object(server, "load_config", return_value={}):
                server._execute_full(task, {})

        self.assertEqual(calls, ["collect", ("deliver", ["approved-a"]), "monitor"])
        self.assertFalse(task.context["waiting_confirmation"])
        self.assertTrue(task.context["confirmation_complete"])

    def test_full_task_sends_previous_confirmed_backlog_before_collecting(self):
        # Arrange
        calls = []
        task = WorkbenchTask(id="backlog-first", mode="full", label="运行全流程")

        def fake_deliver(_task, deliver_config):
            calls.append((
                "deliver",
                deliver_config.get("_workbench_job_ids"),
                deliver_config.get("_workbench_skip_greeting"),
                deliver_config.get("throttle", {}).get("daily_limit"),
            ))

        def fake_collect(_task, _config):
            calls.append("collect")

        # Act
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("deferred-ready"))
                update_job_status(db, "deferred-ready", "ready")
                update_job_greeting(db, "deferred-ready", "您好，我对这个岗位很感兴趣。")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            with patch.object(server, "_execute_deliver", side_effect=fake_deliver), \
                 patch.object(server, "_execute_collect", side_effect=fake_collect), \
                 patch.object(server, "load_config", return_value={"throttle": {"daily_limit": 40}}):
                server._execute_full(task, {})

        # Assert
        self.assertEqual(
            calls,
            [("deliver", ["deferred-ready"], True, 40), "collect"],
        )
        self.assertIn("优先续发上次已确认但未完成的 1 个岗位", task.logs)

    def test_deliver_keeps_partial_result_and_continues_after_single_failure(self):
        # Arrange
        task = WorkbenchTask(id="partial-delivery", mode="full", label="运行全流程")
        config = {"_workbench_job_ids": ["job-a", "job-b", "job-c"]}

        def fake_send(send_config, force=False):
            send_config["_workbench_send_report"] = {
                "sent_count": 1,
                "failed_count": 1,
                "deferred_count": 1,
                "quota_deferred_count": 1,
                "stop_reason": "daily_limit",
            }
            return 1

        # Act: a partial result must not raise and abort the full workflow.
        with patch("bosshunter.ai.greeter.generate_greetings", return_value=3), \
             patch("bosshunter.executor.sender.send_greetings", side_effect=fake_send):
            server._execute_deliver(task, config)

        # Assert
        self.assertIn("招呼语发送结果：成功 1，失败 1，待下次发送 1（共 3）", task.logs)
        self.assertIn("1 个岗位发送失败已单独记录，继续后续流程", task.logs)
        self.assertIn("1 个岗位因今日发送额度未执行，已保留在“待发送招呼语”", task.logs)
        self.assertEqual(task.stop_reason, "daily_limit")
        self.assertEqual(task.metrics["send_success"], 1)
        self.assertEqual(task.metrics["send_deferred"], 1)

    def test_deliver_counts_preserved_greetings_as_ready(self):
        task = WorkbenchTask(id="preserved-greeting", mode="deliver", label="投递")
        config = {"_workbench_job_ids": ["job-a", "job-b", "job-c"]}

        def fake_generate(greeting_config):
            greeting_config["_workbench_greeting_report"] = {"skipped_existing": 1}
            return 1

        def fake_send(send_config, force=False):
            send_config["_workbench_send_report"] = {"sent_count": 2}
            return 2

        with patch("bosshunter.ai.greeter.generate_greetings", side_effect=fake_generate), \
             patch("bosshunter.executor.sender.send_greetings", side_effect=fake_send):
            server._execute_deliver(task, config)

        self.assertIn("招呼语准备完成：2/3（新生成 1）", task.logs)
        self.assertTrue(any("1 个岗位未生成招呼语" in message for message in task.logs))
        self.assertFalse(any("2 个岗位未生成招呼语" in message for message in task.logs))

    def test_deliver_still_stops_on_account_risk_signal(self):
        # Arrange
        task = WorkbenchTask(id="risk-delivery", mode="full", label="运行全流程")
        config = {"_workbench_job_ids": ["job-a", "job-b"]}

        def fake_send(send_config, force=False):
            send_config["_workbench_send_report"] = {
                "sent_count": 0,
                "failed_count": 1,
                "deferred_count": 1,
                "quota_deferred_count": 0,
                "stop_reason": "captcha",
            }
            return 0

        # Act / Assert
        with patch("bosshunter.ai.greeter.generate_greetings", return_value=2), \
             patch("bosshunter.executor.sender.send_greetings", side_effect=fake_send), \
             self.assertRaisesRegex(RuntimeError, "验证码"):
            server._execute_deliver(task, config)

    def test_web_api_workbench_reject_marks_selected_ready_jobs_rejected(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("reject-a"))
                update_job_score(db, "reject-a", 82, "good match")
                update_job_status(db, "reject-a", "ready")

                insert_job(db, _job("reject-b"))
                update_job_score(db, "reject-b", 72, "ok match")
                update_job_status(db, "reject-b", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            body = json.dumps({"job_ids": ["reject-a", "reject-b"]}).encode("utf-8")
            status_headers = {}

            def start_response(status, headers, exc_info=None):
                status_headers["status"] = status
                status_headers["headers"] = dict(headers)

            environ = {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": "/api/workbench/reject",
                "QUERY_STRING": "",
                "CONTENT_LENGTH": str(len(body)),
                "CONTENT_TYPE": "application/json",
                "SERVER_NAME": "127.0.0.1",
                "SERVER_PORT": "8686",
                "wsgi.version": (1, 0),
                "wsgi.url_scheme": "http",
                "wsgi.input": io.BytesIO(body),
                "wsgi.errors": io.StringIO(),
                "wsgi.multithread": False,
                "wsgi.multiprocess": False,
                "wsgi.run_once": False,
            }

            # Act
            response_body = b"".join(
                chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                for chunk in server.app(environ, start_response)
            ).decode("utf-8")

            verify_db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                statuses = {
                    row["id"]: row["status"]
                    for row in verify_db.execute(
                        "SELECT id, status FROM jobs WHERE id IN ('reject-a', 'reject-b')"
                    ).fetchall()
                }
                history_actions = [
                    dict(row)
                    for row in verify_db.execute(
                        "SELECT job_id, action, detail FROM history ORDER BY id"
                    ).fetchall()
                ]
            finally:
                verify_db.close()

        # Assert
        self.assertTrue(status_headers["status"].startswith("200"), response_body)
        self.assertEqual(json.loads(response_body), {"success": True, "count": 2})
        self.assertEqual(statuses, {"reject-a": "rejected", "reject-b": "rejected"})
        self.assertEqual(
            history_actions,
            [
                {"job_id": "reject-a", "action": "rejected", "detail": "Web Dashboard 放弃投递"},
                {"job_id": "reject-b", "action": "rejected", "detail": "Web Dashboard 放弃投递"},
            ],
        )

    def test_web_api_workbench_reject_removes_jobs_from_pending_confirmation(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("reject-visible"))
                update_job_score(db, "reject-visible", 82, "good match")
                update_job_status(db, "reject-visible", "ready")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            body = json.dumps({"job_ids": ["reject-visible"]}).encode("utf-8")
            status_headers = {}

            def start_response(status, headers, exc_info=None):
                status_headers["status"] = status
                status_headers["headers"] = dict(headers)

            environ = {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": "/api/workbench/reject",
                "QUERY_STRING": "",
                "CONTENT_LENGTH": str(len(body)),
                "CONTENT_TYPE": "application/json",
                "SERVER_NAME": "127.0.0.1",
                "SERVER_PORT": "8686",
                "wsgi.version": (1, 0),
                "wsgi.url_scheme": "http",
                "wsgi.input": io.BytesIO(body),
                "wsgi.errors": io.StringIO(),
                "wsgi.multithread": False,
                "wsgi.multiprocess": False,
                "wsgi.run_once": False,
            }

            # Act
            response_body = b"".join(
                chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                for chunk in server.app(environ, start_response)
            ).decode("utf-8")
            workbench_status, _, workbench_body = self._request("/api/workbench")

        # Assert
        self.assertTrue(status_headers["status"].startswith("200"), response_body)
        self.assertTrue(workbench_status.startswith("200"), workbench_body)
        self.assertEqual(json.loads(workbench_body)["pending_confirmation"], [])

    def test_web_api_resume_delete_only_detaches_config_and_keeps_master_resume_file(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            resume_path = base_dir / "data" / "resumes" / "_AI_Homepage.md"
            resume_path.parent.mkdir(parents=True, exist_ok=True)
            resume_path.write_text("# 主简历\n\n完整事实库，不能删减。\n", encoding="utf-8")
            (base_dir / "config.yaml").write_text(
                yaml.dump({"profile": {"resume_path": str(resume_path)}}, allow_unicode=True),
                encoding="utf-8",
            )
            server.set_base_dir(base_dir)

            # Act
            status, _, body = self._request("/api/resume", method="DELETE")
            config = yaml.safe_load((base_dir / "config.yaml").read_text(encoding="utf-8"))

            # Assert
            self.assertTrue(status.startswith("200"), body)
            self.assertEqual(json.loads(body), {"success": True})
            self.assertTrue(resume_path.exists())
            self.assertEqual(config["profile"]["resume_path"], "")

    def test_web_api_resume_upload_preserves_chinese_markdown_filename(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            (base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
            server.set_base_dir(base_dir)
            content = "# 张三\n\n产品经理\n".encode("utf-8")

            # Act
            status, _, body = self._upload_resume("张三的中文简历.md", content, "text/markdown")
            payload = json.loads(body)
            stored_path = Path(payload["path"])
            config = yaml.safe_load((base_dir / "config.yaml").read_text(encoding="utf-8"))

            # Assert
            self.assertTrue(status.startswith("200"), body)
            self.assertEqual(payload["filename"], "张三的中文简历.md")
            self.assertEqual(stored_path.read_bytes(), content)
            self.assertEqual(config["profile"]["resume_path"], str(stored_path))

    def test_web_api_resume_upload_converts_docx_to_markdown(self):
        # Arrange
        document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p>
              <w:pPr><w:pStyle w:val="Title"/></w:pPr>
              <w:r><w:t>李雷</w:t></w:r>
            </w:p>
            <w:p>
              <w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr>
              <w:r><w:t>5 年产品经验</w:t></w:r>
            </w:p>
          </w:body>
        </w:document>"""
        docx_buffer = io.BytesIO()
        with ZipFile(docx_buffer, "w") as archive:
            archive.writestr("word/document.xml", document_xml)

        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            (base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
            server.set_base_dir(base_dir)

            # Act
            status, _, body = self._upload_resume(
                "李雷简历.docx",
                docx_buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            payload = json.loads(body)
            stored_path = Path(payload["path"])
            stored_content = stored_path.read_text(encoding="utf-8")

            # Assert
            self.assertTrue(status.startswith("200"), body)
            self.assertEqual(payload["filename"], "李雷简历.md")
            self.assertEqual(stored_path.suffix, ".md")
            self.assertIn("# 李雷", stored_content)
            self.assertIn("- 5 年产品经验", stored_content)

    def test_web_api_resume_upload_extracts_text_layer_from_pdf(self):
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        contents = DecodedStreamObject()
        contents.set_data(b"BT /F1 12 Tf 72 720 Td (Jane Resume - Product Manager) Tj ET")
        page[NameObject("/Contents")] = contents
        pdf = io.BytesIO()
        writer.write(pdf)

        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            (base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
            server.set_base_dir(base_dir)

            status, _, body = self._upload_resume("resume.pdf", pdf.getvalue(), "application/pdf")
            payload = json.loads(body)
            stored_path = Path(payload["path"])
            stored_text = stored_path.read_text(encoding="utf-8")

        self.assertTrue(status.startswith("200"), body)
        self.assertEqual(payload["filename"], "resume.md")
        self.assertIn("Jane Resume - Product Manager", stored_text)

    def test_web_api_resume_upload_rejects_encrypted_pdf(self):
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.encrypt("secret")
        pdf = io.BytesIO()
        writer.write(pdf)

        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            (base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
            server.set_base_dir(base_dir)
            status, _, body = self._upload_resume("encrypted.pdf", pdf.getvalue(), "application/pdf")

        self.assertTrue(status.startswith("400"), body)
        self.assertEqual(json.loads(body), {"error": "PDF 已加密，请上传未加密的简历"})

    def test_web_api_resume_upload_rejects_damaged_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            (base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
            server.set_base_dir(base_dir)
            status, _, body = self._upload_resume("damaged.pdf", b"%PDF-1.7\nbroken", "application/pdf")

        self.assertTrue(status.startswith("400"), body)
        self.assertEqual(json.loads(body), {"error": "PDF 文件无效或已损坏"})

    def test_web_api_resume_upload_rejects_scanned_pdf_without_text_layer(self):
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        pdf = io.BytesIO()
        writer.write(pdf)

        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            (base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
            server.set_base_dir(base_dir)
            status, _, body = self._upload_resume("scanned.pdf", pdf.getvalue(), "application/pdf")

        self.assertTrue(status.startswith("400"), body)
        self.assertIn("扫描版或无文字层", json.loads(body)["error"])

    def test_web_api_resume_upload_rejects_legacy_doc_format(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            (base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
            server.set_base_dir(base_dir)

            # Act
            status, _, body = self._upload_resume("旧版简历.doc", b"not-a-word-file", "application/msword")

            # Assert
            self.assertTrue(status.startswith("400"), body)
            self.assertEqual(json.loads(body), {"error": "仅支持 .md、.docx 或 .pdf 格式"})

    def test_web_api_history_open_chat_selects_the_recorded_boss_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            (base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("open-chat"))
                add_history(db, "open-chat", "replied", "已回复")
                history_id = db.execute(
                    "SELECT id FROM history WHERE job_id = ?",
                    ("open-chat",),
                ).fetchone()["id"]
            finally:
                db.close()
            server.set_base_dir(base_dir)

            with patch.object(server.task_runner, "status", return_value={"active": None}), \
                 patch(
                     "bosshunter.executor.monitor._open_conversation_from_chat_list",
                     return_value="chat-target",
                 ) as open_conversation:
                status, _, body = self._request(
                    f"/api/history/{history_id}/open-chat",
                    method="POST",
                    json_body={},
                )

        self.assertTrue(status.startswith("200"), body)
        self.assertTrue(json.loads(body)["success"])
        selected_job = open_conversation.call_args.args[0]
        self.assertEqual(selected_job["id"], "open-chat")
        self.assertFalse(open_conversation.call_args.kwargs["background"])

    def test_web_api_detected_reply_prepares_only_that_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            (base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("prepare-reply"))
                add_history(db, "prepare-reply", "hr_reply_detected", "检测到新消息")
                history_id = db.execute(
                    "SELECT id FROM history WHERE job_id = ?",
                    ("prepare-reply",),
                ).fetchone()["id"]
            finally:
                db.close()
            server.set_base_dir(base_dir)

            with patch.object(server.task_runner, "status", return_value={"active": None}), \
                 patch(
                     "bosshunter.executor.monitor.process_detected_reply",
                     return_value={"pending": 1, "failed": 0},
                 ) as process_reply:
                status, _, body = self._request(
                    f"/api/history/{history_id}/prepare-reply",
                    method="POST",
                    json_body={},
                )
                process_reply.return_value = {"pending": 0, "failed": 1}
                failed_status, _, failed_body = self._request(
                    f"/api/history/{history_id}/prepare-reply",
                    method="POST",
                    json_body={},
                )

        self.assertTrue(status.startswith("200"), body)
        self.assertTrue(json.loads(body)["success"])
        self.assertTrue(failed_status.startswith("502"), failed_body)
        self.assertEqual(process_reply.call_count, 2)
        self.assertEqual(process_reply.call_args.args[0], "prepare-reply")

    def test_web_api_history_dismiss_reply_adds_dismissed_history_without_rejecting_job(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("reply-dismiss"))
                update_job_status(db, "reply-dismiss", "sent")
                add_history(db, "reply-dismiss", "reply_pending", "AI建议回复")
                history_id = db.execute(
                    "SELECT id FROM history WHERE job_id = ? AND action = ?",
                    ("reply-dismiss", "reply_pending"),
                ).fetchone()["id"]
            finally:
                db.close()
            server.set_base_dir(base_dir)

            body = b"{}"
            status_headers = {}

            def start_response(status, headers, exc_info=None):
                status_headers["status"] = status
                status_headers["headers"] = dict(headers)

            environ = {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": f"/api/history/{history_id}/dismiss",
                "QUERY_STRING": "",
                "CONTENT_LENGTH": str(len(body)),
                "CONTENT_TYPE": "application/json",
                "SERVER_NAME": "127.0.0.1",
                "SERVER_PORT": "8686",
                "wsgi.version": (1, 0),
                "wsgi.url_scheme": "http",
                "wsgi.input": io.BytesIO(body),
                "wsgi.errors": io.StringIO(),
                "wsgi.multithread": False,
                "wsgi.multiprocess": False,
                "wsgi.run_once": False,
            }

            # Act
            response_body = b"".join(
                chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                for chunk in server.app(environ, start_response)
            ).decode("utf-8")

            verify_db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                job_status = verify_db.execute(
                    "SELECT status FROM jobs WHERE id = ?", ("reply-dismiss",)
                ).fetchone()["status"]
                history_actions = [
                    dict(row)
                    for row in verify_db.execute(
                        "SELECT job_id, action, detail FROM history ORDER BY id"
                    ).fetchall()
                ]
            finally:
                verify_db.close()

        # Assert
        self.assertTrue(status_headers["status"].startswith("200"), response_body)
        self.assertEqual(json.loads(response_body), {"success": True})
        self.assertEqual(job_status, "sent")
        self.assertEqual(history_actions[0], {"job_id": "reply-dismiss", "action": "reply_pending", "detail": "AI建议回复"})
        self.assertEqual(history_actions[1]["job_id"], "reply-dismiss")
        self.assertEqual(history_actions[1]["action"], "reply_dismissed")
        self.assertIn("Web Dashboard 放弃回复建议", history_actions[1]["detail"])

    def test_web_api_history_reply_records_resolution_history(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("reply-confirm"))
                update_job_status(db, "reply-confirm", "sent")
                add_history(db, "reply-confirm", "reply_pending", "AI建议回复")
                history_id = db.execute(
                    "SELECT id FROM history WHERE job_id = ? AND action = ?",
                    ("reply-confirm", "reply_pending"),
                ).fetchone()["id"]
            finally:
                db.close()
            server.set_base_dir(base_dir)

            body = json.dumps({"message": "已手动回复 HR"}).encode("utf-8")
            status_headers = {}

            def start_response(status, headers, exc_info=None):
                status_headers["status"] = status
                status_headers["headers"] = dict(headers)

            environ = {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": f"/api/history/{history_id}/reply",
                "QUERY_STRING": "",
                "CONTENT_LENGTH": str(len(body)),
                "CONTENT_TYPE": "application/json",
                "SERVER_NAME": "127.0.0.1",
                "SERVER_PORT": "8686",
                "wsgi.version": (1, 0),
                "wsgi.url_scheme": "http",
                "wsgi.input": io.BytesIO(body),
                "wsgi.errors": io.StringIO(),
                "wsgi.multithread": False,
                "wsgi.multiprocess": False,
                "wsgi.run_once": False,
            }

            # Act
            response_body = b"".join(
                chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                for chunk in server.app(environ, start_response)
            ).decode("utf-8")

            verify_db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                history_actions = [
                    dict(row)
                    for row in verify_db.execute(
                        "SELECT job_id, action, detail FROM history ORDER BY id"
                    ).fetchall()
                ]
                job_status = verify_db.execute(
                    "SELECT status FROM jobs WHERE id = ?", ("reply-confirm",)
                ).fetchone()["status"]
            finally:
                verify_db.close()

        # Assert
        self.assertTrue(status_headers["status"].startswith("200"), response_body)
        self.assertEqual(json.loads(response_body)["success"], True)
        self.assertEqual(job_status, "replied")
        self.assertEqual(history_actions[0], {"job_id": "reply-confirm", "action": "reply_pending", "detail": "AI建议回复"})
        self.assertEqual(history_actions[1]["job_id"], "reply-confirm")
        self.assertEqual(history_actions[1]["action"], "replied")
        self.assertIn("已手动回复 HR", history_actions[1]["detail"])

    def test_web_api_history_reply_confirmation_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("reply-once"))
                add_history(db, "reply-once", "reply_pending", "AI建议回复")
                history_id = db.execute(
                    "SELECT id FROM history WHERE job_id = ? AND action = 'reply_pending'",
                    ("reply-once",),
                ).fetchone()["id"]
            finally:
                db.close()
            server.set_base_dir(base_dir)

            first_status, _, first_body = self._request(
                f"/api/history/{history_id}/reply",
                method="POST",
                json_body={"message": "已手动回复 HR"},
            )
            second_status, _, second_body = self._request(
                f"/api/history/{history_id}/reply",
                method="POST",
                json_body={"message": "已手动回复 HR"},
            )

            verify_db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                reply_count = verify_db.execute(
                    "SELECT COUNT(*) FROM history WHERE job_id = ? AND action = 'replied'",
                    ("reply-once",),
                ).fetchone()[0]
            finally:
                verify_db.close()

        self.assertTrue(first_status.startswith("200"), first_body)
        self.assertTrue(second_status.startswith("200"), second_body)
        self.assertTrue(json.loads(second_body)["already_resolved"])
        self.assertEqual(reply_count, 1)

    def test_web_api_history_dismiss_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("dismiss-once"))
                add_history(db, "dismiss-once", "reply_pending", "AI建议回复")
                history_id = db.execute(
                    "SELECT id FROM history WHERE job_id = ? AND action = 'reply_pending'",
                    ("dismiss-once",),
                ).fetchone()["id"]
            finally:
                db.close()
            server.set_base_dir(base_dir)

            first_status, _, first_body = self._request(
                f"/api/history/{history_id}/dismiss",
                method="POST",
                json_body={},
            )
            second_status, _, second_body = self._request(
                f"/api/history/{history_id}/dismiss",
                method="POST",
                json_body={},
            )

            verify_db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                dismiss_count = verify_db.execute(
                    "SELECT COUNT(*) FROM history WHERE job_id = ? AND action = 'reply_dismissed'",
                    ("dismiss-once",),
                ).fetchone()[0]
            finally:
                verify_db.close()

        self.assertTrue(first_status.startswith("200"), first_body)
        self.assertTrue(second_status.startswith("200"), second_body)
        self.assertTrue(json.loads(second_body)["already_resolved"])
        self.assertEqual(dismiss_count, 1)

    def test_web_api_history_dismiss_rejects_stale_reply_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("dismiss-stale"))
                add_history(db, "dismiss-stale", "reply_pending", "第一轮建议")
                stale_history_id = db.execute(
                    "SELECT id FROM history WHERE job_id = ? AND action = 'reply_pending'",
                    ("dismiss-stale",),
                ).fetchone()["id"]
                add_history(db, "dismiss-stale", "reply_pending", "第二轮建议")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            status, _, body = self._request(
                f"/api/history/{stale_history_id}/dismiss",
                method="POST",
                json_body={},
            )

        self.assertTrue(status.startswith("409"), body)
        self.assertIn("最新一轮", json.loads(body)["error"])

    def test_web_api_unresolved_count_includes_resume_failures_and_excludes_resolved_rows(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("reply-open"))
                insert_job(db, _job("reply-closed"))
                insert_job(db, _job("resume-failed-open"))
                insert_job(db, _job("resume-failed-resolved"))
                add_history(db, "reply-open", "reply_pending", "AI建议回复")
                add_history(db, "reply-closed", "reply_pending", "AI建议回复")
                add_history(db, "reply-closed", "reply_dismissed", "Web Dashboard 放弃回复建议")
                add_history(db, "resume-failed-open", "resume_failed", "定制简历生成失败")
                add_history(db, "resume-failed-resolved", "resume_failed", "定制简历生成失败")
                add_history(db, "resume-failed-resolved", "needs_resume", "后来已成功生成")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            # Act
            status, _, body = self._request("/api/history/unresolved-replies/count")

        # Assert
        self.assertTrue(status.startswith("200"), body)
        self.assertEqual(json.loads(body), {"count": 2})

    def test_web_api_history_can_include_unresolved_resume_failures_outside_recent_limit(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("resume-failed-open"))
                insert_job(db, _job("resume-failed-resolved"))
                insert_job(db, _job("reply-open"))
                insert_job(db, _job("recent-job"))
                add_history(db, "resume-failed-open", "resume_failed", "仍需处理")
                add_history(db, "resume-failed-resolved", "resume_failed", "旧失败")
                add_history(db, "resume-failed-resolved", "resume_sent", "后来已成功")
                add_history(db, "reply-open", "reply_pending", "待确认回复")
                add_history(db, "recent-job", "sent", "最近记录")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            # Act
            status, _, body = self._request("/api/history?limit=1&include_unresolved=1")

        # Assert
        self.assertTrue(status.startswith("200"), body)
        payload = json.loads(body)
        self.assertEqual(
            {(item["job_id"], item["action"]) for item in payload},
            {
                ("recent-job", "sent"),
                ("reply-open", "reply_pending"),
                ("resume-failed-open", "resume_failed"),
            },
        )

    def test_web_api_history_includes_last_week_replies_outside_recent_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                for job_id in ("recent-reply", "recent-resume-sent", "expired-reply", "recent-noise"):
                    insert_job(db, _job(job_id))
                add_history(
                    db,
                    "recent-reply",
                    "auto_replied",
                    json.dumps({"schema": "auto_replied.v1", "ai_reply": "近一周回复"}),
                )
                add_history(
                    db,
                    "expired-reply",
                    "replied",
                    json.dumps({"schema": "replied.external.v1", "manual_reply": "过期回复"}),
                )
                add_history(
                    db,
                    "recent-resume-sent",
                    "needs_resume",
                    json.dumps({"schema": "needs_resume.v1", "conversation_tail": [{"sender": "hr", "text": "请发简历"}]}),
                )
                add_history(db, "recent-resume-sent", "resume_sent", "简历已发送")
                db.execute(
                    "UPDATE history SET created_at = datetime('now', '-8 days') WHERE job_id = ?",
                    ("expired-reply",),
                )
                db.commit()
                add_history(db, "recent-noise", "sent", "最近普通记录")
            finally:
                db.close()
            server.set_base_dir(base_dir)

            status, _, body = self._request(
                "/api/history?limit=1&include_monitor_conversations=1"
            )
            full_status, _, full_body = self._request(
                "/api/history?limit=50&include_monitor_conversations=1"
            )

        self.assertTrue(status.startswith("200"), body)
        self.assertTrue(full_status.startswith("200"), full_body)
        expected = {
            ("recent-noise", "sent"),
            ("recent-reply", "auto_replied"),
            ("recent-resume-sent", "needs_resume"),
            ("recent-resume-sent", "resume_sent"),
        }
        for payload in (body, full_body):
            self.assertEqual(
                {(item["job_id"], item["action"]) for item in json.loads(payload)},
                expected,
            )

    def test_web_api_history_exposes_structured_failure_reason_and_resolution_state(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("resume-failed-detail"))
                add_history(
                    db,
                    "resume-failed-detail",
                    "resume_failed",
                    json.dumps(
                        {
                            "schema": "resume_failed.v2",
                            "hr_question": "请发一份简历。",
                            "ai_reply": "",
                            "system_reason": "事实完整性校验失败：新增了 50%",
                            "conversation_tail": [],
                        },
                        ensure_ascii=False,
                    ),
                )
            finally:
                db.close()
            server.set_base_dir(base_dir)

            # Act
            status, _, body = self._request("/api/history?limit=10&include_unresolved=1")
            unresolved_item = json.loads(body)[0]

            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                db.execute(
                    "UPDATE jobs SET resume_path = ? WHERE id = ?",
                    ("/tmp/generated.md", "resume-failed-detail"),
                )
                db.commit()
            finally:
                db.close()
            _, _, resolved_body = self._request("/api/history?limit=10&include_unresolved=1")
            resolved_item = json.loads(resolved_body)[0]

        # Assert
        self.assertTrue(status.startswith("200"), body)
        self.assertEqual(unresolved_item["detail_payload"]["hr_question"], "请发一份简历。")
        self.assertEqual(
            unresolved_item["detail_payload"]["system_reason"],
            "事实完整性校验失败：新增了 50%",
        )
        self.assertFalse(unresolved_item["resolved"])
        self.assertEqual(unresolved_item["url"], "https://example.com/job")
        self.assertEqual(unresolved_item["source_platform"], "boss")
        self.assertTrue(resolved_item["resolved"])
        self.assertEqual(resolved_item["resume_path"], "/tmp/generated.md")

    def test_full_task_receives_global_boss_collection_options(self):
        config = {
            "search": {"keywords": ["旧关键词"], "cities": ["北京"]},
            "profile": {"target_cities": ["北京"]},
            "collection": {"default_order": ["boss"]},
            "platforms": {
                "boss": {"enabled": True, "search": {"keywords": ["全局关键词"], "cities": ["上海"], "max_pages": 2, "sort": "newest", "target_count": 4}},
                "zhilian": {"enabled": False, "search": {}},
            },
        }
        with patch.object(server, "load_config", return_value=config), patch.object(server, "_preflight_messages", return_value=[]), patch.object(
            server, "_write_config"
        ), patch.object(server.task_runner, "start", return_value={"id": "full-global-config"}) as start:
            status, _, body = self._request("/api/workbench/task", method="POST", json_body={"mode": "full"})

        self.assertTrue(status.startswith("200"), body)
        task_config = start.call_args.args[1]
        self.assertEqual(task_config["_collection_options"]["platforms"]["boss"]["keywords"], ["全局关键词"])
        self.assertEqual(task_config["_collection_options"]["platforms"]["boss"]["cities"], ["上海"])
        self.assertNotIn("target_count", task_config["_collection_options"]["platforms"]["boss"])
        self.assertTrue(task_config["_collection_options"]["auto_score"])

    def test_full_task_rejects_collection_only_platform_from_saved_config(self):
        config = {
            "search": {"keywords": ["人力"], "cities": ["深圳"]},
            "profile": {"resume_path": "C:/resume.md"},
            "ai": {"api_key": "test-key"},
            "collection": {"default_order": ["boss", "zhilian"]},
            "platforms": {
                "boss": {"enabled": True, "search": {"keywords": ["人力"], "cities": ["深圳"]}},
                "zhilian": {"enabled": True, "search": {"keywords": ["人力"], "cities": ["深圳"]}},
            },
        }
        with patch.object(server, "load_config", return_value=config), patch.object(server, "_preflight_messages", return_value=[]), patch.object(
            server.task_runner, "start", return_value={"id": "full-with-zhilian"}
        ) as start:
            status, _, body = self._request("/api/workbench/task", method="POST", json_body={"mode": "full"})

        self.assertTrue(status.startswith("400"), body)
        self.assertEqual(json.loads(body)["collection_only_platforms"], ["zhilian"])
        start.assert_not_called()

    def test_full_task_rejects_collection_only_platform_from_dialog(self):
        config = {
            "profile": {"resume_path": "C:/resume.md"},
            "ai": {"api_key": "test-key"},
            "collection": {"default_order": ["boss"]},
            "platforms": {
                "boss": {"enabled": True, "search": {}},
                "zhilian": {"enabled": False, "search": {}},
            },
        }
        options = {
            "platform_order": ["zhilian"],
            "auto_score": False,
            "platforms": {
                "zhilian": {
                    "keywords": ["人力"],
                    "cities": ["深圳"],
                    "city_codes": {"深圳": "765"},
                    "max_pages": 3,
                    "sort": "default",
                    "target_count": 3,
                },
            },
        }
        with patch.object(server, "load_config", return_value=config), patch.object(server, "_preflight_messages", return_value=[]), patch.object(
            server, "_write_config"
        ) as write_config, patch.object(server.task_runner, "start", return_value={"id": "full-dialog-options"}) as start:
            status, _, body = self._request(
                "/api/workbench/task",
                method="POST",
                json_body={"mode": "full", "options": options},
            )

        self.assertTrue(status.startswith("400"), body)
        self.assertEqual(json.loads(body)["collection_only_platforms"], ["zhilian"])
        start.assert_not_called()
        write_config.assert_not_called()

    def _seed_scoring_base(self, base_dir: Path):
        (base_dir / "config.yaml").write_text(
            yaml.dump({"ai": {"api_key": "test-api-key"}}, allow_unicode=True),
            encoding="utf-8",
        )
        server.set_base_dir(base_dir)
        db = get_db(server.DATA_DIR / "bosshunter.db")
        try:
            insert_job(db, _job("p1"))
        finally:
            db.close()

    def _seed_run(self, run_id: str, status: str):
        db_path = server.DATA_DIR / "bosshunter.db"
        create_scoring_run(
            db_path,
            run_id=run_id,
            options={"scope": "pending", "limit": None, "force_rescore": False},
            job_ids=["p1"],
        )
        update_scoring_run(db_path, run_id, status=status, pause_reason="AI 服务请求失败" if status == "paused" else None)

    def test_scoring_start_reports_paused_run_with_machine_readable_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed_scoring_base(Path(tmp))
            self._seed_run("run-paused", "paused")

            with patch.object(server, "_preflight_messages", return_value=[]):
                status, _, body = self._request(
                    "/api/scoring/start",
                    method="POST",
                    json_body={"options": {"scope": "pending", "limit": None, "job_ids": [], "force_rescore": False}},
                )

        payload = json.loads(body)
        self.assertTrue(status.startswith("409"), body)
        self.assertEqual(payload.get("code"), "scoring_run_paused")
        self.assertIn("强制开始新任务", payload["error"])

    def test_scoring_start_ignores_non_boolean_force_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed_scoring_base(Path(tmp))
            self._seed_run("run-paused", "paused")
            runner = MagicMock()

            for bad_force in ("false", "true", 1):
                with patch.object(server, "_preflight_messages", return_value=[]), patch.object(server, "task_runner", runner):
                    status, _, body = self._request(
                        "/api/scoring/start",
                        method="POST",
                        json_body={
                            "force": bad_force,
                            "options": {"scope": "pending", "limit": None, "job_ids": [], "force_rescore": False},
                        },
                    )

                payload = json.loads(body)
                self.assertTrue(status.startswith("409"), body)
                self.assertEqual(payload.get("code"), "scoring_run_paused")

            old_run = get_scoring_run(server.DATA_DIR / "bosshunter.db", "run-paused")
            self.assertEqual(old_run["status"], "paused")
            runner.start.assert_not_called()

    def test_scoring_start_with_force_ends_paused_run_and_starts_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed_scoring_base(Path(tmp))
            self._seed_run("run-paused", "paused")
            runner = MagicMock()
            runner.start.return_value = {"id": "task-1", "status": "running"}

            with patch.object(server, "_preflight_messages", return_value=[]), patch.object(server, "task_runner", runner):
                status, _, body = self._request(
                    "/api/scoring/start",
                    method="POST",
                    json_body={
                        "force": True,
                        "options": {"scope": "pending", "limit": None, "job_ids": [], "force_rescore": False},
                    },
                )

            payload = json.loads(body)
            old_run = get_scoring_run(server.DATA_DIR / "bosshunter.db", "run-paused")
            self.assertTrue(status.startswith("200"), body)
            self.assertEqual(old_run["status"], "stopped")
            self.assertIn("强制结束", old_run["error"])
            self.assertEqual(payload["run"]["status"], "running")
            self.assertNotEqual(payload["run"]["id"], "run-paused")
            runner.start.assert_called_once()

    def test_scoring_start_with_force_still_rejects_active_running_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed_scoring_base(Path(tmp))
            self._seed_run("run-active", "running")
            runner = MagicMock()

            with patch.object(server, "_preflight_messages", return_value=[]), patch.object(server, "task_runner", runner):
                status, _, body = self._request(
                    "/api/scoring/start",
                    method="POST",
                    json_body={
                        "force": True,
                        "options": {"scope": "pending", "limit": None, "job_ids": [], "force_rescore": False},
                    },
                )

        payload = json.loads(body)
        self.assertTrue(status.startswith("409"), body)
        self.assertIn("正在运行", payload["error"])
        runner.start.assert_not_called()

    def test_score_checkpoint_writes_error_for_ai_pause_but_not_user_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed_scoring_base(Path(tmp))
            self._seed_run("run-checkpoint", "running")
            task = WorkbenchTask(id="task-ckpt", mode="score", label="单独 AI 评分")
            ai_pause = "AI 服务请求失败 (request_failed, status=404)"
            states = [
                {"remaining_job_ids": ["p1"], "status": "paused", "pause_reason": ai_pause, "error": ai_pause},
                {"remaining_job_ids": ["p1"], "status": "paused", "pause_reason": "用户暂停或任务中断", "error": ""},
            ]

            def fake_score_jobs(config, *args, **kwargs):
                callback = config.get("_workbench_score_checkpoint")
                for state in states:
                    callback(dict(state))

            with patch("bosshunter.ai.scorer.score_jobs", side_effect=fake_score_jobs), patch.object(
                server, "update_scoring_run", return_value=None
            ) as update_run:
                server._execute_score(task, {"_score_run_id": "run-checkpoint", "_score_options": {}})

            paused_calls = [call for call in update_run.call_args_list if call.kwargs.get("status") == "paused"]
            self.assertEqual(paused_calls[0].kwargs.get("error"), ai_pause)
            self.assertEqual(paused_calls[1].kwargs.get("error"), None)
            self.assertEqual(task.error, ai_pause)
            self.assertTrue(task.stop_requested.is_set())

    def test_outreach_resume_can_be_read_and_explicitly_reviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server.set_base_dir(root)
            source_path = root / "data" / "resumes" / "job-review.md"
            image_path = root / "data" / "resumes" / "job-review.png"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text("# 候选人\n\n## 教育经历\n本科\n", encoding="utf-8")
            image_path.write_bytes(b"png")
            db = get_db(root / "data" / "bosshunter.db")
            try:
                insert_job(db, _job("job-review"))
                db.execute(
                    """
                    UPDATE jobs
                    SET resume_source_path = ?, resume_image_path = ?,
                        resume_review_status = 'needs_review', resume_generation_source = 'deepseek'
                    WHERE id = ?
                    """,
                    (str(source_path), str(image_path), "job-review"),
                )
                db.commit()
            finally:
                db.close()

            status, _, body = self._request("/api/jobs/job-review/outreach-resume")
            self.assertTrue(status.startswith("200"), body)
            payload = json.loads(body)
            self.assertEqual(payload["status"], "needs_review")
            self.assertEqual(payload["source"], "deepseek")
            self.assertIn("教育经历", payload["markdown"])
            self.assertNotIn(str(source_path), body)

            status, _, body = self._request(
                "/api/jobs/job-review/outreach-resume/review",
                method="POST",
                json_body={"confirmed": True},
            )
            self.assertTrue(status.startswith("200"), body)
            self.assertEqual(json.loads(body)["status"], "ready")

            db = get_db(root / "data" / "bosshunter.db")
            try:
                row = db.execute(
                    "SELECT resume_review_status, resume_reviewed_at FROM jobs WHERE id = ?",
                    ("job-review",),
                ).fetchone()
            finally:
                db.close()
            self.assertEqual(row["resume_review_status"], "ready")
            self.assertIsNotNone(row["resume_reviewed_at"])


if __name__ == "__main__":
    unittest.main()
