import json
from unittest import TestCase

from bosshunter.collection.base import CollectorHooks
from bosshunter.collection.models import PlatformCollectionRequest
from bosshunter.collection.orchestrator import normalize_collection_options
from bosshunter.collection.platforms.liepin import (
    JS_EXTRACT_DETAIL,
    JS_EXTRACT_LIST,
    LiepinBrowser,
    LiepinCollector,
    get_liepin_city_code,
)
from bosshunter.collection.text import clean_job_description


class LiepinCollectorTests(TestCase):
    def test_list_script_uses_real_detail_url_and_liepin_domain(self):
        self.assertIn('a[href*="/job/"]', JS_EXTRACT_LIST)
        self.assertIn("url: jobUrl", JS_EXTRACT_LIST)
        self.assertIn("liepin.com/job/", JS_EXTRACT_LIST)

    def test_detail_script_targets_job_description(self):
        self.assertIn(".job-intro-container .content", JS_EXTRACT_DETAIL)
        self.assertIn(".job-description", JS_EXTRACT_DETAIL)

    def test_city_and_option_defaults_are_fail_closed(self):
        self.assertEqual(get_liepin_city_code("北京市"), "010")
        self.assertEqual(get_liepin_city_code("上海市"), "020")
        self.assertIsNone(get_liepin_city_code("广州"))
        options = normalize_collection_options({}, {
            "platform_order": ["liepin"],
            "platforms": {"liepin": {"keywords": ["AI 产品"], "cities": ["上海"]}},
        })
        search = options["platforms"]["liepin"]
        self.assertEqual(search["city_codes"], {"上海": "020"})
        self.assertEqual(search["max_pages"], 1)
        self.assertNotIn("target_count", search)

    def test_beijing_search_uses_verified_liepin_city_code(self):
        request = PlatformCollectionRequest(
            "liepin",
            ["AI 产品"],
            ["北京"],
            {"北京": "010"},
            max_pages=1,
        )

        url = LiepinCollector.build_search_url(request, "北京", "AI 产品")

        self.assertIn("dqs=010", url)
        self.assertIn("key=AI%20%E4%BA%A7%E5%93%81", url)

    def test_collection_uses_platform_identity_and_rate_limit(self):
        list_payload = json.dumps({"status": "ready", "jobs": [
            {
                "source_job_id": "job-1",
                "title": "AI 产品经理",
                "company": "示例公司",
                "city": "上海",
                "url": "https://www.liepin.com/job/1001.shtml",
            },
            {
                "source_job_id": "job-2",
                "title": "AI 产品运营",
                "company": "示例公司",
                "city": "上海",
                "url": "https://www.liepin.com/job/1002.shtml",
            },
        ]}, ensure_ascii=False)
        detail_payload = json.dumps({
            "status": "ready",
            "title": "AI 产品",
            "company": "示例公司",
            "city": "上海",
            "jd": "[岗位kanzhun职责]负责需求分析，来自BOSS直聘要求会 SQL。",
        }, ensure_ascii=False)
        sleeps: list[float] = []

        def evaluate(_target, script):
            return list_payload if ".job-info" in script else detail_payload

        browser = LiepinBrowser(
            new_tab=lambda url, **_kwargs: url,
            close_tab=lambda _target: True,
            evaluate=evaluate,
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )
        collected = []
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda candidate: collected.append(candidate) or len(collected) < 2,
            on_parse_failed=lambda reason: self.fail(reason),
            on_event=lambda **_kwargs: None,
        )
        result = LiepinCollector(
            browser=browser,
            sleep=sleeps.append,
            uniform=lambda _low, _high: 13.0,
        ).collect(
            PlatformCollectionRequest("liepin", ["AI 产品"], ["上海"], {"上海": "020"}, max_pages=1),
            hooks,
        )

        self.assertEqual(result.reason_code, "callback_stopped")
        self.assertEqual([candidate.storage_id for candidate in collected], ["liepin:job-1", "liepin:job-2"])
        self.assertEqual(sleeps, [13.0])
        self.assertEqual(clean_job_description(collected[0].jd), "负责需求分析，要求会 SQL。")

    def test_verification_page_stops_platform(self):
        browser = LiepinBrowser(
            new_tab=lambda url, **_kwargs: url,
            close_tab=lambda _target: True,
            evaluate=lambda _target, _script: json.dumps({"status": "blocked", "jobs": []}),
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda _candidate: True,
            on_parse_failed=lambda _reason: None,
            on_event=lambda **_kwargs: None,
        )
        result = LiepinCollector(browser=browser).collect(
            PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=1),
            hooks,
        )
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason_code, "rate_limit")

    def test_collection_waits_for_spa_list_render(self):
        evaluations = 0
        sleeps = []

        def evaluate(_target, script):
            nonlocal evaluations
            if ".job-info" in script:
                evaluations += 1
                if evaluations < 3:
                    return json.dumps({"status": "waiting", "jobs": []})
                return json.dumps({"status": "ready", "jobs": [{
                    "source_job_id": "job-spa",
                    "title": "AI 运营",
                    "company": "示例公司",
                    "city": "上海",
                    "url": "https://www.liepin.com/job/2001.shtml",
                }]})
            return json.dumps({
                "status": "ready", "title": "AI 运营", "company": "示例公司",
                "city": "上海", "jd": "负责 AI 产品运营与数据分析。",
            })

        browser = LiepinBrowser(
            new_tab=lambda url, **_kwargs: url,
            close_tab=lambda _target: True,
            evaluate=evaluate,
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda _candidate: False,
            on_parse_failed=lambda reason: self.fail(reason),
            on_event=lambda **_kwargs: None,
        )

        result = LiepinCollector(browser=browser, sleep=sleeps.append).collect(
            PlatformCollectionRequest("liepin", ["AI运营"], ["上海"], {"上海": "020"}, max_pages=1),
            hooks,
        )

        self.assertEqual(result.reason_code, "callback_stopped")
        self.assertEqual(evaluations, 3)
        self.assertEqual(sleeps, [0.75, 0.75])

    def test_multi_page_collection_clicks_next_and_applies_pacing(self):
        list_calls = {"n": 0}
        next_clicks = []

        def evaluate(_target, script):
            if ".job-info" in script:
                list_calls["n"] += 1
                if list_calls["n"] == 1:
                    return json.dumps({"status": "ready", "jobs": [{
                        "source_job_id": "job-p1",
                        "title": "AI 算法工程师",
                        "company": "示例公司",
                        "city": "上海",
                        "url": "https://www.liepin.com/job/3001.shtml",
                    }]})
                return json.dumps({"status": "ready", "jobs": [{
                    "source_job_id": "job-p2",
                    "title": "AI 数据工程师",
                    "company": "示例公司",
                    "city": "上海",
                    "url": "https://www.liepin.com/job/3002.shtml",
                }]})
            if "page-next" in script or "pager" in script:
                next_clicks.append(script)
                return True
            return json.dumps({
                "status": "ready", "title": "AI 工程师", "company": "示例公司",
                "city": "上海", "jd": "负责 AI 平台研发。",
            })

        browser = LiepinBrowser(
            new_tab=lambda url, **_kwargs: url,
            close_tab=lambda _target: True,
            evaluate=evaluate,
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )
        collected = []
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda candidate: collected.append(candidate) or True,
            on_parse_failed=lambda _reason: None,
            on_event=lambda **_kwargs: None,
        )
        result = LiepinCollector(
            browser=browser,
            sleep=lambda _s: None,
            uniform=lambda _low, _high: 33.0,
        ).collect(
            PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=2),
            hooks,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.reason_code, "search_exhausted")
        self.assertEqual(
            [c.storage_id for c in collected],
            ["liepin:job-p1", "liepin:job-p2"],
        )
        self.assertEqual(len(next_clicks), 1)

    def test_last_page_without_next_button_completes_gracefully(self):
        list_calls = {"n": 0}

        def evaluate(_target, script):
            if ".job-info" in script:
                list_calls["n"] += 1
                return json.dumps({"status": "ready", "jobs": [{
                    "source_job_id": f"job-{list_calls['n']}",
                    "title": "AI 工程师",
                    "company": "示例公司",
                    "city": "上海",
                    "url": f"https://www.liepin.com/job/{4000 + list_calls['n']}.shtml",
                }]})
            if "page-next" in script or "pager" in script:
                return False
            return json.dumps({
                "status": "ready", "title": "AI 工程师", "company": "示例公司",
                "city": "上海", "jd": "负责 AI 研发。",
            })

        browser = LiepinBrowser(
            new_tab=lambda url, **_kwargs: url,
            close_tab=lambda _target: True,
            evaluate=evaluate,
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )
        collected = []
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda candidate: collected.append(candidate) or True,
            on_parse_failed=lambda _reason: None,
            on_event=lambda **_kwargs: None,
        )
        result = LiepinCollector(
            browser=browser,
            sleep=lambda _s: None,
            uniform=lambda _low, _high: 33.0,
        ).collect(
            PlatformCollectionRequest("liepin", ["AI"], ["上海"], {"上海": "020"}, max_pages=3),
            hooks,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.reason_code, "search_exhausted")
        self.assertEqual([c.storage_id for c in collected], ["liepin:job-1"])