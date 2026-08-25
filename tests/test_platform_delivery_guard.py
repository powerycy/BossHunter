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

    def test_collection_only_platforms_reject_delivery_and_resume_routes(self):
        for platform, job_id, url in (
            ("zhilian", "zhilian:zl-1", "https://www.zhaopin.com/jobdetail/zl-1.htm"),
            ("51job", "51job:job-1", "https://jobs.51job.com/shanghai/job-1.html"),
        ):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as tmp:
                base_dir = Path(tmp)
                db = get_db(base_dir / "data" / "bosshunter.db")
                try:
                    insert_job(db, {
                        "id": job_id,
                        "title": "采集岗位",
                        "company": "示例公司",
                        "jd": "JD",
                        "url": url,
                        "source_platform": platform,
                        "source_job_id": job_id.split(":", 1)[1],
                    })
                finally:
                    db.close()
                server.set_base_dir(base_dir)

                with mock.patch.object(server.task_runner, "start") as start:
                    deliver_status, deliver_payload = self._request(
                        "/api/workbench/deliver",
                        {"job_ids": [job_id]},
                    )
                resume_status, resume_payload = self._request(f"/api/jobs/{job_id}/mark-resume-sent")

                self.assertTrue(deliver_status.startswith("403"), deliver_payload)
                self.assertTrue(resume_status.startswith("403"), resume_payload)
                start.assert_not_called()
