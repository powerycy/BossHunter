"""SQLite repository for Resume Studio sources, facts, and versions."""

from __future__ import annotations

import sqlite3
from typing import Any
from uuid import uuid4

FACT_STATUSES = {"pending", "accepted", "rejected"}


def _record(row: sqlite3.Row | None) -> dict[str, Any] | None:
	return dict(row) if row is not None else None


def get_source(conn: sqlite3.Connection, source_id: str) -> dict[str, Any] | None:
	return _record(conn.execute("SELECT * FROM resume_sources WHERE id = ?", (source_id,)).fetchone())


def get_source_by_hash(conn: sqlite3.Connection, content_hash: str) -> dict[str, Any] | None:
	return _record(conn.execute("SELECT * FROM resume_sources WHERE content_hash = ?", (content_hash,)).fetchone())


def create_source(
	conn: sqlite3.Connection,
	*,
	filename: str,
	source_type: str,
	stored_path: str,
	content_hash: str,
	normalized_text: str,
	source_id: str | None = None,
) -> dict[str, Any]:
	source_id = source_id or uuid4().hex
	conn.execute(
		"""
		INSERT INTO resume_sources
			(id, filename, source_type, stored_path, content_hash, normalized_text)
		VALUES (?, ?, ?, ?, ?, ?)
		""",
		(source_id, filename, source_type, stored_path, content_hash, normalized_text),
	)
	conn.commit()
	return get_source(conn, source_id) or {}


def set_source_status(conn: sqlite3.Connection, source_id: str, status: str, error: str | None = None) -> None:
	conn.execute(
		"UPDATE resume_sources SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
		(status, error, source_id),
	)
	conn.commit()


def list_sources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
	rows = conn.execute(
		"""
		SELECT s.id, s.filename, s.source_type, s.stored_path, s.content_hash, s.status, s.error,
		       s.created_at, s.updated_at,
		       COUNT(f.id) AS fact_count,
		       SUM(CASE WHEN f.status = 'accepted' THEN 1 ELSE 0 END) AS accepted_count,
		       SUM(CASE WHEN f.status = 'pending' THEN 1 ELSE 0 END) AS pending_count
		FROM resume_sources s
		LEFT JOIN resume_facts f ON f.source_id = s.id
		GROUP BY s.id
		ORDER BY s.created_at DESC, s.id DESC
		"""
	).fetchall()
	return [dict(row) for row in rows]


