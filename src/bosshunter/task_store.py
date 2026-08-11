"""Persistence helpers for recoverable workbench tasks.

Only redacted configuration snapshots and structured progress are stored here.
Runtime events, callbacks and credentials remain in memory.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from bosshunter.db import get_db, get_task_row, list_task_rows, upsert_task_row


_SECRET_KEYS = {
    "api_key",
    "auth_token",
    "access_token",
    "refresh_token",
    "cookie",
    "cookies",
    "authorization",
    "login_state",
    "session",
    "session_token",
    "password",
}
_RUNTIME_KEYS = {"_workbench_stop_event", "_workbench_pause_event", "_workbench_log", "_workbench_score_progress", "_workbench_collect_progress"}


def redact_task_snapshot(value: Any, *, _key: str = "") -> Any:
    """Deep-copy a config while dropping secret and runtime values."""
    key = _key.lower()
    if key in _SECRET_KEYS or any(token in key for token in ("api_key", "auth_token", "cookie", "token")):
        return None
    if key in _RUNTIME_KEYS:
        return None
    if isinstance(value, dict):
        result = {}
        for item_key, item_value in value.items():
            normalized = str(item_key).lower()
            if normalized in _RUNTIME_KEYS or normalized in _SECRET_KEYS or any(
                token in normalized for token in ("api_key", "auth_token", "cookie", "token")
            ):
                continue
            result[str(item_key)] = redact_task_snapshot(item_value, _key=str(item_key))
        return result
    if isinstance(value, list):
        return [redact_task_snapshot(item, _key=_key) for item in value]
    if isinstance(value, tuple):
        return [redact_task_snapshot(item, _key=_key) for item in value]
    if callable(value):
        return None
    return deepcopy(value)


def json_text(value: Any, default: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return json.dumps(default, ensure_ascii=False, separators=(",", ":"))


def decode_json(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default
    return parsed


def save_task(task: Any, db_path: Path) -> None:
    """Persist the current safe task projection to the selected SQLite file."""
    snapshot = redact_task_snapshot(task.config_snapshot or {})
    values = {
        "id": task.id,
        "mode": task.mode,
        "label": task.label,
        "status": task.status,
        "stage": task.stage,
        "config_snapshot_json": json_text(snapshot, {}),
        "checkpoint_json": json_text(task.checkpoint, {}),
        "progress_json": json_text(task.progress, {}),
        "logs_json": json_text(task.logs[-100:], []),
        "profile_id": task.profile_id,
        "context_refs_json": json_text(task.context_refs, {}),
        "error": _safe_text(task.error),
        "stop_reason": _safe_text(task.stop_reason),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "finished_at": task.finished_at,
    }
    db = get_db(db_path)
    try:
        upsert_task_row(db, values)
    finally:
        db.close()


def load_tasks(db_path: Path, *, include_terminal: bool = True) -> list[dict[str, Any]]:
    db = get_db(db_path)
    try:
        rows = list_task_rows(db)
    finally:
        db.close()
    if include_terminal:
        return rows
    return [row for row in rows if row.get("status") not in {"completed", "completed_with_errors", "failed", "stopped"}]


def load_task(db_path: Path, task_id: str) -> dict[str, Any] | None:
    db = get_db(db_path)
    try:
        return get_task_row(db, task_id)
    finally:
        db.close()


def mark_orphaned_tasks_paused(db_path: Path) -> list[str]:
    """Convert tasks interrupted by an app restart into recoverable pauses."""
    rows = load_tasks(db_path)
    changed: list[str] = []
    for row in rows:
        if row.get("status") not in {"running", "pausing", "stopping"}:
            continue
        row["status"] = "paused"
        row["stage"] = row.get("stage") or "应用重启后等待恢复"
        row["stop_reason"] = "应用已重启，请手动决定是否恢复"
        row["updated_at"] = _now()
        progress = decode_json(row.get("progress_json"), {})
        checkpoint = decode_json(row.get("checkpoint_json"), {})
        logs = decode_json(row.get("logs_json"), [])
        if isinstance(logs, list):
            logs.append(row["stop_reason"])
        row["progress_json"] = json_text(progress, {})
        row["checkpoint_json"] = json_text(checkpoint, {})
        row["logs_json"] = json_text(logs if isinstance(logs, list) else [], [])
        db = get_db(db_path)
        try:
            db.execute(
                """UPDATE workbench_tasks SET status='paused', stage=?, checkpoint_json=?, progress_json=?,
                   logs_json=?, stop_reason=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (row["stage"], row["checkpoint_json"], row["progress_json"], row["logs_json"], row["stop_reason"], row["id"]),
            )
            db.commit()
        finally:
            db.close()
        changed.append(str(row["id"]))
    return changed


def _safe_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"(?i)(api[_ -]?key|auth[_ -]?token|cookie|authorization)\s*[:=]\s*[^\s,;]+", r"\1=[已隐藏]", str(value))
    return text[:1000]


def _now() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")
