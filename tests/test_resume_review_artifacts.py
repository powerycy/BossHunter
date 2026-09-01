import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bosshunter.db import get_db


class ResumeMasterPolicyTests(unittest.TestCase):
    def test_project_order_can_change_without_triggering_deletion(self):
        from bosshunter.ai.resume import _find_project_preservation_issues

        source = """# 候选人
## 项目经历
### BossHunter｜AI 产品经理
- 完成真实发送验证。
### 设备知识问答 Agent｜产品经理
- 完成检索评测。
"""
        reordered = """# 候选人
## 项目经历
### 设备知识问答 Agent｜产品经理
- 完成检索评测。
### BossHunter｜AI 产品经理
- 完成真实发送验证。
"""

        self.assertEqual(_find_project_preservation_issues(reordered, source), [])

    def test_missing_master_project_is_blocked(self):
        from bosshunter.ai.resume import _find_project_preservation_issues

        source = """## 项目经历
### BossHunter｜AI 产品经理
- 完成真实发送验证。
### 设备知识问答 Agent｜产品经理
- 完成检索评测。
"""
        candidate = """## 项目经历
### BossHunter｜AI 产品经理
- 完成真实发送验证。
"""

        issues = _find_project_preservation_issues(candidate, source)

        self.assertTrue(any("基础简历有 2 个项目" in issue for issue in issues))
        self.assertTrue(any("设备知识问答 Agent" in issue for issue in issues))

    def test_recruiter_placeholder_company_is_removed(self):
        from bosshunter.ai.resume import _remove_recruiter_company_references

        result = _remove_recruiter_company_references(
            "# 张三\n\n求职方向：AI 产品经理｜某大型互联网公司\n\n## 教育经历\n本科\n",
            {"company": "某大型互联网公司", "hr_title": "猎头顾问"},
        )

        self.assertNotIn("某大型互联网公司", result)
        self.assertIn("AI 产品经理", result)

    def test_image_html_has_fixed_sheet_and_end_marker(self):
        from bosshunter.ai.resume import _resume_html

        rendered = _resume_html("# 张三\n\n## 教育经历\n本科\n", image_mode=True)

        self.assertIn("data-resume-sheet", rendered)
        self.assertIn("data-resume-end", rendered)
        self.assertIn("width: 210mm", rendered)
        self.assertIn("min-height: 297mm", rendered)

    @patch("bosshunter.ai.resume.close_tab")
    @patch("bosshunter.ai.resume.screenshot")
    @patch("bosshunter.ai.resume.evaluate")
    @patch("bosshunter.ai.resume.new_tab")
    def test_png_render_requires_complete_a4_sheet(self, new_tab, evaluate, screenshot, close_tab):
        from bosshunter.ai.resume import _render_png_via_cdp

        new_tab.return_value = "target-1"
        evaluate.return_value = {"width": 794, "height": 1123, "endInside": True}

        def write_png(_target, output_path, *, selector=""):
            header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
            Path(output_path).write_bytes(header + (794).to_bytes(4, "big") + (1123).to_bytes(4, "big"))
            return selector == "[data-resume-sheet]"

        screenshot.side_effect = write_png
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "resume.png"
            result = _render_png_via_cdp("<main data-resume-sheet></main>", output)

        self.assertTrue(result)
        close_tab.assert_called_once_with("target-1")


class ResumeSchemaTests(unittest.TestCase):
    def test_review_columns_are_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "bosshunter.db")
            try:
                columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)").fetchall()}
            finally:
                db.close()

        self.assertTrue({
            "resume_source_path",
            "resume_image_path",
            "resume_review_status",
            "resume_generation_source",
            "resume_failure_reason",
            "resume_reviewed_at",
        }.issubset(columns))


if __name__ == "__main__":
    unittest.main()
