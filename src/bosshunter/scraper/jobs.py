"""Backward-compatible BOSS collection facade.
New multi-platform flows use ``CollectionOrchestrator`` directly. This module
keeps the historical ``scrape_jobs(config, keywords, limit)`` contract and
injects its old test seams into the standalone ``BossCollector``.
"""

from __future__ import annotations

import time

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from bosshunter.cancellation import get_stop_event
from bosshunter.collection.base import CollectorHooks
from bosshunter.collection.models import JobCandidate, PlatformCollectionRequest
from bosshunter.collection.platforms.boss import BossBrowser, BossCollector, generate_boss_job_id
from bosshunter.browser import close_tab, evaluate, new_tab, scroll, wait_for_load
from bosshunter.config import CITY_CODES
from bosshunter.db import get_db, insert_job, job_exists
from bosshunter.job_filters import matching_blocked_company, matching_deal_breaker
from bosshunter.throttle import PageThrottle

console = Console()


def _generate_job_id(url: str) -> str:
    """Retain the old private helper for integrations and tests."""
    return generate_boss_job_id(url)


def _resolve_city_code(city: str, config: dict) -> str | None:
    """Retain the historical custom-over-builtin BOSS city lookup helper."""
    search_config = config.get("search", {}) if isinstance(config.get("search"), dict) else {}
    custom_codes = search_config.get("city_codes") if isinstance(search_config.get("city_codes"), dict) else {}
    custom = custom_codes.get(city)
    if custom not in (None, ""):
        return str(custom)
    builtin = CITY_CODES.get(city)
    return str(builtin) if builtin else None


def _legacy_request(config: dict, keywords: list[str], limit: int | None) -> PlatformCollectionRequest:
    search_config = config.get("search", {}) if isinstance(config.get("search"), dict) else {}
    cities = search_config.get("cities") or config.get("profile", {}).get("target_cities", ["北京"])
    custom_codes = search_config.get("city_codes") if isinstance(search_config.get("city_codes"), dict) else {}
    return PlatformCollectionRequest(
        platform="boss",
        keywords=[str(keyword).strip() for keyword in keywords if str(keyword).strip()],
        cities=[str(city).strip() for city in cities if str(city).strip()],
        city_codes={str(city): str(code) for city, code in custom_codes.items()},
        max_pages=min(int(search_config.get("max_pages", 3) or 3), 10),
        sort=str(search_config.get("sort") or "default"),
        target_count=limit,
    )


def scrape_jobs(
    config: dict,
    keywords: list[str],
    limit: int | None = None,
    *,
    collected_job_ids: list[str] | None = None,
) -> int:
    """Collect BOSS jobs using the new adapter while preserving the old API."""
    db = get_db()
    stop_event = get_stop_event(config)
    request = _legacy_request(config, keywords, limit)
    counts = {"seen": 0, "new": 0, "duplicate": 0, "filtered": 0, "parse_failed": 0, "save_failed": 0}
    progress_callback = config.get("_workbench_collect_progress")
    profile = config.get("profile", {}) if isinstance(config.get("profile"), dict) else {}

    def report() -> None:
        # The old callback shape is intentionally preserved for CLI callers and
        # pre-existing dashboard tests. Structured progress is emitted by the
        # orchestrator path.
        if callable(progress_callback):
            progress_callback({
                "seen": counts["seen"],
                "new": counts["new"],
                "duplicate": counts["duplicate"],
            })

    def inspect(candidate: JobCandidate) -> bool:
        counts["seen"] += 1
        report()
        if job_exists(db, candidate.storage_id):
            counts["duplicate"] += 1
            report()
            return False
        if matching_deal_breaker(candidate.title, profile.get("deal_breakers", [])):
            counts["filtered"] += 1
            return False
        if matching_blocked_company(candidate.company, profile.get("blocked_companies", [])):
            counts["filtered"] += 1
            return False
        return True

    def save(candidate: JobCandidate) -> bool:
        if matching_deal_breaker(candidate.jd, profile.get("jd_deal_breakers", [])):
            counts["filtered"] += 1
            return True
        inserted = insert_job(db, candidate.as_job_record())
        # Legacy test doubles historically returned None; only an explicit
        # False means the database rejected the insert.
        if inserted is False:
            counts["duplicate"] += 1
        else:
            counts["new"] += 1
            if collected_job_ids is not None:
                collected_job_ids.append(candidate.storage_id)
        report()
        return stop_event is None or (not stop_event.is_set() and (limit is None or counts["new"] < limit))

    def parse_failed(_reason: str) -> None:
        counts["parse_failed"] += 1

    hooks = CollectorHooks(
        stop_event=stop_event,
        on_list_candidate=inspect,
        on_candidate=save,
        on_parse_failed=parse_failed,
        on_event=lambda **_values: None,
    )
    collector = BossCollector(
        browser=BossBrowser(
            new_tab=new_tab,
            close_tab=close_tab,
            evaluate=evaluate,
            scroll=scroll,
            wait_for_load=wait_for_load,
        ),
        throttle_factory=PageThrottle,
        sleep=time.sleep,
    )
    try:
        collector.collect(request, hooks)
        report()
        return counts["new"]
    finally:
        db.close()
