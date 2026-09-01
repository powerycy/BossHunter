import json
from threading import Event
from unittest import TestCase
from unittest.mock import Mock, patch

from bosshunter.collection.base import CollectionBlockedError, CollectorHooks
from bosshunter.collection.models import JobCandidate, PlatformCollectionRequest
from bosshunter.collection.orchestrator import normalize_collection_options
from bosshunter.collection.platforms.job51 import (
    API_PAGE_SIZE,
    HARD_MAX_PAGES,
    Job51Browser,
    Job51Collector,
    _analyze_api_response,
    _ApiRateLimiter,
    _extract_core_terms,
    _is_internship,
    _is_relevant_to_keyword,
    _parse_salary_range,
    _payload,
    _reason_code_for,
    _salary_within_range,
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
        self.assertGreaterEqual(len(snap["cities"]), 2)

    def test_fail_closed_for_unknown_city(self):
        self.assertEqual(get_51job_city_code("北京市"), "010000")
        self.assertEqual(get_51job_city_code("上海市"), "020000")
        self.assertIsNone(get_51job_city_code("广州"))

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


class ParseSalaryRangeTests(TestCase):
    """_parse_salary_range — 多格式薪资解析。"""

    def test_yearly_salary_converts_to_monthly_k(self):
        lo, hi = _parse_salary_range("25-38万/年")
        self.assertAlmostEqual(lo, 25 * 10000 / 12 / 1000, places=1)
        self.assertAlmostEqual(hi, 38 * 10000 / 12 / 1000, places=1)

    def test_cn_range_wan_to_wan(self):
        lo, hi = _parse_salary_range("1万-2万")
        self.assertEqual(lo, 10.0)
        self.assertEqual(hi, 20.0)

    def test_cn_range_qian_to_wan(self):
        lo, hi = _parse_salary_range("8千-1.2万")
        self.assertEqual(lo, 8.0)
        self.assertEqual(hi, 12.0)

    def test_cn_range_plain_wan(self):
        lo, hi = _parse_salary_range("1.5-2万")
        self.assertEqual(lo, 15.0)
        self.assertEqual(hi, 20.0)

    def test_single_wan(self):
        lo, hi = _parse_salary_range("3万")
        self.assertEqual(lo, 30.0)
        self.assertEqual(hi, 30.0)

    def test_single_qian(self):
        lo, hi = _parse_salary_range("5千")
        self.assertEqual(lo, 5.0)
        self.assertEqual(hi, 5.0)

    def test_k_range(self):
        lo, hi = _parse_salary_range("15-25K")
        self.assertEqual(lo, 15.0)
        self.assertEqual(hi, 25.0)

    def test_single_k(self):
        lo, hi = _parse_salary_range("20K")
        self.assertEqual(lo, 20.0)
        self.assertEqual(hi, 20.0)

    def test_unparseable_returns_none(self):
        self.assertIsNone(_parse_salary_range("面议"))
        self.assertIsNone(_parse_salary_range(""))
        self.assertIsNone(_parse_salary_range(None))

    def test_with_extra_suffix(self):
        lo, hi = _parse_salary_range("1.5-2万·13薪")
        self.assertEqual(lo, 15.0)
        self.assertEqual(hi, 20.0)


class ExtractCoreTermsTests(TestCase):
    """_extract_core_terms — 关键词词根提取。"""

    def test_strips_generic_role_words(self):
        cores = _extract_core_terms("AI产品经理")
        self.assertIn("AI产品", cores)

    def test_splits_on_separators(self):
        cores = _extract_core_terms("Python/后端开发")
        self.assertIn("Python", cores)
        self.assertIn("后端开发", cores)

    def test_all_generic_returns_empty(self):
        cores = _extract_core_terms("主管")
        self.assertEqual(cores, [])


class IsRelevantToKeywordTests(TestCase):
    """_is_relevant_to_keyword — 关键词相关性校验。"""

    def test_all_generic_keyword_passes_everything(self):
        self.assertTrue(_is_relevant_to_keyword("任意岗位", "jd", "主管"))

    def test_chinese_core_substring_match(self):
        self.assertTrue(_is_relevant_to_keyword("AI产品经理", "jd", "AI产品"))

    def test_ascii_core_word_boundary(self):
        self.assertTrue(_is_relevant_to_keyword("Python 开发", "jd", "Python"))
        self.assertFalse(_is_relevant_to_keyword("Pythonista 开发", "jd", "Python"))

    def test_no_match_returns_false(self):
        self.assertFalse(_is_relevant_to_keyword("财务总监", "jd", "AI产品"))


class IsInternshipTests(TestCase):
    """_is_internship — 实习岗位判定。"""

    def test_detects_internship_in_title(self):
        self.assertTrue(_is_internship("AI实习工程师", ""))

    def test_detects_intern_english(self):
        self.assertTrue(_is_internship("Software Intern", ""))

    def test_detects_guanpei(self):
        self.assertTrue(_is_internship("管培生", ""))

    def test_non_internship_returns_false(self):
        self.assertFalse(_is_internship("高级工程师", "3年"))


class SalaryWithinRangeTests(TestCase):
    """_salary_within_range — 薪资范围过滤。"""

    def test_no_filter_passes_all(self):
        self.assertTrue(_salary_within_range("面议", 0, 0))

    def test_unparseable_passes(self):
        self.assertTrue(_salary_within_range("面议", 10, 30))

    def test_below_min_rejected(self):
        self.assertFalse(_salary_within_range("5K", 10, 0))

    def test_above_max_rejected(self):
        self.assertFalse(_salary_within_range("50K", 0, 30))

    def test_within_range_passes(self):
        self.assertTrue(_salary_within_range("15-25K", 10, 30))


class ApiRateLimiterTests(TestCase):
    """_ApiRateLimiter — 自适应速率档位。"""

    def test_light_tier_defaults(self):
        limiter = _ApiRateLimiter(total_requests=10)
        self.assertEqual(limiter.per_min_limit, 30)
        self.assertEqual(limiter.gap_range, (2.0, 3.0))

    def test_medium_tier(self):
        limiter = _ApiRateLimiter(total_requests=100)
        self.assertEqual(limiter.per_min_limit, 20)
        self.assertEqual(limiter.gap_range, (3.0, 5.0))

    def test_heavy_tier(self):
        limiter = _ApiRateLimiter(total_requests=200)
        self.assertEqual(limiter.per_min_limit, 12)
        self.assertEqual(limiter.gap_range, (5.0, 8.0))

    def test_set_tier_updates_and_clears_no_burst(self):
        limiter = _ApiRateLimiter(total_requests=10, no_burst=True)
        limiter.set_tier(200)
        self.assertEqual(limiter.per_min_limit, 12)

    def test_wait_before_request_returns_true_with_stop_event(self):
        stop = Event()
        limiter = _ApiRateLimiter(total_requests=10, no_burst=True)
        self.assertTrue(limiter.wait_before_request(stop))



class PayloadTests(TestCase):
    """_payload — JSON 包装解析。"""

    def test_dict_passthrough(self):
        self.assertEqual(_payload({"a": 1}), {"a": 1})

    def test_valid_json_string(self):
        self.assertEqual(_payload('{"a": 1}'), {"a": 1})

    def test_invalid_json_string(self):
        self.assertEqual(_payload("not json"), {})

    def test_non_dict_returns_empty(self):
        self.assertEqual(_payload(42), {})
        self.assertEqual(_payload([1, 2]), {})
        self.assertEqual(_payload(None), {})


class WouldBeCollectedTests(TestCase):
    """_would_be_collected — 探针口径过滤。"""

    def _make_candidate(self, **overrides):
        defaults = dict(
            platform="51job", source_job_id="job-1", title="AI工程师",
            company="示例公司", city="上海", city_code="020000",
            jd="负责AI研发", salary="15-25K", experience="3年",
        )
        defaults.update(overrides)
        return JobCandidate(**defaults)

    def test_passes_with_no_filters(self):
        c = self._make_candidate()
        collector = Job51Collector()
        self.assertTrue(collector._would_be_collected(
            c, "AI", deal_breakers=[], jd_deal_breakers=[],
            blocked_companies=[], allow_internship=False,
            salary_min=0, salary_max=0,
        ))

    def test_rejects_deal_breaker_in_title(self):
        c = self._make_candidate(title="外包岗位")
        collector = Job51Collector()
        self.assertFalse(collector._would_be_collected(
            c, "AI", deal_breakers=["外包"], jd_deal_breakers=[],
            blocked_companies=[], allow_internship=False,
            salary_min=0, salary_max=0,
        ))

    def test_rejects_blocked_company(self):
        c = self._make_candidate(company="黑名单公司")
        collector = Job51Collector()
        self.assertFalse(collector._would_be_collected(
            c, "AI", deal_breakers=[], jd_deal_breakers=[],
            blocked_companies=["黑名单公司"], allow_internship=False,
            salary_min=0, salary_max=0,
        ))

    def test_rejects_jd_deal_breaker(self):
        c = self._make_candidate(jd="包含外包关键词")
        collector = Job51Collector()
        self.assertFalse(collector._would_be_collected(
            c, "AI", deal_breakers=[], jd_deal_breakers=["外包"],
            blocked_companies=[], allow_internship=False,
            salary_min=0, salary_max=0,
        ))

    def test_rejects_internship_when_disallowed(self):
        c = self._make_candidate(title="AI实习工程师")
        collector = Job51Collector()
        self.assertFalse(collector._would_be_collected(
            c, "AI", deal_breakers=[], jd_deal_breakers=[],
            blocked_companies=[], allow_internship=False,
            salary_min=0, salary_max=0,
        ))

    def test_rejects_salary_out_of_range(self):
        c = self._make_candidate(salary="5K")
        collector = Job51Collector()
        self.assertFalse(collector._would_be_collected(
            c, "AI", deal_breakers=[], jd_deal_breakers=[],
            blocked_companies=[], allow_internship=False,
            salary_min=10, salary_max=0,
        ))


class PassesCollectorFiltersTests(TestCase):
    """_passes_collector_filters — 正式入库增值过滤。"""

    def _make_candidate(self, **overrides):
        defaults = dict(
            platform="51job", source_job_id="job-1", title="AI工程师",
            company="示例公司", city="上海", city_code="020000",
            jd="负责AI研发", salary="15-25K", experience="3年",
        )
        defaults.update(overrides)
        return JobCandidate(**defaults)

    def test_passes_normal_job(self):
        c = self._make_candidate()
        collector = Job51Collector()
        self.assertTrue(collector._passes_collector_filters(
            c, "AI", allow_internship=False, salary_min=0, salary_max=0,
        ))

    def test_rejects_internship(self):
        c = self._make_candidate(title="实习工程师")
        collector = Job51Collector()
        self.assertFalse(collector._passes_collector_filters(
            c, "AI", allow_internship=False, salary_min=0, salary_max=0,
        ))

    def test_rejects_irrelevant_title(self):
        c = self._make_candidate(title="财务总监")
        collector = Job51Collector()
        self.assertFalse(collector._passes_collector_filters(
            c, "AI", allow_internship=False, salary_min=0, salary_max=0,
        ))

    def test_rejects_salary_out_of_range(self):
        c = self._make_candidate(salary="50K")
        collector = Job51Collector()
        self.assertFalse(collector._passes_collector_filters(
            c, "AI", allow_internship=False, salary_min=0, salary_max=30,
        ))


class FetchPageTests(TestCase):
    """_fetch_page — API 页面获取与分析。"""

    def test_none_evaluate_returns_l3_empty(self):
        collector = Job51Collector(browser=Job51Browser(evaluate=Mock(return_value=None)))
        result = collector._fetch_page("host", "kw", "020000", 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["level"], 3)
        self.assertEqual(result["signal"], "empty_result")

    def test_error_wrapper_returns_l3_fetch_error(self):
        raw = json.dumps({"error": "NetworkError"})
        collector = Job51Collector(browser=Job51Browser(evaluate=Mock(return_value=raw)))
        result = collector._fetch_page("host", "kw", "020000", 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["level"], 3)
        self.assertEqual(result["signal"], "fetch_error")

    def test_valid_response_analyzed(self):
        body = _ok_body([{"jobName": "AI"}], total=100)
        raw = json.dumps({"http_status": 200, "content_type": "application/json", "body": body})
        collector = Job51Collector(browser=Job51Browser(evaluate=Mock(return_value=raw)))
        result = collector._fetch_page("host", "kw", "020000", 1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["signal"], "ok")


class EnsureHostTabTests(TestCase):
    """_ensure_host_tab — 宿主页管理。"""

    def _make_request(self):
        return PlatformCollectionRequest(
            "51job", ["AI"], ["上海"], {"上海": "020000"}, max_pages=1,
        )

    def test_reuses_existing_alive_tab(self):
        evaluate_mock = Mock(return_value=json.dumps({"http_status": 200, "body": _ok_body([])}))
        browser = Job51Browser(
            get_page_targets=Mock(return_value=[{"url": "https://we.51job.com/pc/search", "targetId": "tab-1"}]),
            evaluate=evaluate_mock,
        )
        collector = Job51Collector(browser=browser, sleep=Mock())
        target, owned = collector._ensure_host_tab(self._make_request())
        self.assertEqual(target, "tab-1")
        self.assertFalse(owned)

    def test_opens_new_tab_when_no_existing(self):
        browser = Job51Browser(
            get_page_targets=Mock(return_value=[]),
            new_tab=Mock(return_value="new-tab"),
        )
        collector = Job51Collector(browser=browser, sleep=Mock())
        target, owned = collector._ensure_host_tab(self._make_request())
        self.assertEqual(target, "new-tab")
        self.assertTrue(owned)

    def test_raises_when_new_tab_fails(self):
        browser = Job51Browser(
            get_page_targets=Mock(return_value=[]),
            new_tab=Mock(return_value=None),
        )
        collector = Job51Collector(browser=browser, sleep=Mock())
        with self.assertRaises(CollectionBlockedError) as raised:
            collector._ensure_host_tab(self._make_request())
        self.assertEqual(raised.exception.code, "rate_limit")


class CollectEntryTests(TestCase):
    """collect — 入口前置校验路径。"""

    def _hooks(self):
        return CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _: True,
            on_candidate=lambda _: True,
            on_parse_failed=lambda _: None,
            on_event=lambda **_: None,
        )

    def test_no_valid_city_returns_failed(self):
        request = PlatformCollectionRequest("51job", ["AI"], ["未知城市"], {}, max_pages=1)
        collector = Job51Collector()
        result = collector.collect(request, self._hooks())
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason_code, "no_valid_city")

    def test_outside_send_window_returns_completed(self):
        request = PlatformCollectionRequest(
            "51job", ["AI"], ["上海"], {"上海": "020000"}, max_pages=1,
        )
        collector = Job51Collector(config={"throttle": {"send_windows": ["00:00-00:01"]}})
        with patch("bosshunter.collection.platforms.job51.SendWindowChecker.is_active", return_value=False):
            result = collector.collect(request, self._hooks())
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.reason_code, "outside_window")

    def test_day_off_returns_completed(self):
        request = PlatformCollectionRequest(
            "51job", ["AI"], ["上海"], {"上海": "020000"}, max_pages=1,
        )
        collector = Job51Collector(config={"throttle": {"day_off_probability": 1.0}})
        with (
            patch("bosshunter.collection.platforms.job51.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.job51.should_take_day_off", return_value=True),
        ):
            result = collector.collect(request, self._hooks())
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.reason_code, "day_off")

    def test_stop_event_during_loop_returns_stopped(self):
        stop = Event()
        stop.set()
        hooks = CollectorHooks(
            stop_event=stop,
            on_list_candidate=lambda _: True,
            on_candidate=lambda _: True,
            on_parse_failed=lambda _: None,
            on_event=lambda **_: None,
        )
        request = PlatformCollectionRequest(
            "51job", ["AI"], ["上海"], {"上海": "020000"}, max_pages=1,
        )
        collector = Job51Collector()
        with (
            patch("bosshunter.collection.platforms.job51.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.job51.should_take_day_off", return_value=False),
            patch.object(collector, "_ensure_host_tab", return_value=("host", True)),
        ):
            result = collector.collect(request, hooks)
        self.assertEqual(result.status, "stopped")
        self.assertEqual(result.reason_code, "user_stopped")


