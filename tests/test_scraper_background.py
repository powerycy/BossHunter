import json
import unittest
from unittest.mock import Mock, call, patch

from bosshunter.scraper.jobs import scrape_jobs


class ScraperBackgroundTests(unittest.TestCase):
    def test_search_and_detail_pages_open_in_background(self):
        db = Mock()
        progress = Mock()
        progress.add_task.return_value = "task-1"
        progress_context = Mock()
        progress_context.__enter__ = Mock(return_value=progress)
        progress_context.__exit__ = Mock(return_value=False)

        jobs = [{
            "title": "AI Product Manager",
            "company": "Example",
            "salary": "20-30K",
            "experience": "3-5 years",
            "url": "/job_detail/background-job.html",
        }]
        detail = {
            "title": "AI Product Manager",
            "company": "Example",
            "salary": "20-30K",
            "experience": "3-5 years",
            "jd": "Build AI products",
        }

        config = {
            "profile": {"target_cities": ["北京"], "deal_breakers": []},
            "search": {"max_pages": 1},
        }

        with patch("bosshunter.scraper.jobs.get_db", return_value=db), \
             patch("bosshunter.scraper.jobs.Progress", return_value=progress_context), \
             patch("bosshunter.scraper.jobs.PageThrottle") as throttle_cls, \
             patch(
                 "bosshunter.scraper.jobs.new_tab",
                 side_effect=["search-target", "detail-target"],
             ) as new_tab, \
             patch(
                 "bosshunter.scraper.jobs.evaluate",
                 side_effect=[json.dumps(jobs), json.dumps(detail)],
             ), \
             patch("bosshunter.scraper.jobs.wait_for_load"), \
             patch("bosshunter.scraper.jobs.scroll"), \
             patch("bosshunter.scraper.jobs.close_tab"), \
             patch("bosshunter.scraper.jobs.job_exists", return_value=False), \
             patch("bosshunter.scraper.jobs.matching_deal_breaker", return_value=False), \
             patch("bosshunter.scraper.jobs.insert_job"), \
             patch("bosshunter.scraper.jobs.time.sleep"):
            throttle_cls.return_value.wait.return_value = None
            count = scrape_jobs(config, ["AI"])

        self.assertEqual(count, 1)
        self.assertEqual(
            new_tab.call_args_list,
            [
                call(
                    "https://www.zhipin.com/web/geek/job?query=AI&city=101010100",
                    background=True,
                ),
                call(
                    "https://www.zhipin.com/job_detail/background-job.html",
                    background=True,
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
