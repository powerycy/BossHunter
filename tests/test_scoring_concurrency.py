import threading
import unittest
from unittest.mock import MagicMock, patch

from bosshunter.ai import scorer


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": f"产品经理 {job_id}",
        "company": f"公司 {job_id}",
        "salary": "20-30K",
        "experience": "3-5年",
        "jd": "负责产品规划和项目落地",
    }


class ScoringConcurrencyTests(unittest.TestCase):
    def test_ai_requests_run_concurrently_with_configured_worker_count(self):
        jobs = [_job(str(index)) for index in range(3)]
        barrier = threading.Barrier(3)
        db = MagicMock()

        def score_request(*_args, **_kwargs):
            barrier.wait(timeout=2)
            return '{"score": 80, "reason": "匹配", "missing": ""}'

        with (
            patch("bosshunter.ai.scorer.get_db", return_value=db),
            patch("bosshunter.ai.scorer._load_resume", return_value="真实简历"),
            patch("bosshunter.ai.scorer.get_jobs_by_status", return_value=jobs),
            patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
            patch("bosshunter.ai.scorer._call_claude", side_effect=score_request),
            patch("bosshunter.ai.scorer.update_job_quick_score"),
            patch("bosshunter.ai.scorer.update_job_score"),
            patch("bosshunter.ai.scorer.update_job_status"),
        ):
            result = scorer.score_jobs(
                {
                    "ai": {"scoring_concurrency": 3},
                    "scoring": {"threshold": 70},
                }
            )

        self.assertEqual(result, (3, 0))


if __name__ == "__main__":
    unittest.main()
