"""User-facing Browser Runtime diagnostics."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx
from rich.console import Console

from bosshunter.browser import evaluate, find_boss_tab
from bosshunter.browser.runtime import check_node_available, ensure_runtime, get_runtime_url, runtime_health, runtime_targets


def run_browser_diagnostics(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Collect Browser Runtime readiness state."""
    node = check_node_available()
    runtime_ready = ensure_runtime(config)
    runtime_url = get_runtime_url(config)
    health = runtime_health(config)
    targets = runtime_targets(config) if runtime_ready else None
    if runtime_ready:
        # `/targets` establishes the CDP connection. Refresh health afterwards
        # so browser product/version information is available to diagnostics.
        health = runtime_health(config) or health
    boss_tab = find_boss_tab() if runtime_ready else None
    browser_product, browser_name = _browser_identity(health)

    errors: list[str] = []
    if not node.get("available") and not runtime_ready:
        errors.append("Node.js is required for BossHunter Browser Runtime.")
    if not runtime_ready:
        if health and health.get("runtime") != "bosshunter":
            errors.append("Runtime port is occupied by a non-BossHunter service.")
        else:
            errors.append("BossHunter Browser Runtime is not ready.")
    if runtime_ready and targets is None:
        errors.append("Chrome is not connected to Browser Runtime.")

    return {
        "node": node,
        "runtime": runtime_ready,
        "chrome": isinstance(targets, list),
        "targets": targets or [],
        "boss_tab": boss_tab,
        "errors": errors,
        "runtime_url": runtime_url,
        "health": health,
        "browser_product": browser_product,
        "browser_name": browser_name,
    }


def get_boss_login_status(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a safe, user-facing BOSS login state for task guards."""
    diagnostics = run_browser_diagnostics(config)
    checked_at = datetime.now().isoformat(timespec="seconds")
    if not diagnostics.get("runtime") or not diagnostics.get("chrome"):
        return {
            "ready": False,
            "status": "unavailable",
            "message": "BOSS 浏览器连接不可用，请先启动并连接 Google Chrome。",
            "checked_at": checked_at,
        }

    boss_tab = diagnostics.get("boss_tab")
    if not isinstance(boss_tab, dict):
        return {
            "ready": False,
            "status": "logged_out",
            "message": "请先在已连接的 Chrome 中打开 BOSS 直聘并登录。",
            "checked_at": checked_at,
        }

    url = str(boss_tab.get("url") or "").lower()
    if any(marker in url for marker in ("/login", "/web/user", "signin")):
        return {
            "ready": False,
            "status": "logged_out",
            "message": "BOSS 直聘当前未登录，请在 Chrome 中完成登录后重试。",
            "checked_at": checked_at,
        }

    state = _boss_page_login_state(str(boss_tab.get("targetId") or ""))
    if state.get("has_user_nav") and not state.get("has_login_dialog"):
        return {
            "ready": True,
            "status": "logged_in",
            "message": "BOSS 已登录。",
            "checked_at": checked_at,
        }
    return {
        "ready": False,
        "status": "logged_out",
        "message": "无法确认 BOSS 登录状态，请在 Chrome 中重新登录后重试。",
        "checked_at": checked_at,
    }


def _boss_page_login_state(target_id: str) -> dict[str, bool]:
    """Read non-sensitive visual login markers from the active BOSS page."""
    if not target_id:
        return {}
    expression = """
        (() => {
            const isVisible = (element) => {
                const style = window.getComputedStyle(element);
                const bounds = element.getBoundingClientRect();
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && style.opacity !== '0'
                    && bounds.width > 0
                    && bounds.height > 0;
            };
            return JSON.stringify({
                has_user_nav: Boolean(document.querySelector('.user-nav, .user-nav-avatar, .nav-figure')),
                has_login_dialog: Array.from(document.querySelectorAll('.login-dialog, .login-panel, .sign-wrap')).some(isVisible)
            });
        })()
    """
    raw_state = evaluate(target_id, expression)
    if not isinstance(raw_state, str):
        return {}
    try:
        parsed = json.loads(raw_state)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        "has_user_nav": bool(parsed.get("has_user_nav")),
        "has_login_dialog": bool(parsed.get("has_login_dialog")),
    }


def _browser_identity(health: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Return browser identity, including fallback support for older runtimes."""
    health = health or {}
    product = health.get("browserProduct")
    name = health.get("browserName")
    if product or name:
        return product, name

    port = health.get("chromePort")
    if not port:
        return None, None
    try:
        result = httpx.get(f"http://127.0.0.1:{int(port)}/json/version", timeout=2, trust_env=False)
        result.raise_for_status()
        product = result.json().get("Browser")
    except (httpx.HTTPError, TypeError, ValueError):
        return None, None

    if isinstance(product, str) and product.startswith("Edg/"):
        return product, "Microsoft Edge"
    if isinstance(product, str) and product.startswith("Chrome/"):
        return product, "Google Chrome"
    return product if isinstance(product, str) else None, None


def print_browser_diagnostics(config: dict[str, Any] | None = None, console: Console | None = None) -> bool:
    """Print actionable runtime diagnostics. Returns True when runtime and Chrome are ready."""
    out = console or Console()
    out.print("[bold]正在检测 Browser Runtime...[/bold]")
    result = run_browser_diagnostics(config)

    node = result["node"]
    if node.get("available"):
        out.print(f"[green]✓[/green] Node.js 可用: {node.get('version') or 'unknown'}")
    else:
        out.print("[red]✗[/red] Node.js 不可用")
        out.print("  BossHunter 内置浏览器运行时需要 Node.js。请安装 Node.js 22+ 后重试。")

    if result["runtime"]:
        out.print(f"[green]✓[/green] BossHunter CDP Runtime 已启动: {result['runtime_url']}")
    else:
        out.print(f"[red]✗[/red] BossHunter CDP Runtime 未就绪: {result['runtime_url']}")
        if any("non-BossHunter" in error for error in result["errors"]):
            out.print("  端口已被非 BossHunter 服务占用，请停止旧的 CDP Proxy 或在 config.yaml 中修改 browser.proxy_port。")

    if result["chrome"]:
        out.print("[green]✓[/green] 已连接 Chrome")
    elif any("non-BossHunter" in error for error in result["errors"]):
        out.print("[yellow]![/yellow] 暂未检测 Chrome：Browser Runtime 端口被占用，无法启动内置 Runtime。")
    else:
        out.print("[red]✗[/red] 未发现 Chrome 调试端口。")
        out.print("  请打开 Chrome，访问 chrome://inspect/#remote-debugging，勾选 Allow remote debugging。")
        out.print("  或使用: chrome.exe --remote-debugging-port=9222")

    if result["boss_tab"]:
        out.print(f"[green]✓[/green] 发现 BOSS直聘 页面: {result['boss_tab'].get('title', '')}")
    else:
        out.print("[yellow]![/yellow] 未发现 BOSS直聘 页面，请在 Chrome 中打开 www.zhipin.com 并登录")

    return bool(result["runtime"] and result["chrome"])
