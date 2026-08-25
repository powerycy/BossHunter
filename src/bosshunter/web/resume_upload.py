"""Backward-compatible imports for resume document conversion."""

from bosshunter.resume_builder.documents import (
	MAX_DOCX_XML_SIZE,
	SUPPORTED_RESUME_EXTENSIONS,
	ResumeUploadError,
	docx_to_markdown,
	pdf_to_markdown,
	prepare_resume_content,
	safe_resume_filename,
)

__all__ = [
	"MAX_DOCX_XML_SIZE",
	"SUPPORTED_RESUME_EXTENSIONS",
	"ResumeUploadError",
	"docx_to_markdown",
	"pdf_to_markdown",
	"prepare_resume_content",
	"safe_resume_filename",
]
