"""Database module - SQLite storage for jobs, history and state tracking."""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


DB_PATH = Path("./data/bosshunter.db")
JOB_DELETED_SCOPES = {"active", "only", "all"}
MAX_JOB_IDS = 10000


def get_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a database connection, creating tables if needed."""
    # Tests and local tooling may explicitly point at an isolated database.  The
    # environment escape hatch is intentionally opt-in and never changes the
    # production default.
    if db_path is not None:
        path = db_path
    elif DB_PATH == Path("./data/bosshunter.db") and os.environ.get("BOSSHUNTER_DB_PATH"):
        path = Path(os.environ["BOSSHUNTER_DB_PATH"])
    else:
        path = DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_tables(conn)
    return conn


def _init_tables(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            salary TEXT,
            city TEXT,
            experience TEXT,
            jd TEXT,
            hr_name TEXT,
            hr_title TEXT,
            hr_active TEXT,
            company_size TEXT,
            company_industry TEXT,
            url TEXT,
            city_code TEXT,
            score INTEGER DEFAULT 0,
            score_reason TEXT,
            score_failure_json TEXT DEFAULT NULL,
            greeting TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (job_id) REFERENCES jobs(id)
        );

        CREATE TABLE IF NOT EXISTS risk_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
        CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score);
        CREATE INDEX IF NOT EXISTS idx_history_job_id ON history(job_id);
        CREATE INDEX IF NOT EXISTS idx_risk_events_type ON risk_events(event_type);
    """)
    conn.commit()
    _migrate_v1_1(conn)
    _migrate_v1_2(conn)
    _migrate_v1_3(conn)
    _migrate_v1_4(conn)
    _init_extended_tables(conn)


def job_exists(conn: sqlite3.Connection, job_id: str) -> bool:
    """Check if a job already exists in the database."""
    row = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return row is not None


def job_url_exists(conn: sqlite3.Connection, url: str) -> bool:
    if not str(url or "").strip():
        return False
    row = conn.execute("SELECT 1 FROM jobs WHERE url = ? LIMIT 1", (str(url).strip(),)).fetchone()
    return row is not None


def normalize_job_filters(filters: dict[str, Any] | None = None) -> dict[str, str]:
    """Normalize the small, shared filter contract used by list and export queries."""
    if filters is None:
        filters = {}
    if not isinstance(filters, dict):
        raise ValueError("筛选条件必须是对象")
    unknown = set(filters) - {"q", "status", "city"}
    if unknown:
        raise ValueError("筛选条件包含不支持的字段")

    normalized: dict[str, str] = {}
    for key in ("q", "status", "city"):
        value = filters.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError(f"筛选条件 {key} 必须是字符串")
        normalized[key] = value.strip()
    return normalized


def _normalize_job_ids(job_ids: Any, *, required: bool = False) -> list[str]:
    if job_ids is None:
        values: list[Any] = []
    elif isinstance(job_ids, (str, bytes, dict)):
        raise ValueError("岗位 ID 必须是数组")
    else:
        try:
            values = list(job_ids)
        except TypeError as exc:
            raise ValueError("岗位 ID 必须是数组") from exc

    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("岗位 ID 必须是字符串")
        job_id = value.strip()
        if job_id and job_id not in normalized:
            normalized.append(job_id)
    if len(normalized) > MAX_JOB_IDS:
        raise ValueError(f"一次最多处理 {MAX_JOB_IDS} 个岗位")
    if required and not normalized:
        raise ValueError("岗位 ID 不能为空")
    return normalized


