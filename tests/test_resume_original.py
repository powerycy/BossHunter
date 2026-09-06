"""Unit tests for original-PDF companion helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bosshunter.web.resume_original import (
	companion_pdf_exists,
	companion_pdf_path,
	remove_companion_pdf,
	resolve_configured_resume_files,
	upload_keeps_original_pdf,
	write_resume_artifacts,
)


class UploadKeepsOriginalPdfTests(unittest.TestCase):
	def test_pdf_upload_keeps_original(self):
		self.assertTrue(upload_keeps_original_pdf("简历.pdf"))
		self.assertTrue(upload_keeps_original_pdf("Resume.PDF"))

	def test_non_pdf_does_not_keep_original(self):
		self.assertFalse(upload_keeps_original_pdf("简历.md"))
		self.assertFalse(upload_keeps_original_pdf("简历.docx"))


class CompanionPdfPathTests(unittest.TestCase):
	def test_maps_markdown_to_sibling_pdf(self):
		self.assertEqual(
			companion_pdf_path(Path("/data/resumes/张三.md")),
			Path("/data/resumes/张三.pdf"),
		)


class ResolveConfiguredResumeFilesTests(unittest.TestCase):
	def test_pdf_config_maps_to_markdown_and_self(self):
		md, pdf = resolve_configured_resume_files(Path("/data/resumes/resume.pdf"))
		self.assertEqual(md, Path("/data/resumes/resume.md"))
		self.assertEqual(pdf, Path("/data/resumes/resume.pdf"))

	def test_markdown_config_maps_to_self_and_companion(self):
		md, pdf = resolve_configured_resume_files(Path("/data/resumes/resume.md"))
		self.assertEqual(md, Path("/data/resumes/resume.md"))
		self.assertEqual(pdf, Path("/data/resumes/resume.pdf"))


class WriteResumeArtifactsTests(unittest.TestCase):
	def test_writes_markdown_and_companion_pdf(self):
		with tempfile.TemporaryDirectory() as tmp:
			md_path = Path(tmp) / "resume.md"
			pdf_path = write_resume_artifacts(
				md_path,
				b"# md\n",
				original_pdf_bytes=b"%PDF-1.4 original",
			)
			self.assertEqual(pdf_path, companion_pdf_path(md_path))
			self.assertEqual(md_path.read_bytes(), b"# md\n")
			self.assertEqual(pdf_path.read_bytes(), b"%PDF-1.4 original")
			self.assertTrue(companion_pdf_exists(md_path))

	def test_removes_stale_pdf_when_new_upload_has_no_original(self):
		with tempfile.TemporaryDirectory() as tmp:
			md_path = Path(tmp) / "resume.md"
			write_resume_artifacts(md_path, b"# old\n", original_pdf_bytes=b"%PDF-old")
			result = write_resume_artifacts(md_path, b"# new\n", original_pdf_bytes=None)
			self.assertIsNone(result)
			self.assertEqual(md_path.read_text(encoding="utf-8"), "# new\n")
			self.assertFalse(companion_pdf_exists(md_path))


class RemoveCompanionPdfTests(unittest.TestCase):
	def test_removes_sibling_pdf_for_markdown_master(self):
		with tempfile.TemporaryDirectory() as tmp:
			md_path = Path(tmp) / "resume.md"
			pdf_path = Path(tmp) / "resume.pdf"
			md_path.write_text("# keep\n", encoding="utf-8")
			pdf_path.write_bytes(b"%PDF")
			self.assertTrue(remove_companion_pdf(md_path))
			self.assertTrue(md_path.exists())
			self.assertFalse(pdf_path.exists())

	def test_does_not_delete_pdf_only_legacy_without_markdown(self):
		with tempfile.TemporaryDirectory() as tmp:
			pdf_path = Path(tmp) / "resume.pdf"
			pdf_path.write_bytes(b"%PDF")
			self.assertFalse(remove_companion_pdf(pdf_path))
			self.assertTrue(pdf_path.exists())

	def test_deletes_pdf_when_sibling_markdown_exists(self):
		with tempfile.TemporaryDirectory() as tmp:
			md_path = Path(tmp) / "resume.md"
			pdf_path = Path(tmp) / "resume.pdf"
			md_path.write_text("# keep\n", encoding="utf-8")
			pdf_path.write_bytes(b"%PDF")
			self.assertTrue(remove_companion_pdf(pdf_path))
			self.assertTrue(md_path.exists())
			self.assertFalse(pdf_path.exists())

	def test_returns_false_when_companion_already_absent(self):
		with tempfile.TemporaryDirectory() as tmp:
			md_path = Path(tmp) / "resume.md"
			md_path.write_text("# keep\n", encoding="utf-8")
			self.assertFalse(remove_companion_pdf(md_path))
			self.assertTrue(md_path.exists())


if __name__ == "__main__":
	unittest.main()
