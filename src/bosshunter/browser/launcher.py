"""Launch a dedicated Chrome instance that BossHunter can safely control."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx


DEFAULT_LOGIN_URL = "https://www.zhipin.com/web/geek/recommend"


def find_chrome_executable() -> Path | None:
    """Find a locally installed Google Chrome executable on supported platforms."""
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _chrome_port(config: dict[str, Any]) -> int:
    ports = config.get("browser", {}).get("chrome_ports", [9222])
    try:
        return int(ports[0])
    except (IndexError, TypeError, ValueError):
        return 9222


def _debugging_ready(port: int) -> bool:
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=1, trust_env=False)
        return response.status_code == 200 and bool(response.json().get("webSocketDebuggerUrl"))
    except (httpx.HTTPError, ValueError):
        return False


def _wait_for_debugging_port(port: int, timeout: float = 10) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _debugging_ready(port):
            return True
        time.sleep(0.25)
    return False


def launch_chrome(config: dict[str, Any], dashboard_url: str, login_url: str = DEFAULT_LOGIN_URL) -> dict[str, Any]:
    """Start an isolated Chrome profile with CDP enabled and open the two entry pages.

    A dedicated profile avoids altering the user's everyday Chrome profile while keeping
    the login session available to the local Browser Runtime on subsequent starts.
    """
    port = _chrome_port(config)
    if _debugging_ready(port):
        return {"started": False, "ready": True, "port": port, "message": "已复用已开启调试的 Chrome"}

    executable = find_chrome_executable()
    if executable is None:
        return {"started": False, "ready": False, "port": port, "message": "未找到 Google Chrome"}

    browser_cfg = config.get("browser", {})
    profile_dir = Path(browser_cfg.get("chrome_profile_dir", "./data/chrome-debug-profile")).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    arguments = [
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    arguments.extend(url for url in (dashboard_url, login_url) if url)
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([str(executable), *arguments], **kwargs)

    ready = _wait_for_debugging_port(port)
    message = "Chrome 已开启远程调试" if ready else "Chrome 已启动，但远程调试端口尚未就绪"
    return {"started": True, "ready": ready, "port": port, "message": message}
