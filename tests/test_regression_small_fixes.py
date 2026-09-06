import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import yaml


ROOT = Path(__file__).resolve().parents[1]


class PublicPrivacyTests(unittest.TestCase):
    def test_tracked_files_do_not_reference_company_api_brand(self):
        import subprocess

        blocked = "one" + "api"
        result = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files"],
            capture_output=True,
            text=True,
            check=True,
        )

        offenders = []
        for rel_path in result.stdout.splitlines():
            path = ROOT / rel_path
            if not path.is_file():
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if blocked in source.lower():
                offenders.append(rel_path)

        self.assertEqual(offenders, [])


class VersionMetadataTests(unittest.TestCase):
    def test_release_version_is_consistent(self):
        import json

        import bosshunter
        from bosshunter.web.server import health

        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        sidebar_source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "components"
            / "layout"
            / "Sidebar.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn('version = "2.3.2"', pyproject)
        self.assertEqual(bosshunter.__version__, "2.3.2")
        self.assertEqual(json.loads(health())["version"], "2.3.2")
        self.assertIn("v2.3.2 · 本地控制台", sidebar_source)
        self.assertNotIn("v1.1.0", sidebar_source)


class ConfigExampleTests(unittest.TestCase):
    def test_example_uses_search_cities_list(self):
        config = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))

        self.assertIn("cities", config["search"])
        self.assertIsInstance(config["search"]["cities"], list)
        self.assertNotIn("city", config["search"])

    def test_example_defaults_to_not_allowing_internships(self):
        config = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))

        self.assertIs(config["profile"]["allow_internship"], False)

    def test_example_defaults_to_disabled_follow_up(self):
        config = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))

        self.assertIs(config["follow_up"]["enabled"], False)

    def test_example_does_not_include_prefilter_threshold(self):
        config = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))

        self.assertNotIn("prefilter_threshold", config["scoring"])


class ConfigValidationTests(unittest.TestCase):
    def test_load_config_rejects_unsupported_ai_provider(self):
        from bosshunter.config import load_config

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text("ai:\n  provider: openai\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Anthropic 或 OpenAI 兼容接口"):
                load_config(config_path)

    def test_load_config_defaults_to_not_allowing_internships(self):
        from bosshunter.config import load_config

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text("profile:\n  salary_min: 10\n", encoding="utf-8")

            config = load_config(config_path)

        self.assertIs(config["profile"]["allow_internship"], False)
        self.assertNotIn("prefilter_threshold", config["scoring"])

    def test_load_config_defaults_to_disabled_follow_up(self):
        from bosshunter.config import load_config

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text("profile:\n  salary_min: 10\n", encoding="utf-8")

            config = load_config(config_path)

        self.assertIs(config["follow_up"]["enabled"], False)

    def test_monitor_does_not_follow_up_when_setting_is_missing(self):
        from bosshunter.executor import monitor

        with patch.object(monitor, "get_db") as get_db:
            result = monitor._check_follow_ups({"follow_up": {}}, Mock())

        self.assertEqual(result, 0)
        get_db.assert_not_called()

    def test_reply_monitor_opens_chat_in_background(self):
        from bosshunter.executor import monitor

        tracked_job = {"id": "job-1", "status": "sent"}
        db = Mock()
        with patch.object(monitor, "get_db", return_value=db), \
             patch.object(
                 monitor,
                 "get_jobs_by_status",
                 side_effect=[[tracked_job], [], [], [], []],
             ), \
             patch.object(monitor, "new_tab", return_value="chat-target") as new_tab, \
             patch.object(monitor, "wait_for_load"), \
             patch.object(monitor, "evaluate", return_value="[]"), \
             patch.object(monitor, "close_tab"), \
             patch.object(monitor.time, "sleep"):
            result = monitor.check_replies(
                {"monitor": {"chat_url": "https://www.zhipin.com/web/geek/chat"}}
            )

        self.assertEqual(result, [])
        new_tab.assert_called_once_with(
            "https://www.zhipin.com/web/geek/chat",
            background=True,
        )

    def test_monitor_job_pages_stay_in_background(self):
        from bosshunter.executor import monitor

        with patch.object(
            monitor,
            "new_tab",
            side_effect=["job-target-1", "job-target-2"],
        ) as new_tab, \
             patch.object(monitor, "wait_for_load"), \
             patch.object(monitor, "click", return_value=False), \
             patch.object(monitor, "close_tab"), \
             patch.object(monitor, "_open_conversation_from_chat_list", return_value=None), \
             patch.object(monitor.time, "sleep"):
            result = monitor._open_conversation(
                {"url": "https://www.zhipin.com/job_detail/job-1.html"},
                {"monitor": {}},
            )

        self.assertIsNone(result)
        self.assertEqual(
            new_tab.call_args_list,
            [
                call(
                    "https://www.zhipin.com/job_detail/job-1.html",
                    background=True,
                ),
                call(
                    "https://www.zhipin.com/job_detail/job-1.html",
                    background=True,
                ),
            ],
        )


