"""Shared page-access budgets and persistent safety locks for platform workflows."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

from bosshunter.db import (
    add_platform_access,
    count_platform_access_today,
    get_active_platform_safety_lock,
    set_platform_safety_lock,
)


class PlatformSafetyStop(RuntimeError):
    """Raised before another platform page is opened when a safety guard stops work."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class PlatformAccessGuard:
    conn: sqlite3.Connection
    config: dict
    stage: str

    def ensure_unlocked(self) -> None:
        lock = get_active_platform_safety_lock(self.conn)
        if lock:
            raise PlatformSafetyStop("persistent_risk_lock")

    def reserve(self, action: str, *, daily_limit: int | None = None) -> None:
        """Reserve one page-open attempt before navigation."""
        self.ensure_unlocked()
        safety_cfg = self.config.get("safety", {})
        global_limit = _positive_int(safety_cfg.get("daily_platform_page_limit", 500), 500)
        if count_platform_access_today(self.conn) >= global_limit:
            raise PlatformSafetyStop("daily_platform_page_limit")
        if daily_limit is not None:
            stage_count = count_platform_access_today(
                self.conn,
                stage=self.stage,
                action=action,
            )
            if stage_count >= max(int(daily_limit), 1):
                raise PlatformSafetyStop(f"daily_{action}_limit")
        add_platform_access(self.conn, self.stage, action)

    def lock(self, reason: str) -> None:
        safety_cfg = self.config.get("safety", {})
        minutes = _positive_int(safety_cfg.get("risk_lock_minutes", 1440), 1440)
        set_platform_safety_lock(self.conn, reason, minutes=minutes)


@dataclass
class TransientPlatformAccessGuard:
    """Open a short-lived DB connection for workflows without a cycle-long connection."""

    config: dict
    stage: str
    db_factory: Callable[[], sqlite3.Connection]

    def ensure_unlocked(self) -> None:
        conn = self.db_factory()
        try:
            PlatformAccessGuard(conn, self.config, self.stage).ensure_unlocked()
        finally:
            conn.close()

    def reserve(self, action: str, *, daily_limit: int | None = None) -> None:
        conn = self.db_factory()
        try:
            PlatformAccessGuard(conn, self.config, self.stage).reserve(
                action,
                daily_limit=daily_limit,
            )
        finally:
            conn.close()


def _positive_int(value: object, default: int) -> int:
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return default
