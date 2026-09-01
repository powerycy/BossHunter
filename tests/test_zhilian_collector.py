import json
from pathlib import Path
from threading import Event
from unittest import TestCase

from bosshunter.collection.base import CollectionBlockedError, CollectionError, CollectorHooks
from bosshunter.collection.models import PlatformCollectionRequest
from bosshunter.collection.platforms.zhilian import (
    JD_CLASSES,
    JS_EXTRACT_DETAIL,
    JS_EXTRACT_LIST,
    ZhilianBrowser,
    ZhilianCollector,
    _source_job_id,
    get_zhilian_city_code,
    load_zhilian_city_snapshot,
    parse_zhilian_detail_html,
    parse_zhilian_list_html,
)


FIXTURES = Path(__file__).parent / "fixtures"


class ZhilianFixtureTests(TestCase):
    def test_city_snapshot_is_local_and_not_shared_with_boss_codes(self):
        snapshot = load_zhilian_city_snapshot()
        self.assertEqual(snapshot["schema"], "bosshunter.zhilian_cities.v1")
        self.assertEqual(snapshot["source"], "bundled_public_reference_snapshot")
        self.assertGreaterEqual(len(snapshot["cities"]), 10)
        self.assertEqual(get_zhilian_city_code("北京"), "530")
        self.assertEqual(get_zhilian_city_code("北京市"), "530")
        self.assertIsNone(get_zhilian_city_code("不存在的城市"))

    def test_list_and_detail_fixture_are_platform_specific_and_convertible(self):
        item = parse_zhilian_list_html(
            (FIXTURES / "zhilian_search.html").read_text(encoding="utf-8"),
            city="北京",
            source_keyword="AI 产品",
        )[0]
        detail = parse_zhilian_detail_html(
            (FIXTURES / "zhilian_detail.html").read_text(encoding="utf-8"),
            source_job_id=item["source_job_id"],
            list_candidate=item,
        )

        self.assertEqual(item["source_job_id"], "zl-1001")
        self.assertEqual(item["title"], "AI 产品实习生")
        self.assertEqual(detail["jd"], "负责 AI 招聘产品的用户调研、需求分析和数据复盘。")
        candidate = ZhilianCollector._candidate_from_detail(detail, ZhilianCollector._candidate_from_list(item, "北京", "AI 产品"))
        self.assertEqual(candidate.storage_id, "zhilian:zl-1001")
        self.assertEqual(candidate.platform, "zhilian")

    def test_current_live_dom_fixture_ignores_normal_login_link_and_reads_fields(self):
        item = parse_zhilian_list_html(
            (FIXTURES / "zhilian_current_search.html").read_text(encoding="utf-8"),
            city="深圳",
            source_keyword="人力",
        )[0]
        detail = parse_zhilian_detail_html(
            (FIXTURES / "zhilian_current_detail.html").read_text(encoding="utf-8"),
            source_job_id=item["source_job_id"],
            list_candidate=item,
        )

        self.assertEqual(item["source_job_id"], "CC123J40800000001")
        self.assertEqual(item["city"], "深圳·南山·南山")
        self.assertEqual(detail["title"], "人力资源信息管理岗")
        self.assertEqual(detail["company"], "示例科技有限公司")
        self.assertEqual(detail["city"], "深圳·南山·南山")
        self.assertIn("HR 系统管理", detail["jd"])

    def test_list_card_without_href_builds_detail_url_from_platform_job_id(self):
        items = parse_zhilian_list_html(
            """
            <div class="positionlist__list">
              <div class="joblist-box__item" data-positionid="NOHREF-1">
                <span class="jobinfo__name">人力专员</span>
                <p class="jobinfo__salary">8千-1万</p>
                <span class="jobinfo__other-info-item">深圳·南山</span>
                <div class="companyinfo__name">示例公司</div>
              </div>
            </div>
            """,
            city="深圳",
            source_keyword="人力",
        )

        self.assertEqual(items[0]["source_job_id"], "NOHREF-1")
        self.assertEqual(items[0]["url"], "https://www.zhaopin.com/jobdetail/NOHREF-1.htm")

    def test_current_dom_selectors_cover_anchor_company_and_detail_jd(self):
        self.assertIn(".companyinfo__name", JS_EXTRACT_LIST)
        self.assertIn("div.job-card", JS_EXTRACT_LIST)
        self.assertIn(".describtion-card__detail-content", JS_EXTRACT_DETAIL)
        self.assertIn("descriptionCard", JS_EXTRACT_DETAIL)
        self.assertIn("describtion-card__detail-content", JD_CLASSES)

    def test_current_detail_markup_parses_without_list_fallback(self):
        detail = parse_zhilian_detail_html(
            """
            <div class="summary-planes__title">人力专员</div>
            <div class="summary-planes__salary">8千-1万</div>
            <div class="summary-planes__info">深圳 南山 经验不限 大专</div>
            <div class="company-info__name">示例公司</div>
            <div class="address-info__content">深圳南山区</div>
            <div class="describtion-card__detail-content">负责招聘与员工关系管理。</div>
            """,
            source_job_id="CC123J40800000001",
        )

        self.assertEqual(detail["title"], "人力专员")
        self.assertEqual(detail["company"], "示例公司")
        self.assertEqual(detail["salary"], "8千-1万")
        self.assertEqual(detail["jd"], "负责招聘与员工关系管理。")

    def test_detail_with_readable_jd_ignores_generic_login_cta(self):
        detail = parse_zhilian_detail_html(
            """
            <header><button>立即登录</button><span>请登录后查看更多服务</span></header>
            <div class="summary-planes__title">AI 产品经理</div>
            <div class="company-info__name">示例科技</div>
            <div class="address-info__content">北京市朝阳区</div>
            <div class="describtion-card__detail-content">负责 AI 产品规划与用户研究。</div>
            """,
            source_job_id="zl-login-cta",
            list_candidate={"city": "北京", "url": "/jobdetail/zl-login-cta.htm"},
        )

        self.assertIn("AI 产品规划", detail["jd"])

    def test_detail_explicit_login_wall_still_blocks_even_with_stale_jd(self):
        with self.assertRaises(CollectionBlockedError) as error:
            parse_zhilian_detail_html(
                """
                <div class="login-dialog">登录失效，请先登录后继续</div>
                <div class="describtion-card__detail-content">这是页面上残留的旧职位描述。</div>
                """,
                source_job_id="zl-expired",
            )

        self.assertEqual(error.exception.code, "login_required")

    def test_live_detail_script_prefers_readable_jd_over_generic_login_cta(self):
        status_line = next(line for line in JS_EXTRACT_DETAIL.splitlines() if "status:" in line)
        self.assertLess(status_line.index("jdText ? 'ready'"), status_line.index("loginRequired ? 'login_required'"))
        self.assertIn("loginDialog", JS_EXTRACT_DETAIL)
        self.assertIn("loginPage", JS_EXTRACT_DETAIL)

    def test_source_job_id_ignores_detail_query_parameters(self):
        self.assertEqual(
            _source_job_id(
                "http://www.zhaopin.com/jobdetail/CC123J40800000001.htm?refcode=4019&data_identity=opaque"
            ),
            "CC123J40800000001",
        )

    def test_list_candidate_can_defer_company_until_detail_page(self):
        candidate = ZhilianCollector._candidate_from_list(
            {
                "source_job_id": "zl-2",
                "title": "人力专员",
                "url": "https://www.zhaopin.com/jobdetail/zl-2.htm",
            },
            "深圳",
            "人力",
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.company, "")

    def test_build_search_url_uses_current_city_search_page(self):
        url = ZhilianCollector.build_search_url(
            PlatformCollectionRequest("zhilian", ["人力"], ["深圳"], {"深圳": "765"}),
            "深圳",
            "人力",
            1,
        )
        self.assertEqual(url, "https://www.zhaopin.com/sou/jl765/")

    def test_missing_detail_jd_is_a_parse_failure(self):
        with self.assertRaises(CollectionError) as error:
            parse_zhilian_detail_html(
                '<div class="jobinfo__name">岗位</div><div class="companyinfo__name">公司</div><div class="jobinfo__city">北京</div>',
                source_job_id="zl-1",
                list_candidate={"url": "/job/1.html"},
            )
        self.assertEqual(error.exception.code, "parse_failed")

    def test_selector_change_is_explicitly_reported(self):
        with self.assertRaises(CollectionError) as error:
            parse_zhilian_list_html("<html><body><div>页面结构已变化，但列表节点全部消失；这是一段足够长的诊断文本，用于确认选择器整体失效而不是正常的空结果。</div></body></html>")
        self.assertEqual(error.exception.code, "selector_changed")

    def test_explicit_login_wall_is_blocked_but_login_link_is_not(self):
        with self.assertRaises(CollectionBlockedError) as error:
            parse_zhilian_list_html(
                '<html><body><input placeholder="输入职位、公司等搜索"><div>请先登录后查看职位详情</div></body></html>'
            )
        self.assertIn("登录", str(error.exception))

        with self.assertRaises(CollectionBlockedError) as modern_error:
            parse_zhilian_list_html(
                '<html><body><input placeholder="搜索职位、公司"><p>登录查看更多相关职位</p><button>立即登录</button></body></html>'
            )
        self.assertEqual(modern_error.exception.code, "login_required")

    def test_collector_uses_shared_runtime_and_stops_at_target(self):
        responses = {
            "list": json.dumps({"items": [
                {"source_job_id": "zl-1", "title": "岗位一", "company": "公司一", "city": "北京"},
                {"source_job_id": "zl-2", "title": "岗位二", "company": "公司二", "city": "北京"},
            ]}),
            "detail": json.dumps({"source_job_id": "zl-1", "title": "岗位一", "company": "公司一", "city": "北京", "jd": "JD"}),
        }
        opened: list[str] = []

        def new_tab(url, **_):
            opened.append(url)
            return f"tab-{len(opened)}"

        browser = ZhilianBrowser(
            new_tab=new_tab,
            close_tab=lambda _target: True,
            evaluate=lambda _target, script: responses["detail" if "describtion__detail-content" in script else "list"],
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )
        collected = []
        hooks = CollectorHooks(
            stop_event=Event(),
            on_list_candidate=lambda candidate: True,
            on_candidate=lambda candidate: collected.append(candidate) or len(collected) < 1,
            on_parse_failed=lambda reason: self.fail(reason),
            on_event=lambda **_kwargs: None,
        )
        result = ZhilianCollector(browser=browser).collect(
            PlatformCollectionRequest("zhilian", ["AI"], ["北京"], {"北京": "530"}, max_pages=1),
            hooks,
        )

        self.assertEqual(result.reason_code, "callback_stopped")
        self.assertEqual(len(collected), 1)
        self.assertEqual(opened[1], "https://www.zhaopin.com/jobdetail/zl-1.htm")

    def test_collector_submits_keyword_through_shared_browser_input_actions(self):
        responses = {
            "list": json.dumps({"items": [
                {"source_job_id": "zl-1", "title": "岗位一", "company": "公司一", "city": "北京", "url": "/job/1.html"},
            ]}),
            "detail": json.dumps({"source_job_id": "zl-1", "title": "岗位一", "company": "公司一", "city": "北京", "jd": "JD"}),
        }
        actions: list[tuple[str, str]] = []

        def record(name):
            def action(_target, value, **_kwargs):
                actions.append((name, value))
                return True
            return action

        browser = ZhilianBrowser(
            new_tab=lambda _url, **_kwargs: "tab-1",
            close_tab=lambda _target: True,
            evaluate=lambda _target, script: responses["detail" if "describtion__detail-content" in script else "list"],
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
            click_action=record("click"),
            type_text_action=record("type"),
            press_key_action=record("key"),
        )
        hooks = CollectorHooks(
            stop_event=Event(),
            on_list_candidate=lambda candidate: True,
            on_candidate=lambda candidate: False,
            on_parse_failed=lambda reason: self.fail(reason),
            on_event=lambda **_kwargs: None,
        )

        result = ZhilianCollector(browser=browser).collect(
            PlatformCollectionRequest("zhilian", ["人力"], ["北京"], {"北京": "530"}, max_pages=1),
            hooks,
        )

        self.assertEqual(result.reason_code, "callback_stopped")
        self.assertEqual(actions, [
            (
                "click",
                'input[placeholder="输入职位、公司等搜索"], '
                'input[placeholder="搜索职位、公司"], '
                'input[placeholder*="职位、公司"]',
            ),
            ("key", "SelectAll"),
            ("key", "Backspace"),
            ("type", "人力"),
        ])

    def test_collector_reads_current_split_page_by_clicking_job_card(self):
        search_state_calls = 0

        def evaluate_current(_target, script):
            nonlocal search_state_calls
            if "item_count" in script:
                search_state_calls += 1
                if search_state_calls == 1:
                    return json.dumps({"url": "https://www.zhaopin.com/jobs?jl=530", "input": "", "signature": "old"})
                return json.dumps({
                    "url": "https://www.zhaopin.com/jobs?jl=530&pageMode=search&kw=AI运营",
                    "input": "AI运营",
                    "signature": "new",
                })
            if "submitted_by" in script:
                return json.dumps({"ok": True, "value": "AI运营", "submitted_by": "button"})
            if "card.click()" in script:
                return json.dumps({"ok": True})
            if "descriptionCard" in script:
                return json.dumps({
                    "status": "ready",
                    "title": "AI 产品运营",
                    "company": "示例科技",
                    "salary": "1-2万",
                    "city": "北京",
                    "jd": "负责 AI 产品运营、用户增长与数据复盘。",
                    "url": "https://www.zhaopin.com/jobdetail/CC123J40800000001.htm",
                })
            return json.dumps({"status": "ready", "items": [{"card_index": 0, "company": "示例科技", "city": "北京"}]})

        navigated = []
        browser = ZhilianBrowser(
            new_tab=lambda _url, **_kwargs: "tab-current",
            close_tab=lambda _target: True,
            evaluate=evaluate_current,
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
            navigate_action=lambda _target, url: navigated.append(url) or True,
        )
        collected = []
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda candidate: True,
            on_candidate=lambda candidate: collected.append(candidate) or False,
            on_parse_failed=lambda reason: self.fail(reason),
            on_event=lambda **_kwargs: None,
        )

        result = ZhilianCollector(browser=browser, sleep=lambda _seconds: None).collect(
            PlatformCollectionRequest("zhilian", ["AI运营"], ["北京"], {"北京": "530"}, max_pages=1),
            hooks,
        )

        self.assertEqual(result.reason_code, "callback_stopped")
        self.assertEqual(navigated, ["https://www.zhaopin.com/sou/jl530/"])
        self.assertEqual(collected[0].storage_id, "zhilian:CC123J40800000001")
        self.assertIn("用户增长", collected[0].jd)

    def test_detail_reader_rechecks_a_transient_false_login_state(self):
        responses = iter([
            {"status": "login_required", "title": "AI 产品运营", "company": "示例科技", "jd": ""},
            {
                "status": "ready",
                "title": "AI 产品运营",
                "company": "示例科技",
                "city": "北京",
                "jd": "负责 AI 产品运营。",
                "url": "https://www.zhaopin.com/jobdetail/zl-retry.htm",
            },
        ])
        waits = []
        browser = ZhilianBrowser(
            evaluate=lambda _target, _script: json.dumps(next(responses)),
        )
        collector = ZhilianCollector(browser=browser, sleep=waits.append)

        detail = collector._read_detail_with_retry("tab-current", "北京")

        self.assertEqual(detail["status"], "ready")
        self.assertEqual(waits, [0.8])
