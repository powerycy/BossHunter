import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from bosshunter.ai import scorer
from bosshunter.ai.scorer import ScoreOutcome
from bosshunter.db import (
    get_db,
    get_score_trace,
    insert_job,
    persist_job_score_and_trace,
    update_job_score,
    update_job_status,
)
from bosshunter.web import server


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": "AI Product Manager",
        "company": "Example",
        "salary": "20-30K",
        "city": "Shanghai",
        "experience": "3-5 years",
        "jd": "Build AI product features",
        "hr_name": "HR",
        "hr_title": "Recruiter",
        "hr_active": "active",
        "company_size": "100-499",
        "company_industry": "Software",
        "url": f"https://example.com/jobs/{job_id}",
    }


def _score_payload(**overrides) -> dict:
    payload = {
        "role_summary": "负责 AI 产品规划与落地",
        "core_duties": {"score": 34, "evidence": "有产品规划和项目落地经历"},
        "transferable_evidence": {"score": 21, "evidence": "有用户研究和跨团队协作成果"},
        "hard_requirements": {"score": 12, "evidence": "年限和核心技能多数满足"},
        "tools_industry": {"score": 7, "evidence": "熟悉 SaaS 和常用数据工具"},
        "practical_fit": {"score": 8, "evidence": "城市和薪资条件匹配"},
        "caps": ["technical_required"],
        "hard_gaps": ["缺少 Linux 私有化部署经历"],
        "reason": "产品交付经验匹配，但有硬技术缺口",
        "missing": "Linux 部署",
    }
    payload.update(overrides)
    return payload


def _result(**overrides):
    result = scorer._structured_score_result(_score_payload(**overrides))
    assert result is not None
    return result


def _request(path: str):
    status_headers = {}

    def start_response(status, headers, exc_info=None):
        status_headers["status"] = status
        status_headers["headers"] = dict(headers)

    response_iter = server.app(
        {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": "8686",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        },
        start_response,
    )
    try:
        body = b"".join(
            chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
            for chunk in response_iter
        ).decode("utf-8")
    finally:
        close = getattr(response_iter, "close", None)
        if close:
            close()
    return status_headers["status"], json.loads(body)


def test_structured_score_keeps_trace_fields_and_normalizes_untrusted_text():
    result = _result(
        role_summary="  岗位\n核心职责  ",
        core_duties={"score": 34, "evidence": "  有规划\n交付经验  "},
        hard_gaps=["  缺少 Linux  ", "缺少 Linux", {"unexpected": "object"}, "x" * 300],
    )

    assert result.role_summary == "岗位 核心职责"
    assert result.component_evidence["core_duties"] == "有规划 交付经验"
    assert result.hard_gaps == ("缺少 Linux", "x" * scorer.HARD_GAP_LIMIT)


def test_missing_evidence_and_malformed_hard_gaps_do_not_invalidate_structured_score():
    result = _result(
        core_duties={"score": 34},
        hard_gaps={"not": "an array"},
    )

    assert result.component_evidence["core_duties"] == ""
    assert result.hard_gaps == ()
    assert result.score == 55


def test_review_merge_prefers_review_evidence_and_unions_hard_gaps_stably():
    first = _result(
        role_summary="初评岗位概括",
        hard_gaps=["缺少 Linux", "缺少 SQL"],
        core_duties={"score": 30, "evidence": "初评职责证据"},
    )
    review = _result(
        role_summary="复核岗位概括",
        hard_gaps=["缺少 SQL", "缺少部署"],
        core_duties={"score": 32, "evidence": "复核职责证据"},
        caps=["weak_core_transfer"],
    )

    merged = scorer._merge_review_results(first, review)

    assert merged.reviewed is True
    assert merged.role_summary == "复核岗位概括"
    assert merged.component_evidence["core_duties"] == "复核职责证据"
    assert merged.hard_gaps == ("缺少 Linux", "缺少 SQL", "缺少部署")
    assert merged.caps == ("technical_required", "weak_core_transfer")


def test_trace_builder_has_only_contract_fields_and_never_includes_raw_inputs():
    payload = _score_payload(
        resume="PRIVATE_RESUME_MARKER",
        jd="PRIVATE_JD_MARKER",
        api_key="PRIVATE_KEY_MARKER",
        raw_response="PRIVATE_RAW_RESPONSE_MARKER",
    )
    result = scorer._structured_score_result(payload)
    assert result is not None

    trace = scorer.build_score_trace(result)
    serialized = json.dumps(trace, ensure_ascii=False)

    assert set(trace) == {
        "schema_version",
        "role_summary",
        "components",
        "raw_score",
        "final_score",
        "caps",
        "hard_gaps",
        "summary_reason",
        "missing",
        "review_status",
    }
    assert trace["components"]["core_duties"]["max_score"] == 40
    assert trace["raw_score"] == 82
    assert trace["final_score"] == 55
    assert trace["review_status"] == "initial"
    assert "PRIVATE_" not in serialized


def test_trace_sanitizer_rejects_boolean_schema_version():
    trace = scorer.build_score_trace(_result())
    trace["schema_version"] = True

    assert scorer.sanitize_score_trace(trace) is None


def test_score_trace_table_migrates_legacy_database_without_rewriting_jobs(tmp_path):
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL, salary TEXT,
                city TEXT, experience TEXT, jd TEXT, hr_name TEXT, hr_title TEXT, hr_active TEXT,
                company_size TEXT, company_industry TEXT, url TEXT, score INTEGER DEFAULT 0,
                score_reason TEXT, greeting TEXT, status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute("INSERT INTO jobs (id, title, company) VALUES ('legacy', 'PM', 'Example')")
        connection.commit()
    finally:
        connection.close()

    db = get_db(db_path)
    try:
        table = db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'score_traces'"
        ).fetchone()
        job = db.execute("SELECT title, company FROM jobs WHERE id = 'legacy'").fetchone()
    finally:
        db.close()

    assert table is not None
    assert dict(job) == {"title": "PM", "company": "Example"}


