"""Built-in BossHunter Browser Runtime management."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from importlib.resources import files
from pathlib import Path
from typing import Any

import httpx

from bosshunter.config import DEFAULTS

_browser_config: dict[str, Any] | None = None
_runtime_process: subprocess.Popen | None = None


def _default_browser_config() -> dict[str, Any]:
    defaults = DEFAULTS["browser"]
    result: dict[str, Any] = {}
    for key, value in defaults.items():
        result[key] = value[:] if isinstance(value, list) else value
    return result


def set_browser_config(config: dict[str, Any] | None) -> None:
    """Set process-wide browser config used by facade calls."""
    global _browser_config
    _browser_config = get_browser_config(config)


def get_browser_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return browser config merged with defaults."""
    merged = _default_browser_config()
    source = config if config is not None else (_browser_config or {})
    browser = source.get("browser", source) if isinstance(source, dict) else {}
    if isinstance(browser, dict):
        merged.update(browser)
    return merged


def get_runtime_url(config: dict[str, Any] | None = None) -> str:
    """Return configured local runtime URL."""
    browser = get_browser_config(config)
    host = browser.get("proxy_host", "127.0.0.1")
    port = browser.get("proxy_port", 3456)
    return f"http://{host}:{port}"


def get_runtime_script_path() -> Path:
    """Return bundled CDP proxy script path."""
    try:
        script = files(__package__).joinpath("cdp-proxy.mjs")
        path = Path(os.fspath(script))
        if path.exists():
            return path
    except Exception:
        pass
    return Path(__file__).with_name("cdp-proxy.mjs")


def check_node_available() -> dict[str, Any]:
    """Check whether Node.js can run the bundled browser runtime."""
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "version": None, "error": str(exc)}

    version = (result.stdout or result.stderr or "").strip()
    return {
        "available": result.returncode == 0,
        "version": version or None,
        "error": None if result.returncode == 0 else version,
    }


def runtime_health(config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Return `/health` JSON when the runtime responds."""
    try:
        response = httpx.get(f"{get_runtime_url(config)}/health", timeout=3, trust_env=False)
        if response.status_code == 200:
            return response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return None


def is_bosshunter_runtime(config: dict[str, Any] | None = None) -> bool:
    """Return True only when the local service identifies as BossHunter Runtime."""
    health = runtime_health(config)
    return bool(health and health.get("runtime") == "bosshunter")


def runtime_targets(config: dict[str, Any] | None = None) -> list[dict[str, Any]] | None:
    """Return runtime page targets when Chrome and BossHunter Runtime are ready."""
    if not is_bosshunter_runtime(config):
        return None
    try:
        response = httpx.get(f"{get_runtime_url(config)}/targets", timeout=5, trust_env=False)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                return data
    except (httpx.HTTPError, ValueError):
        return None
    return None


def _chrome_debugging_available(browser: dict[str, Any]) -> bool:
    """Return whether any configured Chrome debugging endpoint is responding."""
    for port in browser.get("chrome_ports", [9222, 9229, 9333]):
        try:
            response = httpx.get(f"http://127.0.0.1:{int(port)}/json/version", timeout=1, trust_env=False)
            if response.status_code == 200 and response.json().get("webSocketDebuggerUrl"):
                return True
        except (httpx.HTTPError, ValueError, TypeError):
            continue
    return False


def _is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _find_available_runtime_port(browser: dict[str, Any]) -> int | None:
    host = browser.get("proxy_host", "127.0.0.1")
    start_port = int(browser.get("proxy_port", 3456))
    for port in range(start_port + 1, min(start_port + 100, 65535) + 1):
        if _is_port_available(host, port):
            return port
    return None


def _find_reusable_runtime_port(browser: dict[str, Any]) -> int | None:
    """Find an already-connected BossHunter Runtime on a fallback local port."""
    host = browser.get("proxy_host", "127.0.0.1")
    start_port = int(browser.get("proxy_port", 3456))
    for port in range(start_port + 1, min(start_port + 100, 65535) + 1):
        candidate = dict(browser)
        candidate["proxy_port"] = port
        health = runtime_health(candidate)
        if health and health.get("runtime") == "bosshunter" and health.get("connected"):
            return port
    return None


def _switch_runtime_port(config: dict[str, Any] | None, browser: dict[str, Any], port: int) -> None:
    global _browser_config
    browser["proxy_port"] = port
    if isinstance(config, dict):
        if isinstance(config.get("browser"), dict):
            config["browser"]["proxy_port"] = port
        else:
            config["proxy_port"] = port
    _browser_config = browser


def _runtime_env(config: dict[str, Any] | None = None) -> dict[str, str]:
    browser = get_browser_config(config)
    env = os.environ.copy()
    env["BOSSHUNTER_BROWSER_PROXY_PORT"] = str(browser.get("proxy_port", 3456))
    env["BOSSHUNTER_CHROME_PORTS"] = ",".join(str(port) for port in browser.get("chrome_ports", [9222, 9229, 9333]))
    env["BOSSHUNTER_ENABLE_PORT_GUARD"] = "true" if browser.get("enable_port_guard", True) else "false"
    return env


def start_runtime(config: dict[str, Any] | None = None) -> subprocess.Popen:
    """Start the bundled Node.js browser runtime."""
    global _runtime_process
    script_path = get_runtime_script_path()
    kwargs: dict[str, Any] = {
        "env": _runtime_env(config),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    _runtime_process = subprocess.Popen(["node", str(script_path)], **kwargs)
    return _runtime_process


def ensure_runtime(config: dict[str, Any] | None = None, wait_seconds: float = 15.0) -> bool:
    """Ensure the local Browser Runtime is ready for page operations."""
    browser = get_browser_config(config)
    if runtime_targets(browser) is not None:
        set_browser_config(browser)
        return True
    if browser.get("runtime") != "builtin":
        return False
    if not browser.get("auto_start_proxy", True):
        return False
    if not check_node_available().get("available"):
        return False

    health = runtime_health(browser)
    stale_bosshunter_runtime = (
        health
        and health.get("runtime") == "bosshunter"
        and not health.get("connected")
        and _chrome_debugging_available(browser)
    )
    if health and (health.get("runtime") != "bosshunter" or stale_bosshunter_runtime):
        reusable_port = _find_reusable_runtime_port(browser)
        if reusable_port is not None:
            _switch_runtime_port(config, browser, reusable_port)
            if runtime_targets(browser) is not None:
                set_browser_config(browser)
                return True

        fallback_port = _find_available_runtime_port(browser)
        if fallback_port is None:
            return False
        _switch_runtime_port(config, browser, fallback_port)

    start_runtime(browser)
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if runtime_targets(browser) is not None:
            set_browser_config(browser)
            return True
        time.sleep(0.5)
    return False


__all__ = [
    "set_browser_config",
    "get_browser_config",
    "get_runtime_url",
    "get_runtime_script_path",
    "check_node_available",
    "runtime_health",
    "is_bosshunter_runtime",
    "runtime_targets",
    "start_runtime",
    "ensure_runtime",
]