class ItemToCandidateEdgeTests(TestCase):
    """_item_to_candidate — 边界情况。"""

    def test_missing_job_id_returns_none(self):
        self.assertIsNone(Job51Collector._item_to_candidate(
            {"jobName": "AI"}, "上海", "020000", "AI",
        ))

    def test_missing_job_name_returns_none(self):
        self.assertIsNone(Job51Collector._item_to_candidate(
            {"jobId": "123"}, "上海", "020000", "AI",
        ))

    def test_falls_back_to_city_when_no_area_string(self):
        cand = Job51Collector._item_to_candidate(
            {"jobId": "1", "jobName": "AI"}, "上海", "020000", "AI",
        )
        self.assertEqual(cand.city, "上海")

    def test_extracts_company_from_fullCompanyName(self):
        cand = Job51Collector._item_to_candidate(
            {"jobId": "1", "jobName": "AI", "fullCompanyName": "全称公司"},
            "上海", "020000", "AI",
        )
        self.assertEqual(cand.company, "全称公司")

    def test_falls_back_to_companyName(self):
        cand = Job51Collector._item_to_candidate(
            {"jobId": "1", "jobName": "AI", "companyName": "简称公司"},
            "上海", "020000", "AI",
        )
        self.assertEqual(cand.company, "简称公司")


