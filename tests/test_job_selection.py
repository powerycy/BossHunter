import tempfile
import unittest
from pathlib import Path

from bosshunter.db import (
    get_db,
    get_jobs_pending_confirmation,
    get_jobs_ready_to_send,
    insert_job,
    update_job_greeting,
    update_job_score,
    update_job_status,
)


def _job(job_id: str, title: str = "Engineer") -> dict:
    return {
        "id": job_id,
        "title": title,
        "company": "Example",
        "salary": "10-20K",
        "city": "Beijing",
        "experience": "1-3 years",
        "jd": "Build product features",
        "hr_name": "HR",
        "hr_title": "Recruiter",
        "hr_active": "",
        "company_size": "",
        "company_industry": "",
        "url": "https://example.com/job",
    }


class JobSelectionTests(unittest.TestCase):
    def test_pending_confirmation_excludes_jobs_with_greetings(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            try:
                insert_job(db, _job("scored"))
                update_job_score(db, "scored", 88, "good match")
                update_job_status(db, "scored", "ready")

                insert_job(db, _job("sendable"))
                update_job_score(db, "sendable", 92, "great match")
                update_job_status(db, "sendable", "ready")
                update_job_greeting(db, "sendable", "Hi, this role looks like a strong fit.")

                jobs = get_jobs_pending_confirmation(db)
            finally:
                db.close()

        self.assertEqual([job["id"] for job in jobs], ["scored"])

    def test_ready_to_send_requires_a_non_empty_greeting(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            try:
                insert_job(db, _job("no-greeting"))
                update_job_status(db, "no-greeting", "ready")

                insert_job(db, _job("blank-greeting"))
                update_job_status(db, "blank-greeting", "ready")
                update_job_greeting(db, "blank-greeting", "   ")

                insert_job(db, _job("sendable"))
                update_job_status(db, "sendable", "ready")
                update_job_greeting(db, "sendable", "Hi, this role looks like a strong fit.")

                insert_job(db, _job("approved"))
                update_job_status(db, "approved", "approved")
                update_job_greeting(db, "approved", "Not ready for send status yet.")

                jobs = get_jobs_ready_to_send(db)
            finally:
                db.close()

        self.assertEqual([job["id"] for job in jobs], ["sendable"])


if __name__ == "__main__":
    unittest.main()
