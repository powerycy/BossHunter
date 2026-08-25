"""Platform-neutral cleanup for text sent to filters and AI prompts."""

from __future__ import annotations

import re


_SOURCE_NOISE = (
    re.compile(r"\[\s*岗位(?:kanzhun)?职责\s*\]", re.IGNORECASE),
    re.compile(r"来自\s*(?:BOSS\s*直聘|智联招聘|前程无忧|51job)", re.IGNORECASE),
    re.compile(r"(?:本|该)职位(?:信息)?来源(?:于|[::：])\s*(?:BOSS\s*直聘|智联招聘|前程无忧|51job)", re.IGNORECASE),
)


def clean_job_description(value: object) -> str:
    """Remove known page-source boilerplate without rewriting JD facts."""
    text = str(value or "").replace("\u00a0", " ").strip()
    for pattern in _SOURCE_NOISE:
        text = pattern.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
