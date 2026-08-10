import io
import json
import unittest
from unittest.mock import patch

from bosshunter.web import server


class PauseTaskApiTests(unittest.TestCase):
    def test_pause_route_requests_a_safe_task_pause(self):
        captured = {}

        def fake_stop(task_id, reason):
            captured["task_id"] = task_id
            captured["reason"] = reason
            return {"id": task_id, "status": "stopping", "stop_reason": reason}

        body = b"{}"
        status_headers = {}

        def start_response(status, headers, exc_info=None):
            status_headers["status"] = status

        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/api/workbench/task/task-123/pause",
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

        with patch.object(server.task_runner, "stop", side_effect=fake_stop):
            response_body = b"".join(server.app(environ, start_response)).decode("utf-8")

        payload = json.loads(response_body)
        self.assertTrue(status_headers["status"].startswith("200"), response_body)
        self.assertEqual(payload["status"], "stopping")
        self.assertEqual(captured["task_id"], "task-123")
        self.assertIn("暂停", captured["reason"])


if __name__ == "__main__":
    unittest.main()