def test_persist_score_trace_replaces_current_trace_and_rolls_back_together(tmp_path):
    db = get_db(tmp_path / "trace.db")
    try:
        insert_job(db, _job("job-1"))
        first_trace = scorer.build_score_trace(_result(role_summary="第一次"))
        persist_job_score_and_trace(db, "job-1", 55, "first", first_trace)
        second_trace = scorer.build_score_trace(_result(role_summary="第二次"))
        persist_job_score_and_trace(db, "job-1", 55, "second", second_trace)
        found, trace = get_score_trace(db, "job-1")
        assert found is True
        assert trace is not None
        assert trace["role_summary"] == "第二次"

        db.execute("UPDATE jobs SET score = 19, score_reason = 'stable' WHERE id = 'job-1'")
        db.execute(
            """
            CREATE TRIGGER reject_trace_insert BEFORE INSERT ON score_traces
            WHEN NEW.job_id = 'job-2'
            BEGIN SELECT RAISE(ABORT, 'trace failure'); END
            """
        )
        insert_job(db, _job("job-2"))
        with pytest.raises(sqlite3.DatabaseError, match="trace failure"):
            persist_job_score_and_trace(db, "job-2", 55, "new", first_trace)
        job_two = db.execute("SELECT score, score_reason FROM jobs WHERE id = 'job-2'").fetchone()
        found_two, _ = get_score_trace(db, "job-2")
    finally:
        db.close()

    assert dict(job_two) == {"score": 0, "score_reason": None}
    assert found_two is False


def test_successful_score_persists_trace_while_prefilter_and_failure_do_not(tmp_path):
    db_path = tmp_path / "scoring.db"
    db = get_db(db_path)
    try:
        insert_job(db, _job("success"))
        insert_job(db, _job("prefilter"))
        insert_job(db, _job("failed"))
    finally:
        db.close()

    outcomes = [ScoreOutcome(result=_result()), ScoreOutcome(failure_detail="invalid JSON")]

    def quick_score(job, _config):
        return (0, "硬性条件不符") if job["id"] == "prefilter" else (80, "通过")

    with (
        patch("bosshunter.ai.scorer.get_db", side_effect=lambda: get_db(db_path)),
        patch("bosshunter.ai.scorer._load_resume", return_value="resume"),
        patch("bosshunter.ai.scorer.quick_score", side_effect=quick_score),
        patch("bosshunter.ai.scorer._score_job_with_ai", side_effect=outcomes),
    ):
        scored, filtered = scorer.score_jobs({"ai": {"scoring_concurrency": 1}, "scoring": {"threshold": 55}})

    assert (scored, filtered) == (1, 1)
    db = get_db(db_path)
    try:
        assert get_score_trace(db, "success")[0] is True
        assert get_score_trace(db, "prefilter")[0] is False
        assert get_score_trace(db, "failed")[0] is False
    finally:
        db.close()


