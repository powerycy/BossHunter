"""Offline-first BOSS city snapshot, lookup and explicit refresh helpers."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx


CITY_SOURCE_URL = "https://www.zhipin.com/wapi/zpCommon/data/cityGroup.json"
_BUNDLED_SNAPSHOT = Path(__file__).parent / "data" / "boss_cities.json"
_REQUIRED_CITY_CODES = {
	"北京": "101010100",
	"上海": "101020100",
	"广州": "101280100",
	"深圳": "101280600",
}


class CityRefreshError(RuntimeError):
	"""Raised when an explicitly requested city refresh is unsafe to apply."""


def _city_record(item: Any, first_char: str = "") -> dict[str, Any] | None:
	if not isinstance(item, dict):
		return None
	name = str(item.get("name") or "").strip()
	code = str(item.get("code") or "").strip()
	if not name or not code or not code.isdigit() or not 8 <= len(code) <= 10:
		return None
	return {
		"name": name,
		"code": code,
		"first_char": str(item.get("first_char") or item.get("firstChar") or first_char).upper(),
		"hot": bool(item.get("hot")),
	}


def _flatten_city_groups(payload: Any) -> list[dict[str, Any]]:
	if not isinstance(payload, dict):
		return []
	groups = payload.get("zpData", {}).get("cityGroup") if isinstance(payload.get("zpData"), dict) else None
	if not isinstance(groups, list):
		return []
	result: list[dict[str, Any]] = []
	for group in groups:
		if not isinstance(group, dict):
			continue
		first_char = str(group.get("firstChar") or "").upper()
		for item in group.get("cityList") or []:
			record = _city_record(item, first_char)
			if record:
				result.append(record)
	return result


def _snapshot_records(payload: Any) -> list[dict[str, Any]]:
	if not isinstance(payload, dict):
		return []
	if isinstance(payload.get("cities"), list):
		return [record for item in payload["cities"] if (record := _city_record(item))]
	return _flatten_city_groups(payload)


def validate_city_payload(payload: Any, *, min_count: int = 300) -> dict[str, Any]:
	"""Validate and normalize a BOSS cityGroup response.

	The returned value is a package snapshot and is safe to write only when no
	``ValueError`` is raised.  HTML, partial JSON and duplicate codes are rejected.
	"""
	if not isinstance(payload, dict) or payload.get("code") != 0:
		raise ValueError("城市接口返回不是成功 JSON")
	records = _flatten_city_groups(payload)
	if len(records) < min_count:
		raise ValueError(f"城市接口返回数量不足（至少需要 {min_count} 个）")
	return _validate_records(records)


def _validate_records(records: list[dict[str, Any]], *, min_count: int = 0) -> dict[str, Any]:
	if min_count and len(records) < min_count:
		raise ValueError(f"城市快照数量不足（至少需要 {min_count} 个）")
	by_code: dict[str, dict[str, Any]] = {}
	for record in records:
		normalized = _city_record(record)
		if not normalized:
			raise ValueError("城市快照包含空名称或无效编码")
		if normalized["code"] in by_code:
			raise ValueError("城市编码重复")
		by_code[normalized["code"]] = normalized
	for name, code in _REQUIRED_CITY_CODES.items():
		if not any(item["name"] == name and item["code"] == code for item in by_code.values()):
			raise ValueError(f"城市快照缺少{ name }或编码不正确")
	ordered = sorted(by_code.values(), key=lambda item: (not item["hot"], item["first_char"], item["name"]))
	return {
		"schema": "bosshunter.cities.v1",
		"source_url": CITY_SOURCE_URL,
		"fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
		"cities": ordered,
	}


def validate_city_snapshot(payload: Any) -> dict[str, Any]:
	"""Validate a bundled/cache snapshot with the same safety constraints."""
	if not isinstance(payload, dict) or payload.get("schema") != "bosshunter.cities.v1":
		raise ValueError("城市快照 schema 无效")
	records = _snapshot_records(payload)
	return _validate_records(records, min_count=300)


def _read_snapshot(path: Path, *, require_full: bool = True) -> dict[str, Any] | None:
	try:
		payload = json.loads(path.read_text(encoding="utf-8"))
		if require_full:
			return validate_city_snapshot(payload)
		return _validate_records(_snapshot_records(payload))
	except (OSError, ValueError, TypeError, json.JSONDecodeError):
		return None


def _cache_path(base_dir: Path | str | None = None, cache_path: Path | str | None = None) -> Path:
	if cache_path is not None:
		return Path(cache_path)
	return (Path(base_dir) if base_dir is not None else Path.cwd()) / "data" / "cities.cache.json"


def load_city_snapshot(
	base_dir: Path | str | None = None,
	*,
	cache_path: Path | str | None = None,
) -> dict[str, Any]:
	"""Load a valid user cache, otherwise the bundled snapshot, without network."""
	cache = _read_snapshot(_cache_path(base_dir, cache_path))
	if cache:
		cache["source"] = "cache"
		return cache
	bundled = _read_snapshot(_BUNDLED_SNAPSHOT)
	if not bundled:
		raise RuntimeError("内置城市快照不可用")
	bundled["source"] = "bundled"
	return bundled


def load_cities(
	base_dir: Path | str | None = None,
	*,
	cache_path: Path | str | None = None,
) -> list[dict[str, Any]]:
	return list(load_city_snapshot(base_dir, cache_path=cache_path)["cities"])


def get_city_map(
	base_dir: Path | str | None = None,
	*,
	cache_path: Path | str | None = None,
) -> dict[str, str]:
	return {str(city["name"]): str(city["code"]) for city in load_cities(base_dir, cache_path=cache_path)}


def get_city_code(
	name: str,
	base_dir: Path | str | None = None,
	*,
	cache_path: Path | str | None = None,
) -> str | None:
	needle = str(name or "").strip()
	if not needle:
		return None
	return get_city_map(base_dir, cache_path=cache_path).get(needle)


def _response_json(response: Any) -> Any:
	if getattr(response, "status_code", None) != 200:
		raise CityRefreshError(f"城市接口返回异常状态 {getattr(response, 'status_code', 'unknown')}")
	content_type = str(getattr(response, "headers", {}).get("content-type", "")).lower()
	text = str(getattr(response, "text", "") or "")
	if "html" in content_type or text.lstrip().lower().startswith(("<!doctype", "<html")):
		raise CityRefreshError("城市接口返回 HTML，继续使用本地城市列表")
	try:
		payload = response.json()
	except Exception as exc:
		raise CityRefreshError("城市接口返回不是合法 JSON，继续使用本地城市列表") from exc
	return payload


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	temporary_path: str | None = None
	try:
		with tempfile.NamedTemporaryFile(
			"w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
		) as handle:
			temporary_path = handle.name
			json.dump(payload, handle, ensure_ascii=False, indent=2)
			handle.flush()
			os.fsync(handle.fileno())
		os.replace(temporary_path, path)
		temporary_path = None
	finally:
		if temporary_path:
			try:
				Path(temporary_path).unlink()
			except OSError:
				pass


def refresh_city_cache(
	cache_path: Path | str | None = None,
	*,
	fetcher: Callable[..., Any] | None = None,
	timeout: float = 10.0,
) -> dict[str, Any]:
	"""Explicitly fetch, validate and atomically replace the local city cache."""
	path = _cache_path(cache_path=cache_path)
	fetch = fetcher or httpx.get
	try:
		response = fetch(CITY_SOURCE_URL, timeout=timeout, follow_redirects=True)
		payload = _response_json(response)
		snapshot = validate_city_payload(payload)
	except CityRefreshError:
		raise
	except (httpx.HTTPError, OSError, ValueError, TypeError) as exc:
		raise CityRefreshError("城市列表刷新失败，继续使用本地城市列表") from exc
	_atomic_write(path, snapshot)
	snapshot["source"] = "cache"
	return snapshot
