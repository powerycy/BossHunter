"""Deterministic, database-backed selection rules for independent scoring."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable


SCORE_SCOPES = {"pending", "failed", "selected", "all_scored"}
DELIVERY_LOCKED_STATUSES = {
	"approved",
	"sent",
	"replied",
	"resume_sent",
	"needs_resume",
	"follow_up_sent",
}
SCORABLE_STATUSES = {"pending", "scored", "ready", "filtered"}
PREFILTER_PREFIXES = ("预筛不通过:", "AI评分失败:", "AI 评分失败:", "评分失败:")


def validate_options(
	scope: str = "pending",
	limit: int | None = 20,
	job_ids: Iterable[Any] | None = None,
	force_rescore: bool = False,
) -> dict[str, Any]:
	scope = str(scope or "pending").strip().lower()
	if scope not in SCORE_SCOPES:
		raise ValueError("评分范围无效")
	if limit is not None:
		if isinstance(limit, bool):
			raise ValueError("评分数量必须是正整数或全部")
		try:
			limit = int(limit)
		except (TypeError, ValueError) as exc:
			raise ValueError("评分数量必须是正整数或全部") from exc
		if limit <= 0 or limit > 10000:
			raise ValueError("评分数量必须在 1 到 10000 之间")
	ids: list[str] = []
	for value in job_ids or []:
		job_id = str(value or "").strip()
		if job_id and job_id not in ids:
			ids.append(job_id)
	if len(ids) > 5000:
		raise ValueError("一次最多选择 5000 个岗位")
	if scope == "selected" and not ids:
		raise ValueError("岗位池已选评分必须提供岗位 ID")
	return {
		"scope": scope,
		"limit": limit,
		"job_ids": ids,
		"force_rescore": bool(force_rescore),
	}


def _is_ai_scored(job: dict[str, Any]) -> bool:
	status = str(job.get("status") or "")
	reason = str(job.get("score_reason") or "").strip()
	if status in {"scored", "ready"}:
		return True
	if status == "filtered" and reason and not reason.startswith(PREFILTER_PREFIXES):
		return True
	return False


def _is_retryable_failure(job: dict[str, Any]) -> bool:
	if job.get("score_failure_json"):
		return True
	return str(job.get("score_reason") or "").startswith(("AI评分失败:", "AI 评分失败:", "评分失败:"))


def _query_jobs(conn: sqlite3.Connection, options: dict[str, Any]) -> list[dict[str, Any]]:
	scope = options["scope"]
	ids = options["job_ids"]
	where = ["status NOT IN ({})".format(",".join("?" for _ in DELIVERY_LOCKED_STATUSES))]
	params: list[Any] = list(sorted(DELIVERY_LOCKED_STATUSES))
	if scope in {"pending", "failed"}:
		where.append("status = ?")
		params.append("pending")
	elif scope == "all_scored":
		where.append("status IN ('scored', 'ready', 'filtered')")
	elif scope == "selected":
		if not ids:
			return []
		where.append("id IN ({})".format(",".join("?" for _ in ids)))
		params.extend(ids)
	query = "SELECT * FROM jobs WHERE " + " AND ".join(where) + " ORDER BY score DESC, created_at DESC"
	rows = [dict(row) for row in conn.execute(query, params).fetchall()]
	if scope == "failed":
		rows = [job for job in rows if _is_retryable_failure(job)]
	elif scope == "pending":
		rows = [job for job in rows if not _is_ai_scored(job)]
	elif scope == "selected" and not options["force_rescore"]:
		rows = [job for job in rows if not _is_ai_scored(job)]
	elif scope == "all_scored":
		rows = [job for job in rows if _is_ai_scored(job)]
	if options["limit"] is not None:
		rows = rows[: options["limit"]]
	return rows


def select_scoring_jobs(conn: sqlite3.Connection, **kwargs: Any) -> list[dict[str, Any]]:
	options = validate_options(**kwargs)
	return _query_jobs(conn, options)


def preview_scoring(
	conn: sqlite3.Connection,
	*,
	scope: str = "pending",
	limit: int | None = 20,
	job_ids: Iterable[Any] | None = None,
	force_rescore: bool = False,
	max_attempts_per_job: int = 2,
) -> dict[str, Any]:
	options = validate_options(scope, limit, job_ids, force_rescore)
	selected = _query_jobs(conn, options)
	eligible_ids = {str(job["id"]) for job in selected}
	if options["scope"] == "selected":
		requested = set(options["job_ids"])
		skipped = max(len(requested - eligible_ids), 0)
	else:
		where = ["status NOT IN ({})".format(",".join("?" for _ in DELIVERY_LOCKED_STATUSES))]
		params: list[Any] = list(sorted(DELIVERY_LOCKED_STATUSES))
		row = conn.execute("SELECT COUNT(*) AS cnt FROM jobs WHERE " + " AND ".join(where), params).fetchone()
		skipped = max(int(row["cnt"] or 0) - len(selected), 0)
	try:
		attempts = max(1, min(int(max_attempts_per_job or 2), 3))
	except (TypeError, ValueError):
		attempts = 2
	return {
		"scope": options["scope"],
		"eligible_jobs": len(selected),
		"skipped_jobs": skipped,
		"first_attempt_requests": len(selected),
		"max_attempts_per_job": attempts,
		"max_possible_requests": len(selected) * attempts,
		"job_ids": [str(job["id"]) for job in selected],
		"note": "关键词预筛可能减少实际 AI 请求数；已进入投递链路的岗位不会重评。",
	}


def serialize_score_failure(kind: str, stage: str, message: str, *, retryable: bool = True, status_code: int | None = None) -> str:
	payload = {
		"schema": "bosshunter.score_failure.v1",
		"kind": str(kind or "request_failed")[:64],
		"stage": str(stage or "score")[:64],
		"message": str(message or "AI 评分失败")[:240],
		"status_code": status_code if isinstance(status_code, int) else None,
		"retryable": bool(retryable),
	}
	return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
