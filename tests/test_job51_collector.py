import json
from unittest import TestCase
from unittest.mock import Mock, patch

from bosshunter.collection.base import CollectionBlockedError, CollectorHooks
from bosshunter.collection.models import PlatformCollectionRequest
from bosshunter.collection.orchestrator import normalize_collection_options
from bosshunter.collection.platforms.job51 import (
    API_PAGE_SIZE,
    HARD_MAX_PAGES,
    Job51Browser,
    Job51Collector,
    _analyze_api_response,
    _reason_code_for,
    get_51job_city_code,
    load_51job_city_snapshot,
)
from bosshunter.collection.text import clean_job_description


def _ok_body(items: list, total: int | None = None) -> str:
    """Build a valid 51job API response body."""
    return json.dumps({
        "status": "1",
        "resultbody": {
            "job": {
                "items": items,
                "totalCount": total if total is not None else len(items),
            }
        },
    }, ensure_ascii=False)


class AnalyzeApiResponseTests(TestCase):
    """L0-L3 风控分级 — PR #81 核心路径。"""

    def test_l0_normal_response_with_items(self):
        body = _ok_body([{"jobName": "Python"}], total=100)
        r = _analyze_api_response(200, "application/json", body)
        self.assertTrue(r["ok"])
        self.assertEqual(r["level"], 0)
        self.assertEqual(r["signal"], "ok")
        self.assertEqual(len(r["jobs"]), 1)
        self.assertEqual(r["total"], 100)

    def test_l3_http_error(self):
        r = _analyze_api_response(403, "text/html", "")
        self.assertFalse(r["ok"])
        self.assertEqual(r["level"], 3)
        self.assertEqual(r["signal"], "http_error")

    def test_l3_non_json_with_captcha_hint(self):
        r = _analyze_api_response(200, "text/html", "<html>请完成验证</html>")
        self.assertFalse(r["ok"])
        self.assertEqual(r["level"], 3)
        self.assertEqual(r["signal"], "non_json")
        self.assertIn("验证", r["note"])

    def test_l3_non_json_with_login_hint(self):
        r = _analyze_api_response(200, "text/html", '<html>请登录</html>')
        self.assertFalse(r["ok"])
        self.assertEqual(r["level"], 3)
        self.assertEqual(r["signal"], "non_json")
        self.assertIn("登录", r["note"])

    def test_l3_non_json_plain_html(self):
        r = _analyze_api_response(200, "text/html", "<html>Not Found</html>")
        self.assertFalse(r["ok"])
        self.assertEqual(r["level"], 3)
        self.assertEqual(r["signal"], "non_json")

    def test_l1_json_parse_error(self):
        r = _analyze_api_response(200, "application/json", "{broken json")
        self.assertFalse(r["ok"])
        self.assertEqual(r["level"], 1)
        self.assertEqual(r["signal"], "parse_error")

    def test_l3_hard_risk_status_not_1_with_verify(self):
        body = json.dumps({"status": "0", "message": "请完成滑块验证"}, ensure_ascii=False)
        r = _analyze_api_response(200, "application/json", body)
        self.assertFalse(r["ok"])
        self.assertEqual(r["level"], 3)
        self.assertEqual(r["signal"], "hard_risk")

    def test_l2_api_limited_status_not_1_without_hard_signals(self):
        body = json.dumps({"status": "0", "message": "rate limited"}, ensure_ascii=False)
        r = _analyze_api_response(200, "application/json", body)
        self.assertFalse(r["ok"])
        self.assertEqual(r["level"], 2)
        self.assertEqual(r["signal"], "api_limited")

    def test_l2_empty_items_with_total(self):
        body = _ok_body([], total=50)
        r = _analyze_api_response(200, "application/json", body)
        self.assertFalse(r["ok"])
        self.assertEqual(r["level"], 2)
        self.assertEqual(r["signal"], "empty_items")
        self.assertEqual(r["total"], 50)

    def test_l0_empty_search_without_total(self):
        r = _analyze_api_response(200, "application/json", _ok_body([], total=0))
        self.assertTrue(r["ok"])
        self.assertEqual(r["signal"], "no_results")
        self.assertEqual(r["jobs"], [])

    def test_l0_total_defaults_to_items_count(self):
        body = json.dumps({
            "status": "1",
            "resultbody": {"job": {"items": [{"jobName": "a"}, {"jobName": "b"}]}},
        }, ensure_ascii=False)
        r = _analyze_api_response(200, "application/json", body)
        self.assertTrue(r["ok"])
        self.assertEqual(r["total"], 2)

    def test_reason_code_mapping(self):
        parse_err = {"signal": "parse_error"}
        self.assertEqual(_reason_code_for(parse_err), "selector_changed")
        other = {"signal": "http_error"}
        self.assertEqual(_reason_code_for(other), "rate_limit")


