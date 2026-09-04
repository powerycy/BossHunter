"""Helpers for keeping and locating the original PDF beside converted Markdown."""

from __future__ import annotations

from pathlib import Path


def upload_keeps_original_pdf(raw_filename: str) -> bool:
	"""Return True when the uploaded file should keep a companion PDF. Pure."""
	return Path(str(raw_filename)).suffix.lower() == ".pdf"


def companion_pdf_path(markdown_path: Path) -> Path:
	"""Return the sibling .pdf path for a stored Markdown resume. Pure."""
	return Path(markdown_path).with_suffix(".pdf")


def resolve_configured_resume_files(configured_path: Path) -> tuple[Path, Path]:
	"""Map a configured resume path to (markdown_path, pdf_path) candidates. Pure.

	- ``resume.pdf`` → (``resume.md``, ``resume.pdf``)
	- ``resume.md``  → (``resume.md``, ``resume.pdf``)
	"""
	path = Path(configured_path)
	if path.suffix.lower() == ".pdf":
		return path.with_suffix(".md"), path
	return path, path.with_suffix(".pdf")


def companion_pdf_exists(markdown_path: Path) -> bool:
	"""Return True when a sibling original PDF exists on disk."""
	path = companion_pdf_path(markdown_path)
	return path.is_file()


def remove_companion_pdf(configured_path: Path) -> bool:
	"""Delete the companion PDF for a Markdown resume.

	When the configured path is already a ``.pdf``, treat it as the companion
	only if a sibling Markdown master exists (so we do not wipe a PDF-only
	legacy file that is still the sole resume). Returns True when a file was
	removed.
	"""
	path = Path(configured_path)
	markdown_path, pdf_path = resolve_configured_resume_files(path)
	if path.suffix.lower() == ".pdf" and not markdown_path.is_file():
		return False
	if not pdf_path.is_file():
		return False
	pdf_path.unlink()
	return True


def write_resume_artifacts(
	markdown_path: Path,
	markdown_bytes: bytes,
	*,
	original_pdf_bytes: bytes | None,
) -> Path | None:
	"""Write Markdown and optionally a companion PDF; remove stale PDF when absent.

	Returns the companion PDF path when written, otherwise None.
	"""
	markdown_path = Path(markdown_path)
	markdown_path.parent.mkdir(parents=True, exist_ok=True)
	markdown_path.write_bytes(markdown_bytes)

	pdf_path = companion_pdf_path(markdown_path)
	if original_pdf_bytes is None:
		if pdf_path.exists():
			pdf_path.unlink()
		return None

	pdf_path.write_bytes(original_pdf_bytes)
	return pdf_path
