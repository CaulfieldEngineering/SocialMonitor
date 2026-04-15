"""Source plugin registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from social_monitor.sources.base import BaseSource

SOURCE_REGISTRY: dict[str, type[BaseSource]] = {}


def register_source(name: str):
    """Decorator to register a source plugin."""

    def decorator(cls: type[BaseSource]) -> type[BaseSource]:
        SOURCE_REGISTRY[name] = cls
        return cls

    return decorator
