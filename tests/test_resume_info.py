"""Unit tests for pure resume info helpers."""

from __future__ import annotations

import io
import tempfile
import time
import unittest
from pathlib import Path

from bosshunter.web.resume_info import (
	build_resume_info_payload,
	configured_resume_exists,
	format_file_size,
	format_uploaded_at,
	is_default_resume_placeholder,
	load_resume_info,
	read_resume_text,
	resolve_resume_filesystem_path,
)


class FormatFileSizeTests(unittest.TestCase):
	def test_bytes(self):
		self.assertEqual(format_file_size(0), "0 B")
		self.assertEqual(format_file_size(512), "512 B")

	def test_kilobytes(self):
		self.assertEqual(format_file_size(1024), "1.0 KB")
		self.assertEqual(format_file_size(1536), "1.5 KB")

	def test_megabytes(self):
		self.assertEqual(format_file_size(1024 * 1024), "1.0 MB")

	def test_rejects_negative(self):
		with self.assertRaises(ValueError):
			format_file_size(-1)


class FormatUploadedAtTests(unittest.TestCase):
	def test_formats_local_time(self):
		mtime = time.mktime(time.strptime("2026-09-04 15:30", "%Y-%m-%d %H:%M"))
		self.assertEqual(format_uploaded_at(mtime), "2026-09-04 15:30")


class BuildResumeInfoPayloadTests(unittest.TestCase):
	def test_basic_payload(self):
		mtime = time.mktime(time.strptime("2026-09-04 15:30", "%Y-%m-%d %H:%M"))
		payload = build_resume_info_payload(
			filename="resume.md",
			size=2048,
			mtime=mtime,
			content="# 李雷\n\n产品经理\n",
			path="/tmp/resume.md",
		)
		self.assertEqual(
			payload,
			{
				"filename": "resume.md",
				"size": 2048,
				"size_label": "2.0 KB",
				"uploaded_at": "2026-09-04 15:30",
				"cache_buster": str(int(mtime)),
				"content": "# 李雷\n\n产品经理\n",
				"path": "/tmp/resume.md",
				"has_original_pdf": False,
			},
		)

	def test_sanitizes_nul_bytes_in_content(self):
		mtime = time.mktime(time.strptime("2026-09-04 15:30", "%Y-%m-%d %H:%M"))
		payload = build_resume_info_payload(
			filename="resume.md",
			size=3,
			mtime=mtime,
			content="A\x00B",
			path="/tmp/resume.md",
		)
		self.assertEqual(payload["content"], "AB")

	def test_includes_original_pdf_fields_when_present(self):
		mtime = time.mktime(time.strptime("2026-09-04 15:30", "%Y-%m-%d %H:%M"))
		payload = build_resume_info_payload(
			filename="resume.md",
			size=100,
			mtime=mtime,
			content="# x\n",
			path="/tmp/resume.md",
			has_original_pdf=True,
		)
		self.assertTrue(payload["has_original_pdf"])
		self.assertEqual(payload["original_filename"], "resume.pdf")
		self.assertEqual(payload["original_preview_url"], "/api/resume/original")
		self.assertEqual(payload["cache_buster"], str(int(mtime)))


