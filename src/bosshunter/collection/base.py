"""Collector protocol and explicit platform failure categories."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Callable, Protocol

from bosshunter.collection.models import (
    JobCandidate,
    PlatformCollectionRequest,
    PlatformCollectionResult,
)


class CollectionError(RuntimeError):
    """An expected, user-actionable collection failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class CollectionBlockedError(CollectionError):
    """The platform requires user action or has blocked the session."""


@dataclass
class CollectorHooks:
    """Callbacks supplied by the shared collection layer."""

    stop_event: Event | None
    on_list_candidate: Callable[[JobCandidate], bool]
    on_candidate: Callable[[JobCandidate], bool]
    on_parse_failed: Callable[[str], None]
    on_event: Callable[..., None]


class Collector(Protocol):
    platform: str

    def collect(
        self,
        request: PlatformCollectionRequest,
        hooks: CollectorHooks,
    ) -> PlatformCollectionResult:
        ...
