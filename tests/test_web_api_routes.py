import io
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import yaml

from bosshunter.db import add_history, get_db, insert_job, update_job_score, update_job_status
from bosshunter.throttle import SendWindowChecker
from bosshunter.web import server
from threading import Event

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

    def _request(self, path: str, method: str = "GET"):
        if "?" in path:
            path_info, query_string = path.split("?", 1)
        else:
            path_info, query_string = path, ""

        status_headers = {}

        def start_response(status, headers, exc_info=None):
            status_headers["status"] = status
            status_headers["headers"] = dict(headers)

        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path_info,
            "QUERY_STRING": query_string,
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": "8686",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(b""),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }

        body = b"".join(
            chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
            for chunk in server.app(environ, start_response)
        ).decode("utf-8")
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

    def test_web_api_full_task_continues_delivery_and_monitoring_after_confirmation(self):
        # Arrange
        calls = []

        def fake_collect(task, config):
            calls.append("collect")

        def fake_deliver(task, config):
            calls.append(("deliver", config.get("_workbench_job_ids")))

        def fake_monitor(task, config):
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
                 patch.object(server, "_execute_monitor", side_effect=fake_monitor):
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
        self.assertEqual(calls, ["collect", ("deliver", ["ready-a", "ready-b"]), "monitor"])

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
            self.assertEqual(json.loads(body), {"error": "仅支持 .md 或 .docx 格式"})

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
                insert_job(db, _job("recent-job"))
                add_history(db, "resume-failed-open", "resume_failed", "仍需处理")
                add_history(db, "resume-failed-resolved", "resume_failed", "旧失败")
                add_history(db, "resume-failed-resolved", "resume_sent", "后来已成功")
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
            {("recent-job", "sent"), ("resume-failed-open", "resume_failed")},
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
        self.assertTrue(resolved_item["resolved"])
        self.assertEqual(resolved_item["resume_path"], "/tmp/generated.md")


if __name__ == "__main__":
    unittest.main()
