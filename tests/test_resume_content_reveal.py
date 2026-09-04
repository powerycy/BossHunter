"""Tests for resume content reveal wiring and display helpers."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from bosshunter.web import server

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "src" / "bosshunter" / "web" / "frontend" / "src"


class ResumeDisplayHelperNodeTests(unittest.TestCase):
	def test_pure_typescript_helpers(self):
		script = ROOT / "tests" / "js" / "resume_display_helpers.test.ts"
		result = subprocess.run(
			["node", "--experimental-strip-types", str(script)],
			cwd=str(ROOT),
			capture_output=True,
			text=True,
			check=False,
		)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
		self.assertIn("resume_display_helpers: ok", result.stdout)


class ResumeContentRevealSourceTests(unittest.TestCase):
	def setUp(self):
		self.upload_section = (FRONTEND / "components" / "config" / "ResumeUploadSection.tsx").read_text(
			encoding="utf-8"
		)
		self.dual = (FRONTEND / "components" / "config" / "ResumeDualPreview.tsx").read_text(encoding="utf-8")
		self.pdf_reveal = (FRONTEND / "components" / "config" / "ResumePdfReveal.tsx").read_text(encoding="utf-8")
		self.md_reveal = (FRONTEND / "components" / "config" / "ResumeMarkdownReveal.tsx").read_text(
			encoding="utf-8"
		)
		self.display_lib = (FRONTEND / "lib" / "resumeDisplay.ts").read_text(encoding="utf-8")
		self.config_page = (FRONTEND / "pages" / "ConfigPage.tsx").read_text(encoding="utf-8")

	def test_config_page_mounts_upload_section_only(self):
		self.assertIn("ResumeUploadSection", self.config_page)
		self.assertNotIn("handleResumeUpload", self.config_page)
		self.assertNotIn("/api/resume/upload", self.config_page)

	def test_upload_section_intercepts_page_and_zone_drag(self):
		self.assertIn("window.addEventListener('dragover'", self.upload_section)
		self.assertIn("window.addEventListener('drop'", self.upload_section)
		self.assertIn("onDrop={handleResumeDrop}", self.upload_section)
		self.assertIn("/api/resume/upload", self.upload_section)
		self.assertIn("resumeDropOutsideTip", self.upload_section)
		self.assertIn("afterDropOutside", self.upload_section)
		self.assertIn("afterSuccessfulResumeLoad", self.upload_section)
		self.assertIn("请将文件拖放到简历上传区域", self.display_lib)

	def test_upload_section_keeps_drop_tip_across_successful_load(self):
		"""GET success must clear load errors only, not the outside-drop tip channel."""
		self.assertIn("afterSuccessfulResumeLoad", self.display_lib)
		self.assertIn("dropOutsideTip", self.display_lib)
		self.assertIn("resumePanelVisibleMessage", self.upload_section)
		# Prefer exact call shapes over bare identifier presence.
		self.assertIn("setPanelMessages((prev) => afterSuccessfulResumeLoad(prev))", self.upload_section)
		self.assertIn("setPanelMessages((prev) => afterDropOutside(prev, resumeDropOutsideTip()))", self.upload_section)
		null_branch = self.upload_section.split("data === null")[1].split("const message")[0]
		self.assertIn("afterSuccessfulResumeLoad(prev)", null_branch)
		success_branch = self.upload_section.split("data && data.filename")[1].split("shouldSyncResumePath")[0]
		self.assertIn("afterSuccessfulResumeLoad(prev)", success_branch)

	def test_upload_section_uses_dual_preview(self):
		self.assertIn("ResumeDualPreview", self.upload_section)
		self.assertIn(
			"ResumeDualPreview key={`${resumeInfo.path}:${resumeInfo.cache_buster || ''}`}",
			self.upload_section,
		)
		self.assertIn("resumeLoadErrorMessage", self.upload_section)
		self.assertIn("shouldSyncResumePath", self.upload_section)
		self.assertIn("删除简历失败", self.upload_section)
		self.assertIn("data === null", self.upload_section)

	def test_dual_preview_defaults_hidden_with_independent_toggles(self):
		self.assertIn("const [pdfVisible, setPdfVisible] = useState(false)", self.dual)
		self.assertIn("const [mdVisible, setMdVisible] = useState(false)", self.dual)
		self.assertIn("ResumePdfReveal", self.dual)
		self.assertIn("ResumeMarkdownReveal", self.dual)
		self.assertIn("nextResumeContentVisible", self.dual)

	def test_labels_are_independent_for_pdf_and_markdown(self):
		self.assertIn("查看原 PDF", self.display_lib)
		self.assertIn("隐藏原 PDF", self.display_lib)
		self.assertIn("查看转换后的 MD", self.display_lib)
		self.assertIn("隐藏转换后的 MD", self.display_lib)
		self.assertIn("resumePdfToggleLabel", self.pdf_reveal)
		self.assertIn("resumeMarkdownToggleLabel", self.md_reveal)
		self.assertIn("iframe", self.pdf_reveal)
		self.assertIn("cache_buster", self.display_lib)
		self.assertIn("resumeLoadErrorMessage", self.display_lib)

	def test_upload_section_wires_failure_message_channels(self):
		"""Abnormal UI paths must route through afterResumeLoadFailure / explicit copy."""
		self.assertIn("setPanelMessages((prev) => afterResumeLoadFailure(prev, message))", self.upload_section)
		self.assertIn(
			"setPanelMessages((prev) => afterResumeLoadFailure(prev, data.error || '简历上传失败'))",
			self.upload_section,
		)
		self.assertIn(
			"setPanelMessages((prev) => afterResumeLoadFailure(prev, '网络错误，简历上传失败'))",
			self.upload_section,
		)
		self.assertIn(
			"setPanelMessages((prev) => afterResumeLoadFailure(prev, '网络错误，无法读取简历'))",
			self.upload_section,
		)
		self.assertIn("删除简历失败", self.upload_section)
		self.assertIn("网络错误，删除简历失败", self.upload_section)
		self.assertIn("!res.ok || !data.success", self.upload_section)
		self.assertIn("!res.ok || !data?.success", self.upload_section)
		fail_branch = self.upload_section.split("const message = resumeLoadErrorMessage")[1].split(
			"if (data && data.filename)"
		)[0]
		self.assertIn("afterResumeLoadFailure(prev, message)", fail_branch)
		self.assertNotIn("afterSuccessfulResumeLoad", fail_branch)


class ResumeApiPreviewRouteTests(unittest.TestCase):
	def setUp(self):
		self.original_base_dir = server.BASE_DIR

	def tearDown(self):
		server.set_base_dir(self.original_base_dir)

	def _request(self, path: str, method: str = "GET"):
		if "?" in path:
			path_info, query_string = path.split("?", 1)
		else:
			path_info, query_string = path, ""
		status_headers = {}

		def start_response(status, headers, exc_info=None):
			status_headers["status"] = status
			status_headers["headers"] = dict(headers)

		environ = {
			"REQUEST_METHOD": method,
			"PATH_INFO": path_info,
			"QUERY_STRING": query_string,
			"SERVER_NAME": "127.0.0.1",
			"SERVER_PORT": "8686",
			"wsgi.version": (1, 0),
			"wsgi.url_scheme": "http",
			"wsgi.input": io.BytesIO(b""),
			"wsgi.errors": io.StringIO(),
			"wsgi.multithread": False,
			"wsgi.multiprocess": False,
			"wsgi.run_once": False,
		}
		response_iter = server.app(environ, start_response)
		try:
			body = b"".join(
				chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
				for chunk in response_iter
			)
		finally:
			close = getattr(response_iter, "close", None)
			if close:
				close()
		return status_headers["status"], status_headers["headers"], body

	def _upload_resume(self, filename: str, content: bytes, content_type: str):
		boundary = "----BossHunterResumeUpload"
		body = (
			(
				f"--{boundary}\r\n"
				f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
				f"Content-Type: {content_type}\r\n\r\n"
			).encode("utf-8")
			+ content
			+ f"\r\n--{boundary}--\r\n".encode("utf-8")
		)
		status_headers = {}

		def start_response(status, headers, exc_info=None):
			status_headers["status"] = status
			status_headers["headers"] = dict(headers)

		environ = {
			"REQUEST_METHOD": "POST",
			"PATH_INFO": "/api/resume/upload",
			"QUERY_STRING": "",
			"CONTENT_LENGTH": str(len(body)),
			"CONTENT_TYPE": f"multipart/form-data; boundary={boundary}",
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
		response_body = b"".join(
			chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
			for chunk in server.app(environ, start_response)
		).decode("utf-8")
		return status_headers["status"], status_headers["headers"], response_body

	def _assert_absolute_resume_path(self, raw_path: str, *, base_dir: Path | None = None) -> Path:
		path = Path(raw_path)
		self.assertTrue(path.is_absolute(), f"expected absolute resume path, got {raw_path!r}")
		if base_dir is not None:
			self.assertTrue(
				str(path.resolve()).startswith(str(base_dir.resolve())),
				f"resume path {path} is outside base_dir {base_dir}",
			)
		return path

	def _assert_http_not_found(self, status: str, headers: dict, body: bytes) -> None:
		"""Original PDF misses must be HTTP 404, not a swallowed 500 JSON envelope."""
		self.assertTrue(status.startswith("404"), body)
		self.assertFalse(status.startswith("500"), body)
		content_type = str(headers.get("Content-Type") or headers.get("content-type") or "")
		# Bottle maps abort(404) to a small JSON body in this app; never PDF.
		self.assertNotIn("application/pdf", content_type.lower())
		if "json" in content_type.lower() and body:
			payload = json.loads(body.decode("utf-8"))
			self.assertIn("error", payload)

	def _make_text_pdf(self, text: str) -> bytes:
		writer = PdfWriter()
		page = writer.add_blank_page(width=612, height=792)
		font = DictionaryObject({
			NameObject("/Type"): NameObject("/Font"),
			NameObject("/Subtype"): NameObject("/Type1"),
			NameObject("/BaseFont"): NameObject("/Helvetica"),
		})
		page[NameObject("/Resources")] = DictionaryObject({
			NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
		})
		contents = DecodedStreamObject()
		contents.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1"))
		page[NameObject("/Contents")] = contents
		buf = io.BytesIO()
		writer.write(buf)
		return buf.getvalue()

	def test_pdf_upload_keeps_original_and_returns_preview_fields(self):
		pdf_bytes = self._make_text_pdf("Preview Candidate")
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			(base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
			server.set_base_dir(base_dir)
			status, _, body = self._upload_resume("candidate.pdf", pdf_bytes, "application/pdf")
			payload = json.loads(body)
			md_path = self._assert_absolute_resume_path(payload["path"], base_dir=base_dir)
			config = yaml.safe_load((base_dir / "config.yaml").read_text(encoding="utf-8"))
			self.assertTrue(status.startswith("200"), body)
			self.assertTrue(payload["has_original_pdf"])
			self.assertEqual(payload["original_filename"], "candidate.pdf")
			self.assertEqual(payload["original_preview_url"], "/api/resume/original")
			self.assertIn("cache_buster", payload)
			self.assertIn("Preview Candidate", payload["content"])
			self.assertTrue(md_path.with_suffix(".pdf").is_file())
			self.assertEqual(md_path.suffix, ".md")
			self._assert_absolute_resume_path(config["profile"]["resume_path"], base_dir=base_dir)

	def test_pdf_upload_original_endpoint_serves_pdf_bytes(self):
		pdf_bytes = self._make_text_pdf("Serve Original")
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			(base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
			server.set_base_dir(base_dir)
			upload_status, _, upload_body = self._upload_resume(
				"serve.pdf",
				pdf_bytes,
				"application/pdf",
			)
			upload_payload = json.loads(upload_body)
			self.assertTrue(upload_status.startswith("200"), upload_body)
			md_path = self._assert_absolute_resume_path(upload_payload["path"], base_dir=base_dir)
			pdf_path = md_path.with_suffix(".pdf")
			self.assertTrue(pdf_path.is_file())

			status, headers, body = self._request("/api/resume/original")
			content_type = str(headers.get("Content-Type") or headers.get("content-type") or "")
			self.assertTrue(status.startswith("200"), body[:200])
			self.assertIn("application/pdf", content_type.lower())
			self.assertTrue(body.startswith(b"%PDF"), body[:32])
			self.assertEqual(body, pdf_path.read_bytes())

	def test_markdown_upload_has_no_original_pdf(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			(base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
			server.set_base_dir(base_dir)
			status, _, body = self._upload_resume(
				"only.md",
				"# Only Markdown\n".encode("utf-8"),
				"text/markdown",
			)
			payload = json.loads(body)
			self.assertTrue(status.startswith("200"), body)
			self._assert_absolute_resume_path(payload["path"], base_dir=base_dir)
			self.assertFalse(payload["has_original_pdf"])
			self.assertNotIn("original_filename", payload)
			self.assertIn("cache_buster", payload)

	def test_original_missing_returns_real_404(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			md_path = base_dir / "data" / "resumes" / "solo.md"
			md_path.parent.mkdir(parents=True)
			md_path.write_text("# solo\n", encoding="utf-8")
			(base_dir / "config.yaml").write_text(
				yaml.dump({"profile": {"resume_path": str(md_path)}}, allow_unicode=True),
				encoding="utf-8",
			)
			server.set_base_dir(base_dir)
			status, headers, body = self._request("/api/resume/original")
			self._assert_http_not_found(status, headers, body)

	def test_delete_removes_companion_pdf_but_keeps_markdown(self):
		pdf_bytes = self._make_text_pdf("Delete Companion")
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			(base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
			server.set_base_dir(base_dir)
			status, _, body = self._upload_resume("keep.pdf", pdf_bytes, "application/pdf")
			payload = json.loads(body)
			md_path = Path(payload["path"])
			pdf_path = md_path.with_suffix(".pdf")
			self.assertTrue(status.startswith("200"), body)
			self.assertTrue(pdf_path.exists())

			del_status, _, del_body = self._request("/api/resume", method="DELETE")
			config = yaml.safe_load((base_dir / "config.yaml").read_text(encoding="utf-8"))
			self.assertTrue(del_status.startswith("200"), del_body)
			self.assertEqual(config["profile"]["resume_path"], "")
			self.assertTrue(md_path.exists())
			self.assertFalse(pdf_path.exists())

	def test_delete_clears_config_when_pdf_only_cannot_convert(self):
		"""Encrypted PDF-only legacy path must still detach; do not block on conversion."""
		writer = PdfWriter()
		writer.add_blank_page(width=612, height=792)
		writer.encrypt("secret")
		pdf_buf = io.BytesIO()
		writer.write(pdf_buf)

		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			resume_dir = base_dir / "data" / "resumes"
			resume_dir.mkdir(parents=True)
			pdf_path = resume_dir / "locked.pdf"
			pdf_path.write_bytes(pdf_buf.getvalue())
			(base_dir / "config.yaml").write_text(
				yaml.dump({"profile": {"resume_path": str(pdf_path)}}, allow_unicode=True),
				encoding="utf-8",
			)
			server.set_base_dir(base_dir)

			del_status, _, del_body = self._request("/api/resume", method="DELETE")
			config = yaml.safe_load((base_dir / "config.yaml").read_text(encoding="utf-8"))

			self.assertTrue(del_status.startswith("200"), del_body)
			self.assertEqual(json.loads(del_body.decode("utf-8")), {"success": True})
			self.assertEqual(config["profile"]["resume_path"], "")
			# Sole PDF master is left on disk (never force-convert / wipe on DELETE).
			self.assertTrue(pdf_path.is_file())
			self.assertFalse(pdf_path.with_suffix(".md").exists())

	def test_delete_clears_config_when_scanned_pdf_only(self):
		writer = PdfWriter()
		writer.add_blank_page(width=612, height=792)
		pdf_buf = io.BytesIO()
		writer.write(pdf_buf)

		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			resume_dir = base_dir / "data" / "resumes"
			resume_dir.mkdir(parents=True)
			pdf_path = resume_dir / "scanned.pdf"
			pdf_path.write_bytes(pdf_buf.getvalue())
			(base_dir / "config.yaml").write_text(
				yaml.dump({"profile": {"resume_path": str(pdf_path)}}, allow_unicode=True),
				encoding="utf-8",
			)
			server.set_base_dir(base_dir)

			del_status, _, del_body = self._request("/api/resume", method="DELETE")
			config = yaml.safe_load((base_dir / "config.yaml").read_text(encoding="utf-8"))

			self.assertTrue(del_status.startswith("200"), del_body)
			self.assertEqual(config["profile"]["resume_path"], "")
			self.assertTrue(pdf_path.is_file())
			self.assertFalse(pdf_path.with_suffix(".md").exists())

	def test_get_missing_configured_file_returns_error_payload(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			missing = base_dir / "data" / "resumes" / "missing.md"
			(base_dir / "config.yaml").write_text(
				yaml.dump({"profile": {"resume_path": str(missing)}}, allow_unicode=True),
				encoding="utf-8",
			)
			server.set_base_dir(base_dir)
			status, _, body = self._request("/api/resume")
			payload = json.loads(body.decode("utf-8"))
			self.assertTrue(status.startswith("404"), body)
			self.assertIn("error", payload)

	def test_get_default_placeholder_missing_returns_null_not_error(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			(base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
			server.set_base_dir(base_dir)
			status, _, body = self._request("/api/resume")
			self.assertTrue(status.startswith("200"), body)
			self.assertEqual(json.loads(body.decode("utf-8")), None)

	def test_get_resolves_relative_resume_path_against_base_dir(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			resume = base_dir / "nested" / "cv.md"
			resume.parent.mkdir(parents=True)
			resume.write_text("# relative\n", encoding="utf-8")
			(base_dir / "config.yaml").write_text(
				yaml.dump({"profile": {"resume_path": "./nested/cv.md"}}, allow_unicode=True),
				encoding="utf-8",
			)
			server.set_base_dir(base_dir)
			status, _, body = self._request("/api/resume")
			payload = json.loads(body.decode("utf-8"))
			self.assertTrue(status.startswith("200"), body)
			self.assertEqual(payload["filename"], "cv.md")
			self.assertIn("# relative", payload["content"])

	def test_get_rewrites_config_from_pdf_path_to_markdown(self):
		pdf_bytes = self._make_text_pdf("Rewrite Config Path")
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			resume_dir = base_dir / "data" / "resumes"
			resume_dir.mkdir(parents=True)
			pdf_path = resume_dir / "legacy.pdf"
			pdf_path.write_bytes(pdf_bytes)
			(base_dir / "config.yaml").write_text(
				yaml.dump({"profile": {"resume_path": str(pdf_path)}}, allow_unicode=True),
				encoding="utf-8",
			)
			server.set_base_dir(base_dir)

			status, _, body = self._request("/api/resume")
			payload = json.loads(body.decode("utf-8"))
			config = yaml.safe_load((base_dir / "config.yaml").read_text(encoding="utf-8"))

			self.assertTrue(status.startswith("200"), body)
			self.assertTrue(str(payload["path"]).endswith("legacy.md"))
			self._assert_absolute_resume_path(payload["path"], base_dir=base_dir)
			self._assert_absolute_resume_path(config["profile"]["resume_path"], base_dir=base_dir)
			self.assertTrue(str(config["profile"]["resume_path"]).endswith("legacy.md"))
			self.assertTrue((resume_dir / "legacy.md").is_file())

	def test_upload_reclaims_preferred_name_after_delete_leaves_orphan(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			resume_dir = base_dir / "data" / "resumes"
			resume_dir.mkdir(parents=True)
			(resume_dir / "resume.md").write_text("# orphan leftover\n", encoding="utf-8")
			(base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
			server.set_base_dir(base_dir)

			status, _, body = self._upload_resume(
				"resume.md",
				"# brand new\n".encode("utf-8"),
				"text/markdown",
			)
			payload = json.loads(body)
			self.assertTrue(status.startswith("200"), body)
			self.assertEqual(payload["filename"], "resume.md")
			stored = self._assert_absolute_resume_path(payload["path"], base_dir=base_dir)
			self.assertEqual(stored.name, "resume.md")
			self.assertEqual((resume_dir / "resume.md").read_text(encoding="utf-8"), "# brand new\n")

	def test_upload_avoids_orphan_stem_when_another_resume_is_active(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			resume_dir = base_dir / "data" / "resumes"
			resume_dir.mkdir(parents=True)
			(resume_dir / "resume.md").write_text("# orphan leftover\n", encoding="utf-8")
			active = resume_dir / "active.md"
			active.write_text("# active\n", encoding="utf-8")
			(base_dir / "config.yaml").write_text(
				yaml.dump({"profile": {"resume_path": str(active)}}, allow_unicode=True),
				encoding="utf-8",
			)
			server.set_base_dir(base_dir)

			status, _, body = self._upload_resume(
				"resume.md",
				"# brand new\n".encode("utf-8"),
				"text/markdown",
			)
			payload = json.loads(body)
			self.assertTrue(status.startswith("200"), body)
			self.assertEqual(payload["filename"], "resume-2.md")
			stored = self._assert_absolute_resume_path(payload["path"], base_dir=base_dir)
			self.assertEqual(stored.name, "resume-2.md")

	# ── Abnormal / error flows ──────────────────────────────

	def test_upload_rejects_missing_file_field(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			(base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
			server.set_base_dir(base_dir)

			boundary = "----BossHunterNoFile"
			# Valid multipart body, but no `file` part.
			body = (
				f"--{boundary}\r\n"
				f'Content-Disposition: form-data; name="note"\r\n\r\n'
				f"ignored\r\n"
				f"--{boundary}--\r\n"
			).encode("utf-8")
			status_headers = {}

			def start_response(status, headers, exc_info=None):
				status_headers["status"] = status
				status_headers["headers"] = dict(headers)

			environ = {
				"REQUEST_METHOD": "POST",
				"PATH_INFO": "/api/resume/upload",
				"QUERY_STRING": "",
				"CONTENT_LENGTH": str(len(body)),
				"CONTENT_TYPE": f"multipart/form-data; boundary={boundary}",
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
			response = b"".join(
				chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
				for chunk in server.app(environ, start_response)
			)
			payload = json.loads(response.decode("utf-8"))
			self.assertTrue(status_headers["status"].startswith("400"), response)
			self.assertIn("error", payload)

	def test_upload_rejects_file_over_10mb(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			(base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
			server.set_base_dir(base_dir)
			huge = b"#" + (b"x" * (10 * 1024 * 1024))
			status, _, body = self._upload_resume("too-big.md", huge, "text/markdown")
			payload = json.loads(body)
			self.assertTrue(status.startswith("400"), body)
			self.assertIn("10MB", payload["error"])

	def test_upload_rejects_non_utf8_markdown(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			(base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
			server.set_base_dir(base_dir)
			status, _, body = self._upload_resume(
				"bad.md",
				"简历".encode("gbk"),
				"text/markdown",
			)
			payload = json.loads(body)
			self.assertTrue(status.startswith("400"), body)
			self.assertIn("UTF-8", payload["error"])

	def test_upload_markdown_strips_embedded_nul_bytes(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			(base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
			server.set_base_dir(base_dir)
			status, _, body = self._upload_resume(
				"nul.md",
				b"# A\x00B\n",
				"text/markdown",
			)
			payload = json.loads(body)
			self.assertTrue(status.startswith("200"), body)
			self.assertEqual(payload["content"], "# AB\n")
			stored = self._assert_absolute_resume_path(payload["path"], base_dir=base_dir)
			self.assertEqual(stored.read_text(encoding="utf-8"), "# AB\n")

	def test_markdown_replace_removes_stale_companion_pdf(self):
		pdf_bytes = self._make_text_pdf("Stale PDF")
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			(base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
			server.set_base_dir(base_dir)
			status, _, body = self._upload_resume("swap.pdf", pdf_bytes, "application/pdf")
			payload = json.loads(body)
			md_path = self._assert_absolute_resume_path(payload["path"], base_dir=base_dir)
			pdf_path = md_path.with_suffix(".pdf")
			self.assertTrue(status.startswith("200"), body)
			self.assertTrue(pdf_path.is_file())

			status2, _, body2 = self._upload_resume(
				"swap.md",
				"# markdown replacement\n".encode("utf-8"),
				"text/markdown",
			)
			payload2 = json.loads(body2)
			self.assertTrue(status2.startswith("200"), body2)
			self._assert_absolute_resume_path(payload2["path"], base_dir=base_dir)
			self.assertFalse(payload2["has_original_pdf"])
			self.assertFalse(pdf_path.exists())
			self.assertEqual(md_path.read_text(encoding="utf-8"), "# markdown replacement\n")

	def test_get_encrypted_pdf_only_returns_400(self):
		writer = PdfWriter()
		writer.add_blank_page(width=612, height=792)
		writer.encrypt("secret")
		pdf_buf = io.BytesIO()
		writer.write(pdf_buf)

		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			resume_dir = base_dir / "data" / "resumes"
			resume_dir.mkdir(parents=True)
			pdf_path = resume_dir / "locked.pdf"
			pdf_path.write_bytes(pdf_buf.getvalue())
			(base_dir / "config.yaml").write_text(
				yaml.dump({"profile": {"resume_path": str(pdf_path)}}, allow_unicode=True),
				encoding="utf-8",
			)
			server.set_base_dir(base_dir)

			status, _, body = self._request("/api/resume")
			payload = json.loads(body.decode("utf-8"))
			self.assertTrue(status.startswith("400"), body)
			self.assertIn("error", payload)
			self.assertIn("加密", payload["error"])

	def test_get_scanned_pdf_only_returns_400(self):
		writer = PdfWriter()
		writer.add_blank_page(width=612, height=792)
		pdf_buf = io.BytesIO()
		writer.write(pdf_buf)

		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			resume_dir = base_dir / "data" / "resumes"
			resume_dir.mkdir(parents=True)
			pdf_path = resume_dir / "scan.pdf"
			pdf_path.write_bytes(pdf_buf.getvalue())
			(base_dir / "config.yaml").write_text(
				yaml.dump({"profile": {"resume_path": str(pdf_path)}}, allow_unicode=True),
				encoding="utf-8",
			)
			server.set_base_dir(base_dir)

			status, _, body = self._request("/api/resume")
			payload = json.loads(body.decode("utf-8"))
			self.assertTrue(status.startswith("400"), body)
			self.assertIn("error", payload)

	def test_get_whitespace_only_resume_path_returns_null(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			(base_dir / "config.yaml").write_text(
				yaml.dump({"profile": {"resume_path": "   "}}, allow_unicode=True),
				encoding="utf-8",
			)
			server.set_base_dir(base_dir)
			status, _, body = self._request("/api/resume")
			self.assertTrue(status.startswith("200"), body)
			self.assertEqual(json.loads(body.decode("utf-8")), None)

	def test_original_with_no_resume_configured_returns_404(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			(base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
			server.set_base_dir(base_dir)
			status, headers, body = self._request("/api/resume/original")
			self._assert_http_not_found(status, headers, body)

	def test_original_whitespace_only_resume_path_returns_404(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			(base_dir / "config.yaml").write_text(
				yaml.dump({"profile": {"resume_path": "   "}}, allow_unicode=True),
				encoding="utf-8",
			)
			server.set_base_dir(base_dir)
			status, headers, body = self._request("/api/resume/original")
			self._assert_http_not_found(status, headers, body)

	def test_delete_with_empty_config_still_succeeds(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			(base_dir / "config.yaml").write_text("{}\n", encoding="utf-8")
			server.set_base_dir(base_dir)
			status, _, body = self._request("/api/resume", method="DELETE")
			config = yaml.safe_load((base_dir / "config.yaml").read_text(encoding="utf-8"))
			self.assertTrue(status.startswith("200"), body)
			self.assertEqual(json.loads(body.decode("utf-8")), {"success": True})
			self.assertEqual(config["profile"]["resume_path"], "")

	def test_delete_markdown_only_clears_config_and_keeps_file(self):
		with tempfile.TemporaryDirectory() as tmp:
			base_dir = Path(tmp)
			resume_dir = base_dir / "data" / "resumes"
			resume_dir.mkdir(parents=True)
			md_path = resume_dir / "solo.md"
			md_path.write_text("# keep me\n", encoding="utf-8")
			(base_dir / "config.yaml").write_text(
				yaml.dump({"profile": {"resume_path": str(md_path)}}, allow_unicode=True),
				encoding="utf-8",
			)
			server.set_base_dir(base_dir)

			status, _, body = self._request("/api/resume", method="DELETE")
			config = yaml.safe_load((base_dir / "config.yaml").read_text(encoding="utf-8"))
			self.assertTrue(status.startswith("200"), body)
			self.assertEqual(config["profile"]["resume_path"], "")
			self.assertTrue(md_path.is_file())
			self.assertEqual(md_path.read_text(encoding="utf-8"), "# keep me\n")


if __name__ == "__main__":
	unittest.main()
