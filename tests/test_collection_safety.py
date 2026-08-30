import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bosshunter.config import DEFAULTS, load_config
from bosshunter.db import (
    add_platform_access,
    count_platform_access_today,
    get_active_platform_safety_lock,
    get_db,
    set_platform_safety_lock,
)
from bosshunter.platform_safety import PlatformAccessGuard, PlatformSafetyStop
from bosshunter.scraper.jobs import scrape_jobs
from bosshunter.web.server import _execute_collect, _wait_for_collection_delivery_cooldown
from bosshunter.web.tasks import WorkbenchTask

class CollectionSafetyTests(unittest.TestCase):
    def test_default_limits_are_daily_only(self):
        collection = DEFAULTS["collection"]
        self.assertNotIn("daily_new_jobs_limit", collection)
        self.assertEqual(collection["daily_search_page_limit"], 60)
        self.assertEqual(collection["daily_detail_page_limit"], 150)
        self.assertEqual(collection["risk_pause_min_minutes"], 5)
        self.assertEqual(collection["risk_pause_max_minutes"], 10)
        self.assertEqual(collection["collection_delay_multiplier"], 1.5)
        self.assertNotIn("max_new_jobs_per_cycle", collection)
        self.assertNotIn("max_search_pages_per_cycle", collection)

    def test_retired_count_limits_are_removed_from_existing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(
                """
search:
  target_count: 88
collection:
  default_target_count: 66
  daily_new_jobs_limit: 100
platforms:
  boss:
    search:
      target_count: 77
  zhilian:
    search:
      target_count: 55
  51job:
    search:
      target_count: 44
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertNotIn("target_count", config["search"])
        self.assertNotIn("default_target_count", config["collection"])
        self.assertNotIn("daily_new_jobs_limit", config["collection"])
        for platform in ("boss", "zhilian", "51job"):
            self.assertNotIn("target_count", config["platforms"][platform]["search"])

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

    def test_external_page_events_do_not_consume_boss_page_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            add_platform_access(db, "collection", "search_page", platform="zhilian")
            guard = PlatformAccessGuard(db, {"safety": {"daily_platform_page_limit": 1}}, "collection")
            guard.reserve("search_page")
            with self.assertRaises(PlatformSafetyStop) as raised:
                guard.reserve("search_page")
            self.assertEqual(raised.exception.reason, "daily_platform_page_limit")
            self.assertEqual(count_platform_access_today(db, platform="boss"), 1)
            self.assertEqual(count_platform_access_today(db, platform="zhilian"), 1)
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
             patch("bosshunter.collection.platforms.boss.PlatformAccessGuard") as guard_cls, \
             patch("bosshunter.scraper.jobs.Progress", return_value=context), \
             patch("bosshunter.scraper.jobs.new_tab", return_value="worker"), \
             patch("bosshunter.scraper.jobs.wait_for_load"), \
             patch("bosshunter.scraper.jobs.evaluate", return_value=json.dumps({"risk": "captcha"})), \
             patch("bosshunter.scraper.jobs.close_tab"), \
             patch("bosshunter.scraper.jobs.time.sleep"):
            count = scrape_jobs(config, ["AI"])

        self.assertEqual(count, 0)
        self.assertEqual(config["_workbench_collect_report"]["stop_reason"], "captcha")
        guard_cls.return_value.lock.assert_called_once()
        lock_call = guard_cls.return_value.lock.call_args
        self.assertEqual(lock_call.args, ("captcha",))
        self.assertGreaterEqual(lock_call.kwargs["minutes"], 5)
        self.assertLessEqual(lock_call.kwargs["minutes"], 10)

    def test_transient_collection_risk_is_ignored_without_locking(self):
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
             patch("bosshunter.collection.platforms.boss.PlatformAccessGuard") as guard_cls, \
             patch("bosshunter.scraper.jobs.Progress", return_value=context), \
             patch("bosshunter.scraper.jobs.new_tab", return_value="worker"), \
             patch("bosshunter.scraper.jobs.wait_for_load"), \
             patch(
                 "bosshunter.scraper.jobs.evaluate",
                 side_effect=[
                     json.dumps({"risk": "blocked", "evidence": "blocked_page"}),
                     json.dumps({"risk": None}),
                     json.dumps([]),
                 ],
             ), \
             patch("bosshunter.scraper.jobs.scroll"), \
             patch("bosshunter.scraper.jobs.close_tab"), \
             patch("bosshunter.scraper.jobs.time.sleep"):
            count = scrape_jobs(config, ["AI"])

        self.assertEqual(count, 0)
        self.assertEqual(config["_workbench_collect_report"]["stop_reason"], "search_exhausted")
        guard_cls.return_value.lock.assert_not_called()

    def test_consecutive_page_failures_end_collection_without_risk_lock(self):
        db = Mock()
        progress = Mock()
        progress.add_task.return_value = "task"
        context = Mock()
        context.__enter__ = Mock(return_value=progress)
        context.__exit__ = Mock(return_value=False)
        config = {
            "profile": {"target_cities": ["北京"]},
            "search": {"max_pages": 3},
            "collection": {"max_consecutive_page_failures": 3},
        }

        with patch("bosshunter.scraper.jobs.get_db", return_value=db), \
             patch("bosshunter.collection.platforms.boss.PlatformAccessGuard") as guard_cls, \
             patch("bosshunter.scraper.jobs.Progress", return_value=context), \
             patch("bosshunter.scraper.jobs.new_tab", return_value=None), \
             patch("bosshunter.scraper.jobs.close_tab"), \
             patch("bosshunter.scraper.jobs.time.sleep"):
            count = scrape_jobs(config, ["AI"])

        self.assertEqual(count, 0)
        self.assertEqual(config["_workbench_collect_report"]["stop_reason"], "consecutive_page_failures")
        guard_cls.return_value.lock.assert_not_called()

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
        task.context["boss_collection_completed_monotonic"] = time.monotonic()
        task.stop_requested.set()
        self.assertTrue(
            _wait_for_collection_delivery_cooldown(
                task,
                {
                    "collection": {
                        "delivery_cooldown_min_minutes": 5,
                        "delivery_cooldown_max_minutes": 15,
                    }
                },
            )
        )

    def test_collection_delivery_cooldown_selects_one_random_value_per_flow(self):
        task = WorkbenchTask(id="full", mode="full", label="运行全流程")
        task.context["boss_collection_completed_monotonic"] = time.monotonic()
        task.stop_requested.set()
        config = {
            "collection": {
                "delivery_cooldown_min_minutes": 5,
                "delivery_cooldown_max_minutes": 15,
            }
        }

        with patch("bosshunter.web.server.random.uniform", return_value=11.25) as choose:
            self.assertTrue(_wait_for_collection_delivery_cooldown(task, config))
            self.assertTrue(_wait_for_collection_delivery_cooldown(task, config))

        choose.assert_called_once_with(5, 15)
        self.assertEqual(task.context["boss_delivery_cooldown_minutes"], 11.25)


if __name__ == "__main__":
    unittest.main()