def query_jobs(
    conn: sqlite3.Connection,
    *,
    deleted: str = "active",
    filters: dict[str, Any] | None = None,
    job_ids: Any = None,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Query jobs with one shared deletion and filter semantic for all callers."""
    if not isinstance(deleted, str) or deleted not in JOB_DELETED_SCOPES:
        raise ValueError("deleted 参数无效")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500):
        raise ValueError("limit 参数无效")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset 参数无效")

    normalized_filters = normalize_job_filters(filters)
    normalized_ids = None if job_ids is None else _normalize_job_ids(job_ids, required=True)
    where: list[str] = []
    params: list[Any] = []
    job_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "deleted_at" in job_columns:
        if deleted == "active":
            where.append("deleted_at IS NULL")
        elif deleted == "only":
            where.append("deleted_at IS NOT NULL")
    elif deleted == "only":
        # Old databases are migrated on their next normal open; until then
        # they have no deleted rows and the recycle bin is simply empty.
        return [], 0

    if normalized_filters["status"]:
        where.append("status = ?")
        params.append(normalized_filters["status"])
    if normalized_filters["city"]:
        where.append("city = ?")
        params.append(normalized_filters["city"])
    if normalized_filters["q"]:
        where.append("(company LIKE ? OR title LIKE ? OR jd LIKE ?)")
        like = f"%{normalized_filters['q']}%"
        params.extend([like, like, like])
    if normalized_ids is not None:
        placeholders = ",".join("?" for _ in normalized_ids)
        where.append(f"id IN ({placeholders})")
        params.extend(normalized_ids)

    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    total = int(conn.execute(f"SELECT COUNT(*) AS cnt FROM jobs{where_sql}", params).fetchone()["cnt"])
    query_params = list(params)
    pagination = ""
    if limit is not None:
        pagination = " LIMIT ? OFFSET ?"
        query_params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT * FROM jobs{where_sql} ORDER BY score DESC, created_at DESC{pagination}",
        query_params,
    ).fetchall()
    return [dict(row) for row in rows], total


DELETION_PROTECTED_STATUSES = {
    "sent",
    "replied",
    "resume_sent",
    "needs_resume",
    "follow_up_sent",
}
DELETION_PROTECTED_HISTORY_ACTIONS = {
    "sent",
    "replied",
    "resume_sent",
    "needs_resume",
    "follow_up_sent",
    "reply_pending",
    "reply_dismissed",
    "auto_replied",
}
ACTIVE_TASK_STATUSES = {"running", "pausing", "stopping", "paused"}


class JobDeletionConfirmationError(ValueError):
    code = "confirmation_required"


class JobDeletionConflictError(ValueError):
    code = "deletion_conflict"

    def __init__(self, message: str, *, blocked: list[dict[str, Any]] | None = None, not_found: list[str] | None = None):
        super().__init__(message)
        self.blocked = blocked or []
        self.not_found = not_found or []


def _job_rows_by_ids(conn: sqlite3.Connection, job_ids: list[str]) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in job_ids)
    return [
        dict(row)
        for row in conn.execute(f"SELECT * FROM jobs WHERE id IN ({placeholders})", job_ids).fetchall()
    ]


def _history_protection_reasons(conn: sqlite3.Connection, job_id: str) -> list[str]:
    actions = {
        str(row["action"] or "").strip().lower()
        for row in conn.execute("SELECT action FROM history WHERE job_id = ?", (job_id,)).fetchall()
    }
    reasons: list[str] = []
    if actions & DELETION_PROTECTED_HISTORY_ACTIONS:
        reasons.append("历史中存在发送或回复证据")
    return reasons


def get_job_permanent_delete_reasons(conn: sqlite3.Connection, job_id: str) -> list[str]:
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return ["岗位不存在"]
    reasons: list[str] = []
    if str(row["status"] or "") in DELETION_PROTECTED_STATUSES:
        reasons.append(f"当前状态为 {row['status']}")
    reasons.extend(_history_protection_reasons(conn, job_id))
    return reasons


def _collect_exact_job_ids(payload: Any, known_ids: set[str], found: set[str]) -> None:
    if isinstance(payload, dict):
        for value in payload.values():
            _collect_exact_job_ids(value, known_ids, found)
    elif isinstance(payload, (list, tuple, set)):
        for value in payload:
            _collect_exact_job_ids(value, known_ids, found)
    elif isinstance(payload, str) and payload in known_ids:
        found.add(payload)


def _task_job_conflicts(conn: sqlite3.Connection, job_ids: set[str]) -> list[dict[str, Any]]:
    if not job_ids:
        return []
    rows = conn.execute(
        """SELECT id, status, checkpoint_json, config_snapshot_json, progress_json, context_refs_json
           FROM workbench_tasks
           WHERE status IN ('running', 'pausing', 'stopping', 'paused')
              OR (checkpoint_json IS NOT NULL AND TRIM(checkpoint_json) != '')"""
    ).fetchall()
    conflicts: list[dict[str, Any]] = []
    for row in rows:
        found: set[str] = set()
        for key in ("checkpoint_json", "config_snapshot_json", "progress_json", "context_refs_json"):
            raw = row[key]
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            _collect_exact_job_ids(payload, job_ids, found)
        for job_id in sorted(found):
            conflicts.append({
                "job_id": job_id,
                "reasons": ["运行中、暂停中或可恢复任务引用"],
                "task_id": str(row["id"]),
                "task_status": str(row["status"]),
            })
    return conflicts


def soft_delete_jobs(
    conn: sqlite3.Connection,
    job_ids: Any,
    *,
    confirmed: bool = False,
    reason: str = "用户移入回收站",
) -> dict[str, Any]:
    """Move jobs to the recycle bin while retaining every business field/history."""
    if confirmed is not True:
        raise JobDeletionConfirmationError("移入回收站需要 confirmed=true")
    ids = _normalize_job_ids(job_ids, required=True)
    rows = _job_rows_by_ids(conn, ids)
    found_ids = {str(row["id"]) for row in rows}
    not_found = [job_id for job_id in ids if job_id not in found_ids]
    active_rows = [row for row in rows if row.get("deleted_at") is None]
    warning_ids = [
        str(row["id"])
        for row in rows
        if str(row.get("status") or "") in DELETION_PROTECTED_STATUSES
        or _history_protection_reasons(conn, str(row["id"]))
    ]
    delete_reason = str(reason or "用户移入回收站").strip()[:240]
    with conn:
        for row in active_rows:
            job_id = str(row["id"])
            conn.execute(
                "UPDATE jobs SET deleted_at = CURRENT_TIMESTAMP, deleted_reason = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
                (delete_reason, job_id),
            )
            conn.execute(
                "INSERT INTO history (job_id, action, detail) VALUES (?, 'soft_deleted', ?)",
                (job_id, delete_reason),
            )
    return {
        "requested_count": len(ids),
        "affected_count": len(active_rows),
        "not_found": not_found,
        "warning_ids": warning_ids,
    }


def restore_jobs(conn: sqlite3.Connection, job_ids: Any, *, confirmed: bool = False) -> dict[str, Any]:
    """Restore deleted jobs without touching status, score, greeting, or history."""
    if confirmed is not True:
        raise JobDeletionConfirmationError("恢复岗位需要 confirmed=true")
    ids = _normalize_job_ids(job_ids, required=True)
    rows = _job_rows_by_ids(conn, ids)
    found_ids = {str(row["id"]) for row in rows}
    not_found = [job_id for job_id in ids if job_id not in found_ids]
    deleted_rows = [row for row in rows if row.get("deleted_at") is not None]
    with conn:
        for row in deleted_rows:
            job_id = str(row["id"])
            conn.execute(
                "UPDATE jobs SET deleted_at = NULL, deleted_reason = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NOT NULL",
                (job_id,),
            )
            conn.execute(
                "INSERT INTO history (job_id, action, detail) VALUES (?, 'restored', ?)",
                (job_id, "用户从回收站恢复岗位"),
            )
    return {
        "requested_count": len(ids),
        "affected_count": len(deleted_rows),
        "not_found": not_found,
        "already_active": [str(row["id"]) for row in rows if row.get("deleted_at") is None],
    }


def permanent_delete_jobs(
    conn: sqlite3.Connection,
    job_ids: Any,
    *,
    confirmed: bool = False,
    confirmation: str = "",
) -> dict[str, Any]:
    """Permanently delete only safe recycle-bin jobs in one all-or-nothing transaction."""
    if confirmed is not True or confirmation != "PERMANENT_DELETE":
        raise JobDeletionConfirmationError("永久删除需要 confirmed=true 和 confirmation=PERMANENT_DELETE")
    ids = _normalize_job_ids(job_ids, required=True)
    with conn:
        rows = _job_rows_by_ids(conn, ids)
        found_ids = {str(row["id"]) for row in rows}
        not_found = [job_id for job_id in ids if job_id not in found_ids]
        if not_found:
            raise JobDeletionConflictError("存在不存在的岗位，未执行永久删除", not_found=not_found)
        not_deleted = [str(row["id"]) for row in rows if row.get("deleted_at") is None]
        if not_deleted:
            raise JobDeletionConflictError(
                "只有回收站岗位允许永久删除",
                blocked=[{"job_id": job_id, "reasons": ["岗位尚未移入回收站"]} for job_id in not_deleted],
            )

        blocked: list[dict[str, Any]] = []
        for row in rows:
            job_id = str(row["id"])
            reasons = get_job_permanent_delete_reasons(conn, job_id)
            if reasons:
                blocked.append({"job_id": job_id, "reasons": reasons})
        blocked.extend(_task_job_conflicts(conn, set(ids)))
        if blocked:
            raise JobDeletionConflictError("存在受保护岗位，批量永久删除已整体拒绝", blocked=blocked)

        # history is the only direct FK dependency today; delete it first so
        # this remains safe even when foreign_keys enforcement is enabled.
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM history WHERE job_id IN ({placeholders})", ids)
        cursor = conn.execute(f"DELETE FROM jobs WHERE id IN ({placeholders})", ids)
        if cursor.rowcount != len(ids):
            raise JobDeletionConflictError("永久删除数量校验失败，事务已回滚")
    return {"requested_count": len(ids), "affected_count": len(ids), "blocked": [], "not_found": []}


def mark_job_filtered(conn: sqlite3.Connection, job_id: str, source: str, reason: str) -> None:
    conn.execute(
        """UPDATE jobs SET status='filtered', filter_source=?, filter_reason=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
        (str(source)[:64], str(reason)[:240], job_id),
    )
    conn.commit()


