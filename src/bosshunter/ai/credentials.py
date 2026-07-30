"""Credential helpers for AI providers."""

import hashlib
import os
import re

import httpx

from bosshunter.config import AI_SERVICE_PRESETS


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


def get_ai_service(config: dict) -> str:
    """Return the configured user-facing AI service, preserving legacy configs."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    service = str(ai_cfg.get("service") or "").strip()
    if service in AI_SERVICE_PRESETS:
        return service
    return "custom" if ai_cfg.get("provider") == "openai_compatible" else "anthropic"


def get_ai_api_key(config: dict) -> str | None:
    """Resolve a standard API key without exposing or copying its value."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    service = get_ai_service(config)
    if service == "deepseek":
        return (
            os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ai_cfg.get("api_key")
        )
    if service == "doubao":
        return (
            os.environ.get("ARK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ai_cfg.get("api_key")
        )
    if service == "custom":
        return (
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
            or ai_cfg.get("api_key")
            or ai_cfg.get("auth_token")
        )
    return get_anthropic_api_key(config)


def get_ai_key_source(config: dict) -> str | None:
    """Return only the credential source name, never the credential value."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
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
    if ai_cfg.get("api_key"):
        return "本地配置"
    if ai_cfg.get("auth_token"):
        return "本地配置（Auth Token）"
    return None


def get_ai_base_url(config: dict) -> str | None:
    """Resolve the service-specific API base URL."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    service = get_ai_service(config)
    if service == "deepseek":
        return (
            os.environ.get("DEEPSEEK_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or ai_cfg.get("base_url")
            or AI_SERVICE_PRESETS[service]["base_url"]
        )
    if service == "doubao":
        return (
            os.environ.get("ARK_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or ai_cfg.get("base_url")
            or AI_SERVICE_PRESETS[service]["base_url"]
        )
    if service == "custom":
        return (
            os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("ANTHROPIC_BASE_URL")
            or ai_cfg.get("base_url")
        )
    return os.environ.get("ANTHROPIC_BASE_URL") or ai_cfg.get("base_url")


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
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    for block in response.content:
        text = getattr(block, "text", None)
        if text is not None:
            return text.strip()
    return None


def call_openai_compatible_text(prompt: str, config: dict, max_tokens: int) -> str | None:
    """Call an OpenAI-compatible chat completions endpoint."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    api_key = get_ai_api_key(config)
    base_url = get_ai_base_url(config)
    model = ai_cfg.get("model") or ""
    if not api_key or not base_url:
        return None

    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.2,
            },
            timeout=60,
        )
        response.raise_for_status()
        choices = response.json().get("choices", [])
        if not choices:
            return None
        content = choices[0].get("message", {}).get("content")
        return content.strip() if isinstance(content, str) else None
    except Exception:
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
