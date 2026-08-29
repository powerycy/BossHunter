from threading import Event
from unittest.mock import patch

import pytest

from bosshunter.ai.scorer import ScoreOutcome, score_jobs
from bosshunter.db import (
	JobDeletionConflictError,
	get_db,
	insert_job,
	soft_delete_jobs,
	update_job_score,
	update_job_status,
)
from bosshunter.scoring_run_store import (
	create_scoring_run,
	get_scoring_run,
	mark_orphaned_scoring_runs_paused,
	update_scoring_run,
)
from bosshunter.scoring_selection import preview_scoring, select_scoring_jobs, validate_options


def _job(job_id: str) -> dict:
	return {
		"id": job_id,
		"title": f"AI Product Manager {job_id}",
		"company": "Example",
		"salary": "20-30K",
		"city": "Shanghai",
		"experience": "1-3 years",
		"jd": "Build AI product features",
		"hr_name": "HR",
		"hr_title": "Recruiter",
		"hr_active": "active",
		"company_size": "100-499",
		"company_industry": "Software",
		"url": f"https://example.com/jobs/{job_id}",
	}


def test_default_selection_scores_all_unscored_pending_but_never_historical_or_deleted(tmp_path):
	db = get_db(tmp_path / "selection.db")
	try:
		for job_id in ("old-pending", "new-pending", "already-scored", "deleted-pending"):
			insert_job(db, _job(job_id))
		update_job_score(db, "already-scored", 82, "already evaluated")
		update_job_status(db, "already-scored", "ready")
		soft_delete_jobs(db, ["deleted-pending"], confirmed=True)

		selected = select_scoring_jobs(db)
		preview = preview_scoring(db)
	finally:
		db.close()

	assert {job["id"] for job in selected} == {"old-pending", "new-pending"}
	assert preview["eligible_jobs"] == 2
	assert preview["job_ids"] and len(preview["job_ids"]) == 2


def test_selected_jobs_do_not_rescore_existing_results_without_explicit_force(tmp_path):
	db = get_db(tmp_path / "force.db")
	try:
		for job_id in ("pending", "scored"):
			insert_job(db, _job(job_id))
		update_job_score(db, "scored", 75, "evaluated")
		update_job_status(db, "scored", "scored")

		normal = select_scoring_jobs(db, scope="selected", job_ids=["pending", "scored"])
		forced = select_scoring_jobs(
			db,
			scope="selected",
			job_ids=["pending", "scored"],
			force_rescore=True,
		)
	finally:
		db.close()

	assert [job["id"] for job in normal] == ["pending"]
	assert {job["id"] for job in forced} == {"pending", "scored"}


def test_scoring_selection_rejects_non_array_job_ids():
	with pytest.raises(ValueError, match="岗位 ID 必须是数组"):
		validate_options(scope="selected", job_ids="job-one")


def test_pause_checkpoint_keeps_current_and_unstarted_jobs_for_recovery(tmp_path):
	db_path = tmp_path / "checkpoint.db"
	db = get_db(db_path)
	try:
		for job_id in ("one", "two", "three"):
			insert_job(db, _job(job_id))
	finally:
		db.close()

	checkpoints: list[dict] = []
	outcomes = [
		ScoreOutcome(failure_detail="temporary invalid response"),
		ScoreOutcome(pause_reason="AI quota exhausted"),
	]
	with (
		patch("bosshunter.ai.scorer.get_db", side_effect=lambda: get_db(db_path)),
		patch("bosshunter.ai.scorer._load_resume", return_value="real resume"),
		patch("bosshunter.ai.scorer.quick_score", return_value=(80, "pass")),
		patch("bosshunter.ai.scorer._score_job_with_ai", side_effect=outcomes),
	):
		score_jobs(
			{
				"ai": {"scoring_concurrency": 1},
				"scoring": {"threshold": 60},
				"_workbench_score_checkpoint": checkpoints.append,
			}
		)

	assert checkpoints[-1]["status"] == "paused"
	assert checkpoints[-1]["pause_reason"] == "AI quota exhausted"
	assert checkpoints[-1]["error"] == "AI quota exhausted"
	assert len(checkpoints[-1]["remaining_job_ids"]) == 2
	assert set(checkpoints[-1]["remaining_job_ids"]).issubset({"one", "two", "three"})


def test_user_stop_pause_keeps_error_empty_for_clean_record(tmp_path):
	db_path = tmp_path / "userstop.db"
	db = get_db(db_path)
	try:
		for job_id in ("one", "two"):
			insert_job(db, _job(job_id))
	finally:
		db.close()

	checkpoints: list[dict] = []
	stop_event = Event()
	stop_event.set()
	with (
		patch("bosshunter.ai.scorer.get_db", side_effect=lambda: get_db(db_path)),
		patch("bosshunter.ai.scorer._load_resume", return_value="real resume"),
		patch("bosshunter.ai.scorer.quick_score", return_value=(80, "pass")),
	):
		score_jobs(
			{
				"ai": {"scoring_concurrency": 1},
				"scoring": {"threshold": 60},
				"_workbench_stop_event": stop_event,
				"_workbench_score_checkpoint": checkpoints.append,
			}
		)

	assert checkpoints[-1]["status"] == "paused"
	assert checkpoints[-1]["pause_reason"] == "用户暂停或任务中断"
	assert checkpoints[-1]["error"] == ""


def test_restart_preserves_remaining_jobs_and_marks_run_recoverable(tmp_path):
	db_path = tmp_path / "restart.db"
	get_db(db_path).close()
	create_scoring_run(
		db_path,
		run_id="run-restart",
		options={"scope": "pending", "limit": None, "force_rescore": False},
		job_ids=["one", "two"],
	)
	update_scoring_run(
		db_path,
		"run-restart",
		status="running",
		remaining_job_ids=["two"],
	)

	assert mark_orphaned_scoring_runs_paused(db_path) == 1
	run = get_scoring_run(db_path, "run-restart")

	assert run is not None
	assert run["status"] == "paused"
	assert run["remaining_job_ids"] == ["two"]
	assert run["recoverable"] is True


def test_recycle_bin_cannot_remove_jobs_referenced_by_recoverable_scoring_run(tmp_path):
	db_path = tmp_path / "conflict.db"
	db = get_db(db_path)
	try:
		insert_job(db, _job("in-flight"))
	finally:
		db.close()
	create_scoring_run(
		db_path,
		run_id="run-conflict",
		options={"scope": "pending", "limit": None, "force_rescore": False},
		job_ids=["in-flight"],
	)
	update_scoring_run(db_path, "run-conflict", status="paused")

	db = get_db(db_path)
	try:
		with pytest.raises(JobDeletionConflictError):
			soft_delete_jobs(db, ["in-flight"], confirmed=True)
	finally:
		db.close()

	update_scoring_run(db_path, "run-conflict", status="stopped", remaining_job_ids=[])
	db = get_db(db_path)
	try:
		result = soft_delete_jobs(db, ["in-flight"], confirmed=True)
	finally:
		db.close()
	assert result["affected_count"] == 1
