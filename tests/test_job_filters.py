import unittest

from bosshunter.ai.prefilter import quick_score
from bosshunter.job_filters import matching_blocked_company


class CompanyFilterTests(unittest.TestCase):
    def test_blocked_company_matches_case_insensitive_substring(self):
        matched = matching_blocked_company("某公司科技有限公司", ["某公司"])

        self.assertEqual(matched, "某公司")

    def test_blocked_company_ignores_empty_rules(self):
        matched = matching_blocked_company("某公司科技有限公司", ["", "  "])

        self.assertIsNone(matched)

    def test_quick_score_filters_existing_job_by_company(self):
        score, reason = quick_score(
            {"title": "产品经理", "company": "某公司科技有限公司", "salary": "20-30K"},
            {"profile": {"blocked_companies": ["某公司"]}},
        )

        self.assertEqual(score, 0)
        self.assertIn("某公司", reason)


if __name__ == "__main__":
    unittest.main()
