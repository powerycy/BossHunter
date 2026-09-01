import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from threading import Thread
from unittest.mock import MagicMock, patch

from bosshunter.db import add_history, get_db, insert_job, update_job_status
from bosshunter.throttle import RequestThrottle


def _job(job_id: str, hr_name: str | None = None) -> dict:
    return {
        "id": job_id,
        "title": f"岗位-{job_id}",
        "company": f"公司-{job_id}",
        "salary": "20-30K",
        "city": "北京",
        "experience": "1-3年",
        "jd": "负责产品运营",
        "hr_name": hr_name or f"HR-{job_id}",
        "hr_title": "招聘者",
        "hr_active": "",
        "company_size": "",
        "company_industry": "",
        "url": f"https://example.com/{job_id}",
    }


class MonitorThrottleTests(unittest.TestCase):
    def test_boss_operation_multiplier_applies_to_monitor_cycle_wait(self):
        from bosshunter.executor import monitor

        config = {
            "collection": {"collection_delay_multiplier": 1.5},
            "monitor": {"interval": 30},
        }

        self.assertEqual(monitor.get_boss_operation_interval_multiplier(config), 1.5)
        self.assertEqual(monitor.get_effective_monitor_interval_minutes(config), 45)

    def test_boss_operation_multiplier_applies_to_monitor_page_requests(self):
        from bosshunter.executor import monitor

        config = {
            "collection": {"collection_delay_multiplier": 1.5},
            "throttle": {
                "interval_min": 60,
                "interval_max": 180,
                "send_windows": [],
            },
        }

        with patch.object(monitor, "RequestThrottle") as request_throttle, \
             patch.object(monitor, "check_replies", return_value=[]), \
             patch.object(monitor, "_check_follow_ups", return_value=0):
            monitor.monitor_and_send_resumes(config)

        request_throttle.assert_called_once_with(90, 270)

    def test_manual_chat_open_can_request_a_foreground_tab(self):
        from bosshunter.executor import monitor

        with patch.object(monitor, "new_tab", return_value="chat-target") as new_tab:
            target_id = monitor._open_monitor_tab(
                "https://www.zhipin.com/web/geek/chat",
                {},
                background=False,
            )

        self.assertEqual(target_id, "chat-target")
        new_tab.assert_called_once_with(
            "https://www.zhipin.com/web/geek/chat",
            background=False,
        )

    def test_manual_reply_suggestion_is_counted_as_pending_not_failed(self):
        from bosshunter.executor import monitor

        item = {"job": {"id": "pending-job"}, "conversation": {}}
        with patch.object(monitor, "check_replies", return_value=[item]), \
             patch.object(monitor, "_handle_conversation", return_value="reply_pending"), \
             patch.object(monitor, "_check_follow_ups", return_value=0):
            summary = monitor.monitor_and_send_resumes(
                {"throttle": {"send_windows": []}, "monitor": {}}
            )

        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["failed"], 0)

    def test_single_detected_reply_processing_disables_every_outbound_path(self):
        from bosshunter.executor import monitor

        with patch.object(
            monitor,
            "monitor_and_send_resumes",
            return_value={"pending": 1},
        ) as run_monitor, patch.object(monitor, "close_monitor_chat_target") as close_target:
            summary = monitor.process_detected_reply(
                "job-one",
                {
                    "monitor": {"auto_reply_hr_questions": True, "max_conversations_per_cycle": 5},
                    "follow_up": {"enabled": True},
                },
            )

        safe_config = run_monitor.call_args.args[0]
        self.assertEqual(summary, {"pending": 1})
        self.assertEqual(safe_config["_monitor_job_ids"], ["job-one"])
        self.assertEqual(safe_config["monitor"]["max_conversations_per_cycle"], 1)
        self.assertFalse(safe_config["monitor"]["auto_reply_hr_questions"])
        self.assertFalse(safe_config["follow_up"]["enabled"])
        self.assertEqual(safe_config["throttle"]["send_windows"], [])
        self.assertTrue(safe_config["_monitor_reuse_chat_tab"])
        close_target.assert_called_once_with(safe_config)

    def test_boss_operation_multiplier_is_bounded_and_tolerates_invalid_values(self):
        from bosshunter.executor import monitor

        self.assertEqual(
            monitor.get_boss_operation_interval_multiplier(
                {"collection": {"collection_delay_multiplier": 99}}
            ),
            5,
        )
        self.assertEqual(
            monitor.get_boss_operation_interval_multiplier(
                {"collection": {"collection_delay_multiplier": "invalid"}}
            ),
            1.5,
        )

    def test_mark_makes_configured_request_interval_effective(self):
        throttle = RequestThrottle(delay_min=60, delay_max=60)

        with patch("bosshunter.throttle.time.time", return_value=100), \
             patch("bosshunter.throttle.random.gauss", return_value=60), \
             patch("bosshunter.throttle.random.random", return_value=1), \
             patch("bosshunter.throttle.time.sleep") as sleep:
            throttle.mark()
            stopped = throttle.wait()

        self.assertFalse(stopped)
        sleep.assert_called_once_with(60)

    def test_every_monitor_tab_open_marks_and_waits_after_the_first(self):
        from bosshunter.executor import monitor

        events = []

        class FakeThrottle:
            has_marked_request = False

            def wait(self, _stop_event=None):
                events.append("wait")
                return False

            def mark(self):
                events.append("mark")
                self.has_marked_request = True

        throttle = FakeThrottle()

        def open_tab(_url, background=False):
            self.assertTrue(background)
            events.append("open")
            return f"target-{events.count('open')}"

        config = {"_monitor_request_throttle": throttle}
        with patch.object(monitor, "new_tab", side_effect=open_tab):
            monitor._open_monitor_tab("https://example.com/one", config)
            monitor._open_monitor_tab("https://example.com/two", config)

        self.assertEqual(events, ["open", "mark", "wait", "open", "mark"])

    def test_web_monitor_reuses_one_live_chat_list_tab(self):
        from bosshunter.executor import monitor

        config = {
            "_monitor_reuse_chat_tab": True,
            "_monitor_runtime_state": {},
        }
        with patch.object(monitor, "_open_monitor_tab", return_value="chat-target") as open_tab, \
             patch.object(monitor, "get_page_info", return_value={"url": "https://www.zhipin.com/web/geek/chat"}), \
             patch.object(monitor, "close_tab") as close_tab:
            first = monitor._get_monitor_chat_target("https://www.zhipin.com/web/geek/chat", config)
            second = monitor._get_monitor_chat_target("https://www.zhipin.com/web/geek/chat", config)
            monitor.close_monitor_chat_target(config)

        self.assertEqual(first, ("chat-target", False))
        self.assertEqual(second, ("chat-target", True))
        open_tab.assert_called_once()
        close_tab.assert_called_once_with("chat-target")

    def test_web_monitor_selects_scanned_row_without_opening_another_page(self):
        from bosshunter.executor import monitor

        conversation = {
            "_chat_target_id": "chat-target",
            "element_index": 3,
            "hr_name": "HR-复用",
            "company": "示例公司",
        }
        monitor._SHARED_MONITOR_TARGETS.add("chat-target")
        try:
            with patch.object(
                monitor,
                "get_page_info",
                return_value={"url": "https://www.zhipin.com/web/geek/chat"},
            ), patch.object(
                monitor,
                "evaluate",
                return_value=json.dumps({"success": True}),
            ) as evaluate, patch.object(monitor, "_inspect_monitor_page"):
                target_id = monitor._open_scanned_conversation(
                    _job("reuse", "HR-复用") | {"company": "示例公司"},
                    {},
                    conversation,
                )
        finally:
            monitor._SHARED_MONITOR_TARGETS.discard("chat-target")

        self.assertEqual(target_id, "chat-target")
        script = evaluate.call_args.args[1]
        self.assertIn("expectedIndex = 3", script)
        self.assertIn("target.click()", script)


