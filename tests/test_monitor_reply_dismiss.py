import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bosshunter.db import add_history, get_db, get_jobs_needing_resume, insert_job, update_job_status


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": "AI运营",
        "company": "Example",
        "salary": "20-30K",
        "city": "北京",
        "experience": "1-3年",
        "jd": "负责AI内容运营",
        "hr_name": "HR",
        "hr_title": "招聘者",
        "hr_active": "",
        "company_size": "",
        "company_industry": "",
        "url": "https://example.com/job",
    }


class MonitorReplyDismissTests(unittest.TestCase):
    def test_shared_scan_target_is_reused_and_left_open_for_the_next_conversation(self):
        from bosshunter.executor import monitor

        messages = [
            {"sender": "me", "text": "您好，我对岗位很感兴趣。"},
            {"sender": "hr", "text": "方便介绍一下你的相关经验吗？"},
        ]
        conversation = {
            "_chat_target_id": "chat-target",
            "element_index": 0,
            "hr_name": "HR",
            "company": "Example",
            "last_message": "方便介绍一下你的相关经验吗？",
        }

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, _job("shared-target"))
                update_job_status(db, "shared-target", "replied")
            finally:
                db.close()

            def open_db():
                return get_db(db_path)

            monitor._SHARED_MONITOR_TARGETS.add("chat-target")
            try:
                with patch.object(monitor, "get_db", side_effect=open_db), \
                     patch.object(monitor, "_open_scanned_conversation", return_value="chat-target") as reuse, \
                     patch.object(monitor, "_open_conversation") as open_conversation, \
                     patch.object(monitor, "_wait_or_stop", return_value=False), \
                     patch.object(monitor, "evaluate", return_value=json.dumps(messages, ensure_ascii=False)), \
                     patch.object(monitor, "_generate_auto_reply", return_value="本地生成的建议回复"), \
                     patch.object(monitor, "_browser_close_tab") as browser_close:
                    action = monitor._handle_conversation(
                        _job("shared-target") | {"status": "replied"},
                        {"monitor": {"auto_reply_hr_questions": False}},
                        conversation,
                    )
            finally:
                monitor._SHARED_MONITOR_TARGETS.discard("chat-target")

        self.assertEqual(action, "reply_pending")
        reuse.assert_called_once()
        open_conversation.assert_not_called()
        browser_close.assert_not_called()

    def test_manual_boss_reply_is_recorded_once_without_generating_or_sending(self):
        from bosshunter.executor import monitor

        messages = [
            {"sender": "me", "text": "您好，我对岗位很感兴趣。"},
            {"sender": "hr", "text": "方便介绍一下你的相关经验吗？"},
            {"sender": "unknown", "text": "可以，我做过两个相关项目。"},
        ]
        conversation = {
            "last_direction": "me",
            "is_our_message": True,
            "last_message": "可以，我做过两个相关项目。",
        }

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, _job("manual-reply"))
                update_job_status(db, "manual-reply", "sent")
            finally:
                db.close()

            def open_db():
                return get_db(db_path)

            with patch.object(monitor, "get_db", side_effect=open_db), \
                 patch.object(monitor, "_open_conversation", return_value="target-1"), \
                 patch.object(monitor, "_wait_or_stop", return_value=False), \
                 patch.object(monitor, "evaluate", return_value=json.dumps(messages, ensure_ascii=False)), \
                 patch.object(monitor, "close_tab"), \
                 patch.object(monitor, "_generate_auto_reply") as generate_reply, \
                 patch.object(monitor, "_send_message_in_chat") as send_message:
                first_action = monitor._handle_conversation(
                    _job("manual-reply") | {"status": "sent"},
                    {"monitor": {}},
                    conversation,
                )
                second_action = monitor._handle_conversation(
                    _job("manual-reply") | {"status": "replied"},
                    {"monitor": {}},
                    conversation,
                )

            verify_db = get_db(db_path)
            try:
                row = verify_db.execute(
                    "SELECT status FROM jobs WHERE id = ?",
                    ("manual-reply",),
                ).fetchone()
                replies = verify_db.execute(
                    "SELECT detail FROM history WHERE job_id = ? AND action = 'replied' ORDER BY id",
                    ("manual-reply",),
                ).fetchall()
            finally:
                verify_db.close()

        self.assertEqual(first_action, "recorded_user_reply")
        self.assertEqual(second_action, "skipped_user_replied")
        self.assertEqual(row["status"], "replied")
        self.assertEqual(len(replies), 1)
        payload = json.loads(replies[0]["detail"])
        self.assertEqual(payload["schema"], "replied.external.v1")
        self.assertEqual(payload["hr_question"], "方便介绍一下你的相关经验吗？")
        self.assertEqual(payload["manual_reply"], "可以，我做过两个相关项目。")
        self.assertEqual(
            [item["sender"] for item in payload["conversation_tail"]],
            ["me", "hr", "me"],
        )
        generate_reply.assert_not_called()
        send_message.assert_not_called()

    def test_dismissed_pending_reply_is_not_recreated_by_monitor(self):
        from bosshunter.executor import monitor

        messages = [
            {"sender": "me", "text": "您好，我对岗位很感兴趣。"},
            {"sender": "hr", "text": "方便介绍一下你的AI内容运营经验吗？"},
        ]
        pending_detail = monitor._build_reply_detail(messages, "可以，我有AI内容运营经验。")

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, _job("job-1"))
                update_job_status(db, "job-1", "replied")
                add_history(db, "job-1", "reply_pending", pending_detail)
                add_history(
                    db,
                    "job-1",
                    "reply_dismissed",
                    monitor._build_reply_resolution_detail(
                        "reply_dismissed.v1",
                        "Web Dashboard 放弃回复建议",
                        pending_detail,
                    ),
                )
            finally:
                db.close()

            def open_db():
                return get_db(db_path)

            with patch.object(monitor, "get_db", side_effect=open_db), \
                 patch.object(monitor, "_open_conversation", return_value="target-1"), \
                 patch.object(monitor, "evaluate", return_value=json.dumps(messages, ensure_ascii=False)), \
                 patch.object(monitor, "close_tab"), \
                 patch.object(monitor, "_generate_auto_reply", return_value="可以，我有AI内容运营经验。") as generate_reply:
                action = monitor._handle_conversation(_job("job-1") | {"status": "replied"}, {"monitor": {}})

            verify_db = get_db(db_path)
            try:
                actions = [
                    row["action"]
                    for row in verify_db.execute(
                        "SELECT action FROM history WHERE job_id = ? ORDER BY id",
                        ("job-1",),
                    ).fetchall()
                ]
            finally:
                verify_db.close()

        self.assertEqual(action, "skipped_dismissed_reply")
        self.assertEqual(actions, ["reply_pending", "reply_dismissed"])
        generate_reply.assert_not_called()

    def test_new_hr_message_after_dismissed_reply_creates_new_pending_reply(self):
        from bosshunter.executor import monitor

        dismissed_messages = [
            {"sender": "me", "text": "您好，我对岗位很感兴趣。"},
            {"sender": "hr", "text": "方便介绍一下你的AI内容运营经验吗？"},
        ]
        new_messages = [
            *dismissed_messages,
            {"sender": "hr", "text": "那你能说说最近做过的增长项目吗？"},
        ]
        pending_detail = monitor._build_reply_detail(dismissed_messages, "可以，我有AI内容运营经验。")

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, _job("job-2"))
                update_job_status(db, "job-2", "replied")
                add_history(db, "job-2", "reply_pending", pending_detail)
                add_history(
                    db,
                    "job-2",
                    "reply_dismissed",
                    monitor._build_reply_resolution_detail(
                        "reply_dismissed.v1",
                        "Web Dashboard 放弃回复建议",
                        pending_detail,
                    ),
                )
            finally:
                db.close()

            def open_db():
                return get_db(db_path)

            with patch.object(monitor, "get_db", side_effect=open_db), \
                 patch.object(monitor, "_open_conversation", return_value="target-1"), \
                 patch.object(monitor, "evaluate", return_value=json.dumps(new_messages, ensure_ascii=False)), \
                 patch.object(monitor, "close_tab"), \
                 patch.object(monitor, "_generate_auto_reply", return_value="我最近做过一个增长项目。") as generate_reply:
                action = monitor._handle_conversation(_job("job-2") | {"status": "replied"}, {"monitor": {}})

            verify_db = get_db(db_path)
            try:
                actions = [
                    row["action"]
                    for row in verify_db.execute(
                        "SELECT action FROM history WHERE job_id = ? ORDER BY id",
                        ("job-2",),
                    ).fetchall()
                ]
            finally:
                verify_db.close()

        self.assertEqual(action, "reply_pending")
        self.assertEqual(actions, ["reply_pending", "reply_dismissed", "reply_pending"])
        generate_reply.assert_called_once()

    def test_resume_request_card_with_reject_button_is_not_treated_as_rejection(self):
        from bosshunter.executor import monitor

        messages = [
            {"sender": "hr", "text": "您是否接受此工作地点? 理想国际大厦 暂不考虑可以接受"},
            {"sender": "hr", "text": "已读 2周"},
            {
                "sender": "hr",
                "text": "我想要一份您的附件简历，您是否同意 拒绝 同意 拒绝 同意",
            },
        ]

        self.assertTrue(monitor._detect_resume_request(messages))
        self.assertTrue(monitor._has_resume_request_card(messages))
        self.assertFalse(monitor._detect_rejection(messages))

    def test_short_positive_hr_replies_are_treated_as_resume_intent(self):
        from bosshunter.executor import monitor

        for reply in ("好", "好的！", "可以。", "你好啊，可以聊一聊~"):
            with self.subTest(reply=reply):
                messages = [
                    {"sender": "me", "text": "如果合适，我可以补充发送简历。"},
                    {"sender": "hr", "text": reply},
                ]
                self.assertTrue(monitor._detect_resume_request(messages))

    def test_words_containing_positive_reply_text_are_not_treated_as_resume_intent(self):
        from bosshunter.executor import monitor

        for reply in ("不可以", "好像可以", "可以接受此工作地点"):
            with self.subTest(reply=reply):
                messages = [
                    {"sender": "me", "text": "您好，我对岗位很感兴趣。"},
                    {"sender": "hr", "text": reply},
                ]
                self.assertFalse(monitor._detect_resume_request(messages))

    def test_reconcile_marks_saved_greeting_as_own_and_filters_boss_notices(self):
        from bosshunter.executor import monitor

        greeting = "您好，我对这个岗位感兴趣，想进一步了解团队情况。"
        messages = [
            {"sender": "unknown", "text": f"送达 {greeting}"},
            {"sender": "hr", "text": "你与该职位竞争者PK情况 查看详细分析"},
            {"sender": "hr", "text": "方便介绍一下最近的相关经历吗？"},
        ]

        reconciled = monitor._reconcile_conversation_messages(
            messages,
            {"greeting": greeting},
        )

        self.assertEqual(
            [message["sender"] for message in reconciled],
            ["me", "system", "hr"],
        )
        self.assertEqual(
            monitor._get_hr_messages_after_last_reply(reconciled),
            [reconciled[-1]],
        )

    def test_job_recommendations_are_not_classified_as_hr_replies(self):
        from bosshunter.executor import monitor

        recommendations = (
            "新岗位速递 根据你的历史开聊/收藏岗位，识别到以下新发布岗位你可能感兴趣",
            "VIP数据总结 根据你的开聊/收藏岗位，已为你推荐70个新岗位",
            "我是你的求职助手，感谢您使用VIP权益，您的权益已到期，点击续费vip",
        )

        for recommendation in recommendations:
            with self.subTest(recommendation=recommendation):
                reconciled = monitor._reconcile_conversation_messages(
                    [{"sender": "hr", "text": recommendation}],
                    {"greeting": ""},
                )
                self.assertEqual(reconciled[0]["sender"], "system")
                self.assertEqual(monitor._get_hr_messages_after_last_reply(reconciled), [])

    def test_chat_extractors_keep_unknown_direction_conservative(self):
        from bosshunter.executor import monitor

        self.assertIn("return 'unknown'", monitor.JS_EXTRACT_CONVERSATION)
        self.assertIn("lastDirection !== 'me' && !isSystemMessage", monitor.JS_EXTRACT_CHAT_LIST)
        self.assertIn("新岗位速递", monitor.JS_EXTRACT_CHAT_LIST)
        self.assertIn("我是你的求职助手", monitor.JS_EXTRACT_CHAT_LIST)

    def test_boss_chat_invitation_generates_tailored_resume(self):
        from bosshunter.executor import monitor

        messages = [
            {"sender": "me", "text": "如果合适，我可以补充发送简历。"},
            {"sender": "hr", "text": "你好啊，可以聊一聊~"},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "bosshunter.db"
            generated_resume = Path(tmp) / "tailored.md"
            generated_resume.write_text("# 定制简历", encoding="utf-8")
            db = get_db(db_path)
            try:
                insert_job(db, _job("job-short-resume-intent"))
                update_job_status(db, "job-short-resume-intent", "sent")
            finally:
                db.close()

            def open_db():
                return get_db(db_path)

            with patch.object(monitor, "get_db", side_effect=open_db), \
                 patch.object(monitor, "_open_conversation", return_value="target-1"), \
                 patch.object(monitor, "evaluate", return_value=json.dumps(messages, ensure_ascii=False)), \
                 patch.object(monitor, "close_tab"), \
                 patch.object(monitor, "_send_message_in_chat", return_value=True) as send_message, \
                 patch.object(monitor.time, "sleep"), \
                 patch(
                     "bosshunter.ai.resume.generate_tailored_resume",
                     return_value=generated_resume,
                 ) as generate_resume:
                action = monitor._handle_conversation(
                    _job("job-short-resume-intent") | {"status": "sent"},
                    {"profile": {"portfolio_url": "https://example.com/resume"}},
                )

            verify_db = get_db(db_path)
            try:
                history = [
                    row["action"]
                    for row in verify_db.execute(
                        "SELECT action FROM history WHERE job_id = ? ORDER BY id",
                        ("job-short-resume-intent",),
                    ).fetchall()
                ]
                detail = verify_db.execute(
                    "SELECT detail FROM history WHERE job_id = ? AND action = 'needs_resume'",
                    ("job-short-resume-intent",),
                ).fetchone()["detail"]
            finally:
                verify_db.close()

        self.assertEqual(action, "needs_resume")
        self.assertEqual(history, ["needs_resume"])
        generate_resume.assert_called_once()
        send_message.assert_not_called()
        self.assertIn("未自动发送在线简历", json.loads(detail)["ai_reply"])

    def test_failed_tailored_resume_generation_does_not_mark_job_needs_resume(self):
        from bosshunter.executor import monitor

        messages = [
            {"sender": "me", "text": "您好，我对岗位很感兴趣。"},
            {"sender": "hr", "text": "方便发一份你的简历过来吗？"},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, _job("job-resume-failed"))
                update_job_status(db, "job-resume-failed", "sent")
            finally:
                db.close()

            def open_db():
                return get_db(db_path)

            with patch.object(monitor, "get_db", side_effect=open_db), \
                 patch.object(monitor, "_open_conversation", return_value="target-1"), \
                 patch.object(monitor, "evaluate", return_value=json.dumps(messages, ensure_ascii=False)), \
                 patch.object(monitor, "close_tab"), \
                 patch.object(monitor, "_send_message_in_chat", return_value=True), \
                 patch("bosshunter.ai.resume.generate_tailored_resume", return_value=None):
                action = monitor._handle_conversation(
                    _job("job-resume-failed") | {"status": "sent"},
                    {"profile": {"portfolio_url": "https://example.com/resume"}},
                )

            verify_db = get_db(db_path)
            try:
                row = verify_db.execute(
                    "SELECT status, resume_path FROM jobs WHERE id = ?",
                    ("job-resume-failed",),
                ).fetchone()
                actions = [
                    item["action"]
                    for item in verify_db.execute(
                        "SELECT action FROM history WHERE job_id = ? ORDER BY id",
                        ("job-resume-failed",),
                    ).fetchall()
                ]
                needing_resume = get_jobs_needing_resume(verify_db)
            finally:
                verify_db.close()

        self.assertEqual(action, "failed")
        self.assertEqual(row["status"], "sent")
        self.assertIsNone(row["resume_path"])
        self.assertEqual(actions, ["resume_failed"])
        self.assertEqual(needing_resume, [])

    def test_resume_failure_history_keeps_hr_message_and_system_reason_separate(self):
        from bosshunter.executor import monitor

        messages = [
            {"sender": "me", "text": "您好，我对岗位很感兴趣。"},
            {"sender": "hr", "text": "请发一份简历。"},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, _job("job-resume-reason"))
                update_job_status(db, "job-resume-reason", "sent")
            finally:
                db.close()

            def open_db():
                return get_db(db_path)

            with patch.object(monitor, "get_db", side_effect=open_db), \
                 patch.object(monitor, "_open_conversation", return_value="target-1"), \
                 patch.object(monitor, "evaluate", return_value=json.dumps(messages, ensure_ascii=False)), \
                 patch.object(monitor, "close_tab"), \
                 patch.object(monitor, "_send_message_in_chat", return_value=True), \
                 patch("bosshunter.ai.resume.generate_tailored_resume", return_value=None), \
                 patch(
                     "bosshunter.ai.resume.get_last_resume_failure_reason",
                     return_value="占位符校验失败：模型新增了 [待填写姓名]",
                 ):
                action = monitor._handle_conversation(
                    _job("job-resume-reason") | {"status": "sent"},
                    {"profile": {"portfolio_url": "https://example.com/resume"}},
                )

            verify_db = get_db(db_path)
            try:
                detail = verify_db.execute(
                    "SELECT detail FROM history WHERE job_id = ? AND action = 'resume_failed'",
                    ("job-resume-reason",),
                ).fetchone()["detail"]
            finally:
                verify_db.close()

        payload = json.loads(detail)
        self.assertEqual(action, "failed")
        self.assertEqual(payload["schema"], "resume_failed.v2")
        self.assertEqual(payload["hr_question"], "请发一份简历。")
        self.assertEqual(payload["system_reason"], "占位符校验失败：模型新增了 [待填写姓名]")
        self.assertEqual(payload["ai_reply"], "")


if __name__ == "__main__":
    unittest.main()
