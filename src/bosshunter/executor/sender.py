"""Sender module - Auto-send greetings with throttle control."""

import time
import json
from threading import Event
from urllib.parse import urljoin
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from bosshunter.browser import (
    new_tab,
    close_tab,
    evaluate,
    click_at,
    get_page_targets,
    navigate,
    press_key,
    type_text,
)
from bosshunter.db import get_db, get_jobs_ready_to_send, update_job_status, add_history, add_risk_event
from bosshunter.throttle import RequestThrottle, SendWindowChecker, ProgressiveBackoff, should_take_day_off

console = Console()

CHAT_BUTTON_SELECTOR = (
    'a[redirect-url*="/web/geek/chat"], '
    'a[data-url*="/friend/add"], '
    'a.btn-startchat, '
    '[ka="job_detail_chat"], '
    '[ka^="go_chat"], '
    '[ka*="gochat"], '
    '.op-btn-chat, '
    '.btn-startchat-wrap'
)

CHAT_BUTTON_SCRIPT_FOR_TESTS = """
(() => {
    const selectors = [
        'a[redirect-url*="/web/geek/chat"]',
        'a[data-url*="/friend/add"]',
        'a.btn-startchat',
        '[ka="job_detail_chat"]',
        '[ka^="go_chat"]',
        '[ka*="gochat"]',
        '.op-btn-chat',
        '.btn-startchat-wrap'
    ];
    const candidates = selectors.flatMap((selector, priority) =>
        Array.from(document.querySelectorAll(selector)).map((el) => ({el, selector, priority}))
    );
    const elementState = (el) => {
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        const visible = !!(
            rect.width && rect.height &&
            style.display !== 'none' &&
            style.visibility !== 'hidden' &&
            style.pointerEvents !== 'none'
        );
        const inViewport = visible && rect.bottom > 0 && rect.right > 0 &&
            rect.top < innerHeight && rect.left < innerWidth;
        const x = Math.min(Math.max(rect.x + rect.width / 2, 0), innerWidth - 1);
        const y = Math.min(Math.max(rect.y + rect.height / 2, 0), innerHeight - 1);
        const top = inViewport ? document.elementFromPoint(x, y) : null;
        const topmost = !!(top && (top === el || el.contains(top)));
        return {rect, visible, inViewport, topmost};
    };
    const isVisible = (el) => elementState(el).visible;
    const score = (item) => {
        const el = item.el;
        const text = (el.innerText || el.textContent || '').trim();
        const ka = el.getAttribute('ka') || '';
        const redirectUrl = el.getAttribute('redirect-url') || '';
        const dataUrl = el.getAttribute('data-url') || '';
        const tagName = String(el.tagName || '').toLowerCase();
        let value = 0;
        if (isVisible(el)) value += 1000;
        const state = elementState(el);
        if (state.inViewport) value += 500;
        if (state.topmost) value += 300;
        if (tagName === 'a') value += 200;
        if (redirectUrl.includes('/web/geek/chat')) value += 300;
        if (dataUrl.includes('/friend/add')) value += 250;
        if (el.classList && el.classList.contains('btn-startchat')) value += 120;
        if (text.includes('沟通')) value += 80;
        if (ka === 'job_detail_chat' || ka.includes('go_chat') || ka.includes('gochat')) value += 60;
        if (el.classList && el.classList.contains('btn-startchat-wrap')) value -= 100;
        return value - item.priority;
    };
    const matches = candidates
        .filter((item) => {
            const el = item.el;
            const text = (el.innerText || el.textContent || '').trim();
            const ka = el.getAttribute('ka') || '';
            const redirectUrl = el.getAttribute('redirect-url') || '';
            const dataUrl = el.getAttribute('data-url') || '';
            return (
                text.includes('沟通') ||
                redirectUrl.includes('/web/geek/chat') ||
                dataUrl.includes('/friend/add') ||
                ka === 'job_detail_chat' ||
                ka.includes('go_chat') ||
                ka.includes('gochat')
            );
        })
        .sort((a, b) => score(b) - score(a));
    const btn = matches[0] && matches[0].el;
    if (!btn) return JSON.stringify({
        success: false,
        error: 'no_chat_button',
        candidates: candidates.map((item) => {
            const el = item.el;
            const text = (el.innerText || el.textContent || '').trim();
            return {
                text,
                ka: el.getAttribute('ka'),
                className: String(el.className || ''),
                tagName: el.tagName,
                redirectUrl: el.getAttribute('redirect-url'),
                dataUrl: el.getAttribute('data-url'),
                visible: isVisible(el)
            };
        })
    });
    btn.scrollIntoView({block: 'center', inline: 'center'});
    const rect = btn.getBoundingClientRect();
    btn.click();
    return JSON.stringify({
        success: true,
        interaction: 'dom_click',
        x: rect.x + rect.width / 2,
        y: rect.y + rect.height / 2,
        button_text: (btn.innerText || btn.textContent || '').trim(),
        ka: btn.getAttribute('ka'),
        className: String(btn.className || ''),
        tagName: btn.tagName,
        redirectUrl: btn.getAttribute('redirect-url'),
        dataUrl: btn.getAttribute('data-url'),
        visible: isVisible(btn)
    });
})()
"""


