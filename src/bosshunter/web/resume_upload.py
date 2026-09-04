"""Resume upload filename handling and document-to-Markdown conversion."""

from __future__ import annotations

import re
import unicodedata
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from bosshunter.web.resume_text import sanitize_resume_text

MAX_DOCX_XML_SIZE = 20 * 1024 * 1024
SUPPORTED_RESUME_EXTENSIONS = {".md", ".docx", ".pdf"}
_WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{_WORD_NAMESPACE}}}"


class ResumeUploadError(ValueError):
	"""An invalid or unsupported resume upload."""


def safe_resume_filename(raw_filename: str) -> str:
	"""Return a filesystem-safe filename while preserving Unicode characters."""
	if not raw_filename:
		raise ResumeUploadError("文件名不能为空")

	name = unicodedata.normalize("NFC", str(raw_filename))
	name = name.replace("\\", "/").rsplit("/", 1)[-1]
	name = "".join(char for char in name if char >= " " and char != "\x7f")
	name = name.strip().strip(".")
	if not name:
		raise ResumeUploadError("文件名不能为空")

	suffix = Path(name).suffix.lower()
	if suffix not in SUPPORTED_RESUME_EXTENSIONS:
		raise ResumeUploadError("仅支持 .md、.docx 或 .pdf 格式")

	stem = name[: -len(Path(name).suffix)].strip().strip(".")
	if not stem:
		stem = "resume"

	# Keep the final name comfortably below common 255-byte filesystem limits.
	return f"{_truncate_utf8(stem, 200 - len(suffix.encode('utf-8')))}{suffix}"


def prepare_resume_content(filename: str, content: bytes) -> tuple[str, bytes]:
	"""Return the Markdown storage filename and UTF-8 content for an upload."""
	safe_name = safe_resume_filename(filename)
	suffix = Path(safe_name).suffix.lower()
	if suffix == ".md":
		try:
			text = content.decode("utf-8")
		except UnicodeDecodeError as exc:
			raise ResumeUploadError("Markdown 文件必须使用 UTF-8 编码") from exc
		return safe_name, sanitize_resume_text(text).encode("utf-8")

	markdown = docx_to_markdown(content) if suffix == ".docx" else pdf_to_markdown(content)
	output_name = f"{Path(safe_name).stem}.md"
	return output_name, sanitize_resume_text(markdown).encode("utf-8")


def pdf_to_markdown(content: bytes) -> str:
	"""Extract text-layer content from a PDF resume as Markdown-like text."""
	try:
		reader = PdfReader(BytesIO(content))
		if reader.is_encrypted:
			raise ResumeUploadError("PDF 已加密，请上传未加密的简历")
		pages = [page.extract_text() or "" for page in reader.pages]
	except ResumeUploadError:
		raise
	except (KeyError, OSError, PdfReadError, TypeError, ValueError) as exc:
		raise ResumeUploadError("PDF 文件无效或已损坏") from exc

	text = "\n\n".join(page.strip() for page in pages if page.strip()).strip()
	if not text:
		raise ResumeUploadError("未能从 PDF 提取文字；扫描版或无文字层简历请先 OCR 后再上传")
	return f"{text}\n"


def docx_to_markdown(content: bytes) -> str:
	"""Extract readable Markdown-like text from a DOCX document."""
	try:
		with ZipFile(BytesIO(content)) as archive:
			try:
				document_info = archive.getinfo("word/document.xml")
			except KeyError as exc:
				raise ResumeUploadError("Word 文件无效：缺少正文内容") from exc
			if document_info.file_size > MAX_DOCX_XML_SIZE:
				raise ResumeUploadError("Word 文件内容过大")
			document_xml = archive.read(document_info)
	except (BadZipFile, OSError, RuntimeError) as exc:
		raise ResumeUploadError("Word 文件无效或已损坏") from exc

	try:
		root = ElementTree.fromstring(document_xml)
	except ElementTree.ParseError as exc:
		raise ResumeUploadError("Word 文件正文无法解析") from exc

	body = root.find(f".//{_W}body")
	if body is None:
		raise ResumeUploadError("Word 文件无效：缺少正文内容")

	lines: list[str] = []
	for child in body:
		if child.tag == f"{_W}p":
			line = _paragraph_to_markdown(child)
			if line:
				lines.append(line)
		elif child.tag == f"{_W}tbl":
			lines.extend(_table_to_markdown(child))

	markdown = "\n\n".join(lines).strip()
	if not markdown:
		raise ResumeUploadError("Word 文件中未识别到可用文字")
	return f"{markdown}\n"


def _paragraph_to_markdown(paragraph: ElementTree.Element) -> str:
	text = _element_text(paragraph)
	if not text:
		return ""

	style_node = paragraph.find(f"./{_W}pPr/{_W}pStyle")
	style = style_node.get(f"{_W}val", "") if style_node is not None else ""
	heading_match = re.fullmatch(r"(?:Heading|标题)\s*([1-6])", style, flags=re.IGNORECASE)
	if style.lower() in {"title", "标题"}:
		return f"# {text}"
	if heading_match:
		return f"{'#' * int(heading_match.group(1))} {text}"
	if paragraph.find(f"./{_W}pPr/{_W}numPr") is not None:
		return f"- {text}"
	return text


def _table_to_markdown(table: ElementTree.Element) -> list[str]:
	rows: list[str] = []
	for row in table.findall(f"./{_W}tr"):
		cells = []
		for cell in row.findall(f"./{_W}tc"):
			cell_text = " / ".join(
				text for paragraph in cell.findall(f"./{_W}p") if (text := _element_text(paragraph))
			)
			if cell_text:
				cells.append(cell_text)
		if cells:
			rows.append(" | ".join(cells))
	return rows


def _element_text(element: ElementTree.Element) -> str:
	parts: list[str] = []
	for node in element.iter():
		if node.tag == f"{_W}t" and node.text:
			parts.append(node.text)
		elif node.tag == f"{_W}tab":
			parts.append(" ")
		elif node.tag in {f"{_W}br", f"{_W}cr"}:
			parts.append("\n")
	return re.sub(r"[ \t]+", " ", "".join(parts)).strip()


def _truncate_utf8(value: str, max_bytes: int) -> str:
	encoded = value.encode("utf-8")
	if len(encoded) <= max_bytes:
		return value
	return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()
