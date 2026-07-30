"""Structured, user-facing checks before starting a workbench task."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx

from bosshunter.ai.credentials import (
	get_ai_api_key,
	get_ai_base_url,
	get_ai_key_source,
	get_ai_service,
)
from bosshunter.browser.diagnostics import run_browser_diagnostics


VALID_MODES = {"full", "collect", "rescore", "monitor"}


def collect_preflight_checks(mode: str, config: dict) -> list[dict[str, str]]:
	"""Collect configuration, AI connectivity, and browser readiness checks."""
	checks = _configuration_checks(mode, config)
	if mode not in VALID_MODES:
		return checks

	with ThreadPoolExecutor(max_workers=2) as executor:
		ai_future = executor.submit(check_ai_connection, deepcopy(config), mode in {"full", "collect", "rescore"})
		browser_future = executor.submit(check_browser_connection, deepcopy(config))
		for future, fallback in (
			(ai_future, _check("ai_connection", "AI 接口连接", "error", "AI 接口检测失败", "请检查 AI 设置后重试。", "config")),
			(
				browser_future,
				_check(
					"browser_runtime",
					"浏览器连接",
					"error",
					"浏览器连接检测失败",
					"请重新启动 BossHunter 后再试。",
					"browser",
				),
			),
		):
			try:
				checks.extend(future.result())
			except Exception:
				checks.append(fallback)
	return checks


def check_ai_connection(config: dict, required: bool = True) -> list[dict[str, str]]:
	"""Validate AI credentials and perform a no-token model-list request."""
	ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
	severity = "error" if required else "warning"
	provider = str(ai_cfg.get("provider") or "anthropic")
	service = get_ai_service(config)
	credential = get_ai_api_key(config)
	key_source = get_ai_key_source(config)
	base_url_value = get_ai_base_url(config)
	model = str(ai_cfg.get("model") or "").strip()
	if not credential:
		return [
			_check(
				"ai_credentials",
				"AI API Key",
				severity,
				"尚未填写 AI API Key",
				"打开“配置 → AI 设置”，填写 API Key 并保存。单独监测仍可启动，但无法生成 AI 回复或定制简历。",
				"config",
			)
		]
	if not model:
		return [
			_check(
				"ai_model",
				"AI 模型",
				severity,
				"尚未填写模型名称",
				"打开“配置 → AI 设置”，填写接口支持的模型名称。",
				"config",
			)
		]

	base_url = str(base_url_value or "").strip()
	if service == "custom" and provider == "openai_compatible" and not base_url:
		return [
			_check(
				"ai_base_url",
				"AI Base URL",
				severity,
				"兼容 API 缺少 Base URL",
				"打开“配置 → AI 设置”，填写服务商提供的 Base URL。",
				"config",
			)
		]

	if provider == "openai_compatible":
		models_url = f"{base_url.rstrip('/')}/models"
		headers = {"Authorization": f"Bearer {credential}"}
	else:
		models_url = (
			f"{base_url.rstrip('/')}/v1/models"
			if base_url
			else "https://api.anthropic.com/v1/models"
		)
		auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or ai_cfg.get("auth_token")
		headers = (
			{"Authorization": f"Bearer {auth_token}"}
			if auth_token
			else {"x-api-key": str(credential)}
		)
		headers["anthropic-version"] = "2023-06-01"

	try:
		result = httpx.get(models_url, headers=headers, timeout=8, follow_redirects=True)
	except httpx.TimeoutException:
		return [
			_check(
				"ai_connection",
				"AI 接口连接",
				severity,
				"AI 接口连接超时",
				"请检查 Base URL、网络或代理设置，然后重新检测。",
				"config",
			)
		]
	except httpx.HTTPError:
		return [
			_check(
				"ai_connection",
				"AI 接口连接",
				severity,
				"无法连接 AI 接口",
				"请检查 Base URL、网络或代理设置，然后重新检测。",
				"config",
			)
		]

	if result.status_code in {401, 403}:
		return [
			_check(
				"ai_connection",
				"AI 接口连接",
				severity,
				"API Key 验证失败",
				"服务商拒绝了当前凭证，请核对 API Key 是否正确、有效且有权限。",
				"config",
			)
		]
	if result.status_code in {404, 405}:
		return [
			_check(
				"ai_connection",
				"AI 接口连接",
				"warning",
				"接口可访问，但无法自动验证模型",
				"该兼容服务未提供模型列表接口；BossHunter 启动时仍会尝试消息接口。",
				"config",
			)
		]
	if result.status_code == 429:
		return [
			_check(
				"ai_connection",
				"AI 接口连接",
				"warning",
				"AI 接口已连接，但当前被限流",
				"请检查额度或稍后重试。",
				"config",
			)
		]
	if result.status_code >= 400:
		return [
			_check(
				"ai_connection",
				"AI 接口连接",
				severity,
				f"AI 接口返回异常状态 {result.status_code}",
				"请检查 Base URL、API Key 和服务商状态。",
				"config",
			)
		]

	return [
		_check(
			"ai_connection",
			"AI 接口连接",
			"pass",
			"AI 接口连接正常",
			f"已验证凭证和服务地址，当前模型：{model}；Key 来源：{key_source or '未知'}。",
		)
	]


def check_browser_connection(config: dict) -> list[dict[str, str]]:
	"""Translate Browser Runtime diagnostics into actionable checks."""
	result = run_browser_diagnostics(config)
	checks: list[dict[str, str]] = []
	node = result.get("node") or {}
	errors = [str(error) for error in result.get("errors") or []]

	if result.get("runtime"):
		checks.append(
			_check(
				"browser_runtime",
				"浏览器运行组件",
				"pass",
				"BossHunter 浏览器运行组件正常",
				f"本地连接地址：{result.get('runtime_url') or '已就绪'}。",
			)
		)
	elif not node.get("available"):
		checks.append(
			_check(
				"browser_runtime",
				"浏览器运行组件",
				"error",
				"缺少可用的 Node.js",
				"BossHunter 无法启动浏览器连接组件，请安装 Node.js 22+ 后重启。",
				"browser",
			)
		)
	else:
		if any("non-BossHunter" in error for error in errors):
			message = "浏览器连接端口被其他程序占用"
			detail = "请停止占用本地 Runtime 端口的程序，或修改 browser.proxy_port 后重启。"
		else:
			message = "BossHunter 浏览器运行组件未启动"
			detail = "请重新启动 BossHunter；若仍失败，运行 bosshunter connect 查看终端诊断。"
		checks.append(_check("browser_runtime", "浏览器运行组件", "error", message, detail, "browser"))

	if result.get("runtime") and not result.get("chrome"):
		checks.append(
			_check(
				"chrome_connection",
				"Chrome 远程调试",
				"error",
				"没有连接到开启远程调试的 Google Chrome",
				"请打开 Google Chrome，访问 chrome://inspect/#remote-debugging，并开启 Allow remote debugging，然后重新检测。",
				"browser",
			)
		)
	elif result.get("chrome"):
		product = str(result.get("browser_product") or "").strip()
		browser_name = str(result.get("browser_name") or "").strip()
		is_google_chrome = (
			browser_name in {"Google Chrome", "Google Chrome Canary"}
			if browser_name
			else not product or product.startswith("Chrome/")
		)
		if not is_google_chrome:
			detected_browser = browser_name or product
			checks.append(
				_check(
					"chrome_product",
					"浏览器类型",
					"error",
					f"当前连接的不是 Google Chrome（{detected_browser}）",
					"请关闭当前调试连接，改用 Google Chrome 开启远程调试后重新检测。",
					"browser",
				)
			)
		else:
			browser_text = browser_name or product
			product_text = f"（{browser_text}）" if browser_text else ""
			checks.append(
				_check(
					"chrome_connection",
					"Chrome 远程调试",
					"pass",
					f"Google Chrome 已连接{product_text}",
					"远程调试连接正常。",
				)
			)

	if result.get("chrome"):
		boss_tab = result.get("boss_tab")
		if boss_tab:
			url = str(boss_tab.get("url") or "")
			if any(marker in url.lower() for marker in ("/login", "/user/", "signin")):
				checks.append(
					_check(
						"boss_login",
						"BOSS 直聘登录",
						"warning",
						"BOSS 直聘可能尚未登录",
						"请在已连接的 Google Chrome 中完成登录，再开始任务。",
						"browser",
					)
				)
			else:
				checks.append(
					_check(
						"boss_tab",
						"BOSS 直聘页面",
						"pass",
						"已发现 BOSS 直聘页面",
						"请确认该页面已登录招聘平台账号。",
					)
				)
		else:
			checks.append(
				_check(
					"boss_tab",
					"BOSS 直聘页面",
					"warning",
					"未发现已打开的 BOSS 直聘页面",
					"请在已连接的 Google Chrome 中打开 www.zhipin.com 并确认已经登录。",
					"browser",
				)
			)
	return checks


def error_messages(checks: list[dict[str, str]]) -> list[str]:
	"""Return backward-compatible blocker messages from structured checks."""
	return [
		f"{check['message']}：{check['detail']}"
		for check in checks
		if check.get("status") == "error"
	]


def _configuration_checks(mode: str, config: dict) -> list[dict[str, str]]:
	checks: list[dict[str, str]] = []
	if mode not in VALID_MODES:
		checks.append(
			_check(
				"task_mode",
				"任务模式",
				"error",
				f"不支持的任务模式：{mode}",
				"请刷新页面后重试。",
			)
		)
		return checks

	resume_path = config.get("profile", {}).get("resume_path", "")
	if not resume_path or not Path(str(resume_path)).exists():
		checks.append(
			_check(
				"resume",
				"简历文件",
				"error",
				"尚未配置有效简历",
				"打开“配置 → 个人信息”，上传 .md 或 .docx 简历。",
				"config",
			)
		)
	else:
		checks.append(_check("resume", "简历文件", "pass", "简历文件已就绪", Path(str(resume_path)).name))

	if mode in {"full", "collect"}:
		keywords = config.get("search", {}).get("keywords") or []
		if not keywords:
			checks.append(
				_check(
					"search_keywords",
					"搜索关键词",
					"error",
					"尚未填写搜索关键词",
					"打开“配置 → 搜索设置”，至少添加一个岗位关键词。",
					"config",
				)
			)
		else:
			checks.append(
				_check(
					"search_keywords",
					"搜索关键词",
					"pass",
					"搜索关键词已配置",
					"、".join(str(keyword) for keyword in keywords[:3]),
				)
			)
	return checks


def _check(
	check_id: str,
	title: str,
	status: str,
	message: str,
	detail: str,
	action: str = "",
) -> dict[str, str]:
	return {
		"id": check_id,
		"title": title,
		"status": status,
		"message": message,
		"detail": detail,
		"action": action,
	}
