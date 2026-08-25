"""SQLite repository for Resume Studio sources, facts, and versions."""

from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import uuid4

FACT_STATUSES = {"pending", "accepted", "rejected"}
CLARIFICATION_STATUSES = {"open", "answered", "dismissed"}


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


def set_source_classification(
	conn: sqlite3.Connection,
	source_id: str,
	*,
	detected_kind: str,
	confidence: float,
	evidence: str | None,
	selected_kind: str | None = None,
) -> None:
	conn.execute(
		"""
		UPDATE resume_sources
		SET detected_kind = ?, detected_kind_confidence = ?, detected_kind_evidence = ?,
		    selected_kind = ?, updated_at = CURRENT_TIMESTAMP
		WHERE id = ?
		""",
		(detected_kind, confidence, evidence, selected_kind, source_id),
	)
	conn.commit()


def list_sources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
	rows = conn.execute(
		"""
		SELECT s.id, s.filename, s.source_type, s.stored_path, s.content_hash,
		       s.detected_kind, s.detected_kind_confidence, s.detected_kind_evidence, s.selected_kind,
		       s.status, s.error,
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
	accepted_keys: set[tuple[str, str, str, str, str]] = set()
	accepted_legacy_content: set[str] = set()
	for row in conn.execute(
		"""
		SELECT fact_type, entity_type, field_name, group_id, content, edited_content
		FROM resume_facts WHERE source_id = ? AND status = 'accepted'
		""",
		(source_id,),
	).fetchall():
		fact_type = str(row["fact_type"] or "legacy")
		if fact_type == "legacy":
			accepted_legacy_content.add(str(row["content"]).strip().casefold())
			if row["edited_content"]:
				accepted_legacy_content.add(str(row["edited_content"]).strip().casefold())
			continue
		base_key = (
			fact_type,
			str(row["entity_type"] or ""),
			str(row["field_name"] or ""),
			str(row["group_id"] or ""),
		)
		accepted_keys.add((*base_key, str(row["content"]).strip().casefold()))
		if row["edited_content"]:
			accepted_keys.add((*base_key, str(row["edited_content"]).strip().casefold()))
	conn.execute(
		"""
		DELETE FROM resume_fact_evidence
		WHERE fact_id IN (
			SELECT id FROM resume_facts WHERE source_id = ? AND status != 'accepted'
		)
		""",
		(source_id,),
	)
	conn.execute("DELETE FROM resume_facts WHERE source_id = ? AND status != 'accepted'", (source_id,))
	for fact in facts:
		content = str(fact["content"]).strip()
		fact_type = str(fact.get("fact_type", "legacy"))
		key = (
			fact_type,
			str(fact.get("entity_type") or ""),
			str(fact.get("field_name") or ""),
			str(fact.get("group_id") or ""),
			content.casefold(),
		)
		if not content or key in accepted_keys or (fact_type == "legacy" and content.casefold() in accepted_legacy_content):
			continue
		fact_id = uuid4().hex
		conn.execute(
			"""
			INSERT INTO resume_facts
				(id, source_id, category, content, evidence, confidence,
				 fact_type, entity_type, field_name, group_id, structured_data,
				 completeness, needs_clarification, status)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
			""",
			(
				fact_id,
				source_id,
				fact["category"],
				content,
				fact["evidence"],
				float(fact.get("confidence", 0)),
				str(fact.get("fact_type", "legacy")),
				fact.get("entity_type"),
				fact.get("field_name"),
				fact.get("group_id"),
				json.dumps(fact.get("structured_data"), ensure_ascii=False)
				if fact.get("structured_data") is not None
				else None,
				float(fact.get("completeness", 1)),
				1 if fact.get("needs_clarification") else 0,
			),
		)
		for evidence_item in fact.get("evidence_items", []):
			conn.execute(
				"""
				INSERT INTO resume_fact_evidence
					(fact_id, component, quote, start_offset, end_offset)
				VALUES (?, ?, ?, ?, ?)
				""",
				(
					fact_id,
					str(evidence_item.get("component", "content")),
					str(evidence_item["quote"]),
					evidence_item.get("start_offset"),
					evidence_item.get("end_offset"),
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
	records = [dict(row) for row in rows]
	if not records:
		return records

	by_id = {record["id"]: record for record in records}
	placeholders = ", ".join("?" for _ in records)
	evidence_rows = conn.execute(
		f"""
		SELECT fact_id, component, quote, start_offset, end_offset
		FROM resume_fact_evidence
		WHERE fact_id IN ({placeholders})
		ORDER BY id
		""",
		list(by_id),
	).fetchall()
	for record in records:
		raw = record.get("structured_data")
		if raw:
			try:
				record["structured_data"] = json.loads(raw)
			except (TypeError, json.JSONDecodeError):
				record["structured_data"] = None
		record["needs_clarification"] = bool(record.get("needs_clarification"))
		record["evidence_items"] = []
	for row in evidence_rows:
		by_id[row["fact_id"]]["evidence_items"].append(dict(row))
	return records


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
		SELECT
			(SELECT COUNT(*)
			 FROM resume_version_facts vf
			 JOIN resume_facts f ON f.id = vf.fact_id
			 WHERE f.source_id = ?)
			+
			(SELECT COUNT(*)
			 FROM resume_profile_facts pf
			 JOIN resume_facts f ON f.id = pf.fact_id
			 WHERE f.source_id = ?) AS count
		""",
		(source_id, source_id),
	).fetchone()
	return int(row["count"] if row else 0)


