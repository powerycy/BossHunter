"""SQLite persistence for collection run checkpoints."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from bosshunter.db import get_db


def _decode(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _serialize(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def create_collection_run(
    db_path: Path,
    *,
    run_id: str,
    options: dict[str, Any],
    platform_states: dict[str, Any],
    task_id: str = "",
) -> dict[str, Any]:
    conn = get_db(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO collection_runs (
                id, task_id, status, options_json, platform_states_json,
                collected_job_ids_json, current_platform, stop_reason, error, finished_at
            ) VALUES (?, ?, 'pending', ?, ?, '[]', '', '', '', NULL)
            """,
            (run_id, task_id, _serialize(options), _serialize(platform_states)),
        )
        conn.commit()
    finally:
        conn.close()
    return get_collection_run(db_path, run_id) or {}


def update_collection_run(
    db_path: Path,
    run_id: str,
    *,
    status: str | None = None,
    platform_states: dict[str, Any] | None = None,
    collected_job_ids: list[str] | None = None,
    current_platform: str | None = None,
    stop_reason: str | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    assignments: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
    values: list[Any] = []
    if status is not None:
        assignments.append("status = ?")
        values.append(status)
    if platform_states is not None:
        assignments.append("platform_states_json = ?")
        values.append(_serialize(platform_states))
    if collected_job_ids is not None:
        assignments.append("collected_job_ids_json = ?")
        values.append(_serialize(collected_job_ids))
    if current_platform is not None:
        assignments.append("current_platform = ?")
        values.append(current_platform)
    if stop_reason is not None:
        assignments.append("stop_reason = ?")
        values.append(stop_reason)
    if error is not None:
        assignments.append("error = ?")
        values.append(error)
    if status in {"completed", "completed_with_shortage", "completed_with_errors", "stopped", "failed"}:
        assignments.append("finished_at = CURRENT_TIMESTAMP")
    values.append(run_id)
    conn = get_db(db_path)
    try:
        conn.execute(
            f"UPDATE collection_runs SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        conn.commit()
    finally:
        conn.close()
    return get_collection_run(db_path, run_id)


def get_collection_run(db_path: Path, run_id: str) -> dict[str, Any] | None:
    conn = get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM collection_runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    result = dict(row)
    result["options"] = _decode(result.pop("options_json", ""), {})
    result["platform_states"] = _decode(result.pop("platform_states_json", ""), {})
    result["collected_job_ids"] = _decode(result.pop("collected_job_ids_json", ""), [])
    return result


def list_collection_runs(db_path: Path, limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 100))
    conn = get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id FROM collection_runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()
    return [item for row in rows if (item := get_collection_run(db_path, str(row["id"]))) is not None]


def mark_orphaned_collection_runs_stopped(db_path: Path) -> int:
    conn = get_db(db_path)
    try:
        cursor = conn.execute(
            """
            UPDATE collection_runs
            SET status = 'stopped',
                stop_reason = '应用已重启，未自动恢复采集',
                updated_at = CURRENT_TIMESTAMP,
                finished_at = CURRENT_TIMESTAMP
            WHERE status IN ('pending', 'running')
            """
        )
        conn.commit()
        return int(cursor.rowcount)
    finally:
        conn.close()
