import json
from unittest import TestCase
from unittest.mock import patch

from bosshunter.collection.capabilities import platform_supports
from bosshunter.executor import zhilian_actions


class ZhilianActionAdapterTests(TestCase):
    def test_zhilian_exposes_full_workflow_capabilities(self):
        for capability in ("collect", "score", "greet", "deliver", "monitor"):
            self.assertTrue(platform_supports("zhilian", capability))

    @patch.object(zhilian_actions, "_target_is_open", return_value=True)
    @patch.object(zhilian_actions, "close_tab")
    @patch.object(zhilian_actions, "send_zhilian_message", return_value={"success": True, "verified": True})
    @patch.object(zhilian_actions, "open_zhilian_conversation", return_value="zhilian-target")
    def test_greeting_adapter_requires_verified_message_result(self, open_conversation, send_message, close_tab, target_is_open):
        result, failed_target = zhilian_actions.send_zhilian_greeting_once(
            {"source_platform": "zhilian", "url": "https://www.zhaopin.com/jobdetail/1.htm"},
            "你好，想进一步了解这个岗位。",
            {},
        )

        self.assertTrue(result["success"])
        self.assertIsNone(failed_target)
        send_message.assert_called_once()
        close_tab.assert_called_once_with("zhilian-target")

    @patch.object(zhilian_actions, "evaluate", return_value=json.dumps([
        {"sender": "hr", "text": "请发一份简历", "kind": "message"},
    ], ensure_ascii=False))
    def test_conversation_parser_keeps_sender_and_message(self, evaluate):
        messages = zhilian_actions.extract_zhilian_conversation("zhilian-target")

        self.assertEqual(messages, [{"sender": "hr", "text": "请发一份简历", "kind": "message"}])
        evaluate.assert_called_once_with("zhilian-target", zhilian_actions.ZHILIAN_CONVERSATION_JS)