def clear_job_filter(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute(
        "UPDATE jobs SET filter_source=NULL, filter_reason=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (job_id,),
    )
    conn.commit()


def insert_job(conn: sqlite3.Connection, job: dict[str, Any]) -> bool:
    """Insert a new job record and report whether it was actually inserted."""
    values = dict(job)
    values.setdefault("city_code", None)
    cursor = conn.execute("""
        INSERT OR IGNORE INTO jobs (id, title, company, salary, city, city_code, experience, jd,
            hr_name, hr_title, hr_active, company_size, company_industry, url)
        VALUES (:id, :title, :company, :salary, :city, :city_code, :experience, :jd,
            :hr_name, :hr_title, :hr_active, :company_size, :company_industry, :url)
    """, values)
    conn.commit()
    return cursor.rowcount > 0


def update_job_score(conn: sqlite3.Connection, job_id: str, score: int, reason: str) -> None:
    """Update job matching score."""
    conn.execute(
        "UPDATE jobs SET score = ?, score_reason = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (score, reason, job_id)
    )
    conn.commit()


def update_job_score_failure(conn: sqlite3.Connection, job_id: str, failure_json: str) -> None:
    """Store only a structured, credential-safe latest score failure."""
    conn.execute(
        "UPDATE jobs SET score_failure_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (failure_json, job_id),
    )
    conn.commit()


def clear_job_score_failure(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute(
        "UPDATE jobs SET score_failure_json = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (job_id,),
    )
    conn.commit()


def update_job_greeting(conn: sqlite3.Connection, job_id: str, greeting: str) -> None:
    """Update job greeting message."""
    conn.execute(
        """UPDATE jobs
           SET greeting = ?, greeting_status = 'generated', greeting_failure_json = NULL,
               greeting_updated_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (greeting, job_id),
    )
    conn.commit()


def update_job_greeting_failure(
    conn: sqlite3.Connection,
    job_id: str,
    failure_json: str,
    *,
    attempts: int | None = None,
) -> None:
    """Persist a redacted, job-level greeting failure without hiding the job."""
    if attempts is None:
        conn.execute(
            """UPDATE jobs
               SET greeting_status = 'failed', greeting_failure_json = ?,
                   greeting_attempts = COALESCE(greeting_attempts, 0) + 1,
                   greeting_updated_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (failure_json, job_id),
        )
    else:
        conn.execute(
            """UPDATE jobs
               SET greeting_status = 'failed', greeting_failure_json = ?, greeting_attempts = ?,
                   greeting_updated_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (failure_json, max(int(attempts), 0), job_id),
        )
    conn.commit()


def mark_job_greeting_generating(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute(
        """UPDATE jobs
           SET greeting_status = 'generating', greeting_attempts = COALESCE(greeting_attempts, 0) + 1,
               greeting_updated_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (job_id,),
    )
    conn.commit()


def clear_job_greeting_failure(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute(
        """UPDATE jobs SET greeting_failure_json = NULL,
           greeting_status = CASE WHEN greeting IS NOT NULL AND TRIM(greeting) != '' THEN 'generated' ELSE 'not_started' END,
           updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
        (job_id,),
    )
    conn.commit()


def update_job_status(conn: sqlite3.Connection, job_id: str, status: str) -> None:
    """Update job status."""
    conn.execute(
        "UPDATE jobs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, job_id)
    )
    conn.commit()


def add_history(conn: sqlite3.Connection, job_id: str, action: str, detail: str = "") -> None:
    """Add a history record."""
    conn.execute(
        "INSERT INTO history (job_id, action, detail) VALUES (?, ?, ?)",
        (job_id, action, detail)
    )
    conn.commit()


def get_jobs_by_status(conn: sqlite3.Connection, status: str) -> list[dict]:
    """Get all jobs with a given status."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = ? AND deleted_at IS NULL ORDER BY score DESC", (status,)
    ).fetchall()
    return [dict(row) for row in rows]


def get_jobs_pending_confirmation(conn: sqlite3.Connection) -> list[dict]:
    """Get scored jobs that still need manual confirmation."""
    rows = conn.execute("""
        SELECT * FROM jobs
        WHERE status = 'ready'
          AND deleted_at IS NULL
          AND (greeting IS NULL OR TRIM(greeting) = '')
        ORDER BY score DESC
    """).fetchall()
    return [dict(row) for row in rows]


def get_jobs_ready_to_send(conn: sqlite3.Connection) -> list[dict]:
    """Get jobs that have generated greetings and are ready to send."""
    rows = conn.execute("""
        SELECT * FROM jobs
        WHERE status IN ('ready', 'approved')
          AND deleted_at IS NULL
          AND greeting IS NOT NULL
          AND TRIM(greeting) != ''
        ORDER BY score DESC
    """).fetchall()
    return [dict(row) for row in rows]


def get_jobs_greeting_generation_items(conn: sqlite3.Connection) -> list[dict]:
    """Return every approved job that still needs a greeting or previously failed."""
    rows = conn.execute(
        """SELECT * FROM jobs
           WHERE status = 'approved'
             AND deleted_at IS NULL
             AND (greeting IS NULL OR TRIM(greeting) = '' OR
                  COALESCE(greeting_status, 'not_started') IN ('not_started', 'pending', 'generating', 'failed'))
           ORDER BY score DESC, updated_at DESC"""
    ).fetchall()
    return [dict(row) for row in rows]


def get_jobs_greeting_status(conn: sqlite3.Connection, statuses: tuple[str, ...] = ('pending', 'failed')) -> list[dict]:
    """Return approved jobs by greeting sub-state, including legacy rows."""
    placeholders = ",".join("?" for _ in statuses)
    rows = conn.execute(
        f"""SELECT * FROM jobs
            WHERE status = 'approved' AND deleted_at IS NULL AND (
              (COALESCE(greeting_status, 'not_started') IN ({placeholders})) OR
              (greeting_status IS NULL AND (greeting IS NULL OR TRIM(greeting) = ''))
            )
            ORDER BY score DESC, updated_at DESC""",
        statuses,
    ).fetchall()
    return [dict(row) for row in rows]


def get_jobs_with_send_errors(conn: sqlite3.Connection) -> list[dict]:
    """Get jobs where greeting sending failed and can be retried."""
    rows = conn.execute("""
        SELECT * FROM jobs
        WHERE status = 'error'
          AND deleted_at IS NULL
          AND greeting IS NOT NULL
          AND TRIM(greeting) != ''
        ORDER BY updated_at DESC, score DESC
    """).fetchall()
    return [dict(row) for row in rows]


def get_pending_scored_jobs(conn: sqlite3.Connection, threshold: int = 60) -> list[dict]:
    """Get jobs that passed scoring and are pending confirmation."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'scored' AND deleted_at IS NULL AND score >= ? ORDER BY score DESC",
        (threshold,)
    ).fetchall()
    return [dict(row) for row in rows]


def get_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Get job status statistics."""
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM jobs WHERE deleted_at IS NULL GROUP BY status"
    ).fetchall()
    return {row["status"]: row["cnt"] for row in rows}


def _migrate_v1_1(conn: sqlite3.Connection) -> None:
    """Add v1.1 columns if they don't exist."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "quick_score" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN quick_score INTEGER DEFAULT 0")
    if "resume_path" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN resume_path TEXT DEFAULT NULL")
    conn.commit()


def _migrate_v1_2(conn: sqlite3.Connection) -> None:
    """Add city codes and structured score failures without rebuilding data."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "city_code" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN city_code TEXT DEFAULT NULL")
    if "score_failure_json" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN score_failure_json TEXT DEFAULT NULL")
    conn.commit()


def _migrate_v1_3(conn: sqlite3.Connection) -> None:
    """Add greeting/filter columns idempotently without rebuilding jobs."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    additions = {
        "greeting_status": "TEXT DEFAULT 'not_started'",
        "greeting_failure_json": "TEXT DEFAULT NULL",
        "greeting_attempts": "INTEGER DEFAULT 0",
        "greeting_updated_at": "TIMESTAMP DEFAULT NULL",
        "filter_source": "TEXT DEFAULT NULL",
        "filter_reason": "TEXT DEFAULT NULL",
        "manual_override": "INTEGER DEFAULT 0",
        "manual_override_at": "TIMESTAMP DEFAULT NULL",
    }
    for name, definition in additions.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
    # Old non-empty greetings are immediately compatible with the new view.
    conn.execute(
        """UPDATE jobs SET greeting_status = 'generated'
           WHERE (greeting_status IS NULL OR greeting_status = 'not_started')
             AND greeting IS NOT NULL AND TRIM(greeting) != ''"""
    )
    conn.commit()


def _migrate_v1_4(conn: sqlite3.Connection) -> None:
    """Add soft-delete metadata without rebuilding or rewriting job rows."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "deleted_at" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN deleted_at TIMESTAMP NULL")
    if "deleted_reason" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN deleted_reason TEXT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_deleted_at ON jobs(deleted_at)")
    conn.commit()


