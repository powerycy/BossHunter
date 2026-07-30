"""Built-in BossHunter Browser Runtime management."""

from __future__ import annotations

import os
import re
import shutil
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
_MIN_NODE_MAJOR = 22


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


def _candidate_node_executables() -> list[str]:
    """Return Node.js candidates without depending only on the caller's PATH."""
    candidates: list[str] = []

    def add(candidate: str | Path | None) -> None:
        if not candidate:
            return
        value = os.fspath(candidate)
        if value not in candidates:
            candidates.append(value)

    # Explicit override stays first for managed and packaged installations.
    add(os.environ.get("BOSSHUNTER_NODE_PATH"))
    add(shutil.which("node"))
    # Keep the command name as a fallback so shell-managed installations and
    # existing tests continue to work even when `which` is unavailable.
    add("node")

    # Some AI desktop runtimes ship a private Node.js but do not export it to
    # child-process PATH. Reuse that executable instead of asking the user to
    # install a duplicate copy.
    executable = Path(sys.executable).resolve()
    for parent in executable.parents:
        add(parent / "node" / "bin" / ("node.exe" if os.name == "nt" else "node"))

    home = Path.home()
    patterns = [
        ".cache/codex-runtimes/*/dependencies/node/bin/node",
        ".nvm/versions/node/*/bin/node",
        ".volta/bin/node",
        ".local/share/fnm/node-versions/*/installation/bin/node",
        ".local/share/mise/installs/node/*/bin/node",
        ".asdf/installs/nodejs/*/bin/node",
    ]
    for pattern in patterns:
        for candidate in sorted(home.glob(pattern), reverse=True):
            add(candidate)

    if os.name == "nt":
        for root_name in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(root_name)
            if root:
                add(Path(root) / "nodejs" / "node.exe")
                add(Path(root) / "Programs" / "nodejs" / "node.exe")
    else:
        add("/opt/homebrew/bin/node")
        add("/usr/local/bin/node")
        add("/opt/local/bin/node")

    return candidates


def _node_major(version: str) -> int | None:
    match = re.match(r"^v?(\d+)", version.strip())
    return int(match.group(1)) if match else None


def check_node_available() -> dict[str, Any]:
    """Find a Node.js runtime new enough for the bundled browser component."""
    errors: list[str] = []
    for executable in _candidate_node_executables():
        if executable != "node" and not Path(executable).is_file():
            continue
        try:
            result = subprocess.run(
                [executable, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
            continue

        version = (result.stdout or result.stderr or "").strip()
        major = _node_major(version)
        if result.returncode == 0 and major is not None and major >= _MIN_NODE_MAJOR:
            return {
                "available": True,
                "version": version or None,
                "executable": executable,
                "error": None,
            }
        if version:
            errors.append(f"{executable}: {version}（需要 Node.js {_MIN_NODE_MAJOR}+）")

    return {
        "available": False,
        "version": None,
        "executable": None,
        "error": errors[-1] if errors else f"未找到 Node.js {_MIN_NODE_MAJOR}+",
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


def start_runtime(config: dict[str, Any] | None = None, node_executable: str = "node") -> subprocess.Popen:
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
    _runtime_process = subprocess.Popen([node_executable, str(script_path)], **kwargs)
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
    node = check_node_available()
    if not node.get("available"):
        return False

    health = runtime_health(browser)
    if health and health.get("runtime") != "bosshunter":
        fallback_port = _find_available_runtime_port(browser)
        if fallback_port is None:
            return False
        _switch_runtime_port(config, browser, fallback_port)

    start_runtime(browser, str(node.get("executable") or "node"))
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
