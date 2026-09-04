"""Pure helpers and local loaders for resume metadata shown in the config panel."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from bosshunter.config import DEFAULTS
from bosshunter.web.resume_original import resolve_configured_resume_files
from bosshunter.web.resume_text import resume_cache_buster, sanitize_resume_text

DEFAULT_RESUME_PATH = str(DEFAULTS.get("profile", {}).get("resume_path") or "./resume.md")


def format_file_size(size: int) -> str:
	"""Return a human-readable file size label. Pure: no I/O."""
	if size < 0:
		raise ValueError("size must be non-negative")
	if size < 1024:
		return f"{size} B"
	kb = size / 1024
	if kb < 1024:
		return f"{kb:.1f} KB"
	return f"{kb / 1024:.1f} MB"


def format_uploaded_at(mtime: float) -> str:
	"""Format a file mtime as local YYYY-MM-DD HH:MM. Pure: no I/O."""
	return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))


def is_default_resume_placeholder(raw_path: str | None) -> bool:
	"""Return True when path is the config default placeholder. Pure: no I/O."""
	if raw_path is None:
		return True
	raw = str(raw_path).strip().replace("\\", "/")
	if not raw:
		return True
	default = DEFAULT_RESUME_PATH.strip().replace("\\", "/")
	candidates = {
		default,
		default.lstrip("./"),
		f"./{Path(default).name}",
		Path(default).name,
	}
	return raw in candidates


def resolve_resume_filesystem_path(raw_path: str | Path, base_dir: Path | None = None) -> Path:
	"""Resolve a configured resume path against base_dir when relative."""
	path = Path(raw_path)
	if not path.is_absolute() and base_dir is not None:
		path = Path(base_dir) / path
	return path.resolve()


def configured_resume_exists(raw_path: str | None, base_dir: Path | None = None) -> bool:
	"""Return True when Markdown and/or original PDF resume files exist on disk."""
	if not raw_path or not str(raw_path).strip():
		return False
	configured = resolve_resume_filesystem_path(raw_path, base_dir)
	markdown_path, pdf_path = resolve_configured_resume_files(configured)
	return markdown_path.is_file() or pdf_path.is_file()


def build_resume_info_payload(
	*,
	filename: str,
	size: int,
	mtime: float,
	content: str,
	path: str,
	has_original_pdf: bool = False,
	original_pdf_path: str | None = None,
) -> dict[str, Any]:
	"""Assemble the resume JSON payload for the web UI. Pure: no I/O."""
	clean_content = sanitize_resume_text(content)
	payload: dict[str, Any] = {
		"filename": filename,
		"size": size,
		"size_label": format_file_size(size),
		"uploaded_at": format_uploaded_at(mtime),
		"cache_buster": resume_cache_buster(mtime),
		"content": clean_content,
		"path": path,
		"has_original_pdf": bool(has_original_pdf),
	}
	if has_original_pdf:
		pdf_name = Path(original_pdf_path).name if original_pdf_path else Path(path).with_suffix(".pdf").name
		payload["original_filename"] = pdf_name
		payload["original_preview_url"] = "/api/resume/original"
	return payload


def read_resume_text(path: Path) -> str:
	"""Read UTF-8 resume text from disk and strip embedded NUL bytes."""
	return sanitize_resume_text(path.read_text(encoding="utf-8"))


def read_and_repair_resume_text(path: Path) -> str:
	"""Read resume text once; best-effort NUL repair on disk does not fail the read."""
	raw = path.read_text(encoding="utf-8")
	clean = sanitize_resume_text(raw)
	if clean != raw:
		try:
			path.write_text(clean, encoding="utf-8")
		except OSError:
			pass
	return clean


def ensure_resume_markdown_file(configured_path: Path) -> Path | None:
	"""Ensure an AI-readable Markdown resume exists; create it from PDF if needed."""
	configured = Path(configured_path)
	markdown_path, pdf_path = resolve_configured_resume_files(configured)

	if markdown_path.is_file():
		return markdown_path

	if not pdf_path.is_file():
		return None

	from bosshunter.web.resume_upload import pdf_to_markdown

	text = sanitize_resume_text(pdf_to_markdown(pdf_path.read_bytes()))
	markdown_path.parent.mkdir(parents=True, exist_ok=True)
	markdown_path.write_text(text, encoding="utf-8")
	return markdown_path


def load_resume_info(path: Path) -> dict[str, Any] | None:
	"""Load resume metadata/content and always prefer the Markdown path for AI."""
	configured = Path(path)
	markdown_path = ensure_resume_markdown_file(configured)
	if markdown_path is None:
		return None

	_, pdf_path = resolve_configured_resume_files(markdown_path)
	has_pdf = pdf_path.is_file()
	content = read_and_repair_resume_text(markdown_path)
	stat = markdown_path.stat()
	return build_resume_info_payload(
		filename=markdown_path.name,
		size=stat.st_size,
		mtime=stat.st_mtime,
		content=content,
		path=str(markdown_path),
		has_original_pdf=has_pdf,
		original_pdf_path=str(pdf_path) if has_pdf else None,
	)
