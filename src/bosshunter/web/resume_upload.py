"""Resume upload filename handling and document-to-Markdown conversion."""

from __future__ import annotations

import math
import re
import time
import unicodedata
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_DOCX_XML_SIZE = 20 * 1024 * 1024
MAX_PDF_PAGES = 10
OCR_RENDER_SCALE = 2.0
MIN_OCR_RENDER_SCALE = 0.75
MAX_OCR_PAGE_PIXELS = 4_000_000
MAX_OCR_TOTAL_PIXELS = 20_000_000
MAX_OCR_SECONDS = 90
MIN_OCR_TEXT_CHARS = 20
RAPID_OCR_PARAMS = {
	"Global.log_level": "warning",
	"Global.max_side_len": 1200,
	"EngineConfig.onnxruntime.intra_op_num_threads": 2,
	"EngineConfig.onnxruntime.inter_op_num_threads": 1,
	"Cls.cls_batch_num": 4,
	"Rec.rec_batch_num": 4,
}
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


def prepare_resume_content(filename: str, content: bytes) -> tuple[str, bytes, str | None]:
	"""Return the storage filename, UTF-8 content, and any review warning."""
	safe_name = safe_resume_filename(filename)
	suffix = Path(safe_name).suffix.lower()
	if suffix == ".md":
		try:
			content.decode("utf-8")
		except UnicodeDecodeError as exc:
			raise ResumeUploadError("Markdown 文件必须使用 UTF-8 编码") from exc
		return safe_name, content, None

	if suffix == ".docx":
		markdown = docx_to_markdown(content)
		warning = None
	else:
		markdown, used_ocr = _convert_pdf_to_markdown(content)
		warning = "该 PDF 没有可用文字层，已在本机 OCR；请核对识别结果后再使用。" if used_ocr else None
	output_name = f"{Path(safe_name).stem}.md"
	return output_name, markdown.encode("utf-8"), warning


def pdf_to_markdown(content: bytes) -> str:
	"""Extract a PDF resume as Markdown-like text, with local OCR fallback."""
	markdown, _ = _convert_pdf_to_markdown(content)
	return markdown


def _convert_pdf_to_markdown(content: bytes) -> tuple[str, bool]:
	"""Return converted text and whether the optional local OCR path was used."""
	try:
		reader = PdfReader(BytesIO(content))
		if reader.is_encrypted:
			raise ResumeUploadError("PDF 已加密，请上传未加密的简历")
		if len(reader.pages) > MAX_PDF_PAGES:
			raise ResumeUploadError(f"PDF 页数超过 {MAX_PDF_PAGES} 页限制")
		pages = [page.extract_text() or "" for page in reader.pages]
	except ResumeUploadError:
		raise
	except (KeyError, OSError, PdfReadError, TypeError, ValueError) as exc:
		raise ResumeUploadError("PDF 文件无效或已损坏") from exc

	text = "\n\n".join(page.strip() for page in pages if page.strip()).strip()
	if text:
		return f"{text}\n", False
	return _ocr_pdf_to_markdown(content), True


def _load_ocr_runtime():
	"""Load optional OCR dependencies only when a PDF has no text layer."""
	try:
		import numpy as np
		import pypdfium2 as pdfium
		from rapidocr import RapidOCR
	except ImportError as exc:
		raise ResumeUploadError(
			'PDF 没有可用文字层；请安装本地 OCR 组件后重试：pip install -e ".[ocr]"'
		) from exc
	return np, pdfium, RapidOCR


def _create_ocr_engine(rapid_ocr_class):
	"""Create the pinned local engine with an actionable model-install error."""
	try:
		return rapid_ocr_class(params=RAPID_OCR_PARAMS)
	except Exception as exc:
		raise ResumeUploadError(
			'本地 OCR 模型加载失败；请联网重新安装完整组件：pip install --force-reinstall "rapidocr==3.9.2"。'
			"安装完成后可断网识别。"
		) from exc


