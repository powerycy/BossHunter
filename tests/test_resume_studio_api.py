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
		self.assertTrue(Path(first["stored_path"]).is_relative_to(self.base_dir / "data" / "resume_sources"))

	def test_full_review_compose_and_activate_flow(self):
		source = self._upload_source()
		extraction = json.dumps({
			"facts": [{
				"category": "项目经历",
				"content": "使用 Python 开发工具，2024 年服务 20 名用户。",
				"evidence": "使用 Python 开发工具，2024 年服务 20 名用户。",
				"confidence": 0.95,
			}]
		}, ensure_ascii=False)
		with patch.object(service, "call_anthropic_text", return_value=extraction):
			status, _, body = self._request(
				f"/api/resume-studio/sources/{source['id']}/extract",
				method="POST",
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
				json_body={"target_role": "Python 后端工程师"},
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
		extraction = json.dumps({
			"facts": [{
				"category": "项目经历",
				"content": "使用 Python 开发工具，2024 年服务 20 名用户。",
				"evidence": "使用 Python 开发工具，2024 年服务 20 名用户。",
				"confidence": 1,
			}]
		}, ensure_ascii=False)
		with patch.object(service, "call_anthropic_text", return_value=extraction):
			_, _, body = self._request(f"/api/resume-studio/sources/{source['id']}/extract", method="POST")
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
			self._request("/api/resume-studio/compose", method="POST", json_body={})

		status, _, body = self._request(
			f"/api/resume-studio/sources/{source['id']}",
			method="DELETE",
			json_body={"confirmed": True},
		)
		self.assertTrue(status.startswith("409"), body)
		self.assertIn("主简历版本引用", json.loads(body)["error"])


if __name__ == "__main__":
	unittest.main()
