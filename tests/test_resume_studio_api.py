import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from bosshunter.resume_builder import service
from bosshunter.web import server


class ResumeStudioApiTests(unittest.TestCase):
	def setUp(self):
		self.original_base_dir = server.BASE_DIR
		self.temporary = tempfile.TemporaryDirectory()
		self.base_dir = Path(self.temporary.name)
		(self.base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
		server.set_base_dir(self.base_dir)

	def tearDown(self):
		server.set_base_dir(self.original_base_dir)
		self.temporary.cleanup()

	def _request(
		self,
		path: str,
		*,
		method: str = "GET",
		json_body: dict | None = None,
		multipart: tuple[str, bytes, str] | None = None,
	):
		status_headers = {}

		def start_response(status, headers, exc_info=None):
			status_headers["status"] = status
			status_headers["headers"] = dict(headers)

		body = b""
		content_type = None
		if json_body is not None:
			body = json.dumps(json_body).encode("utf-8")
			content_type = "application/json"
		elif multipart is not None:
			filename, content, mime = multipart
			boundary = "----BossHunterResumeStudio"
			body = (
				(
					f"--{boundary}\r\n"
					f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
					f"Content-Type: {mime}\r\n\r\n"
				).encode()
				+ content
				+ f"\r\n--{boundary}--\r\n".encode()
			)
			content_type = f"multipart/form-data; boundary={boundary}"

		environ = {
			"REQUEST_METHOD": method,
			"PATH_INFO": path,
			"QUERY_STRING": "",
			"CONTENT_LENGTH": str(len(body)),
			"SERVER_NAME": "127.0.0.1",
			"SERVER_PORT": "8686",
			"wsgi.version": (1, 0),
			"wsgi.url_scheme": "http",
			"wsgi.input": io.BytesIO(body),
			"wsgi.errors": io.StringIO(),
			"wsgi.multithread": False,
			"wsgi.multiprocess": False,
			"wsgi.run_once": False,
		}
		if content_type:
			environ["CONTENT_TYPE"] = content_type
		response_iter = server.app(environ, start_response)
		try:
			response_body = b"".join(
				chunk if isinstance(chunk, bytes) else chunk.encode("utf-8") for chunk in response_iter
			).decode("utf-8")
		finally:
			close = getattr(response_iter, "close", None)
			if close:
				close()
		return status_headers["status"], status_headers["headers"], response_body

	def _upload_source(self):
		status, _, body = self._request(
			"/api/resume-studio/sources",
			method="POST",
			multipart=(
				"项目说明.md",
				"项目 Alpha\n使用 Python 开发工具，2024 年服务 20 名用户。".encode(),
				"text/markdown",
			),
		)
		self.assertTrue(status.startswith("200"), body)
		return json.loads(body)["source"]

	def _star_extraction(self):
		return json.dumps({
			"stories": [{
				"title": {"text": "项目 Alpha", "evidence": "项目 Alpha"},
				"situation": None,
				"task": None,
				"action": {
					"text": "使用 Python 开发工具，2024 年服务 20 名用户。",
					"evidence": "使用 Python 开发工具，2024 年服务 20 名用户。",
				},
				"result": None,
				"technologies": [{
					"name": "Python",
					"evidence": "使用 Python 开发工具，2024 年服务 20 名用户。",
				}],
				"professional_skills": [],
				"ownership_level": "unknown",
				"ownership_evidence": "",
				"confidence": 0.95,
			}]
		}, ensure_ascii=False)

	def test_source_upload_is_local_and_duplicate_safe(self):
		first = self._upload_source()
		status, _, duplicate_body = self._request(
			"/api/resume-studio/sources",
			method="POST",
			multipart=(
				"项目副本.md",
				"项目 Alpha\n使用 Python 开发工具，2024 年服务 20 名用户。".encode(),
				"text/markdown",
			),
		)
		workspace_status, _, workspace_body = self._request("/api/resume-studio")
		workspace = json.loads(workspace_body)

		self.assertTrue(status.startswith("200"), duplicate_body)
		self.assertTrue(json.loads(duplicate_body)["duplicate"])
		self.assertTrue(workspace_status.startswith("200"), workspace_body)
		self.assertEqual(len(workspace["sources"]), 1)
		self.assertEqual(workspace["sources"][0]["id"], first["id"])
		stored_path = Path(first["stored_path"]).resolve()
		expected_root = (self.base_dir / "data" / "resume_sources").resolve()
		self.assertTrue(stored_path.is_relative_to(expected_root))

	def test_external_ai_calls_require_explicit_consent(self):
		source = self._upload_source()
		status, _, body = self._request(
			f"/api/resume-studio/sources/{source['id']}/extract",
			method="POST",
			json_body={"source_kind": "technical_document"},
		)
		self.assertTrue(status.startswith("428"), body)
		self.assertIn("外部 AI", json.loads(body)["error"])

		status, _, body = self._request(
			"/api/resume-studio/profile/compose", method="POST", json_body={}
		)
		self.assertTrue(status.startswith("428"), body)

	def test_full_review_compose_and_activate_flow(self):
		source = self._upload_source()
		extraction = self._star_extraction()
		with patch.object(service, "call_anthropic_text", return_value=extraction):
			status, _, body = self._request(
				f"/api/resume-studio/sources/{source['id']}/extract",
				method="POST",
				json_body={"source_kind": "technical_document", "external_ai_consent": True},
			)
		self.assertTrue(status.startswith("200"), body)
		fact = json.loads(body)["facts"][0]

		status, _, body = self._request(
			f"/api/resume-studio/facts/{fact['id']}",
			method="PATCH",
			json_body={"status": "accepted", "content": fact["content"]},
		)
		self.assertTrue(status.startswith("200"), body)

		composition = json.dumps({
			"sections": [{
				"title": "项目经历",
				"items": [{"text": fact["content"], "fact_ids": [fact["id"]]}],
			}]
		}, ensure_ascii=False)
		with patch.object(service, "call_anthropic_text", return_value=composition):
			status, _, body = self._request(
				"/api/resume-studio/compose",
				method="POST",
				json_body={"target_role": "Python 后端工程师", "external_ai_consent": True},
			)
		self.assertTrue(status.startswith("200"), body)
		version = json.loads(body)["version"]
		self.assertEqual(version["status"], "draft")

		status, _, body = self._request(
			f"/api/resume-studio/versions/{version['id']}/activate",
			method="POST",
		)
		config = yaml.safe_load((self.base_dir / "config.yaml").read_text(encoding="utf-8"))

		self.assertTrue(status.startswith("200"), body)
		self.assertEqual(json.loads(body)["version"]["status"], "active")
		self.assertEqual(config["profile"]["resume_path"], version["file_path"])
		self.assertTrue(Path(config["profile"]["resume_path"]).exists())

	def test_referenced_source_delete_returns_conflict(self):
		source = self._upload_source()
		extraction = self._star_extraction()
		with patch.object(service, "call_anthropic_text", return_value=extraction):
			_, _, body = self._request(
				f"/api/resume-studio/sources/{source['id']}/extract",
				method="POST",
				json_body={"source_kind": "technical_document", "external_ai_consent": True},
			)
		fact = json.loads(body)["facts"][0]
		self._request(
			f"/api/resume-studio/facts/{fact['id']}",
			method="PATCH",
			json_body={"status": "accepted"},
		)
		composition = json.dumps({
			"sections": [{
				"title": "项目经历",
				"items": [{"text": fact["content"], "fact_ids": [fact["id"]]}],
			}]
		}, ensure_ascii=False)
		with patch.object(service, "call_anthropic_text", return_value=composition):
			self._request(
				"/api/resume-studio/compose", method="POST",
				json_body={"external_ai_consent": True},
			)

		status, _, body = self._request(
			f"/api/resume-studio/sources/{source['id']}",
			method="DELETE",
			json_body={"confirmed": True},
		)
		self.assertTrue(status.startswith("409"), body)
		self.assertIn("版本引用", json.loads(body)["error"])

	def test_extract_api_rejects_invalid_source_kind(self):
		source = self._upload_source()
		status, _, body = self._request(
			f"/api/resume-studio/sources/{source['id']}/extract",
			method="POST",
			json_body={"source_kind": "spreadsheet", "external_ai_consent": True},
		)

		self.assertTrue(status.startswith("400"), body)
		self.assertIn("材料类型", json.loads(body)["error"])

	def test_profile_clarification_compose_activate_and_download_flow(self):
		source = self._upload_source()
		with patch.object(service, "call_anthropic_text", return_value=self._star_extraction()):
			_, _, body = self._request(
				f"/api/resume-studio/sources/{source['id']}/extract",
				method="POST",
				json_body={"source_kind": "technical_document", "external_ai_consent": True},
			)
		fact = json.loads(body)["facts"][0]
		self._request(
			f"/api/resume-studio/facts/{fact['id']}",
			method="PATCH",
			json_body={"status": "accepted"},
		)
		status, _, body = self._request(
			"/api/resume-studio/profile/clarifications/refresh",
			method="POST",
		)
		self.assertTrue(status.startswith("200"), body)
		ownership = next(
			item for item in json.loads(body)["clarifications"] if item["kind"] == "ownership"
		)
		status, _, body = self._request(
			f"/api/resume-studio/profile/clarifications/{ownership['id']}",
			method="PATCH",
			json_body={
				"status": "answered",
				"answer": "我参与 Python 模块实现，未负责整体技术方案。",
			},
		)
		self.assertTrue(status.startswith("200"), body)

		composition = json.dumps({
			"sections": [],
			"projects": [{
				"title": "项目 Alpha",
				"meta": "",
				"fact_ids": [fact["id"]],
				"clarification_ids": [],
				"stars": [{
					"heading": "Python 工具",
					"situation": "",
					"task": "开发工具",
					"action": fact["content"],
					"result": "2024 年服务 20 名用户",
					"bullet": fact["content"],
					"technologies": ["Python"],
					"fact_ids": [fact["id"]],
					"clarification_ids": [],
				}],
			}],
			"known_gaps": [],
			"approved_framings": [],
		}, ensure_ascii=False)
		with patch.object(service, "call_anthropic_text", return_value=composition):
			status, _, body = self._request(
				"/api/resume-studio/profile/compose",
				method="POST",
				json_body={"external_ai_consent": True},
			)
		self.assertTrue(status.startswith("200"), body)
		profile = json.loads(body)["profile"]
		self.assertEqual(profile["fact_count"], 1)
		self.assertEqual(profile["quality_report"]["evidence_coverage"], 1)

		status, _, body = self._request(
			f"/api/resume-studio/profile/versions/{profile['id']}/activate",
			method="POST",
		)
		self.assertTrue(status.startswith("200"), body)
		self.assertEqual(json.loads(body)["profile"]["status"], "active")
		config = yaml.safe_load((self.base_dir / "config.yaml").read_text(encoding="utf-8"))
		self.assertEqual(config["profile"]["resume_path"], profile["markdown_path"])

		status, headers, body = self._request(
			f"/api/resume-studio/profile/versions/{profile['id']}/download",
		)
		self.assertTrue(status.startswith("200"), body)
		self.assertIn("职业简历档案", body)
		self.assertIn("attachment", headers["Content-Disposition"])

	def test_download_and_activation_reject_paths_outside_managed_directories(self):
		outside = self.base_dir / "outside.md"
		outside.write_text("private", encoding="utf-8")
		version_id = "a" * 32
		profile_id = "b" * 32
		db = server._get_web_db()
		try:
			db.execute(
				"INSERT INTO resume_versions (id, name, markdown, file_path) VALUES (?, ?, ?, ?)",
				(version_id, "tampered", "# tampered", str(outside)),
			)
			db.execute(
				"""INSERT INTO resume_profile_versions
				(id, name, profile_json, markdown, quality_report, json_path, markdown_path)
				VALUES (?, ?, ?, ?, ?, ?, ?)""",
				(profile_id, "tampered", "{}", "# tampered", "{}", str(outside), str(outside)),
			)
			db.commit()
		finally:
			db.close()

		for path, method in (
			(f"/api/resume-studio/versions/{version_id}/download", "GET"),
			(f"/api/resume-studio/versions/{version_id}/activate", "POST"),
			(f"/api/resume-studio/profile/versions/{profile_id}/download", "GET"),
			(f"/api/resume-studio/profile/versions/{profile_id}/activate", "POST"),
		):
			status, _, body = self._request(path, method=method)
			self.assertTrue(status.startswith("409"), body)
			self.assertIn("受管目录", json.loads(body)["error"])

	def test_clear_resume_studio_requires_typed_confirmation_and_removes_only_workspace_data(self):
		source = self._upload_source()
		stored_path = Path(source["stored_path"])
		manual_resume = self.base_dir / "data" / "resumes" / "manual_resume.md"
		manual_resume.parent.mkdir(parents=True, exist_ok=True)
		manual_resume.write_text("# manual", encoding="utf-8")
		(self.base_dir / "config.yaml").write_text(
			yaml.safe_dump({"profile": {"resume_path": str(manual_resume)}}, allow_unicode=True),
			encoding="utf-8",
		)
		status, _, body = self._request(
			"/api/resume-studio", method="DELETE",
			json_body={"confirmed": True, "confirmation_text": "delete"},
		)
		self.assertTrue(status.startswith("409"), body)
		self.assertTrue(stored_path.exists())

		status, _, body = self._request(
			"/api/resume-studio", method="DELETE",
			json_body={"confirmed": True, "confirmation_text": "清空"},
		)
		self.assertTrue(status.startswith("200"), body)
		self.assertFalse(stored_path.exists())
		self.assertTrue(manual_resume.exists())
		config = yaml.safe_load((self.base_dir / "config.yaml").read_text(encoding="utf-8"))
		self.assertEqual(config["profile"]["resume_path"], str(manual_resume))
		_, _, workspace_body = self._request("/api/resume-studio")
		workspace = json.loads(workspace_body)
		self.assertEqual(workspace["sources"], [])
		self.assertEqual(workspace["facts"], [])


if __name__ == "__main__":
	unittest.main()