class RealLastPageTests(TestCase):
    """主动末页判定 — 终止条件。"""

    def test_returns_fallback_when_total_zero(self):
        analysis = {"total": 0}
        self.assertEqual(Job51Collector._real_last_page(analysis, 50), 50)

    def test_returns_fallback_when_total_missing(self):
        analysis = {}
        self.assertEqual(Job51Collector._real_last_page(analysis, 50), 50)

    def test_calculates_exact_pages(self):
        analysis = {"total": 600}
        expected = (600 + API_PAGE_SIZE - 1) // API_PAGE_SIZE
        self.assertEqual(Job51Collector._real_last_page(analysis, 50), expected)

    def test_caps_at_fallback(self):
        analysis = {"total": 2000}
        raw_pages = (2000 + API_PAGE_SIZE - 1) // API_PAGE_SIZE
        self.assertGreater(raw_pages, HARD_MAX_PAGES)
        self.assertEqual(Job51Collector._real_last_page(analysis, HARD_MAX_PAGES), HARD_MAX_PAGES)

    def test_minimum_one_page(self):
        analysis = {"total": 1}
        self.assertEqual(Job51Collector._real_last_page(analysis, 50), 1)

    def test_partial_last_page_rounds_up(self):
        analysis = {"total": 21}
        self.assertEqual(Job51Collector._real_last_page(analysis, 50), 2)


class PlanProbePagesTests(TestCase):
    """采样策略 — 分布探针。"""

    def test_empty_when_max_less_than_start(self):
        self.assertEqual(Job51Collector._plan_probe_pages(5, 3), [])

    def test_all_pages_when_range_le_two(self):
        self.assertEqual(Job51Collector._plan_probe_pages(1, 1), [1])
        self.assertEqual(Job51Collector._plan_probe_pages(1, 2), [1, 2])

    def test_start_page_always_included(self):
        pages = Job51Collector._plan_probe_pages(1, 20)
        self.assertIn(1, pages)

    def test_max_page_included(self):
        pages = Job51Collector._plan_probe_pages(1, 20)
        self.assertIn(20, pages)

    def test_no_adjacent_pages_in_front_section(self):
        for _ in range(20):
            pages = sorted(Job51Collector._plan_probe_pages(1, 30))
            front = [p for p in pages if p <= 10]
            for i in range(len(front) - 1):
                self.assertGreater(front[i + 1] - front[i], 1,
                                   f"Adjacent front pages: {front}")

    def test_all_pages_within_bounds(self):
        for _ in range(20):
            pages = Job51Collector._plan_probe_pages(3, 25)
            for p in pages:
                self.assertGreaterEqual(p, 3)
                self.assertLessEqual(p, 25)

    def test_front_dense_more_than_rear(self):
        for _ in range(10):
            pages = Job51Collector._plan_probe_pages(1, 40)
            front_count = sum(1 for p in pages if p <= 10)
            rear_count = sum(1 for p in pages if p > 10)
            self.assertGreaterEqual(front_count, rear_count)

    def test_no_duplicates(self):
        pages = Job51Collector._plan_probe_pages(1, 30)
        self.assertEqual(len(pages), len(set(pages)))


class Job51CitySnapshotTests(TestCase):
    """城市编码 fail-closed — 保持原有约束。"""

    def test_city_snapshot_loads(self):
        snap = load_51job_city_snapshot()
        self.assertEqual(snap["schema"], "bosshunter.51job_cities.v1")
        self.assertGreaterEqual(len(snap["cities"]), 30)

    def test_fail_closed_for_unknown_city(self):
        self.assertEqual(get_51job_city_code("北京市"), "010000")
        self.assertEqual(get_51job_city_code("上海市"), "020000")
        self.assertIsNone(get_51job_city_code("昆明"))  # 昆明未收录，应返回 None

    def test_option_defaults_are_fail_closed(self):
        options = normalize_collection_options({}, {
            "platform_order": ["51job"],
            "platforms": {"51job": {"keywords": ["AI 产品"], "cities": ["上海"]}},
        })
        search = options["platforms"]["51job"]
        self.assertEqual(search["city_codes"], {"上海": "020000"})
        self.assertEqual(search["max_pages"], 1)


