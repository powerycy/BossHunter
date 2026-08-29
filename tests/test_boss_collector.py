import json
from urllib.parse import parse_qs
from unittest import TestCase
from unittest.mock import MagicMock

from bosshunter.collection.base import CollectorHooks
from bosshunter.collection.models import JobCandidate, PlatformCollectionRequest
from bosshunter.collection.platforms.boss import (
    JS_DETECT_COLLECTION_RISK,
    JS_EXTRACT_DETAIL,
    JS_EXTRACT_LIST,
    SEARCH_URL,
    BossBrowser,
    BossCollector,
    build_boss_filter_query,
    generate_boss_job_id,
    normalize_boss_search_filters,
)


class BossCollectorUnitTests(TestCase):
    def test_generate_boss_job_id_from_detail_url(self):
        url = "https://www.zhipin.com/job_detail/abc123.html"
        self.assertEqual(generate_boss_job_id(url), "abc123")

    def test_generate_boss_job_id_fallback_to_hash(self):
        url = "https://example.com/some/path"
        job_id = generate_boss_job_id(url)
        self.assertEqual(len(job_id), 16)

    def test_resolve_city_code_from_request(self):
        request = PlatformCollectionRequest(
            "boss", ["AI"], ["北京"], {"北京": "101010100"}, max_pages=1,
        )
        self.assertEqual(BossCollector.resolve_city_code("北京", request), "101010100")

    def test_resolve_city_code_missing_returns_none(self):
        request = PlatformCollectionRequest(
            "boss", ["AI"], ["未知城市"], {}, max_pages=1,
        )
        self.assertIsNone(BossCollector.resolve_city_code("未知城市", request))

    def test_search_url_format(self):
        self.assertIn("zhipin.com", SEARCH_URL)
        self.assertIn("{keyword}", SEARCH_URL)
        self.assertIn("{city_code}", SEARCH_URL)

    def test_boss_filters_are_encoded_from_known_options(self):
        query = parse_qs(build_boss_filter_query({
            "job_type": ["全职"],
            "experience": ["应届生", "1-3年"],
            "degree": ["本科"],
            "scale": ["100-499人"],
            "salary": ["10-20K"],
            "industry": ["100001", "100002"],
        }))

        self.assertEqual(query["jobType"], ["0"])
        self.assertEqual(query["experience"], ["102,104"])
        self.assertEqual(query["degree"], ["203"])
        self.assertEqual(query["scale"], ["303"])
        self.assertEqual(query["salary"], ["405"])
        self.assertEqual(query["industry"], ["100001,100002"])

    def test_boss_filters_reject_unknown_labels_and_query_injection(self):
        normalized = normalize_boss_search_filters({
            "experience": ["1-3年", "任意经验"],
            "industry": ["100001&sortType=2", "200002"],
            "unexpected": ["value"],
        })

        self.assertEqual(normalized, {
            "experience": ["1-3年"],
            "industry": ["200002"],
        })
        self.assertNotIn("sortType", build_boss_filter_query(normalized))

    def test_list_script_targets_job_card_wrap(self):
        self.assertIn(".job-card-wrap", JS_EXTRACT_LIST)
        self.assertIn(".job-name", JS_EXTRACT_LIST)
        self.assertIn(".job-salary", JS_EXTRACT_LIST)

    def test_detail_script_targets_jd(self):
        self.assertIn(".job-sec-text", JS_EXTRACT_DETAIL)
        self.assertIn(".info-primary", JS_EXTRACT_DETAIL)

    def test_risk_detection_covers_captcha_and_block(self):
        self.assertIn("captcha", JS_DETECT_COLLECTION_RISK)
        self.assertIn("blocked", JS_DETECT_COLLECTION_RISK)
        self.assertIn("rate_limit", JS_DETECT_COLLECTION_RISK)
        self.assertIn("login_required", JS_DETECT_COLLECTION_RISK)