def replace_fact_candidates(
	conn: sqlite3.Connection,
	source_id: str,
	facts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
	accepted: set[str] = set()
	for row in conn.execute(
			"SELECT content, edited_content FROM resume_facts WHERE source_id = ? AND status = 'accepted'",
			(source_id,),
		).fetchall():
		accepted.add(str(row["content"]).strip().casefold())
		if row["edited_content"]:
			accepted.add(str(row["edited_content"]).strip().casefold())
	conn.execute("DELETE FROM resume_facts WHERE source_id = ? AND status != 'accepted'", (source_id,))
	for fact in facts:
		content = str(fact["content"]).strip()
		if not content or content.casefold() in accepted:
			continue
		conn.execute(
			"""
			INSERT INTO resume_facts
				(id, source_id, category, content, evidence, confidence, status)
			VALUES (?, ?, ?, ?, ?, ?, 'pending')
			""",
			(
				uuid4().hex,
				source_id,
				fact["category"],
				content,
				fact["evidence"],
				float(fact.get("confidence", 0)),
			),
		)
	conn.commit()
	return list_facts(conn, source_id=source_id)


def list_facts(
	conn: sqlite3.Connection,
	*,
	source_id: str | None = None,
	status: str | None = None,
) -> list[dict[str, Any]]:
	where: list[str] = []
	params: list[Any] = []
	if source_id:
		where.append("f.source_id = ?")
		params.append(source_id)
	if status:
		where.append("f.status = ?")
		params.append(status)
	clause = f"WHERE {' AND '.join(where)}" if where else ""
	rows = conn.execute(
		f"""
		SELECT f.*, s.filename AS source_filename,
		       COALESCE(f.edited_content, f.content) AS effective_content
		FROM resume_facts f
		JOIN resume_sources s ON s.id = f.source_id
		{clause}
		ORDER BY f.created_at, f.id
		""",
		params,
	).fetchall()
	return [dict(row) for row in rows]


def update_fact(
	conn: sqlite3.Connection,
	fact_id: str,
	*,
	status: str,
	edited_content: str | None = None,
) -> dict[str, Any] | None:
	if status not in FACT_STATUSES:
		raise ValueError("无效的事实审核状态")
	row = conn.execute("SELECT * FROM resume_facts WHERE id = ?", (fact_id,)).fetchone()
	if row is None:
		return None
	cleaned = edited_content.strip() if isinstance(edited_content, str) else None
	if cleaned == "":
		cleaned = None
	if cleaned == row["content"]:
		cleaned = None
	if status == "accepted" and not (cleaned or str(row["content"]).strip()):
		raise ValueError("已接受的事实内容不能为空")
	conn.execute(
		"""
		UPDATE resume_facts
		SET status = ?, edited_content = ?, updated_at = CURRENT_TIMESTAMP
		WHERE id = ?
		""",
		(status, cleaned, fact_id),
	)
	conn.commit()
	return _record(
		conn.execute(
			"""
			SELECT f.*, s.filename AS source_filename,
			       COALESCE(f.edited_content, f.content) AS effective_content
			FROM resume_facts f JOIN resume_sources s ON s.id = f.source_id
			WHERE f.id = ?
			""",
			(fact_id,),
		).fetchone()
	)


def source_version_references(conn: sqlite3.Connection, source_id: str) -> int:
	row = conn.execute(
		"""
		SELECT COUNT(*) AS count
		FROM resume_version_facts vf
		JOIN resume_facts f ON f.id = vf.fact_id
		WHERE f.source_id = ?
		""",
		(source_id,),
	).fetchone()
	return int(row["count"] if row else 0)


def delete_source_records(conn: sqlite3.Connection, source_id: str) -> None:
	conn.execute("DELETE FROM resume_facts WHERE source_id = ?", (source_id,))
	conn.execute("DELETE FROM resume_sources WHERE id = ?", (source_id,))
	conn.commit()


def create_version(
	conn: sqlite3.Connection,
	*,
	name: str,
	target_role: str,
	markdown: str,
	file_path: str,
	fact_ids: list[str],
	version_id: str | None = None,
) -> dict[str, Any]:
	version_id = version_id or uuid4().hex
	conn.execute(
		"""
		INSERT INTO resume_versions (id, name, target_role, markdown, file_path)
		VALUES (?, ?, ?, ?, ?)
		""",
		(version_id, name, target_role, markdown, file_path),
	)
	for fact_id in dict.fromkeys(fact_ids):
		conn.execute(
			"INSERT INTO resume_version_facts (version_id, fact_id) VALUES (?, ?)",
			(version_id, fact_id),
		)
	conn.commit()
	return get_version(conn, version_id) or {}


def get_version(conn: sqlite3.Connection, version_id: str) -> dict[str, Any] | None:
	return _record(conn.execute("SELECT * FROM resume_versions WHERE id = ?", (version_id,)).fetchone())


def list_versions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
	rows = conn.execute(
		"""
		SELECT v.*, COUNT(vf.fact_id) AS fact_count
		FROM resume_versions v
		LEFT JOIN resume_version_facts vf ON vf.version_id = v.id
		GROUP BY v.id
		ORDER BY v.created_at DESC, v.id DESC
		"""
	).fetchall()
	return [dict(row) for row in rows]


def mark_version_active(conn: sqlite3.Connection, version_id: str) -> dict[str, Any] | None:
	if get_version(conn, version_id) is None:
		return None
	conn.execute("UPDATE resume_versions SET status = 'draft', activated_at = NULL WHERE status = 'active'")
	conn.execute(
		"""
		UPDATE resume_versions
		SET status = 'active', activated_at = CURRENT_TIMESTAMP
		WHERE id = ?
		""",
		(version_id,),
	)
	conn.commit()
	return get_version(conn, version_id)