def _parse_js_result(result) -> dict:
    if not result:
        return {"success": False, "error": "no_response"}
    if isinstance(result, dict):
        return result
    try:
        return json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return {"success": False, "error": "parse_error"}


def _stop_requested(stop_event) -> bool:
    return bool(stop_event and stop_event.is_set())


def _sleep_or_stop(seconds: float, stop_event) -> bool:
    if stop_event:
        return bool(stop_event.wait(seconds))
    time.sleep(seconds)
    return False


def _detect_greet_popup(target_id: str) -> dict:
    detect_popup_js = """
    (() => {
        const visible = (element) => element && !!(
            element.offsetWidth || element.offsetHeight || element.getClientRects().length
        );
        const explicitPreset = Array.from(document.querySelectorAll('.greet-boss-pop, .greet-pop'))
            .find((element) => visible(element));
        if (explicitPreset) {
            return JSON.stringify({success: true, popup: true, kind: 'preset_greeting'});
        }

        const startChat = Array.from(document.querySelectorAll('.dialog-wrap.startchat-dialog'))
            .find((element) => visible(element) && element.querySelector('textarea.input-area'));
        if (startChat) {
            return JSON.stringify({success: true, popup: true, kind: 'startchat_dialog'});
        }

        const preset = Array.from(document.querySelectorAll('.dialog-wrap'))
            .find((element) => {
                if (!visible(element)) return false;
                const text = (element.innerText || element.textContent || '').trim();
                const hasEditableGreeting = !!element.querySelector('textarea.input-area');
                return !hasEditableGreeting && /预设招呼语|默认招呼语|自动招呼语|打招呼语/.test(text);
            });
        if (preset) {
            return JSON.stringify({success: true, popup: true, kind: 'preset_greeting'});
        }
        return JSON.stringify({success: true, popup: false, kind: null});
    })()
    """
    return _parse_js_result(evaluate(target_id, detect_popup_js))


def _is_preset_greeting_popup(state: dict) -> bool:
    if state.get("action") == "no_popup":
        return False
    if not state.get("popup"):
        return False
    return state.get("kind") in {None, "preset_greeting"}


def _confirm_preset_greeting(target_id: str) -> dict:
    result = _parse_js_result(evaluate(target_id, """
    (() => {
        const visible = (element) => {
            if (!element) return false;
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return !!(rect.width && rect.height && style.display !== 'none'
                && style.visibility !== 'hidden' && style.pointerEvents !== 'none');
        };
        const dialogs = Array.from(document.querySelectorAll('.greet-boss-pop, .greet-pop, .dialog-wrap'))
            .filter(visible);
        const popup = dialogs.find((element) =>
            element.matches('.greet-boss-pop, .greet-pop')
            || /预设招呼语|默认招呼语|自动招呼语|打招呼语/.test(
                (element.innerText || element.textContent || '').trim()
            )
        );
        if (!popup) return JSON.stringify({success: false, error: 'preset_popup_missing'});
        const buttons = Array.from(popup.querySelectorAll(
            '[ka="dialog_confirm"], .btn-sure, button, [role="button"]'
        )).filter((element) => {
            if (!visible(element) || element.disabled || element.classList.contains('disabled')) return false;
            const text = (element.innerText || element.textContent || '').trim();
            return element.matches('[ka="dialog_confirm"], .btn-sure')
                || /确定|确认|继续|开始沟通|立即沟通/.test(text);
        });
        const button = buttons[0];
        if (!button) return JSON.stringify({success: false, error: 'preset_confirm_missing'});
        button.scrollIntoView({block: 'center', inline: 'center'});
        button.click();
        return JSON.stringify({success: true, action: 'preset_confirmed'});
    })()
    """))
    if not result.get("success"):
        return {
            **result,
            "history_detail": "检测到平台招呼语，但无法确认招呼语弹窗",
            "skip_backoff": True,
        }
    return {"success": True, "action": "preset_confirmed"}


