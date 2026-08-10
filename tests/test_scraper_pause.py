import unittest
from threading import Event
from unittest.mock import Mock, patch

from bosshunter.scraper.jobs import _scrape_combo
from bosshunter.throttle import PageThrottle


class ScraperPauseTests(unittest.TestCase):
    def test_search_page_pause_skips_scroll_and_extraction(self):
        db = Mock()
        pause_event = Event()
        combo = ("北京", "101010100", "AI")

        def pause_during_load(target_id, *, timeout, stop_event):
            self.assertEqual(target_id, "search-target")
            self.assertIs(stop_event, pause_event)
            pause_event.set()
            return False

        with patch("bosshunter.scraper.jobs.get_db", return_value=db), \
             patch("bosshunter.scraper.jobs.new_tab", return_value="search-target"), \
             patch("bosshunter.scraper.jobs.wait_for_load", side_effect=pause_during_load), \
             patch("bosshunter.scraper.jobs.scroll") as scroll, \
             patch("bosshunter.scraper.jobs.evaluate") as evaluate, \
             patch("bosshunter.scraper.jobs.close_tab") as close_tab, \
             patch("bosshunter.scraper.jobs.time.sleep"):
            count, job_ids = _scrape_combo(
                {"profile": {}, "search": {}},
                combo,
                max_pages=1,
                limit=None,
                stop_event=pause_event,
            )

        self.assertEqual((count, job_ids), (0, set()))
        scroll.assert_not_called()
        evaluate.assert_not_called()
        close_tab.assert_called_once_with("search-target")
        db.close.assert_called_once_with()

    def test_page_throttle_returns_immediately_when_pause_is_requested(self):
        stop_event = Event()
        stop_event.set()

        with patch("bosshunter.throttle.time.sleep") as sleep:
            interrupted = PageThrottle(delay_min=10, delay_max=10).wait(stop_event)

        self.assertTrue(interrupted)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
