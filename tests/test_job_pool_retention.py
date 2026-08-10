import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bosshunter.ai import scorer
from bosshunter.config import load_config
from bosshunter.db import get_db, insert_job
from bosshunter.web import server


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": "Product Manager",
        "company": "Example",
        "salary": "20-30K",
        "city": "Shanghai",
        "experience": "3-5 years",
        "jd": "Build AI product features",
        "hr_name": "HR",
        "hr_title": "Recruiter",
        "hr_active": "",
        "company_size": "",
        "company_industry": "",
        "url": f"https://example.com/{job_id}",
    }


def _get_json(path: str) -> tuple[str, list[dict]]:
    path_info, query_string = path.split("?", 1)
    result: dict[str, object] = {}

    def start_response(status, headers, exc_info=None):
        result["status"] = status

    environ = {
        "REQUEST_METHOD": "GET",
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
    response_iter = server.app(environ, start_response)
    try:
        body = b"".join(
            chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
            for chunk in response_iter
        )
    finally:
        close = getattr(response_iter, "close", None)
        if close:
            close()
    return str(result["status"]), json.loads(body.decode("utf-8"))


class JobPoolListingTests(unittest.TestCase):
    def setUp(self):
        self.original_base_dir = server.BASE_DIR
        self.tmp = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.tmp.name)
        server.set_base_dir(self.base_dir)

    def tearDown(self):
        server.set_base_dir(self.original_base_dir)
        self.tmp.cleanup()

    def test_unlimited_job_request_returns_every_unscored_job(self):
        db = get_db(self.base_dir / "data" / "bosshunter.db")
        try:
            for index in range(101):
                insert_job(db, _job(f"pending-{index}"))
        finally:
            db.close()

        status, jobs = _get_json("/api/jobs?limit=0")

        self.assertTrue(status.startswith("200"))
        self.assertEqual(len(jobs), 101)
        self.assertTrue(all(job["status"] == "pending" and job["score"] in {None, 0} for job in jobs))


class LowScoreDeletionTests(unittest.TestCase):
    def test_low_scored_job_is_deleted_after_successful_ai_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "jobs.db"
            db = get_db(database_path)
            try:
                insert_job(db, _job("low-score"))
            finally:
                db.close()

            with (
                patch("bosshunter.ai.scorer.get_db", side_effect=lambda: get_db(database_path)),
                patch("bosshunter.ai.scorer._load_resume", return_value="Resume"),
                patch("bosshunter.ai.scorer._call_claude", return_value='{"score": 49, "reason": "low", "missing": ""}'),
            ):
                scored, filtered = scorer.score_jobs(
                    {
                        "ai": {"scoring_concurrency": 1},
                        "scoring": {"threshold": 71, "low_score_delete_threshold": 50},
                    }
                )

            db = get_db(database_path)
            try:
                row = db.execute("SELECT id FROM jobs WHERE id = ?", ("low-score",)).fetchone()
            finally:
                db.close()

        self.assertEqual((scored, filtered), (0, 1))
        self.assertIsNone(row)

    def test_low_score_delete_threshold_defaults_to_fifty(self):
        config = load_config(Path("missing-config.yaml"))

        self.assertEqual(config["scoring"]["low_score_delete_threshold"], 50)


if __name__ == "__main__":
    unittest.main()