def _submit_startchat_greeting(target_id: str, greeting: str) -> dict:
    greeting_escaped = json.dumps(greeting, ensure_ascii=False)
    input_state = _parse_js_result(evaluate(target_id, """
    (() => {
        const visible = (element) => {
            if (!element) return false;
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return !!(rect.width && rect.height && style.display !== 'none'
                && style.visibility !== 'hidden' && style.pointerEvents !== 'none');
        };
        const dialog = Array.from(document.querySelectorAll('.dialog-wrap.startchat-dialog'))
            .find(visible);
        const input = dialog && Array.from(dialog.querySelectorAll('textarea.input-area, textarea'))
            .find(visible);
        if (!input) return JSON.stringify({success: false, error: 'startchat_input_missing'});
        input.scrollIntoView({block: 'center', inline: 'center'});
        const rect = input.getBoundingClientRect();
        return JSON.stringify({
            success: true,
            x: rect.x + rect.width / 2,
            y: rect.y + rect.height / 2
        });
    })()
    """))
    if not input_state.get("success"):
        return {
            **input_state,
            "history_detail": "首次沟通弹窗中未找到招呼语输入框",
            "skip_backoff": True,
        }
    if not click_at(target_id, f"{input_state['x']},{input_state['y']}"):
        return {"success": False, "error": "startchat_input_focus_failed", "skip_backoff": True}
    if not press_key(target_id, "SelectAll") or not press_key(target_id, "Backspace"):
        return {"success": False, "error": "startchat_input_clear_failed", "skip_backoff": True}
    if not type_text(target_id, greeting, human=True):
        return {"success": False, "error": "startchat_trusted_input_failed", "skip_backoff": True}

    submit_state = _parse_js_result(evaluate(target_id, f"""
    (() => {{
        const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
        const visible = (element) => {{
            if (!element) return false;
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return !!(rect.width && rect.height && style.display !== 'none'
                && style.visibility !== 'hidden' && style.pointerEvents !== 'none');
        }};
        const dialog = Array.from(document.querySelectorAll('.dialog-wrap.startchat-dialog'))
            .find(visible);
        const input = dialog && Array.from(dialog.querySelectorAll('textarea.input-area, textarea'))
            .find(visible);
        if (!input || normalize(input.value) !== normalize({greeting_escaped})) {{
            return JSON.stringify({{success: false, error: 'startchat_input_not_filled'}});
        }}
        const buttons = Array.from(dialog.querySelectorAll(
            '.send-message, [ka="dialog_confirm"], .btn-sure, .btn-send, '
            + '.send-btn, button, [role="button"]'
        )).filter((element) => {{
            if (!visible(element) || element.disabled || element.classList.contains('disabled')) return false;
            const text = (element.innerText || element.textContent || '').trim();
            return element.matches(
                '.send-message, [ka="dialog_confirm"], .btn-sure, .btn-send, .send-btn'
            ) || /发送|确定|开始沟通|立即沟通/.test(text);
        }});
        const button = buttons[0];
        if (!button) return JSON.stringify({{success: false, error: 'startchat_submit_missing'}});
        button.scrollIntoView({{block: 'center', inline: 'center'}});
        const rect = button.getBoundingClientRect();
        return JSON.stringify({{
            success: true,
            x: rect.x + rect.width / 2,
            y: rect.y + rect.height / 2
        }});
    }})()
    """))
    if not submit_state.get("success"):
        return {
            **submit_state,
            "history_detail": "首次沟通招呼语未被 BOSS 输入组件接受",
            "skip_backoff": True,
        }
    if not click_at(target_id, f"{submit_state['x']},{submit_state['y']}"):
        return {
            "success": False,
            "error": "startchat_submit_click_failed",
            "history_detail": "首次沟通招呼语已填写，但真实提交点击失败",
            "skip_backoff": True,
        }
    return {"success": True, "action": "first_contact_submitted"}


def _navigate_to_chat_redirect(target_id: str, click_result: dict) -> bool:
    """Reuse BOSS's own chat destination without foregrounding the job tab."""
    redirect_url = str(click_result.get("redirectUrl") or "").strip()
    if not redirect_url.startswith("/web/geek/chat"):
        return False
    return navigate(target_id, urljoin("https://www.zhipin.com", redirect_url))


def _handle_greet_popup(target_id: str, greeting: str, click_result: dict | None = None) -> dict:
    state = _detect_greet_popup(target_id)
    if not state.get("success"):
        return {
            "success": False,
            "error": state.get("error", "popup_detection_failed"),
            "history_detail": "无法识别首次沟通页面状态",
            "skip_backoff": True,
        }
    if _is_preset_greeting_popup(state):
        return _confirm_preset_greeting(target_id)
    if state.get("kind") == "startchat_dialog":
        if click_result and _navigate_to_chat_redirect(target_id, click_result):
            return {"success": True, "action": "startchat_redirected"}
        return {
            "success": False,
            "error": "startchat_redirect_unavailable",
            "history_detail": "首次沟通弹窗缺少可验证的聊天地址，已停止发送且未切换前台",
            "skip_backoff": True,
        }
    return {"success": True, "action": "no_popup"}


def _chat_target_matches_job(target_id: str, job: dict) -> bool:
    job_id = json.dumps(str(job.get("id") or ""), ensure_ascii=False)
    company = json.dumps(str(job.get("company") or ""), ensure_ascii=False)
    title = json.dumps(str(job.get("title") or ""), ensure_ascii=False)
    result = _parse_js_result(evaluate(target_id, f"""
    (() => {{
        const normalize = (value) => String(value || '').replace(/\\s+/g, '').toLowerCase();
        const expectedId = normalize({job_id});
        const expectedCompany = normalize({company});
        const expectedTitle = normalize({title});
        const activeRoots = Array.from(new Set([
            document.querySelector('.chat-conversation'),
            document.querySelector('.friend-content.selected')
        ].filter(Boolean)));
        const activeText = normalize(
            activeRoots.map((element) => element.innerText || element.textContent || '').join(' ')
        );
        const activeHtml = activeRoots.map((element) => element.outerHTML || '').join(' ');
        const idMatch = !!expectedId && (
            location.href.includes(expectedId) ||
            activeHtml.includes(expectedId)
        );
        const identityMatch = !!expectedCompany && activeText.includes(expectedCompany) &&
            (!expectedTitle || activeText.includes(expectedTitle));
        return JSON.stringify({{success: true, matches: idMatch || identityMatch}});
    }})()
    """))
    return bool(result.get("success") and result.get("matches"))


