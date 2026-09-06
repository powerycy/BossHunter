"""Boundary tests for collection/scoring run stores (DB layer).

These cover JSON round-tripping, terminal-status side effects, limit
clamping and orphan recovery without touching network or AI paths.
"""

import tempfile
from pathlib import Path

from bosshunter.collection_run_store import (
    create_collection_run,
    get_collection_run,
    list_collection_runs,
    mark_orphaned_collection_runs_stopped,
    update_collection_run,
)
from bosshunter.scoring_run_store import (
    TERMINAL_RUN_STATUSES,
    create_scoring_run,
    get_scoring_run,
    list_scoring_runs,
    mark_orphaned_scoring_runs_paused,
    update_scoring_run,
)


def _tmp_db() -> Path:
    return Path(tempfile.mkdtemp()) / "runs.db"


# ---------------------------------------------------------------------------
# collection_run_store
# ---------------------------------------------------------------------------


def test_create_then_get_decodes_json_fields():
    db = _tmp_db()
    create_collection_run(
        db,
        run_id="run-1",
        options={"city": "北京", "keyword": "Python"},
        platform_states={"zhilian": {"page": 3}},
    )
    row = get_collection_run(db, "run-1")
    assert row is not None
    assert row["options"] == {"city": "北京", "keyword": "Python"}
    assert row["platform_states"] == {"zhilian": {"page": 3}}
    assert row["collected_job_ids"] == []


def test_get_missing_collection_run_returns_none():
    db = _tmp_db()
    assert get_collection_run(db, "nope") is None


def test_terminal_status_sets_finished_at():
    db = _tmp_db()
    create_collection_run(db, run_id="run-1", options={}, platform_states={})
    update_collection_run(db, "run-1", status="completed")
    row = get_collection_run(db, "run-1")
    assert row["status"] == "completed"
    assert row["finished_at"] is not None


def test_non_terminal_status_does_not_set_finished_at():
    db = _tmp_db()
    create_collection_run(db, run_id="run-1", options={}, platform_states={})
    update_collection_run(db, "run-1", status="running")
    row = get_collection_run(db, "run-1")
    assert row["finished_at"] is None


def test_list_collection_runs_clamps_limit():
    db = _tmp_db()
    for i in range(5):
        create_collection_run(db, run_id=f"run-{i}", options={}, platform_states={})
    assert len(list_collection_runs(db, limit=0)) == 1      # <1 clamped to 1
    assert len(list_collection_runs(db, limit=2)) == 2
    assert len(list_collection_runs(db, limit=9999)) == 5    # >100 clamped (only 5 exist)


def test_mark_orphaned_collection_runs_only_affects_active():
    db = _tmp_db()
    create_collection_run(db, run_id="active", options={}, platform_states={})
    create_collection_run(db, run_id="done", options={}, platform_states={})
    update_collection_run(db, "done", status="completed")

    affected = mark_orphaned_collection_runs_stopped(db)
    assert affected == 1
    assert get_collection_run(db, "active")["status"] == "stopped"
    assert get_collection_run(db, "done")["status"] == "completed"


# ---------------------------------------------------------------------------
# scoring_run_store
# ---------------------------------------------------------------------------


def test_create_scoring_run_initializes_progress():
    db = _tmp_db()
    run = create_scoring_run(db, run_id="s-1", options={"model": "x"}, job_ids=["a", "b", "c"])
    assert run["options"] == {"model": "x"}
    assert run["remaining_job_ids"] == ["a", "b", "c"]
    assert run["progress"] == {"selected": 3, "completed": 0}
    assert run["recoverable"] is False


def test_recoverable_true_only_when_paused_with_remaining():
    db = _tmp_db()
    create_scoring_run(db, run_id="s-1", options={}, job_ids=["a"])
    update_scoring_run(db, "s-1", status="paused", remaining_job_ids=["a"])
    assert get_scoring_run(db, "s-1")["recoverable"] is True

    update_scoring_run(db, "s-1", status="paused", remaining_job_ids=[])
    assert get_scoring_run(db, "s-1")["recoverable"] is False


def test_scoring_terminal_status_sets_finished_at():
    db = _tmp_db()
    create_scoring_run(db, run_id="s-1", options={}, job_ids=["a"])
    update_scoring_run(db, "s-1", status="completed")
    assert get_scoring_run(db, "s-1")["finished_at"] is not None


def test_scoring_running_resumes_paused_and_clears_error():
    db = _tmp_db()
    create_scoring_run(db, run_id="s-1", options={}, job_ids=["a"])
    update_scoring_run(db, "s-1", status="paused", error="boom")
    update_scoring_run(db, "s-1", status="running")
    row = get_scoring_run(db, "s-1")
    assert row["status"] == "running"
    assert row["finished_at"] is None
    assert row["error"] is None


def test_scoring_running_cannot_resurrect_terminal_run():
    # 已终态的 run 不应被 update_scoring_run(running=...) 改回 running，
    # 这是由 WHERE status NOT IN (终态) 保护的安全边界。
    db = _tmp_db()
    create_scoring_run(db, run_id="s-1", options={}, job_ids=["a"])
    update_scoring_run(db, "s-1", status="completed")
    update_scoring_run(db, "s-1", status="running")
    assert get_scoring_run(db, "s-1")["status"] == "completed"


def test_mark_orphaned_scoring_runs_pauses_active_only():
    db = _tmp_db()
    create_scoring_run(db, run_id="active", options={}, job_ids=["a"])
    create_scoring_run(db, run_id="done", options={}, job_ids=["b"])
    update_scoring_run(db, "done", status="completed")

    affected = mark_orphaned_scoring_runs_paused(db)
    assert affected == 1
    assert get_scoring_run(db, "active")["status"] == "paused"
    assert get_scoring_run(db, "done")["status"] == "completed"


def test_list_scoring_runs_returns_decoded_rows():
    db = _tmp_db()
    create_scoring_run(db, run_id="s-1", options={"k": 1}, job_ids=["a"])
    rows = list_scoring_runs(db, limit=10)
    assert len(rows) == 1
    assert rows[0]["options"] == {"k": 1}
    assert rows[0]["remaining_job_ids"] == ["a"]


def test_terminal_run_statuses_are_consistent():
    # 防御性测试：确保 TERMINAL_RUN_STATUSES 与 update 逻辑认知一致，
    # 后续若有人改动常量不会破坏终态判断。
    assert TERMINAL_RUN_STATUSES == {"completed", "completed_with_errors", "failed", "stopped"}

