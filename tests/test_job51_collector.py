import json
from unittest import TestCase

from bosshunter.collection.base import CollectorHooks
from bosshunter.collection.models import PlatformCollectionRequest
from bosshunter.collection.orchestrator import normalize_collection_options
from bosshunter.collection.platforms.job51 import JS_EXTRACT_DETAIL, JS_EXTRACT_LIST, Job51Browser, Job51Collector, get_51job_city_code
from bosshunter.collection.text import clean_job_description


class Job51CollectorTests(TestCase):
    def test_list_script_uses_each_card_real_detail_url_for_multiple_cities(self):
        self.assertIn('a[href*="jobs.51job.com/"]', JS_EXTRACT_LIST)
        self.assertIn("url: jobUrl", JS_EXTRACT_LIST)
        self.assertNotIn("jobs.51job.com/shanghai/", JS_EXTRACT_LIST)

    def test_detail_script_targets_job_description_without_footer_noise(self):
        self.assertIn(".bmsg.job_msg.inbox > div:first-child", JS_EXTRACT_DETAIL)
        self.assertNotIn("body.slice(anchor)", JS_EXTRACT_DETAIL)

    def test_city_and_option_defaults_are_fail_closed(self):
        self.assertEqual(get_51job_city_code("北京市"), "010000")
        self.assertEqual(get_51job_city_code("上海市"), "020000")
        self.assertIsNone(get_51job_city_code("广州"))
        options = normalize_collection_options({}, {
            "platform_order": ["51job"],
            "platforms": {"51job": {"keywords": ["AI 产品"], "cities": ["上海"]}},
        })
        search = options["platforms"]["51job"]
        self.assertEqual(search["city_codes"], {"上海": "020000"})
        self.assertEqual(search["max_pages"], 1)
        self.assertNotIn("target_count", search)

    def test_beijing_search_uses_verified_51job_area_code(self):
        request = PlatformCollectionRequest(
            "51job",
            ["AI 产品"],
            ["北京"],
            {"北京": "010000"},
            max_pages=1,
        )

        url = Job51Collector.build_search_url(request, "北京", "AI 产品")

        self.assertIn("jobArea=010000", url)
        self.assertIn("keyword=AI%20%E4%BA%A7%E5%93%81", url)

    def test_collection_uses_platform_identity_and_rate_limit(self):
        list_payload = json.dumps({"status": "ready", "jobs": [
            {
                "source_job_id": "job-1",
                "title": "AI 产品经理",
                "company": "示例公司",
                "city": "上海",
                "url": "https://jobs.51job.com/shanghai/job-1.html",
            },
            {
                "source_job_id": "job-2",
                "title": "AI 产品运营",
                "company": "示例公司",
                "city": "上海",
                "url": "https://jobs.51job.com/shanghai/job-2.html",
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
            return list_payload if ".joblist-item" in script else detail_payload

        sleeps = []
        browser = Job51Browser(
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
        result = Job51Collector(
            browser=browser,
            sleep=sleeps.append,
            uniform=lambda _low, _high: 13.0,
        ).collect(
            PlatformCollectionRequest("51job", ["AI 产品"], ["上海"], {"上海": "020000"}, max_pages=1),
            hooks,
        )

        self.assertEqual(result.reason_code, "callback_stopped")
        self.assertEqual([candidate.storage_id for candidate in collected], ["51job:job-1", "51job:job-2"])
        self.assertEqual(sleeps, [13.0])
        self.assertEqual(clean_job_description(collected[0].jd), "负责需求分析，要求会 SQL。")

    def test_verification_page_stops_platform(self):
        browser = Job51Browser(
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
        result = Job51Collector(browser=browser).collect(
            PlatformCollectionRequest("51job", ["AI"], ["上海"], {"上海": "020000"}, max_pages=1),
            hooks,
        )
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason_code, "rate_limit")

    def test_collection_waits_for_spa_list_render(self):
        evaluations = 0
        sleeps = []

        def evaluate(_target, script):
            nonlocal evaluations
            if ".joblist-item" in script:
                evaluations += 1
                if evaluations < 3:
                    return json.dumps({"status": "waiting", "jobs": []})
                return json.dumps({"status": "ready", "jobs": [{
                    "source_job_id": "job-spa",
                    "title": "AI 运营",
                    "company": "示例公司",
                    "city": "上海",
                    "url": "https://jobs.51job.com/shanghai/job-spa.html",
                }]})
            return json.dumps({
                "status": "ready", "title": "AI 运营", "company": "示例公司",
                "city": "上海", "jd": "负责 AI 产品运营与数据分析。",
            })

        browser = Job51Browser(
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

        result = Job51Collector(browser=browser, sleep=sleeps.append).collect(
            PlatformCollectionRequest("51job", ["AI运营"], ["上海"], {"上海": "020000"}, max_pages=1),
            hooks,
        )

        self.assertEqual(result.reason_code, "callback_stopped")
        self.assertEqual(evaluations, 3)
        self.assertEqual(sleeps, [0.75, 0.75])


    def test_multi_page_collection_clicks_next_and_applies_pacing(self):
        # 覆盖 page>1 的翻页分支（此前无测试）：第一页采集后点击下一页，
        # 第二页再次渲染出岗位；翻页安全间隔应被施加一次。
        list_calls = {"n": 0}
        next_clicks = []

        def evaluate(target, script):
            if ".joblist-item" in script:
                list_calls["n"] += 1
                if list_calls["n"] == 1:
                    return json.dumps({"status": "ready", "jobs": [{
                        "source_job_id": "job-p1",
                        "title": "AI 算法工程师",
                        "company": "示例公司",
                        "city": "上海",
                        "url": "https://jobs.51job.com/shanghai/job-p1.html",
                    }]})
                return json.dumps({"status": "ready", "jobs": [{
                    "source_job_id": "job-p2",
                    "title": "AI 数据工程师",
                    "company": "示例公司",
                    "city": "上海",
                    "url": "https://jobs.51job.com/shanghai/job-p2.html",
                }]})
            if "btn-next" in script:
                next_clicks.append(script)
                return True  # 模拟存在下一页按钮
            return json.dumps({
                "status": "ready", "title": "AI 工程师", "company": "示例公司",
                "city": "上海", "jd": "负责 AI 平台研发。",
            })

        sleeps = []
        browser = Job51Browser(
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
        result = Job51Collector(
            browser=browser,
            sleep=sleeps.append,
            uniform=lambda _low, _high: 33.0,
        ).collect(
            PlatformCollectionRequest("51job", ["AI"], ["上海"], {"上海": "020000"}, max_pages=2),
            hooks,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.reason_code, "search_exhausted")
        self.assertEqual(
            [c.storage_id for c in collected],
            ["51job:job-p1", "51job:job-p2"],
        )
        # 第二页触发了一次 JS_CLICK_NEXT 调用
        self.assertEqual(len(next_clicks), 1)
        # 一次翻页间隔，另一次是第二个详情页之前的安全间隔。
        self.assertEqual(sleeps, [33.0, 33.0])

    def test_last_page_without_next_button_completes_gracefully(self):
        # 翻到第二页时若已无下一页按钮（JS_CLICK_NEXT 返回 False），
        # 应优雅结束而非报错。
        list_calls = {"n": 0}

        def evaluate(target, script):
            if ".joblist-item" in script:
                list_calls["n"] += 1
                return json.dumps({"status": "ready", "jobs": [{
                    "source_job_id": f"job-{list_calls['n']}",
                    "title": "AI 工程师",
                    "company": "示例公司",
                    "city": "上海",
                    "url": f"https://jobs.51job.com/shanghai/job-{list_calls['n']}.html",
                }]})
            if "btn-next" in script:
                return False  # 最后一页，无下一页按钮
            return json.dumps({
                "status": "ready", "title": "AI 工程师", "company": "示例公司",
                "city": "上海", "jd": "负责 AI 研发。",
            })

        browser = Job51Browser(
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
        result = Job51Collector(
            browser=browser,
            sleep=lambda _s: None,
            uniform=lambda _low, _high: 33.0,
        ).collect(
            PlatformCollectionRequest("51job", ["AI"], ["上海"], {"上海": "020000"}, max_pages=3),
            hooks,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.reason_code, "search_exhausted")
        self.assertEqual([c.storage_id for c in collected], ["51job:job-1"])
class JobDescriptionCleanupTests(TestCase):
    def test_known_platform_source_noise_is_removed(self):
        dirty = "[岗位kanzhun职责]1.公司业务后台开发 来自BOSS直聘 2.掌握 SQL"
        self.assertEqual(clean_job_description(dirty), "1.公司业务后台开发 2.掌握 SQL")