class LoadResumeInfoTests(unittest.TestCase):
	def test_returns_none_for_missing(self):
		self.assertIsNone(load_resume_info(Path("/tmp/does-not-exist-resume.md")))

	def test_loads_markdown(self):
		with tempfile.TemporaryDirectory() as tmp:
			path = Path(tmp) / "resume.md"
			path.write_text("# body\n", encoding="utf-8")
			info = load_resume_info(path)
			self.assertIsNotNone(info)
			assert info is not None
			self.assertEqual(info["content"], "# body\n")
			self.assertFalse(info["has_original_pdf"])

	def test_detects_companion_original_pdf(self):
		with tempfile.TemporaryDirectory() as tmp:
			path = Path(tmp) / "resume.md"
			path.write_text("# body\n", encoding="utf-8")
			path.with_suffix(".pdf").write_bytes(b"%PDF-1.4")
			info = load_resume_info(path)
			self.assertIsNotNone(info)
			assert info is not None
			self.assertTrue(info["has_original_pdf"])
			self.assertEqual(info["original_filename"], "resume.pdf")

	def test_repairs_nul_bytes_on_disk_when_loading_markdown(self):
		with tempfile.TemporaryDirectory() as tmp:
			path = Path(tmp) / "resume.md"
			path.write_bytes(b"A\x00B\n")
			info = load_resume_info(path)
			self.assertIsNotNone(info)
			assert info is not None
			self.assertEqual(info["content"], "AB\n")
			self.assertEqual(path.read_text(encoding="utf-8"), "AB\n")

	def test_loads_when_config_points_at_pdf_with_sibling_markdown(self):
		with tempfile.TemporaryDirectory() as tmp:
			md_path = Path(tmp) / "resume.md"
			pdf_path = Path(tmp) / "resume.pdf"
			md_path.write_text("# from md\n", encoding="utf-8")
			pdf_path.write_bytes(b"%PDF-1.4")
			info = load_resume_info(pdf_path)
			self.assertIsNotNone(info)
			assert info is not None
			self.assertEqual(info["content"], "# from md\n")
			self.assertTrue(info["has_original_pdf"])
			self.assertEqual(info["filename"], "resume.md")
			self.assertEqual(info["path"], str(md_path))

	def test_materializes_markdown_when_only_pdf_exists(self):
		from pypdf import PdfWriter
		from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

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
		contents.set_data(b"BT /F1 12 Tf 72 720 Td (Only PDF Candidate) Tj ET")
		page[NameObject("/Contents")] = contents
		buf = io.BytesIO()
		writer.write(buf)

		with tempfile.TemporaryDirectory() as tmp:
			pdf_path = Path(tmp) / "solo.pdf"
			pdf_path.write_bytes(buf.getvalue())
			info = load_resume_info(pdf_path)
			self.assertIsNotNone(info)
			assert info is not None
			md_path = Path(tmp) / "solo.md"
			self.assertTrue(md_path.is_file())
			self.assertEqual(info["path"], str(md_path))
			self.assertIn("Only PDF Candidate", info["content"])
			self.assertTrue(info["has_original_pdf"])

	def test_load_encrypted_pdf_only_raises_upload_error(self):
		from pypdf import PdfWriter

		from bosshunter.web.resume_upload import ResumeUploadError

		writer = PdfWriter()
		writer.add_blank_page(width=612, height=792)
		writer.encrypt("secret")
		buf = io.BytesIO()
		writer.write(buf)

		with tempfile.TemporaryDirectory() as tmp:
			pdf_path = Path(tmp) / "locked.pdf"
			pdf_path.write_bytes(buf.getvalue())
			with self.assertRaises(ResumeUploadError):
				load_resume_info(pdf_path)
			self.assertFalse((Path(tmp) / "locked.md").exists())

	def test_load_scanned_pdf_only_raises_upload_error(self):
		from pypdf import PdfWriter

		from bosshunter.web.resume_upload import ResumeUploadError

		writer = PdfWriter()
		writer.add_blank_page(width=612, height=792)
		buf = io.BytesIO()
		writer.write(buf)

		with tempfile.TemporaryDirectory() as tmp:
			pdf_path = Path(tmp) / "scan.pdf"
			pdf_path.write_bytes(buf.getvalue())
			with self.assertRaises(ResumeUploadError):
				load_resume_info(pdf_path)

	def test_read_resume_text(self):
		with tempfile.TemporaryDirectory() as tmp:
			path = Path(tmp) / "resume.md"
			path.write_text("hello", encoding="utf-8")
			self.assertEqual(read_resume_text(path), "hello")

	def test_read_resume_text_strips_nul(self):
		with tempfile.TemporaryDirectory() as tmp:
			path = Path(tmp) / "resume.md"
			path.write_bytes(b"A\x00B")
			self.assertEqual(read_resume_text(path), "AB")


class DefaultResumePlaceholderTests(unittest.TestCase):
	def test_recognizes_default_forms(self):
		self.assertTrue(is_default_resume_placeholder("./resume.md"))
		self.assertTrue(is_default_resume_placeholder("resume.md"))
		self.assertTrue(is_default_resume_placeholder(""))
		self.assertTrue(is_default_resume_placeholder(None))

	def test_rejects_explicit_custom_paths(self):
		self.assertFalse(is_default_resume_placeholder("/tmp/custom.md"))
		self.assertFalse(is_default_resume_placeholder("data/resumes/mine.md"))


class ResolveResumeFilesystemPathTests(unittest.TestCase):
	def test_resolves_relative_against_base_dir(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			resolved = resolve_resume_filesystem_path("./resume.md", root)
			self.assertEqual(resolved, (root / "resume.md").resolve())

	def test_keeps_absolute_paths(self):
		absolute = Path("/tmp/absolute-resume.md")
		self.assertEqual(resolve_resume_filesystem_path(absolute, Path("/other")), absolute.resolve())


class ConfiguredResumeExistsTests(unittest.TestCase):
	def test_false_when_missing(self):
		with tempfile.TemporaryDirectory() as tmp:
			self.assertFalse(configured_resume_exists("./resume.md", Path(tmp)))

	def test_true_for_markdown_or_companion_pdf(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			md = root / "resume.md"
			md.write_text("# x\n", encoding="utf-8")
			self.assertTrue(configured_resume_exists(str(md), root))
			md.unlink()
			(root / "resume.pdf").write_bytes(b"%PDF-1.4")
			self.assertTrue(configured_resume_exists(str(root / "resume.md"), root))


if __name__ == "__main__":
	unittest.main()
