import unittest
from unittest.mock import patch

from bosshunter.ai.credentials import get_anthropic_api_key


class AnthropicCredentialTests(unittest.TestCase):
    def test_prefers_documented_api_key_env_var(self):
        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "from-api-key",
                "ANTHROPIC_AUTH_TOKEN": "from-auth-token",
            },
            clear=True,
        ):
            result = get_anthropic_api_key({"ai": {"api_key": "from-config"}})

        self.assertEqual(result, "from-api-key")

    def test_keeps_auth_token_as_backward_compatible_fallback(self):
        with patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": "from-auth-token"}, clear=True):
            result = get_anthropic_api_key({"ai": {"api_key": "from-config"}})

        self.assertEqual(result, "from-auth-token")

    def test_falls_back_to_config_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            result = get_anthropic_api_key({"ai": {"api_key": "from-config"}})

        self.assertEqual(result, "from-config")


if __name__ == "__main__":
    unittest.main()
