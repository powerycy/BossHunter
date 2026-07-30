"""Credential helpers for AI providers."""

import hashlib
import os
import re

import httpx


_MODEL_RESOLVE_CACHE: dict[tuple[str, str, str], str] = {}


def get_anthropic_api_key(config: dict) -> str | None:
    """Resolve the Anthropic API key from env or config."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    return (
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or ai_cfg.get("api_key")
        or ai_cfg.get("auth_token")
    )


def build_anthropic_client_kwargs(config: dict) -> dict:
    """Build Anthropic SDK client kwargs from env and config."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    api_key = os.environ.get("ANTHROPIC_API_KEY") or ai_cfg.get("api_key")
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or ai_cfg.get("auth_token")

    kwargs = {}
    if api_key:
        kwargs["api_key"] = api_key
    if auth_token:
        kwargs["auth_token"] = auth_token

    base_url = os.environ.get("ANTHROPIC_BASE_URL") or ai_cfg.get("base_url")
    if base_url:
        kwargs["base_url"] = base_url

    return kwargs


def resolve_anthropic_model(model: str, config: dict) -> str:
    """Resolve configured model name against compatible API model IDs when needed."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or ai_cfg.get("base_url")
    if not base_url:
        return model

    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or ai_cfg.get("auth_token")
    api_key = os.environ.get("ANTHROPIC_API_KEY") or ai_cfg.get("api_key")
    cache_key = (base_url.rstrip("/"), model, _credential_fingerprint(auth_token or api_key or ""))
    if cache_key in _MODEL_RESOLVE_CACHE:
        return _MODEL_RESOLVE_CACHE[cache_key]

    headers = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    elif api_key:
        headers["x-api-key"] = api_key

    try:
        response = httpx.get(f"{base_url.rstrip('/')}/v1/models", headers=headers, timeout=10)
        response.raise_for_status()
        model_ids = [item.get("id", "") for item in response.json().get("data", [])]
    except Exception:
        _MODEL_RESOLVE_CACHE[cache_key] = model
        return model

    resolved = _match_model_name(model, model_ids) or model
    _MODEL_RESOLVE_CACHE[cache_key] = resolved
    return resolved


def call_anthropic_text(prompt: str, config: dict, max_tokens: int) -> str | None:
    """Call Anthropic-compatible Messages API and return the first text block."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    if ai_cfg.get("provider") == "openai_compatible":
        return call_openai_compatible_text(prompt, config, max_tokens)

    try:
        import anthropic
    except ImportError:
        return None

    if not get_anthropic_api_key(config):
        return None

    model = resolve_anthropic_model(ai_cfg.get("model", "claude-sonnet-4-6"), config)
    client = anthropic.Anthropic(**build_anthropic_client_kwargs(config))
    return _extract_first_text(client, model, prompt, max_tokens, config)


def _thinking_mode(config: dict) -> str:
    """Resolve extended-thinking mode from config. One of auto/disabled/enabled/off."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    mode = str(ai_cfg.get("thinking", "auto")).strip().lower()
    if mode not in {"auto", "disabled", "enabled", "off"}:
        mode = "auto"
    return mode


def _thinking_budget(config: dict) -> int:
    """Thinking budget tokens when mode=enabled. Clamped to >=1024."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    try:
        return max(int(ai_cfg.get("thinking_budget", 2048)), 1024)
    except (TypeError, ValueError):
        return 2048


def _thinking_strategies(mode: str, budget: int, max_tokens: int) -> list[dict]:
    """Build ordered messages.create kwarg strategies for the given thinking mode.

    Each strategy is merged into {model, messages} on call. Earlier strategies are
    preferred; later ones act as fallbacks (e.g. when a compatible service rejects
    the thinking parameter, or a thinking response still yields no TextBlock).
    """
    big = max(max_tokens, 2048)
    if mode == "disabled":
        # 强制禁用 thinking；失败时用更大 max_tokens 重试一次（仍保持禁用）
        return [
            {"thinking": {"type": "disabled"}},
            {"thinking": {"type": "disabled"}, "max_tokens": big},
        ]
    if mode == "enabled":
        # Anthropic requires max_tokens > thinking budget
        return [{"thinking": {"type": "enabled", "budget_tokens": budget}, "max_tokens": max(max_tokens, budget + 1024)}]
    if mode == "off":
        # 不传 thinking 参数；失败时放大 max_tokens 重试一次（仍不传该参数）
        return [
            {},
            {"max_tokens": big},
        ]
    # auto: prefer disabled (clean TextBlock), then disabled + larger budget,
    # finally default behaviour with a larger budget for models that always think.
    return [
        {"thinking": {"type": "disabled"}},
        {"thinking": {"type": "disabled"}, "max_tokens": big},
        {"max_tokens": big},
    ]


