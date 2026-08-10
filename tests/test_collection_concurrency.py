import unittest
from threading import Event, Thread
from unittest.mock import Mock, patch

from bosshunter.config import DEFAULTS, get_collection_concurrency, normalize_scoring_config
from bosshunter.scraper.jobs import _scrape_combo, scrape_jobs


class CollectionConcurrencyConfigTests(unittest.TestCase):
    def test_defaults_to_one_and_clamps_to_one_through_three(self):
        self.assertEqual(DEFAULTS["search"]["collection_concurrency"], 1)
        self.assertEqual(get_collection_concurrency({}), 1)
        self.assertEqual(get_collection_concurrency({"search": {"collection_concurrency": 0}}), 1)
        self.assertEqual(get_collection_concurrency({"search": {"collection_concurrency": 2}}), 2)
        self.assertEqual(get_collection_concurrency({"search": {"collection_concurrency": 99}}), 3)
        self.assertEqual(get_collection_concurrency({"search": {"collection_concurrency": "invalid"}}), 1)

    def test_normalization_persists_the_bounded_collection_concurrency(self):
        config = {"search": {"collection_concurrency": 99}}

        normalize_scoring_config(config)

        self.assertEqual(config["search"]["collection_concurrency"], 3)

    def test_runs_city_keyword_combinations_in_parallel(self):
        started = []
        both_started = Event()
        release_workers = Event()
        result = {}

        def fake_scrape_combo(config, combo, *, max_pages, limit, stop_event):
            started.append(combo)
            if len(started) == 2:
                both_started.set()
            release_workers.wait(3)
            return 1, {combo[2]}

        config = {
            "profile": {"target_cities": ["北京", "上海"]},
            "search": {"max_pages": 1, "collection_concurrency": 2},
        }

        with patch("bosshunter.scraper.jobs._scrape_combo", side_effect=fake_scrape_combo):
            with patch("bosshunter.scraper.jobs.console"):
                with patch("bosshunter.scraper.jobs.time.sleep"):
                    thread = Thread(target=lambda: result.setdefault("count", scrape_jobs(config, ["AI"])))
                    thread.start()
                    self.assertTrue(both_started.wait(0.5))
                    release_workers.set()
                    thread.join(3)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result["count"], 2)

    def test_worker_failure_does_not_stop_remaining_combinations(self):
        completed = []

        def fake_scrape_combo(config, combo, *, max_pages, limit, stop_event):
            if combo[0] == "北京":
                raise RuntimeError("temporary failure")
            completed.append(combo[0])
            return 1, {combo[0]}

        config = {
            "profile": {"target_cities": ["北京", "上海", "深圳"]},
            "search": {"max_pages": 1, "collection_concurrency": 2},
        }

        with patch("bosshunter.scraper.jobs._scrape_combo", side_effect=fake_scrape_combo), \
             patch("bosshunter.scraper.jobs.console"):
            count = scrape_jobs(config, ["AI"])

        self.assertEqual(count, 2)
        self.assertEqual(set(completed), {"上海", "深圳"})

    def test_stop_event_prevents_submitting_the_next_combination(self):
        calls = []
        stop_event = Event()

        def fake_scrape_combo(config, combo, *, max_pages, limit, stop_event):
            calls.append(combo)
            stop_event.set()
            return 1, {combo[0]}

        config = {
            "profile": {"target_cities": ["北京", "上海"]},
            "search": {"max_pages": 1, "collection_concurrency": 1},
            "_workbench_stop_event": stop_event,
        }

        with patch("bosshunter.scraper.jobs._scrape_combo", side_effect=fake_scrape_combo):
            scrape_jobs(config, ["AI"])

        self.assertEqual(len(calls), 1)

    def test_each_worker_opens_and_closes_its_own_database_connection(self):
        first_db = Mock()
        second_db = Mock()
        config = {"profile": {}, "search": {}}
        combo = ("北京", "101010100", "AI")

        with patch("bosshunter.scraper.jobs.get_db", side_effect=[first_db, second_db]) as get_db, \
             patch("bosshunter.scraper.jobs.new_tab", return_value=None):
            _scrape_combo(config, combo, max_pages=1, limit=None, stop_event=None)
            _scrape_combo(config, combo, max_pages=1, limit=None, stop_event=None)

        self.assertEqual(get_db.call_count, 2)
        first_db.close.assert_called_once_with()
        second_db.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
