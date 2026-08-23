import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bosshunter.config import DEFAULTS
from bosshunter.db import (
    add_platform_access,
    count_jobs_created_today,
    count_platform_access_today,
    get_active_platform_safety_lock,
    get_db,
    insert_job,
    set_platform_safety_lock,
)
from bosshunter.platform_safety import PlatformAccessGuard, PlatformSafetyStop
from bosshunter.scraper.jobs import scrape_jobs
from bosshunter.web.server import _execute_collect, _wait_for_collection_delivery_cooldown
from bosshunter.web.tasks import WorkbenchTask


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": "AI 产品经理",
        "company": "示例公司",
        "salary": "20-30K",
        "city": "北京",
        "experience": "3-5年",
        "jd": "产品工作",
        "hr_name": "",
        "hr_title": "",
        "hr_active": "",
        "company_size": "",
        "company_industry": "",
        "url": f"https://www.zhipin.com/job_detail/{job_id}.html",
    }


class CollectionSafetyTests(unittest.TestCase):
    def test_default_limits_are_daily_only(self):
        collection = DEFAULTS["collection"]
        self.assertEqual(collection["daily_new_jobs_limit"], 100)
        self.assertEqual(collection["daily_search_page_limit"], 30)
        self.assertEqual(collection["daily_detail_page_limit"], 150)
        self.assertNotIn("max_new_jobs_per_cycle", collection)
        self.assertNotIn("max_search_pages_per_cycle", collection)

    def test_daily_access_limit_stops_before_the_next_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            guard = PlatformAccessGuard(db, {"safety": {"daily_platform_page_limit": 500}}, "collection")
            for _ in range(30):
                guard.reserve("search_page", daily_limit=30)

            with self.assertRaises(PlatformSafetyStop) as raised:
                guard.reserve("search_page", daily_limit=30)

            self.assertEqual(raised.exception.reason, "daily_search_page_limit")
            self.assertEqual(
                count_platform_access_today(db, stage="collection", action="search_page"),
                30,
            )
            db.close()

    def test_global_page_budget_is_shared_across_workflows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            config = {"safety": {"daily_platform_page_limit": 2}}
            PlatformAccessGuard(db, config, "collection").reserve("search_page")
            PlatformAccessGuard(db, config, "send").reserve("job_page")

            with self.assertRaises(PlatformSafetyStop) as raised:
                PlatformAccessGuard(db, config, "monitor").reserve("monitor_page")

            self.assertEqual(raised.exception.reason, "daily_platform_page_limit")
            db.close()

    def test_unique_daily_count_ignores_accesses_and_counts_jobs_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            insert_job(db, _job("one"))
            add_platform_access(db, "collection", "search_page")
            add_platform_access(db, "collection", "detail_page")
            self.assertEqual(count_jobs_created_today(db), 1)
            db.close()

    def test_risk_lock_survives_a_new_database_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "bosshunter.db"
            db = get_db(db_path)
            set_platform_safety_lock(db, "captcha", minutes=30)
            db.close()

            reopened = get_db(db_path)
            lock = get_active_platform_safety_lock(reopened)
            self.assertEqual(lock["reason"], "captcha")
            reopened.close()

    def test_daily_new_job_limit_stops_without_opening_platform_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "bosshunter.db"
            db = get_db(db_path)
            for index in range(100):
                insert_job(db, _job(f"job-{index}"))
            config = {
                "profile": {"target_cities": ["北京"]},
                "search": {"max_pages": 1},
                "collection": {"daily_new_jobs_limit": 100},
            }

            with patch("bosshunter.scraper.jobs.get_db", return_value=db), \
                 patch("bosshunter.scraper.jobs.new_tab") as new_tab:
                count = scrape_jobs(config, ["AI"])

            self.assertEqual(count, 0)
            self.assertEqual(config["_workbench_collect_report"]["stop_reason"], "daily_new_jobs_limit")
            new_tab.assert_not_called()

    def test_collection_risk_stops_and_records_safe_reason(self):
        db = Mock()
        progress = Mock()
        progress.add_task.return_value = "task"
        context = Mock()
        context.__enter__ = Mock(return_value=progress)
        context.__exit__ = Mock(return_value=False)
        config = {
            "profile": {"target_cities": ["北京"]},
            "search": {"max_pages": 1},
        }

        with patch("bosshunter.scraper.jobs.get_db", return_value=db), \
             patch("bosshunter.scraper.jobs.count_jobs_created_today", return_value=0), \
             patch("bosshunter.scraper.jobs.PlatformAccessGuard") as guard_cls, \
             patch("bosshunter.scraper.jobs.Progress", return_value=context), \
             patch("bosshunter.scraper.jobs.new_tab", return_value="worker"), \
             patch("bosshunter.scraper.jobs.wait_for_load"), \
             patch("bosshunter.scraper.jobs.evaluate", return_value=json.dumps({"risk": "captcha"})), \
             patch("bosshunter.scraper.jobs.close_tab"), \
             patch("bosshunter.scraper.jobs.time.sleep"):
            count = scrape_jobs(config, ["AI"])

        self.assertEqual(count, 0)
        self.assertEqual(config["_workbench_collect_report"]["stop_reason"], "captcha")
        guard_cls.return_value.lock.assert_called_once_with("captcha")

    def test_frontend_task_log_explains_daily_limit(self):
        task = WorkbenchTask(id="collect", mode="collect", label="单独采集")
        config = {"search": {"keywords": ["AI"]}}

        def fake_scrape(collect_config, _keywords, *, collected_job_ids=None):
            collect_config["_workbench_collect_report"] = {"stop_reason": "daily_search_page_limit"}
            return 0

        with patch("bosshunter.scraper.jobs.scrape_jobs", side_effect=fake_scrape), \
             patch("bosshunter.ai.scorer.score_jobs", return_value=(0, 0)):
            _execute_collect(task, config)

        self.assertTrue(any("为了账户安全" in line and "单日搜索页上限" in line for line in task.logs))

    def test_collection_delivery_cooldown_is_cancellable(self):
        task = WorkbenchTask(id="full", mode="full", label="运行全流程")
        task.context["collection_completed_monotonic"] = time.monotonic()
        task.stop_requested.set()
        self.assertTrue(
            _wait_for_collection_delivery_cooldown(
                task,
                {"collection": {"delivery_cooldown_minutes": 30}},
            )
        )


if __name__ == "__main__":
    unittest.main()
