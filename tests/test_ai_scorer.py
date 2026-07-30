import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bosshunter.ai import scorer
from bosshunter.db import (
    get_db,
    get_funnel_stats,
    insert_job,
    update_job_quick_score,
    update_job_score,
    update_job_status,
)


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": "产品经理",
        "company": "示例公司",
        "salary": "15-20K",
        "city": "杭州",
        "experience": "1-3年",
        "jd": "负责产品需求分析与原型设计",
        "hr_name": "HR",
        "hr_title": "招聘经理",
        "hr_active": "刚刚活跃",
        "company_size": "20-99人",
        "company_industry": "互联网",
        "url": "https://example.com/job",
    }


class AiScorerTests(unittest.TestCase):
    def _config(self, resume_path: Path) -> dict:
        return {
            "profile": {"resume_path": str(resume_path)},
            "scoring": {"threshold": 60},
            "ai": {"scoring_max_attempts": 2},
        }

    def test_failed_attempts_are_recorded_and_job_stays_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db_path = base / "bosshunter.db"
            resume_path = base / "resume.md"
            resume_path.write_text("# 简历", encoding="utf-8")
            db = get_db(db_path)
            insert_job(db, _job("failed-job"))
            db.close()

            with (
                patch.object(scorer, "get_db", side_effect=lambda: get_db(db_path)),
                patch.object(scorer, "quick_score", return_value=(100, "通过")),
                patch.object(scorer, "_call_claude", side_effect=[RuntimeError("request timed out"), "not json"]) as ai_call,
            ):
                result = scorer.score_jobs(self._config(resume_path))

            verify_db = get_db(db_path)
            row = dict(verify_db.execute("SELECT status, score, score_reason FROM jobs WHERE id = 'failed-job'").fetchone())
            verify_db.close()

        self.assertEqual(result, (0, 0))
        self.assertEqual(ai_call.call_count, 2)
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["score"], 0)
        self.assertIn("AI评分失败", row["score_reason"])

    def test_second_attempt_can_complete_a_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db_path = base / "bosshunter.db"
            resume_path = base / "resume.md"
            resume_path.write_text("# 简历", encoding="utf-8")
            db = get_db(db_path)
            insert_job(db, _job("retry-job"))
            db.close()

            progress_events = []
            config = self._config(resume_path)
            config["_workbench_score_progress"] = progress_events.append
            with (
                patch.object(scorer, "get_db", side_effect=lambda: get_db(db_path)),
                patch.object(scorer, "quick_score", return_value=(100, "通过")),
                patch.object(
                    scorer,
                    "_call_claude",
                    side_effect=["", '{"score": 78, "reason": "能力匹配", "missing": "行业经验"}'],
                ),
            ):
                result = scorer.score_jobs(config)

            verify_db = get_db(db_path)
            row = dict(verify_db.execute("SELECT status, score, score_reason FROM jobs WHERE id = 'retry-job'").fetchone())
            verify_db.close()

        self.assertEqual(result, (1, 0))
        self.assertEqual(row["status"], "ready")
        self.assertEqual(row["score"], 78)
        self.assertIn("行业经验", row["score_reason"])
        self.assertEqual(progress_events[-1], {"completed": 1, "total": 1, "scored": 1, "filtered": 0, "failed": 0})

    def test_explicit_rescore_includes_ai_filtered_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db_path = base / "bosshunter.db"
            resume_path = base / "resume.md"
            resume_path.write_text("# 简历", encoding="utf-8")
            db = get_db(db_path)
            insert_job(db, _job("filtered-job"))
            update_job_quick_score(db, "filtered-job", 100)
            update_job_score(db, "filtered-job", 52, "能力部分匹配")
            update_job_status(db, "filtered-job", "filtered")
            db.close()

            with (
                patch.object(scorer, "get_db", side_effect=lambda: get_db(db_path)),
                patch.object(scorer, "quick_score", return_value=(100, "通过")),
                patch.object(scorer, "_call_claude", return_value='{"score": 72, "reason": "重新评估通过", "missing": ""}'),
            ):
                result = scorer.score_jobs(self._config(resume_path), rescore_filtered=True)

            verify_db = get_db(db_path)
            row = dict(verify_db.execute("SELECT status, score FROM jobs WHERE id = 'filtered-job'").fetchone())
            verify_db.close()

        self.assertEqual(result, (1, 0))
        self.assertEqual(row, {"status": "ready", "score": 72})


class FunnelStatsTests(unittest.TestCase):
    def test_ai_scored_includes_low_scores_but_excludes_prefilter_and_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            insert_job(db, _job("ready"))
            update_job_score(db, "ready", 80, "匹配")
            update_job_status(db, "ready", "ready")

            insert_job(db, _job("ai-filtered"))
            update_job_score(db, "ai-filtered", 50, "匹配度较低")
            update_job_status(db, "ai-filtered", "filtered")

            insert_job(db, _job("prefiltered"))
            update_job_score(db, "prefiltered", 0, "预筛不通过: 实习岗位")
            update_job_status(db, "prefiltered", "filtered")

            insert_job(db, _job("failed"))
            update_job_score(db, "failed", 0, "AI评分失败: request timed out")

            stats = get_funnel_stats(db)
            db.close()

        self.assertEqual(stats["AI评分"], 2)


if __name__ == "__main__":
    unittest.main()
