import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from bosshunter.db import get_db
from bosshunter.resume_builder.service import (
	MAX_STAR_BATCHES,
	ResumeBuilderError,
	_star_batches,
	activate_career_profile,
	compose_career_profile,
	compose_resume_version,
	delete_resume_source,
	extract_source_facts,
	ingest_resume_source,
	refresh_profile_clarifications,
)
from bosshunter.resume_builder.store import (
	list_clarifications,
	list_facts,
	list_profile_versions,
	list_sources,
	list_versions,
	update_clarification,
	update_fact,
)


class ResumeBuilderTests(unittest.TestCase):
	def setUp(self):
		self.temporary = tempfile.TemporaryDirectory()
		self.base_dir = Path(self.temporary.name)
		self.connection = get_db(self.base_dir / "data" / "bosshunter.db")
		self.source_dir = self.base_dir / "data" / "resume_sources"
		self.output_dir = self.base_dir / "data" / "resumes"

	def tearDown(self):
		self.connection.close()
		self.temporary.cleanup()

	def _source(self, text: str = "项目 Alpha\n使用 Python 和 SQLite 开发本地工具，2024 年服务 20 名用户。"):
		source, duplicate = ingest_resume_source(
			self.connection,
			filename="技术作品.md",
			content=text.encode("utf-8"),
			storage_dir=self.source_dir,
		)
		self.assertFalse(duplicate)
		return source

	def _extract(self, source_id: str):
		def caller(prompt, config, max_tokens, **kwargs):
			self.assertIn("原始材料", prompt)
			return json.dumps({
				"stories": [
					{
						"title": {"text": "项目 Alpha", "evidence": "项目 Alpha"},
						"situation": None,
						"task": None,
						"action": {
							"text": "使用 Python 和 SQLite 开发本地工具，2024 年服务 20 名用户。",
							"evidence": "使用 Python 和 SQLite 开发本地工具，2024 年服务 20 名用户。",
						},
						"result": None,
						"technologies": [
							{
								"name": "Python",
								"evidence": "使用 Python 和 SQLite 开发本地工具，2024 年服务 20 名用户。",
							},
						],
						"professional_skills": [],
						"ownership_level": "unknown",
						"ownership_evidence": "",
						"confidence": 0.96,
					},
					{
						"title": {"text": "项目 Alpha", "evidence": "项目 Alpha"},
						"action": {
							"text": "用户满意度达到 99%",
							"evidence": "使用 Python 和 SQLite 开发本地工具，2024 年服务 20 名用户。",
						},
						"technologies": [],
						"professional_skills": [],
						"ownership_level": "unknown",
						"ownership_evidence": "",
						"confidence": 0.99,
					},
				]
			}, ensure_ascii=False)

		return extract_source_facts(
			self.connection,
			source_id,
			{},
			source_kind="technical_document",
			call_text=caller,
		)

	def test_database_initializes_resume_studio_tables(self):
		tables = {
			row["name"]
			for row in self.connection.execute(
				"SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'resume_%'"
			).fetchall()
		}
		self.assertEqual(
			tables,
			{
				"resume_sources",
				"resume_facts",
				"resume_fact_evidence",
				"resume_clarifications",
				"resume_profile_versions",
				"resume_profile_facts",
				"resume_profile_clarifications",
				"resume_versions",
				"resume_version_facts",
			},
		)

	def test_star_batches_merge_many_sections_and_bound_calls(self):
		many_headings = "\n\n".join(
			f"# 章节 {index}\n使用 Python 处理第 {index} 个问题。" for index in range(80)
		)
		self.assertEqual(len(_star_batches(many_headings)), 1)

		long_document = "\n\n".join(
			f"# 长章节 {index}\n" + ("技术分析与验证。" * 1000) for index in range(16)
		)
		batches = _star_batches(long_document)
		self.assertLessEqual(len(batches), MAX_STAR_BATCHES)
		self.assertTrue(all(batch.strip() for batch in batches))
		self.assertIn("# 长章节 0", batches[0])
		self.assertIn("# 长章节 15", batches[-1])

		source = self._source(many_headings)
		calls = []

		def caller(prompt, config, max_tokens, **kwargs):
			calls.append(kwargs["purpose"])
			return json.dumps({
				"stories": [{
					"title": {"text": "章节 0", "evidence": "章节 0"},
					"action": {
						"text": "使用 Python 处理第 0 个问题。",
						"evidence": "使用 Python 处理第 0 个问题。",
					},
					"technologies": [{
						"name": "Python",
						"evidence": "使用 Python 处理第 0 个问题。",
					}],
					"professional_skills": [],
					"ownership_level": "unknown",
					"ownership_evidence": "",
					"confidence": 0.9,
				}],
			}, ensure_ascii=False)

		facts = extract_source_facts(
			self.connection,
			source["id"],
			{},
			source_kind="technical_document",
			call_text=caller,
		)
		self.assertEqual(len(facts), 1)
		self.assertEqual(calls, ["resume_source_star"])

	def test_database_migrates_legacy_resume_tables_in_place(self):
		legacy_path = self.base_dir / "legacy" / "bosshunter.db"
		legacy_path.parent.mkdir(parents=True)
		legacy = sqlite3.connect(legacy_path)
		legacy.executescript("""
			CREATE TABLE resume_sources (
				id TEXT PRIMARY KEY, filename TEXT NOT NULL, source_type TEXT NOT NULL,
				stored_path TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,
				normalized_text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ready',
				error TEXT, created_at TIMESTAMP, updated_at TIMESTAMP
			);
			CREATE TABLE resume_facts (
				id TEXT PRIMARY KEY, source_id TEXT NOT NULL, category TEXT NOT NULL,
				content TEXT NOT NULL, edited_content TEXT, evidence TEXT NOT NULL,
				confidence REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
				created_at TIMESTAMP, updated_at TIMESTAMP
			);
		""")
		legacy.close()

		migrated = get_db(legacy_path)
		try:
			source_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(resume_sources)")}
			fact_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(resume_facts)")}
			self.assertIn("detected_kind", source_columns)
			self.assertIn("selected_kind", source_columns)
			self.assertIn("structured_data", fact_columns)
			self.assertIn("needs_clarification", fact_columns)
		finally:
			migrated.close()

	def test_ingest_deduplicates_normalized_source_by_sha256(self):
		first = self._source()
		second, duplicate = ingest_resume_source(
			self.connection,
			filename="同一作品的副本.md",
			content="项目 Alpha\r\n使用 Python 和 SQLite 开发本地工具，2024 年服务 20 名用户。\r\n".encode(),
			storage_dir=self.source_dir,
		)

		self.assertTrue(duplicate)
		self.assertEqual(second["id"], first["id"])
		self.assertEqual(len(list_sources(self.connection)), 1)

	def test_extraction_keeps_evidence_backed_fact_and_drops_hallucinated_metric(self):
		source = self._source()
		facts = self._extract(source["id"])

		self.assertEqual(len(facts), 1)
		self.assertEqual(facts[0]["category"], "项目经历")
		self.assertEqual(facts[0]["fact_type"], "star_story")
		self.assertIn("20 名用户", facts[0]["content"])
		self.assertNotIn("99%", facts[0]["content"])
		self.assertTrue(facts[0]["needs_clarification"])
		self.assertEqual(facts[0]["structured_data"]["ownership_level"], "unknown")
		self.assertTrue(facts[0]["evidence_items"])
		self.assertEqual(list_sources(self.connection)[0]["status"], "review")

	def test_star_extraction_retries_invalid_json_once(self):
		source = self._source()
		calls = []

		def caller(prompt, config, max_tokens, **kwargs):
			calls.append(prompt)
			if len(calls) == 1:
				return '{"stories": ['
			return json.dumps({
				"stories": [{
					"title": {"text": "项目 Alpha", "evidence": "项目 Alpha"},
					"action": {
						"text": "使用 Python 和 SQLite 开发本地工具，2024 年服务 20 名用户。",
						"evidence": "使用 Python 和 SQLite 开发本地工具，2024 年服务 20 名用户。",
					},
					"technologies": [],
					"professional_skills": [],
					"ownership_level": "unknown",
					"ownership_evidence": "",
					"confidence": 0.9,
				}],
			}, ensure_ascii=False)

		facts = extract_source_facts(
			self.connection,
			source["id"],
			{},
			source_kind="technical_document",
			call_text=caller,
		)
		self.assertEqual(len(facts), 1)
		self.assertEqual(len(calls), 2)
		self.assertIn("上一次响应不是完整有效的 JSON", calls[1])

	def test_star_extraction_retries_zero_candidates_with_strict_evidence_prompt(self):
		source = self._source()
		calls = []

		def caller(prompt, config, max_tokens, **kwargs):
			calls.append(prompt)
			if len(calls) == 1:
				return json.dumps({
					"stories": [{
						"title": {"text": "项目 Alpha", "evidence": "项目 Alpha"},
						"action": {"text": "构建高性能平台", "evidence": "不存在的概括证据"},
						"technologies": [],
						"professional_skills": [],
						"ownership_level": "unknown",
						"ownership_evidence": "",
						"confidence": 0.8,
					}],
				}, ensure_ascii=False)
			return json.dumps({
				"stories": [{
					"title": {"text": "项目 Alpha", "evidence": "项目 Alpha"},
					"action": {
						"text": "使用 Python 和 SQLite 开发本地工具，2024 年服务 20 名用户。",
						"evidence": "使用 Python 和 SQLite 开发本地工具，2024 年服务 20 名用户。",
					},
					"technologies": [],
					"professional_skills": [],
					"ownership_level": "unknown",
					"ownership_evidence": "",
					"confidence": 0.9,
				}],
			}, ensure_ascii=False)

		facts = extract_source_facts(
			self.connection,
			source["id"],
			{},
			source_kind="technical_document",
			call_text=caller,
		)
		self.assertEqual(len(facts), 1)
		self.assertEqual(len(calls), 2)
		self.assertTrue(all("严格重试规则" in prompt for prompt in calls))

	def test_resume_extraction_keeps_only_verbatim_atomic_fields(self):
		source = self._source(
			"# 张三\n邮箱 zhangsan@example.com\n## 工作经历\n星云科技 | Python 工程师 | 2022-2024"
		)

		def caller(prompt, config, max_tokens, **kwargs):
			self.assertEqual(kwargs["purpose"], "resume_source_resume")
			return json.dumps({
				"facts": [
					{
						"entity_type": "contact",
						"field_name": "email",
						"group_id": "identity",
						"value": "zhangsan@example.com",
						"evidence": "邮箱 zhangsan@example.com",
						"confidence": 1,
					},
					{
						"entity_type": "experience",
						"field_name": "company",
						"group_id": "work-1",
						"value": "星云科技",
						"evidence": "星云科技 | Python 工程师 | 2022-2024",
						"confidence": 0.98,
					},
					{
						"entity_type": "experience",
						"field_name": "achievement",
						"group_id": "work-1",
						"value": "性能提升 80%",
						"evidence": "星云科技 | Python 工程师 | 2022-2024",
						"confidence": 0.99,
					},
				]
			}, ensure_ascii=False)

		facts = extract_source_facts(
			self.connection,
			source["id"],
			{},
			source_kind="resume",
			call_text=caller,
		)

		self.assertEqual(len(facts), 2)
		self.assertEqual({fact["field_name"] for fact in facts}, {"email", "company"})
		self.assertTrue(all(fact["fact_type"] == "resume_field" for fact in facts))
		self.assertNotIn("80%", "\n".join(fact["content"] for fact in facts))

	def test_same_resume_value_in_different_groups_is_not_collapsed(self):
		source = self._source("# 经历一\n星云科技\n\n# 经历二\n星云科技")

		def caller(prompt, config, max_tokens, **kwargs):
			return json.dumps({
				"facts": [
					{
						"entity_type": "experience",
						"field_name": "company",
						"group_id": "work-1",
						"value": "星云科技",
						"evidence": "星云科技",
						"confidence": 0.99,
					},
					{
						"entity_type": "experience",
						"field_name": "company",
						"group_id": "work-2",
						"value": "星云科技",
						"evidence": "星云科技",
						"confidence": 0.99,
					},
				]
			}, ensure_ascii=False)

		facts = extract_source_facts(
			self.connection,
			source["id"],
			{},
			source_kind="resume",
			call_text=caller,
		)
		self.assertEqual(len(facts), 2)
		self.assertNotEqual(facts[0]["group_id"], facts[1]["group_id"])

		update_fact(self.connection, facts[0]["id"], status="accepted")
		extract_source_facts(
			self.connection,
			source["id"],
			{},
			source_kind="resume",
			call_text=caller,
		)
		stored = list_facts(self.connection, source_id=source["id"])
		self.assertEqual(len(stored), 2)
		self.assertEqual(sum(fact["status"] == "accepted" for fact in stored), 1)

	def test_auto_classification_routes_to_resume_extraction(self):
		source = self._source("# 李雷\n## 教育经历\n清华大学 计算机科学 2020")
		purposes = []

		def caller(prompt, config, max_tokens, **kwargs):
			purposes.append(kwargs["purpose"])
			if kwargs["purpose"] == "resume_source_classify":
				return json.dumps({
					"document_kind": "resume",
					"confidence": 0.95,
					"evidence": "教育经历",
				}, ensure_ascii=False)
			return json.dumps({
				"facts": [{
					"entity_type": "education",
					"field_name": "school",
					"group_id": "education-1",
					"value": "清华大学",
					"evidence": "清华大学 计算机科学 2020",
					"confidence": 0.99,
				}]
			}, ensure_ascii=False)

		facts = extract_source_facts(self.connection, source["id"], {}, call_text=caller)
		stored_source = list_sources(self.connection)[0]

		self.assertEqual(purposes[0], "resume_source_classify")
		self.assertTrue(all(purpose == "resume_source_resume" for purpose in purposes[1:]))
		self.assertEqual(facts[0]["structured_data"]["value"], "清华大学")
		self.assertEqual(stored_source["detected_kind"], "resume")
		self.assertEqual(stored_source["detected_kind_confidence"], 0.95)

	def test_low_confidence_auto_classification_requires_user_choice(self):
		source = self._source("一些无法明确判断类型的个人材料内容。")

		def caller(prompt, config, max_tokens, **kwargs):
			return json.dumps({
				"document_kind": "unknown",
				"confidence": 0.3,
				"evidence": "个人材料内容",
			}, ensure_ascii=False)

		with self.assertRaisesRegex(ResumeBuilderError, "请选择"):
			extract_source_facts(self.connection, source["id"], {}, call_text=caller)
		self.assertEqual(list_sources(self.connection)[0]["status"], "failed")

	def test_portfolio_extracts_multiple_star_stories_with_independent_completeness(self):
		source = self._source(
			"# 作品 A\n线上排障耗时较长。\n我使用 Python 构建日志分析工具。\n定位时间从 2 小时降至 20 分钟。"
			"\n\n# 作品 B\n我使用 SQLite 保存本地任务状态。"
		)

		def caller(prompt, config, max_tokens, **kwargs):
			if "# 作品 A" in prompt and "# 作品 B" in prompt:
				first = json.loads(caller("# 作品 A", config, max_tokens, **kwargs))
				second = json.loads(caller("# 作品 B", config, max_tokens, **kwargs))
				return json.dumps(
					{"stories": first["stories"] + second["stories"]},
					ensure_ascii=False,
				)
			if "# 作品 A" in prompt and "# 作品 B" not in prompt:
				return json.dumps({
					"stories": [{
						"title": {"text": "作品 A", "evidence": "作品 A"},
						"situation": {"text": "线上排障耗时较长。", "evidence": "线上排障耗时较长。"},
						"task": None,
						"action": {
							"text": "我使用 Python 构建日志分析工具。",
							"evidence": "我使用 Python 构建日志分析工具。",
						},
						"result": {
							"text": "定位时间从 2 小时降至 20 分钟。",
							"evidence": "定位时间从 2 小时降至 20 分钟。",
						},
						"technologies": [{
							"name": "Python",
							"evidence": "我使用 Python 构建日志分析工具。",
						}],
						"professional_skills": [{
							"name": "日志分析",
							"evidence": "我使用 Python 构建日志分析工具。",
							"derived": True,
						}],
						"ownership_level": "responsible",
						"ownership_evidence": "我使用 Python 构建日志分析工具。",
						"confidence": 0.95,
					}]
				}, ensure_ascii=False)
			return json.dumps({
				"stories": [{
					"title": {"text": "作品 B", "evidence": "作品 B"},
					"situation": None,
					"task": None,
					"action": {
						"text": "我使用 SQLite 保存本地任务状态。",
						"evidence": "我使用 SQLite 保存本地任务状态。",
					},
					"result": None,
					"technologies": [{
						"name": "SQLite",
						"evidence": "我使用 SQLite 保存本地任务状态。",
					}],
					"professional_skills": [],
					"ownership_level": "responsible",
					"ownership_evidence": "我使用 SQLite 保存本地任务状态。",
					"confidence": 0.9,
				}]
			}, ensure_ascii=False)

		facts = extract_source_facts(
			self.connection,
			source["id"],
			{},
			source_kind="portfolio",
			call_text=caller,
		)

		self.assertEqual(len(facts), 2)
		by_title = {fact["structured_data"]["title"]: fact for fact in facts}
		self.assertEqual(by_title["作品 A"]["completeness"], 0.75)
		self.assertEqual(by_title["作品 A"]["structured_data"]["technologies"][0]["name"], "Python")
		self.assertIn("task", by_title["作品 A"]["structured_data"]["missing_fields"])
		self.assertEqual(by_title["作品 B"]["completeness"], 0.25)
		self.assertTrue(by_title["作品 B"]["needs_clarification"])
		self.assertEqual(list_sources(self.connection)[0]["selected_kind"], "portfolio")

	def test_reextract_preserves_accepted_fact_without_duplication(self):
		source = self._source()
		facts = self._extract(source["id"])
		update_fact(self.connection, facts[0]["id"], status="accepted", edited_content="Python 本地工具项目")

		self._extract(source["id"])
		stored = list_facts(self.connection, source_id=source["id"])

		self.assertEqual(len(stored), 1)
		self.assertEqual(stored[0]["status"], "accepted")
		self.assertEqual(stored[0]["effective_content"], "Python 本地工具项目")

	def test_accepting_blank_edit_falls_back_to_extracted_fact(self):
		source = self._source()
		fact = self._extract(source["id"])[0]

		updated = update_fact(self.connection, fact["id"], status="accepted", edited_content="   ")

		self.assertEqual(updated["effective_content"], fact["content"])

	def test_compose_uses_only_accepted_fact_ids_and_writes_version(self):
		source = self._source()
		fact = self._extract(source["id"])[0]
		update_fact(self.connection, fact["id"], status="accepted")

		def caller(prompt, config, max_tokens, **kwargs):
			self.assertIn(fact["id"], prompt)
			return json.dumps({
				"sections": [{
					"title": "项目经历",
					"items": [{
						"text": "使用 Python 和 SQLite 开发本地工具，2024 年服务 20 名用户。",
						"fact_ids": [fact["id"]],
					}],
				}]
			}, ensure_ascii=False)

		version = compose_resume_version(
			self.connection,
			{},
			target_role="Python 后端工程师",
			output_dir=self.output_dir,
			call_text=caller,
		)

		self.assertEqual(version["status"], "draft")
		self.assertIn("## 项目经历", version["markdown"])
		self.assertTrue(Path(version["file_path"]).exists())
		self.assertEqual(len(list_versions(self.connection)), 1)

	def test_compose_rejects_structured_fact_not_present_in_referenced_evidence(self):
		source = self._source()
		fact = self._extract(source["id"])[0]
		update_fact(self.connection, fact["id"], status="accepted")

		def caller(prompt, config, max_tokens, **kwargs):
			return json.dumps({
				"sections": [{
					"title": "项目经历",
					"items": [{"text": "项目转化率提升 88%", "fact_ids": [fact["id"]]}],
				}]
			}, ensure_ascii=False)

		with self.assertRaisesRegex(ResumeBuilderError, "无来源事实"):
			compose_resume_version(
				self.connection,
				{},
				target_role="",
				output_dir=self.output_dir,
				call_text=caller,
			)

	def test_profile_clarifications_preserve_answer_and_profile_is_traceable(self):
		source = self._source()
		fact = self._extract(source["id"])[0]
		update_fact(self.connection, fact["id"], status="accepted")

		items = refresh_profile_clarifications(self.connection)
		self.assertGreaterEqual(len(items), 4)
		self.assertLessEqual(sum(item["status"] == "open" for item in items), 5)
		ownership = next(item for item in items if item["kind"] == "ownership")
		update_clarification(
			self.connection,
			ownership["id"],
			status="answered",
			answer="我参与实现 Python 数据处理模块，未负责整体方案。",
		)
		refreshed = refresh_profile_clarifications(self.connection)
		preserved = next(item for item in refreshed if item["id"] == ownership["id"])
		self.assertEqual(preserved["status"], "answered")
		self.assertIn("未负责整体方案", preserved["answer"])

		def caller(prompt, config, max_tokens, **kwargs):
			self.assertEqual(kwargs["purpose"], "resume_profile_compose")
			self.assertIn(fact["id"], prompt)
			return json.dumps({
				"sections": [],
				"projects": [{
					"title": "项目 Alpha",
					"meta": "",
					"fact_ids": [fact["id"]],
					"clarification_ids": [],
					"stars": [{
						"heading": "本地工具",
						"situation": "",
						"task": "开发本地工具",
						"action": fact["content"],
						"result": "2024 年服务 20 名用户",
						"bullet": fact["content"],
						"technologies": ["Python", "SQLite"],
						"fact_ids": [fact["id"]],
						"clarification_ids": [],
					}],
				}],
				"known_gaps": [{
					"text": "项目结果仍需补充",
					"fact_ids": [fact["id"]],
					"clarification_ids": [],
				}],
				"approved_framings": [],
			}, ensure_ascii=False)

		profile = compose_career_profile(
			self.connection,
			{},
			output_dir=self.base_dir / "data" / "career_profiles",
			call_text=caller,
		)
		self.assertEqual(profile["fact_count"], 1)
		self.assertEqual(profile["quality_report"]["evidence_coverage"], 1)
		self.assertTrue(Path(profile["json_path"]).exists())
		self.assertTrue(Path(profile["markdown_path"]).exists())
		active = activate_career_profile(self.connection, profile["id"])
		self.assertEqual(active["status"], "active")
		self.assertEqual(len(list_profile_versions(self.connection)), 1)
		self.assertTrue(any(item["status"] == "answered" for item in list_clarifications(self.connection)))

	def test_profile_rejects_new_metric(self):
		source = self._source()
		fact = self._extract(source["id"])[0]
		update_fact(self.connection, fact["id"], status="accepted")

		def caller(prompt, config, max_tokens, **kwargs):
			return json.dumps({
				"sections": [],
				"projects": [{
					"title": "项目 Alpha", "meta": "", "fact_ids": [fact["id"]],
					"clarification_ids": [], "stars": [{
						"action": "项目转化率提升 88%", "bullet": "项目转化率提升 88%",
						"fact_ids": [fact["id"]], "clarification_ids": [],
					}],
				}],
			}, ensure_ascii=False)

		with self.assertRaisesRegex(ResumeBuilderError, "无来源事实"):
			compose_career_profile(
				self.connection,
				{},
				output_dir=self.base_dir / "data" / "career_profiles",
				call_text=caller,
			)

	def test_profile_groups_multiple_stars_under_one_project_heading(self):
		source = self._source()
		fact = self._extract(source["id"])[0]
		update_fact(self.connection, fact["id"], status="accepted")

		def caller(prompt, config, max_tokens, **kwargs):
			self.assertIn("一个 project 下可以且应当包含多个 stars", prompt)
			return json.dumps({
				"sections": [],
				"projects": [{
					"title": "项目 Alpha", "meta": "", "fact_ids": [fact["id"]],
					"clarification_ids": [], "stars": [
						{
							"heading": "Python", "action": fact["content"],
							"bullet": fact["content"], "technologies": ["Python"],
							"fact_ids": [fact["id"]], "clarification_ids": [],
						},
						{
							"heading": "SQLite", "action": fact["content"],
							"bullet": fact["content"], "technologies": ["SQLite"],
							"fact_ids": [fact["id"]], "clarification_ids": [],
						},
					],
				}],
			}, ensure_ascii=False)

		profile = compose_career_profile(
			self.connection, {}, output_dir=self.base_dir / "data" / "career_profiles",
			call_text=caller,
		)
		self.assertEqual(profile["markdown"].count("### 项目 Alpha"), 1)
		self.assertEqual(profile["markdown"].count("\n- **"), 2)
		self.assertIn("**Python**", profile["markdown"])
		self.assertIn("**SQLite**", profile["markdown"])

	def test_profile_rejects_new_technology_name(self):
		source = self._source()
		fact = self._extract(source["id"])[0]
		update_fact(self.connection, fact["id"], status="accepted")

		def caller(prompt, config, max_tokens, **kwargs):
			return json.dumps({
				"sections": [],
				"projects": [{
					"title": "项目 Alpha", "meta": "", "fact_ids": [fact["id"]],
					"clarification_ids": [], "stars": [{
						"action": "使用 Rust 开发本地工具", "bullet": "使用 Rust 开发本地工具",
						"fact_ids": [fact["id"]], "clarification_ids": [],
					}],
				}],
			}, ensure_ascii=False)

		with self.assertRaisesRegex(ResumeBuilderError, "无来源事实"):
			compose_career_profile(
				self.connection,
				{},
				output_dir=self.base_dir / "data" / "career_profiles",
				call_text=caller,
			)

	def test_profile_repairs_unsupported_token_without_relaxing_validation(self):
		source = self._source()
		fact = self._extract(source["id"])[0]
		update_fact(self.connection, fact["id"], status="accepted")
		calls = []

		def caller(prompt, config, max_tokens, **kwargs):
			calls.append(prompt)
			text = "使用 Rust 开发本地工具" if len(calls) == 1 else fact["content"]
			return json.dumps({
				"sections": [],
				"projects": [{
					"title": "项目 Alpha", "meta": "", "fact_ids": [fact["id"]],
					"clarification_ids": [], "stars": [{
						"action": text, "bullet": text,
						"fact_ids": [fact["id"]], "clarification_ids": [],
					}],
				}],
			}, ensure_ascii=False)

		profile = compose_career_profile(
			self.connection,
			{},
			output_dir=self.base_dir / "data" / "career_profiles",
			call_text=caller,
		)
		self.assertEqual(profile["fact_count"], 1)
		self.assertEqual(len(calls), 2)
		self.assertIn("上一份草稿未通过确定性校验", calls[1])

	def test_delete_requires_confirmation_and_blocks_version_references(self):
		source = self._source()
		with self.assertRaisesRegex(ResumeBuilderError, "明确确认"):
			delete_resume_source(
				self.connection,
				source["id"],
				storage_dir=self.source_dir,
				confirmed=False,
			)

		fact = self._extract(source["id"])[0]
		update_fact(self.connection, fact["id"], status="accepted")

		def caller(prompt, config, max_tokens, **kwargs):
			return json.dumps({
				"sections": [{
					"title": "项目经历",
					"items": [{"text": fact["content"], "fact_ids": [fact["id"]]}],
				}]
			}, ensure_ascii=False)

		compose_resume_version(
			self.connection,
			{},
			target_role="",
			output_dir=self.output_dir,
			call_text=caller,
		)
		with self.assertRaisesRegex(ResumeBuilderError, "版本引用"):
			delete_resume_source(
				self.connection,
				source["id"],
				storage_dir=self.source_dir,
				confirmed=True,
			)


if __name__ == "__main__":
	unittest.main()