def _extract_first_text(client, model: str, prompt: str, max_tokens: int, config: dict) -> str | None:
    """Call messages.create and return the first TextBlock text.

    兼容默认开启 extended thinking 的模型（如小米 MiMo mimo-v2.5）：thinking 可能
    吃满 max_tokens，导致 response.content 只剩 ThinkingBlock（属性是 .thinking 而非
    .text），原实现遍历 .text 落空后静默返回 None，评分/招呼语被整体跳过。

    行为由 config.ai.thinking 控制：
      auto      优先禁用 thinking 拿 TextBlock，失败则放大 max_tokens 重试（默认，推荐）
      disabled  强制禁用 thinking（适合 MiMo 等默认开 thinking 的模型）
      enabled   启用 thinking（需配合 thinking_budget，max_tokens 会自动放大到 > budget）
      off       不传 thinking 参数（兼容不支持该参数的兼容服务）
    """
    mode = _thinking_mode(config)
    budget = _thinking_budget(config)
    for strategy in _thinking_strategies(mode, budget, max_tokens):
        try:
            response = client.messages.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                **strategy,
            )
        except Exception:
            # 例如兼容服务不接受 thinking 参数 -> 跳到下一个策略
            continue
        for block in response.content:
            text = getattr(block, "text", None)
            if text is not None:
                return text.strip()
    return None


def _openai_thinking_strategies(mode: str, max_tokens: int) -> list[dict]:
    """Build ordered chat/completions payload overrides for the given thinking mode.

    OpenAI 兼容的推理模型（小米 MiMo mimo、DeepSeek v4 等）默认开启 thinking，思考
    内容放在 reasoning_content 字段，会吃满 max_tokens 导致 content 为空或被截断，
    评分/招呼语因此静默失败。实测 `thinking={'type':'disabled'}` 在小米与 DeepSeek
    上都能完全禁用 thinking（rt=None、无 reasoning_content），故 auto/disabled 优先用它；
    enabled 模式不传 disabled、改用更大 max_tokens 让 thinking 与 content 都有产出空间。
    """
    big = max(max_tokens, 2048)
    if mode == "disabled":
        return [
            {"thinking": {"type": "disabled"}},
            {"thinking": {"type": "disabled"}, "max_tokens": big},
        ]
    if mode == "enabled":
        return [{"max_tokens": big}]
    if mode == "off":
        return [{}, {"max_tokens": big}]
    # auto
    return [
        {"thinking": {"type": "disabled"}},
        {"thinking": {"type": "disabled"}, "max_tokens": big},
        {"max_tokens": big},
    ]


def call_openai_compatible_text(prompt: str, config: dict, max_tokens: int) -> str | None:
    """Call an OpenAI-compatible chat completions endpoint."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or ai_cfg.get("api_key")
    base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL") or ai_cfg.get("base_url")
    model = ai_cfg.get("model", "deepseek-chat")
    if not api_key or not base_url:
        return None

    mode = _thinking_mode(config)
    for strategy in _openai_thinking_strategies(mode, max_tokens):
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        payload.update(strategy)
        try:
            response = httpx.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            choices = response.json().get("choices", [])
            if not choices:
                continue
            content = choices[0].get("message", {}).get("content")
            # 空字符串（thinking 吃满 max_tokens）视为失败，进入下一策略重试
            if isinstance(content, str) and content.strip():
                return content.strip()
        except Exception:
            continue
    return None


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
