"""Cooperative cancellation helpers for workbench tasks."""

from __future__ import annotations

from queue import Empty, Queue
from threading import Event, Thread
from typing import Callable, TypeVar


T = TypeVar("T")


class OperationCancelled(RuntimeError):
    """Raised when the user stops an in-progress workbench operation."""


def get_stop_event(config: dict | None) -> Event | None:
    """Return the workbench stop event carried in a runtime config."""
    if not isinstance(config, dict):
        return None
    event = config.get("_workbench_stop_event")
    return event if isinstance(event, Event) else None


def stop_requested(config: dict | None) -> bool:
    """Return whether the current workbench task has been asked to stop."""
    event = get_stop_event(config)
    return bool(event and event.is_set())


def run_cancellable(
    operation: Callable[[], T],
    config: dict | None,
    *,
    poll_seconds: float = 0.1,
) -> T:
    """Run a blocking, side-effect-free operation while polling the stop event.

    Python cannot forcibly interrupt an in-flight HTTPS request. Running the request
    in a daemon thread lets the workbench task stop immediately; the abandoned
    request may finish later, but its result is discarded.
    """
    stop_event = get_stop_event(config)
    if stop_event is None:
        return operation()
    if stop_event.is_set():
        raise OperationCancelled("用户已请求停止")

    results: Queue[tuple[bool, object]] = Queue(maxsize=1)

    def worker() -> None:
        try:
            results.put((True, operation()))
        except Exception as exc:  # Preserve the original provider exception.
            results.put((False, exc))

    Thread(target=worker, name="bosshunter-cancellable-call", daemon=True).start()

    timeout = max(float(poll_seconds), 0.01)
    while True:
        if stop_event.is_set():
            raise OperationCancelled("用户已请求停止")
        try:
            succeeded, value = results.get(timeout=timeout)
        except Empty:
            continue
        if stop_event.is_set():
            raise OperationCancelled("用户已请求停止")
        if succeeded:
            return value  # type: ignore[return-value]
        raise value  # type: ignore[misc]