class AiPromptRegressionTests(unittest.TestCase):
    def test_scorer_prompt_uses_universal_weighted_evidence(self):
        from bosshunter.ai.scorer import SCORING_PROMPT

        self.assertIn("核心职责匹配", SCORING_PROMPT)
        self.assertIn("40分", SCORING_PROMPT)
        self.assertIn("可迁移证据", SCORING_PROMPT)
        self.assertIn("不要自行输出总分", SCORING_PROMPT)
        self.assertNotIn("卫星遥感", SCORING_PROMPT)
        self.assertNotIn("小红书/抖音", SCORING_PROMPT)

    def test_tailored_resume_prompt_preserves_platform_growth_cases(self):
        from bosshunter.ai.resume import RESUME_TAILOR_PROMPT

        self.assertIn("平台案例和量化结果", RESUME_TAILOR_PROMPT)
        self.assertIn("阅读/观看", RESUME_TAILOR_PROMPT)
        self.assertIn("粉丝增长", RESUME_TAILOR_PROMPT)


class PrefilterHardGateTests(unittest.TestCase):
    def test_anonymous_company_jobs_are_filtered_before_ai_scoring(self):
        from bosshunter.ai.prefilter import quick_score

        config = {"profile": {"deal_breakers": [], "salary_min": 0}}
        anonymous_companies = [
            "某互联网公司",
            "某500强上市公司",
            "北京某大型计算机软件上市公司",
            "上海某大型电子商务公司",
            "北京某中型企业数字化与AI服务公司",
        ]

        for company in anonymous_companies:
            with self.subTest(company=company):
                score, reason = quick_score(
                    {
                        "company": company,
                        "title": "AI产品经理",
                        "jd": "",
                        "salary": "20-30K",
                    },
                    config,
                )

                self.assertEqual(score, 0)
                self.assertEqual(reason, "匿名公司岗位")

    def test_named_company_jobs_still_pass_anonymous_company_filter(self):
        from bosshunter.ai.prefilter import quick_score

        config = {"profile": {"deal_breakers": [], "salary_min": 0}}
        score, reason = quick_score(
            {
                "company": "荣耀终端技术有限公司",
                "title": "AI产品经理",
                "jd": "",
                "salary": "20-30K",
            },
            config,
        )

        self.assertEqual(score, 100)
        self.assertEqual(reason, "预筛通过")

    def test_deal_breakers_still_match_title_only(self):
        from bosshunter.ai.prefilter import quick_score

        config = {"profile": {"deal_breakers": ["外包"], "salary_min": 0}}
        job = {"title": "AI产品经理", "jd": "非外包项目，团队稳定", "salary": "20-30K"}

        score, reason = quick_score(job, config)

        self.assertEqual(score, 100)
        self.assertEqual(reason, "预筛通过")

    def test_deal_breaker_in_title_is_filtered(self):
        from bosshunter.ai.prefilter import quick_score

        config = {"profile": {"deal_breakers": ["外包"], "salary_min": 0}}
        job = {"title": "AI产品经理 外包", "jd": "", "salary": "20-30K"}

        score, reason = quick_score(job, config)

        self.assertEqual(score, 0)
        self.assertEqual(reason, "触发排除词: 外包")

    def test_jd_deal_breaker_filters_technical_requirements(self):
        from bosshunter.ai.prefilter import quick_score

        config = {
            "profile": {
                "deal_breakers": [],
                "jd_deal_breakers": ["SQL", "Linux"],
                "salary_min": 0,
            }
        }
        job = {
            "title": "业务实施顾问",
            "jd": "需要熟悉 Linux 环境并能编写 SQL 脚本",
            "salary": "10-15K",
        }

        score, reason = quick_score(job, config)

        self.assertEqual(score, 0)
        self.assertEqual(reason, "触发JD排除词: SQL")

    def test_default_rejects_internship_titles(self):
        from bosshunter.ai.prefilter import quick_score

        config = {"profile": {"deal_breakers": [], "salary_min": 0}}
        job = {"title": "AI产品实习生", "jd": "", "salary": "3-5K"}

        score, reason = quick_score(job, config)

        self.assertEqual(score, 0)
        self.assertEqual(reason, "实习/管培岗位")

    def test_default_rejects_management_trainee_titles(self):
        from bosshunter.ai.prefilter import quick_score

        config = {"profile": {"deal_breakers": [], "salary_min": 0}}
        job = {"title": "产品管培生", "jd": "", "salary": "8-12K"}

        score, reason = quick_score(job, config)

        self.assertEqual(score, 0)
        self.assertEqual(reason, "实习/管培岗位")

    def test_allow_internship_lets_internship_titles_pass_prefilter(self):
        from bosshunter.ai.prefilter import quick_score

        config = {"profile": {"deal_breakers": [], "allow_internship": True, "salary_min": 0}}
        job = {"title": "AI Product Intern", "jd": "", "salary": "3-5K"}

        score, reason = quick_score(job, config)

        self.assertEqual(score, 100)
        self.assertEqual(reason, "预筛通过")

    def test_salary_below_minimum_is_filtered(self):
        from bosshunter.ai.prefilter import quick_score

        config = {"profile": {"deal_breakers": [], "salary_min": 100}}
        job = {"title": "AI产品经理", "jd": "", "salary": "12K"}

        score, reason = quick_score(job, config)

        self.assertEqual(score, 0)
        self.assertEqual(reason, "薪资低于硬性要求: 12K < 100K")

    def test_passing_job_returns_hard_gate_pass(self):
        from bosshunter.ai.prefilter import quick_score

        config = {"profile": {"deal_breakers": ["外包", "996"], "salary_min": 15}}
        job = {"title": "AI产品经理", "jd": "", "salary": "20-30K"}

        score, reason = quick_score(job, config)

        self.assertEqual(score, 100)
        self.assertEqual(reason, "预筛通过")


