"""Credential helpers for AI providers."""

import hashlib
import os
import re

import httpx

from bosshunter.config import AI_SERVICE_PRESETS


_MODEL_RESOLVE_CACHE: dict[tuple[str, str, str], str] = {}
_THINKING_MODES = {"auto", "disabled", "enabled", "off"}


def _ambient_proxy_vars() -> bool:
    """Return true when process-level proxy settings could affect AI calls."""
    return any(os.environ.get(name) for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"))


class AIRequestError(RuntimeError):
    """Normalized AI request failure without exposing credentials or raw payloads."""

    def __init__(self, kind: str, user_message: str, status_code: int | None = None):
        super().__init__(user_message)
        self.kind = kind
        self.user_message = user_message
        self.status_code = status_code

    # kind/status_code 不进 __str__ 的话，raise 链下游只能拿到兜底文案，
    # UI 永远分不清是限流、鉴权还是额度问题（issue #101）。
    def __str__(self) -> str:
        suffix = f" ({self.kind}" + (f", status={self.status_code}" if self.status_code else "") + ")"
        return self.user_message + suffix

    def __repr__(self) -> str:
        return (
            f"AIRequestError(kind={self.kind!r}, status_code={self.status_code!r}, "
            f"user_message={self.user_message!r})"
        )


def _extract_text_content(value: object) -> str | None:
    """Normalize provider text blocks without treating reasoning as final output."""
    if isinstance(value, str):
        text = value.strip()
        return text or None

    if isinstance(value, (list, tuple)):
        parts = [_extract_text_content(item) for item in value]
        text = "\n".join(part for part in parts if part)
        return text.strip() or None

    if isinstance(value, dict):
        block_type = str(value.get("type") or "").lower()
        if block_type in {"thinking", "reasoning", "analysis"}:
            return None
        for key in ("text", "content", "output_text"):
            if key in value:
                text = _extract_text_content(value.get(key))
                if text:
                    return text
        return None

    block_type = str(getattr(value, "type", "") or "").lower()
    if block_type in {"thinking", "reasoning", "analysis"}:
        return None
    for key in ("text", "content", "output_text"):
        if hasattr(value, key):
            text = _extract_text_content(getattr(value, key))
            if text:
                return text
    return None


def _response_error_detail(response: object | None) -> str:
    if response is None:
        return ""
    try:
        payload = response.json()
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error", payload)
    if isinstance(error, dict):
        return " ".join(
            str(error.get(key) or "")
            for key in ("code", "type", "message")
        ).strip()
    return str(error or "")


def normalize_ai_error(exc: Exception, response: object | None = None) -> AIRequestError:
    """Classify provider errors into actionable, credential-safe categories."""
    if isinstance(exc, AIRequestError):
        return exc

    resolved_response = response if response is not None else getattr(exc, "response", None)
    status_code = getattr(exc, "status_code", None) or getattr(resolved_response, "status_code", None)
    raw = f"{type(exc).__name__} {exc} {_response_error_detail(resolved_response)}".lower()

    output_limit_markers = (
        "max_tokens",
        "max completion tokens",
        "maximum output tokens",
        "tokens to sample",
    )
    context_markers = (
        "context_length",
        "context length",
        "maximum context",
        "max context",
        "input tokens",
        "prompt tokens",
        "too many tokens",
        "prompt is too long",
        "request too large",
        "上下文",
        "输入过长",
    )
    quota_markers = (
        "insufficient_quota",
        "quota exceeded",
        "token quota",
        "billing",
        "balance",
        "credit",
        "budget",
        "overdue",
        "payment required",
        "余额不足",
        "额度不足",
    )
    rate_markers = (
        "rate limit",
        "too many requests",
        "requests per minute",
        "tokens per minute",
        " tpm",
        " rpm",
        "限流",
        "频率限制",
    )

    # 判定顺序：明确的 401/403 状态最先归为鉴权——错误体关键词只是启发式，401+「额度」类
    # 混排错误体不应误导恢复建议。其后额度/限流先于上下文：真实错误体常混排多种提示（如
    # "余额不足，请减少输入过长内容"），先查 context marker 会把额度问题误报成上下文超限
    # （issue #101）。quota marker 仍优先于 429 状态码：OpenAI 的 insufficient_quota 实际就配 429 返回。
    if status_code in {401, 403}:
        return AIRequestError("auth", "AI API Key 无效或当前模型没有访问权限", status_code)
    if status_code == 402 or any(marker in raw for marker in quota_markers):
        return AIRequestError("token_quota", "AI Token 额度或账户余额不足", status_code)
    if status_code == 429 or any(marker in raw for marker in rate_markers):
        return AIRequestError("rate_limit", "AI 服务触发请求或 Token 频率限制", status_code)
    if any(marker in raw for marker in context_markers):
        return AIRequestError("context_limit", "请求内容超过当前模型的上下文限制", status_code)
    if any(marker in raw for marker in output_limit_markers):
        return AIRequestError("output_limit", "当前模型不接受设置的输出 Token 上限", status_code)
    if isinstance(exc, httpx.InvalidURL):
        return AIRequestError("invalid_url", "AI 接口地址无效", status_code)
    if isinstance(exc, httpx.RequestError):
        return AIRequestError("network", "AI 服务连接失败或超时", status_code)
    return AIRequestError("request_failed", "AI 服务请求失败", status_code)


def _coerce_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _coerce_timeout(value: object, default: float = 60.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(5.0, min(parsed, 600.0))


def resolve_thinking_options(
    config: dict,
    purpose: str | None = None,
) -> tuple[str, int]:
    """Resolve protocol-agnostic thinking settings for one AI task."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    mode_key = f"{purpose}_thinking_mode" if purpose else ""
    budget_key = f"{purpose}_thinking_budget" if purpose else ""
    family_mode = ai_cfg.get("greeting_thinking_mode") if purpose == "greeting_review" else None
    family_budget = ai_cfg.get("greeting_thinking_budget") if purpose == "greeting_review" else None
    mode = str(
        (ai_cfg.get(mode_key) if mode_key else None)
        or family_mode
        or ai_cfg.get("thinking")
        or "off"
    ).strip().lower()
    if mode == "default":
        mode = "off"
    if mode not in _THINKING_MODES:
        mode = "auto"
    budget = _coerce_int(
        (ai_cfg.get(budget_key) if budget_key else None)
        or family_budget
        or ai_cfg.get("thinking_budget")
        or 2048,
        2048,
        1024,
        32768,
    )
    return mode, budget


def _thinking_strategies(
    mode: str,
    budget: int,
    max_tokens: int,
) -> list[tuple[dict, int]]:
    """Return Anthropic request strategies without hiding non-compatibility errors."""
    expanded_tokens = max(max_tokens, budget + 1024)
    if mode == "disabled":
        strategies = [({"thinking": {"type": "disabled"}}, max_tokens)]
        if expanded_tokens != max_tokens:
            strategies.append(({"thinking": {"type": "disabled"}}, expanded_tokens))
        return strategies
    if mode == "enabled":
        return [
            (
                {"thinking": {"type": "enabled", "budget_tokens": budget}},
                expanded_tokens,
            )
        ]
    if mode == "auto":
        strategies = [({"thinking": {"type": "disabled"}}, max_tokens)]
        if expanded_tokens != max_tokens:
            strategies.append(({"thinking": {"type": "disabled"}}, expanded_tokens))
        strategies.append(({}, expanded_tokens))
        return strategies
    strategies = [({}, max_tokens)]
    if expanded_tokens != max_tokens:
        strategies.append(({}, expanded_tokens))
    return strategies


def _openai_thinking_strategies(
    mode: str,
    budget: int,
    max_tokens: int,
) -> list[tuple[dict, int]]:
    """Return OpenAI-compatible strategies for providers with default reasoning."""
    expanded_tokens = max(max_tokens, budget + 1024)
    if mode == "enabled":
        # Compatible providers generally enable reasoning by default and do not
        # consistently accept Anthropic's explicit enabled parameter.
        return [({}, expanded_tokens)]
    if mode == "disabled":
        strategies = [({"thinking": {"type": "disabled"}}, max_tokens)]
        if expanded_tokens != max_tokens:
            strategies.append(({"thinking": {"type": "disabled"}}, expanded_tokens))
        return strategies
    if mode == "auto":
        strategies = [({"thinking": {"type": "disabled"}}, max_tokens)]
        if expanded_tokens != max_tokens:
            strategies.append(({"thinking": {"type": "disabled"}}, expanded_tokens))
        strategies.append(({}, expanded_tokens))
        return strategies
    strategies = [({}, max_tokens)]
    if expanded_tokens != max_tokens:
        strategies.append(({}, expanded_tokens))
    return strategies


def _is_thinking_compatibility_error(exc: Exception, response: object | None = None) -> bool:
    resolved_response = response if response is not None else getattr(exc, "response", None)
    status_code = getattr(exc, "status_code", None) or getattr(resolved_response, "status_code", None)
    if status_code not in {400, 404, 422}:
        return False
    raw = f"{exc} {_response_error_detail(resolved_response)}".lower()
    parameter_markers = (
        "thinking",
        "budget_tokens",
        "unknown field",
        "unknown parameter",
        "extra inputs",
        "not permitted",
        "unsupported parameter",
        "unrecognized",
    )
    return any(marker in raw for marker in parameter_markers)


def get_anthropic_api_key(config: dict) -> str | None:
    """Resolve the Anthropic API key from config first, then env as fallback."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    return (
        ai_cfg.get("api_key")
        or ai_cfg.get("auth_token")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    )


def get_ai_service(config: dict) -> str:
    """Return the configured user-facing AI service, preserving legacy configs."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    service = str(ai_cfg.get("service") or "").strip()
    if service in AI_SERVICE_PRESETS:
        return service
    return "custom" if ai_cfg.get("provider") == "openai_compatible" else "anthropic"


def get_ai_api_key(config: dict) -> str | None:
    """Resolve a standard API key from config first, then env, without exposing its value."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    service = get_ai_service(config)
    if service == "deepseek":
        return (
            ai_cfg.get("api_key")
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
    if service == "doubao":
        return (
            ai_cfg.get("api_key")
            or os.environ.get("ARK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
    if service == "custom":
        return (
            ai_cfg.get("api_key")
            or ai_cfg.get("auth_token")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
        )
    return get_anthropic_api_key(config)


def get_ai_key_source(config: dict) -> str | None:
    """Return only the credential source name, never the credential value."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    if ai_cfg.get("api_key"):
        return "本地配置"
    if ai_cfg.get("auth_token"):
        return "本地配置（Auth Token）"
    service = get_ai_service(config)
    candidates = {
        "deepseek": ("DEEPSEEK_API_KEY", "OPENAI_API_KEY"),
        "doubao": ("ARK_API_KEY", "OPENAI_API_KEY"),
        "custom": ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"),
        "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    }[service]
    for env_name in candidates:
        if os.environ.get(env_name):
            return env_name
    return None


def get_ai_base_url(config: dict) -> str | None:
    """Resolve the service-specific API base URL from config first, then env, then preset."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    service = get_ai_service(config)
    if service == "deepseek":
        return (
            ai_cfg.get("base_url")
            or os.environ.get("DEEPSEEK_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or AI_SERVICE_PRESETS[service]["base_url"]
        )
    if service == "doubao":
        return (
            ai_cfg.get("base_url")
            or os.environ.get("ARK_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or AI_SERVICE_PRESETS[service]["base_url"]
        )
    if service == "custom":
        return (
            ai_cfg.get("base_url")
            or os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("ANTHROPIC_BASE_URL")
        )
    return ai_cfg.get("base_url") or os.environ.get("ANTHROPIC_BASE_URL")


def get_ai_base_url_source(config: dict) -> str | None:
    """Return only the base URL source name, consistent with get_ai_base_url resolution."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    if ai_cfg.get("base_url"):
        return "本地配置"
    service = get_ai_service(config)
    env_names = {
        "deepseek": ("DEEPSEEK_BASE_URL", "OPENAI_BASE_URL"),
        "doubao": ("ARK_BASE_URL", "OPENAI_BASE_URL"),
        "custom": ("OPENAI_BASE_URL", "ANTHROPIC_BASE_URL"),
        "anthropic": ("ANTHROPIC_BASE_URL",),
    }[service]
    for env_name in env_names:
        if os.environ.get(env_name):
            return env_name
    if AI_SERVICE_PRESETS[service]["base_url"]:
        return "服务商预设"
    return None


def build_anthropic_client_kwargs(config: dict) -> dict:
    """Build Anthropic SDK client kwargs from config first, then env."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    # api_key/auth_token 是一对凭证：config 任一非空就整体不读 env，避免混用两个来源。
    api_key = ai_cfg.get("api_key")
    auth_token = ai_cfg.get("auth_token")
    if not api_key and not auth_token:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")

    kwargs = {}
    if api_key:
        kwargs["api_key"] = api_key
    if auth_token:
        kwargs["auth_token"] = auth_token

    base_url = ai_cfg.get("base_url") or os.environ.get("ANTHROPIC_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url

    if _ambient_proxy_vars():
        kwargs["http_client"] = httpx.Client(trust_env=False)

    return kwargs


def resolve_anthropic_model(model: str, config: dict) -> str:
    """Resolve configured model name against compatible API model IDs when needed."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    base_url = ai_cfg.get("base_url") or os.environ.get("ANTHROPIC_BASE_URL")
    if not base_url:
        return model

    if ai_cfg.get("api_key") or ai_cfg.get("auth_token"):
        auth_token = ai_cfg.get("auth_token")
        api_key = ai_cfg.get("api_key")
    else:
        auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    cache_key = (base_url.rstrip("/"), model, _credential_fingerprint(auth_token or api_key or ""))
    if cache_key in _MODEL_RESOLVE_CACHE:
        return _MODEL_RESOLVE_CACHE[cache_key]

    headers = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    elif api_key:
        headers["x-api-key"] = api_key

    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/v1/models",
            headers=headers,
            timeout=10,
            trust_env=False,
        )
        response.raise_for_status()
        model_ids = [item.get("id", "") for item in response.json().get("data", [])]
    except Exception:
        _MODEL_RESOLVE_CACHE[cache_key] = model
        return model

    resolved = _match_model_name(model, model_ids) or model
    _MODEL_RESOLVE_CACHE[cache_key] = resolved
    return resolved


def call_anthropic_text(
    prompt: str,
    config: dict,
    max_tokens: int,
    *,
    timeout: float | None = None,
    purpose: str | None = None,
) -> str | None:
    """Call Anthropic-compatible Messages API and return the first text block."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    if ai_cfg.get("provider") == "openai_compatible":
        return call_openai_compatible_text(
            prompt,
            config,
            max_tokens,
            timeout=timeout,
            purpose=purpose,
        )

    try:
        import anthropic
    except ImportError:
        return None

    if not get_anthropic_api_key(config):
        return None

    model = resolve_anthropic_model(ai_cfg.get("model", "claude-sonnet-4-6"), config)
    client = anthropic.Anthropic(**build_anthropic_client_kwargs(config))
    mode, budget = resolve_thinking_options(config, purpose)
    strategies = _thinking_strategies(mode, budget, max_tokens)
    thinking_rejected = False
    compatibility_error: Exception | None = None
    output_truncated = False
    for overrides, token_limit in strategies:
        if thinking_rejected and "thinking" in overrides:
            continue
        request_kwargs = {
            "model": model,
            "max_tokens": token_limit,
            "messages": [{"role": "user", "content": prompt}],
            **overrides,
        }
        if timeout is not None:
            request_kwargs["timeout"] = _coerce_timeout(timeout)
        try:
            response = client.messages.create(**request_kwargs)
        except Exception as exc:
            if _is_thinking_compatibility_error(exc):
                thinking_rejected = True
                compatibility_error = exc
                continue
            raise normalize_ai_error(exc) from exc
        if getattr(response, "stop_reason", None) == "max_tokens":
            output_truncated = True
            continue
        text = _extract_text_content(getattr(response, "content", None))
        if text:
            return text
    if output_truncated:
        raise AIRequestError("output_truncated", "AI 返回内容因输出 Token 上限被截断")
    if compatibility_error is not None:
        raise normalize_ai_error(compatibility_error) from compatibility_error
    # 所有策略都没有产出文本时显式报错：静默 return None 会让调用方把
    # thinking-only 响应记成无理由失败，截断和空响应全都不可见（issue #102）。
    raise AIRequestError("empty_response", "AI 服务没有返回文本内容，可能只返回了思考过程")


def call_openai_compatible_text(
    prompt: str,
    config: dict,
    max_tokens: int,
    *,
    timeout: float | None = None,
    purpose: str | None = None,
) -> str | None:
    """Call an OpenAI-compatible chat completions endpoint."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    api_key = get_ai_api_key(config)
    base_url = get_ai_base_url(config)
    model = get_openai_compatible_model(config)
    if _should_resolve_openai_model(model, config):
        model = resolve_openai_model(model, config)
    if not api_key or not base_url:
        return None

    mode, budget = resolve_thinking_options(config, purpose)
    strategies = _openai_thinking_strategies(mode, budget, max_tokens)
    request_timeout = _coerce_timeout(timeout if timeout is not None else ai_cfg.get("timeout_seconds", 60))
    thinking_rejected = False
    compatibility_error: Exception | None = None
    output_truncated = False
    for overrides, token_limit in strategies:
        if thinking_rejected and "thinking" in overrides:
            continue
        response = None
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": token_limit,
            "temperature": 0.2,
            **overrides,
        }
        try:
            response = httpx.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=request_timeout,
                trust_env=False,
            )
            response.raise_for_status()
        except Exception as exc:
            if _is_thinking_compatibility_error(exc, response):
                thinking_rejected = True
                compatibility_error = exc
                continue
            raise normalize_ai_error(exc, response) from exc

        payload_data = response.json()
        choices = payload_data.get("choices", []) if isinstance(payload_data, dict) else []
        if not choices:
            if isinstance(payload_data, dict):
                direct_text = _extract_text_content(payload_data.get("output_text"))
                if direct_text:
                    return direct_text
            continue
        choice = choices[0] if isinstance(choices[0], dict) else {}
        if choice.get("finish_reason") in {"length", "max_tokens"}:
            output_truncated = True
            continue
        message = choice.get("message", {})
        content = message.get("content") if isinstance(message, dict) else None
        text = _extract_text_content(content)
        if not text:
            text = _extract_text_content(choice.get("text"))
        if text:
            return text
    if output_truncated:
        raise AIRequestError("output_truncated", "AI 返回内容因输出 Token 上限被截断")
    if compatibility_error is not None:
        raise normalize_ai_error(compatibility_error) from compatibility_error
    # 同 call_anthropic_text：OpenAI 兼容路径此前在这里静默 return None，
    # 截断后空 choices、thinking-only 响应全部无声失败（issue #102）。
    raise AIRequestError("empty_response", "AI 服务没有返回文本内容，可能只返回了思考过程")


def get_openai_compatible_model(config: dict) -> str:
    """Return the API model ID, normalizing only DeepSeek's case-sensitive IDs."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    model = str(ai_cfg.get("model") or "").strip()
    return model.lower() if get_ai_service(config) == "deepseek" else model


def _should_resolve_openai_model(model: str, config: dict) -> bool:
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    if ai_cfg.get("resolve_model_aliases") is False:
        return False
    service = get_ai_service(config)
    return service == "deepseek" and bool(re.match(r"^v\d", model.strip().lower()))


def resolve_openai_model(model: str, config: dict) -> str:
    """Resolve an OpenAI-compatible shorthand against the provider's model list."""
    base_url = get_ai_base_url(config)
    api_key = get_ai_api_key(config)
    if not model or not base_url or not api_key:
        return model

    base = base_url.rstrip("/")
    cache_key = (base, model, _credential_fingerprint(api_key))
    if cache_key in _MODEL_RESOLVE_CACHE:
        return _MODEL_RESOLVE_CACHE[cache_key]

    urls = [f"{base}/models"]
    if not base.endswith("/v1"):
        urls.append(f"{base}/v1/models")

    resolved = model
    headers = {"Authorization": f"Bearer {api_key}"}
    for url in urls:
        try:
            response = httpx.get(url, headers=headers, timeout=10, trust_env=False)
            response.raise_for_status()
            payload = response.json()
            model_ids = [
                str(item.get("id") or "")
                for item in payload.get("data", [])
                if isinstance(item, dict)
            ]
        except Exception:
            continue
        resolved = _match_model_name(model, model_ids) or model
        break

    _MODEL_RESOLVE_CACHE[cache_key] = resolved
    return resolved


def _match_model_name(requested: str, available: list[str]) -> str | None:
    if requested in available:
        return requested

    requested_norm = _normalize_model_name(requested)
    for candidate in available:
        if _normalize_model_name(candidate) == requested_norm:
            return candidate

    for candidate in available:
        candidate_norm = _normalize_model_name(candidate)
        if requested_norm in candidate_norm or candidate_norm in requested_norm:
            return candidate

    requested_tokens = _model_tokens(requested)
    for candidate in available:
        if requested_tokens and requested_tokens <= _model_tokens(candidate):
            return candidate

    return None


def _credential_fingerprint(credential: str) -> str:
    return hashlib.sha256(credential.encode("utf-8")).hexdigest() if credential else ""


def _normalize_model_name(name: str) -> str:
    return re.sub(r"[\s._-]+", "", name).lower()


def _model_tokens(name: str) -> set[str]:
    return {token for token in re.split(r"[\s._-]+", name.lower()) if token}
