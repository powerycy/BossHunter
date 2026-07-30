import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from bosshunter.db import (
    get_db,
    get_jobs_pending_confirmation,
    get_jobs_ready_to_send,
    get_jobs_with_send_errors,
    insert_job,
    update_job_greeting,
    update_job_score,
    update_job_status,
)
from bosshunter.executor.sender import CHAT_BUTTON_SCRIPT_FOR_TESTS
from bosshunter.executor.sender import _fill_chat_input
from bosshunter.executor.sender import send_greetings
from bosshunter.executor.sender import _send_greeting_once


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
        self.assertNotIn("btn.click()", script)
        self.assertIn("getBoundingClientRect", script)

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
            "url": "https://www.zhipin.com/job_detail/continue-chat.html",
        }
        evaluate_results = [
            '{"success": true}',  # page check
            '{"success": true, "x": 100, "y": 200, "button_text": "继续沟通"}',
            "/job_detail/continue-chat.html",  # DOM click did not navigate
            "/web/geek/chat",  # real click reached chat
        ]

        with patch("bosshunter.executor.sender.new_tab", return_value="target-1"), \
             patch("bosshunter.executor.sender.evaluate", side_effect=evaluate_results), \
             patch("bosshunter.executor.sender._detect_greet_popup", return_value={"success": True, "popup": False}), \
             patch("bosshunter.executor.sender._fill_chat_input", return_value={"success": True}), \
             patch("bosshunter.executor.sender._message_visible", side_effect=[False, True, True, True]), \
             patch("bosshunter.executor.sender.click_at", return_value=True) as click_at, \
             patch("bosshunter.executor.sender.close_tab") as close_tab, \
             patch("bosshunter.executor.sender._sleep_or_stop", return_value=False):
            result, target_id = _send_greeting_once(
                job,
                "您好，我对这个岗位很感兴趣。",
                {"browse_before_greet": False, "_chat_navigation_attempts": 1},
            )

        self.assertIsNone(target_id)
        self.assertTrue(result["success"])
        self.assertEqual(click_at.call_count, 3)
        self.assertEqual(click_at.call_args_list[0].args[1], "100,200")
        fallback_selector = click_at.call_args_list[1].args[1]
        self.assertLess(fallback_selector.index("a.btn-startchat"), fallback_selector.index("btn-startchat-wrap"))
        self.assertEqual(click_at.call_args_list[2].args[1], '.btn-send:not(.disabled)')
        close_tab.assert_called_once_with("target-1")

    def test_send_greeting_waits_for_chat_button_before_failing(self):
        job = {
            "id": "slow-chat-button",
            "url": "https://www.zhipin.com/job_detail/slow-chat-button.html",
        }
        evaluate_results = [
            '{"success": true}',  # page check
            '{"success": false, "error": "no_chat_button"}',
            '{"success": true, "x": 100, "y": 200, "button_text": "继续沟通"}',
            "/web/geek/chat",
        ]

        with patch("bosshunter.executor.sender.new_tab", return_value="target-1"), \
             patch("bosshunter.executor.sender.evaluate", side_effect=evaluate_results) as evaluate_mock, \
             patch("bosshunter.executor.sender._detect_greet_popup", return_value={"success": True, "popup": False}), \
             patch("bosshunter.executor.sender._fill_chat_input", return_value={"success": True}), \
             patch("bosshunter.executor.sender._message_visible", side_effect=[False, True, True, True]), \
             patch("bosshunter.executor.sender.click_at", return_value=True), \
             patch("bosshunter.executor.sender.close_tab") as close_tab, \
             patch("bosshunter.executor.sender._sleep_or_stop", return_value=False):
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

    def test_send_greeting_stops_when_boss_preset_popup_is_visible(self):
        job = {"id": "preset", "url": "https://www.zhipin.com/job_detail/preset.html"}
        evaluate_results = ['{"success": true}', '{"success": true, "x": 100, "y": 200, "button_text": "chat"}']

        with patch("bosshunter.executor.sender.new_tab", return_value="target-1"), \
             patch("bosshunter.executor.sender.evaluate", side_effect=evaluate_results), \
             patch("bosshunter.executor.sender._detect_greet_popup", return_value={"success": True, "popup": True}), \
             patch("bosshunter.executor.sender._sleep_or_stop", return_value=False), \
             patch("bosshunter.executor.sender.click_at") as click_at:
            result, target_id = _send_greeting_once(job, "custom greeting", {"browse_before_greet": False})

        self.assertEqual(result["error"], "preset_greeting_enabled")
        self.assertEqual(target_id, "target-1")
        click_at.assert_called_once_with("target-1", "100,200")

    def test_fill_chat_input_uses_real_editing_events(self):
        evaluate_results = ['{"success": true}', '{"success": true, "disabled": false}']
        with patch("bosshunter.executor.sender.evaluate", side_effect=evaluate_results) as evaluate_mock, \
             patch("bosshunter.executor.sender.type_text", return_value=True) as type_text:
            result = _fill_chat_input("target-1", "custom greeting")

        self.assertTrue(result["success"])
        type_text.assert_called_once_with("target-1", "custom greeting")
        prepare_script = evaluate_mock.call_args_list[0].args[1]
        self.assertIn("input.focus()", prepare_script)
        self.assertIn("deleteContentBackward", prepare_script)

    def test_send_greeting_requires_message_verification(self):
        job = {"id": "unverified", "url": "https://www.zhipin.com/job_detail/unverified.html"}
        evaluate_results = ['{"success": true}', '{"success": true, "x": 100, "y": 200, "button_text": "chat"}', "/web/geek/chat"]

        with patch("bosshunter.executor.sender.new_tab", return_value="target-1"), \
             patch("bosshunter.executor.sender.evaluate", side_effect=evaluate_results), \
             patch("bosshunter.executor.sender._detect_greet_popup", return_value={"success": True, "popup": False}), \
             patch("bosshunter.executor.sender._fill_chat_input", return_value={"success": True}), \
             patch("bosshunter.executor.sender._message_visible", return_value=False), \
             patch("bosshunter.executor.sender.click_at", return_value=True) as click_at, \
             patch("bosshunter.executor.sender._sleep_or_stop", return_value=False):
            result, target_id = _send_greeting_once(
                job,
                "custom greeting",
                {"browse_before_greet": False, "_chat_navigation_attempts": 1, "_send_verification_attempts": 2},
            )

        self.assertEqual(result["error"], "send_not_confirmed")
        self.assertEqual(target_id, "target-1")
        self.assertEqual(click_at.call_count, 2)
        click_at.assert_any_call("target-1", "100,200")
        click_at.assert_any_call("target-1", '.btn-send:not(.disabled)')

    def test_send_greeting_does_not_duplicate_existing_message(self):
        job = {"id": "existing", "url": "https://www.zhipin.com/job_detail/existing.html"}
        evaluate_results = ['{"success": true}', '{"success": true, "x": 100, "y": 200, "button_text": "chat"}', "/web/geek/chat"]

        with patch("bosshunter.executor.sender.new_tab", return_value="target-1"), \
             patch("bosshunter.executor.sender.evaluate", side_effect=evaluate_results), \
             patch("bosshunter.executor.sender._detect_greet_popup", return_value={"success": True, "popup": False}), \
             patch("bosshunter.executor.sender._message_visible", return_value=True), \
             patch("bosshunter.executor.sender._fill_chat_input") as fill_input, \
             patch("bosshunter.executor.sender.click_at") as click_at, \
             patch("bosshunter.executor.sender.close_tab") as close_tab, \
             patch("bosshunter.executor.sender._sleep_or_stop", return_value=False):
            result, target_id = _send_greeting_once(
                job,
                "existing greeting",
                {"browse_before_greet": False, "_chat_navigation_attempts": 1},
            )

        self.assertTrue(result["success"])
        self.assertTrue(result["already_present"])
        self.assertIsNone(target_id)
        fill_input.assert_not_called()
        click_at.assert_called_once_with("target-1", "100,200")
        close_tab.assert_called_once_with("target-1")

    def test_send_greeting_rejects_an_optimistic_bubble_that_disappears(self):
        job = {"id": "vanished", "url": "https://www.zhipin.com/job_detail/vanished.html"}
        evaluate_results = ['{"success": true}', '{"success": true, "x": 100, "y": 200, "button_text": "chat"}', "/web/geek/chat"]

        with patch("bosshunter.executor.sender.new_tab", return_value="target-1"), \
             patch("bosshunter.executor.sender.evaluate", side_effect=evaluate_results), \
             patch("bosshunter.executor.sender._detect_greet_popup", return_value={"success": True, "popup": False}), \
             patch("bosshunter.executor.sender._fill_chat_input", return_value={"success": True}), \
             patch("bosshunter.executor.sender._message_visible", side_effect=[False, True, False]), \
             patch("bosshunter.executor.sender.click_at", return_value=True), \
             patch("bosshunter.executor.sender._sleep_or_stop", return_value=False):
            result, target_id = _send_greeting_once(
                job,
                "optimistic greeting",
                {"browse_before_greet": False, "_chat_navigation_attempts": 1},
            )

        self.assertEqual(result["error"], "send_rejected_after_click")
        self.assertEqual(target_id, "target-1")

    def test_send_greeting_marks_success_only_after_message_appears(self):
        job = {"id": "verified", "url": "https://www.zhipin.com/job_detail/verified.html"}
        evaluate_results = ['{"success": true}', '{"success": true, "x": 100, "y": 200, "button_text": "chat"}', "/web/geek/chat"]

        with patch("bosshunter.executor.sender.new_tab", return_value="target-1"), \
             patch("bosshunter.executor.sender.evaluate", side_effect=evaluate_results), \
             patch("bosshunter.executor.sender._detect_greet_popup", return_value={"success": True, "popup": False}), \
             patch("bosshunter.executor.sender._fill_chat_input", return_value={"success": True}), \
             patch("bosshunter.executor.sender._message_visible", side_effect=[False, True, True, True]), \
             patch("bosshunter.executor.sender.click_at", return_value=True), \
             patch("bosshunter.executor.sender.close_tab") as close_tab, \
             patch("bosshunter.executor.sender._sleep_or_stop", return_value=False):
            result, target_id = _send_greeting_once(
                job,
                "verified greeting",
                {"browse_before_greet": False, "_chat_navigation_attempts": 1},
            )

        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        self.assertIsNone(target_id)
        close_tab.assert_called_once_with("target-1")

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
