"""智联招聘的浏览器动作适配器。

采集和投递/监测使用同一个 Browser Runtime，但页面动作必须与 BOSS
隔离。这里不假设固定的私有接口，只在当前页面上寻找可见的沟通控件，
并要求消息在会话 DOM 中再次出现后才报告成功。

智联页面 DOM 变化或出现登录墙、验证码、频率限制时，动作会安全失败；
不会把一次点击或一个成功返回值当成已发送证据。
"""

from __future__ import annotations

import json
import random
import time
from threading import Event
from typing import Any

from bosshunter.browser import (
    click_at,
    close_tab,
    evaluate,
    get_page_targets,
    new_tab,
    press_key,
    type_text,
    wait_for_load,
)
from bosshunter.db import add_history, get_db, update_job_status


ZHILIAN_CONTACT_JS = r"""
(() => {
    const visible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        return !!(rect.width && rect.height && style.display !== 'none'
            && style.visibility !== 'hidden' && style.pointerEvents !== 'none');
    };
    const textOf = (el) => String(el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    const hrefOf = (el) => String(el.getAttribute('href') || el.getAttribute('data-url') || '').trim();
    const candidates = Array.from(document.querySelectorAll(
        'a, button, [role="button"], [class*="chat"], [class*="commun"], [class*="contact"]'
    )).filter(visible).map((el, index) => {
        const text = textOf(el);
        const href = hrefOf(el);
        const excluded = /投递|申请职位|收藏|下载简历|举报|分享/.test(text);
        const chatText = /立即沟通|在线沟通|继续沟通|发消息|私聊|沟通/.test(text);
        const chatUrl = /chat|message|im|commun|contact/i.test(href);
        const rect = el.getBoundingClientRect();
        let score = 0;
        if (!excluded && chatText) score += 100;
        if (!excluded && chatUrl) score += 80;
        if (/立即沟通|在线沟通|继续沟通/.test(text)) score += 50;
        if (el.tagName.toLowerCase() === 'button' || el.tagName.toLowerCase() === 'a') score += 10;
        return {el, text, href, score, index, x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
    }).filter(item => item.score > 0).sort((a, b) => b.score - a.score);
    const item = candidates[0];
    if (!item) {
        return JSON.stringify({success: false, error: 'zhilian_contact_button_missing', candidates: candidates.slice(0, 12).map(item => ({text: item.text, href: item.href}))});
    }
    return JSON.stringify({success: true, x: item.x, y: item.y, href: item.href, text: item.text});
})()
"""


ZHILIAN_PAGE_STATE_JS = r"""
(() => {
    const text = document.body ? document.body.innerText || '' : '';
    const url = location.href || '';
    const blocked = text.match(/验证码|滑块|访问频繁|频率限制|账号异常|拒绝访问/);
    const login = /请先登录|请登录|登录后(?:查看|继续|获取)|登录失效|账号登录|扫码登录/.test(text);
    const inputs = Array.from(document.querySelectorAll(
        'textarea, [contenteditable="true"], input[placeholder*="消息"], input[placeholder*="沟通"]'
    )).some((el) => {
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        return !!(rect.width && rect.height && style.display !== 'none' && style.visibility !== 'hidden');
    });
    const hasChatUrl = /chat|message|im|commun|contact/i.test(url);
    const hasConversationText = /发送消息|请输入消息|沟通记录|聊天记录|在线沟通/.test(text);
    return JSON.stringify({
        status: blocked ? 'blocked' : login && !inputs ? 'login_required' : (hasChatUrl || inputs || hasConversationText) ? 'ready' : 'waiting',
        blocked_code: blocked ? blocked[0] : '',
        url,
        has_input: inputs,
        has_chat_url: hasChatUrl,
        has_conversation_text: hasConversationText,
    });
})()
"""


ZHILIAN_INPUT_STATE_JS = r"""
(() => {
    const visible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        return !!(rect.width && rect.height && style.display !== 'none' && style.visibility !== 'hidden');
    };
    const inputs = Array.from(document.querySelectorAll(
        'textarea, [contenteditable="true"], input[placeholder*="消息"], input[placeholder*="沟通"]'
    )).filter(visible);
    const input = inputs.find((el) => !/搜索|职位|公司/.test(el.getAttribute('placeholder') || ''));
    if (!input) return JSON.stringify({success: false, error: 'zhilian_message_input_missing'});
    const rect = input.getBoundingClientRect();
    return JSON.stringify({success: true, x: rect.x + rect.width / 2, y: rect.y + rect.height / 2});
})()
"""