def _ocr_pdf_to_markdown(content: bytes) -> str:
	"""Recognize an image-only PDF locally without sending it to a remote service."""
	np, pdfium, rapid_ocr_class = _load_ocr_runtime()
	document = None
	try:
		document = pdfium.PdfDocument(content)
		if len(document) > MAX_PDF_PAGES:
			raise ResumeUploadError(f"PDF 页数超过 {MAX_PDF_PAGES} 页限制")
		engine = _create_ocr_engine(rapid_ocr_class)
		started_at = time.monotonic()
		total_rendered_pixels = 0
		page_texts: list[str] = []
		for page_index in range(len(document)):
			if time.monotonic() - started_at > MAX_OCR_SECONDS:
				raise ResumeUploadError(f"PDF 本地 OCR 超过 {MAX_OCR_SECONDS} 秒限制，请拆分或压缩后重试")
			page = document[page_index]
			bitmap = None
			pil_image = None
			try:
				width, height = page.get_size()
				scale = _bounded_ocr_render_scale(float(width), float(height))
				rendered_pixels = math.ceil(float(width) * scale) * math.ceil(float(height) * scale)
				total_rendered_pixels += rendered_pixels
				if total_rendered_pixels > MAX_OCR_TOTAL_PIXELS:
					raise ResumeUploadError("PDF 页面图像总量过大，请压缩或拆分后重试")
				bitmap = page.render(scale=scale)
				pil_image = bitmap.to_pil().convert("RGB")
				image = np.array(pil_image, copy=True)
			finally:
				if pil_image is not None:
					pil_image.close()
				if bitmap is not None:
					bitmap.close()
				page.close()
			result = engine(image)
			if time.monotonic() - started_at > MAX_OCR_SECONDS:
				raise ResumeUploadError(f"PDF 本地 OCR 超过 {MAX_OCR_SECONDS} 秒限制，请拆分或压缩后重试")
			page_text = _ocr_result_to_text(result.boxes, result.txts, image.shape[1])
			if page_text:
				page_texts.append(page_text)
	except ResumeUploadError:
		raise
	except Exception as exc:
		raise ResumeUploadError("PDF 本地 OCR 失败，请确认文件完整且页面图像清晰") from exc
	finally:
		if document is not None:
			document.close()

	text = "\n\n".join(page_texts).strip()
	if len(re.sub(r"\s+", "", text)) < MIN_OCR_TEXT_CHARS:
		raise ResumeUploadError("未能从 PDF 识别到足够文字，请确认页面清晰或改用 .docx / .md 简历")
	return f"{text}\n"


def _bounded_ocr_render_scale(width: float, height: float) -> float:
	"""Keep one rendered page within a predictable pixel budget."""
	if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
		raise ResumeUploadError("PDF 页面尺寸无效")
	required_scale = math.sqrt(MAX_OCR_PAGE_PIXELS / (width * height))
	if required_scale < MIN_OCR_RENDER_SCALE:
		raise ResumeUploadError("PDF 页面尺寸过大，请压缩或拆分后重试")
	return min(OCR_RENDER_SCALE, required_scale)


def _ocr_result_to_text(boxes: Any, texts: Any, page_width: float) -> str:
	"""Rebuild OCR lines while keeping two-column resumes in column order."""
	if boxes is None or texts is None:
		return ""

	items: list[dict[str, float | str]] = []
	for box, raw_text in zip(boxes, texts):
		text = str(raw_text).strip()
		if not text:
			continue
		xs = [float(point[0]) for point in box]
		ys = [float(point[1]) for point in box]
		left, right = min(xs), max(xs)
		top, bottom = min(ys), max(ys)
		items.append({
			"text": text,
			"left": left,
			"right": right,
			"top": top,
			"bottom": bottom,
			"center_x": (left + right) / 2,
			"center_y": (top + bottom) / 2,
			"height": max(1.0, bottom - top),
		})
	if not items:
		return ""

	split = _detect_ocr_column_split(items, float(page_width))
	if split is None:
		return _format_ocr_column(items)

	left_items = [item for item in items if float(item["center_x"]) < split]
	right_items = [item for item in items if float(item["center_x"]) >= split]
	return "\n\n".join(
		part for part in (_format_ocr_column(left_items), _format_ocr_column(right_items)) if part
	)


