"""QRadar provider abstraction.

`build_provider` and `get_provider` are loaded on first access, not at import.
`app.providers.factory` pulls in every provider and, through them, the pure
fold and parser modules. Those modules import `app.providers.base`, so an eager
import here made any of them fail when it was the first module a fresh process
imported (a partially initialised module in the cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.providers.base import (
    CapabilityNotSupportedError,
    ProviderAuthError,
    ProviderCapability,
    ProviderError,
    ProviderUnavailableError,
    QRadarProvider,
)

if TYPE_CHECKING:
    from app.providers.factory import build_provider, get_provider

__all__ = [
    "CapabilityNotSupportedError",
    "ProviderAuthError",
    "ProviderCapability",
    "ProviderError",
    "ProviderUnavailableError",
    "QRadarProvider",
    "build_provider",
    "get_provider",
]

_LAZY = frozenset({"build_provider", "get_provider"})


def __getattr__(name: str) -> object:
    if name in _LAZY:
        from app.providers import factory

        return getattr(factory, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