def _parse_result(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {"success": False, "error": "empty_browser_response"}
    try:
        result = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {"success": False, "error": "invalid_browser_response"}
    return result if isinstance(result, dict) else {"success": False, "error": "invalid_browser_response"}


def _stop_event(config: dict) -> Event | None:
    event = config.get("_workbench_stop_event")
    return event if isinstance(event, Event) else None


def _wait_or_stop(config: dict, seconds: float) -> bool:
    event = _stop_event(config)
    if event:
        return event.wait(seconds)
    time.sleep(seconds)
    return False


def _state(target_id: str) -> dict[str, Any]:
    return _parse_result(evaluate(target_id, ZHILIAN_PAGE_STATE_JS))


def _action_result(target_id: str) -> dict[str, Any]:
    return _parse_result(evaluate(target_id, ZHILIAN_CONTACT_JS))


def _wait_for_conversation(target_id: str, config: dict, attempts: int = 20) -> dict[str, Any]:
    for _ in range(max(1, attempts)):
        if _wait_or_stop(config, 0.5):
            return {"success": False, "error": "stopped"}
        state = _state(target_id)
        if state.get("status") == "blocked":
            return {"success": False, "error": "blocked", "history_detail": "智联页面受到验证码或频率限制拦截"}
        if state.get("status") == "login_required":
            return {"success": False, "error": "login_required", "history_detail": "智联页面要求登录"}
        if state.get("status") == "ready":
            return {"success": True, **state}
    return {"success": False, "error": "zhilian_conversation_unverified", "history_detail": "智联沟通页面未能加载可验证的会话区域"}


def open_zhilian_conversation(job: dict, config: dict) -> str | None:
    """Open a Zhilian job conversation and return its Browser Runtime target."""
    if _stop_event(config) and _stop_event(config).is_set():
        return None
    job_url = str(job.get("url") or "").strip()
    if not job_url:
        return None
    existing_targets = {
        str(item.get("targetId") or "")
        for item in get_page_targets()
        if item.get("targetId")
    }
    target_id = new_tab(job_url, background=True)
    if not target_id:
        return None
    keep_open = False
    try:
        wait_for_load(target_id, timeout=10)
        initial = _wait_for_conversation(target_id, config, attempts=4)
        if initial.get("error") in {"blocked", "login_required"}:
            return None
        if config.get("_browse_before_greet"):
            browse_min = float(config.get("_browse_duration_min", 15) or 15)
            browse_max = float(config.get("_browse_duration_max", 30) or 30)
            if _wait_or_stop(config, random.uniform(min(browse_min, browse_max), max(browse_min, browse_max))):
                return None
        action = _action_result(target_id)
        if not action.get("success"):
            return None
        if _wait_or_stop(config, 1):
            return None
        if not click_at(target_id, f"{action['x']},{action['y']}"):
            return None

        ready = _wait_for_conversation(target_id, config)
        if ready.get("success"):
            keep_open = True
            return target_id

        # Some Zhilian controls open a new tab. Adopt it only when it is a
        # Zhilian page with a verified conversation state.
        for candidate in get_page_targets():
            candidate_id = str(candidate.get("targetId") or "")
            candidate_url = str(candidate.get("url") or "")
            if not candidate_id or candidate_id in existing_targets or candidate_id == target_id:
                continue
            if "zhaopin.com" not in candidate_url.lower():
                continue
            candidate_state = _wait_for_conversation(candidate_id, config, attempts=8)
            if candidate_state.get("success"):
                close_tab(target_id)
                keep_open = True
                return candidate_id
        return None
    finally:
        # The caller owns a successfully returned target. Failed attempts are
        # closed here so an unavailable action cannot leak background tabs.
        if target_id and not keep_open and _target_is_open(target_id):
            close_tab(target_id)


def _target_is_open(target_id: str) -> bool:
    return any(str(item.get("targetId") or "") == target_id for item in get_page_targets())


def _visible_message_input(target_id: str) -> dict[str, Any]:
    return _parse_result(evaluate(target_id, ZHILIAN_INPUT_STATE_JS))


def _send_button_state(target_id: str) -> dict[str, Any]:
    return _parse_result(evaluate(target_id, r"""
    (() => {
        const visible = (el) => {
            if (!el) return false;
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return !!(rect.width && rect.height && style.display !== 'none' && style.visibility !== 'hidden');
        };
        const buttons = Array.from(document.querySelectorAll('button, a, [role="button"], [class*="send"], [class*="submit"]'))
            .filter(visible)
            .filter((el) => /发送|提交|确定|继续沟通|立即沟通/.test((el.innerText || el.textContent || '').trim()))
            .filter((el) => !/投递|申请职位/.test((el.innerText || el.textContent || '').trim()));
        const button = buttons[0];
        if (!button) return JSON.stringify({success: false, error: 'zhilian_send_button_missing'});
        const rect = button.getBoundingClientRect();
        return JSON.stringify({success: true, x: rect.x + rect.width / 2, y: rect.y + rect.height / 2});
    })()
    """))


def _message_state(target_id: str, message: str) -> dict[str, Any]:
    expected = json.dumps(str(message or ""), ensure_ascii=False)
    return _parse_result(evaluate(target_id, f"""
    (() => {{
        const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
        const expected = normalize({expected});
        const nodes = Array.from(document.querySelectorAll(
            '.message-item, .chat-message, [data-message-id], [class*="message"]'
        ));
        const matches = nodes.filter((node) => normalize(node.innerText || node.textContent) === expected);
        const toast = /发送成功|消息已发送|发送成功/.test(document.body ? document.body.innerText || '' : '');
        return JSON.stringify({success: true, verified: matches.length > 0 || toast, matches: matches.length});
    }})()
    """))


def send_zhilian_message(target_id: str, message: str, config: dict) -> dict[str, Any]:
    """Send and verify one text message in a Zhilian conversation."""
    input_state = _visible_message_input(target_id)
    if not input_state.get("success"):
        return {**input_state, "history_detail": "智联沟通页面未找到消息输入框", "skip_backoff": True}
    if not click_at(target_id, f"{input_state['x']},{input_state['y']}"):
        return {"success": False, "error": "zhilian_input_focus_failed", "skip_backoff": True}
    if not press_key(target_id, "SelectAll") or not press_key(target_id, "Backspace"):
        return {"success": False, "error": "zhilian_input_clear_failed", "skip_backoff": True}
    if not type_text(target_id, message, human=True):
        return {"success": False, "error": "zhilian_trusted_input_failed", "skip_backoff": True}

    button_state = _send_button_state(target_id)
    if not button_state.get("success"):
        return {**button_state, "history_detail": "智联消息已填写，但未找到可验证的发送按钮", "skip_backoff": True}
    if not click_at(target_id, f"{button_state['x']},{button_state['y']}"):
        return {"success": False, "error": "zhilian_send_click_failed", "skip_backoff": True}

    for _ in range(20):
        if _wait_or_stop(config, 0.5):
            return {"success": False, "error": "stopped", "skip_backoff": True}
        state = _message_state(target_id, message)
        if state.get("verified"):
            return {"success": True, "verified": True}
    return {
        "success": False,
        "error": "zhilian_send_unverified",
        "history_detail": "智联已点击发送，但会话中未确认对应消息；未记录为已发送",
        "skip_backoff": True,
    }


def send_zhilian_greeting_once(job: dict, greeting: str, throttle_config: dict) -> tuple[dict[str, Any], str | None]:
    """Open, send and verify a Zhilian greeting for the shared sender."""
    config = {
        "_workbench_stop_event": throttle_config.get("_workbench_stop_event"),
        "_browse_before_greet": bool(throttle_config.get("browse_before_greet", True)),
        "_browse_duration_min": throttle_config.get("browse_duration_min", 15),
        "_browse_duration_max": throttle_config.get("browse_duration_max", 30),
    }
    target_id = open_zhilian_conversation(job, config)
    if not target_id:
        return {"success": False, "error": "zhilian_conversation_open_failed", "history_detail": "无法打开智联对应沟通页面", "skip_backoff": True}, None
    try:
        result = send_zhilian_message(target_id, greeting, config)
        return result, target_id if not result.get("success") else None
    finally:
        if _target_is_open(target_id):
            close_tab(target_id)


ZHILIAN_CONVERSATION_JS = r"""
(() => {
    const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
    const nodes = Array.from(document.querySelectorAll(
        '.message-item, .chat-message, [data-message-id], [class*="message"]'
    ));
    const results = [];
    nodes.forEach((node) => {
        const text = normalize(node.innerText || node.textContent);
        if (!text || text.length > 1000) return;
        const classes = String(node.className || '').toLowerCase();
        const marker = String(node.getAttribute('data-sender') || node.getAttribute('data-role') || '').toLowerCase();
        const isMe = /self|mine|my-message|item-my|right/.test(classes) || /self|mine|me|user/.test(marker);
        const isCard = /简历|附件|发送|申请/.test(text) && /请求|同意|获取|发给/.test(text);
        results.push({sender: isMe ? 'me' : 'hr', text: text.substring(0, 500), kind: isCard ? 'resume_request_card' : 'message'});
    });
    return JSON.stringify(results.slice(-80));
})()
"""


def extract_zhilian_conversation(target_id: str) -> list[dict[str, str]]:
    raw = evaluate(target_id, ZHILIAN_CONVERSATION_JS)
    result = _parse_result(raw)
    if result.get("success") is False:
        try:
            result = json.loads(raw) if isinstance(raw, str) else []
        except (TypeError, json.JSONDecodeError):
            result = []
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict) and item.get("text")]
    return []


def check_zhilian_replies(config: dict, jobs: list[dict]) -> list[dict[str, Any]]:
    """Inspect each tracked Zhilian conversation for a new HR message."""
    results: list[dict[str, Any]] = []
    for job in jobs:
        if _stop_event(config) and _stop_event(config).is_set():
            break
        target_id = open_zhilian_conversation(job, config)
        if not target_id:
            continue
        try:
            messages = extract_zhilian_conversation(target_id)
        finally:
            close_tab(target_id)
        if not messages or messages[-1].get("sender") != "hr":
            continue
        if job.get("status") == "sent":
            db = get_db()
            update_job_status(db, job["id"], "replied")
            add_history(db, job["id"], "replied", f"智联 HR 回复: {messages[-1].get('text', '')[:50]}")
            db.close()
        results.append({"job": job, "conversation": messages})
    return results
