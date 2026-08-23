"""Evidence-first Resume Studio application services."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from bosshunter.ai.credentials import call_anthropic_text
from bosshunter.resume_builder.documents import ResumeUploadError, prepare_resume_content, safe_resume_filename
from bosshunter.resume_builder.store import (
	create_source,
	create_version,
	delete_source_records,
	get_source,
	get_source_by_hash,
	get_version,
	list_facts,
	mark_version_active,
	replace_fact_candidates,
	set_source_status,
	source_version_references,
)

MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_SOURCE_CHARS = 120_000
DEFAULT_CHUNK_CHARS = 12_000
FACT_CATEGORIES = {
	"基本信息",
	"个人优势",
	"工作经历",
	"项目经历",
	"技术栈",
	"量化成果",
	"作品链接",
	"教育经历",
	"证书",
	"其他",
}
_TOKEN_PATTERNS = [
	re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])"),
	re.compile(r"https?://[^\s)>）】]+", re.IGNORECASE),
	re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"),
	re.compile(r"(?<!\d)(?:19|20)\d{2}(?:[./年-](?:0?[1-9]|1[0-2]))?(?:[./月-](?:0?[1-9]|[12]\d|3[01]))?(?:日)?(?!\d)"),
	re.compile(
		r"(?<![\w.])\d+(?:\.\d+)?(?:\s*[-~至到]\s*\d+(?:\.\d+)?)?\s*"
		r"(?:%|％|年|个月|月|天|人|次|篇|万|亿|元|K|k|W|w|倍|\+)(?!\w)"
	),
]


class ResumeBuilderError(ValueError):
	"""A safe, user-facing Resume Studio failure."""


def _clean_whitespace(value: str) -> str:
	return re.sub(r"\s+", " ", value).strip()


def _structured_tokens(text: str) -> set[str]:
	return {
		_clean_whitespace(match.group(0)).casefold()
		for pattern in _TOKEN_PATTERNS
		for match in pattern.finditer(text)
	}


def _unsupported_tokens(candidate: str, evidence: str) -> list[str]:
	supported = _structured_tokens(evidence)
	return sorted(token for token in _structured_tokens(candidate) if token not in supported)


def _json_payload(raw: str) -> dict:
	text = raw.strip()
	text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
	text = re.sub(r"\s*```$", "", text)
	start = text.find("{")
	end = text.rfind("}")
	if start < 0 or end <= start:
		raise ResumeBuilderError("AI 未返回可解析的结构化结果")
	try:
		payload = json.loads(text[start : end + 1])
	except json.JSONDecodeError as exc:
		raise ResumeBuilderError("AI 返回的结构化结果格式无效") from exc
	if not isinstance(payload, dict):
		raise ResumeBuilderError("AI 返回结果必须是 JSON 对象")
	return payload


def _chunks(text: str, max_chars: int = DEFAULT_CHUNK_CHARS) -> list[str]:
	paragraphs = re.split(r"\n\s*\n", text)
	chunks: list[str] = []
	current: list[str] = []
	current_length = 0
	for paragraph in paragraphs:
		paragraph = paragraph.strip()
		if not paragraph:
			continue
		if len(paragraph) > max_chars:
			if current:
				chunks.append("\n\n".join(current))
				current, current_length = [], 0
			chunks.extend(paragraph[index : index + max_chars] for index in range(0, len(paragraph), max_chars))
			continue
		if current and current_length + len(paragraph) + 2 > max_chars:
			chunks.append("\n\n".join(current))
			current, current_length = [], 0
		current.append(paragraph)
		current_length += len(paragraph) + 2
	if current:
		chunks.append("\n\n".join(current))
	return chunks


def ingest_resume_source(
	conn: sqlite3.Connection,
	*,
	filename: str,
	content: bytes,
	storage_dir: Path,
) -> tuple[dict, bool]:
	"""Normalize and store one local source, returning (source, duplicate)."""
	if len(content) > MAX_SOURCE_BYTES:
		raise ResumeBuilderError("文件大小超过 10MB 限制")
	original_name = safe_resume_filename(filename)
	stored_name, normalized_bytes = prepare_resume_content(original_name, content)
	try:
		normalized_text = normalized_bytes.decode("utf-8").replace("\r\n", "\n").strip()
	except UnicodeDecodeError as exc:
		raise ResumeUploadError("材料必须能转换为 UTF-8 文本") from exc
	if len(normalized_text) < 10:
		raise ResumeBuilderError("材料内容过少，无法提取有效简历事实")
	if len(normalized_text) > MAX_SOURCE_CHARS:
		raise ResumeBuilderError("材料文本超过 120000 字符，请拆分后上传")

	digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
	existing = get_source_by_hash(conn, digest)
	if existing:
		return existing, True

	source_id = uuid4().hex
	storage_dir.mkdir(parents=True, exist_ok=True)
	destination = storage_dir / f"{source_id}_{stored_name}"
	temporary = storage_dir / f".{source_id}.tmp"
	try:
		temporary.write_text(f"{normalized_text}\n", encoding="utf-8")
		temporary.replace(destination)
	finally:
		temporary.unlink(missing_ok=True)
	source = create_source(
		conn,
		filename=original_name,
		source_type=Path(original_name).suffix.lower().lstrip("."),
		stored_path=str(destination),
		content_hash=digest,
		normalized_text=normalized_text,
		source_id=source_id,
	)
	return source, False


def extract_source_facts(
	conn: sqlite3.Connection,
	source_id: str,
	config: dict,
	*,
	call_text: Callable[..., str | None] | None = None,
) -> list[dict]:
	"""Extract evidence-backed fact candidates from one source."""
	source = get_source(conn, source_id)
	if not source:
		raise ResumeBuilderError("材料不存在")
	caller = call_text or call_anthropic_text
	set_source_status(conn, source_id, "extracting")
	candidates: list[dict] = []
	seen: set[str] = set()
	try:
		for index, chunk in enumerate(_chunks(source["normalized_text"]), start=1):
			prompt = f"""你是简历事实抽取器。只从下面的原始材料中提取可用于简历的明确事实。

