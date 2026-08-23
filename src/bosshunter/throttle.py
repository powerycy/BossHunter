"""Throttle module - Gaussian delay + burst penalty for anti-detection."""

import random
import time
from collections import deque
from datetime import datetime
from threading import Event


class RequestThrottle:
    """Rate limiter with Gaussian-distributed delays and burst detection."""

    def __init__(self, delay_min: float = 60.0, delay_max: float = 180.0) -> None:
        self._delay_min = delay_min
        self._delay_max = delay_max
        self._last_request_time = 0.0
        self._recent_times: deque[float] = deque(maxlen=12)

    def wait(self, stop_event: Event | None = None) -> bool:
        """Block until it's safe to send the next request."""
        elapsed = time.time() - self._last_request_time
        mean = (self._delay_min + self._delay_max) / 2
        std = (self._delay_max - self._delay_min) / 4
        base_sleep = max(0, random.gauss(mean, std) - elapsed)

        # 5% chance of a longer pause — mimics human hesitation
        if random.random() < 0.05:
            base_sleep += random.uniform(2.0, 5.0)

        burst = self._burst_penalty()
        total = max(0, base_sleep + burst)
        if total > 0:
            if stop_event and stop_event.wait(total):
                return True
            if not stop_event:
                time.sleep(total)
        return bool(stop_event and stop_event.is_set())

    def mark(self) -> None:
        """Record that a request was just sent."""
        now = time.time()
        self._last_request_time = now
        self._recent_times.append(now)

    @property
    def has_marked_request(self) -> bool:
        """Return whether at least one request has been recorded."""
        return self._last_request_time > 0

    def _burst_penalty(self) -> float:
        """Extra delay when requests arrive in bursts."""
        if not self._recent_times:
            return 0.0
        now = time.time()
        recent_15s = sum(1 for ts in self._recent_times if now - ts <= 15)
        recent_45s = sum(1 for ts in self._recent_times if now - ts <= 45)
        if recent_45s >= 6:
            return random.uniform(4.0, 7.0)
        if recent_15s >= 3:
            return random.uniform(1.2, 2.8)
        return 0.0


class PageThrottle:
    """Lighter throttle for page navigation (scraping)."""

    def __init__(self, delay_min: float = 2.0, delay_max: float = 5.0) -> None:
        self._delay_min = delay_min
        self._delay_max = delay_max

    def wait(self, stop_event: Event | None = None) -> bool:
        delay = random.uniform(self._delay_min, self._delay_max)
        if stop_event:
            return stop_event.wait(delay)
        time.sleep(delay)
        return False


class SendWindowChecker:
    """Checks if current time falls within configured send windows."""

    def __init__(self, windows: list[str]) -> None:
        """Parse windows like ["09:00-12:00", "14:00-16:00"]."""
        self._windows: list[tuple[int, int, int, int]] = []
        for w in windows:
            parts = w.strip().split("-")
            if len(parts) != 2:
                continue
            start_h, start_m = self._parse_time(parts[0])
            end_h, end_m = self._parse_time(parts[1])
            if start_h >= 0 and end_h >= 0:
                self._windows.append((start_h, start_m, end_h, end_m))

    def is_active(self) -> bool:
        """Check if current local time is within any send window."""
        if not self._windows:
            return True  # No windows configured = always active
        cur_minutes = self._current_minutes()
        for start_h, start_m, end_h, end_m in self._windows:
            start_minutes = start_h * 60 + start_m
            end_minutes = end_h * 60 + end_m
            if start_minutes <= cur_minutes < end_minutes:
                return True
        return False

    def has_valid_windows(self) -> bool:
        """Return whether at least one configured window parsed successfully."""
        return bool(self._windows)

    def latest_end_time_reached(self) -> bool:
        """Return whether local time is past the latest configured window end."""
        if not self._windows:
            return False
        latest_end = max(end_h * 60 + end_m for _, _, end_h, end_m in self._windows)
        return self._current_minutes() >= latest_end

    def latest_end_datetime(self, now: datetime | None = None) -> datetime | None:
        """Return today's latest configured window end in local time."""
        if not self._windows:
            return None
        current = now or datetime.now()
        latest_end = max(end_h * 60 + end_m for _, _, end_h, end_m in self._windows)
        end_hour, end_minute = divmod(latest_end, 60)
        return current.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)

    def next_window_info(self) -> str:
        """Describe when next window opens."""
        if not self._windows:
            return "无窗口限制"
        cur_minutes = self._current_minutes()
        # Find next window start
        for start_h, start_m, end_h, end_m in sorted(self._windows):
            start_minutes = start_h * 60 + start_m
            if cur_minutes < start_minutes:
                return f"下个窗口: {start_h:02d}:{start_m:02d}"
        # All windows have passed today
        if self._windows:
            first = self._windows[0]
            return f"今日窗口已过，明日 {first[0]:02d}:{first[1]:02d} 开始"
        return ""

    @staticmethod
    def _current_minutes() -> int:
        now = datetime.now()
        return now.hour * 60 + now.minute

    @staticmethod
    def _parse_time(s: str) -> tuple[int, int]:
        """Parse 'HH:MM' to (hour, minute)."""
        parts = s.strip().split(":")
        if len(parts) == 2:
            try:
                hour, minute = int(parts[0]), int(parts[1])
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    return hour, minute
            except ValueError:
                pass
        return -1, -1


class ProgressiveBackoff:
    """Progressive error-based backoff for send failures."""

    def __init__(self) -> None:
        self._consecutive_errors = 0

    def record_error(self) -> float:
        """Record an error and return recommended pause duration in seconds."""
        self._consecutive_errors += 1
        if self._consecutive_errors >= 3:
            return 1800.0  # 30 minutes
        elif self._consecutive_errors == 2:
            return 120.0
        else:
            return 60.0

    def record_success(self) -> None:
        """Reset error counter on success."""
        self._consecutive_errors = 0

    @property
    def should_pause_long(self) -> bool:
        """Whether we've hit too many errors and should stop."""
        return self._consecutive_errors >= 3


def should_take_day_off(probability: float = 0.05) -> bool:
    """Random chance to skip a day entirely (anti-pattern detection)."""
    return random.random() < probability
