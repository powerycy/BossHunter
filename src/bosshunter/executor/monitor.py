"""Monitor module - Watch for HR replies, auto-reply, and send resumes."""

import time
import json

from rich.console import Console

from bosshunter.ai.credentials import call_anthropic_text
from bosshunter.browser import new_tab, close_tab, evaluate, click, wait_for_load, get_page_info
from bosshunter.cancellation import (
    OperationCancelled,
    get_stop_event,
    run_cancellable,
    stop_requested,
)
from bosshunter.db import (
    get_db, get_jobs_by_status,
    update_job_status, add_history, add_risk_event, set_platform_safety_lock,
)
from bosshunter.throttle import RequestThrottle, SendWindowChecker
from bosshunter.platform_safety import (
    PlatformAccessGuard,
    PlatformSafetyStop,
    TransientPlatformAccessGuard,
)

console = Console()

PORTFOLIO_URL = None  # Set via config: profile.portfolio_url
_SHARED_MONITOR_TARGETS: set[str] = set()
_browser_close_tab = close_tab


def close_tab(target_id: str) -> bool:
    """Keep the shared monitor tab alive while conversations are processed."""
    if target_id in _SHARED_MONITOR_TARGETS:
        return True
    return _browser_close_tab(target_id)


def get_boss_operation_interval_multiplier(config: dict) -> float:
    """Return the bounded BOSS page-operation interval multiplier."""
    raw_value = config.get("collection", {}).get("collection_delay_multiplier", 1.5)
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        value = 1.5
    return min(max(value, 1.0), 5.0)


