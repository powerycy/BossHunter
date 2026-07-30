import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from bosshunter.db import (
    get_db,
    get_funnel_stats,
    get_jobs_pending_confirmation,
    get_jobs_ready_to_send,
    get_jobs_with_send_errors,
    insert_job,
    reset_ai_filtered_jobs,
    update_job_greeting,
    update_job_score,
    update_job_status,
)
from bosshunter.executor.sender import CHAT_BUTTON_SCRIPT_FOR_TESTS
from bosshunter.executor.sender import send_greetings
from bosshunter.executor.sender import _send_greeting_once
from bosshunter.executor.sender import _wait_for_chat_page


def _job(job_id: str, title: str = "Engineer") -> dict:
    return {
        "id": job_id,
        "title": title,
        "company": "Example",
        "salary": "10-20K",
        "city": "Beijing",
        "experience": "1-3 years",
        "jd": "Build product features",
        "hr_name": "HR",
        "hr_title": "Recruiter",
        "hr_active": "",
        "company_size": "",
        "company_industry": "",
        "url": "https://example.com/job",
    }


class JobSelectionTests(unittest.TestCase):
    def test_chat_button_script_prefers_real_anchor_over_visible_wrapper(self):
        script = CHAT_BUTTON_SCRIPT_FOR_TESTS

        self.assertIn("redirect-url", script)
        self.assertIn("data-url", script)

        redirect_pos = script.index("redirect-url")
        wrapper_pos = script.index("btn-startchat-wrap")
        self.assertLess(redirect_pos, wrapper_pos)

    def test_send_greeting_reports_unavailable_job_page_before_clicking_chat(self):
        job = {
            "id": "gone",
            "url": "https://www.zhipin.com/job_detail/gone.html",
        }

        with patch("bosshunter.executor.sender.new_tab", return_value="target-1"), \
             patch("bosshunter.executor.sender.evaluate", return_value='{"success": false, "error": "job_page_unavailable", "history_detail": "岗位页面不存在或已下架", "skip_backoff": true}'), \
             patch("bosshunter.executor.sender.close_tab") as close_tab, \
             patch("bosshunter.executor.sender.time.sleep"):
            result, target_id = _send_greeting_once(
                job,
                "您好，我对这个岗位很感兴趣。",
                {"browse_before_greet": False},
            )

        self.assertIsNone(target_id)
        self.assertEqual(result["error"], "job_page_unavailable")
        self.assertEqual(result["history_detail"], "岗位页面不存在或已下架")
        close_tab.assert_called_once_with("target-1")

    def test_send_greeting_uses_real_click_fallback_when_chat_button_does_not_navigate(self):
        job = {
            "id": "continue-chat",
            "company": "Example",
            "title": "Engineer",
            "url": "https://www.zhipin.com/job_detail/continue-chat.html",
        }

        with patch("bosshunter.executor.sender.new_tab", return_value="target-1"), \
             patch("bosshunter.executor.sender.evaluate", return_value='{"success": true}'), \
             patch("bosshunter.executor.sender._click_chat_button", return_value={"success": True, "button_text": "继续沟通"}), \
             patch("bosshunter.executor.sender._detect_greet_popup", return_value={"success": True, "popup": False}), \
             patch("bosshunter.executor.sender._wait_for_chat_page", side_effect=[
                 {"success": False, "error": "chat_navigation_timeout"},
                 {"success": True, "target_id": "target-1"},
             ]), \
             patch("bosshunter.executor.sender._message_delivery_state", side_effect=["missing", "delivered", "delivered"]), \
             patch("bosshunter.executor.sender._fill_chat_input", return_value={"success": True, "disabled": False}), \
             patch("bosshunter.executor.sender.click_at", return_value=True) as click_at, \
             patch("bosshunter.executor.sender.close_tab") as close_tab, \
             patch("bosshunter.executor.sender.time.sleep"):
            result, target_id = _send_greeting_once(
                job,
                "您好，我对这个岗位很感兴趣。",
                {"browse_before_greet": False, "_chat_navigation_attempts": 1},
            )

        self.assertIsNone(target_id)
        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        self.assertEqual(click_at.call_count, 2)
        fallback_selector = click_at.call_args_list[0].args[1]
        self.assertLess(fallback_selector.index("a.btn-startchat"), fallback_selector.index("btn-startchat-wrap"))
        close_tab.assert_called_once_with("target-1")

    def test_send_greeting_waits_for_chat_button_before_failing(self):
        job = {
            "id": "slow-chat-button",
            "company": "Example",
            "title": "Engineer",
            "url": "https://www.zhipin.com/job_detail/slow-chat-button.html",
        }
        evaluate_results = [
            '{"success": true}',  # page check
            '{"success": false, "error": "no_chat_button"}',
            '{"success": true, "button_text": "继续沟通"}',
        ]

        with patch("bosshunter.executor.sender.new_tab", return_value="target-1"), \
             patch("bosshunter.executor.sender.evaluate", side_effect=evaluate_results) as evaluate_mock, \
             patch("bosshunter.executor.sender._detect_greet_popup", return_value={"success": True, "popup": False}), \
             patch("bosshunter.executor.sender._wait_for_chat_page", return_value={"success": True, "target_id": "target-1"}), \
             patch("bosshunter.executor.sender._message_delivery_state", side_effect=["missing", "delivered", "delivered"]), \
             patch("bosshunter.executor.sender._fill_chat_input", return_value={"success": True, "disabled": False}), \
             patch("bosshunter.executor.sender.click_at", return_value=True), \
             patch("bosshunter.executor.sender.close_tab") as close_tab, \
             patch("bosshunter.executor.sender.time.sleep"):
            result, target_id = _send_greeting_once(
                job,
                "您好，我对这个岗位很感兴趣。",
                {
                    "browse_before_greet": False,
                    "_chat_button_attempts": 2,
                    "_chat_navigation_attempts": 1,
                },
            )

        self.assertIsNone(target_id)
        self.assertTrue(result["success"])
        self.assertEqual(evaluate_mock.call_count, len(evaluate_results))
        close_tab.assert_called_once_with("target-1")

    def test_send_greeting_stops_when_platform_preset_greeting_is_enabled(self):
        job = {
            "id": "preset-popup",
            "company": "Example",
            "title": "Engineer",
            "url": "https://www.zhipin.com/job_detail/preset-popup.html",
        }

        with patch("bosshunter.executor.sender.new_tab", return_value="target-1"), \
             patch("bosshunter.executor.sender.evaluate", return_value='{"success": true}'), \
             patch("bosshunter.executor.sender._click_chat_button", return_value={"success": True}), \
             patch(
                 "bosshunter.executor.sender._detect_greet_popup",
                 return_value={"success": True, "popup": True, "kind": "preset_greeting"},
             ), \
             patch("bosshunter.executor.sender._fill_chat_input") as fill_input, \
             patch("bosshunter.executor.sender.time.sleep"):
            result, target_id = _send_greeting_once(
                job,
                "您好，我对这个岗位很感兴趣。",
                {"browse_before_greet": False},
            )

        self.assertEqual(target_id, "target-1")
        self.assertEqual(result["error"], "preset_greeting_enabled")
        self.assertTrue(result["skip_backoff"])
        fill_input.assert_not_called()

    def test_wait_for_chat_page_rejects_a_different_job_conversation(self):
        job = {
            "id": "expected-job",
            "company": "Expected Company",
            "title": "Expected Role",
        }

        with patch("bosshunter.executor.sender.evaluate", return_value="/web/geek/chat"), \
             patch("bosshunter.executor.sender._chat_target_matches_job", return_value=False) as matches_job, \
             patch("bosshunter.executor.sender.get_page_targets", return_value=[]), \
             patch("bosshunter.executor.sender.time.sleep"):
            result = _wait_for_chat_page("target-1", None, attempts=1, job=job)

        self.assertEqual(result["error"], "chat_navigation_timeout")
        matches_job.assert_called_once_with("target-1", job)

    def test_send_greeting_adopts_matching_new_chat_tab_and_closes_old_tab(self):
        job = {
            "id": "new-chat-tab",
            "company": "Example",
            "title": "Engineer",
            "url": "https://www.zhipin.com/job_detail/new-chat-tab.html",
        }

        with patch("bosshunter.executor.sender.new_tab", return_value="job-target"), \
             patch("bosshunter.executor.sender.evaluate", return_value='{"success": true}'), \
             patch("bosshunter.executor.sender._click_chat_button", return_value={"success": True}), \
             patch("bosshunter.executor.sender._detect_greet_popup", return_value={"success": True, "popup": False}), \
             patch(
                 "bosshunter.executor.sender._wait_for_chat_page",
                 return_value={"success": True, "target_id": "chat-target", "opened_new_tab": True},
             ), \
             patch("bosshunter.executor.sender._message_delivery_state", side_effect=["missing", "delivered", "delivered"]), \
             patch("bosshunter.executor.sender._fill_chat_input", return_value={"success": True, "disabled": False}), \
             patch("bosshunter.executor.sender.click_at", return_value=True), \
             patch("bosshunter.executor.sender.close_tab") as close_tab, \
             patch("bosshunter.executor.sender.time.sleep"):
            result, target_id = _send_greeting_once(
                job,
                "您好，我对这个岗位很感兴趣。",
                {"browse_before_greet": False},
            )

        self.assertIsNone(target_id)
        self.assertTrue(result["verified"])
        self.assertEqual(
            [call.args[0] for call in close_tab.call_args_list],
            ["job-target", "chat-target"],
        )

    def test_send_greetings_reopens_job_page_once_when_chat_input_missing(self):
        job = _job("retry-chat-input")
        job["greeting"] = "您好，我对这个岗位很感兴趣。"

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, job)
                update_job_status(db, job["id"], "ready")
                update_job_greeting(db, job["id"], job["greeting"])
            finally:
                db.close()

            attempts = [
                ({"success": False, "error": "no_chat_input"}, "target-1"),
                ({"success": True}, None),
            ]

            with patch("bosshunter.db.DB_PATH", db_path), \
                 patch("bosshunter.executor.sender.should_take_day_off", return_value=False), \
                 patch("bosshunter.executor.sender.SendWindowChecker.is_active", return_value=True), \
                 patch("bosshunter.executor.sender._send_greeting_once", side_effect=attempts) as send_once, \
                 patch("bosshunter.executor.sender.close_tab") as close_tab:
                sent = send_greetings({"throttle": {"daily_limit": 10}}, force=True)

            self.assertEqual(sent, 1)
            self.assertEqual(send_once.call_count, 2)
            close_tab.assert_called_once_with("target-1")

    def test_pending_confirmation_excludes_jobs_with_greetings(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            try:
                insert_job(db, _job("scored"))
                update_job_score(db, "scored", 88, "good match")
                update_job_status(db, "scored", "ready")

                insert_job(db, _job("sendable"))
                update_job_score(db, "sendable", 92, "great match")
                update_job_status(db, "sendable", "ready")
                update_job_greeting(db, "sendable", "Hi, this role looks like a strong fit.")

                jobs = get_jobs_pending_confirmation(db)
            finally:
                db.close()

        self.assertEqual([job["id"] for job in jobs], ["scored"])

    def test_rescore_reset_only_requeues_jobs_filtered_by_ai_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            try:
                for job_id, reason in (
                    ("ai-filtered", "经验匹配度不足"),
                    ("prefiltered", "预筛不通过: 命中一票否决词"),
                    ("ai-failed", "AI评分失败: 服务暂时不可用"),
                    ("ai-failed-spaced", "AI 评分失败: 服务暂时不可用"),
                ):
                    insert_job(db, _job(job_id))
                    update_job_score(db, job_id, 42, reason)
                    update_job_status(db, job_id, "filtered")

                reset_count = reset_ai_filtered_jobs(db)
                rows = {
                    row["id"]: dict(row)
                    for row in db.execute(
                        "SELECT id, status, score, score_reason FROM jobs ORDER BY id"
                    ).fetchall()
                }
            finally:
                db.close()

        self.assertEqual(reset_count, 1)
        self.assertEqual(rows["ai-filtered"]["status"], "pending")
        self.assertEqual(rows["ai-filtered"]["score"], 0)
        self.assertIsNone(rows["ai-filtered"]["score_reason"])
        self.assertEqual(rows["prefiltered"]["status"], "filtered")
        self.assertEqual(rows["ai-failed"]["status"], "filtered")
        self.assertEqual(rows["ai-failed-spaced"]["status"], "filtered")

    def test_funnel_counts_ai_low_scores_but_excludes_prefilter_and_ai_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            try:
                for job_id, reason in (
                    ("ai-low-score", "经验匹配度不足"),
                    ("prefiltered", "预筛不通过: 命中一票否决词"),
                    ("ai-failed", "AI评分失败: 服务暂时不可用"),
                ):
                    insert_job(db, _job(job_id))
                    update_job_score(db, job_id, 42, reason)
                    update_job_status(db, job_id, "filtered")

                insert_job(db, _job("ai-passed"))
                update_job_score(db, "ai-passed", 88, "匹配")
                update_job_status(db, "ai-passed", "ready")
                stats = get_funnel_stats(db)
            finally:
                db.close()

        self.assertEqual(stats["AI评分"], 2)

    def test_ready_to_send_requires_a_non_empty_greeting(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            try:
                insert_job(db, _job("no-greeting"))
                update_job_status(db, "no-greeting", "ready")

                insert_job(db, _job("blank-greeting"))
                update_job_status(db, "blank-greeting", "ready")
                update_job_greeting(db, "blank-greeting", "   ")

                insert_job(db, _job("sendable"))
                update_job_status(db, "sendable", "ready")
                update_job_greeting(db, "sendable", "Hi, this role looks like a strong fit.")

                insert_job(db, _job("approved"))
                update_job_status(db, "approved", "approved")
                update_job_greeting(db, "approved", "Not ready for send status yet.")

                jobs = get_jobs_ready_to_send(db)
            finally:
                db.close()

        self.assertCountEqual([job["id"] for job in jobs], ["approved", "sendable"])

    def test_send_errors_return_only_jobs_with_generated_greetings(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            try:
                insert_job(db, _job("send-failed"))
                update_job_status(db, "send-failed", "error")
                update_job_greeting(db, "send-failed", "Hi, this role looks like a strong fit.")

                insert_job(db, _job("generation-failed"))
                update_job_status(db, "generation-failed", "error")

                insert_job(db, _job("sendable"))
                update_job_status(db, "sendable", "ready")
                update_job_greeting(db, "sendable", "Ready to send.")

                jobs = get_jobs_with_send_errors(db)
            finally:
                db.close()

        self.assertEqual([job["id"] for job in jobs], ["send-failed"])

    def test_send_greetings_force_bypasses_send_window_restriction(self):
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, _job("sendable"))
                update_job_status(db, "sendable", "ready")
                update_job_greeting(db, "sendable", "Ready to send.")
            finally:
                db.close()

            config = {
                "throttle": {
                    "send_windows": ["09:00-16:00"],
                    "daily_limit": 30,
                    "interval_min": 0,
                    "interval_max": 0,
                    "browse_before_greet": False,
                }
            }

            # Act
            with patch("bosshunter.db.DB_PATH", db_path), \
                 patch("bosshunter.throttle.datetime") as mock_datetime, \
                 patch("bosshunter.executor.sender._send_greeting_once", return_value=({"success": True}, None)):
                mock_datetime.now.return_value = datetime(2026, 6, 19, 20, 0)
                sent = send_greetings(config, force=True)

            verify_db = get_db(db_path)
            try:
                status = verify_db.execute("SELECT status FROM jobs WHERE id = 'sendable'").fetchone()["status"]
                outside_window_events = verify_db.execute(
                    "SELECT COUNT(*) AS c FROM risk_events WHERE event_type = 'outside_window'"
                ).fetchone()["c"]
            finally:
                verify_db.close()

        # Assert
        self.assertEqual(sent, 1)
        self.assertEqual(status, "sent")
        self.assertEqual(outside_window_events, 0)


if __name__ == "__main__":
    unittest.main()
