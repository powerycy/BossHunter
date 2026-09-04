"""Unit tests for pure resume text helpers."""

from __future__ import annotations

import unittest

from bosshunter.web.resume_text import resume_cache_buster, sanitize_resume_text


class SanitizeResumeTextTests(unittest.TestCase):
	def test_strips_nul_bytes(self):
		self.assertEqual(sanitize_resume_text("A\x00B\x00C"), "ABC")

	def test_keeps_normal_text(self):
		self.assertEqual(sanitize_resume_text("吴澍\n电话"), "吴澍\n电话")

	def test_empty_and_none_like(self):
		self.assertEqual(sanitize_resume_text(""), "")
		self.assertEqual(sanitize_resume_text("\x00\x00"), "")


class ResumeCacheBusterTests(unittest.TestCase):
	def test_uses_integer_epoch_seconds(self):
		self.assertEqual(resume_cache_buster(1725445800.9), "1725445800")
		self.assertNotEqual(resume_cache_buster(1725445800.0), resume_cache_buster(1725445801.0))

	def test_zero_mtime(self):
		self.assertEqual(resume_cache_buster(0), "0")


if __name__ == "__main__":
	unittest.main()