def _wait_for_chat_page(
    target_id: str,
    stop_event,
    attempts: int = 20,
    job: dict | None = None,
    excluded_target_ids: set[str] | None = None,
) -> dict:
    for _ in range(attempts):
        if _sleep_or_stop(0.5, stop_event):
            close_tab(target_id)
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}
        url_now = evaluate(target_id, "location.pathname")
        if url_now and "/web/geek/chat" in url_now:
            if job is None or _chat_target_matches_job(target_id, job):
                return {"success": True, "target_id": target_id}
        if job is not None:
            for candidate in get_page_targets():
                candidate_id = str(candidate.get("targetId") or "")
                candidate_url = str(candidate.get("url") or "")
                if (
                    candidate_id
                    and candidate_id != target_id
                    and "/web/geek/chat" in candidate_url
                    and _chat_target_matches_job(candidate_id, job)
                ):
                    return {"success": True, "target_id": candidate_id, "opened_new_tab": True}
    return {"success": False, "error": "chat_navigation_timeout"}


def _click_chat_button(target_id: str, stop_event, attempts: int = 30) -> dict:
    click_chat_js = CHAT_BUTTON_SCRIPT_FOR_TESTS

    last_result: dict = {"success": False, "error": "no_chat_button"}
    for attempt in range(max(1, attempts)):
        if _stop_requested(stop_event):
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}
        last_result = _parse_js_result(evaluate(target_id, click_chat_js))
        if last_result.get("success"):
            return last_result
        if attempt < attempts - 1 and _sleep_or_stop(1, stop_event):
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}

    return last_result


def _adopt_chat_target(current_target_id: str, chat_ready: dict) -> str:
    """Switch to a matching chat tab and close the superseded job tab."""
    next_target_id = str(chat_ready.get("target_id") or current_target_id)
    if next_target_id != current_target_id:
        close_tab(current_target_id)
    return next_target_id


def _message_delivery_state(target_id: str, greeting: str) -> str:
    greeting_escaped = json.dumps(greeting, ensure_ascii=False)
    result = _parse_js_result(evaluate(target_id, f"""
    (() => {{
        const normalize = (value) => String(value || '')
            .replace(/[\\u200b-\\u200f\\ufeff]/g, '')
            .replace(/\\s+/g, ' ')
            .trim();
        const removeDeliveryLabels = (value) => normalize(value)
            .replace(/(发送中|已读|未读|送达|发送成功|重试|重新发送)$/g, '')
            .trim();
        const textMatchesExpected = (value, expectedText) => {{
            const text = removeDeliveryLabels(value);
            return text === expectedText || text.includes(expectedText);
        }};
        const messageText = (node) => {{
            const contentNode = node.querySelector(
                '.message-content, .text, .content, .message-text, '
                + '[class*="message-content"], [class*="msg-content"], [class*="text"]'
            );
            return normalize(contentNode ? contentNode.innerText || contentNode.textContent : node.innerText || node.textContent);
        }};
        const expected = normalize({greeting_escaped});
        const ownMessages = Array.from(document.querySelectorAll(
            '.chat-record .message-item.item-myself, .chat-record .item-myself, '
            + '.chat-record .message-item.item-self, .chat-record [class*="item-my"]'
        ));
        const matching = ownMessages.filter((node) => textMatchesExpected(messageText(node), expected));
        const messageList = document.querySelector('.chat-record');
        const vue = messageList && messageList.__vue__;
        const records = vue && Array.isArray(vue.list$) ? vue.list$ : [];
        const matchingRecords = records.filter((message) => {{
            if (!message || !message.isSelf) return false;
            const text = message.text || message.lastText || message.content || message.message || message.body || '';
            return textMatchesExpected(text, expected);
        }});
        if (!matching.length && !matchingRecords.length) {{
            return JSON.stringify({{success: true, state: 'missing'}});
        }}
        const domStates = matching.map((node) => {{
            const statusNode = node.querySelector('.message-status');
            const statusClass = statusNode ? String(statusNode.className || '') : '';
            if (statusClass.includes('status-error')) return 'failed';
            if (statusClass.includes('status-loading')) return 'pending';
            return 'delivered';
        }});
        const recordStates = matchingRecords.map((record) => {{
            const status = Number(record.status);
            if (status === 4) return 'failed';
            if (status === 0) return 'pending';
            return 'delivered';
        }});
        const states = [...domStates, ...recordStates];
        const state = states.includes('delivered') ? 'delivered'
            : states.includes('pending') ? 'pending' : 'failed';
        return JSON.stringify({{success: true, state}});
    }})()
    """))
    if not result.get("success"):
        return "missing"
    return str(result.get("state") or "missing")


def _submitted_message_looks_accepted(target_id: str, greeting: str) -> bool:
    """Return true when Boss appears to have accepted a send despite missing echo."""
    greeting_escaped = json.dumps(greeting, ensure_ascii=False)
    result = _parse_js_result(evaluate(target_id, f"""
    (() => {{
        const normalize = (value) => String(value || '')
            .replace(/[\\u200b-\\u200f\\ufeff]/g, '')
            .replace(/\\s+/g, ' ')
            .trim();
        const expected = normalize({greeting_escaped});
        const input = document.querySelector('#chat-input');
        const inputText = normalize(input ? input.innerText || input.textContent || input.value : '');
        const inputCleared = !!input && inputText.length === 0;
        const ownMessages = Array.from(document.querySelectorAll(
            '.chat-record .message-item.item-myself, .chat-record .item-myself, '
            + '.chat-record .message-item.item-self, .chat-record [class*="item-my"]'
        ));
        const hasFailedOwnMessage = ownMessages.some((node) => {{
            const statusNode = node.querySelector('.message-status');
            const statusClass = statusNode ? String(statusNode.className || '') : '';
            const text = normalize(node.innerText || node.textContent);
            return statusClass.includes('status-error')
                || (text.includes('发送失败') && (text.includes(expected) || expected.includes(text)));
        }});
        return JSON.stringify({{
            success: true,
            accepted: inputCleared && !hasFailedOwnMessage,
            inputCleared,
            hasFailedOwnMessage
        }});
    }})()
    """))
    return bool(result.get("success") and result.get("accepted"))