规则：
1. 不得推测、扩写或补充材料中没有的信息。
2. 每条事实必须包含材料中的原文证据 evidence；尽量逐字摘录。
3. 数字、日期、链接、公司、项目、技术名称必须来自原文。
4. category 只能是：{', '.join(sorted(FACT_CATEGORIES))}。
5. confidence 是 0 到 1 的数字。
6. 只输出 JSON：{{"facts":[{{"category":"项目经历","content":"事实表述","evidence":"原文证据","confidence":0.9}}]}}。

材料名称：{source['filename']}
分片：{index}
原始材料：
{chunk}
"""
			raw = caller(prompt, config, 6000, purpose="resume_source")
			if not raw:
				raise ResumeBuilderError("AI 服务未返回材料抽取结果")
			items = _json_payload(raw).get("facts")
			if not isinstance(items, list):
				raise ResumeBuilderError("AI 抽取结果缺少 facts 数组")
			for item in items:
				if not isinstance(item, dict):
					continue
				category = str(item.get("category", "其他")).strip()
				content = _clean_whitespace(str(item.get("content", "")))
				evidence = _clean_whitespace(str(item.get("evidence", "")))
				if category not in FACT_CATEGORIES or not content or not evidence:
					continue
				if len(content) > 1000 or len(evidence) > 2000:
					continue
				if evidence not in _clean_whitespace(chunk):
					continue
				if _unsupported_tokens(content, evidence):
					continue
				key = content.casefold()
				if key in seen:
					continue
				seen.add(key)
				try:
					confidence = max(0.0, min(1.0, float(item.get("confidence", 0))))
				except (TypeError, ValueError):
					confidence = 0.0
				candidates.append({
					"category": category,
					"content": content,
					"evidence": evidence,
					"confidence": confidence,
				})
		if not candidates:
			raise ResumeBuilderError("未提取到带原文证据的简历事实")
		facts = replace_fact_candidates(conn, source_id, candidates)
		set_source_status(conn, source_id, "review", None)
		return facts
	except Exception as exc:
		set_source_status(conn, source_id, "failed", str(exc)[:500])
		raise


def compose_resume_version(
	conn: sqlite3.Connection,
	config: dict,
	*,
	target_role: str,
	output_dir: Path,
	call_text: Callable[..., str | None] | None = None,
) -> dict:
	"""Compose a draft master resume from accepted facts only."""
	facts = list_facts(conn, status="accepted")
	if not facts:
		raise ResumeBuilderError("请先接受至少一条材料事实")
	caller = call_text or call_anthropic_text
	public_facts = [
		{"id": fact["id"], "category": fact["category"], "content": fact["effective_content"]}
		for fact in facts
	]
	prompt = f"""你是严谨的中文简历编辑。请把已审核事实整理成一份结构清晰的主简历草稿。

