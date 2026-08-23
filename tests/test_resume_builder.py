import json
import tempfile
import unittest
from pathlib import Path

from bosshunter.db import get_db
from bosshunter.resume_builder.service import (
	ResumeBuilderError,
	compose_resume_version,
	delete_resume_source,
	extract_source_facts,
	ingest_resume_source,
)
from bosshunter.resume_builder.store import (
	list_facts,
	list_sources,
	list_versions,
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
				"facts": [
					{
						"category": "项目经历",
						"content": "使用 Python 和 SQLite 开发本地工具，2024 年服务 20 名用户。",
						"evidence": "使用 Python 和 SQLite 开发本地工具，2024 年服务 20 名用户。",
						"confidence": 0.96,
					},
					{
						"category": "量化成果",
						"content": "用户满意度达到 99%",
						"evidence": "使用 Python 和 SQLite 开发本地工具，2024 年服务 20 名用户。",
						"confidence": 0.99,
					},
				]
			}, ensure_ascii=False)

		return extract_source_facts(self.connection, source_id, {}, call_text=caller)

	def test_database_initializes_resume_studio_tables(self):
		tables = {
			row["name"]
			for row in self.connection.execute(
				"SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'resume_%'"
			).fetchall()
		}
		self.assertEqual(
			tables,
			{"resume_sources", "resume_facts", "resume_versions", "resume_version_facts"},
		)

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
		self.assertIn("20 名用户", facts[0]["content"])
		self.assertNotIn("99%", facts[0]["content"])
		self.assertEqual(list_sources(self.connection)[0]["status"], "review")

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
		with self.assertRaisesRegex(ResumeBuilderError, "主简历版本引用"):
			delete_resume_source(
				self.connection,
				source["id"],
				storage_dir=self.source_dir,
				confirmed=True,
			)


if __name__ == "__main__":
	unittest.main()