class ConfirmationUiTests(unittest.TestCase):
    @patch("bosshunter.ui.confirm.Prompt.ask")
    @patch("bosshunter.ui.confirm.get_jobs_pending_confirmation")
    @patch("bosshunter.ui.confirm.get_db")
    def test_confirmation_defaults_to_individual_selection(self, get_db, get_jobs_pending_confirmation, prompt_ask):
        from bosshunter.ui.confirm import show_confirmation

        db = Mock()
        get_db.return_value = db
        get_jobs_pending_confirmation.return_value = [
            {
                "id": "job-1",
                "company": "Example",
                "title": "Engineer",
                "salary": "10-20K",
                "score": 88,
                "score_reason": "good match",
                "greeting": "",
            }
        ]
        prompt_ask.return_value = "q"

        result = show_confirmation({})

        self.assertFalse(result)
        self.assertEqual(prompt_ask.call_args_list[0].kwargs["default"], "s")


class DashboardPageTests(unittest.TestCase):
    def setUp(self):
        self.source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "pages"
            / "DashboardPage.tsx"
        ).read_text(encoding="utf-8")

    def test_dashboard_renders_monitor_execution_history(self):
        self.assertIn("MonitorExecutionView", self.source)
        self.assertIn("history", self.source)
        self.assertIn("<MonitorExecutionView", self.source)
        self.assertIn("history={history}", self.source)

    def test_dashboard_exposes_manual_refresh_button(self):
        self.assertIn("RefreshCw", self.source)
        self.assertIn("onClick={refresh}", self.source)
        self.assertIn("refreshing ? '刷新中' : '刷新'", self.source)
        self.assertIn("最后刷新：", self.source)
        self.assertIn("refreshing && 'animate-spin'", self.source)

    def test_dashboard_shows_detailed_greeting_queue_progress(self):
        self.assertIn("if (log.includes('招呼语进度')) return log", self.source)
        self.assertIn("whitespace-pre-line text-lg", self.source)

    def test_dashboard_falls_back_to_concrete_task_status(self):
        self.assertNotIn("return '等待后端返回阶段'", self.source)
        self.assertIn("`${task.label}正在启动`", self.source)
        self.assertIn("`${task.label}正在停止`", self.source)
        self.assertIn("`${task.label}已完成`", self.source)
        self.assertIn("`${task.label}已停止`", self.source)
        self.assertIn("`${task.label}运行失败`", self.source)
        self.assertIn("currentTaskStage(visibleTask)", self.source)

    def test_dashboard_can_stop_after_start_response_arrives(self):
        active_branch = self.source.index("if (activeTask?.mode === mode)")
        pending_guard = self.source.index("if (modePending) return")

        self.assertLess(active_branch, pending_guard)

    def test_dashboard_refresh_prevents_overlapping_requests(self):
        hook_source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "hooks"
            / "useDashboard.ts"
        ).read_text(encoding="utf-8")

        self.assertIn("refreshingRef.current", hook_source)
        self.assertIn("setLastRefreshedAt", hook_source)
        self.assertIn("setWorkbench(prev => ({ ...prev, task, last_task: task }))", hook_source)
        self.assertIn("scope === 'monitor' ? 2000 : 5000", hook_source)
        self.assertIn("visibilitychange", hook_source)
        self.assertIn("window.addEventListener('focus'", hook_source)

    def test_dashboard_filters_today_jobs_and_clears_hidden_selection(self):
        self.assertIn("filteredTodayJobs", self.source)
        self.assertIn("setSelected(filteredTodayJobs.map(job => job.id))", self.source)
        self.assertIn("visibleJobIds.has(id)", self.source)
        self.assertIn("没有符合当前条件的岗位", self.source)

    def test_workbench_stats_distinguish_today_total_and_current_pending(self):
        self.assertIn("type StatsScope = 'today' | 'total'", self.source)
        self.assertIn("今日数据", self.source)
        self.assertIn("累计数据", self.source)
        self.assertIn("workbench.funnel_today", self.source)
        self.assertIn("workbench.pending_confirmation.length", self.source)
        self.assertIn("今日新增岗位", self.source)
        self.assertIn("当前待确认", self.source)
        self.assertIn("今日已投递", self.source)

    def test_workbench_task_status_shows_current_run_metrics(self):
        self.assertIn("本轮扫描", self.source)
        self.assertIn("本轮新增", self.source)
        self.assertIn("重复岗位", self.source)
        self.assertIn("AI通过", self.source)
        self.assertIn("AI过滤", self.source)
        self.assertIn("AI失败", self.source)

    def test_shared_job_filter_bar_exposes_confirmed_controls(self):
        filter_source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "components"
            / "jobs"
            / "JobFilterBar.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn("搜索职位、公司、JD 或评分理由", filter_source)
        self.assertIn("最低评分", filter_source)
        self.assertIn("最低薪资", filter_source)
        self.assertIn("最高薪资", filter_source)
        self.assertIn("全部状态", filter_source)
        self.assertNotIn("招聘者活跃度", filter_source)
        self.assertIn("采集时间：全部", filter_source)
        self.assertIn("近 3 天", filter_source)
        self.assertIn("近 7 天", filter_source)
        self.assertIn("筛选结果", filter_source)
        self.assertIn("重置筛选", filter_source)
        self.assertIn("2xl:grid-cols-4", filter_source)
        self.assertNotIn("xl:grid-cols-8", filter_source)
        self.assertIn("flex-wrap", filter_source)
        self.assertIn("min-w-0", filter_source)

    def test_jobs_pool_uses_server_search_and_controlled_pagination(self):
        search_hook_source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "hooks"
            / "useJobSearch.ts"
        ).read_text(encoding="utf-8")
        jobs_table_source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "components"
            / "dashboard"
            / "JobsTable.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn("/api/jobs/search?", search_hook_source)
        self.assertIn("250", search_hook_source)
        self.assertIn("created_within", search_hook_source)
        self.assertIn("onPageChange", jobs_table_source)
        self.assertIn("pageSize", jobs_table_source)
        self.assertIn("招聘者活跃", jobs_table_source)
        self.assertIn("活跃度未知", jobs_table_source)
        self.assertIn("dateStr.replace(' ', 'T')", jobs_table_source)
        self.assertIn("`${dateStr.replace(' ', 'T')}Z`", jobs_table_source)

    def test_dashboard_exposes_batch_reject_for_selected_pending_jobs(self):
        # Arrange: DashboardPage source is loaded in setUp.

        # Act / Assert
        self.assertIn("rejectSelectedJobs", self.source)
        self.assertIn("/api/workbench/reject", self.source)
        self.assertIn("放弃已选", self.source)
        self.assertIn("确定放弃这", self.source)
        self.assertIn("setSelected(prev => prev.filter", self.source)

    def test_each_pending_job_card_has_a_reject_action(self):
        self.assertIn("onReject={() => rejectSelectedJobs([job.id])}", self.source)
        self.assertIn("放弃岗位", self.source)

    def test_dashboard_sends_ready_greetings_without_second_confirmation(self):
        # Arrange: DashboardPage source is loaded in setUp.

        # Act / Assert
        self.assertIn("sendReadyGreetings", self.source)
        self.assertIn("direct_send: true", self.source)
        self.assertIn("已直接进入发送流程", self.source)
        self.assertNotIn("confirmDeliver(pendingGreetingJobs.map", self.source)
        self.assertNotIn("confirmDeliver([job.id])}>发送招呼语", self.source)
        self.assertIn("已在当前发送队列中，请等待依次发送", self.source)
        self.assertIn("追加到当前发送队列", self.source)

    def test_dashboard_pending_greetings_can_be_rejected(self):
        # Arrange: DashboardPage source is loaded in setUp.

        # Act / Assert
        self.assertIn("const pendingGreetingJobs = workbench.pending_greetings", self.source)
        self.assertNotIn(
            "workbench.pending_greetings.filter(job => !confirmedDeliveryIds.has(job.id))",
            self.source,
        )
        self.assertIn("rejectSelectedJobs(pendingGreetingJobs.map(job => job.id))", self.source)
        pending_section = self.source[self.source.index("待发送招呼语"):]
        self.assertIn("rejectSelectedJobs([job.id])", pending_section)
        self.assertIn(">放弃</Button>", pending_section)

    def test_dashboard_send_errors_do_not_fake_an_active_full_task(self):
        # Arrange: DashboardPage source is loaded in setUp.

        # Act / Assert
        self.assertNotIn("blockedFullTask", self.source)
        self.assertNotIn("send-errors-blocked-full-flow", self.source)
        self.assertNotIn("全流程卡在打招呼环节", self.source)
        self.assertIn("放弃已失效岗位", self.source)
        self.assertIn("放弃全部", self.source)

    def test_monitor_pending_replies_can_be_dismissed(self):
        # Arrange: DashboardPage source is loaded in setUp.

        # Act / Assert
        self.assertIn("dismissPendingReply", self.source)
        self.assertIn("/dismiss", self.source)
        self.assertIn("reply_dismissed", self.source)
        self.assertIn("放弃", self.source)

    def test_monitor_cards_link_to_the_corresponding_chat_conversation(self):
        self.assertIn("openMonitorConversation", self.source)
        self.assertIn("/open-chat", self.source)
        self.assertIn("打开聊天对话", self.source)
        self.assertIn("已在 BOSS 中定位到对应聊天对话。", self.source)
        self.assertNotIn("https://www.zhipin.com/web/geek/chat?jobId=${encodeURIComponent(item.job_id)}", self.source)

    def test_monitor_cards_render_scrollable_hr_ai_conversation_history(self):
        self.assertIn("monitorConversationMessages(item, history)", self.source)
        self.assertIn("const resumeRequestParsed", self.source)
        self.assertIn("candidate.action === 'needs_resume'", self.source)
        self.assertIn("max-h-[260px]", self.source)
        self.assertIn("overflow-y-auto", self.source)
        self.assertNotIn("可上下滚动", self.source)
        self.assertNotIn("{fromHr ? 'HR' : 'AI 已回答'}", self.source)
        self.assertIn("{fromHr ? 'HR' : 'AI'}", self.source)
        self.assertIn("grid-cols-[28px_minmax(0,1fr)]", self.source)
        self.assertNotIn("{message.time || item.created_at}", self.source)
        self.assertNotIn("监测记录时间", self.source)
        self.assertIn("AI 建议回复（尚未回答）", self.source)
        self.assertIn("已回复：定制简历已发送，本轮聊天记录保留在上方。", self.source)

    def test_monitor_replied_tab_keeps_each_outbound_round(self):
        self.assertIn("const repliedRecords = history.filter(isOutboundReplyRecord)", self.source)
        self.assertIn("暂无近 7 天已回复对话。", self.source)
        self.assertIn("parseHistoryDetail(item).schema.startsWith('replied.')", self.source)
        self.assertIn("item.action === 'auto_replied'", self.source)
        self.assertIn("item.action === 'resume_sent'", self.source)
        self.assertIn("item.action === 'needs_resume' && !isResumeRequestResolved(item, history)", self.source)

    def test_monitor_shows_detected_hr_messages_while_full_chat_is_loading(self):
        self.assertIn("item.action === 'hr_reply_detected'", self.source)
        self.assertIn("isDetectedReplyResolved", self.source)
        self.assertIn("detectedReplies", self.source)
        self.assertIn("detectedReplyPreview", self.source)
        self.assertIn("已检测到 HR 新消息，等待继续读取完整对话并生成处理结果。", self.source)
        self.assertIn("prepareDetectedReply", self.source)
        self.assertIn("/prepare-reply", self.source)
        self.assertIn("读取并生成建议", self.source)
        self.assertNotIn("disabled={!canReply}", self.source)

    def test_monitor_resolution_parser_shows_manual_reply_and_hr_question(self):
        history_detail_source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "lib"
            / "historyDetail.ts"
        ).read_text(encoding="utf-8")

        self.assertIn("item.detail_payload.manual_reply", history_detail_source)
        self.assertIn("item.detail_payload.pending_hr_question", history_detail_source)
        self.assertIn("item.detail_payload.pending_history_id", history_detail_source)

    def test_monitor_surfaces_resume_generation_failures_as_pending_items(self):
        # Arrange: DashboardPage source is loaded in setUp.

        # Act / Assert
        self.assertIn("item.action === 'resume_failed'", self.source)
        self.assertIn("isResumeFailureResolved", self.source)
        self.assertIn("resumeFailures", self.source)
        self.assertIn("pendingItems", self.source)
        self.assertIn("displayedHistory", self.source)
        self.assertIn("定制简历生成失败，尚无可下载文件", self.source)
        self.assertIn("系统失败原因", self.source)
        self.assertIn("parsed.systemReason", self.source)
        self.assertIn("Boolean(item.resolved || item.resume_path)", self.source)
        self.assertNotIn("hrText || item.detail || getActionLabel(item.action)", self.source)

    def test_monitor_parses_legacy_resume_failure_text_as_a_system_reason(self):
        history_detail_source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "lib"
            / "historyDetail.ts"
        ).read_text(encoding="utf-8")

        self.assertIn("item.action === 'resume_failed' ? '' : payloadReply", history_detail_source)
        self.assertIn(
            "item.action === 'resume_failed' ? payloadReply : ''",
            history_detail_source,
        )

    def test_dashboard_shows_automatic_task_deadline_and_stop_reason(self):
        self.assertIn("自动截止：", self.source)
        self.assertIn("visibleTask.deadline_at", self.source)
        self.assertIn("visibleTask.stop_reason", self.source)


