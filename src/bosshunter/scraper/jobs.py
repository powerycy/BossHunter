"""Backward-compatible, BOSS-only collection facade.

The Web multi-platform queue uses ``CollectionOrchestrator``. This module keeps
the historical ``scrape_jobs`` API while applying PR #66's account guardrails
only to BOSS 直聘.
"""

from __future__ import annotations

import time

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from bosshunter.browser import close_tab, evaluate, navigate, new_tab, scroll, wait_for_load
from bosshunter.cancellation import get_stop_event
from bosshunter.collection.base import CollectorHooks
from bosshunter.collection.models import JobCandidate, PlatformCollectionRequest
from bosshunter.collection.platforms.boss import BossBrowser, BossCollector, generate_boss_job_id
from bosshunter.config import CITY_CODES
from bosshunter.db import get_db, insert_job, job_exists
from bosshunter.job_filters import matching_blocked_company, matching_deal_breaker
from bosshunter.outsourcing import load_rules
from bosshunter.platform_safety import PlatformSafetyStop
from bosshunter.throttle import PageThrottle

console = Console()


def _generate_job_id(url: str) -> str:
    return generate_boss_job_id(url)


def _resolve_city_code(city: str, config: dict) -> str | None:
    search_config = config.get("search", {}) if isinstance(config.get("search"), dict) else {}
    custom_codes = search_config.get("city_codes") if isinstance(search_config.get("city_codes"), dict) else {}
    custom = custom_codes.get(city)
    if custom not in (None, ""):
        return str(custom)
    builtin = CITY_CODES.get(city)
    return str(builtin) if builtin else None


def _positive_int(value: object, default: int) -> int:
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return default


def _legacy_request(config: dict, keywords: list[str]) -> PlatformCollectionRequest:
    search_config = config.get("search", {}) if isinstance(config.get("search"), dict) else {}
    cities = search_config.get("cities") or config.get("profile", {}).get("target_cities", ["北京"])
    custom_codes = search_config.get("city_codes") if isinstance(search_config.get("city_codes"), dict) else {}
    return PlatformCollectionRequest(
        platform="boss",
        keywords=[str(keyword).strip() for keyword in keywords if str(keyword).strip()],
        cities=[str(city).strip() for city in cities if str(city).strip()],
        city_codes={str(city): str(code) for city, code in custom_codes.items()},
        max_pages=min(_positive_int(search_config.get("max_pages", 3), 3), 10),
        sort=str(search_config.get("sort") or "default"),
        filters=search_config.get("filters") if isinstance(search_config.get("filters"), dict) else {},
    )


def _scrape_jobs_impl(
    config: dict,
    keywords: list[str],
    limit: int | None = None,
    *,
    collected_job_ids: list[str] | None = None,
) -> int:
    """Collect BOSS jobs; Zhilian/51job never enter this facade or its quotas."""
    db = get_db()
    stop_event = get_stop_event(config)
    report_state = {"stop_reason": None, "new_count": 0}
    config["_workbench_collect_report"] = report_state
    if stop_event is not None and stop_event.is_set():
        db.close()
        return 0

    effective_target = None if limit is None else max(int(limit), 0)
    if effective_target == 0:
        db.close()
        return 0

    request = _legacy_request(config, keywords)
    counts = {
        "seen": 0, "new": 0, "duplicate": 0, "filtered": 0,
        "parse_failed": 0, "save_failed": 0, "search_pages": 0,
    }
    progress_callback = config.get("_workbench_collect_progress")
    profile = config.get("profile", {}) if isinstance(config.get("profile"), dict) else {}
    outsourcing_rules = load_rules(config)

    def emit() -> None:
        if callable(progress_callback):
            progress_callback(dict(counts))

    def inspect(candidate: JobCandidate) -> bool:
        counts["seen"] += 1
        if job_exists(db, candidate.storage_id):
            counts["duplicate"] += 1
            emit()
            return False
        if matching_deal_breaker(candidate.title, profile.get("deal_breakers", [])):
            counts["filtered"] += 1
            emit()
            return False
        if matching_blocked_company(candidate.company, profile.get("blocked_companies", [])):
            counts["filtered"] += 1
            emit()
            return False
        emit()
        return True

    def save(candidate: JobCandidate) -> bool:
        if matching_deal_breaker(candidate.jd, profile.get("jd_deal_breakers", [])):
            counts["filtered"] += 1
            emit()
            return True
        try:
            inserted = insert_job(db, candidate.as_job_record(), rules=outsourcing_rules)
        except Exception:
            counts["save_failed"] += 1
            emit()
            return True
        if inserted is False:
            counts["duplicate"] += 1
        else:
            counts["new"] += 1
            report_state["new_count"] = counts["new"]
            if collected_job_ids is not None:
                collected_job_ids.append(candidate.storage_id)
        emit()
        return bool(
            (stop_event is None or not stop_event.is_set())
            and (effective_target is None or counts["new"] < effective_target)
        )

    def parse_failed(_reason: str) -> None:
        counts["parse_failed"] += 1
        emit()

    def event(**values) -> None:
        if values.get("phase") == "loading_list":
            counts["search_pages"] += 1
        if values.get("message") == "BOSS 列表预筛不通过":
            counts["filtered"] += 1
        emit()

    collector = BossCollector(
        browser=BossBrowser(
            new_tab=new_tab, close_tab=close_tab, evaluate=evaluate,
            navigate=navigate, scroll=scroll, wait_for_load=wait_for_load,
        ),
        throttle_factory=PageThrottle,
        sleep=time.sleep,
        config=config,
        safety_conn=db,
    )
    try:
        result = collector.collect(
            request,
            CollectorHooks(
                stop_event=stop_event,
                on_list_candidate=inspect,
                on_candidate=save,
                on_parse_failed=parse_failed,
                on_event=event,
            ),
        )
        if result.reason_code:
            report_state["stop_reason"] = result.reason_code
        report_state.update({f"{key}_count": value for key, value in counts.items()})
        emit()
        return counts["new"]
    finally:
        db.close()


def scrape_jobs(
    config: dict,
    keywords: list[str],
    limit: int | None = None,
    *,
    collected_job_ids: list[str] | None = None,
) -> int:
    try:
        return _scrape_jobs_impl(config, keywords, limit, collected_job_ids=collected_job_ids)
    except PlatformSafetyStop as exc:
        report = config.setdefault("_workbench_collect_report", {})
        report["stop_reason"] = exc.reason
        console.print(f"[yellow]BOSS 采集已安全停止：{exc.reason}[/yellow]")
        return int(report.get("new_count") or 0)
