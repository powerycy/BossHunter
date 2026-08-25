from unittest.mock import patch

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