def _submit_chat_message_background(target_id: str, greeting: str) -> dict:
    """Use BossHunter's original Vue submit path without foregrounding Chrome."""
    greeting_escaped = json.dumps(greeting, ensure_ascii=False)
    result = _parse_js_result(evaluate(target_id, f"""
    (() => {{
        const input = document.querySelector('#chat-input');
        if (!input) return JSON.stringify({{success: false, error: 'no_chat_input'}});

        let vue = null;
        let element = input;
        for (let index = 0; index < 15 && element; index += 1) {{
            if (element.__vue__) {{
                vue = element.__vue__;
                break;
            }}
            element = element.parentElement;
        }}
        if (!vue || typeof vue.handleSubmit !== 'function') {{
            return JSON.stringify({{success: false, error: 'legacy_submit_unavailable'}});
        }}

        input.innerText = {greeting_escaped};
        input.dispatchEvent(new InputEvent('input', {{
            bubbles: true,
            inputType: 'insertText',
            data: {greeting_escaped}
        }}));
        if (vue._data) vue._data.enableSubmit = true;
        vue.handleSubmit();
        return JSON.stringify({{success: true, action: 'chat_submitted_background'}});
    }})()
    """))
    if result.get("success"):
        return {"success": True, "action": "chat_submitted_background"}
    return result


def _fill_chat_input(target_id: str, greeting: str) -> dict:
    greeting_escaped = json.dumps(greeting, ensure_ascii=False)
    input_state = _parse_js_result(evaluate(target_id, """
    (() => {
        const input = document.querySelector('#chat-input');
        if (!input) return JSON.stringify({success: false, error: 'no_chat_input'});
        return JSON.stringify({success: true});
    })()
    """))
    if not input_state.get("success"):
        return input_state
    if not click_at(target_id, "#chat-input"):
        return {"success": False, "error": "chat_input_focus_failed"}
    if not press_key(target_id, "SelectAll") or not press_key(target_id, "Backspace"):
        return {"success": False, "error": "chat_input_clear_failed"}
    if not type_text(target_id, greeting, human=True):
        return {"success": False, "error": "trusted_input_failed"}

    result = _parse_js_result(evaluate(target_id, f"""
    (() => {{
        const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
        const input = document.querySelector('#chat-input');
        if (!input) return JSON.stringify({{success: false, error: 'no_chat_input'}});
        const sendButton = document.querySelector('.btn-send');
        const disabled = !sendButton || sendButton.disabled || sendButton.classList.contains('disabled');
        const matches = normalize(input.innerText || input.textContent) === normalize({greeting_escaped});
        return JSON.stringify({{
            success: matches,
            error: matches ? null : 'input_not_filled',
            send_button: !!sendButton,
            disabled
        }});
    }})()
    """))
    if result.get("success") and result.get("disabled"):
        if type_text(target_id, " ") and press_key(target_id, "Backspace"):
            result = _parse_js_result(evaluate(target_id, f"""
            (() => {{
                const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const input = document.querySelector('#chat-input');
                const sendButton = document.querySelector('.btn-send');
                const disabled = !sendButton || sendButton.disabled || sendButton.classList.contains('disabled');
                const matches = !!input && normalize(input.innerText || input.textContent) === normalize({greeting_escaped});
                return JSON.stringify({{success: matches, error: matches ? null : 'input_not_filled', disabled}});
            }})()
            """))
    return result


# JS: 在岗位详情页点击"立即沟通"并发送招呼语
JS_SEND_GREETING = """
(async (greeting) => {
    // 找到"立即沟通"按钮
    const btn = document.querySelector('.btn-startchat, .op-btn-chat, [ka="job_detail_chat"]');
    if (!btn) return JSON.stringify({success: false, error: 'no_chat_button'});

    btn.click();
    await new Promise(r => setTimeout(r, 2000));

    // 等待聊天输入框出现
    const input = document.querySelector('.chat-input textarea, .chat-input [contenteditable], .input-area textarea');
    if (!input) return JSON.stringify({success: false, error: 'no_input_box'});

    // 输入招呼语
    if (input.tagName === 'TEXTAREA') {
        input.value = greeting;
        input.dispatchEvent(new Event('input', {bubbles: true}));
    } else {
        input.innerHTML = greeting;
        input.dispatchEvent(new Event('input', {bubbles: true}));
    }

    await new Promise(r => setTimeout(r, 500));

    // 点击发送
    const sendBtn = document.querySelector('.btn-send, .send-btn, [class*="send"]');
    if (sendBtn) {
        sendBtn.click();
        await new Promise(r => setTimeout(r, 1000));
        return JSON.stringify({success: true});
    }

    return JSON.stringify({success: false, error: 'no_send_button'});
})(arguments[0])
"""


