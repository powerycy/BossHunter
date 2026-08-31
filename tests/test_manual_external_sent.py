import tempfile
from pathlib import Path

import pytest

from bosshunter.db import (
    JobDeletionConfirmationError,
    JobManualSentConflictError,
    get_db,
    insert_job,
    mark_external_jobs_sent,
)


def _job(job_id: str, platform: str) -> dict:
    urls = {
        "zhilian": "https://www.zhaopin.com/jobdetail/example.htm",
        "liepin": "https://www.liepin.com/job/1234567890.shtml",
    }
    return {
        "id": job_id,
        "title": "产品经理",
        "company": "示例公司",
        "source_platform": platform,
        "source_job_id": job_id,
        "url": urls.get(platform, "https://jobs.51job.com/example.html"),
    }


def test_external_manual_sent_is_atomic_and_idempotent():
    with tempfile.TemporaryDirectory() as temporary:
        db = get_db(Path(temporary) / "jobs.db")
        try:
            insert_job(db, _job("zhilian-manual", "zhilian"))

            first = mark_external_jobs_sent(db, ["zhilian-manual"], confirmed=True)
            second = mark_external_jobs_sent(db, ["zhilian-manual"], confirmed=True)
            status = db.execute("SELECT status FROM jobs WHERE id = ?", ("zhilian-manual",)).fetchone()["status"]
            history = db.execute(
                "SELECT action, detail FROM history WHERE job_id = ? ORDER BY id",
                ("zhilian-manual",),
            ).fetchall()
        finally:
            db.close()

    assert first["affected_count"] == 1
    assert second == {"requested_count": 1, "affected_count": 0, "already_sent": ["zhilian-manual"]}
    assert status == "sent"
    assert [(row["action"], row["detail"]) for row in history] == [
        ("manual_sent", "用户在智联招聘完成投递后手动标记"),
    ]


def test_manual_sent_supports_liepin_jobs():
    with tempfile.TemporaryDirectory() as temporary:
        db = get_db(Path(temporary) / "jobs.db")
        try:
            insert_job(db, _job("liepin-manual", "liepin"))

            result = mark_external_jobs_sent(db, ["liepin-manual"], confirmed=True)
            status = db.execute("SELECT status FROM jobs WHERE id = ?", ("liepin-manual",)).fetchone()["status"]
            history = db.execute(
                "SELECT action, detail FROM history WHERE job_id = ? ORDER BY id",
                ("liepin-manual",),
            ).fetchall()
        finally:
            db.close()

    assert result["affected_count"] == 1
    assert status == "sent"
    assert [(row["action"], row["detail"]) for row in history] == [
        ("manual_sent", "用户在猎聘完成投递后手动标记"),
    ]


def test_manual_sent_rejects_boss_and_does_not_partially_update_external_jobs():
    with tempfile.TemporaryDirectory() as temporary:
        db = get_db(Path(temporary) / "jobs.db")
        try:
            insert_job(db, _job("external", "51job"))
            insert_job(db, _job("boss", "boss"))
            with pytest.raises(JobManualSentConflictError) as captured:
                mark_external_jobs_sent(db, ["external", "boss"], confirmed=True)
            statuses = {
                row["id"]: row["status"]
                for row in db.execute("SELECT id, status FROM jobs WHERE id IN ('external', 'boss')").fetchall()
            }
        finally:
            db.close()

    assert captured.value.blocked == [{
        "job_id": "boss",
        "reasons": ["仅智联招聘、前程无忧和猎聘支持手动标记已发送"],
    }]
    assert statuses == {"boss": "pending", "external": "pending"}


def test_manual_sent_requires_explicit_confirmation():
    with tempfile.TemporaryDirectory() as temporary:
        db = get_db(Path(temporary) / "jobs.db")
        try:
            insert_job(db, _job("external", "51job"))
            with pytest.raises(JobDeletionConfirmationError):
                mark_external_jobs_sent(db, ["external"], confirmed=False)
        finally:
            db.close()


def test_manual_sent_falls_back_to_platform_code_when_label_missing(monkeypatch):
    import bosshunter.db as db_module

    monkeypatch.setattr(
        db_module,
        "EXTERNAL_MANUAL_SEND_PLATFORMS",
        db_module.EXTERNAL_MANUAL_SEND_PLATFORMS | {"boss"},
    )
    with tempfile.TemporaryDirectory() as temporary:
        db = get_db(Path(temporary) / "jobs.db")
        try:
            insert_job(db, _job("boss-extra", "boss"))
            result = mark_external_jobs_sent(db, ["boss-extra"], confirmed=True)
            history = db.execute(
                "SELECT action, detail FROM history WHERE job_id = ? ORDER BY id",
                ("boss-extra",),
            ).fetchall()
        finally:
            db.close()

    assert result["affected_count"] == 1
    assert [(row["action"], row["detail"]) for row in history] == [
        ("manual_sent", "用户在boss完成投递后手动标记"),
    ]
