import json
import tempfile
import unittest
from pathlib import Path

from bosshunter.ai import scorer
from bosshunter.config import load_config
from bosshunter.db import get_db, insert_job, update_job_score


class DeepScoringContractTests(unittest.TestCase):
    def test_deep_scoring_is_disabled_by_default(self):
        config = load_config()

        self.assertFalse(config["scoring"]["deep_scoring"])
        self.assertFalse(scorer.is_deep_scoring(config))

    def test_deep_prompt_contains_evidence_weights_and_required_inputs(self):
        prompt = scorer._build_scoring_prompt(
            {
                "title": "Product Manager",
                "company": "Example",
                "salary": "20-30K",
                "experience": "3-5 years",
                "jd": "Own delivery and stakeholder management",
            },
            "Worked on delivery projects and managed stakeholders.",
            deep=True,
        )

        for phrase in (
            "55%",
            "25%",
            "15%",
            "5%",
            "evidence_mapping",
            "salary_assessment",
            "Product Manager",
            "Example",
            "20-30K",
            "3-5 years",
        ):
            self.assertIn(phrase, prompt)

    def test_deep_result_keeps_evidence_and_salary_assessment(self):
        result = scorer._validated_score_result(
            json.dumps(
                {
                    "score": 82,
                    "reason": "Delivery evidence matches the core responsibilities.",
                    "missing": "No direct industry evidence.",
                    "salary_assessment": "warning",
                    "evidence_mapping": [
                        {
                            "requirement": "Project delivery",
                            "category": "core",
                            "evidence": "Led delivery planning and risk tracking.",
                            "match": "strong",
                            "gap": "",
                        }
                    ],
                }
            ),
            deep=True,
        )

        self.assertEqual(result[0], 82)
        self.assertIn("Delivery evidence", result[1])
        self.assertEqual(result[2][0]["match"], "strong")
        self.assertEqual(result[3], "warning")

    def test_deep_result_requires_a_valid_evidence_mapping(self):
        result = scorer._validated_score_result(
            '{"score": 70, "reason": "Some evidence", "missing": "A gap", "evidence_mapping": "bad"}',
            deep=True,
        )

        self.assertIsNone(result)


class DeepScorePersistenceTests(unittest.TestCase):
    def test_jobs_store_score_evidence_as_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "data" / "jobs.db")
            insert_job(
                db,
                {
                    "id": "job-1",
                    "title": "Engineer",
                    "company": "Example",
                    "salary": "10-20K",
                    "city": "Shenzhen",
                    "experience": "1-3 years",
                    "jd": "Build features",
                    "hr_name": "",
                    "hr_title": "",
                    "hr_active": "",
                    "company_size": "",
                    "company_industry": "",
                    "url": "https://example.com/job",
                },
            )
            evidence = {
                "salary_assessment": "pass",
                "evidence_mapping": [{"requirement": "Build features", "match": "strong"}],
            }

            update_job_score(db, "job-1", 80, "Good evidence", evidence=evidence)
            row = db.execute("SELECT score_evidence FROM jobs WHERE id = 'job-1'").fetchone()
            db.close()

        self.assertEqual(json.loads(row["score_evidence"]), evidence)

    def test_legacy_rescore_clears_stale_deep_score_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "data" / "jobs.db")
            insert_job(
                db,
                {
                    "id": "job-legacy",
                    "title": "Engineer",
                    "company": "Example",
                    "salary": "10-20K",
                    "city": "Shenzhen",
                    "experience": "1-3 years",
                    "jd": "Build features",
                    "hr_name": "",
                    "hr_title": "",
                    "hr_active": "",
                    "company_size": "",
                    "company_industry": "",
                    "url": "https://example.com/job",
                },
            )
            update_job_score(db, "job-legacy", 80, "Deep", evidence={"evidence_mapping": []})

            update_job_score(db, "job-legacy", 75, "Legacy")
            row = db.execute("SELECT score_evidence FROM jobs WHERE id = 'job-legacy'").fetchone()
            db.close()

        self.assertIsNone(row["score_evidence"])


class DeepScoringUiContractTests(unittest.TestCase):
    def test_config_and_job_pool_expose_deep_scoring_and_evidence(self):
        root = Path(__file__).resolve().parents[1]
        config_page = (root / "src/bosshunter/web/frontend/src/pages/ConfigPage.tsx").read_text(encoding="utf-8")
        schema = (root / "src/bosshunter/web/config_schema.json").read_text(encoding="utf-8")
        jobs_table = (root / "src/bosshunter/web/frontend/src/components/dashboard/JobsTable.tsx").read_text(encoding="utf-8")
        dashboard_page = (root / "src/bosshunter/web/frontend/src/pages/DashboardPage.tsx").read_text(encoding="utf-8")

        dashboard_hook = (root / "src/bosshunter/web/frontend/src/hooks/useDashboard.ts").read_text(encoding="utf-8")

        self.assertIn("deep_scoring", config_page)
        self.assertIn("deep_scoring", schema)
        self.assertIn("evidence_mapping", jobs_table)
        self.assertIn("salary_assessment", jobs_table)
        self.assertIn("score_evidence", dashboard_hook)
        self.assertIn("evidence_mapping", dashboard_page)


if __name__ == "__main__":
    unittest.main()
