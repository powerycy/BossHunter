import json
import tempfile
import time
import unittest
from pathlib import Path
from threading import Thread
from unittest.mock import patch

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


class MonitorIdempotencyAndLimitTests(unittest.TestCase):
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

        self.assertEqual([item["job"]["id"] for item in results], ["two"])


class MonitorRiskTests(unittest.TestCase):
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
