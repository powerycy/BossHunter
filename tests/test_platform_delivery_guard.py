import io
import json
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from bosshunter.db import get_db, insert_job
from bosshunter.web import server


class PlatformDeliveryGuardTests(TestCase):
    @staticmethod
    def _request(path: str, body: dict | None = None):
        raw = json.dumps(body or {}).encode("utf-8")
        result = {}

        def start_response(status, headers, exc_info=None):
            result["status"] = status
            result["headers"] = dict(headers)

        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(raw)),
            "CONTENT_TYPE": "application/json",
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": "8686",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(raw),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        payload = b"".join(
            chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
            for chunk in server.app(environ, start_response)
        ).decode("utf-8")
        return result["status"], json.loads(payload)

    def test_zhilian_job_uses_delivery_adapter_and_resume_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            db = get_db(base_dir / "data" / "bosshunter.db")
            try:
                insert_job(db, {
                    "id": "zhilian:zl-1",
                    "title": "智联岗位",
                    "company": "智联公司",
                    "jd": "JD",
                    "url": "https://sou.zhaopin.com/job/1.html",
                    "source_platform": "zhilian",
                    "source_job_id": "zl-1",
                })
            finally:
                db.close()
            server.set_base_dir(base_dir)

            with mock.patch.object(server.task_runner, "start", return_value={"id": "zhilian-delivery"}) as start:
                deliver_status, deliver_payload = self._request(
                    "/api/workbench/deliver",
                    {"job_ids": ["zhilian:zl-1"]},
                )
            resume_status, resume_payload = self._request("/api/jobs/zhilian:zl-1/mark-resume-sent")

        self.assertTrue(deliver_status.startswith("200"), deliver_payload)
        self.assertEqual(deliver_payload["id"], "zhilian-delivery")
        start.assert_called_once()
        self.assertTrue(resume_status.startswith("200"), resume_payload)
        self.assertTrue(resume_payload["success"])