class CollectionFlowSafetyTests(TestCase):
    def setUp(self):
        self.hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda _candidate: True,
            on_parse_failed=lambda _reason: None,
            on_event=lambda **_kwargs: None,
        )
        self.common_args = {
            "city": "上海",
            "area": "020000",
            "kw": "AI",
            "deal_breakers": [],
            "jd_deal_breakers": [],
            "blocked_companies": [],
            "allow_internship": False,
            "salary_min": 0.0,
            "salary_max": 0.0,
        }

    def test_api_item_keeps_51job_platform_identity(self):
        candidate = Job51Collector._item_to_candidate(
            {
                "jobId": "job-51",
                "jobName": "AI 产品经理",
                "jobDescribe": "负责 AI 产品规划",
                "fullCompanyName": "示例公司",
                "jobAreaString": "上海·浦东新区",
                "jobHref": "https://jobs.51job.com/job-51.html",
            },
            "上海",
            "020000",
            "AI 产品",
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.platform, "51job")
        self.assertEqual(candidate.storage_id, "51job:job-51")
        self.assertEqual(candidate.city, "上海")

    def test_probe_risk_signal_stops_without_marking_keyword_complete(self):
        collector = Job51Collector(safety_conn=object())
        limiter = Mock()
        limiter.wait_before_request.return_value = True
        blocked = {
            "ok": False,
            "level": 3,
            "signal": "hard_risk",
            "note": "请完成滑块验证",
            "jobs": [],
            "total": 0,
        }

        with (
            patch("bosshunter.collection.platforms.job51.get_page_progress", return_value=0),
            patch.object(Job51Collector, "_plan_probe_pages", return_value=[1]),
            patch.object(collector, "_fetch_page", return_value=blocked),
            patch("bosshunter.collection.platforms.job51.mark_combo_collected") as mark_complete,
        ):
            with self.assertRaises(CollectionBlockedError) as raised:
                collector._collect_keyword(
                    PlatformCollectionRequest("51job", ["AI"], ["上海"], {"上海": "020000"}, max_pages=1),
                    self.hooks,
                    "host-tab",
                    limiter,
                    max_pages=1,
                    **self.common_args,
                )

        self.assertEqual(raised.exception.code, "rate_limit")
        mark_complete.assert_not_called()

    def test_hot_pages_are_checkpointed_in_ascending_order(self):
        collector = Job51Collector(safety_conn=object())
        limiter = Mock()
        limiter.wait_before_request.return_value = True
        jobs = [
            {
                "jobId": f"job-{index}",
                "jobName": "AI 工程师",
                "jobDescribe": "负责 AI 平台研发",
                "fullCompanyName": "示例公司",
            }
            for index in range(API_PAGE_SIZE)
        ]
        analysis = {
            "ok": True,
            "level": 0,
            "signal": "ok",
            "note": "正常",
            "jobs": jobs,
            "total": API_PAGE_SIZE * 5,
        }
        fetched_pages = []
        checkpoints = []

        def fetch_page(_host, _keyword, _area, page):
            fetched_pages.append(page)
            return analysis

        with (
            patch("bosshunter.collection.platforms.job51.get_page_progress", return_value=0),
            patch("bosshunter.collection.platforms.job51.upsert_page_progress",
                  side_effect=lambda _conn, _source, _city, _keyword, page: checkpoints.append(page)),
            patch("bosshunter.collection.platforms.job51.mark_combo_collected") as mark_complete,
            patch("bosshunter.collection.platforms.job51.delete_page_progress"),
            patch("bosshunter.collection.platforms.job51._wait_or_stop", return_value=False),
            patch.object(Job51Collector, "_plan_probe_pages", return_value=[3]),
            patch.object(collector, "_fetch_page", side_effect=fetch_page),
            patch.object(collector, "_would_be_collected", return_value=True),
        ):
            collector._collect_keyword(
                PlatformCollectionRequest("51job", ["AI"], ["上海"], {"上海": "020000"}, max_pages=5),
                self.hooks,
                "host-tab",
                limiter,
                max_pages=5,
                **self.common_args,
            )

        self.assertEqual(fetched_pages, [3, 1, 2, 4, 5])
        self.assertEqual(checkpoints, [1, 2, 3, 4, 5])
        mark_complete.assert_called_once()

    def test_collect_only_closes_temporary_host_tab(self):
        request = PlatformCollectionRequest(
            "51job", ["AI"], ["上海"], {"上海": "020000"}, max_pages=1,
        )

        for owned in (False, True):
            with self.subTest(owned=owned):
                close_tab = Mock(return_value=True)
                collector = Job51Collector(browser=Job51Browser(close_tab=close_tab))
                with (
                    patch("bosshunter.collection.platforms.job51.SendWindowChecker.is_active", return_value=True),
                    patch("bosshunter.collection.platforms.job51.should_take_day_off", return_value=False),
                    patch.object(collector, "_ensure_host_tab", return_value=("host-tab", owned)),
                    patch.object(collector, "_collect_keyword"),
                ):
                    result = collector.collect(request, self.hooks)

                self.assertEqual(result.status, "completed")
                self.assertEqual(close_tab.call_count, int(owned))


class JobDescriptionCleanupTests(TestCase):
    def test_known_platform_source_noise_is_removed(self):
        dirty = "[岗位kanzhun职责]1.公司业务后台开发 来自BOSS直聘 2.掌握 SQL"
        self.assertEqual(clean_job_description(dirty), "1.公司业务后台开发 2.掌握 SQL")
