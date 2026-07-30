"""Configuration loader for BossHunter."""

from pathlib import Path
from typing import Any

import yaml


# BOSS直聘城市编码映射
CITY_CODES: dict[str, str] = {
    "北京": "101010100",
    "上海": "101020100",
    "深圳": "101280600",
    "广州": "101280100",
    "杭州": "101210100",
    "成都": "101270100",
    "武汉": "101200100",
    "南京": "101190100",
    "西安": "101110100",
    "苏州": "101190400",
    "天津": "101030100",
    "重庆": "101040100",
    "郑州": "101180100",
    "长沙": "101250100",
    "东莞": "101281600",
    "佛山": "101280800",
    "合肥": "101220100",
    "厦门": "101230200",
    "青岛": "101120200",
    "大连": "101070200",
}


SUPPORTED_AI_PROVIDERS = {"anthropic", "openai_compatible"}
SUPPORTED_AI_SERVICES = {"anthropic", "deepseek", "doubao", "custom"}
AI_SERVICE_PRESETS: dict[str, dict[str, str]] = {
    "anthropic": {
        "provider": "anthropic",
        "label": "Claude / Anthropic",
        "base_url": "",
        "key_env": "ANTHROPIC_API_KEY",
    },
    "deepseek": {
        "provider": "openai_compatible",
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "doubao": {
        "provider": "openai_compatible",
        "label": "豆包 / 火山方舟",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "key_env": "ARK_API_KEY",
    },
    "custom": {
        "provider": "openai_compatible",
        "label": "其他 OpenAI 兼容接口",
        "base_url": "",
        "key_env": "OPENAI_API_KEY",
    },
}


DEFAULTS: dict[str, Any] = {
    "profile": {
        "resume_path": "./resume.md",
        "resume_output_dir": "./data/resumes",
        "target_cities": ["北京"],
        "salary_min": 0,
        "salary_max": 0,
        "allow_internship": False,
        "deal_breakers": [],
        "allow_internship": False,
    },
    "search": {
        "keywords": [],
        "cities": [],  # Empty = fallback to profile.target_cities
        "max_pages": 3,
    },
    "scoring": {
        "threshold": 71,
        "max_candidates": 20,
    },
    "throttle": {
        "daily_limit": 30,
        "interval_min": 60,
        "interval_max": 180,
        "browse_before_greet": True,
        "browse_duration_min": 15,
        "browse_duration_max": 30,
        "send_windows": ["09:00-16:00"],
        "day_off_probability": 0.05,
    },
    "ai": {
        "provider": "anthropic",
        "service": "anthropic",
        "model": "claude-sonnet-4-6",
        "thinking": "auto",
        "thinking_budget": 2048,
        "timeout_seconds": 180,
        "scoring_max_tokens": 8192,
        "scoring_max_attempts": 2,
        "greeting_max_tokens": 8192,
        "greeting_review_max_tokens": 4096,
        "greeting_max_attempts": 2,
        "greeting_review_threshold": 7.0,
        "greeting_max_iterations": 2,
    },
    "monitor": {
        "interval": 30,  # 分钟
        "chat_url": "https://www.zhipin.com/web/geek/chat",
        "max_resume_sends_per_cycle": 5,
        "auto_reply_hr_questions": False,
    },
    "follow_up": {
        "enabled": False,
        "interval_hours": 48,
        "skip_weekends": True,
    },
    "dedup": {
        "history_file": "./data/history.jsonl",
    },
    "browser": {
        "runtime": "builtin",
        "proxy_host": "127.0.0.1",
        "proxy_port": 3456,
        "chrome_ports": [9222, 9229, 9333],
        "auto_start_proxy": True,
        "enable_port_guard": True,
        "site_patterns": True,
    },
}


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load configuration from YAML file, falling back to defaults."""
    cfg = _deep_copy_dict(DEFAULTS)
    if config_path is None:
        config_path = Path("config.yaml")
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
        _deep_merge(cfg, user_cfg)
    _validate_ai_provider(cfg)
    return cfg


def _validate_ai_provider(config: dict[str, Any]) -> None:
    """Fail fast when the configured AI provider is not supported."""
    ai_cfg = config.get("ai", {})
    provider = ai_cfg.get("provider", "anthropic")
    if provider not in SUPPORTED_AI_PROVIDERS:
        raise ValueError("当前版本支持 Anthropic 或 OpenAI 兼容接口。")
    service = ai_cfg.get("service", "anthropic")
    if provider == "openai_compatible" and service == "anthropic":
        # Legacy configs only had `provider`; preserve them as custom OpenAI-compatible.
        ai_cfg["service"] = "custom"
        service = "custom"
    if service not in SUPPORTED_AI_SERVICES:
        raise ValueError("当前版本支持 Claude、DeepSeek、豆包或自定义 OpenAI 兼容接口。")
    expected_provider = AI_SERVICE_PRESETS[service]["provider"]
    if provider != expected_provider:
        ai_cfg["provider"] = expected_provider


def _deep_copy_dict(d: dict) -> dict:
    result = {}
    for k, v in d.items():
        if isinstance(v, dict):
            result[k] = _deep_copy_dict(v)
        elif isinstance(v, list):
            result[k] = v[:]
        else:
            result[k] = v
    return result


def _deep_merge(base: dict, override: dict) -> None:
    """Recursively merge override into base."""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
