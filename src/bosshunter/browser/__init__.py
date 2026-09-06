"""Browser facade backed by BossHunter's built-in Browser Runtime."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from bosshunter.browser.client import RuntimeClient
from bosshunter.browser.runtime import ensure_runtime, get_runtime_url, set_browser_config

CDP_PROXY_URL = get_runtime_url()
CDP_DIRECT_URL = "http://localhost:9222"
CDP_DEFAULT_URL = "http://127.0.0.1:9222"
NAV_TIMEOUT_MS = 15000


def configure(config: dict[str, Any] | None = None) -> None:
    """Set process-wide browser configuration used by runtime helpers."""
    set_browser_config(config)


def _client() -> RuntimeClient:
    return RuntimeClient()


def _ready() -> bool:
    return ensure_runtime()


def check_chrome_connection() -> dict | None:
    """Check whether Browser Runtime can connect to Chrome."""
    if not _ready():
        return None
    return _client().health()


def get_page_targets() -> list[dict]:
    """Get page targets from Browser Runtime."""
    if not _ready():
        return []
    return _client().targets()


def find_boss_tab() -> dict | None:
    """Find a BOSS直聘 tab in Chrome."""
    for target in get_page_targets():
        if "zhipin.com" in target.get("url", ""):
            return target
    return None


def find_zhilian_tab() -> dict | None:
    """Find a 智联招聘 tab in Chrome without opening or logging in."""
    for target in get_page_targets():
        url = str(target.get("url", ""))
        if "zhaopin.com" in url:
            return target
    return None


def new_tab(url: str, background: bool = False) -> str | None:
    """Open a tab and return its target ID."""
    if not _ready():
        return None
    return _client().new_tab(url, background=background)


def close_tab(target_id: str) -> bool:
    """Close a tab by target ID."""
    if not _ready():
        return False
    return _client().close_tab(target_id)


def navigate(target_id: str, url: str) -> bool:
    """Navigate a tab to a URL."""
    if not _ready():
        return False
    return _client().navigate(target_id, url)


def evaluate(target_id: str, expression: str, timeout: float = 30) -> Any:
    """Execute JavaScript in a tab and return the runtime value."""
    if not _ready():
        return None
    return _client().evaluate(target_id, expression, timeout)


def click(target_id: str, selector: str) -> bool:
    """Click an element by CSS selector using DOM click."""
    if not _ready():
        return False
    return _client().click(target_id, selector)


def click_at(target_id: str, selector_or_xy: str) -> bool:
    """Click by selector or x,y coordinates using CDP mouse events."""
    if not _ready():
        return False
    return _client().click_at(target_id, selector_or_xy)


def type_text(target_id: str, text: str, human: bool = False) -> bool:
    """Insert text using CDP input events."""
    if not _ready():
        return False
    return _client().type_text(target_id, text, human=human)


def press_key(target_id: str, key: str) -> bool:
    """Press a supported browser key using CDP keyboard events."""
    if not _ready():
        return False
    return _client().press_key(target_id, key)


def set_files(target_id: str, selector: str, files: list[str]) -> bool:
    """Set files on a file input."""
    if not _ready():
        return False
    return _client().set_files(target_id, selector, files)


def scroll(target_id: str, y: int = 0, direction: str = "") -> bool:
    """Scroll a page."""
    if not _ready():
        return False
    return _client().scroll(target_id, y=y, direction=direction)


def screenshot(target_id: str, file_path: str | Path, *, selector: str = "") -> bool:
    """Capture a screenshot to a file."""
    if not _ready():
        return False
    return _client().screenshot(target_id, file_path, selector=selector)


def print_pdf(target_id: str, file_path: str | Path) -> bool:
    """Render a target as PDF to a file."""
    if not _ready():
        return False
    return _client().print_pdf(target_id, file_path)


def get_page_info(target_id: str) -> dict | None:
    """Get page title, URL, and ready state."""
    if not _ready():
        return None
    return _client().info(target_id)


def wait_for_load(target_id: str, timeout: float = 10.0) -> bool:
    """Wait for page ready state to become complete."""
    start = time.time()
    while time.time() - start < timeout:
        info = get_page_info(target_id)
        if info and info.get("ready") == "complete":
            return True
        time.sleep(0.5)
    return False
