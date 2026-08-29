import json
import unittest
from threading import Event, Lock, Thread, get_ident
from time import sleep
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


def _score_response(score: int = 82) -> str:
    components = {
        82: (34, 21, 12, 7, 8),
        78: (32, 20, 11, 7, 8),
    }[score]
    return json.dumps({
        "role_summary": "客户交付",
        "core_duties": {"score": components[0], "evidence": "匹配"},
        "transferable_evidence": {"score": components[1], "evidence": "匹配"},
        "hard_requirements": {"score": components[2], "evidence": "匹配"},
        "tools_industry": {"score": components[3], "evidence": "匹配"},
        "practical_fit": {"score": components[4], "evidence": "匹配"},
        "caps": [],
        "hard_gaps": [],
        "reason": "匹配",
        "missing": "",
    }, ensure_ascii=False)


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
        self.assertEqual(str(raised.exception), "AI Token 额度或账户余额不足 (token_quota, status=429)")
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

    def test_error_str_and_repr_carry_kind_and_status(self):
        error = credentials.AIRequestError("request_failed", "AI 服务请求失败", 404)

        self.assertEqual(str(error), "AI 服务请求失败 (request_failed, status=404)")
        self.assertEqual(
            str(credentials.AIRequestError("network", "AI 服务连接失败或超时")),
            "AI 服务连接失败或超时 (network)",
        )
        self.assertIn("kind='request_failed'", repr(error))
        self.assertIn("status_code=404", repr(error))

    def test_quota_marker_wins_over_context_marker(self):
        error = RuntimeError("余额不足，请减少输入过长内容")

        normalized = credentials.normalize_ai_error(error)

        self.assertEqual(normalized.kind, "token_quota")

    def test_rate_limit_status_wins_over_context_marker(self):
        request = httpx.Request("POST", "https://api.example.com/chat/completions")
        response = httpx.Response(
            429,
            request=request,
            json={"error": {"message": "请求过于频繁，输入过长"}},
        )
        error = httpx.HTTPStatusError("Too Many Requests", request=request, response=response)

        normalized = credentials.normalize_ai_error(error)

        self.assertEqual(normalized.kind, "rate_limit")

    def test_auth_status_wins_over_quota_and_rate_markers(self):
        request = httpx.Request("POST", "https://api.example.com/v1/messages")
        response = httpx.Response(
            401,
            request=request,
            json={"error": {"message": "quota exceeded for this token"}},
        )
        error = httpx.HTTPStatusError("Unauthorized", request=request, response=response)

        self.assertEqual(credentials.normalize_ai_error(error).kind, "auth")

        response = httpx.Response(
            403,
            request=request,
            json={"error": {"message": "rate limit policy rejected this key"}},
        )
        error = httpx.HTTPStatusError("Forbidden", request=request, response=response)

        self.assertEqual(credentials.normalize_ai_error(error).kind, "auth")