def _send_greeting_once(job: dict, greeting: str, throttle_config: dict) -> tuple[dict, str | None]:
    stop_event = throttle_config.get("_workbench_stop_event")
    existing_target_ids = {
        str(target.get("targetId") or "")
        for target in get_page_targets()
        if target.get("targetId")
    }
    target_id = new_tab(job["url"], background=True)
    if not target_id:
        return {"success": False, "error": "open_page_failed", "history_detail": "无法打开页面", "skip_backoff": True}, None

    if _stop_requested(stop_event):
        close_tab(target_id)
        return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None

    browse_min = throttle_config.get("browse_duration_min", 15)
    browse_max = throttle_config.get("browse_duration_max", 30)
    if throttle_config.get("browse_before_greet", True):
        import random
        browse_time = random.uniform(browse_min, browse_max)
        if _sleep_or_stop(browse_time, stop_event):
            close_tab(target_id)
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None

    page_check_js = """
    (() => {
        const text = document.body ? document.body.innerText : '';
        const title = document.title || '';
        if (
            title.includes('访问的页面不存在') ||
            text.includes('您访问的页面不存在') ||
            text.includes('Oops!')
        ) {
            return JSON.stringify({
                success: false,
                error: 'job_page_unavailable',
                history_detail: '岗位页面不存在或已下架',
                skip_backoff: true
            });
        }
        return JSON.stringify({success: true});
    })()
    """
    page_check = _parse_js_result(evaluate(target_id, page_check_js))
    if not page_check.get("success"):
        close_tab(target_id)
        return page_check, None

    chat_button_attempts = int(throttle_config.get("_chat_button_attempts", 30))
    result1a = _click_chat_button(target_id, stop_event, chat_button_attempts)
    if not result1a.get("success"):
        close_tab(target_id)
        return {"success": False, "error": "no_chat_button", "history_detail": "无法找到沟通按钮", "skip_backoff": True}, None

    if _sleep_or_stop(4, stop_event):
        close_tab(target_id)
        return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None

    popup_result = _handle_greet_popup(target_id, greeting, result1a)
    if not popup_result.get("success"):
        return popup_result, target_id
    contact_action = str(popup_result.get("action") or "no_popup")

    navigation_attempts = int(throttle_config.get("_chat_navigation_attempts", 20))
    chat_ready = _wait_for_chat_page(
        target_id,
        stop_event,
        navigation_attempts,
        job,
        excluded_target_ids=existing_target_ids,
    )
    if chat_ready.get("success"):
        target_id = _adopt_chat_target(target_id, chat_ready)
    if chat_ready.get("error") == "stopped":
        return chat_ready, None
    if not chat_ready.get("success") and contact_action not in {
        "first_contact_submitted",
        "startchat_redirected",
    }:
        console.print("[yellow]    ! 沟通按钮未跳转聊天页，尝试真实点击兜底[/yellow]")
        if click_at(target_id, CHAT_BUTTON_SELECTOR):
            if _sleep_or_stop(1, stop_event):
                close_tab(target_id)
                return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
            popup_result = _handle_greet_popup(target_id, greeting, result1a)
            if not popup_result.get("success"):
                return popup_result, target_id
            contact_action = str(popup_result.get("action") or "no_popup")
            chat_ready = _wait_for_chat_page(
                target_id,
                stop_event,
                navigation_attempts,
                job,
                excluded_target_ids=existing_target_ids,
            )
            if chat_ready.get("success"):
                target_id = _adopt_chat_target(target_id, chat_ready)
            if chat_ready.get("error") == "stopped":
                return chat_ready, None

    if not chat_ready.get("success"):
        if contact_action == "first_contact_submitted":
            return {
                "success": False,
                "error": "first_contact_navigation_unverified",
                "history_detail": "首次招呼语已经提交，但未能进入对应会话验证结果；为避免重复发送，请人工检查",
                "skip_backoff": True,
            }, target_id
        return {
            "success": False,
            "error": "no_chat_input",
            "history_detail": "发送失败: 未进入具体聊天会话，可能是BOSS继续沟通跳转失败",
            "skip_backoff": True,
        }, target_id

    verification_attempts = int(throttle_config.get("_send_verification_attempts", 20))
    if contact_action == "first_contact_submitted":
        for _ in range(max(1, verification_attempts)):
            if _sleep_or_stop(0.5, stop_event):
                close_tab(target_id)
                return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
            delivery_state = _message_delivery_state(target_id, greeting)
            if delivery_state == "failed":
                return {
                    "success": False,
                    "error": "first_contact_send_rejected",
                    "history_detail": "首次沟通招呼语被标记为发送失败",
                    "skip_backoff": True,
                }, target_id
            if delivery_state == "delivered":
                if _sleep_or_stop(2, stop_event):
                    close_tab(target_id)
                    return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
                if _message_delivery_state(target_id, greeting) == "delivered":
                    close_tab(target_id)
                    return {
                        "success": True,
                        "verified": True,
                        "first_contact": True,
                    }, None
                return {
                    "success": False,
                    "error": "first_contact_send_not_stable",
                    "history_detail": "首次招呼语曾出现但未稳定保留在会话中",
                    "skip_backoff": True,
                }, target_id
        if _submitted_message_looks_accepted(target_id, greeting):
            close_tab(target_id)
            return {
                "success": True,
                "accepted_without_echo": True,
                "first_contact": True,
            }, None
        return {
            "success": False,
            "error": "first_contact_delivery_unverified",
            "history_detail": "首次招呼语已提交，但会话中未确认对应消息；为避免重复发送，请人工检查",
            "skip_backoff": True,
        }, target_id

    existing_state = _message_delivery_state(target_id, greeting)
    if existing_state == "delivered":
        close_tab(target_id)
        return {"success": True, "already_present": True, "verified": True}, None
    if existing_state in {"pending", "failed"}:
        return {
            "success": False,
            "error": f"existing_message_{existing_state}",
            "history_detail": f"会话中已有状态为 {existing_state} 的相同消息，请人工检查",
            "skip_backoff": True,
        }, target_id

    submit_result = _submit_chat_message_background(target_id, greeting)
    if not submit_result.get("success"):
        input_result = _fill_chat_input(target_id, greeting)
        if not input_result.get("success"):
            return input_result, target_id
        if input_result.get("disabled"):
            return {
                "success": False,
                "error": "send_button_unavailable",
                "history_detail": "招呼语已填入，但发送按钮不可用，未标记成功",
                "skip_backoff": True,
            }, target_id
        if not click_at(target_id, ".btn-send:not(.disabled)"):
            return {
                "success": False,
                "error": "send_button_click_failed",
                "history_detail": "招呼语已填入，但发送按钮点击失败，未标记成功",
                "skip_backoff": True,
            }, target_id

    for _ in range(max(1, verification_attempts)):
        if _sleep_or_stop(0.5, stop_event):
            close_tab(target_id)
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
        delivery_state = _message_delivery_state(target_id, greeting)
        if delivery_state == "failed":
            return {
                "success": False,
                "error": "send_rejected_after_click",
                "history_detail": "BOSS 将消息标记为发送失败，未记录为已发送",
                "skip_backoff": True,
            }, target_id
        if delivery_state == "delivered":
            if _sleep_or_stop(2, stop_event):
                close_tab(target_id)
                return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
            if _message_delivery_state(target_id, greeting) == "delivered":
                close_tab(target_id)
                return {"success": True, "verified": True}, None
            return {
                "success": False,
                "error": "send_not_stable",
                "history_detail": "消息曾出现但未稳定保留在会话中，未记录为已发送",
                "skip_backoff": True,
            }, target_id

    if _submitted_message_looks_accepted(target_id, greeting):
        close_tab(target_id)
        return {"success": True, "accepted_without_echo": True}, None

    return {
        "success": False,
        "error": "send_not_confirmed",
        "history_detail": "已点击发送，但会话中未确认对应招呼语，未记录为已发送",
        "skip_backoff": True,
    }, target_id


