"""Unit tests for resume filename selection helpers."""

from __future__ import annotations

import tempfile
import unittest
from itertools import islice
from pathlib import Path

from bosshunter.web.resume_names import (
	iter_resume_name_candidates,
	resolve_active_resume_path,
	select_resume_markdown_filename,
	should_overwrite_existing_resume,
)


class IterResumeNameCandidatesTests(unittest.TestCase):
	def test_yields_preferred_then_numbered(self):
		names = list(islice(iter_resume_name_candidates("简历.md"), 3))
		self.assertEqual(names[0], "简历.md")
		self.assertEqual(names[1], "简历-2.md")
		self.assertEqual(names[2], "简历-3.md")


class ShouldOverwriteExistingResumeTests(unittest.TestCase):
	def test_same_bytes_may_overwrite(self):
		self.assertTrue(
			should_overwrite_existing_resume(
				existing_bytes=b"a",
				new_bytes=b"a",
				is_active_resume=False,
			)
		)

	def test_active_resume_may_overwrite_different_bytes(self):
		self.assertTrue(
			should_overwrite_existing_resume(
				existing_bytes=b"old",
				new_bytes=b"new",
				is_active_resume=True,
			)
		)

	def test_orphan_different_bytes_must_not_overwrite_when_other_active(self):
		self.assertFalse(
			should_overwrite_existing_resume(
				existing_bytes=b"old",
				new_bytes=b"new",
				is_active_resume=False,
				no_active_resume=False,
				is_preferred_name=True,
			)
		)

	def test_no_active_resume_may_reclaim_preferred_name(self):
		self.assertTrue(
			should_overwrite_existing_resume(
				existing_bytes=b"old",
				new_bytes=b"new",
				is_active_resume=False,
				no_active_resume=True,
				is_preferred_name=True,
			)
		)


class SelectResumeMarkdownFilenameTests(unittest.TestCase):
	def test_reuses_free_preferred_name(self):
		with tempfile.TemporaryDirectory() as tmp:
			name = select_resume_markdown_filename(Path(tmp), "resume.md", b"# new\n", None)
			self.assertEqual(name, "resume.md")

	def test_overwrites_active_resume(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			active = root / "resume.md"
			active.write_text("# old\n", encoding="utf-8")
			name = select_resume_markdown_filename(root, "resume.md", b"# new\n", active)
			self.assertEqual(name, "resume.md")

	def test_reclaims_preferred_name_when_nothing_active(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			(root / "resume.md").write_text("# orphan leftover after delete\n", encoding="utf-8")
			name = select_resume_markdown_filename(root, "resume.md", b"# new\n", None)
			self.assertEqual(name, "resume.md")

	def test_avoids_orphan_collision_when_another_resume_is_active(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			(root / "resume.md").write_text("# orphan\n", encoding="utf-8")
			active = root / "other.md"
			active.write_text("# active\n", encoding="utf-8")
			name = select_resume_markdown_filename(root, "resume.md", b"# new\n", active)
			self.assertEqual(name, "resume-2.md")


class ResolveActiveResumePathTests(unittest.TestCase):
	def test_missing_default_path_is_treated_as_inactive(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			self.assertIsNone(resolve_active_resume_path("./resume.md", root))
			self.assertIsNone(resolve_active_resume_path("", root))
			self.assertIsNone(resolve_active_resume_path(None, root))

	def test_existing_relative_path_resolves(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			resume = root / "resume.md"
			resume.write_text("# x\n", encoding="utf-8")
			self.assertEqual(resolve_active_resume_path("./resume.md", root), resume.resolve())

	def test_pdf_config_resolves_to_sibling_markdown(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			md = root / "resume.md"
			pdf = root / "resume.pdf"
			md.write_text("# md\n", encoding="utf-8")
			pdf.write_bytes(b"%PDF-1.4")
			self.assertEqual(resolve_active_resume_path(str(pdf), root), md.resolve())

	def test_pdf_only_still_treats_markdown_stem_as_active(self):
		with tempfile.TemporaryDirectory() as tmp:
			root = Path(tmp)
			pdf = root / "resume.pdf"
			pdf.write_bytes(b"%PDF-1.4")
			active = resolve_active_resume_path(str(pdf), root)
			self.assertEqual(active, (root / "resume.md").resolve())
			name = select_resume_markdown_filename(root, "resume.md", b"# new\n", active)
			self.assertEqual(name, "resume.md")


class IterResumeNameCandidatesEdgeTests(unittest.TestCase):
	def test_empty_preferred_falls_back_to_resume_md(self):
		names = list(islice(iter_resume_name_candidates(""), 2))
		self.assertEqual(names[0], "resume.md")
		self.assertEqual(names[1], "resume-2.md")


class ShouldOverwriteExistingResumeEdgeTests(unittest.TestCase):
	def test_no_active_does_not_reclaim_non_preferred_candidate(self):
		self.assertFalse(
			should_overwrite_existing_resume(
				existing_bytes=b"old",
				new_bytes=b"new",
				is_active_resume=False,
				no_active_resume=True,
				is_preferred_name=False,
			)
		)


if __name__ == "__main__":
	unittest.main()