def test_prefilter_and_failed_rescore_preserve_existing_valid_trace(tmp_path):
    db_path = tmp_path / "preserve.db"
    db = get_db(db_path)
    try:
        insert_job(db, _job("existing"))
        existing_trace = scorer.build_score_trace(_result(role_summary="保留的有效说明"))
        persist_job_score_and_trace(db, "existing", 55, "saved", existing_trace)
        update_job_status(db, "existing", "pending")
    finally:
        db.close()

    with (
        patch("bosshunter.ai.scorer.get_db", side_effect=lambda: get_db(db_path)),
        patch("bosshunter.ai.scorer._load_resume", return_value="resume"),
        patch("bosshunter.ai.scorer.quick_score", return_value=(0, "硬性条件不符")),
    ):
        scorer.score_jobs({"ai": {"scoring_concurrency": 1}, "scoring": {"threshold": 55}})

    db = get_db(db_path)
    try:
        update_job_status(db, "existing", "pending")
    finally:
        db.close()
    with (
        patch("bosshunter.ai.scorer.get_db", side_effect=lambda: get_db(db_path)),
        patch("bosshunter.ai.scorer._load_resume", return_value="resume"),
        patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
        patch(
            "bosshunter.ai.scorer._score_job_with_ai",
            return_value=ScoreOutcome(failure_detail="invalid JSON"),
        ),
    ):
        scorer.score_jobs({"ai": {"scoring_concurrency": 1}, "scoring": {"threshold": 55}})

    db = get_db(db_path)
    try:
        found, trace = get_score_trace(db, "existing")
    finally:
        db.close()

    assert found is True
    assert trace is not None
    assert trace["role_summary"] == "保留的有效说明"


def test_score_trace_api_exposes_safe_states_and_never_changes_job_list_payload(tmp_path):
    original_base_dir = server.BASE_DIR
    base_dir = Path(tmp_path)
    try:
        db = get_db(base_dir / "data" / "bosshunter.db")
        try:
            for job_id in ("available", "legacy", "prefilter", "failed", "corrupt"):
                insert_job(db, _job(job_id))
            persist_job_score_and_trace(db, "available", 55, "safe", scorer.build_score_trace(_result()))
            update_job_score(db, "legacy", 80, "legacy score")
            update_job_status(db, "legacy", "ready")
            update_job_score(db, "prefilter", 0, "预筛不通过: 硬性条件不符")
            update_job_status(db, "prefilter", "filtered")
            update_job_score(db, "failed", 0, "AI评分失败: invalid JSON")
            db.execute(
                "INSERT INTO score_traces (job_id, schema_version, trace_json) VALUES (?, ?, ?)",
                ("corrupt", 1, "not-json"),
            )
            db.commit()
        finally:
            db.close()
        server.set_base_dir(base_dir)

        available_status, available = _request("/api/jobs/available/score-trace")
        legacy_status, legacy = _request("/api/jobs/legacy/score-trace")
        prefilter_status, prefilter = _request("/api/jobs/prefilter/score-trace")
        failed_status, failed = _request("/api/jobs/failed/score-trace")
        corrupt_status, corrupt = _request("/api/jobs/corrupt/score-trace")
        missing_status, missing = _request("/api/jobs/missing/score-trace")
    finally:
        server.set_base_dir(original_base_dir)

    assert available_status.startswith("200")
    assert available["state"] == "available"
    assert set(available["trace"]) == {
        "schema_version",
        "role_summary",
        "components",
        "raw_score",
        "final_score",
        "caps",
        "hard_gaps",
        "summary_reason",
        "missing",
        "review_status",
    }
    assert legacy_status.startswith("200") and legacy["state"] == "legacy_missing"
    assert prefilter_status.startswith("200") and prefilter["state"] == "prefilter_only"
    assert failed_status.startswith("200") and failed["state"] == "failed"
    assert corrupt_status.startswith("200") and corrupt["state"] == "unavailable"
    assert missing_status.startswith("404") and missing == {"error": "job_not_found"}