class MonitorIdempotencyAndLimitTests(unittest.TestCase):
    def test_single_job_filter_limits_detection_to_the_requested_job(self):
        from bosshunter.executor import monitor

        db = MagicMock()
        jobs = [_job("job-one"), _job("job-two")]
        with patch.object(monitor, "get_db", return_value=db), \
             patch.object(
                 monitor,
                 "get_jobs_by_status",
                 side_effect=lambda _db, status: jobs if status == "sent" else [],
             ), \
             patch.object(monitor, "_check_boss_replies", return_value=[]) as check_boss:
            result = monitor.check_replies({"_monitor_job_ids": ["job-two"]})

        self.assertEqual(result, [])
        checked_jobs = check_boss.call_args.args[1]
        self.assertEqual([job["id"] for job in checked_jobs], ["job-two"])
        db.close.assert_called_once()

    def test_same_unresolved_reply_is_skipped_but_new_hr_message_is_processed(self):
        from bosshunter.executor import monitor

        original_messages = [
            {"sender": "me", "text": "你好，我对岗位感兴趣。"},
            {"sender": "hr", "text": "请介绍一下相关经验。"},
        ]
        new_messages = [
            *original_messages,
            {"sender": "hr", "text": "也请补充一个最近的项目案例。"},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, _job("dedup"))
                update_job_status(db, "dedup", "replied")
                add_history(
                    db,
                    "dedup",
                    "reply_pending",
                    monitor._build_reply_detail(original_messages, "建议回复"),
                )
            finally:
                db.close()

            def open_db():
                return get_db(db_path)

            common = [
                patch.object(monitor, "get_db", side_effect=open_db),
                patch.object(monitor, "_open_conversation", return_value="target-1"),
                patch.object(monitor, "close_tab"),
                patch.object(monitor.time, "sleep"),
            ]
            with common[0], common[1] as open_conversation, common[2], common[3], \
                 patch.object(monitor, "evaluate", return_value=json.dumps(original_messages)), \
                 patch.object(monitor, "_generate_auto_reply") as generate_reply:
                same_action = monitor._handle_conversation(_job("dedup") | {"status": "replied"}, {"monitor": {}})

            self.assertEqual(same_action, "skipped_existing_pending")
            open_conversation.assert_called_once()
            generate_reply.assert_not_called()

            with patch.object(monitor, "get_db", side_effect=open_db), \
                 patch.object(monitor, "_open_conversation", return_value="target-2"), \
                 patch.object(monitor, "close_tab"), \
                 patch.object(monitor.time, "sleep"), \
                 patch.object(monitor, "evaluate", return_value=json.dumps(new_messages)), \
                 patch.object(monitor, "_generate_auto_reply", return_value="新的建议回复") as generate_reply:
                new_action = monitor._handle_conversation(_job("dedup") | {"status": "replied"}, {"monitor": {}})

            verify_db = get_db(db_path)
            try:
                pending_count = verify_db.execute(
                    "SELECT COUNT(*) FROM history WHERE job_id = ? AND action = 'reply_pending'",
                    ("dedup",),
                ).fetchone()[0]
            finally:
                verify_db.close()

        self.assertEqual(new_action, "reply_pending")
        self.assertEqual(pending_count, 2)
        generate_reply.assert_called_once()

    def test_same_auto_reply_is_not_sent_twice_but_later_hr_turn_is_processed(self):
        from bosshunter.executor import monitor

        original_messages = [
            {"sender": "me", "text": "你好，我对岗位感兴趣。"},
            {"sender": "hr", "text": "请介绍一下相关经验。"},
        ]
        later_messages = [
            *original_messages,
            {"sender": "hr", "text": "也请补充一个最近的项目案例。"},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, _job("auto-round"))
                update_job_status(db, "auto-round", "replied")
                add_history(
                    db,
                    "auto-round",
                    "auto_replied",
                    monitor._build_reply_detail(
                        original_messages,
                        "第一轮自动回复",
                        "auto_replied.v1",
                    ),
                )
            finally:
                db.close()

            def open_db():
                return get_db(db_path)

            config = {"monitor": {"auto_reply_hr_questions": True}}
            with patch.object(monitor, "get_db", side_effect=open_db), \
                 patch.object(monitor, "_open_conversation", return_value="same-target"), \
                 patch.object(monitor, "_wait_or_stop", return_value=False), \
                 patch.object(monitor, "evaluate", return_value=json.dumps(original_messages)), \
                 patch.object(monitor, "close_tab"), \
                 patch.object(monitor, "_generate_auto_reply") as generate_reply, \
                 patch.object(monitor, "_send_message_in_chat") as send_message:
                same_action = monitor._handle_conversation(
                    _job("auto-round") | {"status": "replied"},
                    config,
                )

            self.assertEqual(same_action, "skipped_handled_reply")
            generate_reply.assert_not_called()
            send_message.assert_not_called()

            with patch.object(monitor, "get_db", side_effect=open_db), \
                 patch.object(monitor, "_open_conversation", return_value="later-target"), \
                 patch.object(monitor, "_wait_or_stop", return_value=False), \
                 patch.object(monitor, "evaluate", return_value=json.dumps(later_messages)), \
                 patch.object(monitor, "close_tab"), \
                 patch.object(monitor, "_generate_auto_reply", return_value="第二轮自动回复") as generate_reply, \
                 patch.object(monitor, "_send_message_in_chat", return_value=True) as send_message:
                later_action = monitor._handle_conversation(
                    _job("auto-round") | {"status": "replied"},
                    config,
                )

            verify_db = get_db(db_path)
            try:
                auto_reply_count = verify_db.execute(
                    "SELECT COUNT(*) FROM history WHERE job_id = ? AND action = 'auto_replied'",
                    ("auto-round",),
                ).fetchone()[0]
            finally:
                verify_db.close()

        self.assertEqual(later_action, "auto_replied")
        self.assertEqual(auto_reply_count, 2)
        generate_reply.assert_called_once()
        send_message.assert_called_once_with("later-target", "第二轮自动回复")

    def test_chat_list_skips_same_pending_before_opening_and_caps_new_items(self):
        from bosshunter.executor import monitor

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "bosshunter.db"
            db = get_db(db_path)
            try:
                for job_id in ("one", "two", "three"):
                    insert_job(db, _job(job_id))
                    update_job_status(db, job_id, "sent")
                messages = [{"sender": "hr", "text": "旧问题"}]
                add_history(
                    db,
                    "one",
                    "reply_pending",
                    monitor._build_reply_detail(
                        messages,
                        "旧建议",
                        conversation={"last_message": "旧问题"},
                    ),
                )
                update_job_status(db, "one", "replied")
            finally:
                db.close()

            conversations = [
                {
                    "hr_name": f"HR-{job_id}",
                    "company": f"公司-{job_id}",
                    "last_message": "旧问题" if job_id == "one" else f"新问题-{job_id}",
                    "has_reply": True,
                    "has_unread": False,
                }
                for job_id in ("one", "two", "three")
            ]

            def open_db():
                return get_db(db_path)

            with patch.object(monitor, "get_db", side_effect=open_db), \
                 patch.object(monitor, "_open_monitor_tab", return_value="chat-target"), \
                 patch.object(monitor, "_wait_or_stop", return_value=False), \
                 patch.object(monitor, "_wait_for_page_or_stop", return_value=True), \
                 patch.object(monitor, "evaluate", return_value=json.dumps(conversations)), \
                 patch.object(monitor, "close_tab"):
                results = monitor.check_replies({"monitor": {"max_conversations_per_cycle": 1}})

            verify_db = get_db(db_path)
            try:
                detected_actions = [
                    row["action"]
                    for row in verify_db.execute(
                        "SELECT action FROM history WHERE job_id = ? ORDER BY id",
                        ("two",),
                    ).fetchall()
                ]
            finally:
                verify_db.close()

        self.assertEqual([item["job"]["id"] for item in results], ["two"])
        self.assertEqual(detected_actions, ["hr_reply_detected"])

    def test_web_chat_scan_carries_shared_target_into_processing(self):
        from bosshunter.executor import monitor

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, _job("shared"))
                update_job_status(db, "shared", "sent")
            finally:
                db.close()

            conversation = {
                "element_index": 2,
                "hr_name": "HR-shared",
                "company": "公司-shared",
                "last_message": "方便介绍一下相关经验吗？",
                "has_reply": True,
                "has_unread": True,
            }

            def open_db():
                return get_db(db_path)

            config = {
                "_monitor_reuse_chat_tab": True,
                "_monitor_runtime_state": {},
                "monitor": {"max_conversations_per_cycle": 1},
            }
            with patch.object(monitor, "get_db", side_effect=open_db), \
                 patch.object(monitor, "_open_monitor_tab", return_value="chat-target"), \
                 patch.object(monitor, "_wait_or_stop", return_value=False), \
                 patch.object(monitor, "_wait_for_page_or_stop", return_value=True), \
                 patch.object(monitor, "evaluate", return_value=json.dumps([conversation])), \
                 patch.object(monitor, "close_tab"):
                results = monitor.check_replies(config)

            monitor._SHARED_MONITOR_TARGETS.discard("chat-target")

        self.assertEqual(results[0]["conversation"]["_chat_target_id"], "chat-target")

    def test_chat_list_includes_unrecorded_outbound_reply_but_skips_plain_sent_job(self):
        from bosshunter.executor import monitor

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, _job("greeting"))
                insert_job(db, _job("manual"))
                update_job_status(db, "greeting", "sent")
                update_job_status(db, "manual", "replied")
            finally:
                db.close()

            conversations = [
                {
                    "hr_name": f"HR-{job_id}",
                    "company": f"公司-{job_id}",
                    "last_message": message,
                    "last_direction": "me",
                    "is_our_message": True,
                    "has_reply": False,
                }
                for job_id, message in (
                    ("greeting", "您好，我对岗位很感兴趣。"),
                    ("manual", "可以，我补充一下相关经历。"),
                )
            ]

            def open_db():
                return get_db(db_path)

            with patch.object(monitor, "get_db", side_effect=open_db), \
                 patch.object(monitor, "_open_monitor_tab", return_value="chat-target"), \
                 patch.object(monitor, "_wait_or_stop", return_value=False), \
                 patch.object(monitor, "_wait_for_page_or_stop", return_value=True), \
                 patch.object(monitor, "evaluate", return_value=json.dumps(conversations)), \
                 patch.object(monitor, "close_tab"):
                results = monitor.check_replies({"monitor": {"max_conversations_per_cycle": 5}})

        self.assertEqual([item["job"]["id"] for item in results], ["manual"])