def delete_source_records(conn: sqlite3.Connection, source_id: str) -> None:
	conn.execute(
		"DELETE FROM resume_clarifications WHERE fact_id IN (SELECT id FROM resume_facts WHERE source_id = ?)",
		(source_id,),
	)
	conn.execute(
		"DELETE FROM resume_fact_evidence WHERE fact_id IN (SELECT id FROM resume_facts WHERE source_id = ?)",
		(source_id,),
	)
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


def list_clarifications(
	conn: sqlite3.Connection,
	*,
	status: str | None = None,
) -> list[dict[str, Any]]:
	where = "WHERE c.status = ?" if status else ""
	params: tuple[Any, ...] = (status,) if status else ()
	rows = conn.execute(
		f"""
		SELECT c.*, f.category, f.fact_type, f.entity_type, f.field_name,
		       f.group_id, COALESCE(f.edited_content, f.content) AS fact_content,
		       s.filename AS source_filename
		FROM resume_clarifications c
		LEFT JOIN resume_facts f ON f.id = c.fact_id
		LEFT JOIN resume_sources s ON s.id = f.source_id
		{where}
		ORDER BY c.priority DESC, c.created_at, c.id
		""",
		params,
	).fetchall()
	records = [dict(row) for row in rows]
	for record in records:
		raw = record.pop("metadata_json", None)
		try:
			record["metadata"] = json.loads(raw) if raw else {}
		except (TypeError, json.JSONDecodeError):
			record["metadata"] = {}
	return records


