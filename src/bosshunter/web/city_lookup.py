"""Platform city catalog lookup for custom search locations."""

from collections.abc import Iterable
from typing import Any

import httpx

CITY_CATALOG_URL = "https://www.zhipin.com/wapi/zpCommon/data/city.json"


class CityLookupError(RuntimeError):
    """Raised when a platform city cannot be resolved safely."""


def _normalize_name(value: object) -> str:
    return "".join(str(value or "").split()).casefold()


def _iter_city_entries(value: object) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if value.get("name") and value.get("code") is not None:
            yield value
        for child in value.values():
            yield from _iter_city_entries(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_city_entries(child)


def lookup_city(name: str) -> dict[str, str]:
    """Resolve a city name through the platform's published city catalog."""
    normalized_name = _normalize_name(name)
    if not normalized_name:
        raise CityLookupError("城市名称不能为空")

    try:
        response = httpx.get(
            CITY_CATALOG_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("city catalog payload must be an object")
        entries = _iter_city_entries(payload.get("zpData", {}))
        for entry in entries:
            if _normalize_name(entry.get("name")) == normalized_name:
                return {"name": str(entry["name"]).strip(), "code": str(entry["code"])}
    except httpx.TimeoutException as exc:
        raise CityLookupError("城市目录查询超时，请稍后重试") from exc
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        raise CityLookupError("城市目录查询失败，请检查网络后重试") from exc

    raise CityLookupError(f"未找到城市“{name.strip()}”，请确认名称")
