"""Shared collection contracts and multi-platform orchestration."""

from bosshunter.collection.models import (
    CollectionProgress,
    JobCandidate,
    PlatformCollectionRequest,
    PlatformCollectionResult,
)

__all__ = [
    "CollectionProgress",
    "JobCandidate",
    "PlatformCollectionRequest",
    "PlatformCollectionResult",
]
