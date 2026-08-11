"""Credential-safe AI diagnostics for OpenAI-compatible services."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

import httpx

from bosshunter.ai.credentials import (
	AIRequestError,
	get_ai_api_key,
	get_ai_base_url,
	get_ai_key_source,
	get_ai_service,
	normalize_openai_base_url,
)


def _stage(stage_id: str, status: str, message: str, started: float, detail: str = "") -> dict[str, Any]:
	result: dict[str, Any] = {
		"id": stage_id,
		"status": status,
		"message": message,
		"elapsed_ms": round((time.monotonic() - started) * 1000),
	}
	if detail:
		result["detail"] = detail
	return result


def _safe_models(payload: Any) -> list[dict[str, str]]:
	items = payload.get("data") if isinstance(payload, dict) else None
	if items is None and isinstance(payload, dict):
		items = payload.get("models")
	if not isinstance(items, list):
		raise AIRequestError("invalid_response", "模型列表 JSON 缺少 data/models 数组")
	models: list[dict[str, str]] = []
	for item in items:
		if isinstance(item, str):
			model_id = item.strip()
		elif isinstance(item, dict):
			model_id = str(item.get("id") or item.get("name") or "").strip()
		else:
			model_id = ""
		if model_id:
			models.append({"id": model_id})
	if not models:
		raise AIRequestError("invalid_response", "模型列表为空或缺少模型 ID")
	return models


def _response_json(response: Any) -> Any:
	content_type = str(getattr(response, "headers", {}).get("content-type", "")).lower()
	body_value = getattr(response, "text", None)
	if body_value is not None:
		body = str(body_value or "")
		if not body.strip():
			raise AIRequestError("empty_response", "模型列表接口返回空响应")
		if "html" in content_type or body.lstrip().lower().startswith(("<!doctype", "<html")):
			raise AIRequestError("invalid_content_type", "模型列表接口返回 HTML，而不是 JSON")
	try:
		payload = response.json()
	except Exception as exc:
		raise AIRequestError("invalid_json", "模型列表接口返回不是合法 JSON") from exc
	if not isinstance(payload, dict):
		raise AIRequestError("invalid_response", "模型列表 JSON 顶层结构无效")
	return payload


def _diagnostic_error(exc: Exception) -> tuple[str, str]:
	if isinstance(exc, AIRequestError):
		return exc.kind, exc.user_message
	if isinstance(exc, httpx.TimeoutException):
		return "timeout", "AI 接口连接超时"
	if isinstance(exc, httpx.RequestError):
		return "network", "AI 接口连接失败"
	return "request_failed", "AI 接口检测失败"


def diagnose_ai(
	config: dict,
	*,
	get: Callable[..., Any] | None = None,
	timeout: float = 8.0,
) -> dict[str, Any]:
	"""Run the free model-list check. This function never calls chat completions."""
	ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
	service = get_ai_service(config)
	provider = str(ai_cfg.get("provider") or "")
	model = str(ai_cfg.get("model") or "").strip()
	key = get_ai_api_key(config)
	key_source = get_ai_key_source(config)
	stages: list[dict[str, Any]] = []
	started = time.monotonic()
	base_url_value = get_ai_base_url(config)
	try:
		if provider == "openai_compatible":
			normalized_base_url = normalize_openai_base_url(base_url_value or "", service)
			models_url = f"{normalized_base_url.rstrip('/')}/models"
		else:
			normalized_base_url = str(base_url_value or "").strip() or "https://api.anthropic.com/v1"
		models_url = f"{normalized_base_url.rstrip('/')}/models"
		stages.append(_stage("url", "pass", "服务地址有效", started))
	except ValueError:
		stages.append(_stage("url", "fail", "服务地址无效", started, "请检查 Base URL 格式。"))
		return {
			"ok": False,
			"billable": False,
			"normalized_base_url": None,
			"key_source": key_source,
			"current_model": model,
			"current_model_available": False,
			"models": [],
			"stages": stages,
			"error_kind": "invalid_url",
		}

	if not key:
		stages.append(_stage("credentials", "fail", "未找到 AI 凭证", started, "请在本地配置面板保存 API Key。"))
		return {
			"ok": False,
			"billable": False,
			"normalized_base_url": normalized_base_url,
			"key_source": None,
			"current_model": model,
			"current_model_available": False,
			"models": [],
			"stages": stages,
			"error_kind": "auth",
		}
	stages.append(_stage("credentials", "pass", "已找到本地凭证", started, f"来源：{key_source or '未知'}"))

	request_get = get or httpx.get
	if provider == "openai_compatible":
		headers = {"Authorization": f"Bearer {key}"}
	else:
		auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or ai_cfg.get("auth_token")
		headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {"x-api-key": str(key)}
		headers["anthropic-version"] = "2023-06-01"
	try:
		response = request_get(models_url, headers=headers, timeout=timeout, follow_redirects=True)
		status_code = int(getattr(response, "status_code", 0) or 0)
		if status_code in {401, 403}:
			raise AIRequestError("auth", "AI API Key 无效或无权访问模型列表", status_code)
		if status_code == 402:
			raise AIRequestError("token_quota", "AI Token 额度或账户余额不足", status_code)
		if status_code == 429:
			raise AIRequestError("rate_limit", "AI 服务当前被限流", status_code)
		if status_code >= 400:
			raise AIRequestError("request_failed", f"模型列表接口返回异常状态 {status_code}", status_code)
		models = _safe_models(_response_json(response))
	except Exception as exc:
		kind, message = _diagnostic_error(exc)
		stages.append(_stage("models", "fail", message, started, "基础检测未调用 chat completions。"))
		return {
			"ok": False,
			"billable": False,
			"normalized_base_url": normalized_base_url,
			"key_source": key_source,
			"current_model": model,
			"current_model_available": False,
			"models": [],
			"stages": stages,
			"error_kind": kind,
		}

	stages.append(_stage("models", "pass", "已读取模型列表", started, f"共 {len(models)} 个模型。"))
	available = any(item["id"] == model for item in models)
	if not available:
		available = any(item["id"].lower() == model.lower() for item in models)
	stages.append(_stage(
		"current_model",
		"pass" if available else "fail",
		"当前模型可用" if available else "当前模型不在模型列表中",
		started,
		"点击模型列表中的“使用该模型”后，再按现有保存流程更新配置。",
	))
	return {
		"ok": bool(available),
		"billable": False,
		"normalized_base_url": normalized_base_url,
		"key_source": key_source,
		"current_model": model,
		"current_model_available": available,
		"models": models,
		"stages": stages,
	}


def _extract_stream_json(text: str) -> dict[str, Any] | None:
	start = text.find("{")
	end = text.rfind("}") + 1
	if start < 0 or end <= start:
		return None
	try:
		payload = json.loads(text[start:end])
	except (TypeError, json.JSONDecodeError):
		return None
	return payload if isinstance(payload, dict) else None


def advanced_diagnose_ai(
	config: dict,
	*,
	confirmed: bool = False,
	stream: Callable[..., Any] | None = None,
	timeout: float = 30.0,
) -> dict[str, Any]:
	"""Run one short, user-confirmed streaming probe with synthetic input."""
	if not confirmed:
		raise ValueError("高级 AI 测试需要 confirmed=true，并会产生少量 Token。")
	ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
	service = get_ai_service(config)
	provider = str(ai_cfg.get("provider") or "")
	if provider != "openai_compatible":
		raise ValueError("高级流式测试目前仅支持 OpenAI 兼容服务")
	key = get_ai_api_key(config)
	base_url = normalize_openai_base_url(get_ai_base_url(config) or "", service)
	model = str(ai_cfg.get("model") or "").strip()
	if not key or not model:
		raise ValueError("请先配置 AI Key 和模型名称")
	started = time.monotonic()
	stages = [_stage("request", "pass", "已发出高级测试请求", started)]
	payload = {
		"model": model,
		"messages": [{"role": "user", "content": 'Return only JSON: {"score":75,"reason":"ok","missing":""}'}],
		"max_tokens": 32,
		"temperature": 0,
		"stream": True,
	}
	text_parts: list[str] = []
	stream_call = stream or httpx.stream
	try:
		with stream_call(
			"POST",
			f"{base_url.rstrip('/')}/chat/completions",
			headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
			json=payload,
			timeout=timeout,
		) as response:
			stages.append(_stage("response_headers", "pass", "已收到响应头", started, f"HTTP {getattr(response, 'status_code', 'unknown')}"))
			if int(getattr(response, "status_code", 0) or 0) >= 400:
				raise AIRequestError("request_failed", "高级测试接口返回异常状态", int(response.status_code))
			for line in response.iter_lines():
				line = line.decode("utf-8", "ignore") if isinstance(line, bytes) else str(line)
				if not line or line.startswith(":"):
					continue
				if line.startswith("data:"):
					line = line[5:].strip()
				if line == "[DONE]":
					break
				try:
					chunk = json.loads(line)
				except json.JSONDecodeError:
					continue
				choice = (chunk.get("choices") or [{}])[0]
				delta = choice.get("delta") or {}
				piece = delta.get("content") or choice.get("text") or ""
				if piece:
					if not text_parts:
						stages.append(_stage("first_chunk", "pass", "已收到首个内容块", started))
					text_parts.append(str(piece))
		stages.append(_stage("completed", "pass", "高级测试请求完成", started))
	except Exception as exc:
		kind, message = _diagnostic_error(exc)
		stages.append(_stage("completed", "fail", message, started))
		return {"ok": False, "billable": True, "error_kind": kind, "message": message, "stages": stages}

	parsed = _extract_stream_json("".join(text_parts))
	if not parsed or not {"score", "reason", "missing"}.issubset(parsed):
		return {
			"ok": False,
			"billable": True,
			"error_kind": "invalid_response",
			"message": "高级测试完成，但响应不是可解析的精简评分 JSON",
			"stages": stages,
		}
	return {"ok": True, "billable": True, "parsed": {"score": parsed["score"], "reason": str(parsed["reason"]), "missing": str(parsed["missing"])}, "stages": stages}
