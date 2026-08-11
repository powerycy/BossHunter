"""Persistent, cooperative workbench task runner."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread, Timer
from typing import Any, Callable
from uuid import uuid4

from bosshunter.task_store import (
    decode_json,
    load_tasks,
    mark_orphaned_tasks_paused,
    redact_task_snapshot,
    save_task,
)
from bosshunter.throttle import SendWindowChecker


MODE_LABELS = {
    "full": "运行全流程",
    "collect": "单独采集",
    "score": "单独 AI 评分",
    "rescore": "重新评分",
    "greet": "生成招呼语",
    "monitor": "单独监测",
    "deliver": "确认投递",
}

TERMINAL_STATUSES = {"completed", "completed_with_errors", "paused", "failed", "stopped"}
ACTIVE_STATUSES = {"running", "pausing", "stopping", "paused"}
DEADLINE_MODES = {"full", "monitor", "deliver"}


class TaskAlreadyRunningError(RuntimeError):
    """Raised when a mutually exclusive task exists."""

    def __init__(self, message: str, existing_task: "WorkbenchTask | None" = None):
        super().__init__(message)
        self.existing_task = existing_task


@dataclass
class WorkbenchTask:
    id: str
    mode: str
    label: str
    status: str = "running"
    stage: str = "准备任务"
    progress: dict[str, Any] = field(default_factory=dict)
    checkpoint: dict[str, Any] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished_at: str | None = None
    deadline_at: str | None = None
    stop_reason: str | None = None
    profile_id: str | None = None
    context_refs: dict[str, Any] = field(default_factory=dict)
    config_snapshot: dict[str, Any] = field(default_factory=dict, repr=False)
    pause_requested: Event = field(default_factory=Event, repr=False)
    stop_requested: Event = field(default_factory=Event, repr=False)
    context: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "WorkbenchTask":
        task = cls(
            id=str(row["id"]),
            mode=str(row.get("mode") or "full"),
            label=str(row.get("label") or MODE_LABELS.get(row.get("mode"), "后台任务")),
            status=str(row.get("status") or "paused"),
            stage=str(row.get("stage") or "任务已启动，等待首个进度"),
            progress=decode_json(row.get("progress_json"), {}),
            checkpoint=decode_json(row.get("checkpoint_json"), {}),
            logs=decode_json(row.get("logs_json"), []),
            error=row.get("error"),
            created_at=str(row.get("created_at") or datetime.now().isoformat(timespec="seconds")),
            updated_at=str(row.get("updated_at") or datetime.now().isoformat(timespec="seconds")),
            finished_at=row.get("finished_at"),
            stop_reason=row.get("stop_reason"),
            profile_id=row.get("profile_id"),
            context_refs=decode_json(row.get("context_refs_json"), {}),
            config_snapshot=decode_json(row.get("config_snapshot_json"), {}),
        )
        if not isinstance(task.logs, list):
            task.logs = []
        if not isinstance(task.progress, dict):
            task.progress = {}
        if not isinstance(task.checkpoint, dict):
            task.checkpoint = {}
        if not isinstance(task.context_refs, dict):
            task.context_refs = {}
        if task.status in {"paused", "stopping", "pausing"}:
            task.pause_requested.set() if task.status in {"paused", "pausing"} else None
        return task

    def snapshot(self) -> dict[str, Any]:
        is_active = self.status in {"running", "pausing", "stopping"}
        recoverable = self.status == "paused" and bool(self.checkpoint or self.config_snapshot)
        return {
            "id": self.id,
            "mode": self.mode,
            "label": self.label,
            "status": self.status,
            "stage": self.stage or "任务已启动，等待首个进度",
            "progress": deepcopy(self.progress),
            "checkpoint_summary": _checkpoint_summary(self.checkpoint),
            "can_pause": is_active and self.status == "running",
            "can_resume": recoverable,
            "recoverable": recoverable,
            "logs": list(self.logs),
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "deadline_at": self.deadline_at,
            "stop_reason": self.stop_reason,
            "pause_requested": self.pause_requested.is_set(),
            "stop_requested": self.stop_requested.is_set(),
            "profile_id": self.profile_id,
            "context_refs": deepcopy(self.context_refs),
        }


Executor = Callable[[WorkbenchTask, dict], None]


class WorkbenchTaskRunner:
    def __init__(self, executors: dict[str, Executor] | None = None, store_path: Path | None = None):
        self._executors = executors or {}
        self._tasks: dict[str, WorkbenchTask] = {}
        self._threads: dict[str, Thread] = {}
        self._deadline_timers: dict[str, Timer] = {}
        self._lock = Lock()
        self._store_path: Path | None = None
        self._loaded_store_path: Path | None = None
        if store_path:
            self.set_store_path(store_path)

    def set_store_path(self, path: Path | str | None) -> None:
        """Bind persistence to the current runtime data directory."""
        with self._lock:
            self._store_path = Path(path).resolve() if path else None
            self._loaded_store_path = None

    def start(self, mode: str, config: dict) -> dict:
        if mode not in MODE_LABELS:
            raise ValueError(f"Unsupported workbench mode: {mode}")
        with self._lock:
            self._ensure_loaded_locked()
            active = self._active_task_locked()
            if active:
                raise TaskAlreadyRunningError(
                    f"当前已有后台任务「{active.label}」可恢复或正在运行，请先处理该任务",
                    existing_task=active,
                )
            task = WorkbenchTask(
                id=str(uuid4()),
                mode=mode,
                label=MODE_LABELS[mode],
                config_snapshot=redact_task_snapshot(config),
            )
            task.profile_id = str(config.get("profile_id")) if config.get("profile_id") else None
            task.context_refs = deepcopy(config.get("_context_refs") or {})
            deadline = _deadline_from_config(mode, config)
            if deadline:
                task.deadline_at = deadline.isoformat(timespec="seconds")
            self._tasks[task.id] = task
            self._persist(task)
            if deadline and deadline <= datetime.now():
                task.stop_requested.set()
                task.status = "stopped"
                task.stop_reason = "今日发送时间窗口已截止，后台未启动"
                task.logs.append(task.stop_reason)
                task.updated_at = _now()
                task.finished_at = _now()
                self._persist(task)
                return task.snapshot()
            thread = Thread(target=self._run, args=(task, deepcopy(config)), daemon=True)
            self._threads[task.id] = thread
            if deadline:
                delay_seconds = max((deadline - datetime.now()).total_seconds(), 0)
                timer = Timer(delay_seconds, self._stop_at_deadline, args=(task.id,))
                timer.daemon = True
                self._deadline_timers[task.id] = timer
                timer.start()
            thread.start()
            return task.snapshot()

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded_locked()
            active = self._active_task_locked()
            tasks = [task.snapshot() for task in self._tasks.values()]
            return {"active": active.snapshot() if active else None, "last_task": tasks[-1] if tasks else None, "tasks": tasks}

    def recoverable(self) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_loaded_locked()
            return [task.snapshot() for task in self._tasks.values() if task.status == "paused"]

    def pause(self, task_id: str, reason: str = "用户已请求暂停") -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            if task.status in TERMINAL_STATUSES and task.status != "paused":
                return task.snapshot()
            if task.status == "paused":
                return task.snapshot()
            task.pause_requested.set()
            task.status = "pausing"
            task.stop_reason = reason
            if not task.logs or task.logs[-1] != reason:
                task.logs.append(reason)
            task.updated_at = _now()
            self._persist(task)
            self._wake_confirmation(task)
            return task.snapshot()

    def resume(self, task_id: str, config: dict | None = None) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            if task.status != "paused":
                return task.snapshot()
            task.pause_requested.clear()
            task.stop_requested.clear()
            task.status = "running"
            task.stop_reason = None
            task.error = None
            task.updated_at = _now()
            runtime_config = _resume_config(task.config_snapshot, config or {})
            self._persist(task)
            thread = Thread(target=self._run, args=(task, runtime_config), daemon=True)
            self._threads[task.id] = thread
            thread.start()
            return task.snapshot()

    def stop(self, task_id: str, reason: str = "用户已请求停止") -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            if task.status in TERMINAL_STATUSES:
                if task.status == "paused":
                    task.status = "stopped"
                    task.checkpoint = {}
                    task.stop_reason = reason
                    task.finished_at = _now()
                    task.updated_at = _now()
                    self._persist(task)
                return task.snapshot()
            task.stop_requested.set()
            task.status = "stopping"
            task.stop_reason = reason
            if not task.logs or task.logs[-1] != reason:
                task.logs.append(reason)
            task.updated_at = _now()
            self._persist(task)
            self._wake_confirmation(task)
            return task.snapshot()

    def end(self, task_id: str, reason: str = "用户已结束任务") -> dict:
        return self.stop(task_id, reason)

    def wait(self, timeout: float | None = None) -> None:
        for thread in list(self._threads.values()):
            thread.join(timeout=timeout)

    def set_stage(self, task: WorkbenchTask, stage: str, progress: dict[str, Any] | None = None) -> None:
        task.stage = str(stage)
        if progress is not None:
            task.progress = deepcopy(progress)
        task.updated_at = _now()
        self._persist(task)

    def _run(self, task: WorkbenchTask, config: dict) -> None:
        config = deepcopy(config)
        config["_workbench_stop_event"] = task.stop_requested
        config["_workbench_pause_event"] = task.pause_requested
        try:
            executor = self._executors.get(task.mode)
            if executor:
                executor(task, config)
            with self._lock:
                if task.stop_requested.is_set() or task.status == "stopping":
                    task.status = "stopped"
                    task.checkpoint = {}
                elif task.pause_requested.is_set() or task.context.get("outcome") == "paused":
                    task.status = "paused"
                elif task.context.get("outcome") in {"completed_with_errors", "failed"}:
                    task.status = task.context["outcome"]
                else:
                    task.status = "completed"
                task.updated_at = _now()
                if task.status in TERMINAL_STATUSES and task.status != "paused":
                    task.finished_at = task.finished_at or _now()
                self._persist(task)
        except Exception as exc:
            with self._lock:
                if task.stop_requested.is_set() or task.status == "stopping":
                    task.status = "stopped"
                    task.error = None
                    task.checkpoint = {}
                elif task.pause_requested.is_set():
                    task.status = "paused"
                    task.stop_reason = task.stop_reason or "用户已请求暂停"
                else:
                    task.status = "failed"
                    task.error = _safe_error(exc)
                task.updated_at = _now()
                task.finished_at = _now() if task.status != "paused" else None
                self._persist(task)
        finally:
            with self._lock:
                timer = self._deadline_timers.pop(task.id, None)
            if timer:
                timer.cancel()

    def _persist(self, task: WorkbenchTask) -> None:
        self._ensure_loaded_locked()
        if self._store_path:
            save_task(task, self._store_path)

    def _ensure_loaded_locked(self) -> None:
        if not self._store_path or self._loaded_store_path == self._store_path:
            return
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        mark_orphaned_tasks_paused(self._store_path)
        for row in load_tasks(self._store_path):
            self._tasks[str(row["id"])] = WorkbenchTask.from_row(row)
        self._loaded_store_path = self._store_path

    def _wake_confirmation(self, task: WorkbenchTask) -> None:
        event = task.context.get("confirmation_event")
        if isinstance(event, Event):
            event.set()

    def _stop_at_deadline(self, task_id: str) -> None:
        try:
            self.stop(task_id, "已到发送时间窗口截止时间，后台自动停止")
        except KeyError:
            return

    def _active_task_locked(self) -> WorkbenchTask | None:
        for task in self._tasks.values():
            if task.status in ACTIVE_STATUSES:
                return task
        return None


def _resume_config(snapshot: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Use original non-secret settings and only current in-memory credentials."""
    result = deepcopy(snapshot or {})
    current_ai = current.get("ai") if isinstance(current, dict) else None
    if isinstance(current_ai, dict):
        result_ai = result.setdefault("ai", {})
        for key in ("api_key", "auth_token", "provider", "service", "base_url", "model"):
            if key in current_ai:
                result_ai[key] = current_ai[key]
    return result


def _checkpoint_summary(checkpoint: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(checkpoint, dict):
        return {}
    summary = {}
    for key in ("remaining_job_ids", "remaining_ids", "combo_index", "page", "stage"):
        if key in checkpoint:
            value = checkpoint[key]
            summary[key] = len(value) if key.endswith("ids") and isinstance(value, list) else value
    return summary


def _deadline_from_config(mode: str, config: dict) -> datetime | None:
    if mode not in DEADLINE_MODES:
        return None
    throttle = config.get("throttle", {}) if isinstance(config, dict) else {}
    windows = throttle.get("send_windows", [])
    if not isinstance(windows, list):
        return None
    return SendWindowChecker(windows).latest_end_datetime()


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("api_key", "credential").replace("auth_token", "credential")[:1000]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