def replace_open_clarifications(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
	keys = {str(item["dedupe_key"]) for item in items}
	if keys:
		placeholders = ", ".join("?" for _ in keys)
		conn.execute(
			f"DELETE FROM resume_clarifications WHERE status = 'open' AND dedupe_key NOT IN ({placeholders})",
			list(keys),
		)
	else:
		conn.execute("DELETE FROM resume_clarifications WHERE status = 'open'")
	for item in items:
		conn.execute(
			"""
			INSERT INTO resume_clarifications
				(id, fact_id, dedupe_key, kind, question, priority, metadata_json)
			VALUES (?, ?, ?, ?, ?, ?, ?)
			ON CONFLICT(dedupe_key) DO UPDATE SET
				fact_id = excluded.fact_id,
				kind = excluded.kind,
				question = excluded.question,
				priority = excluded.priority,
				metadata_json = excluded.metadata_json,
				updated_at = CURRENT_TIMESTAMP
			WHERE resume_clarifications.status = 'open'
			""",
			(
				uuid4().hex,
				item.get("fact_id"),
				item["dedupe_key"],
				item["kind"],
				item["question"],
				int(item.get("priority", 0)),
				json.dumps(item.get("metadata", {}), ensure_ascii=False),
			),
		)
	conn.commit()
	return list_clarifications(conn)


def update_clarification(
	conn: sqlite3.Connection,
	clarification_id: str,
	*,
	status: str,
	answer: str | None = None,
) -> dict[str, Any] | None:
	if status not in CLARIFICATION_STATUSES:
		raise ValueError("无效的补充确认状态")
	row = conn.execute("SELECT id FROM resume_clarifications WHERE id = ?", (clarification_id,)).fetchone()
	if row is None:
		return None
	cleaned = answer.strip() if isinstance(answer, str) else None
	if status == "answered" and not cleaned:
		raise ValueError("确认回答不能为空")
	if status == "open":
		cleaned = None
	conn.execute(
		"""
		UPDATE resume_clarifications
		SET status = ?, answer = ?, answered_at = CASE WHEN ? = 'answered' THEN CURRENT_TIMESTAMP ELSE NULL END,
		    updated_at = CURRENT_TIMESTAMP
		WHERE id = ?
		""",
		(status, cleaned, status, clarification_id),
	)
	conn.commit()
	return next(
		(item for item in list_clarifications(conn) if item["id"] == clarification_id),
		None,
	)


def create_profile_version(
	conn: sqlite3.Connection,
	*,
	name: str,
	profile: dict[str, Any],
	markdown: str,
	quality_report: dict[str, Any],
	json_path: str,
	markdown_path: str,
	fact_ids: list[str],
	clarification_ids: list[str],
	profile_id: str | None = None,
) -> dict[str, Any]:
	profile_id = profile_id or uuid4().hex
	conn.execute(
		"""
		INSERT INTO resume_profile_versions
			(id, name, profile_json, markdown, quality_report, json_path, markdown_path)
		VALUES (?, ?, ?, ?, ?, ?, ?)
		""",
		(
			profile_id,
			name,
			json.dumps(profile, ensure_ascii=False),
			markdown,
			json.dumps(quality_report, ensure_ascii=False),
			json_path,
			markdown_path,
		),
	)
	for fact_id in dict.fromkeys(fact_ids):
		conn.execute(
			"INSERT INTO resume_profile_facts (profile_id, fact_id) VALUES (?, ?)",
			(profile_id, fact_id),
		)
	for clarification_id in dict.fromkeys(clarification_ids):
		conn.execute(
			"INSERT INTO resume_profile_clarifications (profile_id, clarification_id) VALUES (?, ?)",
			(profile_id, clarification_id),
		)
	conn.commit()
	return get_profile_version(conn, profile_id) or {}


def get_profile_version(conn: sqlite3.Connection, profile_id: str) -> dict[str, Any] | None:
	row = conn.execute(
		"""
		SELECT p.*,
		       (SELECT COUNT(*) FROM resume_profile_facts pf WHERE pf.profile_id = p.id) AS fact_count,
		       (SELECT COUNT(*) FROM resume_profile_clarifications pc WHERE pc.profile_id = p.id)
		           AS clarification_count
		FROM resume_profile_versions p WHERE p.id = ?
		""",
		(profile_id,),
	).fetchone()
	record = _record(row)
	if record:
		for key in ("profile_json", "quality_report"):
			raw = record.get(key)
			try:
				record[key] = json.loads(raw) if raw else {}
			except (TypeError, json.JSONDecodeError):
				record[key] = {}
	return record


def list_profile_versions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
	rows = conn.execute(
		"""
		SELECT p.id FROM resume_profile_versions p
		ORDER BY p.created_at DESC, p.id DESC
		"""
	).fetchall()
	return [
		profile
		for row in rows
		if (profile := get_profile_version(conn, row["id"])) is not None
	]


def mark_profile_version_active(conn: sqlite3.Connection, profile_id: str) -> dict[str, Any] | None:
	if get_profile_version(conn, profile_id) is None:
		return None
	conn.execute("UPDATE resume_profile_versions SET status = 'draft', activated_at = NULL WHERE status = 'active'")
	conn.execute(
		"""
		UPDATE resume_profile_versions
		SET status = 'active', activated_at = CURRENT_TIMESTAMP
		WHERE id = ?
		""",
		(profile_id,),
	)
	conn.commit()
	return get_profile_version(conn, profile_id)


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


def clear_resume_studio_records(conn: sqlite3.Connection) -> dict[str, int]:
	"""Delete only Resume Studio records and return the deleted row counts."""
	tables = (
		"resume_profile_clarifications",
		"resume_profile_facts",
		"resume_profile_versions",
		"resume_version_facts",
		"resume_versions",
		"resume_clarifications",
		"resume_fact_evidence",
		"resume_facts",
		"resume_sources",
	)
	counts = {
		table: int(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0])
		for table in tables
	}
	try:
		for table in tables:
			conn.execute("DELETE FROM " + table)
		conn.commit()
	except Exception:
		conn.rollback()
		raise
	return counts
