import json
import tempfile
from pathlib import Path
from unittest import TestCase

from bosshunter.collection_run_store import (
    create_collection_run,
    get_collection_run,
    mark_orphaned_collection_runs_stopped,
    update_collection_run,
)
from bosshunter.db import get_db, insert_job


class CollectionRunAndMigrationTests(TestCase):
    def test_source_migration_is_idempotent_and_preserves_legacy_boss_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jobs.db"
            db = get_db(path)
            insert_job(db, {
                "id": "legacy-boss-1",
                "title": "旧岗位",
                "company": "旧公司",
                "jd": "旧 JD",
            })
            db.close()
            db = get_db(path)
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)").fetchall()}
            index_names = {row[1] for row in db.execute("PRAGMA index_list(jobs)").fetchall()}
            legacy = db.execute(
                "SELECT id, source_platform, source_job_id FROM jobs WHERE id = ?",
                ("legacy-boss-1",),
            ).fetchone()
            db.close()
            db = get_db(path)
            try:
                repeated_columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)").fetchall()}
            finally:
                db.close()

        self.assertTrue({"source_platform", "source_job_id", "source_keyword", "source_city_code"} <= columns)
        self.assertIn("idx_jobs_source_identity", index_names)
        self.assertEqual(repeated_columns, columns)
        self.assertEqual(dict(legacy), {"id": "legacy-boss-1", "source_platform": "boss", "source_job_id": None})

    def test_collection_run_checkpoint_persists_platform_progress_and_restart_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runs.db"
            options = {"platform_order": ["boss", "zhilian"], "auto_score": False}
            initial_states = {"boss": {"status": "queued"}, "zhilian": {"status": "queued"}}
            create_collection_run(path, run_id="run-1", task_id="task-1", options=options, platform_states=initial_states)
            update_collection_run(
                path,
                "run-1",
                status="running",
                current_platform="boss",
                platform_states={"boss": {"status": "running", "new": 3}},
                collected_job_ids=["boss-1", "boss-2", "boss-3"],
            )
            saved = get_collection_run(path, "run-1")
            self.assertEqual(saved["platform_states"]["boss"]["new"], 3)
            self.assertEqual(saved["collected_job_ids"], ["boss-1", "boss-2", "boss-3"])
            self.assertEqual(mark_orphaned_collection_runs_stopped(path), 1)
            stopped = get_collection_run(path, "run-1")

        self.assertEqual(stopped["status"], "stopped")
        self.assertIn("应用已重启", stopped["stop_reason"])