def _init_extended_tables(conn: sqlite3.Connection) -> None:
    """Create the lightweight profile/fact/task tables used by new workflows."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS candidate_profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            is_default INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS candidate_documents (
            id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            document_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            text_content TEXT,
            is_primary INTEGER DEFAULT 0,
            parse_status TEXT DEFAULT 'ready',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id)
        );
        CREATE TABLE IF NOT EXISTS candidate_facts (
            id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            source_document_id TEXT NULL,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            normalized_hash TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            conflict_group TEXT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_fact_hash
            ON candidate_facts(profile_id, normalized_hash);
        CREATE INDEX IF NOT EXISTS idx_candidate_documents_profile
            ON candidate_documents(profile_id, is_primary);
        CREATE TABLE IF NOT EXISTS workbench_tasks (
            id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            label TEXT NOT NULL,
            status TEXT NOT NULL,
            stage TEXT,
            config_snapshot_json TEXT NOT NULL,
            checkpoint_json TEXT,
            progress_json TEXT,
            logs_json TEXT,
            profile_id TEXT NULL,
            context_refs_json TEXT NULL,
            error TEXT NULL,
            stop_reason TEXT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP NULL
        );
        CREATE INDEX IF NOT EXISTS idx_workbench_tasks_status ON workbench_tasks(status);
        """
    )
    conn.commit()


