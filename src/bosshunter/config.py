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
        "education": "",
        "recruitment_type": "",
        "greeting_preference": "",
        "salary_min": 0,
        "salary_max": 0,
        "allow_internship": False,
        "deal_breakers": [],
        "jd_deal_breakers": [],
        "blocked_companies": [],
    },
    "search": {
        "keywords": [],
        "cities": [],  # Empty = fallback to profile.target_cities
        "city_codes": {},
        "max_pages": 3,
        "sort": "default",
    },
    "collection": {
        "default_order": ["boss"],
        "auto_score_default": False,
        "daily_search_page_limit": 30,
        "daily_detail_page_limit": 150,
        "max_consecutive_page_failures": 3,
        "risk_pause_min_minutes": 5,
        "risk_pause_max_minutes": 10,
        "collection_delay_multiplier": 1.5,
        "delivery_cooldown_min_minutes": 5,
        "delivery_cooldown_max_minutes": 15,
    },
    "platforms": {
        "boss": {
            "enabled": True,
            "search": {
                "keywords": [],
                "cities": [],
                "city_codes": {},
                "max_pages": 3,
                "sort": "default",
            },
        },
        "zhilian": {
            "enabled": False,
            "search": {
                "keywords": [],
                "cities": [],
                "city_codes": {},
                "max_pages": 3,
                "sort": "default",
            },
        },
        "51job": {
            "enabled": False,
            "search": {
                "keywords": [],
                "cities": ["上海"],
                "city_codes": {"上海": "020000"},
                "max_pages": 1,
                "sort": "default",
            },
        },
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
        "scoring_concurrency": 1,
        "scoring_second_review": False,
        "greeting_max_tokens": 8192,
        "greeting_review_max_tokens": 4096,
        "greeting_max_attempts": 2,
        "greeting_review_threshold": 7.0,
        "greeting_max_iterations": 2,
        "greeting_style_suggestions": True,
        "greeting_auto_apply_style": False,
    },
    "monitor": {
        "interval": 30,  # 分钟
        "initial_cooldown_minutes": 10,
        "chat_url": "https://www.zhipin.com/web/geek/chat",
        "max_conversations_per_cycle": 5,
        "max_consecutive_page_failures": 3,
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
    "safety": {
        "daily_platform_page_limit": 500,
        "risk_lock_minutes": 10,
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
        if isinstance(user_cfg, dict):
            _deep_merge(cfg, user_cfg)
    _normalize_config_sections(cfg)
    _validate_ai_provider(cfg)
    return cfg


def _normalize_config_sections(config: dict[str, Any]) -> dict[str, Any]:
    """Replace malformed sections and discard retired collection-count settings."""
    for section, defaults in DEFAULTS.items():
        if isinstance(defaults, dict) and not isinstance(config.get(section), dict):
            config[section] = _deep_copy_dict(defaults)

    return remove_retired_collection_settings(config)


def remove_retired_collection_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Remove collection-count settings that are no longer supported."""

    # These settings existed briefly, but a result-count limit is not a page-access
    # safety control. Ignore stale values so old config files cannot re-enable it or
    # make the removed fields reappear in the Web UI/API.
    collection = config.get("collection", {})
    collection.pop("daily_new_jobs_limit", None)
    collection.pop("default_target_count", None)
    if "delivery_cooldown_min_minutes" in collection or "delivery_cooldown_max_minutes" in collection:
        collection.pop("delivery_cooldown_minutes", None)
    search = config.get("search", {})
    search.pop("target_count", None)
    platforms = config.get("platforms", {})
    for platform_config in platforms.values():
        if isinstance(platform_config, dict):
            platform_search = platform_config.get("search", {})
            if isinstance(platform_search, dict):
                platform_search.pop("target_count", None)
    return config


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
