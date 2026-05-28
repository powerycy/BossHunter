"""Browser connection module - CDP Proxy connection to user's Chrome."""

import json
import time
from typing import Any

import httpx
from rich.console import Console

console = Console()

CDP_PROXY_URL = "http://localhost:3456"
CDP_DIRECT_URL = "http://localhost:9222"


def check_chrome_connection() -> dict | None:
    """Check if Chrome debug port is accessible via CDP Proxy."""
    try:
        resp = httpx.get(f"{CDP_PROXY_URL}/health", timeout=3)
        if resp.status_code == 200:
            return resp.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    # Fallback: try direct CDP
    try:
        resp = httpx.get(f"{CDP_DIRECT_URL}/json/version", timeout=3)
        if resp.status_code == 200:
            return resp.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return None


def get_page_targets() -> list[dict]:
    """Get list of page targets from CDP Proxy."""
    try:
        resp = httpx.get(f"{CDP_PROXY_URL}/targets", timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return []


def find_boss_tab() -> dict | None:
    """Find a BOSS直聘 tab in Chrome."""
    targets = get_page_targets()
    for target in targets:
        url = target.get("url", "")
        if "zhipin.com" in url:
            return target
    return None


def new_tab(url: str) -> str | None:
    """Open a new tab and return target ID."""
    try:
        resp = httpx.get(f"{CDP_PROXY_URL}/new", params={"url": url}, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("targetId")
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return None


def close_tab(target_id: str) -> bool:
    """Close a tab by target ID."""
    try:
        resp = httpx.get(f"{CDP_PROXY_URL}/close", params={"target": target_id}, timeout=5)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def navigate(target_id: str, url: str) -> bool:
    """Navigate a tab to a URL."""
    try:
        resp = httpx.get(
            f"{CDP_PROXY_URL}/navigate",
            params={"target": target_id, "url": url},
            timeout=15
        )
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def evaluate(target_id: str, expression: str, timeout: float = 30) -> Any:
    """Execute JavaScript in a tab."""
    try:
        resp = httpx.post(
            f"{CDP_PROXY_URL}/eval",
            params={"target": target_id},
            content=expression,
            timeout=timeout
        )
        if resp.status_code == 200:
            if not resp.content:
                return None
            try:
                data = resp.json()
            except (ValueError, TypeError):
                return None
            return data.get("value")
        else:
            # Non-200: try to parse error
            try:
                return resp.json()
            except Exception:
                return None
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return None


def click(target_id: str, selector: str) -> bool:
    """Click an element by CSS selector."""
    try:
        resp = httpx.post(
            f"{CDP_PROXY_URL}/click",
            params={"target": target_id},
            content=selector,
            timeout=10
        )
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def scroll(target_id: str, y: int = 0, direction: str = "") -> bool:
    """Scroll a page."""
    try:
        params: dict[str, Any] = {"target": target_id}
        if direction:
            params["direction"] = direction
        else:
            params["y"] = y
        resp = httpx.get(f"{CDP_PROXY_URL}/scroll", params=params, timeout=5)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def get_page_info(target_id: str) -> dict | None:
    """Get page title and URL."""
    try:
        resp = httpx.get(
            f"{CDP_PROXY_URL}/info",
            params={"target": target_id},
            timeout=5
        )
        if resp.status_code == 200:
            return resp.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return None


def wait_for_load(target_id: str, timeout: float = 10.0) -> bool:
    """Wait for page to finish loading."""
    start = time.time()
    while time.time() - start < timeout:
        info = get_page_info(target_id)
        if info and info.get("ready") == "complete":
            return True
        time.sleep(0.5)
    return False
