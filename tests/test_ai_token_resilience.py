import unittest
from unittest.mock import MagicMock, patch

import httpx

from bosshunter.ai import credentials, greeter, scorer


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": f"AI 产品经理 {job_id}",
        "company": f"公司 {job_id}",
        "salary": "20-30K",
        "experience": "3-5年",
        "jd": "负责 AI 产品规划、用户研究和项目落地。" * 120,
        "score_reason": "产品经验匹配",
    }


class AiCredentialErrorTests(unittest.TestCase):
    def test_openai_compatible_quota_error_is_not_silently_swallowed(self):
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        response = httpx.Response(
            429,
            request=request,
            json={"error": {"code": "insufficient_quota", "message": "quota exceeded"}},
        )
        config = {
            "ai": {
                "service": "deepseek",
                "provider": "openai_compatible",
                "model": "deepseek-chat",
                "api_key": "secret",
            }
        }

        with patch("bosshunter.ai.credentials.httpx.post", return_value=response):
            with self.assertRaises(credentials.AIRequestError) as raised:
                credentials.call_openai_compatible_text("prompt", config, 256)

        self.assertEqual(raised.exception.kind, "token_quota")
        self.assertEqual(str(raised.exception), "AI Token 额度或账户余额不足")
        self.assertNotIn("secret", str(raised.exception))

    def test_context_limit_error_has_actionable_category(self):
        error = RuntimeError(
            "maximum context length exceeded: max_tokens plus input tokens is too large"
        )

        normalized = credentials.normalize_ai_error(error)

        self.assertEqual(normalized.kind, "context_limit")
        self.assertIn("上下文限制", normalized.user_message)

    def test_openai_truncated_response_is_reported_separately(self):
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        response = httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"score": 80'},
                    }
                ]
            },
        )
        config = {
            "ai": {
                "service": "deepseek",
                "provider": "openai_compatible",
                "model": "deepseek-chat",
                "api_key": "secret",
            }
        }

        with patch("bosshunter.ai.credentials.httpx.post", return_value=response):
            with self.assertRaises(credentials.AIRequestError) as raised:
                credentials.call_openai_compatible_text("prompt", config, 256)

        self.assertEqual(raised.exception.kind, "output_truncated")


