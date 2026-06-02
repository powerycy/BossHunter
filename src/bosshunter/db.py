"""Database module - SQLite storage for jobs, history and state tracking."""

import sqlite3
from pathlib import Path
from typing import Any


DB_PATH = Path("./data/bosshunter.db")


def get_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a database connection, creating tables if needed."""
    path = db_path or DB_PATH
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
            score INTEGER DEFAULT 0,
            score_reason TEXT,
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


def job_exists(conn: sqlite3.Connection, job_id: str) -> bool:
    """Check if a job already exists in the database."""
    row = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return row is not None


def insert_job(conn: sqlite3.Connection, job: dict[str, Any]) -> None:
    """Insert a new job record."""
    conn.execute("""
        INSERT OR IGNORE INTO jobs (id, title, company, salary, city, experience, jd,
            hr_name, hr_title, hr_active, company_size, company_industry, url)
        VALUES (:id, :title, :company, :salary, :city, :experience, :jd,
            :hr_name, :hr_title, :hr_active, :company_size, :company_industry, :url)
    """, job)
    conn.commit()


def update_job_score(conn: sqlite3.Connection, job_id: str, score: int, reason: str) -> None:
    """Update job matching score."""
    conn.execute(
        "UPDATE jobs SET score = ?, score_reason = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (score, reason, job_id)
    )
    conn.commit()


def update_job_greeting(conn: sqlite3.Connection, job_id: str, greeting: str) -> None:
    """Update job greeting message."""
    conn.execute(
        "UPDATE jobs SET greeting = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (greeting, job_id)
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
        "SELECT * FROM jobs WHERE status = ? ORDER BY score DESC", (status,)
    ).fetchall()
    return [dict(row) for row in rows]


def get_jobs_pending_confirmation(conn: sqlite3.Connection) -> list[dict]:
    """Get scored jobs that still need manual confirmation."""
    rows = conn.execute("""
        SELECT * FROM jobs
        WHERE status = 'ready'
          AND (greeting IS NULL OR TRIM(greeting) = '')
        ORDER BY score DESC
    """).fetchall()
    return [dict(row) for row in rows]


def get_jobs_ready_to_send(conn: sqlite3.Connection) -> list[dict]:
    """Get jobs that have generated greetings and are ready to send."""
    rows = conn.execute("""
        SELECT * FROM jobs
        WHERE status = 'ready'
          AND greeting IS NOT NULL
          AND TRIM(greeting) != ''
        ORDER BY score DESC
    """).fetchall()
    return [dict(row) for row in rows]


def get_pending_scored_jobs(conn: sqlite3.Connection, threshold: int = 60) -> list[dict]:
    """Get jobs that passed scoring and are pending confirmation."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'scored' AND score >= ? ORDER BY score DESC",
        (threshold,)
    ).fetchall()
    return [dict(row) for row in rows]


def get_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Get job status statistics."""
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
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


def update_job_quick_score(conn: sqlite3.Connection, job_id: str, quick_score: int) -> None:
    """Update job quick (pre-filter) score."""
    conn.execute(
        "UPDATE jobs SET quick_score = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (quick_score, job_id)
    )
    conn.commit()


def add_risk_event(conn: sqlite3.Connection, event_type: str, detail: str = "") -> None:
    """Record a risk/anti-ban event."""
    conn.execute(
        "INSERT INTO risk_events (event_type, detail) VALUES (?, ?)",
        (event_type, detail)
    )
    conn.commit()


def get_funnel_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Get funnel stage counts for dashboard."""
    total = conn.execute("SELECT COUNT(*) as cnt FROM jobs").fetchone()["cnt"]
    prefilter_passed = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE status != 'filtered' OR (status = 'filtered' AND score > 0)").fetchone()["cnt"]
    ai_scored = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE status IN ('scored', 'ready', 'approved', 'rejected', 'sent', 'replied', 'resume_sent', 'needs_resume', 'follow_up_sent')").fetchone()["cnt"]
    approved = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE status IN ('approved', 'sent', 'replied', 'resume_sent', 'needs_resume', 'follow_up_sent')").fetchone()["cnt"]
    sent = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE status IN ('sent', 'replied', 'resume_sent', 'needs_resume', 'follow_up_sent')").fetchone()["cnt"]
    replied = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE status IN ('replied', 'resume_sent', 'needs_resume')").fetchone()["cnt"]
    resume_sent = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE status = 'resume_sent'").fetchone()["cnt"]
    needs_resume = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE status = 'needs_resume'").fetchone()["cnt"]
    follow_up = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE status = 'follow_up_sent'").fetchone()["cnt"]
    rejected = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE status = 'rejected'").fetchone()["cnt"]
    return {"采集总数": total, "初筛通过": prefilter_passed, "AI评分": ai_scored, "人工确认": approved, "发送": sent, "回复": replied, "简历已发": resume_sent, "待手动发简历": needs_resume, "跟进": follow_up, "拒绝": rejected}


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
        FROM jobs WHERE score > 0
        GROUP BY company
        ORDER BY avg_score DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(row) for row in rows]


def get_recent_history(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Get recent history entries with job info."""
    rows = conn.execute("""
        SELECT h.action, h.detail, h.created_at, j.company, j.title
        FROM history h
        JOIN jobs j ON h.job_id = j.id
        ORDER BY h.created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(row) for row in rows]


def get_jobs_needing_resume(conn: sqlite3.Connection) -> list[dict]:
    """Get jobs waiting for manual resume send (tailored PDF generated, not yet sent)."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'needs_resume' ORDER BY updated_at DESC"
    ).fetchall()
    return [dict(row) for row in rows]