def ensure_default_profile(conn: sqlite3.Connection, name: str = "默认求职资料") -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM candidate_profiles WHERE is_default = 1 ORDER BY created_at LIMIT 1"
    ).fetchone()
    if row:
        return dict(row)
    profile_id = "default"
    conn.execute(
        "INSERT OR IGNORE INTO candidate_profiles (id, name, is_default) VALUES (?, ?, 1)",
        (profile_id, name),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM candidate_profiles WHERE id = ?", (profile_id,)).fetchone())


def upsert_candidate_document(
    conn: sqlite3.Connection,
    *,
    profile_id: str,
    document_id: str,
    document_type: str,
    filename: str,
    storage_path: str,
    text_content: str = "",
    is_primary: bool = False,
) -> dict[str, Any]:
    if is_primary:
        conn.execute("UPDATE candidate_documents SET is_primary = 0 WHERE profile_id = ?", (profile_id,))
    conn.execute(
        """INSERT INTO candidate_documents
           (id, profile_id, document_type, filename, storage_path, text_content, is_primary, parse_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'ready')
           ON CONFLICT(id) DO UPDATE SET filename=excluded.filename, storage_path=excluded.storage_path,
             text_content=excluded.text_content, document_type=excluded.document_type,
             is_primary=excluded.is_primary, parse_status='ready', updated_at=CURRENT_TIMESTAMP""",
        (document_id, profile_id, document_type, filename, storage_path, text_content, int(is_primary)),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM candidate_documents WHERE id = ?", (document_id,)).fetchone())


def list_candidate_profiles(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("SELECT * FROM candidate_profiles ORDER BY is_default DESC, updated_at DESC").fetchall()]


def get_candidate_profile(conn: sqlite3.Connection, profile_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM candidate_profiles WHERE id = ?", (profile_id,)).fetchone()
    return dict(row) if row else None


def update_candidate_profile(conn: sqlite3.Connection, profile_id: str, *, name: str | None = None, is_default: bool | None = None) -> dict[str, Any] | None:
    sets: list[str] = []
    values: list[Any] = []
    if name is not None:
        sets.append("name = ?")
        values.append(str(name)[:100])
    if is_default is True:
        conn.execute("UPDATE candidate_profiles SET is_default = 0")
        sets.append("is_default = 1")
    elif is_default is False:
        sets.append("is_default = 0")
    if sets:
        sets.append("updated_at = CURRENT_TIMESTAMP")
        values.append(profile_id)
        conn.execute(f"UPDATE candidate_profiles SET {', '.join(sets)} WHERE id = ?", values)
        conn.commit()
    return get_candidate_profile(conn, profile_id)


def list_candidate_documents(conn: sqlite3.Connection, profile_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("SELECT * FROM candidate_documents WHERE profile_id = ? ORDER BY is_primary DESC, created_at DESC", (profile_id,)).fetchall()]


def get_primary_candidate_document(conn: sqlite3.Connection, profile_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM candidate_documents WHERE profile_id = ? AND is_primary = 1 ORDER BY updated_at DESC LIMIT 1", (profile_id,)).fetchone()
    return dict(row) if row else None


def set_candidate_document_primary(conn: sqlite3.Connection, profile_id: str, document_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM candidate_documents WHERE id = ? AND profile_id = ?", (document_id, profile_id)).fetchone()
    if not row:
        return None
    conn.execute("UPDATE candidate_documents SET is_primary = 0 WHERE profile_id = ?", (profile_id,))
    conn.execute("UPDATE candidate_documents SET is_primary = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (document_id,))
    conn.commit()
    return dict(conn.execute("SELECT * FROM candidate_documents WHERE id = ?", (document_id,)).fetchone())


def add_candidate_fact(conn: sqlite3.Connection, fact: dict[str, Any]) -> dict[str, Any]:
    conn.execute(
        """INSERT OR IGNORE INTO candidate_facts
           (id, profile_id, source_document_id, category, content, normalized_hash, status, conflict_group)
           VALUES (:id, :profile_id, :source_document_id, :category, :content, :normalized_hash, :status, :conflict_group)""",
        {**fact, "status": fact.get("status", "pending"), "conflict_group": fact.get("conflict_group")},
    )
    conn.commit()
    row = conn.execute("SELECT * FROM candidate_facts WHERE id = ?", (fact["id"],)).fetchone()
    return dict(row)


def list_candidate_facts(conn: sqlite3.Connection, profile_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("SELECT * FROM candidate_facts WHERE profile_id = ? ORDER BY created_at DESC", (profile_id,)).fetchall()]


def update_candidate_fact(conn: sqlite3.Connection, fact_id: str, *, status: str | None = None, content: str | None = None) -> dict[str, Any] | None:
    values = []
    sets = []
    if status is not None:
        sets.append("status = ?")
        values.append(status)
    if content is not None:
        sets.append("content = ?")
        values.append(content)
    if not sets:
        row = conn.execute("SELECT * FROM candidate_facts WHERE id = ?", (fact_id,)).fetchone()
        return dict(row) if row else None
    sets.append("updated_at = CURRENT_TIMESTAMP")
    values.append(fact_id)
    conn.execute(f"UPDATE candidate_facts SET {', '.join(sets)} WHERE id = ?", values)
    conn.commit()
    row = conn.execute("SELECT * FROM candidate_facts WHERE id = ?", (fact_id,)).fetchone()
    return dict(row) if row else None


def override_filtered_job(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    if row["filter_source"] not in {"deal_breaker", "prefilter", "ai_score", "anonymous_company"}:
        raise ValueError("该岗位不是可人工覆盖的偏好过滤")
    conn.execute(
        """UPDATE jobs SET manual_override = 1, manual_override_at = CURRENT_TIMESTAMP,
           status = 'ready', updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
        (job_id,),
    )
    add_history(conn, job_id, "filter_overridden", json.dumps({"schema": "bosshunter.filter_override.v1", "reason": row["filter_reason"] or "偏好过滤", "manual": True}, ensure_ascii=False))
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row)


def get_task_row(conn: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM workbench_tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def list_task_rows(conn: sqlite3.Connection, *, statuses: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    if not statuses:
        rows = conn.execute("SELECT * FROM workbench_tasks ORDER BY created_at ASC").fetchall()
    else:
        placeholders = ",".join("?" for _ in statuses)
        rows = conn.execute(f"SELECT * FROM workbench_tasks WHERE status IN ({placeholders}) ORDER BY created_at ASC", statuses).fetchall()
    return [dict(row) for row in rows]


def upsert_task_row(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO workbench_tasks
           (id, mode, label, status, stage, config_snapshot_json, checkpoint_json, progress_json,
            logs_json, profile_id, context_refs_json, error, stop_reason, created_at, updated_at, finished_at)
           VALUES (:id, :mode, :label, :status, :stage, :config_snapshot_json, :checkpoint_json, :progress_json,
            :logs_json, :profile_id, :context_refs_json, :error, :stop_reason, :created_at, :updated_at, :finished_at)
           ON CONFLICT(id) DO UPDATE SET status=excluded.status, stage=excluded.stage,
            checkpoint_json=excluded.checkpoint_json, progress_json=excluded.progress_json, logs_json=excluded.logs_json,
            error=excluded.error, stop_reason=excluded.stop_reason, updated_at=excluded.updated_at,
            finished_at=excluded.finished_at, profile_id=excluded.profile_id, context_refs_json=excluded.context_refs_json""",
        values,
    )
    conn.commit()


def update_job_quick_score(conn: sqlite3.Connection, job_id: str, quick_score: int) -> None:
    """Update job quick (pre-filter) score."""
    conn.execute(
        "UPDATE jobs SET quick_score = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (quick_score, job_id)
    )
    conn.commit()


def reset_ai_filtered_jobs(conn: sqlite3.Connection) -> int:
    """Move only AI-scored low-match jobs back to the pending queue."""
    cursor = conn.execute("""
        UPDATE jobs
        SET status = 'pending', score = 0, score_reason = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE status = 'filtered'
          AND deleted_at IS NULL
          AND COALESCE(score_reason, '') != ''
          AND score_reason NOT LIKE '预筛不通过:%'
          AND score_reason NOT LIKE 'AI评分失败:%'
          AND score_reason NOT LIKE 'AI 评分失败:%'
          AND score_reason NOT LIKE '评分失败:%'
    """)
    conn.commit()
    return cursor.rowcount


def add_risk_event(conn: sqlite3.Connection, event_type: str, detail: str = "") -> None:
    """Record a risk/anti-ban event."""
    conn.execute(
        "INSERT INTO risk_events (event_type, detail) VALUES (?, ?)",
        (event_type, detail)
    )
    conn.commit()


def get_funnel_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Get funnel stage counts for dashboard."""
    total = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE deleted_at IS NULL").fetchone()["cnt"]
    prefilter_passed = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE deleted_at IS NULL AND (status != 'filtered' OR (status = 'filtered' AND score > 0))").fetchone()["cnt"]
    ai_scored = conn.execute("""
        SELECT COUNT(*) as cnt FROM jobs
        WHERE deleted_at IS NULL AND status IN ('scored', 'ready', 'approved', 'rejected', 'sent', 'replied', 'resume_sent', 'needs_resume', 'follow_up_sent')
           OR (
                deleted_at IS NULL AND
                status = 'filtered'
                AND COALESCE(score_reason, '') != ''
                AND score_reason NOT LIKE '预筛不通过:%'
                AND score_reason NOT LIKE 'AI评分失败:%'
                AND score_reason NOT LIKE 'AI 评分失败:%'
                AND score_reason NOT LIKE '评分失败:%'
           )
    """).fetchone()["cnt"]
    approved = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE deleted_at IS NULL AND status IN ('approved', 'sent', 'replied', 'resume_sent', 'needs_resume', 'follow_up_sent')").fetchone()["cnt"]
    sent = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE deleted_at IS NULL AND status IN ('sent', 'replied', 'resume_sent', 'needs_resume', 'follow_up_sent')").fetchone()["cnt"]
    replied = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE deleted_at IS NULL AND status IN ('replied', 'resume_sent', 'needs_resume')").fetchone()["cnt"]
    resume_sent = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE deleted_at IS NULL AND status = 'resume_sent'").fetchone()["cnt"]
    needs_resume = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE deleted_at IS NULL AND status = 'needs_resume'").fetchone()["cnt"]
    resume_generated = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE deleted_at IS NULL AND resume_path IS NOT NULL AND TRIM(resume_path) != ''").fetchone()["cnt"]
    follow_up = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE deleted_at IS NULL AND status = 'follow_up_sent'").fetchone()["cnt"]
    rejected = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE deleted_at IS NULL AND status = 'rejected'").fetchone()["cnt"]
    return {"采集总数": total, "初筛通过": prefilter_passed, "AI评分": ai_scored, "人工确认": approved, "发送": sent, "回复": replied, "简历已发": resume_sent, "待手动发简历": needs_resume, "简历生成": resume_generated, "跟进": follow_up, "拒绝": rejected}


def get_daily_activity(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    """Get daily activity for last N days."""
    rows = conn.execute("""
        SELECT date(created_at) as day, action, COUNT(*) as cnt
        FROM history
        WHERE created_at >= date('now', ?)
        GROUP BY date(created_at), action
        ORDER BY day DESC
    """, (f"-{days} days",)).fetchall()
    return [dict(row) for row in rows]


def get_top_companies(conn: sqlite3.Connection, limit: int = 5) -> list[dict]:
    """Get top companies by average score."""
    rows = conn.execute("""
        SELECT company, ROUND(AVG(score), 0) as avg_score, COUNT(*) as job_count
        FROM jobs WHERE deleted_at IS NULL AND score > 0
        GROUP BY company
        ORDER BY avg_score DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(row) for row in rows]


def get_recent_history(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Get recent history entries with job info."""
    rows = conn.execute("""
        SELECT h.id, h.job_id, h.action, h.detail, h.created_at, j.company, j.title,
               j.resume_path,
               CASE
                 WHEN h.action = 'resume_failed'
                  AND (
                    (j.resume_path IS NOT NULL AND TRIM(j.resume_path) != '')
                    OR EXISTS (
                      SELECT 1
                      FROM history r
                      WHERE r.job_id = h.job_id
                        AND r.action IN ('needs_resume', 'resume_sent')
                        AND r.id > h.id
                    )
                  )
                 THEN 1
                 ELSE 0
               END AS resolved
        FROM history h
        JOIN jobs j ON h.job_id = j.id
        ORDER BY h.created_at DESC, h.id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(row) for row in rows]


def get_unresolved_resume_failures(conn: sqlite3.Connection) -> list[dict]:
    """Get the latest resume generation failure for jobs not resolved by a later success."""
    rows = conn.execute("""
        SELECT h.id, h.job_id, h.action, h.detail, h.created_at, j.company, j.title,
               j.resume_path, 0 AS resolved
        FROM history h
        JOIN jobs j ON h.job_id = j.id
        WHERE h.action = 'resume_failed'
          AND h.id = (
            SELECT MAX(f.id)
            FROM history f
            WHERE f.job_id = h.job_id
              AND f.action = 'resume_failed'
          )
          AND (j.resume_path IS NULL OR TRIM(j.resume_path) = '')
          AND NOT EXISTS (
            SELECT 1
            FROM history r
            WHERE r.job_id = h.job_id
              AND r.action IN ('needs_resume', 'resume_sent')
              AND r.id > h.id
          )
        ORDER BY h.created_at DESC, h.id DESC
    """).fetchall()
    return [dict(row) for row in rows]


def count_unresolved_reply_pending(conn: sqlite3.Connection) -> int:
    """Count latest reply_pending rows that have not been resolved for each job."""
    row = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM history h
        WHERE h.action = 'reply_pending'
          AND h.id = (
            SELECT MAX(p.id)
            FROM history p
            WHERE p.job_id = h.job_id
              AND p.action = 'reply_pending'
          )
          AND NOT EXISTS (
            SELECT 1
            FROM history r
            WHERE r.job_id = h.job_id
              AND r.action IN ('reply_dismissed', 'replied', 'auto_replied')
              AND r.id > h.id
          )
    """).fetchone()
    return int(row["cnt"] or 0)


def count_unresolved_monitor_items(conn: sqlite3.Connection) -> int:
    """Count unresolved reply suggestions and resume generation failures."""
    return count_unresolved_reply_pending(conn) + len(get_unresolved_resume_failures(conn))


def get_jobs_needing_resume(conn: sqlite3.Connection) -> list[dict]:
    """Get jobs waiting for manual resume send (tailored PDF generated, not yet sent)."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'needs_resume' AND deleted_at IS NULL ORDER BY updated_at DESC"
    ).fetchall()
    return [dict(row) for row in rows]