class ScorerTokenResilienceTests(unittest.TestCase):
    def test_invalid_score_json_retries_and_reports_progress(self):
        db = MagicMock()
        job = _job("invalid-json")
        progress_updates: list[dict] = []

        with (
            patch("bosshunter.ai.scorer.get_db", return_value=db),
            patch("bosshunter.ai.scorer._load_resume", return_value="真实简历"),
            patch("bosshunter.ai.scorer.get_jobs_by_status", return_value=[job]),
            patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
            patch(
                "bosshunter.ai.scorer._call_claude",
                side_effect=[
                    "这不是完整 JSON",
                    '{"score": 82, "reason": "匹配", "missing": ""}',
                ],
            ) as call_ai,
            patch("bosshunter.ai.scorer.update_job_quick_score"),
            patch("bosshunter.ai.scorer.update_job_score"),
            patch("bosshunter.ai.scorer.update_job_status"),
        ):
            scored, filtered = scorer.score_jobs(
                {
                    "ai": {"scoring_max_attempts": 2},
                    "scoring": {"threshold": 70},
                    "_workbench_score_progress": progress_updates.append,
                }
            )

        self.assertEqual((scored, filtered), (1, 0))
        self.assertEqual(call_ai.call_count, 2)
        self.assertEqual(progress_updates[-1]["completed"], 1)
        self.assertEqual(progress_updates[-1]["scored"], 1)

    def test_context_limit_retries_once_with_compact_prompt(self):
        db = MagicMock()
        job = _job("long")

        with (
            patch("bosshunter.ai.scorer.get_db", return_value=db),
            patch("bosshunter.ai.scorer._load_resume", return_value="简历内容" * 1000),
            patch("bosshunter.ai.scorer.get_jobs_by_status", return_value=[job]),
            patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
            patch(
                "bosshunter.ai.scorer._call_claude",
                side_effect=[
                    credentials.AIRequestError("context_limit", "请求内容超过当前模型的上下文限制"),
                    '{"score": 78, "reason": "匹配", "missing": ""}',
                ],
            ) as call_ai,
            patch("bosshunter.ai.scorer.update_job_quick_score"),
            patch("bosshunter.ai.scorer.update_job_score"),
            patch("bosshunter.ai.scorer.update_job_status"),
        ):
            scored, _ = scorer.score_jobs({"scoring": {"threshold": 70}})

        self.assertEqual(scored, 1)
        self.assertEqual(call_ai.call_count, 2)
        full_prompt = call_ai.call_args_list[0].args[0]
        compact_prompt = call_ai.call_args_list[1].args[0]
        self.assertLess(len(compact_prompt), len(full_prompt))
        self.assertIn("为适配模型上下文已裁剪", compact_prompt)
        self.assertEqual(call_ai.call_args_list[1].args[2], 128)

    def test_output_limit_retries_once_with_lower_single_request_limit(self):
        db = MagicMock()
        job = _job("output")
        logs: list[str] = []

        with (
            patch("bosshunter.ai.scorer.get_db", return_value=db),
            patch("bosshunter.ai.scorer._load_resume", return_value="真实简历"),
            patch("bosshunter.ai.scorer.get_jobs_by_status", return_value=[job]),
            patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
            patch(
                "bosshunter.ai.scorer._call_claude",
                side_effect=[
                    credentials.AIRequestError("output_limit", "当前模型不接受设置的输出 Token 上限"),
                    '{"score": 82, "reason": "匹配", "missing": ""}',
                ],
            ) as call_ai,
            patch("bosshunter.ai.scorer.update_job_quick_score"),
            patch("bosshunter.ai.scorer.update_job_score"),
            patch("bosshunter.ai.scorer.update_job_status"),
        ):
            scored, filtered = scorer.score_jobs(
                {
                    "scoring": {"threshold": 70},
                    "_workbench_log": logs.append,
                }
            )

        self.assertEqual((scored, filtered), (1, 0))
        self.assertEqual(call_ai.call_count, 2)
        self.assertEqual(call_ai.call_args_list[1].args[2], 128)
        self.assertTrue(any("降低输出 Token 上限后重试评分" in message for message in logs))

    def test_truncated_score_retries_with_larger_output_limit(self):
        db = MagicMock()
        job = _job("truncated")
        logs: list[str] = []

        with (
            patch("bosshunter.ai.scorer.get_db", return_value=db),
            patch("bosshunter.ai.scorer._load_resume", return_value="真实简历"),
            patch("bosshunter.ai.scorer.get_jobs_by_status", return_value=[job]),
            patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
            patch(
                "bosshunter.ai.scorer._call_claude",
                side_effect=[
                    credentials.AIRequestError("output_truncated", "AI 返回内容因输出 Token 上限被截断"),
                    '{"score": 82, "reason": "匹配", "missing": ""}',
                ],
            ) as call_ai,
            patch("bosshunter.ai.scorer.update_job_quick_score"),
            patch("bosshunter.ai.scorer.update_job_score"),
            patch("bosshunter.ai.scorer.update_job_status"),
        ):
            scored, filtered = scorer.score_jobs(
                {
                    "scoring": {"threshold": 70},
                    "_workbench_log": logs.append,
                }
            )

        self.assertEqual((scored, filtered), (1, 0))
        self.assertEqual(call_ai.call_args_list[1].args[2], 16384)
        self.assertTrue(any("回答被截断" in message and "增大输出 Token" in message for message in logs))

    def test_quota_error_pauses_without_changing_pending_jobs(self):
        db = MagicMock()
        jobs = [_job("1"), _job("2")]
        logs: list[str] = []

        with (
            patch("bosshunter.ai.scorer.get_db", return_value=db),
            patch("bosshunter.ai.scorer._load_resume", return_value="真实简历"),
            patch("bosshunter.ai.scorer.get_jobs_by_status", return_value=jobs),
            patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
            patch(
                "bosshunter.ai.scorer._call_claude",
                side_effect=credentials.AIRequestError("token_quota", "AI Token 额度或账户余额不足"),
            ) as call_ai,
            patch("bosshunter.ai.scorer.update_job_quick_score"),
            patch("bosshunter.ai.scorer.update_job_score"),
            patch("bosshunter.ai.scorer.update_job_status") as update_status,
        ):
            scored, filtered = scorer.score_jobs(
                {
                    "scoring": {"threshold": 70},
                    "_workbench_log": logs.append,
                }
            )

        self.assertEqual((scored, filtered), (0, 0))
        self.assertEqual(call_ai.call_count, 1)
        update_status.assert_not_called()
        self.assertTrue(any("安全暂停" in message and "下次运行会继续处理" in message for message in logs))


