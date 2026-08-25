"""Replay Resume Studio extraction and Career Profile composition in temporary storage."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from collections import Counter
from pathlib import Path

from bosshunter.ai.credentials import AIRequestError, call_anthropic_text
from bosshunter.config import load_config
from bosshunter.db import get_db
from bosshunter.resume_builder.service import (
	ResumeBuilderError,
	_star_batches,
	compose_career_profile,
	extract_source_facts,
	ingest_resume_source,
)
from bosshunter.resume_builder.store import update_fact


def _source_kind(path: Path) -> str:
	return "resume" if "简历" in path.name else "technical_document"


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("paths", nargs="+", type=Path)
	parser.add_argument("--config", type=Path, default=Path("config.yaml"))
	parser.add_argument(
		"--allow-external-ai",
		action="store_true",
		help="Explicitly allow sending material text to the configured external AI service.",
	)
	parser.add_argument(
		"--extract-only",
		action="store_true",
		help="Run every extraction but skip Career Profile composition.",
	)
	args = parser.parse_args()
	if not args.allow_external_ai:
		files = []
		for path in args.paths:
			text = path.read_text(encoding="utf-8")
			files.append({
				"file": path.name,
				"kind": _source_kind(path),
				"characters": len(text),
				"headings": sum(line.lstrip().startswith("#") for line in text.splitlines()),
				"planned_llm_calls": len(_star_batches(text)),
			})
		print(json.dumps({
			"mode": "local_batch_plan",
			"files": files,
			"planned_extraction_calls": sum(item["planned_llm_calls"] for item in files),
			"external_ai_used": False,
		}, ensure_ascii=False, indent=2))
		return 0

	config = load_config(args.config)
	purposes: Counter[str] = Counter()
	call_seconds: list[float] = []

	def measured_call(prompt: str, ai_config: dict, max_tokens: int, **kwargs):
		started = time.perf_counter()
		try:
			return call_anthropic_text(prompt, ai_config, max_tokens, **kwargs)
		finally:
			purposes[str(kwargs.get("purpose", "unknown"))] += 1
			call_seconds.append(time.perf_counter() - started)

	results: list[dict] = []
	failures: list[dict] = []
	started = time.perf_counter()
	with tempfile.TemporaryDirectory(prefix="bosshunter-profile-replay-") as temporary:
		base_dir = Path(temporary)
		conn = get_db(base_dir / "bosshunter.db")
		try:
			for path in args.paths:
				kind = _source_kind(path)
				source, _ = ingest_resume_source(
					conn,
					filename=path.name,
					content=path.read_bytes(),
					storage_dir=base_dir / "sources",
				)
				before = sum(purposes.values())
				item_started = time.perf_counter()
				try:
					facts = extract_source_facts(
						conn,
						source["id"],
						config,
						source_kind=kind,
						call_text=measured_call,
					)
				except (AIRequestError, OSError, ResumeBuilderError) as exc:
					failure = {
						"file": path.name,
						"kind": kind,
						"status": "failed",
						"error_type": type(exc).__name__,
						"error": str(exc)[:200],
						"llm_calls": sum(purposes.values()) - before,
						"elapsed_seconds": round(time.perf_counter() - item_started, 2),
					}
					failures.append(failure)
					print(json.dumps({"event": "extraction_complete", **failure}, ensure_ascii=False), flush=True)
					continue
				for fact in facts:
					update_fact(conn, fact["id"], status="accepted")
				result = {
					"file": path.name,
					"kind": kind,
					"status": "passed",
					"characters": len(source["normalized_text"]),
					"facts": len(facts),
					"llm_calls": sum(purposes.values()) - before,
					"elapsed_seconds": round(time.perf_counter() - item_started, 2),
				}
				results.append(result)
				print(json.dumps({"event": "extraction_complete", **result}, ensure_ascii=False), flush=True)

			profile = None
			profile_error = None
			if not failures and not args.extract_only:
				try:
					profile = compose_career_profile(
						conn,
						config,
						output_dir=base_dir / "profiles",
						call_text=measured_call,
					)
				except (AIRequestError, OSError, ResumeBuilderError) as exc:
					profile_error = {
						"error_type": type(exc).__name__,
						"error": str(exc)[:200],
					}
			summary = {
				"mode": "external_ai_extract_only" if args.extract_only else "external_ai_full",
				"files": results,
				"failures": failures,
				"purpose_calls": dict(purposes),
				"total_llm_calls": sum(purposes.values()),
				"total_elapsed_seconds": round(time.perf_counter() - started, 2),
				"mean_call_seconds": round(sum(call_seconds) / len(call_seconds), 2),
				"profile": {
					"fact_count": profile["fact_count"],
					"clarification_count": profile["clarification_count"],
					"evidence_coverage": profile["quality_report"]["evidence_coverage"],
					"open_clarification_count": profile["quality_report"]["open_clarification_count"],
				} if profile else None,
				"profile_error": profile_error,
				"storage": "temporary_deleted",
			}
			print(json.dumps(summary, ensure_ascii=False, indent=2))
		finally:
			conn.close()
	return 1 if failures or profile_error else 0


if __name__ == "__main__":
	raise SystemExit(main())
