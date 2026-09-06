"""Pure helpers for choosing on-disk resume Markdown filenames."""

from __future__ import annotations

from pathlib import Path

from bosshunter.web.resume_original import resolve_configured_resume_files


def iter_resume_name_candidates(preferred_name: str):
	"""Yield preferred.md, preferred-2.md, preferred-3.md, ... Pure: no I/O."""
	preferred = Path(str(preferred_name)).name
	if not preferred:
		preferred = "resume.md"
	stem = Path(preferred).stem or "resume"
	suffix = Path(preferred).suffix or ".md"
	yield f"{stem}{suffix}"
	index = 2
	while index < 10_000:
		yield f"{stem}-{index}{suffix}"
		index += 1


def should_overwrite_existing_resume(
	*,
	existing_bytes: bytes,
	new_bytes: bytes,
	is_active_resume: bool,
	no_active_resume: bool = False,
	is_preferred_name: bool = False,
) -> bool:
	"""Decide whether an existing file may be replaced. Pure: no I/O.

	- Identical bytes → reuse/overwrite (idempotent upload)
	- Active configured resume → overwrite (user is replacing current resume)
	- No active resume + preferred stem → reclaim after DELETE (avoid resume-2.md)
	- Otherwise → caller should pick the next candidate name
	"""
	if existing_bytes == new_bytes:
		return True
	if is_active_resume:
		return True
	if no_active_resume and is_preferred_name:
		return True
	return False


def resolve_active_resume_path(raw_path: str | None, base_dir: Path | None = None) -> Path | None:
	"""Return the active Markdown resume path, or None when unset/missing.

	When config still points at a companion PDF, prefer the sibling ``.md`` path
	so a replace upload overwrites the same stem instead of creating ``*-2.md``.
	"""
	if not raw_path or not str(raw_path).strip():
		return None
	path = Path(str(raw_path).strip())
	candidates = [path]
	if base_dir is not None and not path.is_absolute():
		candidates.append(Path(base_dir) / path)
	for candidate in candidates:
		try:
			resolved = candidate.resolve()
		except OSError:
			continue
		markdown_path, pdf_path = resolve_configured_resume_files(resolved)
		if markdown_path.is_file():
			return markdown_path.resolve()
		if pdf_path.is_file():
			# PDF-only (or legacy PDF config): treat the MD stem as active for naming.
			return markdown_path.resolve()
		if resolved.is_file():
			return resolved
	return None


def select_resume_markdown_filename(
	resume_dir: Path,
	preferred_name: str,
	new_content: bytes,
	active_resume_path: Path | None,
) -> str:
	"""Pick a Markdown filename under resume_dir, avoiding orphan collisions."""
	resume_dir = Path(resume_dir)
	active = Path(active_resume_path).resolve() if active_resume_path else None
	for index, name in enumerate(iter_resume_name_candidates(preferred_name)):
		path = resume_dir / name
		if not path.exists():
			return name
		existing = path.read_bytes()
		is_active = active is not None and path.resolve() == active
		if should_overwrite_existing_resume(
			existing_bytes=existing,
			new_bytes=new_content,
			is_active_resume=is_active,
			no_active_resume=active is None,
			is_preferred_name=index == 0,
		):
			return name
	raise RuntimeError("无法分配唯一的简历文件名")
