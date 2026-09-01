import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class ResumeArtifactTests(unittest.TestCase):
    def test_prompt_forbids_resume_tailoring_artifacts(self):
        from bosshunter.ai.resume import RESUME_TAILOR_PROMPT

        self.assertIn("只输出简历正文", RESUME_TAILOR_PROMPT)
        self.assertIn("不输出任何前言、说明、备注、免责声明", RESUME_TAILOR_PROMPT)
        self.assertIn("不允许单独新增“岗位匹配亮点”", RESUME_TAILOR_PROMPT)
        self.assertIn("{resume_max_pages} 页以内", RESUME_TAILOR_PROMPT)
        self.assertIn("只保留与目标岗位相关的内容", RESUME_TAILOR_PROMPT)
        self.assertIn("项目经历必须按岗位相关度排序", RESUME_TAILOR_PROMPT)
        self.assertIn("尽可能覆盖岗位JD中的职责和要求", RESUME_TAILOR_PROMPT)
        self.assertIn("无法用候选人真实经历支撑的要求不要硬编", RESUME_TAILOR_PROMPT)
        self.assertIn("不要输出JD逐条对照", RESUME_TAILOR_PROMPT)

    def test_finds_resume_artifact_phrases(self):
        from bosshunter.ai.resume import _find_resume_artifacts

        markdown = "以下内容基于原始简历整理。\n\n## 岗位匹配亮点\n- 补充说明：未虚构。"

        artifacts = _find_resume_artifacts(markdown)

        self.assertIn("以下内容基于", artifacts)
        self.assertIn("岗位匹配亮点", artifacts)
        self.assertIn("补充说明", artifacts)
        self.assertIn("未虚构", artifacts)

    def test_finds_expanded_resume_artifact_phrases(self):
        from bosshunter.ai.resume import _find_resume_artifacts

        markdown = (
            "以下为优化后的简历。\n"
            "本次优化根据岗位JD，结合岗位要求，匹配该岗位。\n"
            "这是根据原始简历生成的调整后的简历，也是一份定制简历。"
        )

        artifacts = _find_resume_artifacts(markdown)

        self.assertIn("以下为优化后的", artifacts)
        self.assertIn("本次优化", artifacts)
        self.assertIn("根据岗位JD", artifacts)
        self.assertIn("结合岗位要求", artifacts)
        self.assertIn("匹配该岗位", artifacts)
        self.assertIn("根据原始简历", artifacts)
        self.assertIn("调整后的简历", artifacts)
        self.assertIn("定制简历", artifacts)

    def test_finds_job_tailoring_leakage_phrases(self):
        from bosshunter.ai.resume import _find_resume_artifacts

        markdown = (
            "项目与字节岗位中的要求高度相关，可迁移到AI资讯内容质量评估场景，和岗位要求高度匹配。\n"
            "JD逐条对照：岗位JD覆盖情况如下，无法覆盖的部分已说明。"
        )

        artifacts = _find_resume_artifacts(markdown)

        self.assertIn("岗位中的", artifacts)
        self.assertIn("字节岗位", artifacts)
        self.assertIn("高度相关", artifacts)
        self.assertIn("可迁移到", artifacts)
        self.assertIn("岗位要求", artifacts)
        self.assertIn("高度匹配", artifacts)
        self.assertIn("JD逐条对照", artifacts)
        self.assertIn("岗位JD覆盖", artifacts)
        self.assertIn("无法覆盖", artifacts)

    def test_existing_source_placeholders_are_allowed_but_new_or_rewritten_ones_are_blocked(self):
        from bosshunter.ai.resume import _find_new_placeholders

        base_resume = "# 候选人\n\n电话：[待填写]\n作品集：{{portfolio_url}}\n"

        self.assertEqual(
            _find_new_placeholders(
                "# 候选人\n\n电话：[待填写]\n作品集：{{portfolio_url}}\n",
                base_resume,
            ),
            [],
        )
        self.assertEqual(
            _find_new_placeholders(
                "# 候选人\n\n电话：[请填写电话]\n作品集：{{portfolio_url}}\n",
                base_resume,
            ),
            ["[请填写电话]"],
        )

    def test_integrity_checks_report_new_and_missing_fact_values(self):
        from bosshunter.ai.resume import _find_blocking_integrity_issues

        base_resume = (
            "# 候选人\n\n"
            "## 基本信息\n\n"
            "邮箱：candidate@example.com\n"
            "## 工作经历\n\n"
            "负责内容运营。\n"
        )
        generated = (
            "# 候选人\n\n"
            "## 基本信息\n\n"
            "## 工作经历\n\n"
            "负责内容运营，转化率提升 50%。\n"
        )

        issues = _find_blocking_integrity_issues(generated, base_resume)

        self.assertTrue(any("缺少基础简历中的关键信息" in issue for issue in issues))
        self.assertTrue(any("candidate@example.com" in issue for issue in issues))
        self.assertTrue(any("模型新增了原始简历中不存在的数据" in issue for issue in issues))
        self.assertTrue(any("50%" in issue for issue in issues))

    @patch("bosshunter.ai.resume._render_pdf")
    @patch("bosshunter.ai.resume._call_claude")
    @patch("bosshunter.ai.resume.get_db")
    def test_blocking_validation_failure_is_returned_to_monitor(self, get_db, call_claude, render_pdf):
        from bosshunter.ai.resume import (
            RESUME_COMPLETION_MARKER,
            generate_tailored_resume,
            get_last_resume_failure_reason,
        )

        db = Mock()
        db.execute.return_value.fetchone.return_value = {
            "id": "job-validation",
            "company": "Example",
            "title": "运营",
            "salary": "10-20K",
            "jd": "负责内容运营",
        }
        get_db.return_value = db
        call_claude.return_value = (
            "# 候选人\n\n"
            "## 基本信息\n\n"
            "邮箱：candidate@example.com\n"
            "## 工作经历\n\n"
            "转化率提升 50%。\n"
            f"{RESUME_COMPLETION_MARKER}"
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resume_path = root / "resume.md"
            resume_path.write_text(
                "# 候选人\n\n"
                "## 基本信息\n\n"
                "邮箱：candidate@example.com\n"
                "## 工作经历\n\n"
                "负责内容运营。\n",
                encoding="utf-8",
            )
            result = generate_tailored_resume(
                "job-validation",
                {
                    "profile": {
                        "resume_path": str(resume_path),
                        "resume_output_dir": str(root / "out"),
                    }
                },
            )

        self.assertIsNone(result)
        self.assertIn("事实完整性校验失败", get_last_resume_failure_reason("job-validation"))
        self.assertIn("50%", get_last_resume_failure_reason("job-validation"))
        self.assertEqual(call_claude.call_count, 2)
        render_pdf.assert_not_called()

    @patch("bosshunter.ai.resume._render_pdf")
    @patch("bosshunter.ai.resume._call_claude")
    @patch("bosshunter.ai.resume.get_db")
    def test_dirty_resume_output_is_still_written_for_user_review(self, get_db, call_claude, render_pdf):
        from bosshunter.ai.resume import generate_tailored_resume

        db = Mock()
        db.execute.return_value.fetchone.return_value = {
            "id": "job-1",
            "company": "Example",
            "title": "Engineer",
            "salary": "10-20K",
            "jd": "需要 Python 经验",
        }
        get_db.return_value = db
        call_claude.return_value = "## 岗位匹配亮点\n以下内容基于原始简历整理。"
        render_pdf.return_value = False

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resume_path = root / "resume.md"
            output_dir = root / "out"
            resume_path.write_text("# 张三\n\nPython 工程师", encoding="utf-8")

            result = generate_tailored_resume(
                "job-1",
                {
                    "profile": {
                        "resume_path": str(resume_path),
                        "resume_output_dir": str(output_dir),
                    }
                },
            )

            self.assertIsNotNone(result)
            self.assertTrue(result.exists())
            self.assertIn("岗位匹配亮点", result.read_text(encoding="utf-8"))

        render_pdf.assert_called_once()

    @patch("bosshunter.ai.resume._render_pdf")
    @patch("bosshunter.ai.resume._call_claude")
    @patch("bosshunter.ai.resume.get_db")
    def test_incomplete_resume_output_is_still_written_for_user_review(self, get_db, call_claude, render_pdf):
        from bosshunter.ai.resume import generate_tailored_resume

        db = Mock()
        db.execute.return_value.fetchone.return_value = {
            "id": "job-1",
            "company": "格灵深瞳",
            "title": "科技记者/PR媒体关系",
            "salary": "20-30K",
            "jd": "负责AI科技内容传播、媒体关系、舆情、官网、公众号、视频号。",
        }
        get_db.return_value = db
        call_claude.return_value = "# 候选人\n\n## 基本信息\n\n面向周围神经外科领域专家及临床医生的专业学术交流项目，围绕周围神经疾病诊疗、手术技术、病例"
        render_pdf.return_value = False

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resume_path = root / "resume.md"
            output_dir = root / "out"
            resume_path.write_text(
                "# 候选人\n\n## 基本信息\n\n## 工作经历\n\n## 教育经历\n\n## 相关技能\n",
                encoding="utf-8",
            )

            result = generate_tailored_resume(
                "job-1",
                {
                    "profile": {
                        "resume_path": str(resume_path),
                        "resume_output_dir": str(output_dir),
                    }
                },
            )

            self.assertIsNotNone(result)
            self.assertTrue(result.exists())
            self.assertIn("周围神经疾病诊疗", result.read_text(encoding="utf-8"))

        render_pdf.assert_called_once()

    @patch("bosshunter.ai.resume._render_pdf")
    @patch("bosshunter.ai.resume._call_claude")
    @patch("bosshunter.ai.resume.get_db")
    def test_completed_resume_marker_is_removed_before_writing(self, get_db, call_claude, render_pdf):
        from bosshunter.ai.resume import RESUME_COMPLETION_MARKER, generate_tailored_resume

        db = Mock()
        db.execute.return_value.fetchone.return_value = {
            "id": "job-1",
            "company": "格灵深瞳",
            "title": "科技记者/PR媒体关系",
            "salary": "20-30K",
            "jd": "负责AI科技内容传播、媒体关系、舆情、官网、公众号、视频号。",
        }
        get_db.return_value = db
        tailored = (
            "# 候选人\n\n"
            "## 基本信息\n\n"
            "## 个人优势\n\n"
            "具备AI科技内容传播、媒体关系和品牌传播经验。\n\n"
            "## 工作经历\n\n"
            "- 负责公众号、视频号内容策划和技术型业务表达。\n\n"
            "## 教育经历\n\n"
            "本科。\n\n"
            "## 相关技能\n\n"
            "- AI内容运营、PR传播、媒体沟通。\n\n"
            f"{RESUME_COMPLETION_MARKER}"
        )
        call_claude.return_value = tailored
        render_pdf.return_value = False

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resume_path = root / "resume.md"
            output_dir = root / "out"
            resume_path.write_text(
                "# 候选人\n\n## 基本信息\n\n## 个人优势\n\n旧内容。\n\n## 工作经历\n\n旧内容。\n\n## 教育经历\n\n本科。\n\n## 相关技能\n\n旧技能。\n",
                encoding="utf-8",
            )

            result = generate_tailored_resume(
                "job-1",
                {
                    "profile": {
                        "resume_path": str(resume_path),
                        "resume_output_dir": str(output_dir),
                    }
                },
            )

            self.assertIsNotNone(result)
            saved_md = result.read_text(encoding="utf-8")
            self.assertNotIn(RESUME_COMPLETION_MARKER, saved_md)
            self.assertIn("AI科技内容传播、媒体关系和品牌传播经验", saved_md)

    @patch("bosshunter.ai.resume._render_pdf")
    @patch("bosshunter.ai.resume._call_claude")
    @patch("bosshunter.ai.resume.get_db")
    def test_nearly_unchanged_resume_output_is_still_written_for_user_review(
        self, get_db, call_claude, render_pdf
    ):
        from bosshunter.ai.resume import RESUME_COMPLETION_MARKER, generate_tailored_resume

        db = Mock()
        db.execute.return_value.fetchone.return_value = {
            "id": "job-1",
            "company": "格灵深瞳",
            "title": "科技记者/PR媒体关系",
            "salary": "20-30K",
            "jd": "负责AI科技内容传播、媒体关系、舆情、官网、公众号、视频号。",
        }
        get_db.return_value = db
        render_pdf.return_value = False

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resume_path = root / "resume.md"
            output_dir = root / "out"
            base_resume = (
                "# 候选人\n\n"
                "## 基本信息\n\n"
                "## 个人优势\n\n"
                "原始优势内容。" * 80 + "\n\n"
                "## 工作经历\n\n"
                "原始工作内容。" * 80 + "\n\n"
                "## 教育经历\n\n"
                "本科。\n\n"
                "## 相关技能\n\n"
                "原始技能。\n"
            )
            resume_path.write_text(base_resume, encoding="utf-8")
            call_claude.return_value = f"{base_resume}\n{RESUME_COMPLETION_MARKER}"

            result = generate_tailored_resume(
                "job-1",
                {
                    "profile": {
                        "resume_path": str(resume_path),
                        "resume_output_dir": str(output_dir),
                    }
                },
            )

            self.assertIsNotNone(result)
            self.assertTrue(result.exists())
            self.assertIn("原始工作内容", result.read_text(encoding="utf-8"))

        self.assertEqual(call_claude.call_count, 1)
        render_pdf.assert_called_once()

    @patch("bosshunter.ai.resume._render_pdf")
    @patch("bosshunter.ai.resume._call_claude")
    @patch("bosshunter.ai.resume.get_db")
    def test_overlong_resume_output_is_saved_after_compression_retry_still_exceeds_limit(
        self, get_db, call_claude, render_pdf
    ):
        from bosshunter.ai.resume import RESUME_COMPLETION_MARKER, generate_tailored_resume

        db = Mock()
        db.execute.return_value.fetchone.return_value = {
            "id": "job-1",
            "company": "字节跳动",
            "title": "AI资讯内容运营",
            "salary": "15-30K",
            "jd": "负责AI资讯内容安全和质量评测、内容审核、Skill Agent和AI应用落地。",
        }
        get_db.return_value = db

        overlong_section = "AI内容安全质量评测、审核规则沉淀、项目协同推进。" * 220
        tailored = (
            "# 候选人\n\n"
            "## 基本信息\n\n"
            "北京｜AI内容运营\n\n"
            "## 个人优势\n\n"
            f"{overlong_section}\n\n"
            "## 工作经历\n\n"
            "- 负责AI内容运营和跨团队协作。\n\n"
            "## 教育经历\n\n"
            "本科。\n\n"
            "## 相关技能\n\n"
            "- AI内容评测、内容安全、项目管理。\n\n"
            f"{RESUME_COMPLETION_MARKER}"
        )
        call_claude.return_value = tailored
        render_pdf.return_value = False

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resume_path = root / "resume.md"
            output_dir = root / "out"
            resume_path.write_text(
                "# 候选人\n\n## 基本信息\n\n## 个人优势\n\n旧内容。\n\n## 工作经历\n\n旧内容。\n\n## 教育经历\n\n本科。\n\n## 相关技能\n\n旧技能。\n",
                encoding="utf-8",
            )

            result = generate_tailored_resume(
                "job-1",
                {
                    "profile": {
                        "resume_path": str(resume_path),
                        "resume_output_dir": str(output_dir),
                    }
                },
            )

            self.assertIsNotNone(result)
            self.assertTrue(result.exists())
            self.assertIn(overlong_section, result.read_text(encoding="utf-8"))

        self.assertEqual(call_claude.call_count, 2)
        render_pdf.assert_called_once()

    @patch("bosshunter.ai.resume._render_pdf")
    @patch("bosshunter.ai.resume._call_claude")
    @patch("bosshunter.ai.resume.get_db")
    def test_overlong_resume_output_is_retried_with_compression_instruction(self, get_db, call_claude, render_pdf):
        from bosshunter.ai.resume import RESUME_COMPLETION_MARKER, generate_tailored_resume

        db = Mock()
        db.execute.return_value.fetchone.return_value = {
            "id": "job-1",
            "company": "卓世科技",
            "title": "AI产品运营",
            "salary": "15-25K",
            "jd": "负责AI产品运营、内容增长、小红书和抖音平台案例沉淀。",
        }
        get_db.return_value = db

        overlong_section = "AI产品运营、内容增长、小红书爆款、抖音起号、项目协同推进。" * 220
        overlong_tailored = (
            "# 候选人\n\n"
            "## 基本信息\n\n"
            "北京｜AI产品运营\n\n"
            "## 个人优势\n\n"
            f"{overlong_section}\n\n"
            "## 工作经历\n\n"
            "- 负责AI产品运营和增长协作。\n\n"
            "## 教育经历\n\n"
            "本科。\n\n"
            "## 相关技能\n\n"
            "- AI产品运营、内容增长、小红书、抖音。\n\n"
            f"{RESUME_COMPLETION_MARKER}"
        )
        compressed_tailored = (
            "# 候选人\n\n"
            "## 基本信息\n\n"
            "北京｜AI产品运营\n\n"
            "## 个人优势\n\n"
            "具备AI产品运营、内容增长、小红书爆款和抖音从0到1起号经验。\n\n"
            "## 工作经历\n\n"
            "- 围绕AI产品运营目标推进内容增长、用户反馈整理和跨团队协作。\n\n"
            "## 教育经历\n\n"
            "本科。\n\n"
            "## 相关技能\n\n"
            "- AI产品运营、内容增长、小红书、抖音、AIGC内容。\n\n"
            f"{RESUME_COMPLETION_MARKER}"
        )
        call_claude.side_effect = [overlong_tailored, compressed_tailored]
        render_pdf.return_value = False

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resume_path = root / "resume.md"
            output_dir = root / "out"
            resume_path.write_text(
                "# 候选人\n\n## 基本信息\n\n## 个人优势\n\n旧内容。\n\n## 工作经历\n\n旧内容。\n\n## 教育经历\n\n本科。\n\n## 相关技能\n\n旧技能。\n",
                encoding="utf-8",
            )

            result = generate_tailored_resume(
                "job-1",
                {
                    "profile": {
                        "resume_path": str(resume_path),
                        "resume_output_dir": str(output_dir),
                    },
                    "ai": {"resume_max_retries": 1},
                },
            )

            self.assertIsNotNone(result)
            saved_md = result.read_text(encoding="utf-8")
            self.assertIn("小红书爆款和抖音从0到1起号经验", saved_md)

        self.assertEqual(call_claude.call_count, 2)
        retry_prompt = call_claude.call_args_list[1].args[0]
        self.assertIn("上一次生成结果质量检查未通过", retry_prompt)
        self.assertIn("简历内容过长", retry_prompt)
        self.assertIn("必须压缩到", retry_prompt)

    @patch("bosshunter.ai.resume._render_pdf")
    @patch("bosshunter.ai.resume._call_claude")
    @patch("bosshunter.ai.resume.get_db")
    def test_pdf_over_page_limit_is_kept_and_marked_ready(self, get_db, call_claude, render_pdf):
        from bosshunter.ai.resume import RESUME_COMPLETION_MARKER, generate_tailored_resume

        db = Mock()
        db.execute.return_value.fetchone.return_value = {
            "id": "job-1",
            "company": "字节跳动",
            "title": "AI资讯内容运营",
            "salary": "15-30K",
            "jd": "负责AI资讯内容安全和质量评测、内容审核、Skill Agent和AI应用落地。",
        }
        get_db.return_value = db

        tailored = (
            "# 候选人\n\n"
            "## 基本信息\n\n"
            "北京｜AI内容运营\n\n"
            "## 个人优势\n\n"
            "具备AI内容安全质量评测、内容审核和项目管理经验。\n\n"
            "## 工作经历\n\n"
            "- 负责AI内容运营和跨团队协作。\n\n"
            "## 教育经历\n\n"
            "本科。\n\n"
            "## 相关技能\n\n"
            "- AI内容评测、内容安全、项目管理。\n\n"
            f"{RESUME_COMPLETION_MARKER}"
        )
        call_claude.return_value = tailored

        def write_four_page_pdf(_markdown_text, output_path):
            output_path.write_bytes(
                b"%PDF-1.4\n"
                b"1 0 obj<</Type /Page>>endobj\n"
                b"2 0 obj<</Type /Page>>endobj\n"
                b"3 0 obj<</Type /Page>>endobj\n"
                b"4 0 obj<</Type /Page>>endobj\n"
            )
            return True

        render_pdf.side_effect = write_four_page_pdf

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resume_path = root / "resume.md"
            output_dir = root / "out"
            resume_path.write_text(
                "# 候选人\n\n## 基本信息\n\n## 个人优势\n\n旧内容。\n\n## 工作经历\n\n旧内容。\n\n## 教育经历\n\n本科。\n\n## 相关技能\n\n旧技能。\n",
                encoding="utf-8",
            )

            result = generate_tailored_resume(
                "job-1",
                {
                    "profile": {
                        "resume_path": str(resume_path),
                        "resume_output_dir": str(output_dir),
                    }
                },
            )

            self.assertIsNotNone(result)
            self.assertEqual(result.suffix, ".pdf")
            self.assertTrue(result.exists())
            self.assertTrue((output_dir / "字节跳动_AI资讯内容运营_job-1.md").exists())

        self.assertEqual(db.execute.call_count, 2)
        db.commit.assert_called_once()


class ResumePdfRuntimeTests(unittest.TestCase):
    @patch("bosshunter.ai.resume.wait_for_load", return_value=True)
    @patch("bosshunter.ai.resume.close_tab")
    @patch("bosshunter.ai.resume.print_pdf")
    @patch("bosshunter.ai.resume.new_tab")
    def test_render_pdf_via_cdp_uses_browser_facade(self, new_tab, print_pdf, close_tab, wait_for_load):
        from bosshunter.ai.resume import _render_pdf_via_cdp

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "resume.pdf"
            new_tab.return_value = "target-1"
            print_pdf.side_effect = lambda target, file_path: output.write_bytes(b"pdf") or True

            result = _render_pdf_via_cdp("<html><body>ok</body></html>", output)

        self.assertTrue(result)
        new_tab.assert_called_once()
        self.assertTrue(new_tab.call_args.args[0].startswith("file:///"))
        self.assertIs(new_tab.call_args.kwargs["background"], True)
        wait_for_load.assert_called_once_with("target-1", timeout=10)
        print_pdf.assert_called_once_with("target-1", output.resolve())
        close_tab.assert_called_once_with("target-1")

    @patch("bosshunter.ai.resume.wait_for_load", return_value=True)
    @patch("bosshunter.ai.resume.close_tab")
    @patch("bosshunter.ai.resume.print_pdf")
    @patch("bosshunter.ai.resume.new_tab", return_value="target-1")
    def test_render_pdf_via_cdp_resolves_relative_output_for_runtime(
        self, new_tab, print_pdf, close_tab, wait_for_load
    ):
        from bosshunter.ai.resume import _render_pdf_via_cdp

        def write_pdf(_target, destination):
            self.assertTrue(destination.is_absolute())
            destination.write_bytes(b"%PDF-1.4\n")
            return True

        print_pdf.side_effect = write_pdf
        with tempfile.TemporaryDirectory() as tmp:
            expected_output = (Path(tmp) / "resume.pdf").resolve()
            output = Path(os.path.relpath(expected_output, Path.cwd()))
            self.assertFalse(output.is_absolute())
            result = _render_pdf_via_cdp("<html><body>中文</body></html>", output)

        self.assertTrue(result)
        print_pdf.assert_called_once_with("target-1", expected_output)

    @patch("bosshunter.ai.resume.wait_for_load", return_value=True)
    @patch("bosshunter.ai.resume.close_tab")
    @patch("bosshunter.ai.resume.print_pdf")
    @patch("bosshunter.ai.resume.new_tab")
    def test_render_pdf_via_cdp_rejects_missing_output_file(
        self, new_tab, print_pdf, close_tab, wait_for_load
    ):
        from bosshunter.ai.resume import _render_pdf_via_cdp

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "missing.pdf"
            new_tab.return_value = "target-1"
            print_pdf.return_value = True

            result = _render_pdf_via_cdp("<html><body>ok</body></html>", output)

        self.assertFalse(result)
        self.assertEqual(new_tab.call_count, 2)
        self.assertEqual(wait_for_load.call_count, 2)
        self.assertEqual(print_pdf.call_count, 2)
        self.assertEqual(close_tab.call_count, 2)

    @patch("bosshunter.ai.resume._render_pdf_via_cdp", return_value=False)
    def test_render_pdf_does_not_emit_fontless_fallback_pdf(self, render_via_cdp):
        from bosshunter.ai.resume import _render_pdf

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "resume.pdf"

            result = _render_pdf("# 中文简历\n\n正常正文", output)

            self.assertFalse(result)
            self.assertFalse(output.exists())
        render_via_cdp.assert_called_once()


if __name__ == "__main__":
    unittest.main()
