import unittest

from bosshunter.config import load_config
from bosshunter.ai.scorer import get_scoring_concurrency


class ScoringConfigTests(unittest.TestCase):
    def test_default_scoring_concurrency_is_three(self):
        config = load_config()

        self.assertEqual(config["ai"]["scoring_concurrency"], 3)
        self.assertEqual(get_scoring_concurrency(config), 3)

    def test_scoring_concurrency_is_clamped_to_one_through_five(self):
        self.assertEqual(get_scoring_concurrency({"ai": {"scoring_concurrency": 0}}), 1)
        self.assertEqual(get_scoring_concurrency({"ai": {"scoring_concurrency": 99}}), 5)
        self.assertEqual(get_scoring_concurrency({"ai": {"scoring_concurrency": "invalid"}}), 3)


if __name__ == "__main__":
    unittest.main()