class SidebarTests(unittest.TestCase):
    def setUp(self):
        # Arrange
        self.source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "components"
            / "layout"
            / "Sidebar.tsx"
        ).read_text(encoding="utf-8")

    def test_sidebar_star_link_places_github_icon_left_and_centers_star_label(self):
        # Act / Assert
        self.assertIn("relative flex items-center", self.source)
        self.assertIn("absolute left-3", self.source)
        self.assertIn("mx-auto flex items-center justify-center", self.source)
        self.assertIn("text-xl", self.source)
        self.assertIn("text-yellow-400", self.source)

    def test_sidebar_fetches_unresolved_reply_count(self):
        # Act / Assert
        self.assertIn("/api/history/unresolved-replies/count", self.source)
        self.assertNotIn("item.action === 'reply_pending'", self.source)


class HeaderTests(unittest.TestCase):
    def setUp(self):
        # Arrange
        self.source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "components"
            / "layout"
            / "Header.tsx"
        ).read_text(encoding="utf-8")

    def test_header_version_metadata_right_side_omits_duplicate_console_label(self):
        # Act / Assert
        self.assertNotIn("v2.1 · 本地控制台", self.source)
        self.assertIn("本地服务运行中", self.source)


class ConfigPageTests(unittest.TestCase):
    def setUp(self):
        # Arrange
        self.source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "pages"
            / "ConfigPage.tsx"
        ).read_text(encoding="utf-8")
        self.hook_source = (
            ROOT
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "src"
            / "hooks"
            / "useConfig.ts"
        ).read_text(encoding="utf-8")

    def test_config_page_does_not_render_prefilter_threshold(self):
        # Act / Assert
        self.assertNotIn("prefilter_threshold", self.source)
        self.assertNotIn("预筛阈值", self.source)

    def test_allow_internship_switch_appears_below_deal_breakers(self):
        # Act
        deal_breakers_index = self.source.index("排除关键词")
        allow_internship_index = self.source.index("接受实习/管培岗位")

        # Assert
        self.assertGreater(allow_internship_index, deal_breakers_index)
        self.assertIn("profile.allow_internship", self.source)

    def test_config_page_exposes_jd_deal_breakers(self):
        self.assertIn("JD 排除关键词", self.source)
        self.assertIn("profile.jd_deal_breakers", self.source)
        self.assertIn("完整 JD 含这些词时会在 AI 评分前跳过", self.source)

    def test_config_page_api_failure_displays_error_instead_of_infinite_loading(self):
        # Act / Assert
        self.assertIn("error", self.hook_source)
        self.assertIn("!configRes.ok", self.hook_source)
        self.assertIn("!schemaRes.ok", self.hook_source)
        self.assertIn("配置加载失败", self.source)
        self.assertIn("请确认后端服务已启动", self.source)
        self.assertIn("error", self.source)

    def test_follow_up_switch_defaults_to_off_when_config_field_is_missing(self):
        self.assertIn("config.follow_up?.enabled ?? false", self.source)

    def test_config_page_merges_boss_safety_and_throttle_settings(self):
        self.assertEqual(self.source.count('title="反监测设置"'), 1)
        self.assertNotIn('title="BOSS 直聘采集安全"', self.source)
        self.assertNotIn('title="反检测设置"', self.source)
        self.assertIn('label="BOSS 操作间隔倍率"', self.source)
        self.assertNotIn('label="BOSS 采集间隔倍数"', self.source)

    def test_random_delivery_cooldown_is_below_ai_settings(self):
        ai_index = self.source.index('title="AI 设置"')
        anti_monitor_index = self.source.index('title="反监测设置"')
        monitor_index = self.source.index('title="监控设置"')

        self.assertLess(ai_index, anti_monitor_index)
        self.assertLess(anti_monitor_index, monitor_index)
        self.assertNotIn("BOSS 页面访问相关设置同时用于采集和监测", self.source)
        self.assertIn("collection.delivery_cooldown_min_minutes", self.source)
        self.assertIn("collection.delivery_cooldown_max_minutes", self.source)
        self.assertNotIn("collection.delivery_cooldown_minutes", self.source)

    def test_minimum_and_maximum_fields_share_compact_range_controls(self):
        for label in (
            "期望薪资范围（K）",
            "BOSS 风险暂停范围（分钟）",
            "BOSS 采集后投递冷却范围（分钟）",
            "发送间隔范围（秒）",
            "模拟浏览时长范围（秒）",
        ):
            self.assertIn(f'label="{label}"', self.source)

        for retired_label in (
            "BOSS 风险暂停最少分钟",
            "BOSS 风险暂停最多分钟",
            "BOSS 采集后投递冷却最少（分钟）",
            "BOSS 采集后投递冷却最多（分钟）",
            "最短间隔 (秒)",
            "最长间隔 (秒)",
            "浏览最短时长 (秒)",
            "浏览最长时长 (秒)",
        ):
            self.assertNotIn(f'label="{retired_label}"', self.source)

    def test_boss_page_limit_estimate_is_directly_below_max_pages(self):
        max_pages_field = self.source.index('<Field label="最大页数">')
        estimate = self.source.index('理论最多 {bossTheoreticalPages} 页', max_pages_field)
        field_end = self.source.index('</Field>', max_pages_field)

        self.assertLess(estimate, field_end)