class ResumeTtlHoursTests(TestCase):
    """_resume_ttl_hours — 断点续采有效期配置解析。"""

    def test_default_24h(self):
        collector = Job51Collector()
        self.assertEqual(collector._resume_ttl_hours(), 24)

    def test_from_platforms_config(self):
        collector = Job51Collector(config={"platforms": {"51job": {"search": {"resume_ttl_hours": 48}}}})
        self.assertEqual(collector._resume_ttl_hours(), 48)

    def test_from_legacy_search_config(self):
        collector = Job51Collector(config={"search": {"resume_ttl_hours": 72}})
        self.assertEqual(collector._resume_ttl_hours(), 72)

    def test_invalid_value_falls_back_to_24(self):
        collector = Job51Collector(config={"search": {"resume_ttl_hours": "abc"}})
        self.assertEqual(collector._resume_ttl_hours(), 24)

    def test_clamped_to_max_720(self):
        collector = Job51Collector(config={"search": {"resume_ttl_hours": 9999}})
        self.assertEqual(collector._resume_ttl_hours(), 720)

    def test_clamped_to_min_1(self):
        collector = Job51Collector(config={"search": {"resume_ttl_hours": -5}})
        self.assertEqual(collector._resume_ttl_hours(), 1)


class WaitOrStopTests(TestCase):
    """_wait_or_stop — 等待/停止逻辑。"""

    def test_sleeps_when_no_stop_event(self):
        from bosshunter.collection.platforms.job51 import _wait_or_stop
        slept = []
        with patch("bosshunter.collection.platforms.job51.time.sleep", side_effect=slept.append):
            result = _wait_or_stop(None, 0.01)
        self.assertFalse(result)
        self.assertEqual(slept, [0.01])

    def test_returns_true_when_event_set(self):
        from bosshunter.collection.platforms.job51 import _wait_or_stop
        stop = Event()
        stop.set()
        self.assertTrue(_wait_or_stop(stop, 10))

    def test_returns_false_when_event_not_set(self):
        from bosshunter.collection.platforms.job51 import _wait_or_stop
        stop = Event()
        self.assertFalse(_wait_or_stop(stop, 0.01))


class CollectSalaryParseErrorTests(TestCase):
    """collect — 薪资配置解析异常降级为 0。"""

    def _hooks(self):
        return CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _: True,
            on_candidate=lambda _: True,
            on_parse_failed=lambda _: None,
            on_event=lambda **_: None,
        )

    def test_invalid_salary_config_does_not_crash(self):
        request = PlatformCollectionRequest(
            "51job", ["AI"], ["上海"], {"上海": "020000"}, max_pages=1,
        )
        collector = Job51Collector(config={"profile": {"salary_min": "abc", "salary_max": "xyz"}})
        with (
            patch("bosshunter.collection.platforms.job51.SendWindowChecker.is_active", return_value=True),
            patch("bosshunter.collection.platforms.job51.should_take_day_off", return_value=False),
            patch.object(collector, "_ensure_host_tab", return_value=("host", True)),
            patch.object(collector, "_collect_keyword"),
        ):
            result = collector.collect(request, self._hooks())
        self.assertEqual(result.status, "completed")
