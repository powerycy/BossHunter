import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from bosshunter.web import server
from bosshunter.web.tasks import WorkbenchTask


class _RecordingRunner:
    def __init__(self):
        self.calls = []

    def start(self, mode, config):
        self.calls.append((mode, config))
        return {"id": "task-1", "mode": mode, "status": "running"}


class ScoringWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.original_base_dir = server.BASE_DIR
        self.original_runner = server.task_runner

    def tearDown(self):
        server.task_runner = self.original_runner
        server.set_base_dir(self.original_base_dir)

    def _post_json(self, path, payload):
        body = json.dumps(payload).encode("utf-8")
        result = {}

        def start_response(status, headers, exc_info=None):
            result["status"] = status
            result["headers"] = dict(headers)

        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": path,
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
        response_iter = server.app(environ, start_response)
        try:
            response_body = b"".join(
                item if isinstance(item, bytes) else item.encode("utf-8")
                for item in response_iter
            )
        finally:
            close = getattr(response_iter, "close", None)
            if close:
                close()
        return result["status"], json.loads(response_body.decode("utf-8"))

    def test_collect_scores_only_newly_inserted_jobs(self):
        task = WorkbenchTask(id="collect", mode="collect", label="collect")
        def fake_scrape(_config, _keywords, *, new_job_ids):
            new_job_ids.add("new-1")
            new_job_ids.add("new-2")
            return 2

        with patch("bosshunter.scraper.jobs.scrape_jobs", side_effect=fake_scrape), \
             patch("bosshunter.ai.scorer.score_jobs") as score_jobs:
            server._execute_collect(task, {"search": {"keywords": ["AI"]}})

        self.assertEqual(score_jobs.call_args.kwargs["job_ids"], {"new-1", "new-2"})

    def test_score_mode_is_not_rejected_by_task_start_preflight(self):
        messages = server._preflight_messages(
            "score",
            {"profile": {"resume_path": str(Path(__file__))}, "ai": {"api_key": "test-key"}},
        )

        self.assertFalse(any("不支持的任务模式" in message for message in messages))

    def test_score_task_route_preserves_selected_job_ids(self):
        runner = _RecordingRunner()
        server.task_runner = runner
        config = {"ai": {"api_key": "test"}, "search": {"keywords": ["AI"]}}

        with patch.object(server, "load_config", return_value=config), \
             patch.object(server, "_preflight_messages", return_value=[]), \
             patch.object(server, "_task_config", side_effect=lambda extra=None: {**config, **(extra or {})}):
            status, payload = self._post_json(
                "/api/workbench/task",
                {"mode": "score", "job_ids": ["job-a", "job-b"]},
            )

        self.assertTrue(status.startswith("200"))
        self.assertEqual(payload["mode"], "score")
        mode, task_config = runner.calls[0]
        self.assertEqual(mode, "score")
        self.assertEqual(task_config["_workbench_score_job_ids"], ["job-a", "job-b"])

    def test_city_lookup_route_returns_safe_error(self):
        with patch("bosshunter.web.server.lookup_city", side_effect=server.CityLookupError("unknown")):
            status, payload = self._post_json("/api/config/cities/lookup", {"city": "Atlantis"})

        self.assertTrue(status.startswith("400"))
        self.assertEqual(payload, {"error": "unknown"})


if __name__ == "__main__":
    unittest.main()
