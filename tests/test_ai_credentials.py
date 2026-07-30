import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bosshunter.ai import credentials


class AnthropicCredentialTests(unittest.TestCase):
    def setUp(self):
        cache = getattr(credentials, "_MODEL_RESOLVE_CACHE", None)
        if cache is not None:
            cache.clear()

    def test_prefers_documented_api_key_env_var(self):
        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "from-api-key",
                "ANTHROPIC_AUTH_TOKEN": "from-auth-token",
            },
            clear=True,
        ):
            result = credentials.get_anthropic_api_key({"ai": {"api_key": "from-config"}})

        self.assertEqual(result, "from-api-key")

    def test_keeps_auth_token_as_backward_compatible_fallback(self):
        with patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": "from-auth-token"}, clear=True):
            result = credentials.get_anthropic_api_key({"ai": {"api_key": "from-config"}})

        self.assertEqual(result, "from-auth-token")

    def test_falls_back_to_config_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            result = credentials.get_anthropic_api_key({"ai": {"api_key": "from-config"}})

        self.assertEqual(result, "from-config")

    def test_falls_back_to_config_auth_token(self):
        with patch.dict("os.environ", {}, clear=True):
            result = credentials.get_anthropic_api_key({"ai": {"auth_token": "from-config-token"}})

        self.assertEqual(result, "from-config-token")

    def test_build_anthropic_client_kwargs_includes_auth_token_when_configured(self):
        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "from-api-key",
                "ANTHROPIC_AUTH_TOKEN": "from-auth-token",
                "ANTHROPIC_BASE_URL": "https://api-gateway.example.com",
            },
            clear=True,
        ):
            build_kwargs = getattr(
                credentials,
                "build_anthropic_client_kwargs",
                lambda config: {"api_key": credentials.get_anthropic_api_key(config)},
            )
            result = build_kwargs({"ai": {}})

        self.assertEqual(result["api_key"], "from-api-key")
        self.assertEqual(result["auth_token"], "from-auth-token")
        self.assertEqual(result["base_url"], "https://api-gateway.example.com")

    def test_build_anthropic_client_kwargs_does_not_duplicate_auth_token_as_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            result = credentials.build_anthropic_client_kwargs({"ai": {"auth_token": "from-config-token"}})

        self.assertNotIn("api_key", result)
        self.assertEqual(result["auth_token"], "from-config-token")

    def test_compatible_api_model_cache_key_does_not_store_raw_credentials(self):
        with (
            patch.dict(
                "os.environ",
                {
                    "ANTHROPIC_BASE_URL": "https://api-gateway.example.com",
                    "ANTHROPIC_AUTH_TOKEN": "very-secret-token",
                },
                clear=True,
            ),
            patch("httpx.get", side_effect=RuntimeError("network down")),
        ):
            credentials.resolve_anthropic_model("claude-sonnet-4-6", {"ai": {}})

        cache_keys = list(credentials._MODEL_RESOLVE_CACHE)
        self.assertEqual(len(cache_keys), 1)
        self.assertNotIn("very-secret-token", cache_keys[0])

    def test_resolves_compatible_api_model_name_from_available_models(self):
        class ModelsResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": [{"id": "Claude Sonnet 4.6"}]}

        with (
            patch.dict(
                "os.environ",
                {
                    "ANTHROPIC_BASE_URL": "https://api-gateway.example.com",
                    "ANTHROPIC_API_KEY": "from-api-key",
                    "ANTHROPIC_AUTH_TOKEN": "from-auth-token",
                },
                clear=True,
            ),
            patch("httpx.get", return_value=ModelsResponse()),
        ):
            result = getattr(credentials, "resolve_anthropic_model", lambda model, config: model)("claude-sonnet-4-6", {"ai": {}})

        self.assertEqual(result, "Claude Sonnet 4.6")

    def test_caches_compatible_api_model_resolution_failures(self):
        with (
            patch.dict(
                "os.environ",
                {
                    "ANTHROPIC_BASE_URL": "https://api-gateway.example.com",
                    "ANTHROPIC_AUTH_TOKEN": "token-a",
                },
                clear=True,
            ),
            patch("httpx.get", side_effect=RuntimeError("network down")) as models_get,
        ):
            first = getattr(credentials, "resolve_anthropic_model", lambda model, config: model)("claude-sonnet-4-6", {"ai": {}})
            second = getattr(credentials, "resolve_anthropic_model", lambda model, config: model)("claude-sonnet-4-6", {"ai": {}})

        self.assertEqual(first, "claude-sonnet-4-6")
        self.assertEqual(second, "claude-sonnet-4-6")
        self.assertEqual(models_get.call_count, 1)

    def test_compatible_api_model_cache_is_separated_by_auth_token(self):
        class ModelsResponse:
            def __init__(self, model):
                self.model = model

            def raise_for_status(self):
                pass

            def json(self):
                return {"data": [{"id": self.model}]}

        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_BASE_URL": "https://api-gateway.example.com",
                "ANTHROPIC_AUTH_TOKEN": "token-a",
            },
            clear=True,
        ):
            with patch("httpx.get", return_value=ModelsResponse("Claude Sonnet 4.6")):
                first = getattr(credentials, "resolve_anthropic_model", lambda model, config: model)("claude-sonnet-4-6", {"ai": {}})

        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_BASE_URL": "https://api-gateway.example.com",
                "ANTHROPIC_AUTH_TOKEN": "token-b",
            },
            clear=True,
        ):
            with patch("httpx.get", return_value=ModelsResponse("Claude Sonnet 4.6 B")):
                second = getattr(credentials, "resolve_anthropic_model", lambda model, config: model)("claude-sonnet-4-6", {"ai": {}})

        self.assertEqual(first, "Claude Sonnet 4.6")
        self.assertEqual(second, "Claude Sonnet 4.6 B")

    def test_call_anthropic_text_uses_resolved_model_and_auth_token(self):
        calls = {}

        class Client:
            def __init__(self, **kwargs):
                calls["kwargs"] = kwargs
                self.messages = self

            def create(self, **kwargs):
                calls["message"] = kwargs
                return SimpleNamespace(content=[SimpleNamespace(text=" ok ")])

        call_text = getattr(credentials, "call_anthropic_text", lambda prompt, config, max_tokens: None)

        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(credentials, "resolve_anthropic_model", lambda model, config: "Claude Sonnet 4.6", create=True),
            patch.object(credentials, "build_anthropic_client_kwargs", lambda config: {"api_key": "key", "auth_token": "token", "base_url": "https://api-gateway.example.com"}, create=True),
            patch.dict("sys.modules", {"anthropic": SimpleNamespace(Anthropic=Client)}),
        ):
            result = call_text("prompt", {"ai": {"model": "claude-sonnet-4-6", "api_key": "key", "auth_token": "token"}}, 123)

        self.assertEqual(result, "ok")
        self.assertEqual(calls["kwargs"]["auth_token"], "token")
        self.assertEqual(calls["message"]["model"], "Claude Sonnet 4.6")
        self.assertEqual(calls["message"]["max_tokens"], 123)

    def test_deepseek_uses_dedicated_environment_key_and_preset_base_url(self):
        config = {"ai": {"service": "deepseek", "provider": "openai_compatible"}}

        with patch.dict(
            "os.environ",
            {"DEEPSEEK_API_KEY": "deepseek-secret", "OPENAI_API_KEY": "generic-secret"},
            clear=True,
        ):
            api_key = credentials.get_ai_api_key(config)
            base_url = credentials.get_ai_base_url(config)
            source = credentials.get_ai_key_source(config)

        self.assertEqual(api_key, "deepseek-secret")
        self.assertEqual(base_url, "https://api.deepseek.com")
        self.assertEqual(source, "DEEPSEEK_API_KEY")

    def test_doubao_uses_ark_environment_key_and_preset_base_url(self):
        config = {"ai": {"service": "doubao", "provider": "openai_compatible"}}

        with patch.dict("os.environ", {"ARK_API_KEY": "ark-secret"}, clear=True):
            api_key = credentials.get_ai_api_key(config)
            base_url = credentials.get_ai_base_url(config)
            source = credentials.get_ai_key_source(config)

        self.assertEqual(api_key, "ark-secret")
        self.assertEqual(base_url, "https://ark.cn-beijing.volces.com/api/v3")
        self.assertEqual(source, "ARK_API_KEY")

    def test_openai_compatible_call_uses_service_preset_without_exposing_key(self):
        class CompletionResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": " ok "}}]}

        config = {
            "ai": {
                "service": "deepseek",
                "provider": "openai_compatible",
                "model": "provider-current-model",
            }
        }
        with (
            patch.dict("os.environ", {"DEEPSEEK_API_KEY": "deepseek-secret"}, clear=True),
            patch("bosshunter.ai.credentials.httpx.post", return_value=CompletionResponse()) as post,
        ):
            result = credentials.call_openai_compatible_text("prompt", config, 123)

        self.assertEqual(result, "ok")
        self.assertEqual(post.call_args.args[0], "https://api.deepseek.com/chat/completions")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer deepseek-secret")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "provider-current-model")


if __name__ == "__main__":
    unittest.main()