def _detect_ocr_column_split(items: list[dict[str, float | str]], page_width: float) -> float | None:
	"""Find a broad vertical whitespace band that separates two text columns."""
	if len(items) < 8 or page_width <= 0:
		return None
	minimum_side = max(3, math.ceil(len(items) * 0.18))
	maximum_crossing = max(1, math.floor(len(items) * 0.12))
	best: tuple[float, float] | None = None

	for percent in range(25, 76, 2):
		split = page_width * percent / 100
		left = [item for item in items if float(item["right"]) <= split]
		right = [item for item in items if float(item["left"]) >= split]
		crossing = len(items) - len(left) - len(right)
		if len(left) < minimum_side or len(right) < minimum_side or crossing > maximum_crossing:
			continue
		if _vertical_span_overlap(left, right) < 0.35:
			continue
		gap = min(float(item["left"]) for item in right) - max(float(item["right"]) for item in left)
		if gap < page_width * 0.04:
			continue
		score = gap - crossing * page_width * 0.02
		if best is None or score > best[0]:
			best = (score, split)
	return best[1] if best else None


def _vertical_span_overlap(
	left: list[dict[str, float | str]],
	right: list[dict[str, float | str]],
) -> float:
	"""Return how much the two candidate columns coexist vertically."""
	left_top = min(float(item["top"]) for item in left)
	left_bottom = max(float(item["bottom"]) for item in left)
	right_top = min(float(item["top"]) for item in right)
	right_bottom = max(float(item["bottom"]) for item in right)
	overlap = max(0.0, min(left_bottom, right_bottom) - max(left_top, right_top))
	shorter_span = max(1.0, min(left_bottom - left_top, right_bottom - right_top))
	return overlap / shorter_span


def _format_ocr_column(items: list[dict[str, float | str]]) -> str:
	"""Merge OCR boxes on the same visual row and keep readable paragraph gaps."""
	if not items:
		return ""
	visual_lines: list[dict[str, Any]] = []
	for item in sorted(items, key=lambda value: (float(value["center_y"]), float(value["left"]))):
		match = next((line for line in visual_lines if _same_ocr_line(item, line)), None)
		if match is None:
			visual_lines.append({
				"items": [item],
				"top": float(item["top"]),
				"bottom": float(item["bottom"]),
				"center_y": float(item["center_y"]),
				"height": float(item["height"]),
			})
		else:
			match["items"].append(item)
			match["top"] = min(float(match["top"]), float(item["top"]))
			match["bottom"] = max(float(match["bottom"]), float(item["bottom"]))
			match["height"] = max(1.0, float(match["bottom"]) - float(match["top"]))
			match["center_y"] = (float(match["top"]) + float(match["bottom"])) / 2

	output: list[str] = []
	previous: dict[str, Any] | None = None
	for line in sorted(visual_lines, key=lambda value: (float(value["top"]), float(value["center_y"]))):
		if previous is not None:
			gap = float(line["top"]) - float(previous["bottom"])
			if gap > max(float(line["height"]), float(previous["height"])) * 1.15:
				output.append("")
		line_items = sorted(line["items"], key=lambda value: float(value["left"]))
		output.append(" ".join(str(item["text"]) for item in line_items))
		previous = line
	return "\n".join(output).strip()


def _same_ocr_line(item: dict[str, float | str], line: dict[str, Any]) -> bool:
	overlap = max(
		0.0,
		min(float(item["bottom"]), float(line["bottom"]))
		- max(float(item["top"]), float(line["top"])),
	)
	minimum_height = max(1.0, min(float(item["height"]), float(line["height"])))
	center_gap = abs(float(item["center_y"]) - float(line["center_y"]))
	return overlap / minimum_height > 0.5 or center_gap < minimum_height * 0.35


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
