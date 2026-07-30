import unittest
from unittest.mock import patch

from bosshunter.ai import scorer


class ScorerRequestOptionsTests(unittest.TestCase):
    def test_deepseek_scoring_enables_thinking_with_larger_output_budget(self):
        config = {
            "ai": {
                "provider": "anthropic",
                "model": "deepseek-v4-pro",
                "base_url": "https://api.deepseek.com/anthropic",
                "api_key": "test-key",
            }
        }

        with (
            patch.object(scorer, "get_anthropic_api_key", return_value="test-key"),
            patch.object(scorer, "call_anthropic_text", return_value="ok") as call_text,
        ):
            result = scorer._call_claude("prompt", config)

        self.assertEqual(result, "ok")
        self.assertEqual(call_text.call_args.args[2], 8192)
        self.assertEqual(call_text.call_args.kwargs["timeout"], 180)
        self.assertTrue(call_text.call_args.kwargs["enable_thinking"])
        self.assertFalse(call_text.call_args.kwargs["disable_thinking"])


if __name__ == "__main__":
    unittest.main()