class ScorerTokenResilienceTests(unittest.TestCase):
    def test_structured_score_is_summed_and_hard_technical_gap_caps_at_55(self):
        response = """{
          "role_summary": "负责客户交付和上线支持",
          "core_duties": {"score": 36, "evidence": "有实施交付经验"},
          "transferable_evidence": {"score": 22, "evidence": "有培训和需求梳理经验"},
          "hard_requirements": {"score": 12, "evidence": "多数要求符合"},
          "tools_industry": {"score": 8, "evidence": "熟悉SaaS业务"},
          "practical_fit": {"score": 9, "evidence": "地点薪资符合"},
          "caps": ["technical_required"],
          "hard_gaps": ["必须熟练使用Linux并独立部署"],
          "reason": "交付经验匹配，但存在硬技术缺口",
          "missing": "Linux部署"
        }"""

        result = scorer._validated_score_result(response)

        self.assertIsNotNone(result)
        self.assertEqual(result.score, 55)
        self.assertEqual(result.raw_score, 87)
        self.assertIn("职责36/40", result.reason)
        self.assertIn("硬技术缺口封顶55", result.reason)

    def test_structured_score_rejects_component_above_its_limit(self):
        response = """{
          "role_summary": "客户成功",
          "core_duties": {"score": 41, "evidence": "不合法"},
          "transferable_evidence": {"score": 20, "evidence": "匹配"},
          "hard_requirements": {"score": 12, "evidence": "匹配"},
          "tools_industry": {"score": 8, "evidence": "匹配"},
          "practical_fit": {"score": 8, "evidence": "匹配"},
          "caps": [], "hard_gaps": [], "reason": "匹配", "missing": ""
        }"""

        self.assertIsNone(scorer._validated_score_result(response))

    def test_legacy_total_score_json_is_rejected(self):
        self.assertIsNone(
            scorer._validated_score_result('{"score": 82, "reason": "匹配", "missing": ""}')
        )

    def test_field_alias_transferable_is_normalized_and_scored(self):
        response = """{
          "role_summary": "客户成功",
          "core_duties": {"score": 30, "evidence": "较匹配"},
          "transferable": {"score": 19, "evidence": "可迁移"},
          "hard_requirements": {"score": 10, "evidence": "基本符合"},
          "tools_industry": {"score": 7, "evidence": "相关"},
          "practical_fit": {"score": 8, "evidence": "符合"},
          "caps": [], "hard_gaps": [], "reason": "整体较匹配", "missing": ""
        }"""

        result = scorer._validated_score_result(response)

        self.assertIsNotNone(result)
        self.assertEqual(result.components["transferable_evidence"], 19)
        self.assertEqual(result.score, 74)
        self.assertEqual(result.raw_score, 74)

    def test_canonical_field_name_wins_over_alias(self):
        response = """{
          "role_summary": "客户成功",
          "core_duties": {"score": 30, "evidence": "较匹配"},
          "transferable": {"score": 5, "evidence": "别名值"},
          "transferable_evidence": {"score": 22, "evidence": "正式字段值"},
          "hard_requirements": {"score": 10, "evidence": "基本符合"},
          "tools_industry": {"score": 7, "evidence": "相关"},
          "practical_fit": {"score": 8, "evidence": "符合"},
          "caps": [], "hard_gaps": [], "reason": "整体较匹配", "missing": ""
        }"""

        result = scorer._validated_score_result(response)

        self.assertIsNotNone(result)
        self.assertEqual(result.components["transferable_evidence"], 22)

    def test_failure_reason_lists_missing_fields(self):
        response = '{"role_summary": "客户成功", "core_duties": {"score": 30, "evidence": "较匹配"}}'

        self.assertIsNone(scorer._validated_score_result(response))
        reason = scorer._score_validation_failure_reason(response)
        self.assertIn("缺少字段", reason)
        for key in ("transferable_evidence", "hard_requirements", "tools_industry", "practical_fit"):
            self.assertIn(key, reason)

    def test_failure_reason_reports_unparseable_json(self):
        self.assertIsNone(scorer._validated_score_result("模型输出的不是 JSON"))
        self.assertIn("JSON", scorer._score_validation_failure_reason("模型输出的不是 JSON"))

    def test_failure_reason_reports_invalid_component_values(self):
        response = """{
          "role_summary": "客户成功",
          "core_duties": {"score": 41, "evidence": "超过上限"},
          "transferable_evidence": {"score": 20, "evidence": "匹配"},
          "hard_requirements": {"score": 12, "evidence": "匹配"},
          "tools_industry": {"score": 8, "evidence": "匹配"},
          "practical_fit": {"score": 8, "evidence": "匹配"},
          "caps": [], "hard_gaps": [], "reason": "匹配", "missing": ""
        }"""

        self.assertIsNone(scorer._validated_score_result(response))
        self.assertIn("字段值无效", scorer._score_validation_failure_reason(response))

    def test_failure_reason_reports_empty_response(self):
        self.assertIn("AI 未返回评分内容", scorer._score_validation_failure_reason(None))
        self.assertIn("AI 未返回评分内容", scorer._score_validation_failure_reason("   "))

    def test_alias_response_scores_successfully_end_to_end(self):
        db = MagicMock()
        job = _job("alias")
        response = """{
          "role_summary": "客户成功",
          "core_duties": {"score": 30, "evidence": "较匹配"},
          "transferable": {"score": 19, "evidence": "可迁移"},
          "hard_requirements": {"score": 10, "evidence": "基本符合"},
          "tools_industry": {"score": 7, "evidence": "相关"},
          "practical_fit": {"score": 8, "evidence": "符合"},
          "caps": [], "hard_gaps": [], "reason": "整体较匹配", "missing": ""
        }"""

        with (
            patch("bosshunter.ai.scorer.get_db", return_value=db),
            patch("bosshunter.ai.scorer._load_resume", return_value="真实简历"),
            patch("bosshunter.ai.scorer.get_jobs_by_status", return_value=[job]),
            patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
            patch("bosshunter.ai.scorer._call_claude", return_value=response),
            patch("bosshunter.ai.scorer.update_job_quick_score"),
            patch("bosshunter.ai.scorer.update_job_score"),
            patch("bosshunter.ai.scorer.update_job_status"),
        ):
            scored, filtered = scorer.score_jobs(
                {"ai": {"scoring_concurrency": 1}, "scoring": {"threshold": 71}}
            )

        self.assertEqual((scored, filtered), (1, 0))

    def test_request_score_reports_specific_validation_failure(self):
        job = _job("badjson")

        with (
            patch("bosshunter.ai.scorer._call_claude", return_value="模型输出的不是 JSON"),
            patch("bosshunter.ai.scorer._notify"),
        ):
            outcome = scorer._request_score(job, "真实简历", {}, 3)

        self.assertIn("JSON", outcome.failure_detail)
        self.assertIn("无法解析", outcome.failure_detail)

    def test_borderline_structured_score_is_reviewed_and_averaged(self):
        db = MagicMock()
        job = _job("review")
        first = """{
          "role_summary": "客户成功",
          "core_duties": {"score": 30, "evidence": "较匹配"},
          "transferable_evidence": {"score": 19, "evidence": "可迁移"},
          "hard_requirements": {"score": 10, "evidence": "基本符合"},
          "tools_industry": {"score": 7, "evidence": "相关"},
          "practical_fit": {"score": 8, "evidence": "符合"},
          "caps": [], "hard_gaps": [], "reason": "整体较匹配", "missing": "行业经验"
        }"""
        review = """{
          "role_summary": "客户成功",
          "core_duties": {"score": 28, "evidence": "部分匹配"},
          "transferable_evidence": {"score": 17, "evidence": "可以迁移"},
          "hard_requirements": {"score": 9, "evidence": "多数符合"},
          "tools_industry": {"score": 6, "evidence": "一般"},
          "practical_fit": {"score": 8, "evidence": "符合"},
          "caps": [], "hard_gaps": [], "reason": "匹配但有差距", "missing": "行业经验"
        }"""

        with (
            patch("bosshunter.ai.scorer.get_db", return_value=db),
            patch("bosshunter.ai.scorer._load_resume", return_value="真实简历"),
            patch("bosshunter.ai.scorer.get_jobs_by_status", return_value=[job]),
            patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
            patch("bosshunter.ai.scorer._call_claude", side_effect=[first, review]) as call_ai,
            patch("bosshunter.ai.scorer.update_job_quick_score"),
            patch("bosshunter.ai.scorer.update_job_score") as update_score,
            patch("bosshunter.ai.scorer.update_job_status"),
        ):
            scored, filtered = scorer.score_jobs(
                {
                    "ai": {"scoring_concurrency": 1, "scoring_second_review": True},
                    "scoring": {"threshold": 71},
                }
            )

        self.assertEqual((scored, filtered), (1, 0))
        self.assertEqual(call_ai.call_count, 2)
        self.assertEqual(update_score.call_args.args[2], 71)
        self.assertIn("二次复核", update_score.call_args.args[3])

    def test_ai_calls_run_with_configured_concurrency_but_db_writes_stay_on_main_thread(self):
        db = MagicMock()
        jobs = [_job(str(index)) for index in range(5)]
        lock = Lock()
        active = 0
        peak = 0
        main_thread = get_ident()
        write_threads: list[int] = []

        def call_ai(*_args, **_kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            sleep(0.03)
            with lock:
                active -= 1
            return _score_response(82)

        def record_write(*_args, **_kwargs):
            write_threads.append(get_ident())

        with (
            patch("bosshunter.ai.scorer.get_db", return_value=db),
            patch("bosshunter.ai.scorer._load_resume", return_value="真实简历"),
            patch("bosshunter.ai.scorer.get_jobs_by_status", return_value=jobs),
            patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
            patch("bosshunter.ai.scorer._call_claude", side_effect=call_ai),
            patch("bosshunter.ai.scorer.update_job_quick_score", side_effect=record_write),
            patch("bosshunter.ai.scorer.update_job_score", side_effect=record_write),
            patch("bosshunter.ai.scorer.update_job_status", side_effect=record_write),
        ):
            scored, filtered = scorer.score_jobs(
                {"ai": {"scoring_concurrency": 3}, "scoring": {"threshold": 71}}
            )

        self.assertEqual((scored, filtered), (5, 0))
        self.assertEqual(peak, 3)
        self.assertTrue(write_threads)
        self.assertEqual(set(write_threads), {main_thread})

    def test_stop_returns_without_waiting_for_inflight_concurrent_ai_calls(self):
        db = MagicMock()
        stop_event = Event()
        ai_started = Event()
        release_ai = Event()
        finished = Event()

        def blocking_ai(*_args, **_kwargs):
            ai_started.set()
            release_ai.wait(2)
            return _score_response(82)

        def run_scoring():
            try:
                scorer.score_jobs(
                    {
                        "ai": {"scoring_concurrency": 3},
                        "scoring": {"threshold": 71},
                        "_workbench_stop_event": stop_event,
                    }
                )
            finally:
                finished.set()

        with (
            patch("bosshunter.ai.scorer.get_db", return_value=db),
            patch("bosshunter.ai.scorer._load_resume", return_value="真实简历"),
            patch("bosshunter.ai.scorer.get_jobs_by_status", return_value=[_job("1"), _job("2")]),
            patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
            patch("bosshunter.ai.scorer._call_claude", side_effect=blocking_ai),
            patch("bosshunter.ai.scorer.update_job_quick_score"),
            patch("bosshunter.ai.scorer.update_job_score"),
            patch("bosshunter.ai.scorer.update_job_status"),
        ):
            thread = Thread(target=run_scoring)
            thread.start()
            self.assertTrue(ai_started.wait(0.5))
            stop_event.set()
            self.assertTrue(finished.wait(0.5))
            release_ai.set()
            thread.join(1)

        db.close.assert_called_once()

    def test_scoring_processes_all_unscored_pending_jobs(self):
        db = MagicMock()
        old_job = _job("old")
        new_job = _job("new")

        with (
            patch("bosshunter.ai.scorer.get_db", return_value=db),
            patch("bosshunter.ai.scorer._load_resume", return_value="真实简历"),
            patch("bosshunter.ai.scorer.get_jobs_by_status", return_value=[old_job, new_job]),
            patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
            patch(
                "bosshunter.ai.scorer._call_claude",
                return_value=_score_response(82),
            ) as call_ai,
            patch("bosshunter.ai.scorer.update_job_quick_score") as update_quick_score,
            patch("bosshunter.ai.scorer.update_job_score"),
            patch("bosshunter.ai.scorer.update_job_status"),
        ):
            scored, filtered = scorer.score_jobs(
                {"scoring": {"threshold": 70}},
            )

        self.assertEqual((scored, filtered), (2, 0))
        self.assertEqual(call_ai.call_count, 2)
        self.assertEqual(
            [item.args for item in update_quick_score.call_args_list],
            [(db, "old", 80), (db, "new", 80)],
        )

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
                    _score_response(82),
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
                    _score_response(78),
                ],
            ) as call_ai,
            patch("bosshunter.ai.scorer.update_job_quick_score"),
            patch("bosshunter.ai.scorer.update_job_score"),
            patch("bosshunter.ai.scorer.update_job_status"),
        ):
            scored, _ = scorer.score_jobs({
                "ai": {"scoring_second_review": False},
                "scoring": {"threshold": 70},
            })

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
                    _score_response(82),
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
                    _score_response(82),
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

    def test_pause_reason_carries_error_kind_and_status_code(self):
        db = MagicMock()
        job = _job("1")
        checkpoints: list[dict] = []

        with (
            patch("bosshunter.ai.scorer.get_db", return_value=db),
            patch("bosshunter.ai.scorer._load_resume", return_value="真实简历"),
            patch("bosshunter.ai.scorer.get_jobs_by_status", return_value=[job]),
            patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
            patch(
                "bosshunter.ai.scorer._call_claude",
                side_effect=credentials.AIRequestError("request_failed", "AI 服务请求失败", 404),
            ),
            patch("bosshunter.ai.scorer.update_job_quick_score"),
            patch("bosshunter.ai.scorer.update_job_score"),
            patch("bosshunter.ai.scorer.update_job_status"),
        ):
            scored, filtered = scorer.score_jobs(
                {
                    "scoring": {"threshold": 70},
                    "_workbench_score_checkpoint": checkpoints.append,
                }
            )

        self.assertEqual((scored, filtered), (0, 0))
        self.assertEqual(checkpoints[-1]["status"], "paused")
        self.assertEqual(
            checkpoints[-1]["pause_reason"],
            "AI 服务请求失败 (request_failed, status=404)",
        )

    def test_truncation_retry_pause_keeps_retry_context(self):
        db = MagicMock()
        job = _job("1")
        checkpoints: list[dict] = []

        with (
            patch("bosshunter.ai.scorer.get_db", return_value=db),
            patch("bosshunter.ai.scorer._load_resume", return_value="真实简历"),
            patch("bosshunter.ai.scorer.get_jobs_by_status", return_value=[job]),
            patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
            patch(
                "bosshunter.ai.scorer._call_claude",
                side_effect=[
                    credentials.AIRequestError("output_truncated", "AI 返回内容因输出 Token 上限被截断"),
                    credentials.AIRequestError("auth", "AI API Key 无效或当前模型没有访问权限", 401),
                ],
            ),
            patch("bosshunter.ai.scorer.update_job_quick_score"),
            patch("bosshunter.ai.scorer.update_job_score"),
            patch("bosshunter.ai.scorer.update_job_status"),
        ):
            scored, filtered = scorer.score_jobs(
                {
                    "scoring": {"threshold": 70},
                    "_workbench_score_checkpoint": checkpoints.append,
                }
            )

        self.assertEqual((scored, filtered), (0, 0))
        self.assertEqual(checkpoints[-1]["status"], "paused")
        pause_reason = checkpoints[-1]["pause_reason"]
        self.assertIn("增大输出 Token 重试后失败", pause_reason)
        self.assertIn("(auth, status=401)", pause_reason)

    def test_empty_response_retries_then_scores_job(self):
        db = MagicMock()
        job = _job("empty-retry")

        with (
            patch("bosshunter.ai.scorer.get_db", return_value=db),
            patch("bosshunter.ai.scorer._load_resume", return_value="真实简历"),
            patch("bosshunter.ai.scorer.get_jobs_by_status", return_value=[job]),
            patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
            patch(
                "bosshunter.ai.scorer._call_claude",
                side_effect=[
                    credentials.AIRequestError("empty_response", "AI 服务没有返回文本内容，可能只返回了思考过程"),
                    _score_response(82),
                ],
            ) as call_ai,
            patch("bosshunter.ai.scorer.update_job_quick_score"),
            patch("bosshunter.ai.scorer.update_job_score"),
            patch("bosshunter.ai.scorer.update_job_status") as update_status,
        ):
            scored, filtered = scorer.score_jobs({"scoring": {"threshold": 70}})

        self.assertEqual((scored, filtered), (1, 0))
        self.assertEqual(call_ai.call_count, 2)
        update_status.assert_called_once_with(db, "empty-retry", "ready")

    def test_persistent_empty_response_fails_single_job_and_continues_batch(self):
        db = MagicMock()
        jobs = [_job("1"), _job("2")]
        logs: list[str] = []
        checkpoints: list[dict] = []

        with (
            patch("bosshunter.ai.scorer.get_db", return_value=db),
            patch("bosshunter.ai.scorer._load_resume", return_value="真实简历"),
            patch("bosshunter.ai.scorer.get_jobs_by_status", return_value=jobs),
            patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
            patch(
                "bosshunter.ai.scorer._call_claude",
                side_effect=[
                    credentials.AIRequestError("empty_response", "AI 服务没有返回文本内容，可能只返回了思考过程"),
                    credentials.AIRequestError("empty_response", "AI 服务没有返回文本内容，可能只返回了思考过程"),
                    _score_response(82),
                ],
            ) as call_ai,
            patch("bosshunter.ai.scorer.update_job_quick_score"),
            patch("bosshunter.ai.scorer.update_job_score") as update_score,
            patch("bosshunter.ai.scorer.update_job_status") as update_status,
        ):
            scored, filtered = scorer.score_jobs(
                {
                    "scoring": {"threshold": 70},
                    "_workbench_log": logs.append,
                    "_workbench_score_checkpoint": checkpoints.append,
                }
            )

        self.assertEqual((scored, filtered), (1, 0))
        self.assertEqual(call_ai.call_count, 3)
        self.assertEqual(update_score.call_count, 2)
        self.assertEqual(
            update_score.call_args_list[0].args,
            (db, "1", 0, "AI评分失败: AI 未返回评分内容"),
        )
        update_status.assert_called_once_with(db, "2", "ready")
        self.assertTrue(any("已跳过 公司 1｜AI 产品经理 1" in message for message in logs))
        self.assertEqual(checkpoints[-1]["status"], "completed_with_errors")

    def test_truncation_retry_empty_response_stays_job_level(self):
        db = MagicMock()
        job = _job("1")

        with (
            patch("bosshunter.ai.scorer.get_db", return_value=db),
            patch("bosshunter.ai.scorer._load_resume", return_value="真实简历"),
            patch("bosshunter.ai.scorer.get_jobs_by_status", return_value=[job]),
            patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
            patch(
                "bosshunter.ai.scorer._call_claude",
                side_effect=[
                    credentials.AIRequestError("output_truncated", "AI 返回内容因输出 Token 上限被截断"),
                    credentials.AIRequestError("empty_response", "AI 服务没有返回文本内容，可能只返回了思考过程"),
                    _score_response(82),
                ],
            ) as call_ai,
            patch("bosshunter.ai.scorer.update_job_quick_score"),
            patch("bosshunter.ai.scorer.update_job_score"),
            patch("bosshunter.ai.scorer.update_job_status") as update_status,
        ):
            scored, filtered = scorer.score_jobs({"scoring": {"threshold": 70}})

        self.assertEqual((scored, filtered), (1, 0))
        self.assertEqual(call_ai.call_count, 3)
        update_status.assert_called_once_with(db, "1", "ready")


