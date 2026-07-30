import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from bosshunter.ai import greeter
from bosshunter.db import get_db, insert_job, update_job_status
from bosshunter.web import server
from bosshunter.web.tasks import WorkbenchTask


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": "产品经理",
        "company": "示例公司",
        "salary": "15-20K",
        "city": "杭州",
        "experience": "1-3年",
        "jd": "负责产品设计",
        "hr_name": "HR",
        "hr_title": "招聘经理",
        "hr_active": "",
        "company_size": "20-99人",
        "company_industry": "互联网",
        "url": "https://example.com/job",
    }


class GreeterReliabilityTests(unittest.TestCase):
    def test_deepseek_greeting_enables_thinking_and_uses_larger_token_limit(self):
        config = {
            "ai": {
                "model": "deepseek-v4-pro",
                "base_url": "https://api.deepseek.com/anthropic",
                "greeting_max_tokens": 8192,
                "greeting_timeout_seconds": 180,
                "greeting_max_attempts": 2,
            }
        }

        with patch.object(greeter, "call_anthropic_text", return_value="招呼语") as call_text:
            result = greeter._call_claude("prompt", config)

        self.assertEqual(result, "招呼语")
        self.assertEqual(call_text.call_args.args[2], 8192)
        self.assertEqual(call_text.call_args.kwargs["timeout"], 180)
        self.assertTrue(call_text.call_args.kwargs["enable_thinking"])
        self.assertFalse(call_text.call_args.kwargs["disable_thinking"])

    def test_greeting_call_retries_empty_response(self):
        config = {"ai": {"greeting_max_attempts": 2}}
        with patch.object(greeter, "call_anthropic_text", side_effect=[None, "第二次成功"]) as call_text:
            result = greeter._call_claude("prompt", config)

        self.assertEqual(result, "第二次成功")
        self.assertEqual(call_text.call_count, 2)

    def test_generation_failure_restores_job_and_records_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db_path = base / "bosshunter.db"
            resume_path = base / "resume.md"
            resume_path.write_text("# 简历", encoding="utf-8")
            db = get_db(db_path)
            insert_job(db, _job("greeting-failed"))
            update_job_status(db, "greeting-failed", "approved")
            db.close()

            with (
                patch.object(greeter, "get_db", side_effect=lambda: get_db(db_path)),
                patch.object(greeter, "_generate_greeting_once", return_value=None),
            ):
                count = greeter.generate_greetings({"profile": {"resume_path": str(resume_path)}})

            verify_db = get_db(db_path)
            row = verify_db.execute("SELECT status, greeting FROM jobs WHERE id = 'greeting-failed'").fetchone()
            history = verify_db.execute(
                "SELECT action, detail FROM history WHERE job_id = 'greeting-failed' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            verify_db.close()

        self.assertEqual(count, 0)
        self.assertEqual(row["status"], "ready")
        self.assertFalse(row["greeting"])
        self.assertEqual(history["action"], "greeting_failed")
        self.assertIn("恢复为待确认", history["detail"])

    def test_delivery_task_fails_instead_of_claiming_success_when_generation_is_incomplete(self):
        task = WorkbenchTask(id="task-1", mode="deliver", label="确认投递", stop_requested=Event())
        config = {"_workbench_job_ids": ["job-a", "job-b"]}

        with (
            patch("bosshunter.ai.greeter.generate_greetings", return_value=0),
            patch("bosshunter.executor.sender.send_greetings") as send_greetings,
        ):
            with self.assertRaisesRegex(RuntimeError, "仅成功生成 0 条"):
                server._execute_deliver(task, config)

        send_greetings.assert_not_called()

    def test_delivery_task_fails_when_sender_reports_partial_success(self):
        task = WorkbenchTask(id="task-2", mode="deliver", label="确认投递", stop_requested=Event())
        config = {"_workbench_job_ids": ["job-a", "job-b"]}

        with (
            patch("bosshunter.ai.greeter.generate_greetings", return_value=2),
            patch("bosshunter.executor.sender.send_greetings", return_value=1),
        ):
            with self.assertRaisesRegex(RuntimeError, "仅成功发送 1 条"):
                server._execute_deliver(task, config)


if __name__ == "__main__":
    unittest.main()