规则：
1. 只能使用给定事实，不得新增、猜测或补全任何信息。
2. 每个条目必须列出支撑它的 fact_ids，且只能引用给定 ID。
3. 可以排序、压缩和合并事实，但不得创造数字、日期、链接、公司、项目或技术名称。
4. 不输出前言、备注或免责声明。
5. 只输出 JSON：{{"sections":[{{"title":"项目经历","items":[{{"text":"简历条目","fact_ids":["id"]}}]}}]}}。

目标方向：{target_role or '通用主简历'}
已审核事实：
{json.dumps(public_facts, ensure_ascii=False)}
"""
	raw = caller(prompt, config, 8000, purpose="resume_compose")
	if not raw:
		raise ResumeBuilderError("AI 服务未返回主简历草稿")
	payload = _json_payload(raw)
	sections = payload.get("sections")
	if not isinstance(sections, list):
		raise ResumeBuilderError("主简历生成结果缺少 sections 数组")

	fact_map = {fact["id"]: fact for fact in facts}
	used_ids: list[str] = []
	markdown_lines = ["# 个人简历"]
	for section in sections:
		if not isinstance(section, dict):
			continue
		title = _clean_whitespace(str(section.get("title", "")))[:40]
		items = section.get("items")
		if not title or not isinstance(items, list):
			continue
		section_lines: list[str] = []
		for item in items:
			if not isinstance(item, dict):
				continue
			text = _clean_whitespace(str(item.get("text", "")))
			fact_ids = [str(value) for value in item.get("fact_ids", []) if str(value) in fact_map]
			if not text or not fact_ids:
				continue
			evidence = "\n".join(
				f"{fact_map[fact_id]['effective_content']}\n{fact_map[fact_id]['evidence']}" for fact_id in fact_ids
			)
			unsupported = _unsupported_tokens(text, evidence)
			if unsupported:
				raise ResumeBuilderError(f"主简历包含无来源事实：{', '.join(unsupported)}")
			section_lines.append(f"- {text}")
			used_ids.extend(fact_ids)
		if section_lines:
			markdown_lines.extend(["", f"## {title}", "", *section_lines])
	if not used_ids:
		raise ResumeBuilderError("主简历生成结果没有可追溯条目")

	markdown = "\n".join(markdown_lines).strip() + "\n"
	version_id = uuid4().hex
	output_dir.mkdir(parents=True, exist_ok=True)
	file_path = output_dir / f"master_resume_{version_id[:12]}.md"
	temporary = output_dir / f".{version_id}.tmp"
	try:
		temporary.write_text(markdown, encoding="utf-8")
		temporary.replace(file_path)
	finally:
		temporary.unlink(missing_ok=True)
	name = f"主简历 {datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M')}"
	return create_version(
		conn,
		name=name,
		target_role=target_role.strip(),
		markdown=markdown,
		file_path=str(file_path),
		fact_ids=used_ids,
		version_id=version_id,
	)


def activate_resume_version(conn: sqlite3.Connection, version_id: str) -> dict:
	version = get_version(conn, version_id)
	if not version:
		raise ResumeBuilderError("主简历版本不存在")
	if not Path(version["file_path"]).exists():
		raise ResumeBuilderError("主简历版本文件不存在")
	return mark_version_active(conn, version_id) or version


def delete_resume_source(
	conn: sqlite3.Connection,
	source_id: str,
	*,
	storage_dir: Path,
	confirmed: bool,
) -> None:
	if not confirmed:
		raise ResumeBuilderError("删除材料需要明确确认")
	source = get_source(conn, source_id)
	if not source:
		raise ResumeBuilderError("材料不存在")
	if source_version_references(conn, source_id):
		raise ResumeBuilderError("材料已被主简历版本引用，不能删除")
	path = Path(source["stored_path"]).resolve()
	root = storage_dir.resolve()
	if not path.is_relative_to(root):
		raise ResumeBuilderError("材料路径不在受管目录内")
	path.unlink(missing_ok=True)
	delete_source_records(conn, source_id)