class GreeterTokenResilienceTests(unittest.TestCase):
    def test_style_guard_flags_template_language_and_technical_stacking(self):
        greeting = (
            "看到这个岗位挺有共鸣，我一直在做Agent、RAG、Prompt和MCP项目，"
            "可以完成从0到1的完整闭环，期待进一步沟通。"
        )

        issues = greeter._greeting_style_issues(greeting)

        self.assertTrue(any("模板化开头" in issue for issue in issues))
        self.assertTrue(any("求职套话" in issue for issue in issues))
        self.assertTrue(any("技术名词" in issue for issue in issues))

    def test_style_guard_flags_an_opening_already_used_in_the_batch(self):
        greeting = "复杂流程里最关键的是先把异常边界定义清楚，我有相关需求梳理经验，可以交流下具体场景。"

        issues = greeter._greeting_style_issues(
            greeting,
            [greeter._opening_signature(greeting)],
        )

        self.assertIn("本批次已使用相同开头，请换一种自然切入方式", issues)

    def test_portfolio_is_only_added_when_the_job_explicitly_requests_it(self):
        config = {
            "profile": {
                "portfolio_url": "https://portfolio.example",
                "extra_highlights": ["有用户研究经验"],
            }
        }
        ordinary_job = _job("ordinary")
        design_job = {**_job("design"), "jd": "请提供交互设计案例和原型作品集。"}

        with patch("bosshunter.ai.greeter._call_claude", return_value="生成结果") as call_ai:
            greeter._generate_greeting_once(ordinary_job, "匿名简历摘要", config)
            ordinary_prompt = call_ai.call_args.args[0]
            greeter._generate_greeting_once(design_job, "匿名简历摘要", config)
            design_prompt = call_ai.call_args.args[0]

        self.assertNotIn("https://portfolio.example", ordinary_prompt)
        self.assertIn("https://portfolio.example", design_prompt)

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

    def test_existing_greeting_is_preserved_and_marked_ready(self):
        db = MagicMock()
        existing = {**_job("existing"), "greeting": "人工编辑后的招呼语"}
        new_job = _job("new")
        logs: list[str] = []
        config = {
            "ai": {"greeting_max_iterations": 0},
            "_workbench_log": logs.append,
        }

        with (
            patch("bosshunter.ai.greeter.get_db", return_value=db),
            patch("bosshunter.ai.greeter.get_jobs_by_status", return_value=[existing, new_job]),
            patch("bosshunter.ai.greeter._get_resume_summary", return_value="真实简历摘要"),
            patch("bosshunter.ai.greeter._call_claude", return_value="新生成的招呼语") as call_ai,
            patch("bosshunter.ai.greeter.update_job_greeting") as update_greeting,
            patch("bosshunter.ai.greeter.update_job_status") as update_status,
        ):
            count = greeter.generate_greetings(config)

        self.assertEqual(count, 1)
        call_ai.assert_called_once()
        update_greeting.assert_called_once_with(db, "new", "新生成的招呼语")
        self.assertIn((db, "existing", "ready"), [call.args for call in update_status.call_args_list])
        self.assertEqual(config["_workbench_greeting_report"]["skipped_existing"], 1)
        self.assertTrue(any("不会用 AI 覆盖" in message for message in logs))

    def test_style_guard_rewrites_even_when_model_review_is_malformed(self):
        db = MagicMock()
        jobs = [_job("style-rewrite")]

        with (
            patch("bosshunter.ai.greeter.get_db", return_value=db),
            patch("bosshunter.ai.greeter.get_jobs_by_status", return_value=jobs),
            patch("bosshunter.ai.greeter._get_resume_summary", return_value="匿名简历摘要"),
            patch(
                "bosshunter.ai.greeter._call_claude",
                side_effect=[
                    "看到这个岗位挺有共鸣，我一直在做相关项目，期待进一步沟通。",
                    "评分很好，但没有按 JSON 返回。",
                    "复杂流程先理清异常边界更重要，我有相关需求梳理经验，可以交流下具体场景。",
                ],
            ) as call_ai,
            patch("bosshunter.ai.greeter.update_job_greeting") as update_greeting,
            patch("bosshunter.ai.greeter.update_job_status"),
        ):
            count = greeter.generate_greetings(
                {"ai": {"greeting_max_iterations": 1}}
            )

        self.assertEqual(count, 1)
        self.assertEqual(call_ai.call_count, 3)
        update_greeting.assert_called_once_with(
            db,
            "style-rewrite",
            "复杂流程先理清异常边界更重要，我有相关需求梳理经验，可以交流下具体场景。",
        )

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

    def test_empty_response_retries_then_generates_greeting(self):
        db = MagicMock()
        jobs = [_job("empty-retry")]

        with (
            patch("bosshunter.ai.greeter.get_db", return_value=db),
            patch("bosshunter.ai.greeter.get_jobs_by_status", return_value=jobs),
            patch("bosshunter.ai.greeter._get_resume_summary", return_value="真实简历摘要"),
            patch(
                "bosshunter.ai.greeter._call_claude",
                side_effect=[
                    credentials.AIRequestError("empty_response", "AI 服务没有返回文本内容，可能只返回了思考过程"),
                    "第二次生成成功的个性化招呼语",
                ],
            ) as call_ai,
            patch("bosshunter.ai.greeter.update_job_greeting") as update_greeting,
            patch("bosshunter.ai.greeter.update_job_status"),
            patch("bosshunter.ai.greeter.add_history") as add_history,
        ):
            count = greeter.generate_greetings(
                {"ai": {"greeting_max_attempts": 2, "greeting_max_iterations": 0}}
            )

        self.assertEqual(count, 1)
        self.assertEqual(call_ai.call_count, 2)
        update_greeting.assert_called_once_with(db, "empty-retry", "第二次生成成功的个性化招呼语")
        add_history.assert_not_called()

    def test_persistent_empty_response_fails_job_and_continues_batch(self):
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
                    credentials.AIRequestError("empty_response", "AI 服务没有返回文本内容，可能只返回了思考过程"),
                    credentials.AIRequestError("empty_response", "AI 服务没有返回文本内容，可能只返回了思考过程"),
                    "岗位2的个性化招呼语",
                ],
            ) as call_ai,
            patch("bosshunter.ai.greeter.update_job_greeting") as update_greeting,
            patch("bosshunter.ai.greeter.update_job_status") as update_status,
            patch("bosshunter.ai.greeter.add_history") as add_history,
        ):
            count = greeter.generate_greetings(
                {
                    "ai": {"greeting_max_attempts": 2, "greeting_max_iterations": 0},
                    "_workbench_log": logs.append,
                }
            )

        self.assertEqual(count, 1)
        self.assertEqual(call_ai.call_count, 3)
        update_greeting.assert_called_once_with(db, "2", "岗位2的个性化招呼语")
        update_status.assert_called_once_with(db, "2", "ready")
        add_history.assert_called_once_with(db, "1", "greeting_failed", "AI 未返回完整招呼语，岗位保留为待生成")
        self.assertTrue(any("已跳过 公司 1｜AI 产品经理 1" in message for message in logs))
        self.assertFalse(any("安全暂停" in message for message in logs))

    def test_review_empty_response_keeps_draft_and_continues_batch(self):
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
                    "这是岗位1的个性化招呼语。",
                    credentials.AIRequestError("empty_response", "AI 服务没有返回文本内容，可能只返回了思考过程"),
                    "这是岗位2的个性化招呼语。",
                    credentials.AIRequestError("empty_response", "AI 服务没有返回文本内容，可能只返回了思考过程"),
                ],
            ) as call_ai,
            patch("bosshunter.ai.greeter.update_job_greeting") as update_greeting,
            patch("bosshunter.ai.greeter.update_job_status"),
        ):
            count = greeter.generate_greetings(
                {"ai": {"greeting_max_iterations": 1}, "_workbench_log": logs.append}
            )

        self.assertEqual(count, 2)
        self.assertEqual(call_ai.call_count, 4)
        update_greeting.assert_any_call(db, "1", "这是岗位1的个性化招呼语。")
        update_greeting.assert_any_call(db, "2", "这是岗位2的个性化招呼语。")
        self.assertTrue(any("质量检查未返回内容，已保留可用招呼语并继续" in message for message in logs))
        self.assertFalse(any("安全暂停" in message for message in logs))


if __name__ == "__main__":
    unittest.main()
