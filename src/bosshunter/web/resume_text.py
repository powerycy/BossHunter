"""Pure text helpers for resume preview and storage."""

from __future__ import annotations


def sanitize_resume_text(text: str) -> str:
	"""Remove NUL bytes that PDF extractors may insert. Pure: no I/O."""
	return str(text).replace("\x00", "")


def resume_cache_buster(mtime: float) -> str:
	"""Return a second-precision cache token from mtime. Pure: no I/O."""
	return str(int(mtime))
