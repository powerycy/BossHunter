"""Platform collector registry."""

from __future__ import annotations

from collections.abc import Callable

from bosshunter.collection.base import Collector


class CollectorRegistry:
    def __init__(self, factories: dict[str, Callable[[], Collector]] | None = None):
        self._factories = dict(factories or {})

    def register(self, platform: str, factory: Callable[[], Collector]) -> None:
        self._factories[str(platform)] = factory

    def get(self, platform: str) -> Collector:
        try:
            return self._factories[platform]()
        except KeyError as exc:
            raise ValueError(f"未注册的采集平台：{platform}") from exc

    def platforms(self) -> tuple[str, ...]:
        return tuple(self._factories)