class MonitorRiskTests(unittest.TestCase):
    def _detect_risk_in_dom(self, *cases):
        from bosshunter.executor import monitor

        runner = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "bosshunter"
            / "web"
            / "frontend"
            / "tests"
            / "monitor_risk_dom_runner.mjs"
        )
        payload = [
            {
                **case,
                "script": monitor.JS_DETECT_MONITOR_RISK,
            }
            for case in cases
        ]
        result = subprocess.run(
            ["node", str(runner)],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        )
        return json.loads(result.stdout)

    def test_risk_detection_ignores_hidden_template_and_obscured_content(self):
        results = self._detect_risk_in_dom(
            {
                "body": '<div style="display:none"><div class="captcha" data-top>请完成验证</div></div>',
                "topSelector": "[data-top]",
            },
            {
                "body": '<div style="visibility:hidden"><span data-top>操作过于频繁</span></div>',
                "topSelector": "[data-top]",
            },
            {
                "body": '<div style="opacity:0"><span data-top>访问被拒绝</span></div>',
                "topSelector": "[data-top]",
            },
            {
                "body": '<div class="captcha">请完成验证</div><div id="mask" data-top></div>',
                "topSelector": "#mask",
            },
        )

        self.assertEqual(results, [{"risk": None}] * 4)

    def test_risk_detection_keeps_visible_and_url_title_safety_signals(self):
        results = self._detect_risk_in_dom(
            {
                "body": '<div class="captcha" data-top>请完成验证</div>',
            },
            {
                "body": '<p data-top>操作过于频繁，请稍后再试</p>',
            },
            {
                "body": '<main data-top>403 Forbidden</main>',
            },
            {
                "title": "访问被拒绝",
                "body": '<main data-top>普通页面</main>',
            },
            {
                "body": '<div style="display:none"><span data-top>请完成验证</span></div>',
                "url": "https://www.zhipin.com/security-check",
                "topSelector": "[data-top]",
            },
        )

        self.assertEqual(
            results,
            [
                {"risk": "captcha"},
                {"risk": "rate_limit"},
                {"risk": "blocked"},
                {"risk": "blocked"},
                {"risk": "captcha"},
            ],
        )

    def test_captcha_stops_cycle_and_records_only_safe_risk_detail(self):
        from bosshunter.executor import monitor

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, _job("risk"))
                update_job_status(db, "risk", "sent")
            finally:
                db.close()

            def open_db():
                return get_db(db_path)

            with patch.object(monitor, "get_db", side_effect=open_db), \
                 patch.object(monitor, "_open_monitor_tab", return_value="chat-target"), \
                 patch.object(monitor, "_wait_or_stop", return_value=False), \
                 patch.object(monitor, "_wait_for_page_or_stop", return_value=True), \
                 patch.object(monitor, "evaluate", return_value=json.dumps({"risk": "captcha"})), \
                 patch.object(monitor, "close_tab"):
                summary = monitor.monitor_and_send_resumes({"throttle": {"send_windows": []}, "monitor": {}})

            verify_db = get_db(db_path)
            try:
                events = [dict(row) for row in verify_db.execute("SELECT event_type, detail FROM risk_events").fetchall()]
            finally:
                verify_db.close()

        self.assertEqual(summary["stop_reason"], "captcha")
        self.assertEqual(events, [{"event_type": "monitor_captcha", "detail": "监测检测到验证码，已停止"}])

    def test_consecutive_page_failures_stop_at_configured_threshold(self):
        from bosshunter.executor import monitor

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "bosshunter.db"

            def open_db():
                return get_db(db_path)

            guard = monitor.MonitorSafetyGuard({"monitor": {"max_consecutive_page_failures": 2}})
            with patch.object(monitor, "get_db", side_effect=open_db):
                guard.record_page_failure()
                with self.assertRaises(monitor.MonitorRiskDetected) as raised:
                    guard.record_page_failure()

            verify_db = get_db(db_path)
            try:
                event = dict(verify_db.execute("SELECT event_type, detail FROM risk_events").fetchone())
            finally:
                verify_db.close()

        self.assertEqual(raised.exception.kind, "consecutive_page_failures")
        self.assertEqual(event["event_type"], "monitor_consecutive_page_failures")


class FullFlowMonitorCooldownTests(unittest.TestCase):
    def test_full_flow_initial_cooldown_is_cancellable_before_first_scan(self):
        from bosshunter.web.tasks import WorkbenchTask, wait_for_initial_monitor_cooldown

        task = WorkbenchTask(id="cooldown", mode="full", label="运行全流程")
        result = []

        worker = Thread(
            target=lambda: result.append(
                wait_for_initial_monitor_cooldown(
                    task,
                    {"monitor": {"initial_cooldown_minutes": 1}},
                    lambda current_task, message: current_task.logs.append(message),
                )
            )
        )
        worker.start()
        deadline = time.monotonic() + 1
        while not task.logs and time.monotonic() < deadline:
            time.sleep(0.01)
        task.stop_requested.set()
        worker.join(timeout=0.5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [True])
        self.assertIn("首次监测冷却已取消", task.logs)


if __name__ == "__main__":
    unittest.main()
