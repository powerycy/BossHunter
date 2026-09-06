"""HTTP client for the built-in BossHunter Browser Runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from bosshunter.browser.runtime import get_runtime_url


class RuntimeClient:
    """Small typed wrapper around Browser Runtime HTTP endpoints."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.base_url = get_runtime_url(config)

    def health(self) -> dict[str, Any] | None:
        return self._get_json("/health", timeout=3)

    def targets(self) -> list[dict[str, Any]]:
        data = self._get_json("/targets", timeout=5)
        return data if isinstance(data, list) else []

    def new_tab(self, url: str, background: bool = False) -> str | None:
        params: dict[str, str] = {"url": url}
        if background:
            params["background"] = "1"
        data = self._get_json("/new", params=params, timeout=30)
        return data.get("targetId") if isinstance(data, dict) else None

    def close_tab(self, target_id: str) -> bool:
        return self._get_ok("/close", params={"target": target_id}, timeout=5)

    def navigate(self, target_id: str, url: str) -> bool:
        return self._get_ok("/navigate", params={"target": target_id, "url": url}, timeout=15)

    def back(self, target_id: str) -> bool:
        return self._get_ok("/back", params={"target": target_id}, timeout=10)

    def evaluate(self, target_id: str, expression: str, timeout: float = 30) -> Any:
        try:
            response = httpx.post(
                f"{self.base_url}/eval",
                params={"target": target_id},
                content=expression,
                timeout=timeout,
                trust_env=False,
            )
            if response.status_code == 200:
                if not response.content:
                    return None
                data = response.json()
                return data.get("value") if isinstance(data, dict) else None
            try:
                return response.json()
            except ValueError:
                return None
        except httpx.HTTPError:
            return None

    def click(self, target_id: str, selector: str) -> bool:
        return self._post_ok("/click", params={"target": target_id}, content=selector, timeout=10)

    def click_at(self, target_id: str, selector_or_xy: str) -> bool:
        return self._post_ok("/clickAt", params={"target": target_id}, content=selector_or_xy, timeout=10)

    def type_text(self, target_id: str, text: str, human: bool = False) -> bool:
        params: dict[str, Any] = {"target": target_id}
        if human:
            params["human"] = "1"
        return self._post_ok("/type", params=params, content=text, timeout=30 if human else 10)

    def press_key(self, target_id: str, key: str) -> bool:
        return self._post_ok("/key", params={"target": target_id}, content=key, timeout=10)

    def set_files(self, target_id: str, selector: str, files: list[str]) -> bool:
        try:
            response = httpx.post(
                f"{self.base_url}/setFiles",
                params={"target": target_id},
                json={"selector": selector, "files": files},
                timeout=10,
                trust_env=False,
            )
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def scroll(self, target_id: str, y: int = 0, direction: str = "") -> bool:
        params: dict[str, Any] = {"target": target_id}
        if direction:
            params["direction"] = direction
        else:
            params["y"] = y
        return self._get_ok("/scroll", params=params, timeout=5)

    def screenshot(self, target_id: str, file_path: str | Path, *, selector: str = "") -> bool:
        params = {"target": target_id, "file": str(file_path)}
        if selector:
            params["selector"] = selector
        return self._get_ok("/screenshot", params=params, timeout=15)

    def print_pdf(self, target_id: str, file_path: str | Path) -> bool:
        return self._get_ok("/pdf", params={"target": target_id, "file": str(file_path)}, timeout=30)

    def info(self, target_id: str) -> dict[str, Any] | None:
        data = self._get_json("/info", params={"target": target_id}, timeout=5)
        return data if isinstance(data, dict) else None

    def _get_json(self, path: str, params: dict[str, Any] | None = None, timeout: float = 5) -> Any:
        try:
            response = httpx.get(f"{self.base_url}{path}", params=params, timeout=timeout, trust_env=False)
            if response.status_code == 200:
                return response.json()
        except (httpx.HTTPError, ValueError):
            return None
        return None

    def _get_ok(self, path: str, params: dict[str, Any] | None = None, timeout: float = 5) -> bool:
        try:
            response = httpx.get(f"{self.base_url}{path}", params=params, timeout=timeout, trust_env=False)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def _post_ok(self, path: str, params: dict[str, Any], content: str, timeout: float = 10) -> bool:
        try:
            response = httpx.post(
                f"{self.base_url}{path}",
                params=params,
                content=content,
                timeout=timeout,
                trust_env=False,
            )
            return response.status_code == 200
        except httpx.HTTPError:
            return False