class BossCollectorCollectionTests(TestCase):
    def _make_browser(self, list_jobs=None, detail=None, risk=None):
        if list_jobs is None:
            list_jobs = []
        if detail is None:
            detail = {}
        risk_raw = json.dumps({"risk": risk}) if risk else json.dumps({"risk": None})

        def evaluate(_target, script):
            if script == JS_DETECT_COLLECTION_RISK:
                return risk_raw
            if script == JS_EXTRACT_LIST:
                return json.dumps(list_jobs)
            if script == JS_EXTRACT_DETAIL:
                return json.dumps(detail)
            return "{}"

        return BossBrowser(
            new_tab=lambda url, **_kw: "tab-1",
            close_tab=lambda _t: True,
            evaluate=evaluate,
            navigate=lambda _t, _u: True,
            scroll=lambda *_a, **_kw: True,
            wait_for_load=lambda *_a, **_kw: True,
        )

    def _make_hooks(self):
        collected = []
        return CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _c: True,
            on_candidate=lambda c: collected.append(c) or True,
            on_parse_failed=lambda _r: None,
            on_event=lambda **_kw: None,
        ), collected

    def _make_throttle(self):
        throttle = MagicMock()
        throttle.wait.return_value = False
        return throttle

    def test_no_valid_city_returns_shortage(self):
        browser = self._make_browser()
        hooks, _ = self._make_hooks()
        result = BossCollector(
            browser=browser,
            throttle_factory=lambda **_kw: self._make_throttle(),
        ).collect(
            PlatformCollectionRequest("boss", ["AI"], ["未知城市"], {}, max_pages=1),
            hooks,
        )
        self.assertEqual(result.status, "completed_with_shortage")
        self.assertEqual(result.reason_code, "no_valid_city")

    def test_captcha_risk_stops_collection(self):
        browser = self._make_browser(risk="captcha")
        hooks, _ = self._make_hooks()
        result = BossCollector(
            browser=browser,
            throttle_factory=lambda **_kw: self._make_throttle(),
            randint=lambda _a, _b: 5,
        ).collect(
            PlatformCollectionRequest("boss", ["AI"], ["北京"], {"北京": "101010100"}, max_pages=1),
            hooks,
        )
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason_code, "captcha")

    def test_rate_limit_risk_stops_collection(self):
        browser = self._make_browser(risk="rate_limit")
        hooks, _ = self._make_hooks()
        result = BossCollector(
            browser=browser,
            throttle_factory=lambda **_kw: self._make_throttle(),
            randint=lambda _a, _b: 7,
        ).collect(
            PlatformCollectionRequest("boss", ["AI"], ["北京"], {"北京": "101010100"}, max_pages=1),
            hooks,
        )
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason_code, "rate_limit")

    def test_empty_list_completes_gracefully(self):
        browser = self._make_browser(list_jobs=[])
        hooks, _ = self._make_hooks()
        result = BossCollector(
            browser=browser,
            throttle_factory=lambda **_kw: self._make_throttle(),
        ).collect(
            PlatformCollectionRequest("boss", ["AI"], ["北京"], {"北京": "101010100"}, max_pages=1),
            hooks,
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.reason_code, "search_exhausted")

    def test_collection_extracts_candidates(self):
        list_jobs = [
            {
                "title": "AI 工程师",
                "salary": "25-40K",
                "company": "示例科技",
                "experience": "3-5年",
                "education": "本科",
                "url": "/job_detail/abc123.html",
            },
        ]
        detail = {
            "title": "AI 工程师",
            "salary": "25-40K",
            "company": "示例科技",
            "experience": "3-5年",
            "education": "本科",
            "jd": "负责 AI 平台开发与维护。",
            "recruitment_type": "experienced",
            "hr_name": "HR",
            "hr_title": "招聘经理",
        }
        browser = self._make_browser(list_jobs=list_jobs, detail=detail)
        hooks, collected = self._make_hooks()
        result = BossCollector(
            browser=browser,
            throttle_factory=lambda **_kw: self._make_throttle(),
        ).collect(
            PlatformCollectionRequest("boss", ["AI"], ["北京"], {"北京": "101010100"}, max_pages=1),
            hooks,
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.reason_code, "search_exhausted")
        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0].title, "AI 工程师")
        self.assertEqual(collected[0].company, "示例科技")
        self.assertIn("AI 平台开发", collected[0].jd)
        self.assertEqual(collected[0].source_job_id, "abc123")

    def test_callback_stop_ends_collection(self):
        list_jobs = [
            {
                "title": "AI 工程师",
                "salary": "25-40K",
                "company": "示例科技",
                "url": "/job_detail/abc123.html",
            },
        ]
        detail = {
            "title": "AI 工程师",
            "company": "示例科技",
            "jd": "负责 AI 开发。",
        }
        browser = self._make_browser(list_jobs=list_jobs, detail=detail)
        collected = []
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _c: True,
            on_candidate=lambda c: collected.append(c) or False,
            on_parse_failed=lambda _r: None,
            on_event=lambda **_kw: None,
        )
        result = BossCollector(
            browser=browser,
            throttle_factory=lambda **_kw: self._make_throttle(),
        ).collect(
            PlatformCollectionRequest("boss", ["AI"], ["北京"], {"北京": "101010100"}, max_pages=1),
            hooks,
        )
        self.assertEqual(result.reason_code, "callback_stopped")
        self.assertEqual(len(collected), 1)

    def test_list_candidate_extraction(self):
        raw = {
            "title": "Python 开发",
            "salary": "20-30K",
            "company": "测试公司",
            "experience": "1-3年",
            "education": "本科",
            "url": "/job_detail/xyz789.html",
        }
        candidate = BossCollector._list_candidate(raw, "北京", "101010100", "Python")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.platform, "boss")
        self.assertEqual(candidate.title, "Python 开发")
        self.assertEqual(candidate.company, "测试公司")
        self.assertEqual(candidate.source_job_id, "xyz789")
        self.assertEqual(candidate.city, "北京")

    def test_list_candidate_rejects_missing_url(self):
        raw = {"title": "Python 开发", "company": "测试公司"}
        candidate = BossCollector._list_candidate(raw, "北京", "101010100", "Python")
        self.assertIsNone(candidate)

    def test_merge_detail_combines_fields(self):
        base = JobCandidate(
            platform="boss",
            source_job_id="abc123",
            title="AI 工程师",
            company="示例科技",
            url="/job_detail/abc123.html",
            source_keyword="AI",
        )
        detail = {
            "title": "AI 资深工程师",
            "salary": "30-50K",
            "company": "示例科技集团",
            "jd": "负责 AI 架构设计。",
            "hr_name": "张经理",
            "hr_title": "HRD",
            "recruitment_type": "experienced",
        }
        merged = BossCollector._merge_detail(base, detail, "https://www.zhipin.com/job_detail/abc123.html")
        self.assertEqual(merged.title, "AI 资深工程师")
        self.assertEqual(merged.salary, "30-50K")
        self.assertIn("AI 架构", merged.jd)
        self.assertEqual(merged.hr_name, "张经理")
        self.assertEqual(merged.url, "https://www.zhipin.com/job_detail/abc123.html")

    def test_merge_detail_falls_back_to_candidate(self):
        base = JobCandidate(
            platform="boss",
            source_job_id="abc123",
            title="AI 工程师",
            company="示例科技",
            url="/job_detail/abc123.html",
            source_keyword="AI",
        )
        detail = {"jd": "负责开发。"}
        merged = BossCollector._merge_detail(base, detail, "https://www.zhipin.com/job_detail/abc123.html")
        self.assertEqual(merged.title, "AI 工程师")
        self.assertEqual(merged.company, "示例科技")