class GreeterTokenResilienceTests(unittest.TestCase):
    def test_greeting_json_wrapper_is_normalized(self):
        response = '```json\n{"greeting":"您好，我的产品经验与岗位需求比较匹配。"}\n```'

        result = greeter._normalize_greeting_response(response)

        self.assertEqual(result, "您好，我的产品经验与岗位需求比较匹配。")

    def test_embedded_nested_greeting_json_is_normalized(self):
        response = '以下是结果：{"data":{"message":{"content":"您好，期待和您进一步沟通。"}}}'

        result = greeter._normalize_greeting_response(response)

        self.assertEqual(result, "您好，期待和您进一步沟通。")

    def test_malformed_structured_greeting_is_retried_instead_of_saved(self):
        self.assertIsNone(greeter._normalize_greeting_response('{"greeting":"未结束'))

    def test_invalid_review_format_keeps_the_generated_greeting(self):
        db = MagicMock()
        jobs = [_job("review-format")]
        logs: list[str] = []

        with (
            patch("bosshunter.ai.greeter.get_db", return_value=db),
            patch("bosshunter.ai.greeter.get_jobs_by_status", return_value=jobs),
            patch("bosshunter.ai.greeter._get_resume_summary", return_value="真实简历摘要"),
            patch(
                "bosshunter.ai.greeter._call_claude",
                side_effect=[
                    "这是一条可用的个性化招呼语。",
                    "评分很好，但没有按 JSON 返回。",
                ],
            ) as call_ai,
            patch("bosshunter.ai.greeter.update_job_greeting") as update_greeting,
            patch("bosshunter.ai.greeter.update_job_status") as update_status,
        ):
            count = greeter.generate_greetings(
                {
                    "ai": {"greeting_max_iterations": 2},
                    "_workbench_log": logs.append,
                }
            )

        self.assertEqual(count, 1)
        self.assertEqual(call_ai.call_count, 2)
        update_greeting.assert_called_once_with(
            db,
            "review-format",
            "这是一条可用的个性化招呼语。",
        )
        update_status.assert_called_once_with(db, "review-format", "ready")
        self.assertTrue(any("质量检查返回格式无法识别" in message for message in logs))

    def test_empty_greeting_retries_before_leaving_job_pending(self):
        db = MagicMock()
        jobs = [_job("retry-empty")]

        with (
            patch("bosshunter.ai.greeter.get_db", return_value=db),
            patch("bosshunter.ai.greeter.get_jobs_by_status", return_value=jobs),
            patch("bosshunter.ai.greeter._get_resume_summary", return_value="真实简历摘要"),
            patch(
                "bosshunter.ai.greeter._call_claude",
                side_effect=[None, "第二次生成成功的个性化招呼语"],
            ) as call_ai,
            patch("bosshunter.ai.greeter.update_job_greeting") as update_greeting,
            patch("bosshunter.ai.greeter.update_job_status"),
            patch("bosshunter.ai.greeter.add_history") as add_history,
        ):
            count = greeter.generate_greetings(
                {
                    "ai": {
                        "greeting_max_attempts": 2,
                        "greeting_max_iterations": 0,
                    },
                }
            )

        self.assertEqual(count, 1)
        self.assertEqual(call_ai.call_count, 2)
        update_greeting.assert_called_once_with(
            db,
            "retry-empty",
            "第二次生成成功的个性化招呼语",
        )
        add_history.assert_not_called()

    def test_review_quota_error_preserves_first_greeting_and_pauses_batch(self):
        db = MagicMock()
        jobs = [_job("1"), _job("2")]
        logs: list[str] = []

        with (
            patch("bosshunter.ai.greeter.get_db", return_value=db),
            patch("bosshunter.ai.greeter.get_jobs_by_status", return_value=jobs),
            patch("bosshunter.ai.greeter._get_resume_summary", return_value="真实简历摘要"),
            patch(
                "bosshunter.ai.greeter._call_claude",
                side_effect=[
                    "这是一条已经可以使用的个性化招呼语。",
                    credentials.AIRequestError("token_quota", "AI Token 额度或账户余额不足"),
                ],
            ) as call_ai,
            patch("bosshunter.ai.greeter.update_job_greeting") as update_greeting,
            patch("bosshunter.ai.greeter.update_job_status"),
        ):
            count = greeter.generate_greetings(
                {
                    "ai": {"greeting_max_iterations": 1},
                    "_workbench_log": logs.append,
                }
            )

        self.assertEqual(count, 1)
        self.assertEqual(call_ai.call_count, 2)
        update_greeting.assert_called_once_with(
            db,
            "1",
            "这是一条已经可以使用的个性化招呼语。",
        )
        self.assertTrue(any("安全暂停" in message and "已生成内容已保存" in message for message in logs))

    def test_output_limit_retries_greeting_without_reducing_batch_size(self):
        db = MagicMock()
        jobs = [_job("1")]
        logs: list[str] = []

        with (
            patch("bosshunter.ai.greeter.get_db", return_value=db),
            patch("bosshunter.ai.greeter.get_jobs_by_status", return_value=jobs),
            patch("bosshunter.ai.greeter._get_resume_summary", return_value="真实简历摘要"),
            patch(
                "bosshunter.ai.greeter._call_claude",
                side_effect=[
                    credentials.AIRequestError("output_limit", "当前模型不接受设置的输出 Token 上限"),
                    "个性化招呼语",
                ],
            ) as call_ai,
            patch("bosshunter.ai.greeter.update_job_greeting") as update_greeting,
            patch("bosshunter.ai.greeter.update_job_status"),
        ):
            count = greeter.generate_greetings(
                {
                    "ai": {"greeting_max_iterations": 0},
                    "_workbench_log": logs.append,
                }
            )

        self.assertEqual(count, 1)
        self.assertEqual(update_greeting.call_count, 1)
        self.assertEqual(call_ai.call_args_list[0].args[2], 8192)
        self.assertEqual(call_ai.call_args_list[1].args[2], 160)
        self.assertTrue(any("降低单次输出 Token 上限后重试招呼语" in message for message in logs))

    def test_truncated_greeting_retries_with_larger_output_limit(self):
        db = MagicMock()
        jobs = [_job("1")]
        logs: list[str] = []

        with (
            patch("bosshunter.ai.greeter.get_db", return_value=db),
            patch("bosshunter.ai.greeter.get_jobs_by_status", return_value=jobs),
            patch("bosshunter.ai.greeter._get_resume_summary", return_value="真实简历摘要"),
            patch(
                "bosshunter.ai.greeter._call_claude",
                side_effect=[
                    credentials.AIRequestError("output_truncated", "AI 返回内容因输出 Token 上限被截断"),
                    "完整的个性化招呼语",
                ],
            ) as call_ai,
            patch("bosshunter.ai.greeter.update_job_greeting"),
            patch("bosshunter.ai.greeter.update_job_status"),
        ):
            count = greeter.generate_greetings(
                {
                    "ai": {"greeting_max_iterations": 0},
                    "_workbench_log": logs.append,
                }
            )

        self.assertEqual(count, 1)
        self.assertEqual(call_ai.call_args_list[0].args[2], 8192)
        self.assertEqual(call_ai.call_args_list[1].args[2], 16384)
        self.assertTrue(any("回答被截断" in message and "增大输出 Token" in message for message in logs))


if __name__ == "__main__":
    unittest.main()
