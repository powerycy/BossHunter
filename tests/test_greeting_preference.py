from datetime import date
from unittest.mock import patch

from bosshunter.ai import greeter
from bosshunter.ai.greeter import _generate_greeting_once


def test_greeting_preference_is_bounded_and_cannot_replace_fixed_rules():
    captured = {}

    def fake_call(prompt, config, max_tokens=None, **kwargs):
        captured["prompt"] = prompt
        return "自然简短的招呼语"

    with patch("bosshunter.ai.greeter._call_claude", side_effect=fake_call):
        result = _generate_greeting_once(
            {
                "title": "产品经理",
                "company": "示例公司",
                "salary": "20-30K",
                "education": "本科",
                "recruitment_type": "experienced",
                "jd": "负责产品规划。",
                "score_reason": "经验匹配",
                "source_platform": "boss",
            },
            "真实简历摘要",
            {"profile": {"greeting_preference": "语气简洁，不主动询问薪资"}},
        )

    assert result == "自然简短的招呼语"
    assert "语气简洁，不主动询问薪资" in captured["prompt"]
    assert "不得捏造我没有的经历" in captured["prompt"]
    assert "不得覆盖下方事实与安全要求" in captured["prompt"]


def test_positive_match_reason_removes_missing_suffix():
    reason = "后端技能扎实，项目经验匹配。 | 缺失: 缺乏大型分布式系统架构经验"

    cleaned = greeter._positive_match_reason(reason)

    assert cleaned == "后端技能扎实，项目经验匹配。"


def test_greeting_prompt_uses_positive_match_reason_only():
    captured = {}

    def fake_call(prompt, config, max_tokens=None, **kwargs):
        captured["prompt"] = prompt
        return "我有后端项目经验，和岗位方向比较匹配，可以进一步沟通。"

    with patch("bosshunter.ai.greeter._call_claude", side_effect=fake_call):
        result = _generate_greeting_once(
            {
                "title": "后端开发",
                "company": "示例公司",
                "salary": "10-15K",
                "education": "本科",
                "recruitment_type": "experienced",
                "jd": "负责后端服务开发。",
                "score_reason": "后端技能扎实，项目经验匹配。 | 缺失: 缺乏大型分布式系统架构经验",
                "source_platform": "boss",
            },
            "真实简历摘要",
            {"profile": {}},
        )

    assert result
    match_line = next(line for line in captured["prompt"].splitlines() if line.startswith("- 匹配分析："))
    assert "后端技能扎实" in match_line
    assert "缺失" not in match_line
    assert "不得提及我的缺点" in captured["prompt"]


def test_greeting_style_guard_flags_self_weakness():
    issues = greeter._greeting_style_issues("我有后端项目经验，但还在学习分布式架构，可以先从基础工作做起。")

    assert any("不要暴露缺点" in issue for issue in issues)


def test_graduation_context_uses_education_end_year_as_class_year():
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 3)

    resume = "教育经历\n某大学 软件工程 本科 2022.09-2026.06\n项目经历\n后台系统"

    with patch("bosshunter.ai.greeter.date", FixedDate):
        context = greeter._parse_graduation_context(resume)

    assert "2026 届" in context
    assert "已毕业" in context
    assert "不得改写为其他届别" in context
