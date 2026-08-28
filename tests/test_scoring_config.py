import unittest
import tempfile
from pathlib import Path

from bosshunter.config import load_config
from bosshunter.ai.scorer import get_scoring_concurrency


class ScoringConfigTests(unittest.TestCase):
    def test_default_scoring_concurrency_is_one(self):
        config = load_config()

        self.assertEqual(config["ai"]["scoring_concurrency"], 1)
        self.assertEqual(get_scoring_concurrency(config), 1)

    def test_scoring_concurrency_is_clamped_to_one_through_three(self):
        self.assertEqual(get_scoring_concurrency({"ai": {"scoring_concurrency": 0}}), 1)
        self.assertEqual(get_scoring_concurrency({"ai": {"scoring_concurrency": 99}}), 3)
        self.assertEqual(get_scoring_concurrency({"ai": {"scoring_concurrency": "invalid"}}), 1)

    def test_malformed_config_sections_fall_back_to_safe_defaults(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text("profile: null\nai: invalid\nscoring: null\nmonitor: []\n", encoding="utf-8")

            config = load_config(path)

        self.assertEqual(config["profile"]["target_cities"], ["北京"])
        self.assertEqual(config["ai"]["provider"], "anthropic")
        self.assertEqual(config["scoring"]["threshold"], 71)
        self.assertEqual(config["monitor"]["interval"], 30)


if __name__ == "__main__":
    unittest.main()