def send_greetings(config: dict, force: bool = False) -> int:
    """Send generated greetings. Returns count of successfully sent."""
    db = get_db()
    throttle_config = config.get("throttle", {})
    stop_event = config.get("_workbench_stop_event")
    workbench_job_ids = {str(job_id) for job_id in config.get("_workbench_job_ids", [])}
    send_report = {
        "requested_count": len(workbench_job_ids),
        "eligible_count": 0,
        "scheduled_count": 0,
        "attempted_count": 0,
        "sent_count": 0,
        "failed_count": 0,
        "deferred_count": len(workbench_job_ids),
        "quota_deferred_count": 0,
        "stop_reason": None,
    }
    # Keep the integer return value for CLI/backward compatibility while giving
    # the web workflow enough detail to distinguish failures from quota deferrals.
    config["_workbench_send_report"] = send_report
    if isinstance(stop_event, Event):
        throttle_config = dict(throttle_config)
        throttle_config["_workbench_stop_event"] = stop_event

    # Anti-ban: random day off (可通过 --force 跳过)
    day_off_prob = throttle_config.get("day_off_probability", 0.05)
    if not force and should_take_day_off(day_off_prob):
        console.print("[yellow]🎲 今日随机休息（防检测），跳过发送[/yellow]")
        add_risk_event(db, "day_off", "随机休息日")
        send_report["stop_reason"] = "day_off"
        db.close()
        return 0

    # Anti-ban: send window check (可通过 --force 跳过)
    send_windows = throttle_config.get("send_windows", [])
    window_checker = SendWindowChecker(send_windows)
    if not force and not window_checker.is_active():
        info = window_checker.next_window_info()
        console.print("[yellow]⏰ 当前不在发送时间窗口内，暂不发送[/yellow]")
        console.print(f"[dim]  {info}[/dim]")
        add_risk_event(db, "outside_window", info)
        send_report["stop_reason"] = "outside_window"
        db.close()
        return 0

    jobs = get_jobs_ready_to_send(db)
    if workbench_job_ids:
        jobs = [job for job in jobs if str(job["id"]) in workbench_job_ids]
    send_report["eligible_count"] = len(jobs)
    send_report["deferred_count"] = len(workbench_job_ids) if workbench_job_ids else len(jobs)

    if not jobs:
        console.print("[yellow]没有已生成招呼语的待发送岗位，请先运行 bosshunter greet[/yellow]")
        send_report["stop_reason"] = "no_ready_jobs"
        db.close()
        return 0

    # Check daily limit
    daily_limit = throttle_config.get("daily_limit", 30)
    interval_min = throttle_config.get("interval_min", 60)
    interval_max = throttle_config.get("interval_max", 180)

    # Count today's sent
    today_sent = db.execute(
        "SELECT COUNT(*) as cnt FROM history WHERE action='sent' AND date(created_at)=date('now')"
    ).fetchone()
    already_sent = today_sent["cnt"] if today_sent else 0

    remaining_quota = daily_limit - already_sent
    if remaining_quota <= 0:
        console.print(f"[yellow]今日已达发送上限 ({daily_limit})[/yellow]")
        send_report["quota_deferred_count"] = len(jobs)
        send_report["stop_reason"] = "daily_limit"
        db.close()
        return 0

    jobs_to_send = jobs[:remaining_quota]
    send_report["scheduled_count"] = len(jobs_to_send)
    send_report["quota_deferred_count"] = max(len(jobs) - len(jobs_to_send), 0)
    if send_report["quota_deferred_count"]:
        send_report["stop_reason"] = "daily_limit"
    throttle = RequestThrottle(delay_min=interval_min, delay_max=interval_max)
    backoff = ProgressiveBackoff()
    sent_count = 0

    console.print(f"[bold]准备发送 {len(jobs_to_send)} 条招呼语[/bold] (今日已发 {already_sent}/{daily_limit})")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console
    ) as progress:
        task = progress.add_task("发送中", total=len(jobs_to_send))

        for job in jobs_to_send:
            if _stop_requested(stop_event):
                console.print("[yellow]已请求停止，结束发送[/yellow]")
                send_report["stop_reason"] = "stopped"
                break

            greeting = job.get("greeting", "")
            if not greeting:
                update_job_status(db, job["id"], "error")
                send_report["attempted_count"] += 1
                send_report["failed_count"] += 1
                progress.update(task, advance=1)
                continue

            # Wait between sends (except first)
            if sent_count > 0:
                progress.update(task, description="等待间隔...")
                if throttle.wait(stop_event):
                    console.print("[yellow]已请求停止，结束发送[/yellow]")
                    send_report["stop_reason"] = "stopped"
                    break

            progress.update(task, description=f"发送: {job['company'][:10]} - {job['title'][:15]}")

            result_data, failed_target_id = _send_greeting_once(job, greeting, throttle_config)
            if result_data.get("error") == "stopped":
                send_report["stop_reason"] = "stopped"
                break
            if result_data.get("error") == "no_chat_input" and failed_target_id:
                console.print("[yellow]    ! 未进入具体聊天会话，重新打开岗位页再试一次[/yellow]")
                close_tab(failed_target_id)
                result_data, failed_target_id = _send_greeting_once(job, greeting, throttle_config)
                if result_data.get("error") == "stopped":
                    send_report["stop_reason"] = "stopped"
                    break

            send_report["attempted_count"] += 1

            if not result_data.get("success"):
                console.print(f"[yellow]    ! 发送失败，已记录并关闭任务页面: {result_data.get('error', 'unknown')}[/yellow]")
                if failed_target_id:
                    close_tab(failed_target_id)
                    failed_target_id = None

            if result_data.get("success"):
                throttle.mark()
                update_job_status(db, job["id"], "sent")
                add_history(db, job["id"], "sent", greeting[:50])
                sent_count += 1
                send_report["sent_count"] = sent_count
                backoff.record_success()
            else:
                error = result_data.get("error", "unknown")
                send_report["failed_count"] += 1
                update_job_status(db, job["id"], "error")
                add_history(db, job["id"], "error", result_data.get("history_detail", f"发送失败: {error}"))
                if result_data.get("skip_backoff"):
                    progress.update(task, advance=1)
                    continue

                throttle.mark()

                # Progressive backoff on errors
                pause_duration = backoff.record_error()
                add_risk_event(db, "send_error", f"{error} (连续{backoff._consecutive_errors}次)")

                # If we encounter rate limiting, stop immediately
                if error in ["captcha", "rate_limit", "blocked"]:
                    console.print(f"\n[red]⚠ 检测到风控信号: {error}，安全暂停[/red]")
                    add_risk_event(db, error, f"触发风控: {error}")
                    send_report["stop_reason"] = error
                    break

                # If too many consecutive errors, pause
                if backoff.should_pause_long:
                    console.print(f"\n[red]⚠ 连续错误过多，暂停 {int(pause_duration/60)} 分钟[/red]")
                    add_risk_event(db, "backoff_pause", f"暂停{int(pause_duration)}秒")
                    send_report["stop_reason"] = "consecutive_errors"
                    break
                elif pause_duration > 0:
                    console.print(f"\n[yellow]  错误退避: 额外等待 {int(pause_duration)}秒[/yellow]")
                    if _sleep_or_stop(pause_duration, stop_event):
                        send_report["stop_reason"] = "stopped"
                        break

            progress.update(task, advance=1)

    console.print(f"\n[green]✓ 成功发送 {sent_count} 条[/green]")
    report_total = len(workbench_job_ids) if workbench_job_ids else len(jobs)
    send_report["sent_count"] = sent_count
    send_report["deferred_count"] = max(
        report_total - sent_count - send_report["failed_count"],
        0,
    )
    db.close()
    return sent_count
