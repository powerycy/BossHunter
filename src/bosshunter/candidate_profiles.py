"""Lightweight local job-search profile and fact helpers."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from bosshunter.db import (
    add_candidate_fact,
    ensure_default_profile,
    get_primary_candidate_document,
    list_candidate_documents,
    list_candidate_facts,
    list_candidate_profiles,
    upsert_candidate_document,
)


def ensure_profile_for_resume(conn, resume_path: str | Path | None) -> dict[str, Any]:
    """Create/map the default profile without requiring fact confirmation."""
    profile = ensure_default_profile(conn)
    if resume_path:
        path = Path(resume_path)
        document_id = f"resume-{hashlib.sha1(str(path).encode('utf-8')).hexdigest()[:16]}"
        text = ""
        if path.exists() and path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
        upsert_candidate_document(
            conn,
            profile_id=profile["id"],
            document_id=document_id,
            document_type="resume",
            filename=path.name or "resume",
            storage_path=str(path),
            text_content=text,
            is_primary=True,
        )
    return profile


def profile_payload(conn, profile_id: str) -> dict[str, Any] | None:
    profiles = [profile for profile in list_candidate_profiles(conn) if str(profile["id"]) == str(profile_id)]
    if not profiles:
        return None
    profile = profiles[0]
    documents = list_candidate_documents(conn, profile_id)
    facts = list_candidate_facts(conn, profile_id)
    return {
        **profile,
        "documents": [_safe_document(document) for document in documents],
        "facts": facts,
        "pending_fact_count": sum(1 for fact in facts if fact.get("status") == "pending"),
        "conflict_fact_count": sum(1 for fact in facts if fact.get("status") == "conflict"),
    }


def derive_pending_facts(
    conn,
    *,
    profile_id: str,
    document_id: str | None,
    text: str,
) -> list[dict[str, Any]]:
    """Split headings and useful bullet/paragraph lines deterministically."""
    facts: list[dict[str, Any]] = []
    category = "补充经历"
    for raw_line in str(text or "").splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw_line).strip()
        heading = re.sub(r"^\s*#+\s*", "", line).strip()
        if not line:
            continue
        if raw_line.lstrip().startswith("#"):
            category = heading[:80] or category
            continue
        if len(line) < 8:
            continue
        normalized = _normalize_fact(line)
        fact = {
            "id": f"fact-{uuid4().hex[:20]}",
            "profile_id": profile_id,
            "source_document_id": document_id,
            "category": category,
            "content": line[:1000],
            "normalized_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            "status": "pending",
        }
        try:
            facts.append(add_candidate_fact(conn, fact))
        except Exception:
            # A normalized duplicate is a no-op; facts already visible to the
            # user must not prevent a document from being uploaded.
            continue
    return facts


def _normalize_fact(value: str) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").lower(), flags=re.UNICODE)


def _safe_document(document: dict[str, Any]) -> dict[str, Any]:
    # The browser gets metadata only; local path/text stays server-side.
    return {key: document.get(key) for key in ("id", "profile_id", "document_type", "filename", "is_primary", "parse_status", "created_at", "updated_at")}
