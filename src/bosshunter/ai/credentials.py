"""Credential helpers for AI providers."""

import os


def get_anthropic_api_key(config: dict) -> str | None:
    """Resolve the Anthropic API key from env or config."""
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    return (
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or ai_cfg.get("api_key")
    )
