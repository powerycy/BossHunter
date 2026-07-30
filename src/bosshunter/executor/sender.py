"""Sender module - Auto-send greetings with throttle control."""

import time
import json
from threading import Event
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from bosshunter.browser import new_tab, close_tab, evaluate, click_at, type_text
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
    const isVisible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    const score = (item) => {
        const el = item.el;
        const text = (el.innerText || el.textContent || '').trim();
        const ka = el.getAttribute('ka') || '';
        const redirectUrl = el.getAttribute('redirect-url') || '';
        const dataUrl = el.getAttribute('data-url') || '';
        const tagName = String(el.tagName || '').toLowerCase();
        let value = 0;
        if (isVisible(el)) value += 1000;
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
    return JSON.stringify({
        success: true,
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
        const popup = document.querySelector('.greet-boss-pop, .greet-pop, .dialog-wrap');
        const visible = popup && !!(popup.offsetWidth || popup.offsetHeight || popup.getClientRects().length);
        return JSON.stringify({success: true, popup: !!visible});
    })()
    """
    return _parse_js_result(evaluate(target_id, detect_popup_js))


def _preset_greeting_error() -> dict:
    return {
        "success": False,
        "error": "preset_greeting_enabled",
        "history_detail": "检测到 BOSS 预设招呼语弹窗，请关闭平台自动招呼语后重试",
        "skip_backoff": True,
    }


def _message_visible(target_id: str, greeting: str) -> bool:
    greeting_escaped = json.dumps(greeting, ensure_ascii=False)
    result = _parse_js_result(evaluate(target_id, f"""
    (() => {{
        const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
        const expected = normalize({greeting_escaped});
        const ownMessages = Array.from(document.querySelectorAll(
            '.chat-record .message-item.item-myself, .chat-record .item-myself, '
            + '.chat-record .message-item.item-self, .chat-record [class*="item-my"]'
        ));
        const ownMatch = ownMessages.some((node) => normalize(node.innerText || node.textContent).includes(expected));
        const messageList = document.querySelector('.chat-record');
        const vue = messageList && messageList.__vue__;
        const records = vue && Array.isArray(vue.list$) ? vue.list$ : [];
        const vueMatch = records.some((message) => {{
            if (!message || !message.isSelf) return false;
            const text = message.text || message.lastText || message.content || '';
            return normalize(text).includes(expected);
        }});
        return JSON.stringify({{success: true, visible: ownMatch || vueMatch}});
    }})()
    """))
    return bool(result.get("success") and result.get("visible"))


def _fill_chat_input(target_id: str, greeting: str) -> dict:
    greeting_escaped = json.dumps(greeting, ensure_ascii=False)
    prepared = _parse_js_result(evaluate(target_id, """
    (() => {
        const input = document.querySelector('#chat-input');
        if (!input) return JSON.stringify({success: false, error: 'no_chat_input'});

        input.focus();
        document.execCommand('selectAll', false, null);
        document.execCommand('delete', false, null);
        input.textContent = '';
        input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'deleteContentBackward'}));
        return JSON.stringify({success: true});
    })()
    """))
    if not prepared.get("success"):
        return prepared

    # CDP Input.insertText produces browser-level input events and keeps the
    # BOSS Vue editor's internal state in sync with what is visibly rendered.
    if not type_text(target_id, greeting):
        return {"success": False, "error": "trusted_input_failed"}

    return _parse_js_result(evaluate(target_id, f"""
    (() => {{
        const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
        const greeting = {greeting_escaped};
        const input = document.querySelector('#chat-input');
        if (!input) return JSON.stringify({{success: false, error: 'no_chat_input'}});
        const sendButton = document.querySelector('.btn-send');
        const disabled = !sendButton || sendButton.disabled || sendButton.classList.contains('disabled');
        const matches = normalize(input.innerText || input.textContent) === normalize(greeting);
        return JSON.stringify({{
            success: matches,
            error: matches ? null : 'input_not_filled',
            send_button: !!sendButton,
            disabled
        }});
    }})()
    """))


def _wait_for_chat_page(target_id: str, stop_event, attempts: int = 20) -> dict:
    for _ in range(attempts):
        if _sleep_or_stop(0.5, stop_event):
            close_tab(target_id)
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}
        url_now = evaluate(target_id, "location.pathname")
        if url_now and "/web/geek/chat" in url_now:
            return {"success": True}
    return {"success": False, "error": "chat_navigation_timeout"}


def _click_chat_button(target_id: str, stop_event, attempts: int = 6) -> dict:
    click_chat_js = CHAT_BUTTON_SCRIPT_FOR_TESTS

    last_result: dict = {"success": False, "error": "no_chat_button"}
    for attempt in range(max(1, attempts)):
        if _stop_requested(stop_event):
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}
        last_result = _parse_js_result(evaluate(target_id, click_chat_js))
        if last_result.get("success"):
            x = last_result.get("x")
            y = last_result.get("y")
            if x is not None and y is not None and click_at(target_id, f"{x},{y}"):
                return last_result
            last_result = {"success": False, "error": "chat_button_click_failed"}
            return last_result
        if last_result.get("error") != "no_chat_button":
            return last_result
        if attempt < attempts - 1 and _sleep_or_stop(1, stop_event):
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}

    return last_result


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
    target_id = new_tab(job["url"])
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

    chat_button_attempts = int(throttle_config.get("_chat_button_attempts", 6))
    result1a = _click_chat_button(target_id, stop_event, chat_button_attempts)
    if not result1a.get("success"):
        close_tab(target_id)
        return {"success": False, "error": "no_chat_button", "history_detail": "无法找到沟通按钮", "skip_backoff": True}, None

    if _sleep_or_stop(4, stop_event):
        close_tab(target_id)
        return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None

    popup_state = _detect_greet_popup(target_id)
    if popup_state.get("popup"):
        return _preset_greeting_error(), target_id

    navigation_attempts = int(throttle_config.get("_chat_navigation_attempts", 20))
    chat_ready = _wait_for_chat_page(target_id, stop_event, navigation_attempts)
    if chat_ready.get("error") == "stopped":
        return chat_ready, None
    if not chat_ready.get("success"):
        console.print("[yellow]    ! 沟通按钮未跳转聊天页，尝试真实点击兜底[/yellow]")
        if click_at(target_id, CHAT_BUTTON_SELECTOR):
            if _sleep_or_stop(1, stop_event):
                close_tab(target_id)
                return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
            popup_state = _detect_greet_popup(target_id)
            if popup_state.get("popup"):
                return _preset_greeting_error(), target_id
            chat_ready = _wait_for_chat_page(target_id, stop_event, navigation_attempts)
            if chat_ready.get("error") == "stopped":
                return chat_ready, None

    if not chat_ready.get("success"):
        return {
            "success": False,
            "error": "no_chat_input",
            "history_detail": "发送失败: 未进入具体聊天会话，可能是BOSS继续沟通跳转失败",
            "skip_backoff": True,
        }, target_id

    # Avoid duplicate messages if a previous click succeeded but the local task timed out.
    if _message_visible(target_id, greeting):
        close_tab(target_id)
        return {"success": True, "already_present": True}, None

    input_result = _fill_chat_input(target_id, greeting)
    if not input_result.get("success"):
        return input_result, target_id

    if _sleep_or_stop(0.5, stop_event):
        close_tab(target_id)
        return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None

    if not click_at(target_id, '.btn-send:not(.disabled)'):
        return {
            "success": False,
            "error": "send_button_unavailable",
            "history_detail": "招呼语已填入，但发送按钮不可用，未标记成功",
            "skip_backoff": True,
        }, target_id

    verification_attempts = int(throttle_config.get("_send_verification_attempts", 20))
    for _ in range(max(1, verification_attempts)):
        if _sleep_or_stop(0.5, stop_event):
            close_tab(target_id)
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
        if _message_visible(target_id, greeting):
            # BOSS first renders an optimistic local bubble. Keep the tab open
            # long enough to ensure the server does not reject and remove it.
            if _sleep_or_stop(5, stop_event):
                close_tab(target_id)
                return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
            if not _message_visible(target_id, greeting):
                return {
                    "success": False,
                    "error": "send_rejected_after_click",
                    "history_detail": "消息曾短暂出现但随后被 BOSS 移除，未标记成功",
                    "skip_backoff": True,
                }, target_id
            if _sleep_or_stop(2, stop_event):
                close_tab(target_id)
                return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
            if _message_visible(target_id, greeting):
                close_tab(target_id)
                return {"success": True, "verified": True}, None
            return {
                "success": False,
                "error": "send_rejected_after_click",
                "history_detail": "消息未能稳定保留在 BOSS 会话中，未标记成功",
                "skip_backoff": True,
            }, target_id

    return {
        "success": False,
        "error": "send_not_confirmed",
        "history_detail": "已点击发送，但会话中未出现对应招呼语，未标记成功",
        "skip_backoff": True,
    }, target_id


def send_greetings(config: dict, force: bool = False) -> int:
    """Send generated greetings. Returns count of successfully sent."""
    db = get_db()
    throttle_config = config.get("throttle", {})
    stop_event = config.get("_workbench_stop_event")
    if isinstance(stop_event, Event):
        throttle_config = dict(throttle_config)
        throttle_config["_workbench_stop_event"] = stop_event

    # Anti-ban: random day off (可通过 --force 跳过)
    day_off_prob = throttle_config.get("day_off_probability", 0.05)
    if not force and should_take_day_off(day_off_prob):
        console.print("[yellow]🎲 今日随机休息（防检测），跳过发送[/yellow]")
        add_risk_event(db, "day_off", "随机休息日")
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
        db.close()
        return 0

    jobs = get_jobs_ready_to_send(db)
    _workbench_job_ids = {str(job_id) for job_id in config.get("_workbench_job_ids", [])}
    if _workbench_job_ids:
        jobs = [job for job in jobs if str(job["id"]) in _workbench_job_ids]

    if not jobs:
        console.print("[yellow]没有已生成招呼语的待发送岗位，请先运行 bosshunter greet[/yellow]")
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
        db.close()
        return 0

    jobs_to_send = jobs[:remaining_quota]
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
                break

            greeting = job.get("greeting", "")
            if not greeting:
                update_job_status(db, job["id"], "error")
                progress.update(task, advance=1)
                continue

            # Wait between sends (except first)
            if sent_count > 0:
                progress.update(task, description="等待间隔...")
                if throttle.wait(stop_event):
                    console.print("[yellow]已请求停止，结束发送[/yellow]")
                    break

            progress.update(task, description=f"发送: {job['company'][:10]} - {job['title'][:15]}")

            result_data, failed_target_id = _send_greeting_once(job, greeting, throttle_config)
            if result_data.get("error") == "stopped":
                break
            if result_data.get("error") == "no_chat_input" and failed_target_id:
                console.print("[yellow]    ! 未进入具体聊天会话，重新打开岗位页再试一次[/yellow]")
                close_tab(failed_target_id)
                result_data, failed_target_id = _send_greeting_once(job, greeting, throttle_config)
                if result_data.get("error") == "stopped":
                    break

            if not result_data.get("success"):
                console.print(f"[yellow]    ! 发送失败，保留页面便于排查: {result_data.get('error', 'unknown')}[/yellow]")

            if result_data.get("success"):
                throttle.mark()
                update_job_status(db, job["id"], "sent")
                add_history(db, job["id"], "sent", greeting[:50])
                sent_count += 1
                backoff.record_success()
            else:
                error = result_data.get("error", "unknown")
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
                    break

                # If too many consecutive errors, pause
                if backoff.should_pause_long:
                    console.print(f"\n[red]⚠ 连续错误过多，暂停 {int(pause_duration/60)} 分钟[/red]")
                    add_risk_event(db, "backoff_pause", f"暂停{int(pause_duration)}秒")
                    break
                elif pause_duration > 0:
                    console.print(f"\n[yellow]  错误退避: 额外等待 {int(pause_duration)}秒[/yellow]")
                    if _sleep_or_stop(pause_duration, stop_event):
                        break

            progress.update(task, advance=1)

    console.print(f"\n[green]✓ 成功发送 {sent_count} 条[/green]")
    db.close()
    return sent_count
