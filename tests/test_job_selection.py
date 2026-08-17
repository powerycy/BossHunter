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
from bosshunter.executor.sender import _chat_target_matches_job
from bosshunter.executor.sender import _confirm_preset_greeting
from bosshunter.executor.sender import _handle_greet_popup
from bosshunter.executor.sender import _message_delivery_state
from bosshunter.executor.sender import _submitted_message_looks_accepted
from bosshunter.executor.sender import send_greetings
from bosshunter.executor.sender import _send_greeting_once
from bosshunter.executor.sender import _submit_chat_message_background
from bosshunter.executor.sender import _submit_startchat_greeting
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
        self.assertIn("btn.click()", script)
        self.assertIn("interaction: 'dom_click'", script)

        redirect_pos = script.index("redirect-url")
        wrapper_pos = script.index("btn-startchat-wrap")
        self.assertLess(redirect_pos, wrapper_pos)

    def test_preset_confirmation_uses_background_dom_click(self):
        with patch(
            "bosshunter.executor.sender.evaluate",
            return_value='{"success": true, "action": "preset_confirmed"}',
        ) as evaluate_mock, patch("bosshunter.executor.sender.click_at") as click_at:
            result = _confirm_preset_greeting("target-1")

        self.assertEqual(result["action"], "preset_confirmed")
        self.assertIn("button.click()", evaluate_mock.call_args.args[1])
        click_at.assert_not_called()

    def test_startchat_submission_uses_trusted_typing_and_real_send_message_click(self):
        greeting = "您好，我的经历和岗位需求比较匹配。"
        with patch(
            "bosshunter.executor.sender.evaluate",
            side_effect=[
                '{"success": true, "x": 10, "y": 20}',
                '{"success": true, "x": 30, "y": 40}',
            ],
        ) as evaluate_mock, patch(
            "bosshunter.executor.sender.click_at",
            return_value=True,
        ) as click_at, patch(
            "bosshunter.executor.sender.press_key",
            return_value=True,
        ) as press_key, patch(
            "bosshunter.executor.sender.type_text",
            return_value=True,
        ) as type_text:
            result = _submit_startchat_greeting("target-1", greeting)

        self.assertEqual(result["action"], "first_contact_submitted")
        submit_script = evaluate_mock.call_args_list[1].args[1]
        self.assertIn(".send-message", submit_script)
        self.assertEqual(
            [item.args for item in click_at.call_args_list],
            [("target-1", "10,20"), ("target-1", "30,40")],
        )
        self.assertEqual(
            [item.args for item in press_key.call_args_list],
            [("target-1", "SelectAll"), ("target-1", "Backspace")],
        )
        type_text.assert_called_once_with("target-1", greeting, human=True)

    def test_original_chat_submit_path_remains_available(self):
        greeting = "您好，我的经历和岗位需求比较匹配。"
        with patch(
            "bosshunter.executor.sender.evaluate",
            return_value='{"success": true, "action": "chat_submitted_background"}',
        ) as evaluate_mock:
            result = _submit_chat_message_background("target-1", greeting)

        self.assertEqual(result["action"], "chat_submitted_background")
        script = evaluate_mock.call_args.args[1]
        self.assertIn("vue.handleSubmit()", script)
        self.assertIn("enableSubmit", script)

    def test_delivery_check_matches_bubble_text_with_status_labels(self):
        greeting = "您好，我的经历和岗位需求比较匹配。"
        with patch(
            "bosshunter.executor.sender.evaluate",
            return_value='{"success": true, "state": "delivered"}',
        ) as evaluate_mock:
            state = _message_delivery_state("target-1", greeting)

        self.assertEqual(state, "delivered")
        script = evaluate_mock.call_args.args[1]
        self.assertIn(".message-content", script)
        self.assertIn("text.includes(expectedText)", script)
        self.assertIn("发送中|已读|未读|送达|发送成功|重试|重新发送", script)

    def test_send_acceptance_fallback_requires_cleared_input_without_failure(self):
        greeting = "您好，我的经历和岗位需求比较匹配。"
        with patch(
            "bosshunter.executor.sender.evaluate",
            return_value='{"success": true, "accepted": true, "inputCleared": true, "hasFailedOwnMessage": false}',
        ) as evaluate_mock:
            accepted = _submitted_message_looks_accepted("target-1", greeting)

        self.assertTrue(accepted)
        script = evaluate_mock.call_args.args[1]
        self.assertIn("inputCleared && !hasFailedOwnMessage", script)
        self.assertIn("status-error", script)

    def test_startchat_popup_reuses_chat_redirect_without_foreground_input(self):
        click_result = {"redirectUrl": "/web/geek/chat?jobId=first-contact"}
        with patch(
            "bosshunter.executor.sender._detect_greet_popup",
            return_value={"success": True, "popup": True, "kind": "startchat_dialog"},
        ), patch(
            "bosshunter.executor.sender._navigate_to_chat_redirect",
            return_value=True,
        ) as redirect, patch(
            "bosshunter.executor.sender._submit_startchat_greeting",
        ) as foreground_submit:
            result = _handle_greet_popup(
                "target-1",
                "您好，我对这个岗位很感兴趣。",
                click_result,
            )

        self.assertEqual(result["action"], "startchat_redirected")
        redirect.assert_called_once_with("target-1", click_result)
        foreground_submit.assert_not_called()

    def test_send_greeting_reports_unavailable_job_page_before_clicking_chat(self):
        job = {
            "id": "gone",
            "url": "https://www.zhipin.com/job_detail/gone.html",
        }

        with patch("bosshunter.executor.sender.new_tab", return_value="target-1") as new_tab, \
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
        new_tab.assert_called_once_with(job["url"], background=True)
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
             patch("bosshunter.executor.sender._submit_chat_message_background", return_value={"success": False}), \
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
             patch("bosshunter.executor.sender._submit_chat_message_background", return_value={"success": False}), \
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

    def test_send_greeting_accepts_cleared_input_when_echo_is_missing(self):
        job = {
            "id": "accepted-without-echo",
            "company": "Example",
            "title": "Engineer",
            "url": "https://www.zhipin.com/job_detail/accepted-without-echo.html",
        }

        with patch("bosshunter.executor.sender.new_tab", return_value="target-1"), \
             patch("bosshunter.executor.sender.evaluate", return_value='{"success": true}'), \
             patch("bosshunter.executor.sender._click_chat_button", return_value={"success": True}), \
             patch("bosshunter.executor.sender._detect_greet_popup", return_value={"success": True, "popup": False}), \
             patch("bosshunter.executor.sender._wait_for_chat_page", return_value={"success": True, "target_id": "target-1"}), \
             patch("bosshunter.executor.sender._message_delivery_state", return_value="missing"), \
             patch("bosshunter.executor.sender._submit_chat_message_background", return_value={"success": True}), \
             patch("bosshunter.executor.sender._submitted_message_looks_accepted", return_value=True), \
             patch("bosshunter.executor.sender.close_tab") as close_tab, \
             patch("bosshunter.executor.sender.time.sleep"):
            result, target_id = _send_greeting_once(
                job,
                "您好，我对这个岗位很感兴趣。",
                {"browse_before_greet": False, "_send_verification_attempts": 1},
            )

        self.assertIsNone(target_id)
        self.assertTrue(result["success"])
        self.assertTrue(result["accepted_without_echo"])
        close_tab.assert_called_once_with("target-1")

    def test_send_greeting_uses_original_flow_when_platform_preset_is_enabled(self):
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
             patch(
                 "bosshunter.executor.sender._confirm_preset_greeting",
                 return_value={"success": True, "action": "preset_confirmed"},
             ) as confirm_preset, \
             patch(
                 "bosshunter.executor.sender._wait_for_chat_page",
                 return_value={"success": True, "target_id": "target-1"},
             ), \
             patch(
                 "bosshunter.executor.sender._message_delivery_state",
                 side_effect=["missing", "delivered", "delivered"],
             ), \
             patch(
                 "bosshunter.executor.sender._fill_chat_input",
                 return_value={"success": True, "disabled": False},
             ) as fill_input, \
             patch(
                 "bosshunter.executor.sender._submit_chat_message_background",
                 return_value={"success": True, "action": "chat_submitted_background"},
             ) as background_submit, \
             patch("bosshunter.executor.sender.click_at", return_value=True), \
             patch("bosshunter.executor.sender.close_tab"), \
             patch("bosshunter.executor.sender.time.sleep"):
            result, target_id = _send_greeting_once(
                job,
                "您好，我对这个岗位很感兴趣。",
                {"browse_before_greet": False},
            )

        self.assertIsNone(target_id)
        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        confirm_preset.assert_called_once_with("target-1")
        background_submit.assert_called_once()
        fill_input.assert_not_called()

    def test_send_greeting_redirects_first_contact_and_uses_background_chat_submit(self):
        job = {
            "id": "first-contact",
            "company": "Example",
            "title": "Engineer",
            "url": "https://www.zhipin.com/job_detail/first-contact.html",
        }
        greeting = "您好，我对这个岗位很感兴趣。"

        with patch("bosshunter.executor.sender.new_tab", return_value="target-1"), \
             patch("bosshunter.executor.sender.evaluate", return_value='{"success": true}'), \
             patch(
                 "bosshunter.executor.sender._click_chat_button",
                 return_value={
                     "success": True,
                     "redirectUrl": "/web/geek/chat?jobId=first-contact",
                 },
             ), \
             patch(
                 "bosshunter.executor.sender._detect_greet_popup",
                 return_value={"success": True, "popup": True, "kind": "startchat_dialog"},
             ), \
             patch(
                 "bosshunter.executor.sender._navigate_to_chat_redirect",
                 return_value=True,
             ) as redirect_first_contact, \
             patch("bosshunter.executor.sender._submit_startchat_greeting") as foreground_submit, \
             patch(
                 "bosshunter.executor.sender._wait_for_chat_page",
                 return_value={"success": True, "target_id": "target-1"},
             ), \
             patch(
                 "bosshunter.executor.sender._message_delivery_state",
                 side_effect=["missing", "delivered", "delivered"],
             ), \
             patch(
                 "bosshunter.executor.sender._submit_chat_message_background",
                 return_value={"success": True, "action": "chat_submitted_background"},
             ) as background_submit, \
             patch("bosshunter.executor.sender._fill_chat_input") as fill_input, \
             patch("bosshunter.executor.sender.close_tab") as close_tab, \
             patch("bosshunter.executor.sender.time.sleep"):
            result, target_id = _send_greeting_once(
                job,
                greeting,
                {"browse_before_greet": False, "_send_verification_attempts": 1},
            )

        self.assertIsNone(target_id)
        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        redirect_first_contact.assert_called_once()
        foreground_submit.assert_not_called()
        background_submit.assert_called_once_with("target-1", greeting)
        fill_input.assert_not_called()
        close_tab.assert_called_once_with("target-1")

    def test_first_contact_without_chat_redirect_fails_without_foreground_click(self):
        job = {
            "id": "first-contact-unverified",
            "company": "Example",
            "title": "Engineer",
            "url": "https://www.zhipin.com/job_detail/first-contact-unverified.html",
        }

        with patch("bosshunter.executor.sender.new_tab", return_value="target-1"), \
             patch("bosshunter.executor.sender.evaluate", return_value='{"success": true}'), \
             patch(
                 "bosshunter.executor.sender._click_chat_button",
                 return_value={"success": True, "redirectUrl": ""},
             ), \
             patch(
                 "bosshunter.executor.sender._detect_greet_popup",
                 return_value={"success": True, "popup": True, "kind": "startchat_dialog"},
             ), \
             patch(
                 "bosshunter.executor.sender._navigate_to_chat_redirect",
                 return_value=False,
             ), \
             patch("bosshunter.executor.sender._submit_startchat_greeting") as foreground_submit, \
             patch("bosshunter.executor.sender.click_at") as foreground_click, \
             patch("bosshunter.executor.sender.time.sleep"):
            result, target_id = _send_greeting_once(
                job,
                "您好，我对这个岗位很感兴趣。",
                {"browse_before_greet": False, "_send_verification_attempts": 1},
            )

        self.assertEqual(target_id, "target-1")
        self.assertEqual(result["error"], "startchat_redirect_unavailable")
        foreground_submit.assert_not_called()
        foreground_click.assert_not_called()

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

    def test_chat_match_only_uses_active_conversation_not_whole_sidebar(self):
        job = {
            "id": "expected-job",
            "company": "Expected Company",
            "title": "Expected Role",
        }
        with patch(
            "bosshunter.executor.sender.evaluate",
            return_value='{"success": true, "matches": true}',
        ) as evaluate_mock:
            self.assertTrue(_chat_target_matches_job("user-chat", job))

        script = evaluate_mock.call_args.args[1]
        self.assertIn(".chat-conversation", script)
        self.assertIn(".friend-content.selected", script)
        self.assertNotIn("document.body.innerText", script)
        self.assertNotIn("performance.getEntriesByType", script)

    def test_wait_for_chat_page_adopts_preexisting_chat_when_active_job_matches(self):
        job = {
            "id": "expected-job",
            "company": "Expected Company",
            "title": "Expected Role",
        }
        targets = [{"targetId": "user-chat", "url": "https://www.zhipin.com/web/geek/chat"}]

        with patch("bosshunter.executor.sender.evaluate", return_value="/job_detail/expected-job"), \
             patch("bosshunter.executor.sender._chat_target_matches_job", return_value=True) as matches_job, \
             patch("bosshunter.executor.sender.get_page_targets", return_value=targets), \
             patch("bosshunter.executor.sender.time.sleep"):
            result = _wait_for_chat_page(
                "task-target",
                None,
                attempts=1,
                job=job,
                excluded_target_ids={"user-chat"},
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["target_id"], "user-chat")
        matches_job.assert_called_once_with("user-chat", job)

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
             patch(
                 "bosshunter.executor.sender._submit_chat_message_background",
                 return_value={"success": True, "action": "chat_submitted_background"},
             ), \
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

    def test_send_greetings_closes_failed_task_page_after_recording_error(self):
        job = _job("failed-first-contact")
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

            failure = {
                "success": False,
                "error": "startchat_submit_missing",
                "history_detail": "首次沟通招呼语未能填写或提交",
                "skip_backoff": True,
            }
            with patch("bosshunter.db.DB_PATH", db_path), \
                 patch("bosshunter.executor.sender.should_take_day_off", return_value=False), \
                 patch("bosshunter.executor.sender.SendWindowChecker.is_active", return_value=True), \
                 patch(
                     "bosshunter.executor.sender._send_greeting_once",
                     return_value=(failure, "task-target"),
                 ), \
                 patch("bosshunter.executor.sender.close_tab") as close_tab:
                sent = send_greetings({"throttle": {"daily_limit": 10}}, force=True)

            self.assertEqual(sent, 0)
            close_tab.assert_called_once_with("task-target")

    def test_send_greetings_reports_failed_and_quota_deferred_jobs_separately(self):
        jobs = [_job(f"quota-{index}") for index in range(3)]
        for job in jobs:
            job["greeting"] = f"您好，我对 {job['id']} 很感兴趣。"

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "bosshunter.db"
            db = get_db(db_path)
            try:
                for job in jobs:
                    insert_job(db, job)
                    update_job_status(db, job["id"], "ready")
                    update_job_greeting(db, job["id"], job["greeting"])
            finally:
                db.close()

            failure = {
                "success": False,
                "error": "no_chat_input",
                "history_detail": "未进入具体聊天会话",
                "skip_backoff": True,
            }
            config = {
                "_workbench_job_ids": [job["id"] for job in jobs],
                "throttle": {
                    "daily_limit": 2,
                    "interval_min": 0,
                    "interval_max": 0,
                },
            }
            with patch("bosshunter.db.DB_PATH", db_path), \
                 patch("bosshunter.executor.sender.should_take_day_off", return_value=False), \
                 patch("bosshunter.executor.sender.SendWindowChecker.is_active", return_value=True), \
                 patch(
                     "bosshunter.executor.sender._send_greeting_once",
                     side_effect=[({"success": True}, None), (failure, None)],
                 ):
                sent = send_greetings(config, force=True)

            report = config["_workbench_send_report"]

        self.assertEqual(sent, 1)
        self.assertEqual(report["scheduled_count"], 2)
        self.assertEqual(report["attempted_count"], 2)
        self.assertEqual(report["sent_count"], 1)
        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["deferred_count"], 1)
        self.assertEqual(report["quota_deferred_count"], 1)
        self.assertEqual(report["stop_reason"], "daily_limit")

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

    def test_pending_confirmation_keeps_approved_jobs_without_greetings_recoverable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            try:
                insert_job(db, _job("approved"))
                update_job_score(db, "approved", 88, "good match")
                update_job_status(db, "approved", "approved")

                insert_job(db, _job("deleted-approved"))
                update_job_score(db, "deleted-approved", 90, "great match")
                update_job_status(db, "deleted-approved", "approved")
                db.execute(
                    "UPDATE jobs SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?",
                    ("deleted-approved",),
                )
                db.commit()

                jobs = get_jobs_pending_confirmation(db)
            finally:
                db.close()

        self.assertEqual([job["id"] for job in jobs], ["approved"])

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
