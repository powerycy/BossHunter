"""Purpose-bounded candidate context assembly for AI prompts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bosshunter.candidate_profiles import ensure_profile_for_resume
from bosshunter.db import get_primary_candidate_document, list_candidate_facts


def build_candidate_context(
    config: dict[str, Any],
    *,
    purpose: str = "scoring",
    profile_id: str | None = None,
    db=None,
) -> str:
    """Build a bounded context from the primary resume and confirmed facts.

    Existing CLI configurations without a profile continue to use their local
    resume path. Pending/conflicting facts are deliberately excluded.
    """
    limit = 1500 if purpose in {"greeting", "greeting_review"} else 3000
    text = ""
    local_db = db
    should_close = False
    try:
        if local_db is not None:
            if not profile_id:
                profile = ensure_profile_for_resume(local_db, config.get("profile", {}).get("resume_path"))
                profile_id = str(profile["id"])
            document = get_primary_candidate_document(local_db, profile_id)
            if document:
                text = str(document.get("text_content") or "")
                for fact in list_candidate_facts(local_db, profile_id):
                    if fact.get("status") == "confirmed":
                        text += f"\n[{fact.get('category')}] {fact.get('content')}"
        if not text:
            path = Path(config.get("profile", {}).get("resume_path", "./resume.md"))
            if path.exists():
                text = path.read_text(encoding="utf-8")
        return _truncate(text, limit)
    except (OSError, UnicodeDecodeError):
        return ""
    finally:
        if should_close and local_db is not None:
            local_db.close()


def _truncate(value: str, limit: int) -> str:
    value = str(value or "")
    if len(value) <= limit:
        return value
    marker = "\n...[为适配模型上下文已裁剪]...\n"
    available = max(limit - len(marker), 2)
    head = max(int(available * 0.7), 1)
    return f"{value[:head]}{marker}{value[-(available - head):]}"