class ConfigSchemaTests(unittest.TestCase):
    def setUp(self):
        import json

        self.schema_source = (
            ROOT / "src" / "bosshunter" / "web" / "config_schema.json"
        ).read_text(encoding="utf-8")
        self.schema = json.loads(self.schema_source)

    def test_schema_does_not_include_prefilter_threshold(self):
        self.assertNotIn("prefilter_threshold", self.schema_source)

    def test_schema_adds_allow_internship_after_deal_breakers(self):
        profile = next(section for section in self.schema["sections"] if section["key"] == "profile")
        keys = [field["key"] for field in profile["fields"]]

        self.assertIn("allow_internship", keys)
        self.assertGreater(keys.index("allow_internship"), keys.index("deal_breakers"))

        allow_field = profile["fields"][keys.index("allow_internship")]
        self.assertEqual(allow_field["label"], "接受实习/管培岗位")
        self.assertEqual(allow_field["type"], "switch")
        self.assertIs(allow_field["default"], False)

    def test_schema_defaults_to_disabled_follow_up(self):
        follow_up = next(section for section in self.schema["sections"] if section["key"] == "follow_up")
        enabled = next(field for field in follow_up["fields"] if field["key"] == "enabled")

        self.assertEqual(enabled["label"], "启用自动跟进")
        self.assertEqual(enabled["type"], "switch")
        self.assertIs(enabled["default"], False)


class ScorerPrefilterTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "src" / "bosshunter" / "ai" / "scorer.py").read_text(encoding="utf-8")

    def test_scorer_no_longer_depends_on_prefilter_threshold(self):
        self.assertNotIn("prefilter_threshold", self.source)
        self.assertIn("if qs == 0:", self.source)


if __name__ == "__main__":
    unittest.main()
