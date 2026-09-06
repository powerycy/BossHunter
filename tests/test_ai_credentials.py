import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bosshunter.ai import credentials


class AnthropicCredentialTests(unittest.TestCase):
    def setUp(self):
        cache = getattr(credentials, "_MODEL_RESOLVE_CACHE", None)
        if cache is not None:
            cache.clear()

    def test_config_api_key_takes_precedence_over_env_vars(self):
        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "from-api-key",
                "ANTHROPIC_AUTH_TOKEN": "from-auth-token",
            },
            clear=True,
        ):
            result = credentials.get_anthropic_api_key({"ai": {"api_key": "from-config"}})

        self.assertEqual(result, "from-config")

    def test_falls_back_to_env_api_key_when_config_empty(self):
        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "from-api-key",
                "ANTHROPIC_AUTH_TOKEN": "from-auth-token",
            },
            clear=True,
        ):
            self.assertEqual(credentials.get_anthropic_api_key({"ai": {}}), "from-api-key")

    def test_keeps_auth_token_as_backward_compatible_fallback(self):
        with patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": "from-auth-token"}, clear=True):
            result = credentials.get_anthropic_api_key({"ai": {}})

        self.assertEqual(result, "from-auth-token")

    def test_falls_back_to_config_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            result = credentials.get_anthropic_api_key({"ai": {"api_key": "from-config"}})

        self.assertEqual(result, "from-config")

    def test_falls_back_to_config_auth_token(self):
        with patch.dict("os.environ", {}, clear=True):
            result = credentials.get_anthropic_api_key({"ai": {"auth_token": "from-config-token"}})

        self.assertEqual(result, "from-config-token")

    def test_config_credentials_block_excludes_env_credentials_in_build_kwargs(self):
        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "from-api-key",
                "ANTHROPIC_AUTH_TOKEN": "from-auth-token",
                "ANTHROPIC_BASE_URL": "https://env-gateway.example.com",
            },
            clear=True,
        ):
            result = credentials.build_anthropic_client_kwargs(
                {"ai": {"api_key": "from-config", "base_url": "https://config-gateway.example.com"}}
            )

        self.assertEqual(result["api_key"], "from-config")
        self.assertNotIn("auth_token", result)
        self.assertEqual(result["base_url"], "https://config-gateway.example.com")

    def test_build_kwargs_falls_back_to_env_when_config_credentials_empty(self):
        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "from-api-key",
                "ANTHROPIC_AUTH_TOKEN": "from-auth-token",
            },
            clear=True,
        ):
            result = credentials.build_anthropic_client_kwargs({"ai": {}})

        self.assertEqual(result["api_key"], "from-api-key")
        self.assertEqual(result["auth_token"], "from-auth-token")

    def test_config_base_url_takes_precedence_over_env(self):
        with patch.dict("os.environ", {"ANTHROPIC_BASE_URL": "https://env-gateway.example.com"}, clear=True):
            result = credentials.get_ai_base_url({"ai": {"base_url": "https://config-gateway.example.com"}})

        self.assertEqual(result, "https://config-gateway.example.com")

    def test_base_url_falls_back_to_env_when_config_empty(self):
        with patch.dict("os.environ", {"ANTHROPIC_BASE_URL": "https://env-gateway.example.com"}, clear=True):
            result = credentials.get_ai_base_url({"ai": {}})

        self.assertEqual(result, "https://env-gateway.example.com")

    def test_key_source_prefers_local_config_over_env(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "from-api-key"}, clear=True):
            self.assertEqual(credentials.get_ai_key_source({"ai": {"api_key": "from-config"}}), "本地配置")
            self.assertEqual(credentials.get_ai_key_source({"ai": {}}), "ANTHROPIC_API_KEY")

    def test_base_url_source_reflects_resolution_order(self):
        with patch.dict("os.environ", {"ANTHROPIC_BASE_URL": "https://env-gateway.example.com"}, clear=True):
            self.assertEqual(credentials.get_ai_base_url_source({"ai": {"base_url": "https://config-gateway.example.com"}}), "本地配置")
            self.assertEqual(credentials.get_ai_base_url_source({"ai": {}}), "ANTHROPIC_BASE_URL")
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(credentials.get_ai_base_url_source({"ai": {}}))
            self.assertEqual(credentials.get_ai_base_url_source({"ai": {"service": "deepseek"}}), "服务商预设")

    def test_resolve_anthropic_model_uses_config_url_and_credentials_first(self):
        class ModelsResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": [{"id": "Claude Sonnet 4.6"}]}

        with (
            patch.dict(
                "os.environ",
                {
                    "ANTHROPIC_BASE_URL": "https://env-gateway.example.com",
                    "ANTHROPIC_AUTH_TOKEN": "from-env-token",
                },
                clear=True,
            ),
            patch("httpx.get", return_value=ModelsResponse()) as models_get,
        ):
            result = credentials.resolve_anthropic_model(
                "claude-sonnet-4-6",
                {"ai": {"base_url": "https://config-gateway.example.com", "api_key": "from-config"}},
            )

        self.assertEqual(result, "Claude Sonnet 4.6")
        self.assertEqual(models_get.call_args.args[0], "https://config-gateway.example.com/v1/models")
        self.assertEqual(models_get.call_args.kwargs["headers"]["x-api-key"], "from-config")
        self.assertNotIn("Authorization", models_get.call_args.kwargs["headers"])
        self.assertFalse(models_get.call_args.kwargs["trust_env"])

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

    def test_anthropic_client_disables_ambient_proxy_settings(self):
        fake_client = SimpleNamespace(trust_env=False, close=lambda: None)
        with (
            patch.dict("os.environ", {"HTTP_PROXY": "http://127.0.0.1:7897"}, clear=True),
            patch("bosshunter.ai.credentials.httpx.Client", return_value=fake_client) as client_ctor,
        ):
            result = credentials.build_anthropic_client_kwargs({"ai": {"api_key": "from-config"}})

        client = result.get("http_client")
        try:
            self.assertIn("http_client", result)
            self.assertFalse(client.trust_env)
            client_ctor.assert_called_once_with(trust_env=False)
        finally:
            if client is not None:
                client.close()

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

    def test_anthropic_text_blocks_are_combined_and_reasoning_is_ignored(self):
        class Client:
            def __init__(self, **kwargs):
                self.messages = self

            def create(self, **kwargs):
                return SimpleNamespace(
                    content=[
                        SimpleNamespace(type="thinking", thinking="internal reasoning"),
                        SimpleNamespace(type="text", text="第一段"),
                        SimpleNamespace(type="text", text="第二段"),
                    ]
                )

        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(credentials, "resolve_anthropic_model", return_value="claude-sonnet-4-6"),
            patch.dict("sys.modules", {"anthropic": SimpleNamespace(Anthropic=Client)}),
        ):
            result = credentials.call_anthropic_text(
                "prompt",
                {"ai": {"api_key": "key", "thinking": "off"}},
                256,
            )

        self.assertEqual(result, "第一段\n第二段")

    def test_anthropic_auto_thinking_retries_without_unsupported_parameter(self):
        requests = []

        class ThinkingCompatibilityError(RuntimeError):
            status_code = 400

        class Client:
            def __init__(self, **kwargs):
                self.messages = self

            def create(self, **kwargs):
                requests.append(kwargs)
                if len(requests) == 1:
                    raise ThinkingCompatibilityError("unknown parameter: thinking")
                return SimpleNamespace(content=[SimpleNamespace(text=" fallback ok ")])

        config = {
            "ai": {
                "api_key": "key",
                "model": "claude-sonnet-4-6",
                "thinking": "auto",
                "thinking_budget": 2048,
            }
        }
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(credentials, "resolve_anthropic_model", return_value="claude-sonnet-4-6"),
            patch.dict("sys.modules", {"anthropic": SimpleNamespace(Anthropic=Client)}),
        ):
            result = credentials.call_anthropic_text("prompt", config, 256)

        self.assertEqual(result, "fallback ok")
        self.assertEqual(requests[0]["thinking"], {"type": "disabled"})
        self.assertNotIn("thinking", requests[1])
        self.assertEqual(requests[1]["max_tokens"], 3072)

    def test_anthropic_enabled_thinking_reserves_output_budget(self):
        requests = []

        class Client:
            def __init__(self, **kwargs):
                self.messages = self

            def create(self, **kwargs):
                requests.append(kwargs)
                return SimpleNamespace(content=[SimpleNamespace(text=" ok ")])

        config = {
            "ai": {
                "api_key": "key",
                "model": "claude-sonnet-4-6",
                "thinking": "enabled",
                "thinking_budget": 2048,
            }
        }
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(credentials, "resolve_anthropic_model", return_value="claude-sonnet-4-6"),
            patch.dict("sys.modules", {"anthropic": SimpleNamespace(Anthropic=Client)}),
        ):
            result = credentials.call_anthropic_text("prompt", config, 256)

        self.assertEqual(result, "ok")
        self.assertEqual(requests[0]["thinking"], {"type": "enabled", "budget_tokens": 2048})
        self.assertEqual(requests[0]["max_tokens"], 3072)

    def test_anthropic_auto_retries_empty_thinking_response_with_larger_budget(self):
        requests = []

        class Client:
            def __init__(self, **kwargs):
                self.messages = self

            def create(self, **kwargs):
                requests.append(kwargs)
                if len(requests) == 1:
                    return SimpleNamespace(content=[SimpleNamespace(thinking="reasoning only")])
                return SimpleNamespace(content=[SimpleNamespace(text=" complete text ")])

        config = {
            "ai": {
                "api_key": "key",
                "model": "claude-sonnet-4-6",
                "thinking": "auto",
                "thinking_budget": 2048,
            }
        }
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(credentials, "resolve_anthropic_model", return_value="claude-sonnet-4-6"),
            patch.dict("sys.modules", {"anthropic": SimpleNamespace(Anthropic=Client)}),
        ):
            result = credentials.call_anthropic_text("prompt", config, 256)

        self.assertEqual(result, "complete text")
        self.assertEqual(requests[0]["thinking"], {"type": "disabled"})
        self.assertEqual(requests[1]["thinking"], {"type": "disabled"})
        self.assertEqual(requests[1]["max_tokens"], 3072)

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
        self.assertFalse(post.call_args.kwargs["trust_env"])

    def test_deepseek_shorthand_model_is_resolved_before_chat_request(self):
        class ModelsResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": [{"id": "deepseek-v4-flash"}, {"id": "deepseek-v4-pro"}]}

        class CompletionResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": " ok "}}]}

        config = {
            "ai": {
                "service": "deepseek",
                "provider": "openai_compatible",
                "model": "v4-flash",
            }
        }
        with (
            patch.dict("os.environ", {"DEEPSEEK_API_KEY": "deepseek-secret"}, clear=True),
            patch("bosshunter.ai.credentials.httpx.get", return_value=ModelsResponse()) as models_get,
            patch("bosshunter.ai.credentials.httpx.post", return_value=CompletionResponse()) as post,
        ):
            result = credentials.call_openai_compatible_text("prompt", config, 123)

        self.assertEqual(result, "ok")
        self.assertEqual(models_get.call_args.args[0], "https://api.deepseek.com/models")
        self.assertFalse(models_get.call_args.kwargs["trust_env"])
        self.assertEqual(post.call_args.kwargs["json"]["model"], "deepseek-v4-flash")

    def test_deepseek_model_name_is_lowercased_only_in_the_outbound_request(self):
        class CompletionResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        config = {
            "ai": {
                "service": "deepseek",
                "provider": "openai_compatible",
                "model": "DeepSeek-V4-Flash",
            }
        }
        with (
            patch.dict("os.environ", {"DEEPSEEK_API_KEY": "deepseek-secret"}, clear=True),
            patch("bosshunter.ai.credentials.httpx.post", return_value=CompletionResponse()) as post,
        ):
            credentials.call_openai_compatible_text("prompt", config, 8)

        self.assertEqual(post.call_args.kwargs["json"]["model"], "deepseek-v4-flash")
        self.assertEqual(config["ai"]["model"], "DeepSeek-V4-Flash")

    def test_custom_compatible_model_name_preserves_case_in_the_outbound_request(self):
        class CompletionResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        config = {
            "ai": {
                "service": "custom",
                "provider": "openai_compatible",
                "base_url": "https://compatible.example/v1",
                "api_key": "local-test-secret",
                "model": "Vendor/CaseSensitive-Model",
            }
        }
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("bosshunter.ai.credentials.httpx.post", return_value=CompletionResponse()) as post,
        ):
            credentials.call_openai_compatible_text("prompt", config, 8)

        self.assertEqual(post.call_args.kwargs["json"]["model"], "Vendor/CaseSensitive-Model")

    def test_openai_compatible_accepts_array_content(self):
        class CompletionResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "choices": [{
                        "message": {
                            "content": [
                                {"type": "text", "text": "第一段"},
                                {"type": "output_text", "text": "第二段"},
                            ]
                        }
                    }]
                }

        config = {
            "ai": {
                "service": "deepseek",
                "provider": "openai_compatible",
                "model": "provider-current-model",
                "thinking": "off",
            }
        }
        with (
            patch.dict("os.environ", {"DEEPSEEK_API_KEY": "deepseek-secret"}, clear=True),
            patch("bosshunter.ai.credentials.httpx.post", return_value=CompletionResponse()),
        ):
            result = credentials.call_openai_compatible_text("prompt", config, 123)

        self.assertEqual(result, "第一段\n第二段")

    def test_openai_compatible_does_not_use_reasoning_as_final_answer(self):
        class CompletionResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "choices": [{
                        "finish_reason": "stop",
                        "message": {
                            "content": None,
                            "reasoning_content": "这只是模型思考过程",
                        },
                    }]
                }

        config = {
            "ai": {
                "service": "deepseek",
                "provider": "openai_compatible",
                "model": "provider-current-model",
                "thinking": "off",
            }
        }
        with (
            patch.dict("os.environ", {"DEEPSEEK_API_KEY": "deepseek-secret"}, clear=True),
            patch("bosshunter.ai.credentials.httpx.post", return_value=CompletionResponse()),
            self.assertRaises(credentials.AIRequestError) as raised,
        ):
            credentials.call_openai_compatible_text("prompt", config, 123)

        self.assertEqual(raised.exception.kind, "empty_response")
        self.assertNotIn("这只是模型思考过程", str(raised.exception))

    def test_openai_empty_choices_raises_empty_response(self):
        class EmptyChoicesResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": []}

        config = {
            "ai": {
                "service": "deepseek",
                "provider": "openai_compatible",
                "model": "deepseek-chat",
            }
        }
        with (
            patch.dict("os.environ", {"DEEPSEEK_API_KEY": "deepseek-secret"}, clear=True),
            patch("bosshunter.ai.credentials.httpx.post", return_value=EmptyChoicesResponse()),
            self.assertRaises(credentials.AIRequestError) as raised,
        ):
            credentials.call_openai_compatible_text("prompt", config, 256)

        self.assertEqual(raised.exception.kind, "empty_response")

    def test_anthropic_thinking_only_response_raises_empty_response(self):
        class Client:
            def __init__(self, **kwargs):
                self.messages = self

            def create(self, **kwargs):
                return SimpleNamespace(content=[SimpleNamespace(thinking="只是思考过程")])

        config = {
            "ai": {
                "api_key": "key",
                "model": "claude-sonnet-4-6",
                "thinking": "auto",
                "thinking_budget": 2048,
            }
        }
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(credentials, "resolve_anthropic_model", return_value="claude-sonnet-4-6"),
            patch.dict("sys.modules", {"anthropic": SimpleNamespace(Anthropic=Client)}),
            self.assertRaises(credentials.AIRequestError) as raised,
        ):
            credentials.call_anthropic_text("prompt", config, 256)

        self.assertEqual(raised.exception.kind, "empty_response")

    def test_openai_compatible_auto_thinking_falls_back_only_for_compatibility_error(self):
        class UnsupportedThinkingResponse:
            status_code = 400

            def raise_for_status(self):
                raise RuntimeError("unsupported parameter: thinking")

            def json(self):
                return {"error": {"message": "unsupported parameter: thinking"}}

        class CompletionResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": " fallback ok "}}]}

        config = {
            "ai": {
                "service": "deepseek",
                "provider": "openai_compatible",
                "model": "deepseek-chat",
                "thinking": "auto",
                "thinking_budget": 2048,
            }
        }
        with (
            patch.dict("os.environ", {"DEEPSEEK_API_KEY": "deepseek-secret"}, clear=True),
            patch(
                "bosshunter.ai.credentials.httpx.post",
                side_effect=[UnsupportedThinkingResponse(), CompletionResponse()],
            ) as post,
        ):
            result = credentials.call_openai_compatible_text("prompt", config, 256)

        self.assertEqual(result, "fallback ok")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].kwargs["json"]["thinking"], {"type": "disabled"})
        self.assertNotIn("thinking", post.call_args_list[1].kwargs["json"])
        self.assertEqual(post.call_args_list[1].kwargs["json"]["max_tokens"], 3072)

    def test_openai_enabled_thinking_uses_provider_default_reasoning_mode(self):
        class CompletionResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": " ok "}}]}

        config = {
            "ai": {
                "service": "deepseek",
                "provider": "openai_compatible",
                "model": "deepseek-reasoner",
                "thinking": "enabled",
                "thinking_budget": 2048,
            }
        }
        with (
            patch.dict("os.environ", {"DEEPSEEK_API_KEY": "deepseek-secret"}, clear=True),
            patch("bosshunter.ai.credentials.httpx.post", return_value=CompletionResponse()) as post,
        ):
            result = credentials.call_openai_compatible_text("prompt", config, 256)

        self.assertEqual(result, "ok")
        payload = post.call_args.kwargs["json"]
        self.assertNotIn("thinking", payload)
        self.assertEqual(payload["max_tokens"], 3072)


if __name__ == "__main__":
    unittest.main()