def _positive_interval_seconds(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(parsed, 1.0)


def get_effective_monitor_interval_minutes(
    config: dict,
    base_interval_minutes: float | int | None = None,
) -> float:
    """Apply the BOSS operation multiplier to the wait between monitor cycles."""
    raw_interval = (
        base_interval_minutes
        if base_interval_minutes is not None
        else config.get("monitor", {}).get("interval", 30)
    )
    try:
        interval = float(raw_interval)
    except (TypeError, ValueError):
        interval = 30.0
    return max(interval, 1.0) * get_boss_operation_interval_multiplier(config)


# JS: Extract chat list with full message context
JS_EXTRACT_CHAT_LIST = r"""
(() => {
    const items = document.querySelectorAll('li[role=listitem]');
    const results = [];
    items.forEach(item => {
        const nameText = item.querySelector('.name-text');
        const nameBox = item.querySelector('.name-box');
        const lastMsgEl = item.querySelector('.last-msg-text');
        const msgStatus = item.querySelector('.message-status');
        const unreadEl = item.querySelector('.unread-count, .badge-count, .notice-badge, [class*="unread"]');

        if (!nameText) return;

        const spans = nameBox ? nameBox.querySelectorAll('span') : [];
        const company = spans.length >= 2 ? spans[1].textContent.trim() : '';
        const hrTitle = spans.length >= 3 ? spans[spans.length - 1].textContent.trim() : '';

        // A missing delivery marker is not enough to prove the message came from HR.
        // Treat uncertain rows as candidates and verify direction from the full chat.
        const statusClass = msgStatus ? msgStatus.className : '';
        const lastMsgClass = lastMsgEl ? String(lastMsgEl.className || '').toLowerCase() : '';
        const lastMessage = lastMsgEl ? lastMsgEl.textContent.trim().substring(0, 200) : '';
        const isOurMessage = statusClass.includes('status-read')
            || statusClass.includes('status-delivery')
            || /(myself|self|mine|outgoing|send)/.test(lastMsgClass);
        const isHrMessage = /(friend|other|incoming|receive)/.test(lastMsgClass);
        const isSystemMessage = /正在与Boss.+沟通|近30天过滤了.+BOSS发来的消息|你与该职位竞争者PK情况|新岗位速递|VIP数据总结|根据你的历史开聊\/收藏岗位|根据你的开聊\/收藏岗位.+为你推荐\d+个新岗位|识别到以下新发布岗位你可能感兴趣|我是你的求职助手|感谢您使用VIP权益|(?:您的|VIP)权益已到期|点击续费vip|牛人vip怎么样|附件简历请求已发送|附件简历已发送给对方|附件简历.{0,80}已发送给Boss/i.test(lastMessage);
        const lastDirection = isOurMessage ? 'me' : (isHrMessage ? 'hr' : 'unknown');
        const hasReply = !!lastMsgEl && lastDirection !== 'me' && !isSystemMessage;

        results.push({
            hr_name: nameText.textContent.trim(),
            company: company,
            hr_title: hrTitle,
            last_message: lastMessage,
            has_reply: hasReply,
            has_unread: !!unreadEl,
            is_our_message: isOurMessage,
            last_direction: lastDirection,
            element_index: results.length
        });
    });
    return JSON.stringify(results);
})()
"""

JS_DETECT_MONITOR_RISK = """
(() => {
    // Read only visible UI content. Hidden template nodes and mask placeholders
    // often contain generic security wording and must not stop a real session.
    const visible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || 1) === 0) return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    };
    const visibleText = (el) => visible(el) ? (el.innerText || el.textContent || '').trim() : '';
    const visibleNodes = Array.from(document.querySelectorAll('body *')).filter(visible);
    const text = visibleNodes.map(visibleText).filter(Boolean).join(' ');
    const title = document.title || '';
    const url = window.location.href || '';
    const hasCaptchaElement = visibleNodes.some((el) =>
        el.matches('.geetest_panel, .captcha, [class*="captcha"], [id*="captcha"], iframe[src*="captcha"], iframe[src*="verify"]')
    );
    if (
        hasCaptchaElement || /captcha|verify|security-check/i.test(url) ||
        ['请完成验证', '安全验证', '拖动滑块', '点击完成验证'].some(value => text.includes(value))
    ) return JSON.stringify({risk: 'captcha'});
    if (
        ['操作过于频繁', '访问过于频繁', '请求过于频繁', '操作频繁，请稍后再试'].some(value => text.includes(value))
    ) return JSON.stringify({risk: 'rate_limit'});
    if (
        ['账号存在异常', '账号已被限制', '当前账号异常', '访问被拒绝', '账号或请求被拦截'].some(value => text.includes(value)) ||
        ['账号异常', '访问受限'].some(value => title.includes(value))
    ) return JSON.stringify({risk: 'blocked'});
    return JSON.stringify({risk: null});
})()
"""


class MonitorRiskDetected(RuntimeError):
    """Raised when monitoring must stop immediately for account safety."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class MonitorSafetyGuard:
    """Track consecutive monitor page failures and stop at a conservative threshold."""

    def __init__(self, config: dict) -> None:
        self.config = config
        raw_limit = config.get("monitor", {}).get("max_consecutive_page_failures", 3)
        try:
            self.limit = max(int(raw_limit), 1)
        except (TypeError, ValueError):
            self.limit = 3
        self.consecutive_page_failures = 0

    def record_page_failure(self) -> None:
        self.consecutive_page_failures += 1
        if self.consecutive_page_failures >= self.limit:
            _raise_monitor_risk("consecutive_page_failures", self.config)

    def record_page_success(self) -> None:
        self.consecutive_page_failures = 0

# JS: Extract full conversation messages from an open chat, including rich/system cards
JS_EXTRACT_CONVERSATION = r"""
(() => {
    const MESSAGE_SELECTORS = '.chat-message, .message-item';
    const TEXT_SELECTORS = '.msg-text, .text, .message-text';
    const CARD_SELECTORS = [
        '.card',
        '.card-wrap',
        '.message-card',
        '.system-card',
        '.resume-card',
        '[class*="card"]',
        '[class*="Card"]',
        '[class*="resume"]',
        '[class*="Resume"]',
        '[class*="attachment"]',
        '[class*="Attachment"]'
    ].join(',');
    const ACTION_SELECTORS = 'button, a, [role=button], .btn, [class*="btn"], [class*="button"], [class*="Button"]';

    function normalizeText(value) {
        return (value || '').replace(/\s+/g, ' ').trim();
    }

    function isVisible(el) {
        if (!el) return false;
        const rects = el.getClientRects();
        const style = window.getComputedStyle(el);
        return rects.length > 0 && style.visibility !== 'hidden' && style.display !== 'none';
    }

    function visibleText(el) {
        if (!isVisible(el)) return '';
        return normalizeText(el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '');
    }

    function pushUnique(parts, value) {
        const text = normalizeText(value);
        if (text && !parts.includes(text)) parts.push(text);
    }

    function collectVisibleText(root) {
        const parts = [];
        const textEl = root.querySelector(TEXT_SELECTORS);
        if (textEl) pushUnique(parts, visibleText(textEl));

        const cards = root.querySelectorAll(CARD_SELECTORS);
        cards.forEach(card => {
            pushUnique(parts, visibleText(card));
            card.querySelectorAll(ACTION_SELECTORS).forEach(action => {
                pushUnique(parts, visibleText(action));
                pushUnique(parts, action.getAttribute('aria-label'));
                pushUnique(parts, action.getAttribute('title'));
            });
        });

        if (!parts.length) pushUnique(parts, visibleText(root));
        return normalizeText(parts.join(' '));
    }

    function isResumeRequestCard(text) {
        const hasAttachmentResume = text.includes('附件简历') || text.includes('您的附件简历');
        const hasIntent = ['是否同意', '同意', '想要一份', '请求', '获取', '发送', '发给'].some(kw => text.includes(kw));
        return hasAttachmentResume && hasIntent;
    }

    function senderOf(msg, text) {
        if (/正在与Boss.+沟通|近30天过滤了.+BOSS发来的消息|你与该职位竞争者PK情况|新岗位速递|VIP数据总结|根据你的历史开聊\/收藏岗位|根据你的开聊\/收藏岗位.+为你推荐\d+个新岗位|识别到以下新发布岗位你可能感兴趣|我是你的求职助手|感谢您使用VIP权益|(?:您的|VIP)权益已到期|点击续费vip|牛人vip怎么样|附件简历请求已发送|附件简历已发送给对方|附件简历.{0,80}已发送给Boss/i.test(text)) {
            return 'system';
        }

        const classNames = [msg, ...msg.querySelectorAll('[class]')]
            .map(el => String(el.className || '').toLowerCase())
            .join(' ');
        if (/(^|\s|[-_])(item-myself|message-self|msg-self|is-self|my-message|message-mine|from-me|outgoing)(\s|$|[-_])/.test(classNames)) {
            return 'me';
        }
        if (/(^|\s|[-_])(item-friend|message-other|message-receive|from-other|incoming)(\s|$|[-_])/.test(classNames)) {
            return 'hr';
        }
        return 'unknown';
    }

    const msgs = document.querySelectorAll(MESSAGE_SELECTORS);
    const results = [];
    msgs.forEach(msg => {
        const text = collectVisibleText(msg);
        if (text) {
            results.push({
                sender: senderOf(msg, text),
                text: text.substring(0, 500),
                kind: isResumeRequestCard(text) ? 'resume_request_card' : 'message'
            });
        }
    });
    return JSON.stringify(results);
})()
"""

# JS: Check if resume dialog appeared
JS_CHECK_RESUME_DIALOG = """
(async () => {
    for (let i = 0; i < 10; i++) {
        const dialog = document.querySelector('.choose-resume-dialog');
        if (dialog && dialog.offsetHeight > 0) {
            const items = dialog.querySelectorAll('.list-item');
            const names = Array.from(items).map(it => {
                const n = it.querySelector('.resume-name');
                return n ? n.textContent.trim() : '';
            });
            return JSON.stringify({success: true, items: names.length, names: names});
        }
        await new Promise(r => setTimeout(r, 300));
    }
    return JSON.stringify({success: false, error: 'dialog_not_appeared'});
})()
"""

# JS: Verify resume was sent
JS_VERIFY_RESUME_SENT = """
(async () => {
    await new Promise(r => setTimeout(r, 2000));
    const msgs = document.querySelectorAll('.message-item');
    const lastEight = Array.from(msgs).slice(-8);
    for (const m of lastEight) {
        const text = m.textContent.trim();
        if (text.includes('附件简历请求已发送') || text.includes('联系方式已隐藏') || text.includes('简历已发送')) {
            return JSON.stringify({sent: true});
        }
    }
    return JSON.stringify({sent: false});
})()
"""


def _call_claude(prompt: str, config: dict) -> str | None:
    """Call Claude API and return response text."""
    try:
        return run_cancellable(
            lambda: call_anthropic_text(prompt, config, 500),
            config,
        )
    except OperationCancelled:
        raise
    except Exception as e:
        console.print(f"[red]API 调用失败: {e}[/red]")
        return None


def _wait_or_stop(config: dict, seconds: float) -> bool:
    """Sleep interruptibly when running in the workbench."""
    stop_event = get_stop_event(config)
    if stop_event is None:
        time.sleep(seconds)
        return False
    return stop_event.wait(seconds)


def _wait_for_page_or_stop(target_id: str, config: dict, timeout: float = 10) -> bool:
    """Wait for page loading without pinning a stopped workbench task."""
    try:
        run_cancellable(
            lambda: wait_for_load(target_id, timeout=timeout),
            config,
        )
    except OperationCancelled:
        return False
    return not stop_requested(config)


def _monitor_safety_guard(config: dict) -> MonitorSafetyGuard:
    guard = config.get("_monitor_safety_guard")
    if isinstance(guard, MonitorSafetyGuard):
        return guard
    guard = MonitorSafetyGuard(config)
    config["_monitor_safety_guard"] = guard
    return guard


def _record_page_failure_unless_stopped(config: dict) -> None:
    if not stop_requested(config):
        _monitor_safety_guard(config).record_page_failure()


def _record_monitor_risk(kind: str, config: dict | None = None) -> None:
    """Persist a safe risk event without page text, URLs, or account data."""
    labels = {
        "captcha": "监测检测到验证码，已停止",
        "rate_limit": "监测检测到频率限制，已停止",
        "blocked": "监测检测到账号或请求拦截，已停止",
        "consecutive_page_failures": "监测连续页面失败达到阈值，已停止",
    }
    db = None
    try:
        db = get_db()
        add_risk_event(db, f"monitor_{kind}", labels.get(kind, "监测检测到风险信号，已停止"))
        raw_minutes = (config or {}).get("safety", {}).get("risk_lock_minutes", 10)
        try:
            lock_minutes = max(int(raw_minutes), 1)
        except (TypeError, ValueError):
            lock_minutes = 10
        set_platform_safety_lock(db, kind, minutes=lock_minutes)
    except Exception:
        # Failure to persist telemetry must never allow risky browsing to continue.
        pass
    finally:
        if db is not None:
            db.close()


def _raise_monitor_risk(kind: str, config: dict | None = None) -> None:
    _record_monitor_risk(kind, config)
    raise MonitorRiskDetected(kind)


def _inspect_monitor_page(target_id: str, config: dict) -> None:
    """Stop immediately when the current platform page exposes a risk signal."""
    raw = evaluate(target_id, JS_DETECT_MONITOR_RISK)
    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(result, dict):
        return
    kind = result.get("risk")
    if kind in {"captcha", "rate_limit", "blocked"}:
        _raise_monitor_risk(kind, config)


def _open_monitor_tab(url: str, config: dict, *, background: bool = True) -> str | None:
    """Open one monitor page with an effective interval after every prior open attempt."""
    if stop_requested(config):
        return None
    throttle = config.get("_monitor_request_throttle")
    stop_event = get_stop_event(config)
    if throttle is not None and bool(getattr(throttle, "has_marked_request", False)):
        if throttle.wait(stop_event):
            return None
    access_guard = config.get("_platform_access_guard")
    if isinstance(access_guard, (PlatformAccessGuard, TransientPlatformAccessGuard)):
        try:
            access_guard.reserve("monitor_page")
        except PlatformSafetyStop as exc:
            raise MonitorRiskDetected(exc.reason) from exc
    target_id = new_tab(url, background=background)
    if throttle is not None and hasattr(throttle, "mark"):
        # Record attempts as well as successful opens so retries cannot become a burst.
        throttle.mark()
    return target_id


def _get_monitor_chat_target(chat_url: str, config: dict) -> tuple[str | None, bool]:
    """Return a live chat-list tab, reusing it for Web monitor loops when enabled."""
    if not config.get("_monitor_reuse_chat_tab"):
        return _open_monitor_tab(chat_url, config), False

    runtime_state = config.setdefault("_monitor_runtime_state", {})
    target_id = runtime_state.get("chat_target_id")
    if target_id:
        try:
            if get_page_info(str(target_id)):
                return str(target_id), True
        except Exception:
            pass
        runtime_state.pop("chat_target_id", None)

    target_id = _open_monitor_tab(chat_url, config)
    if target_id:
        runtime_state["chat_target_id"] = target_id
    return target_id, False


def close_monitor_chat_target(config: dict) -> None:
    """Close and forget the reusable chat-list tab, if one exists."""
    runtime_state = config.get("_monitor_runtime_state")
    if not isinstance(runtime_state, dict):
        return
    target_id = runtime_state.pop("chat_target_id", None)
    if target_id:
        _SHARED_MONITOR_TARGETS.discard(str(target_id))
        close_tab(str(target_id))


def _discard_monitor_chat_target(config: dict, target_id: str) -> None:
    runtime_state = config.get("_monitor_runtime_state")
    if isinstance(runtime_state, dict) and runtime_state.get("chat_target_id") == target_id:
        runtime_state.pop("chat_target_id", None)
    _SHARED_MONITOR_TARGETS.discard(str(target_id))
    close_tab(target_id)


def _detect_rejection(messages: list[dict]) -> bool:
    """Check if HR is rejecting in messages AFTER user's last reply."""
    rejection_keywords = ["不合适", "不匹配", "不太合适", "暂时没有", "不符合", "不太符合",
                          "很遗憾", "无法推进", "不考虑", "已招满", "岗位已关闭", "对不起"]
    # Only check HR messages after my last reply
    hr_msgs_after = _get_hr_messages_after_last_reply(messages)
    for msg in hr_msgs_after:
        text = msg["text"]
        if _looks_like_non_rejection_action_card(text):
            continue
        for kw in rejection_keywords:
            if kw in text:
                return True
    return False


def _looks_like_non_rejection_action_card(text: str) -> bool:
    """Ignore BOSS system cards whose button labels look like rejection words."""
    text = text or ""
    if _looks_like_resume_request_card(text):
        return True
    if "您是否接受此工作地点" in text and "暂不考虑" in text and "可以接受" in text:
        return True
    return False


def _looks_like_resume_request_card(text: str) -> bool:
    """Detect BOSS rich-card requests for the user's attachment resume.

    This is intentionally stricter than the normal text-message resume detector:
    card handling should only trigger when strong attachment-resume wording appears
    with an action or request signal.
    """
    text = text or ""
    attachment_signals = ["附件简历", "您的附件简历", "我的附件简历"]
    intent_signals = ["是否同意", "同意", "想要一份", "请求", "获取", "发送", "发给"]
    rejection_context = ["不匹配", "不合适", "不太合适", "不符合", "不太符合", "很遗憾", "无法推进", "祝", "已招满", "岗位已关闭"]

    if any(kw in text for kw in rejection_context):
        return False
    return any(kw in text for kw in attachment_signals) and any(kw in text for kw in intent_signals)


def _is_short_resume_acknowledgement(text: str) -> bool:
    """Treat standalone positive HR acknowledgements as resume intent."""
    normalized = "".join(str(text or "").split()).strip("，,。.!！?？~～…")
    return normalized in {
        "好",
        "好的",
        "可以",
        "你好啊，可以聊一聊",
        "你好，可以聊一聊",
        "您好，可以聊一聊",
    }


def _detect_resume_request(messages: list[dict]) -> bool:
    """Check if HR is asking for a resume in messages AFTER user's last reply.

    Excludes messages that are actually rejections containing the word '简历'.
    Also detects BOSS rich cards requesting the user's attachment resume.
    """
    resume_keywords = ["简历", "简历发", "发一份简历", "看看简历", "发个简历", "发下简历",
                       "附件", "发一下简历", "方便发", "看看你的简历"]
    # Rejection context: if '简历' appears alongside rejection phrases, it's NOT a request
    rejection_context = ["不匹配", "不合适", "不太合适", "不符合", "不太符合", "很遗憾",
                         "无法推进", "祝", "已招满", "岗位已关闭"]
    # Only check HR messages after my last reply
    hr_msgs_after = _get_hr_messages_after_last_reply(messages)
    for msg in hr_msgs_after:
        text = msg["text"]
        # Skip if this message contains rejection context
        has_rejection = any(kw in text for kw in rejection_context)
        if has_rejection:
            continue
        if msg.get("kind") == "resume_request_card" or _looks_like_resume_request_card(text):
            return True
        if _is_short_resume_acknowledgement(text):
            return True
        for kw in resume_keywords:
            if kw in text:
                return True
    return False


def _has_resume_request_card(messages: list[dict]) -> bool:
    """Check for BOSS resume request cards in HR messages after the user's last reply."""
    hr_msgs_after = _get_hr_messages_after_last_reply(messages)
    for msg in hr_msgs_after:
        text = msg.get("text", "")
        if msg.get("kind") == "resume_request_card" or _looks_like_resume_request_card(text):
            return True
    return False


def _get_hr_messages_after_last_reply(messages: list[dict]) -> list[dict]:
    """Get HR messages that came AFTER the user's last reply.

    If user never replied, return all HR messages.
    """
    # Find the index of user's last message
    last_my_idx = -1
    for i, msg in enumerate(messages):
        if msg["sender"] == "me":
            last_my_idx = i

    # Get HR messages after that point
    after = messages[last_my_idx + 1:] if last_my_idx >= 0 else messages
    return [m for m in after if m["sender"] == "hr"]


def _normalized_message_text(text: str) -> str:
    """Normalize chat text for conservative sender reconciliation."""
    return " ".join(str(text or "").split())


def _looks_like_system_message(text: str) -> bool:
    """Identify BOSS UI notices that are not participant messages."""
    normalized = _normalized_message_text(text)
    normalized_lower = normalized.lower()
    system_markers = (
        "您正在与Boss",
        "近30天过滤了",
        "你与该职位竞争者PK情况",
        "新岗位速递",
        "VIP数据总结",
        "根据你的历史开聊/收藏岗位",
        "识别到以下新发布岗位你可能感兴趣",
    )
    assistant_markers = (
        "我是你的求职助手",
        "感谢您使用vip权益",
        "您的权益已到期",
        "vip权益已到期",
        "点击续费vip",
        "牛人vip怎么样",
        "附件简历请求已发送",
        "附件简历已发送给对方",
    )
    return (
        any(marker in normalized for marker in system_markers)
        or any(marker in normalized_lower for marker in assistant_markers)
        or ("附件简历" in normalized and "已发送给Boss" in normalized)
    )


def _matches_own_greeting(message_text: str, greeting: str) -> bool:
    """Return true when a chat bubble contains this job's saved greeting."""
    message = _normalized_message_text(message_text)
    expected = _normalized_message_text(greeting)
    if len(expected) < 8 or not message:
        return False
    if expected in message:
        return True
    prefix_length = min(len(expected), 48)
    return prefix_length >= 24 and expected[:prefix_length] in message


def _reconcile_conversation_messages(messages: list[dict], job: dict) -> list[dict]:
    """Correct known own/system messages without guessing unknown as HR."""
    greeting = str(job.get("greeting") or "")
    reconciled = []
    for raw_message in messages:
        message = dict(raw_message) if isinstance(raw_message, dict) else {}
        text = str(message.get("text") or "")
        sender = str(message.get("sender") or "unknown")
        strong_resume_request = (
            message.get("kind") == "resume_request_card"
            or _looks_like_resume_request_card(text)
        )
        if _looks_like_system_message(text) and not strong_resume_request:
            sender = "system"
        elif _matches_own_greeting(text, greeting):
            sender = "me"
        elif strong_resume_request and sender in {"unknown", "system"}:
            sender = "hr"
        elif sender not in {"me", "hr", "system"}:
            sender = "unknown"
        message["sender"] = sender
        message["text"] = text
        reconciled.append(message)
    return reconciled


def _reconcile_outbound_chat_preview(
    messages: list[dict],
    conversation: dict | None,
) -> list[dict]:
    """Use a proven outgoing chat-list preview to identify the matching full bubble."""
    if not (
        (conversation or {}).get("last_direction") == "me"
        or (conversation or {}).get("is_our_message")
    ):
        return messages
    preview = _normalized_message_text(str((conversation or {}).get("last_message") or ""))
    for message in reversed(messages):
        full_text = _normalized_message_text(str(message.get("text") or ""))
        if preview and (
            full_text == preview
            or (len(preview) >= 12 and full_text.startswith(preview))
        ):
            message["sender"] = "me"
            break
    return messages


def _truncate_text(text: str, limit: int) -> str:
    """Limit text length for history payloads."""
    text = text or ""
    return text if len(text) <= limit else text[:limit]


def _build_reply_detail(
    messages: list[dict],
    ai_reply: str,
    schema: str = "reply_pending.v1",
    conversation: dict | None = None,
) -> str:
    """Build structured history detail containing the HR question and AI reply."""
    hr_messages = _get_hr_messages_after_last_reply(messages)
    hr_question = "\n".join(str(msg.get("text", "")) for msg in hr_messages[-3:]).strip()
    reply_fingerprint = _reply_fingerprint_from_hr_question(hr_question)
    payload = {
        "schema": schema,
        "hr_question": _truncate_text(hr_question, 1000),
        "reply_fingerprint": reply_fingerprint,
        "chat_list_last_message": _reply_fingerprint_from_hr_question(
            str((conversation or {}).get("last_message", ""))
        ),
        "ai_reply": _truncate_text(ai_reply or "", 1000),
        "conversation_tail": [
            {
                "sender": str(msg.get("sender", "")),
                "text": _truncate_text(str(msg.get("text", "")), 500),
            }
            for msg in messages[-6:]
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _build_external_reply_detail(
    messages: list[dict],
    conversation: dict | None = None,
) -> str | None:
    """Capture one HR-to-user reply round that was sent directly in BOSS."""
    last_participant = next(
        (message for message in reversed(messages) if message.get("sender") in {"me", "hr"}),
        None,
    )
    if not last_participant or last_participant.get("sender") != "me":
        return None
    last_hr_index = next(
        (index for index in range(len(messages) - 1, -1, -1) if messages[index].get("sender") == "hr"),
        -1,
    )
    if last_hr_index < 0:
        return None
    outbound_messages = [
        message for message in messages[last_hr_index + 1:]
        if message.get("sender") == "me"
    ]
    if not outbound_messages:
        return None
    manual_reply = "\n".join(str(message.get("text") or "") for message in outbound_messages[-3:]).strip()
    payload = json.loads(_build_reply_detail(
        messages[:last_hr_index + 1],
        "",
        "replied.external.v1",
        conversation,
    ))
    payload.update({
        "manual_reply": _truncate_text(manual_reply, 1000),
        "last_outbound_message": _truncate_text(str(outbound_messages[-1].get("text") or ""), 500),
        "conversation_tail": [
            {"sender": str(message.get("sender", "")), "text": _truncate_text(str(message.get("text", "")), 500)}
            for message in messages[-6:]
        ],
    })
    return json.dumps(payload, ensure_ascii=False)


def _build_resume_failure_detail(messages: list[dict], failure_reason: str) -> str:
    """Build a resume failure payload without mixing system errors into HR text."""
    payload = json.loads(_build_reply_detail(messages, "", "resume_failed.v2"))
    payload["system_reason"] = _truncate_text(
        failure_reason or "定制简历生成失败，未获得更具体的错误信息",
        2000,
    )
    return json.dumps(payload, ensure_ascii=False)


def _parse_reply_detail(detail: str) -> dict:
    """Parse structured reply detail, returning an empty dict for legacy text rows."""
    if not isinstance(detail, str) or not detail.strip().startswith("{"):
        return {}
    try:
        payload = json.loads(detail)
    except (json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _reply_fingerprint_from_hr_question(hr_question: str) -> str:
    """Build a stable fingerprint for the HR message currently awaiting a decision."""
    return " ".join(str(hr_question or "").split())


def _reply_fingerprint_from_messages(messages: list[dict]) -> str:
    hr_messages = _get_hr_messages_after_last_reply(messages)
    hr_question = "\n".join(str(msg.get("text", "")) for msg in hr_messages[-3:]).strip()
    return _reply_fingerprint_from_hr_question(hr_question)


def _reply_fingerprint_from_detail(detail: str) -> str:
    payload = _parse_reply_detail(detail)
    fingerprint = payload.get("reply_fingerprint") or payload.get("pending_reply_fingerprint")
    if isinstance(fingerprint, str) and fingerprint.strip():
        return _reply_fingerprint_from_hr_question(fingerprint)
    hr_question = payload.get("hr_question") or payload.get("pending_hr_question")
    return _reply_fingerprint_from_hr_question(hr_question) if isinstance(hr_question, str) else ""


def _chat_list_fingerprint_from_detail(detail: str) -> str:
    payload = _parse_reply_detail(detail)
    value = payload.get("chat_list_last_message")
    return _reply_fingerprint_from_hr_question(value) if isinstance(value, str) else ""


def _build_reply_resolution_detail(
    schema: str,
    note: str,
    pending_detail: str,
    manual_reply: str = "",
    pending_history_id: int | None = None,
) -> str:
    """Build structured history detail for manual reply decisions."""
    pending_payload = _parse_reply_detail(pending_detail)
    pending_hr_question = pending_payload.get("hr_question") if isinstance(pending_payload.get("hr_question"), str) else ""
    payload = {
        "schema": schema,
        "note": note,
        "pending_history_id": pending_history_id,
        "pending_reply_fingerprint": _reply_fingerprint_from_detail(pending_detail),
        "pending_hr_question": _truncate_text(pending_hr_question, 1000),
        "manual_reply": _truncate_text(manual_reply, 1000),
    }
    return json.dumps(payload, ensure_ascii=False)


def _check_if_i_already_replied(messages: list[dict]) -> bool:
    """Check if I (the user) already replied after the last HR message.

    Returns True if the last message in conversation is from 'me'.
    """
    last_participant = next(
        (message for message in reversed(messages) if message.get("sender") in {"me", "hr"}),
        None,
    )
    return bool(last_participant and last_participant.get("sender") == "me")


def _check_if_portfolio_sent(messages: list[dict], portfolio_url: str = "") -> bool:
    """Check if portfolio URL was already sent in conversation."""
    if not portfolio_url:
        return True  # No portfolio configured, treat as already sent
    for msg in messages:
        if msg["sender"] == "me" and portfolio_url in msg["text"]:
            return True
    return False


def _generate_auto_reply(messages: list[dict], job: dict, config: dict) -> str | None:
    """Generate a natural reply based on conversation context."""
    # Build conversation context
    conv_text = "\n".join([f"{'我' if m['sender'] == 'me' else 'HR'}: {m['text']}" for m in messages[-10:]])

    # Read resume summary from file
    from pathlib import Path
    resume_path = Path(config.get("profile", {}).get("resume_path", "./resume.md"))
    resume_summary = resume_path.read_text(encoding="utf-8")[:800] if resume_path.exists() else "（未配置简历）"

    prompt = f"""你是一位求职者，正在BOSS直聘上和HR沟通。请根据对话上下文生成一条自然、礼貌的回复。

## 对话记录
{conv_text}

## 我的背景
{resume_summary}

## 岗位信息
- 职位：{job.get('title', '')}
- 公司：{job.get('company', '')}

## 要求
1. 根据HR最后一条消息的意图来回复，自然对话
2. 语气像真人在手机上打字，不要太正式
3. 字数控制在30-100字
4. 不要重复之前已经说过的内容
5. 如果HR在约面试时间，积极配合
6. 不要捏造经历或头衔

请直接输出回复文本，不要加任何标记。"""

    return _call_claude(prompt, config)


def _send_message_in_chat(target_id: str, message: str) -> bool:
    """Send a text message in the current open chat via Vue handleSubmit."""
    js_send = f"""
    (() => {{
        const input = document.querySelector('#chat-input');
        if (!input) return JSON.stringify({{success: false, error: 'no_input'}});

        // Find Vue instance
        let vue = null;
        let el = input;
        for (let i = 0; i < 15 && el; i++) {{
            if (el.__vue__) {{ vue = el.__vue__; break; }}
            el = el.parentElement;
        }}
        if (!vue) return JSON.stringify({{success: false, error: 'no_vue'}});

        input.innerText = {json.dumps(message)};
        vue._data.enableSubmit = true;
        vue.handleSubmit();
        return JSON.stringify({{success: true}});
    }})()
    """
    result = evaluate(target_id, js_send)
    if not result:
        return False
    try:
        data = json.loads(result) if isinstance(result, str) else result
        return data.get("success", False)
    except (json.JSONDecodeError, TypeError):
        return False


def _open_conversation(job: dict, config: dict) -> str | None:
    """Open a conversation with HR. Returns target_id or None.

    Strategies tried in order:
      A) Job detail page → click "继续沟通"
      B) Chat list → match by HR name + company
      C) Chat list → match by company name only (fallback when hr_name is empty)
    Each strategy retries once on failure.
    """
    if stop_requested(config):
        return None
    job_url = job.get("url", "")

    # Strategy A: Via job URL (try up to 2 times)
    if job_url:
        for attempt in range(2):
            target_id = _open_monitor_tab(job_url, config)
            if not target_id:
                _record_page_failure_unless_stopped(config)
                if attempt == 0:
                    if _wait_or_stop(config, 3):
                        return None
                    continue
                break

            if _wait_or_stop(config, 3) or not _wait_for_page_or_stop(target_id, config, timeout=10):
                close_tab(target_id)
                _record_page_failure_unless_stopped(config)
                return None
            try:
                _inspect_monitor_page(target_id, config)
            except MonitorRiskDetected:
                close_tab(target_id)
                raise
            if _wait_or_stop(config, 1):
                close_tab(target_id)
                return None

            # Click "继续沟通" or similar chat button
            if click(target_id, ".btn-startchat") or click(target_id, "[ka*='chat']"):
                if _wait_or_stop(config, 3) or not _wait_for_page_or_stop(target_id, config, timeout=10):
                    close_tab(target_id)
                    _record_page_failure_unless_stopped(config)
                    return None
                try:
                    _inspect_monitor_page(target_id, config)
                except MonitorRiskDetected:
                    close_tab(target_id)
                    raise
                if _wait_or_stop(config, 2):
                    close_tab(target_id)
                    return None
                # Verify we're in a chat page
                info = get_page_info(target_id)
                if info and "chat" in (info.get("url") or ""):
                    return target_id
                # Sometimes click navigates to chat
                if info and "zhipin.com" in (info.get("url") or ""):
                    return target_id

            close_tab(target_id)
            _record_page_failure_unless_stopped(config)
            if attempt == 0:
                if _wait_or_stop(config, 2):
                    return None

    # Strategy B/C: Open chat list and click conversation
    target_id = _open_conversation_from_chat_list(job, config)
    return target_id


def _open_conversation_from_chat_list(
    job: dict,
    config: dict,
    *,
    background: bool = True,
) -> str | None:
    """Open conversation from chat list. Matches by name+company, then company-only fallback."""
    if stop_requested(config):
        return None
    monitor_cfg = config.get("monitor", {})
    chat_url = monitor_cfg.get("chat_url", "https://www.zhipin.com/web/geek/chat")
    target_id = _open_monitor_tab(chat_url, config, background=background)
    if not target_id:
        _record_page_failure_unless_stopped(config)
        return None

    if _wait_or_stop(config, 4) or not _wait_for_page_or_stop(target_id, config, timeout=10):
        close_tab(target_id)
        _record_page_failure_unless_stopped(config)
        return None
    try:
        _inspect_monitor_page(target_id, config)
    except MonitorRiskDetected:
        close_tab(target_id)
        raise
    if _wait_or_stop(config, 3):
        close_tab(target_id)
        return None

    hr_name = (job.get("hr_name") or "").strip()
    company = (job.get("company") or "").strip()

    # Build JS that tries: 1) exact hr_name+company match, 2) company-only match
    js_click_chat = f"""
    (async () => {{
        const items = document.querySelectorAll('li[role=listitem]');
        let companyOnlyMatch = null;

        for (const item of items) {{
            const nameEl = item.querySelector('.name-text');
            const nameBox = item.querySelector('.name-box');
            const spans = nameBox ? nameBox.querySelectorAll('span') : [];
            const name = (nameEl ? nameEl.textContent : '').trim();
            const comp = spans.length >= 2 ? spans[1].textContent.trim() : '';

            const hrName = {json.dumps(hr_name, ensure_ascii=False)};
            const targetComp = {json.dumps(company, ensure_ascii=False)};
            const sameCompany = !!comp && !!targetComp
                && (comp.includes(targetComp) || targetComp.includes(comp));

            // Exact match: HR name + company
            if (hrName && name === hrName && (sameCompany || !targetComp)) {{
                const fc = item.querySelector('.friend-content') || item;
                const rect = fc.getBoundingClientRect();
                const x = rect.x + rect.width / 2;
                const y = rect.y + rect.height / 2;
                for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {{
                    fc.dispatchEvent(new PointerEvent(type, {{
                        bubbles: true, cancelable: true, composed: true,
                        clientX: x, clientY: y, pointerId: 1, pointerType: 'mouse'
                    }}));
                }}
                await new Promise(r => setTimeout(r, 2000));
                return JSON.stringify({{success: true, match: 'exact'}});
            }}

            // Company-only match (save first match as fallback)
            if (!companyOnlyMatch && sameCompany) {{
                companyOnlyMatch = item;
            }}
        }}

        // Fallback: company-only match (when hr_name is empty or not found)
        if (companyOnlyMatch) {{
            const fc = companyOnlyMatch.querySelector('.friend-content') || companyOnlyMatch;
            const rect = fc.getBoundingClientRect();
            const x = rect.x + rect.width / 2;
            const y = rect.y + rect.height / 2;
            for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {{
                fc.dispatchEvent(new PointerEvent(type, {{
                    bubbles: true, cancelable: true, composed: true,
                    clientX: x, clientY: y, pointerId: 1, pointerType: 'mouse'
                }}));
            }}
            await new Promise(r => setTimeout(r, 2000));
            return JSON.stringify({{success: true, match: 'company_only'}});
        }}

        return JSON.stringify({{success: false, error: 'conversation_not_found', total: items.length}});
    }})()
    """

    result = evaluate(target_id, js_click_chat)
    if not result:
        close_tab(target_id)
        return None

    try:
        click_result = json.loads(result) if isinstance(result, str) else result
        if click_result.get("success"):
            if _wait_or_stop(config, 1):
                close_tab(target_id)
                return None
            try:
                _inspect_monitor_page(target_id, config)
            except MonitorRiskDetected:
                close_tab(target_id)
                raise
            return target_id
    except (json.JSONDecodeError, TypeError):
        pass

    # Retry: scroll chat list to load more and try again
    console.print("[dim]    对话列表未找到，尝试滚动加载...[/dim]")
    evaluate(target_id, """
    (() => {
        const nav = document.querySelector('nav') || document.querySelector('.chat-list');
        if (nav) nav.scrollTop = nav.scrollHeight;
    })()
    """)
    if _wait_or_stop(config, 3):
        close_tab(target_id)
        return None

    result = evaluate(target_id, js_click_chat)
    if result:
        try:
            click_result = json.loads(result) if isinstance(result, str) else result
            if click_result.get("success"):
                if _wait_or_stop(config, 1):
                    close_tab(target_id)
                    return None
                try:
                    _inspect_monitor_page(target_id, config)
                except MonitorRiskDetected:
                    close_tab(target_id)
                    raise
                return target_id
        except (json.JSONDecodeError, TypeError):
            pass

    close_tab(target_id)
    _record_page_failure_unless_stopped(config)
    return None


def _deliver_resume_to_chat(target_id: str) -> bool:
    """Send platform resume in an already-open chat conversation.

    Flow: Click "发简历" → select resume → confirm send
    """
    # Click "发简历" button
    if not click(target_id, '[d-c="62009"]'):
        console.print("[dim]    未找到发简历按钮[/dim]")
        return False

    time.sleep(2)

    # Wait for resume selection dialog
    dialog_result = evaluate(target_id, JS_CHECK_RESUME_DIALOG)
    if not dialog_result:
        return False

    try:
        dialog_data = json.loads(dialog_result) if isinstance(dialog_result, str) else dialog_result
    except (json.JSONDecodeError, TypeError):
        return False

    if not dialog_data.get("success"):
        console.print(f"[dim]    简历选择框未出现: {dialog_data.get('error', '')}[/dim]")
        return False

    console.print(f"[dim]    可选简历: {dialog_data.get('names', [])}[/dim]")

    # Select first resume item
    if not click(target_id, ".choose-resume-dialog .list-item"):
        return False
    time.sleep(1)

    # Click send button
    if not click(target_id, ".choose-resume-dialog .btn-confirm"):
        return False
    time.sleep(2)

    # Verify success
    verify_result = evaluate(target_id, JS_VERIFY_RESUME_SENT)
    if verify_result:
        try:
            verify_data = json.loads(verify_result) if isinstance(verify_result, str) else verify_result
            if verify_data.get("sent"):
                return True
        except (json.JSONDecodeError, TypeError):
            pass

    console.print("[dim]    发送验证不确定，可能已发送[/dim]")
    return True  # Optimistic - resume dialog appeared and we clicked send


def _check_boss_replies(config: dict, tracked_jobs: list[dict] | None = None) -> list[dict]:
    """Open BOSS chat page and detect conversations with HR replies.

    Returns list of conversations with replies (including matched job info).
    """
    db = get_db()
    if stop_requested(config):
        db.close()
        return []
    monitor_cfg = config.get("monitor", {})
    chat_url = monitor_cfg.get("chat_url", "https://www.zhipin.com/web/geek/chat")

    # Get jobs we've sent greetings to (or already replied/resume_sent/follow_up_sent/needs_resume - keep monitoring)
    if tracked_jobs is None:
        sent_jobs = get_jobs_by_status(db, "sent")
        replied_jobs = get_jobs_by_status(db, "replied")
        resume_sent_jobs = get_jobs_by_status(db, "resume_sent")
        follow_up_jobs = get_jobs_by_status(db, "follow_up_sent")
        needs_resume_jobs = get_jobs_by_status(db, "needs_resume")
        tracked_jobs = sent_jobs + replied_jobs + resume_sent_jobs + follow_up_jobs + needs_resume_jobs
    all_tracked_jobs = [
        job for job in tracked_jobs
        if str(job.get("source_platform") or "boss").strip().lower() == "boss"
    ]

    if not all_tracked_jobs:
        console.print("[dim]没有需要监测的对话[/dim]")
        db.close()
        return []

    console.print(f"[bold]监测 {len(all_tracked_jobs)} 个对话的回复情况...[/bold]")

    # Open chat page
    target_id, reused_chat_target = _get_monitor_chat_target(chat_url, config)
    if not target_id:
        console.print("[red]无法打开聊天页面[/red]")
        db.close()
        _monitor_safety_guard(config).record_page_failure()
        return []

    if (
        (not reused_chat_target and _wait_or_stop(config, 3))
        or not _wait_for_page_or_stop(target_id, config, timeout=10)
    ):
        _discard_monitor_chat_target(config, target_id)
        db.close()
        if not stop_requested(config):
            _monitor_safety_guard(config).record_page_failure()
        return []
    try:
        _inspect_monitor_page(target_id, config)
    except MonitorRiskDetected:
        _discard_monitor_chat_target(config, target_id)
        db.close()
        raise
    if not reused_chat_target and _wait_or_stop(config, 2):
        _discard_monitor_chat_target(config, target_id)
        db.close()
        return []

    # Extract chat list
    raw = evaluate(target_id, JS_EXTRACT_CHAT_LIST)
    if not config.get("_monitor_reuse_chat_tab"):
        close_tab(target_id)
    if stop_requested(config):
        db.close()
        return []

    if not raw:
        console.print("[yellow]未能获取聊天列表[/yellow]")
        if config.get("_monitor_reuse_chat_tab"):
            _discard_monitor_chat_target(config, target_id)
        db.close()
        _monitor_safety_guard(config).record_page_failure()
        return []

    try:
        conversations = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        console.print("[yellow]聊天列表解析失败[/yellow]")
        if config.get("_monitor_reuse_chat_tab"):
            _discard_monitor_chat_target(config, target_id)
        db.close()
        _monitor_safety_guard(config).record_page_failure()
        return []

    if not isinstance(conversations, list):
        console.print("[yellow]聊天列表格式异常[/yellow]")
        if config.get("_monitor_reuse_chat_tab"):
            _discard_monitor_chat_target(config, target_id)
        db.close()
        _monitor_safety_guard(config).record_page_failure()
        return []

    if config.get("_monitor_reuse_chat_tab"):
        _SHARED_MONITOR_TARGETS.add(str(target_id))
        for conversation in conversations:
            if isinstance(conversation, dict):
                conversation["_chat_target_id"] = str(target_id)

    _monitor_safety_guard(config).record_page_success()

    if not conversations:
        console.print("[dim]聊天列表为空[/dim]")
        db.close()
        return []

    console.print(f"[dim]获取到 {len(conversations)} 条对话[/dim]")

    # Match conversations to tracked jobs and find ones with HR replies
    raw_limit = monitor_cfg.get("max_conversations_per_cycle", 5)
    try:
        max_conversations = max(int(raw_limit), 1)
    except (TypeError, ValueError):
        max_conversations = 5
    results = []
    for conv in conversations:
        if stop_requested(config):
            break
        matched_job = _match_conversation_to_job(conv, all_tracked_jobs)
        if matched_job:
            is_outbound = (
                conv.get("last_direction") == "me"
                or bool(conv.get("is_our_message"))
            )
            if is_outbound:
                if _matches_own_greeting(
                    str(conv.get("last_message") or ""),
                    str(matched_job.get("greeting") or ""),
                ):
                    continue
                if str(matched_job.get("status") or "") == "sent" and not db.execute(
                    "SELECT 1 FROM history WHERE job_id = ? AND action = 'hr_reply_detected' LIMIT 1",
                    (matched_job["id"],),
                ).fetchone():
                    continue
                if _has_recorded_outbound_reply(db, matched_job["id"], conv):
                    continue
                results.append({"job": matched_job, "conversation": conv})
                console.print(
                    f"[green]  ✓ {matched_job['company']} - {matched_job['title']} 有待同步的已发送回复[/green]"
                )
                if len(results) >= max_conversations:
                    console.print(f"[dim]本轮已达到对话处理上限 {max_conversations}[/dim]")
                    break
                continue

            if not conv.get("has_reply"):
                continue
            pending = _get_unresolved_pending_reply(db, matched_job["id"])
            if pending and _pending_matches_chat_list(_row_text(pending, "detail"), conv):
                console.print(
                    f"[dim]  跳过已有待确认回复: {matched_job['company']} - {matched_job['title']}[/dim]"
                )
                continue
            handled = _get_latest_handled_reply(db, matched_job["id"])
            if handled and _handled_reply_matches_chat_list(_row_text(handled, "detail"), conv):
                console.print(
                    f"[dim]  跳过已处理的相同HR消息: {matched_job['company']} - {matched_job['title']}[/dim]"
                )
                continue

            # Update status to replied if it was 'sent'
            if matched_job.get("status") == "sent":
                update_job_status(db, matched_job["id"], "replied")
                add_history(
                    db,
                    matched_job["id"],
                    "hr_reply_detected",
                    f"HR回复: {conv.get('last_message', '')[:50]}",
                )

            results.append({
                "job": matched_job,
                "conversation": conv,
            })
            console.print(
                f"[green]  ✓ {matched_job['company']} - {matched_job['title']} 有新回复[/green]"
            )
            if len(results) >= max_conversations:
                console.print(f"[dim]本轮已达到对话处理上限 {max_conversations}[/dim]")
                break

    if not results:
        console.print("[dim]暂无新回复[/dim]")

    db.close()
    return results


def check_replies(config: dict) -> list[dict]:
    """Check BOSS replies; collection-only platforms never enter chat flows."""
    db = get_db()
    try:
        tracked = []
        for status in ("sent", "replied", "resume_sent", "follow_up_sent", "needs_resume"):
            tracked.extend(get_jobs_by_status(db, status))
    finally:
        db.close()
    requested_ids = {
        str(job_id)
        for job_id in config.get("_monitor_job_ids", [])
        if str(job_id)
    }
    if requested_ids:
        tracked = [job for job in tracked if str(job.get("id") or "") in requested_ids]
    boss_results = _check_boss_replies(config, tracked)
    return boss_results


def _match_conversation_to_job(conv: dict, jobs: list[dict]) -> dict | None:
    """Match a chat conversation to a job record."""
    conv_hr = conv.get("hr_name", "").strip()
    conv_company = conv.get("company", "").strip()

    if not conv_hr:
        return None

    # Exact match first (hr_name + company)
    for job in jobs:
        job_hr = (job.get("hr_name") or "").strip()
        job_company = (job.get("company") or "").strip()
        if conv_hr == job_hr and conv_company == job_company:
            return job

    # Fuzzy match by hr_name + partial company
    for job in jobs:
        job_hr = (job.get("hr_name") or "").strip()
        job_company = (job.get("company") or "").strip()
        if conv_hr == job_hr and job_company and (
            conv_company in job_company or job_company in conv_company
        ):
            return job

    # Fallback: job has no hr_name, match by company name only
    for job in jobs:
        job_hr = (job.get("hr_name") or "").strip()
        job_company = (job.get("company") or "").strip()
        if not job_hr and job_company and (
            conv_company == job_company
            or conv_company in job_company
            or job_company in conv_company
        ):
            return job

    return None


def _open_scanned_conversation(
    job: dict,
    config: dict,
    conversation: dict | None,
) -> str | None:
    """Select a just-scanned row in the shared chat tab without opening another page."""
    target_id = str((conversation or {}).get("_chat_target_id") or "")
    if not target_id or target_id not in _SHARED_MONITOR_TARGETS or stop_requested(config):
        return None
    try:
        page_info = get_page_info(target_id)
    except Exception:
        return None
    if not page_info or "chat" not in str(page_info.get("url") or ""):
        return None

    raw_index = (conversation or {}).get("element_index", -1)
    try:
        element_index = int(raw_index)
    except (TypeError, ValueError):
        element_index = -1
    expected_hr = str((conversation or {}).get("hr_name") or job.get("hr_name") or "").strip()
    expected_company = str((conversation or {}).get("company") or job.get("company") or "").strip()
    js_select = f"""
    (() => {{
        const rows = Array.from(document.querySelectorAll('li[role=listitem]'));
        const expectedIndex = {element_index};
        const expectedHr = {json.dumps(expected_hr, ensure_ascii=False)};
        const expectedCompany = {json.dumps(expected_company, ensure_ascii=False)};
        const identity = row => {{
            if (!row) return false;
            const name = (row.querySelector('.name-text')?.textContent || '').trim();
            const spans = row.querySelector('.name-box')?.querySelectorAll('span') || [];
            const company = spans.length >= 2 ? (spans[1].textContent || '').trim() : '';
            return name === expectedHr && company === expectedCompany;
        }};
        let row = expectedIndex >= 0 ? rows[expectedIndex] : null;
        if (!identity(row)) row = rows.find(identity) || null;
        if (!row) return JSON.stringify({{success: false, error: 'conversation_not_found'}});
        const target = row.querySelector('.friend-content') || row;
        target.click();
        return JSON.stringify({{success: true}});
    }})()
    """
    result = evaluate(target_id, js_select)
    try:
        selected = json.loads(result) if isinstance(result, str) else result
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(selected, dict) or not selected.get("success"):
        return None
    try:
        _inspect_monitor_page(target_id, config)
    except MonitorRiskDetected:
        raise
    return target_id


def _handle_conversation(job: dict, config: dict, conversation: dict | None = None) -> str:
    """Handle a single conversation that has an HR reply.

    Returns action taken: 'stopped', 'recorded_user_reply', 'skipped_user_replied',
    'skipped_existing_resume', 'rejected', 'needs_resume', 'auto_replied',
    or 'failed'.
    """
    if stop_requested(config):
        return "stopped"
    console.print(f"\n  [bold]处理: {job['company']} - {job['title']}[/bold]")

    # Prefer the already-open chat list row from this scan. Falling back keeps
    # CLI/legacy callers working while Web monitoring avoids a duplicate page open.
    target_id = _open_scanned_conversation(job, config, conversation)
    if not target_id:
        target_id = _open_conversation(job, config)
    if not target_id:
        if stop_requested(config):
            return "stopped"
        console.print("[yellow]    无法打开对话[/yellow]")
        return "failed"

    if _wait_or_stop(config, 2):
        close_tab(target_id)
        return "stopped"

    # Extract full BOSS conversation messages.
    raw = evaluate(target_id, JS_EXTRACT_CONVERSATION)
    if stop_requested(config):
        close_tab(target_id)
        return "stopped"
    if not raw:
        close_tab(target_id)
        console.print("[yellow]    无法获取对话内容[/yellow]")
        _monitor_safety_guard(config).record_page_failure()
        return "failed"

    try:
        messages = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        close_tab(target_id)
        _monitor_safety_guard(config).record_page_failure()
        return "failed"
    if not isinstance(messages, list):
        close_tab(target_id)
        return "failed"
    messages = _reconcile_conversation_messages(messages, job)
    messages = _reconcile_outbound_chat_preview(messages, conversation)

    if not isinstance(messages, list):
        close_tab(target_id)
        _monitor_safety_guard(config).record_page_failure()
        return "failed"

    _monitor_safety_guard(config).record_page_success()

    # Check if I already replied after the last HR message
    if _check_if_i_already_replied(messages):
        recorded = _record_external_user_reply(job, messages, conversation)
        console.print(
            "[green]    ✓ 已同步 BOSS 中手动发送的回复[/green]"
            if recorded else "[dim]    我已回复，且本轮无需重复同步[/dim]"
        )
        close_tab(target_id)
        return "recorded_user_reply" if recorded else "skipped_user_replied"

    db = get_db()
    existing_pending_reply = _has_existing_pending_reply(db, job["id"], messages)
    db.close()
    if existing_pending_reply:
        console.print("[dim]    相同HR消息已有待确认回复，跳过重复处理[/dim]")
        close_tab(target_id)
        return "skipped_existing_pending"

    db = get_db()
    already_handled_reply = _has_handled_reply_for_messages(db, job["id"], messages)
    db.close()
    if already_handled_reply:
        console.print("[dim]    相同HR消息已处理过，跳过重复回复[/dim]")
        close_tab(target_id)
        return "skipped_handled_reply"

    # Check if HR is rejecting
    if _detect_rejection(messages):
        if stop_requested(config):
            close_tab(target_id)
            return "stopped"
        console.print("[dim]    HR已拒绝，标记并停止跟踪[/dim]")
        db = get_db()
        update_job_status(db, job["id"], "rejected")
        add_history(db, job["id"], "rejected", "HR回复拒绝")
        db.close()
        close_tab(target_id)
        return "rejected"

    # Check if HR is asking for resume
    resume_request_from_card = _has_resume_request_card(messages)
    if _detect_resume_request(messages):
        console.print("[cyan]    HR要求简历，生成定制化简历...[/cyan]")

        db = get_db()
        already_generated_resume = _has_generated_resume_for_job(db, job["id"])
        db.close()
        if already_generated_resume:
            console.print("[dim]    定制简历已生成过，跳过重复生成和重复记录[/dim]")
            close_tab(target_id)
            return "skipped_existing_resume"

        # Generate tailored resume, then hand off to user for manual sending
        from bosshunter.ai.resume import generate_tailored_resume, get_last_resume_failure_reason

        resume_failure_reason = ""
        try:
            resume_path = generate_tailored_resume(job["id"], config)
            if not resume_path:
                resume_failure_reason = get_last_resume_failure_reason(job["id"])
        except OperationCancelled:
            close_tab(target_id)
            return "stopped"
        except Exception as exc:
            resume_path = None
            resume_failure_reason = f"生成过程发生异常：{exc}"
            console.print(f"[red]    定制简历生成异常：{exc}[/red]")
        if stop_requested(config):
            close_tab(target_id)
            return "stopped"
        if resume_path:
            console.print(f"[bold green]    ✓ 定制简历已生成: {resume_path}[/bold green]")
            console.print(f"[bold yellow]    ⚠ 请手动发送上述简历给 {job['company']} HR[/bold yellow]")
        else:
            console.print("[yellow]    ! 定制简历生成失败，请手动处理[/yellow]")

        # Auto-send a portfolio link only when automatic replies are explicitly
        # enabled. Manual-confirmation mode must stay free of outbound messages.
        # Card-triggered requests are always recognition-only.
        portfolio_status = "未自动发送在线简历"
        if not resume_request_from_card:
            portfolio_url = config.get("profile", {}).get("portfolio_url", "")
            auto_reply_enabled = config.get("monitor", {}).get(
                "auto_reply_hr_questions",
                False,
            )
            if _check_if_portfolio_sent(messages, portfolio_url):
                portfolio_status = "在线简历此前已发送"
                console.print("[dim]    在线简历链接已发过，跳过[/dim]")
            elif not auto_reply_enabled:
                console.print("[yellow]    自动回复已关闭，在线简历链接等待手动处理[/yellow]")
            else:
                if _wait_or_stop(config, 2):
                    close_tab(target_id)
                    return "stopped"
                link_msg = f"这是我的在线简历，方便您查看：{portfolio_url}"
                if stop_requested(config):
                    close_tab(target_id)
                    return "stopped"
                if _send_message_in_chat(target_id, link_msg):
                    portfolio_status = "在线简历已发送"
                    console.print("[green]    ✓ 在线简历链接已发送[/green]")
                else:
                    portfolio_status = "在线简历发送失败"
                    console.print("[yellow]    ! 在线简历链接发送失败[/yellow]")

        if not resume_path:
            if stop_requested(config):
                close_tab(target_id)
                return "stopped"
            history_detail = _build_resume_failure_detail(
                messages,
                resume_failure_reason or "定制简历生成失败，未获得更具体的错误信息",
            )
            db = get_db()
            add_history(db, job["id"], "resume_failed", history_detail)
            db.close()
            close_tab(target_id)
            return "failed"

        # Update status to needs_resume so user knows to send the PDF
        if resume_request_from_card:
            history_detail = _build_reply_detail(
                messages,
                f"附件简历卡片请求已识别，未自动发送在线简历，定制PDF待手动发送: {resume_path}",
                "needs_resume.v1",
            )
        else:
            history_detail = _build_reply_detail(
                messages,
                f"{portfolio_status}，定制PDF待手动发送: {resume_path}",
                "needs_resume.v1",
            )

        if stop_requested(config):
            close_tab(target_id)
            return "stopped"
        db = get_db()
        update_job_status(db, job["id"], "needs_resume")
        add_history(db, job["id"], "needs_resume", history_detail)
        db.close()

        close_tab(target_id)
        return "needs_resume"

    db = get_db()
    dismissed_pending_reply = _has_dismissed_pending_reply(db, job["id"], messages)
    db.close()
    if dismissed_pending_reply:
        console.print("[dim]    该回复建议已放弃，跳过本轮[/dim]")
        close_tab(target_id)
        return "skipped_dismissed_reply"

    # HR replied but not asking for resume — generate a natural reply
    console.print("[cyan]    生成自动回复...[/cyan]")
    try:
        reply = _generate_auto_reply(messages, job, config)
    except OperationCancelled:
        close_tab(target_id)
        return "stopped"
    if stop_requested(config):
        close_tab(target_id)
        return "stopped"
    if not reply:
        console.print("[yellow]    回复生成失败[/yellow]")
        close_tab(target_id)
        return "failed"

    console.print(f"[dim]    回复内容: {reply[:80]}...[/dim]")

    if not config.get("monitor", {}).get("auto_reply_hr_questions", False):
        if stop_requested(config):
            close_tab(target_id)
            return "stopped"
        console.print("[yellow]    已生成回复建议，等待监测执行中确认[/yellow]")
        db = get_db()
        add_history(
            db,
            job["id"],
            "reply_pending",
            _build_reply_detail(messages, reply, conversation=conversation),
        )
        db.close()
        close_tab(target_id)
        return "reply_pending"

    # Send the reply
    if stop_requested(config):
        close_tab(target_id)
        return "stopped"
    if _send_message_in_chat(target_id, reply):
        console.print("[green]    ✓ 自动回复已发送[/green]")
        db = get_db()
        add_history(
            db,
            job["id"],
            "auto_replied",
            _build_reply_detail(messages, reply, "auto_replied.v1", conversation=conversation),
        )
        db.close()
        close_tab(target_id)
        return "auto_replied"
    else:
        console.print("[yellow]    ! 发送失败[/yellow]")
        close_tab(target_id)
        return "failed"


def _row_text(row, key: str) -> str:
    """Read a text value from sqlite Row/dict-like objects defensively."""
    if row is None:
        return ""
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return ""
    return value if isinstance(value, str) else ""


def _get_unresolved_pending_reply(db, job_id: str):
    """Return the latest reply suggestion only when no later decision resolved it."""
    return db.execute(
        """
        SELECT h.id, h.detail
        FROM history h
        WHERE h.job_id = ?
          AND h.action = 'reply_pending'
          AND NOT EXISTS (
              SELECT 1
              FROM history r
              WHERE r.job_id = h.job_id
                AND r.id > h.id
                AND r.action IN ('reply_dismissed', 'replied', 'auto_replied')
          )
        ORDER BY h.id DESC
        LIMIT 1
        """,
        (job_id,),
    ).fetchone()


def _get_latest_handled_reply(db, job_id: str):
    """Return the latest resolved/handled monitor action carrying an HR fingerprint."""
    return db.execute(
        """
        SELECT action, detail
        FROM history
        WHERE job_id = ?
          AND action IN ('reply_dismissed', 'replied', 'auto_replied', 'needs_resume', 'resume_failed')
        ORDER BY id DESC
        LIMIT 1
        """,
        (job_id,),
    ).fetchone()


def _outbound_reply_matches_chat_list(detail: str, conversation: dict) -> bool:
    """Match a recorded outbound message to BOSS's possibly truncated preview."""
    payload = _parse_reply_detail(detail)
    value = (
        payload.get("last_outbound_message")
        or payload.get("manual_reply")
        or payload.get("ai_reply")
    )
    expected = _normalized_message_text(value if isinstance(value, str) else "")
    current = _normalized_message_text(str(conversation.get("last_message") or ""))
    return bool(current and expected) and (
        current == expected
        or (len(current) >= 12 and expected.startswith(current))
    )


def _has_recorded_outbound_reply(db, job_id: str, conversation: dict) -> bool:
    rows = db.execute(
        """
        SELECT detail FROM history
        WHERE job_id = ? AND action IN ('replied', 'auto_replied')
        ORDER BY id DESC LIMIT 20
        """,
        (job_id,),
    ).fetchall()
    return any(_outbound_reply_matches_chat_list(_row_text(row, "detail"), conversation) for row in rows)


def _record_external_user_reply(
    job: dict,
    messages: list[dict],
    conversation: dict | None = None,
) -> bool:
    """Persist a BOSS-side manual reply once without generating or sending text."""
    detail = _build_external_reply_detail(messages, conversation)
    if not detail:
        return False
    payload = _parse_reply_detail(detail)
    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT detail FROM history
            WHERE job_id = ? AND action IN ('replied', 'auto_replied')
            ORDER BY id DESC LIMIT 20
            """,
            (job["id"],),
        ).fetchall()
        if any(
            _reply_fingerprint_from_detail(_row_text(row, "detail")) == payload["reply_fingerprint"]
            and _parse_reply_detail(_row_text(row, "detail")).get("manual_reply") == payload["manual_reply"]
            for row in rows
        ):
            return False
        if str(job.get("status") or "") == "sent":
            update_job_status(db, job["id"], "replied")
        add_history(db, job["id"], "replied", detail)
        return True
    finally:
        db.close()


def _has_handled_reply_for_messages(db, job_id: str, messages: list[dict]) -> bool:
    """Keep one outbound decision per HR turn while allowing later HR turns."""
    handled = _get_latest_handled_reply(db, job_id)
    if handled is None:
        return False
    if _row_text(handled, "action") not in {"replied", "auto_replied"}:
        return False
    handled_fingerprint = _reply_fingerprint_from_detail(_row_text(handled, "detail"))
    if not handled_fingerprint:
        return False
    return handled_fingerprint == _reply_fingerprint_from_messages(messages)


def _pending_matches_chat_list(pending_detail: str, conversation: dict) -> bool:
    """Avoid reopening an unresolved suggestion unless the chat list proves it changed."""
    if conversation.get("has_unread"):
        return False
    current_last_message = _reply_fingerprint_from_hr_question(conversation.get("last_message", ""))
    stored_last_message = _chat_list_fingerprint_from_detail(pending_detail)
    if stored_last_message:
        return stored_last_message == current_last_message

    payload = _parse_reply_detail(pending_detail)
    pending_fingerprint = _reply_fingerprint_from_detail(pending_detail)
    if not pending_fingerprint:
        # Legacy suggestions have no safe message identity. Keep them idempotent until
        # the user confirms or dismisses them rather than repeatedly opening the chat.
        return True
    hr_question = payload.get("hr_question")
    if isinstance(hr_question, str) and hr_question.strip():
        pending_last_message = _reply_fingerprint_from_hr_question(hr_question.splitlines()[-1])
    else:
        pending_last_message = pending_fingerprint
    return bool(current_last_message) and pending_last_message == current_last_message


def _handled_reply_matches_chat_list(detail: str, conversation: dict) -> bool:
    """Match a previously handled HR message without treating legacy plain text as proof."""
    if conversation.get("has_unread"):
        return False
    fingerprint = _reply_fingerprint_from_detail(detail)
    if not fingerprint:
        return False
    current_last_message = _reply_fingerprint_from_hr_question(conversation.get("last_message", ""))
    payload = _parse_reply_detail(detail)
    stored_last_message = _chat_list_fingerprint_from_detail(detail)
    if not stored_last_message:
        hr_question = payload.get("hr_question") or payload.get("pending_hr_question")
        if isinstance(hr_question, str) and hr_question.strip():
            stored_last_message = _reply_fingerprint_from_hr_question(hr_question.splitlines()[-1])
    return bool(current_last_message) and stored_last_message == current_last_message


def _has_existing_pending_reply(db, job_id: str, messages: list[dict]) -> bool:
    """Return true only for the same still-unresolved HR message sequence."""
    pending = _get_unresolved_pending_reply(db, job_id)
    if pending is None:
        return False
    pending_fingerprint = _reply_fingerprint_from_detail(_row_text(pending, "detail"))
    if not pending_fingerprint:
        return True
    return pending_fingerprint == _reply_fingerprint_from_messages(messages)


def _has_generated_resume_for_job(db, job_id: str) -> bool:
    """Return true when this job already has a generated resume/request result."""
    row = db.execute(
        "SELECT status, resume_path FROM jobs WHERE id = ? AND deleted_at IS NULL",
        (job_id,),
    ).fetchone()
    status = _row_text(row, "status")
    resume_path = _row_text(row, "resume_path").strip()
    return status == "resume_sent" or bool(resume_path)


def _has_follow_up_history(db, job_id: str) -> bool:
    """Return true when a follow-up was already recorded for this job."""
    row = db.execute(
        "SELECT 1 FROM history WHERE job_id = ? AND action = 'follow_up_sent' LIMIT 1",
        (job_id,),
    ).fetchone()
    return row is not None


def _has_dismissed_pending_reply(db, job_id: str, messages: list[dict] | None = None) -> bool:
    """Return true when the latest manual reply decision was to dismiss it."""
    row = db.execute(
        """
        SELECT action, detail
        FROM history
        WHERE job_id = ?
          AND action IN ('reply_pending', 'reply_dismissed', 'auto_replied', 'replied')
        ORDER BY id DESC
        LIMIT 1
        """,
        (job_id,),
    ).fetchone()
    if _row_text(row, "action") != "reply_dismissed":
        return False
    dismissed_fingerprint = _reply_fingerprint_from_detail(_row_text(row, "detail"))
    if not dismissed_fingerprint or messages is None:
        return True
    return dismissed_fingerprint == _reply_fingerprint_from_messages(messages)


def _check_follow_ups(config: dict, throttle, replied_job_ids: set | None = None) -> int:
    """Send a follow-up message to jobs that have been in 'sent' status for configured hours with no HR reply.

    replied_job_ids: set of job IDs that already had HR replies this cycle — always skip these.
    Respects config: follow_up.enabled, follow_up.interval_hours, follow_up.max_times, follow_up.skip_weekends.
    Returns count of follow-ups sent.
    """
    from datetime import datetime, timedelta

    stop_event = get_stop_event(config)
    if stop_event and stop_event.is_set():
        return 0
    follow_up_cfg = config.get("follow_up", {})
    if not follow_up_cfg.get("enabled", False):
        return 0

    # Skip weekends if configured
    if follow_up_cfg.get("skip_weekends", True):
        today = datetime.now().weekday()
        if today >= 5:  # Saturday=5, Sunday=6
            return 0

    interval_hours = follow_up_cfg.get("interval_hours", 48)

    db = get_db()
    sent_jobs = get_jobs_by_status(db, "sent")

    if not sent_jobs:
        db.close()
        return 0

    cutoff = datetime.now() - timedelta(hours=interval_hours)
    stale_jobs = []
    for job in sent_jobs:
        # Skip any job that already had an HR reply this cycle
        if replied_job_ids and job["id"] in replied_job_ids:
            console.print(f"[dim]  跟进跳过（本轮已有HR回复）: {job['company']}[/dim]")
            continue
        updated = job.get("updated_at", "")
        if not updated:
            continue
        try:
            job_time = datetime.fromisoformat(updated.replace("Z", "+00:00")) if "T" in updated else datetime.strptime(updated, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue
        if job_time < cutoff:
            stale_jobs.append(job)

    if not stale_jobs:
        db.close()
        return 0

    console.print(f"\n[cyan]发现 {len(stale_jobs)} 个超过{interval_hours}小时无回复的对话，尝试跟进...[/cyan]")

    count = 0
    for job in stale_jobs:
        if stop_event and stop_event.is_set():
            break
        if _has_follow_up_history(db, job["id"]):
            console.print(f"[dim]  跟进跳过（已记录过跟进）: {job['company']}[/dim]")
            continue

        try:
            follow_up_msg = _generate_follow_up(job, config)
        except OperationCancelled:
            break
        if not follow_up_msg:
            continue
        if stop_event and stop_event.is_set():
            break

        target_id = _open_conversation(job, config)
        if not target_id:
            if stop_event and stop_event.is_set():
                break
            continue

        if _wait_or_stop(config, 2):
            close_tab(target_id)
            break

        if stop_event and stop_event.is_set():
            close_tab(target_id)
            break
        if _send_message_in_chat(target_id, follow_up_msg):
            console.print(f"[green]  ✓ 跟进: {job['company']} - {job['title']}[/green]")
            update_job_status(db, job["id"], "follow_up_sent")
            add_history(db, job["id"], "follow_up_sent", follow_up_msg[:100])
            count += 1
        else:
            console.print(f"[yellow]  ! 跟进失败: {job['company']}[/yellow]")

        close_tab(target_id)

    db.close()
    return count


def _generate_follow_up(job: dict, config: dict) -> str | None:
    """Generate a short follow-up message via AI."""
    interval_hours = config.get("follow_up", {}).get("interval_hours", 48)
    prompt = f"""你是一位求职者，之前在BOSS直聘上给HR发了招呼消息，但{interval_hours}小时没收到回复。请生成一条简短的跟进消息。

## 岗位信息
- 职位：{job.get('title', '')}
- 公司：{job.get('company', '')}

## 要求
1. 字数控制在20-40字
2. 语气自然，不卑不亢，像真人发的
3. 不要太正式，不要用"您好"开头
4. 表达还在关注这个岗位，期待有机会沟通
5. 不要直接复述"48小时没回复"这种话

请直接输出跟进消息文本，不要加任何标记。"""

    return _call_claude(prompt, config)


def monitor_and_send_resumes(config: dict) -> dict:
    """Full monitoring cycle:
    1. Detect HR replies
    2. For each reply: check if user already replied → skip or auto-handle
    3. If HR asks resume → optimize + send PDF + send portfolio link
    4. If HR just replied → generate natural reply

    Returns summary dict with counts.
    """
    throttle_config = config.get("throttle", {})
    stop_event = get_stop_event(config)
    if stop_event and stop_event.is_set():
        return {"skipped": 0, "pending": 0, "replied": 0, "needs_resume": 0, "rejected": 0, "failed": 0}

    # Time window check (09:00-16:00)
    window_checker = SendWindowChecker(throttle_config.get("send_windows", ["09:00-16:00"]))
    if not window_checker.is_active():
        console.print("[yellow]当前不在工作时间窗口内 (09:00-16:00)[/yellow]")
        return {"skipped": 0, "pending": 0, "replied": 0, "needs_resume": 0, "rejected": 0, "failed": 0}

    operation_multiplier = get_boss_operation_interval_multiplier(config)
    throttle = RequestThrottle(
        _positive_interval_seconds(throttle_config.get("interval_min"), 60) * operation_multiplier,
        _positive_interval_seconds(throttle_config.get("interval_max"), 180) * operation_multiplier,
    )
    monitor_config = dict(config)
    monitor_config["_monitor_request_throttle"] = throttle
    monitor_config["_monitor_safety_guard"] = MonitorSafetyGuard(config)
    monitor_config["_platform_access_guard"] = TransientPlatformAccessGuard(
        config,
        "monitor",
        get_db,
    )

    summary = {
        "skipped": 0,
        "pending": 0,
        "replied": 0,
        "needs_resume": 0,
        "rejected": 0,
        "failed": 0,
    }

    # Step 1: Check for HR replies — MUST run first
    console.print("\n[bold cyan]═══ 第一步：处理HR回复 ═══[/bold cyan]")
    try:
        replied_conversations = check_replies(monitor_config)
    except MonitorRiskDetected as exc:
        console.print(f"[red]⚠ 监测检测到风险信号 {exc.kind}，已立即停止[/red]")
        summary["stop_reason"] = exc.kind
        return summary
    if stop_event and stop_event.is_set():
        return summary

    # Collect all job IDs that had HR replies this cycle (used to block follow-up)
    replied_job_ids: set = set()

    if replied_conversations:
        console.print(f"[green]检测到 {len(replied_conversations)} 个需要处理的对话[/green]")

        # Step 2: Handle each reply conversation
        for item in replied_conversations:
            if stop_event and stop_event.is_set():
                break
            job = item["job"]
            replied_job_ids.add(job["id"])
            try:
                action = _handle_conversation(job, monitor_config, item.get("conversation"))
            except MonitorRiskDetected as exc:
                console.print(f"[red]⚠ 监测检测到风险信号 {exc.kind}，已立即停止[/red]")
                summary["stop_reason"] = exc.kind
                break

            if action == "stopped":
                break
            if action in (
                "recorded_user_reply",
                "skipped_user_replied",
                "skipped_existing_resume",
                "skipped_existing_pending",
                "skipped_handled_reply",
                "skipped_dismissed_reply",
            ):
                summary["skipped"] += 1
            elif action == "auto_replied":
                summary["replied"] += 1
            elif action == "needs_resume":
                summary["needs_resume"] += 1
            elif action == "reply_pending":
                summary["pending"] += 1
            elif action == "rejected":
                summary["rejected"] += 1
            else:
                summary["failed"] += 1

        console.print("\n[bold green]═══ 回复处理完成 ═══[/bold green]")
        console.print(f"  跳过(已手动回复): {summary['skipped']}")
        if summary["pending"]:
            console.print(f"  [bold yellow]待确认回复: {summary['pending']}[/bold yellow]")
        console.print(f"  自动回复: {summary['replied']}")
        if summary.get("needs_resume"):
            console.print(f"  [bold yellow]待手动发简历: {summary['needs_resume']}（定制简历已生成，请手动发送）[/bold yellow]")
        if summary["rejected"]:
            console.print(f"  [dim]拒绝: {summary['rejected']}[/dim]")
        if summary["failed"]:
            console.print(f"  [yellow]失败: {summary['failed']}[/yellow]")
    else:
        console.print("[dim]本轮无新HR回复[/dim]")

    # Step 3: Follow up ONLY on jobs with absolutely no HR reply
    # Pass replied_job_ids so follow-up skips any job touched this cycle
    if (stop_event and stop_event.is_set()) or summary.get("stop_reason"):
        return summary
    console.print("\n[bold cyan]═══ 第二步：跟进无回复岗位 ═══[/bold cyan]")
    try:
        follow_up_count = _check_follow_ups(monitor_config, throttle, replied_job_ids=replied_job_ids)
    except MonitorRiskDetected as exc:
        console.print(f"[red]⚠ 监测检测到风险信号 {exc.kind}，已立即停止[/red]")
        summary["stop_reason"] = exc.kind
        return summary
    if follow_up_count:
        console.print(f"  二次跟进: {follow_up_count}")
        summary["follow_up"] = follow_up_count

    return summary


def process_detected_reply(job_id: str, config: dict) -> dict:
    """Process one detected BOSS reply without any automatic outbound action."""
    safe_config = dict(config)
    safe_config["monitor"] = {
        **config.get("monitor", {}),
        "auto_reply_hr_questions": False,
        "max_conversations_per_cycle": 1,
    }
    safe_config["follow_up"] = {**config.get("follow_up", {}), "enabled": False}
    safe_config["throttle"] = {**config.get("throttle", {}), "send_windows": []}
    safe_config["_monitor_job_ids"] = [str(job_id)]
    safe_config["_monitor_reuse_chat_tab"] = True
    safe_config["_monitor_runtime_state"] = {}
    try:
        return monitor_and_send_resumes(safe_config)
    finally:
        close_monitor_chat_target(safe_config)
