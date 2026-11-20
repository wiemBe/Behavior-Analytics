"""A dict-backed stand-in for the Redis calls of the queued preview (SCALE-2h3).

The suite has no Redis (the compose stack is not part of the test run), so the lock and the
record of `app.services.property_watch_preview` are tested against this. It models what the
code relies on: `SET NX EX`, `GET`, `DEL`, the compare-and-delete script, and expiry (a test
calls `expire` to drop a key as the server would when its time-to-live runs out). Real TTL
timing and a real server's `NX` are not exercised.
"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.services.property_watch_preview import (
    _RELEASE_SCRIPT,
    PreviewRedis,
    PreviewStore,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("redis down")

    def set(self, name: str, value: str, *, nx: bool = False, ex: int | None = None) -> Any:
        self._check()
        if nx and name in self.values:
            return None
        self.values[name] = value
        self.ttls[name] = ex
        return True

    def get(self, name: str) -> str | None:
        self._check()
        return self.values.get(name)

    def delete(self, *names: str) -> int:
        self._check()
        return sum(1 for name in names if self.values.pop(name, None) is not None)

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        self._check()
        assert script == _RELEASE_SCRIPT and numkeys == 1
        key, owner = keys_and_args
        held = self.values.get(key)
        if held is not None and held.startswith(owner):
            del self.values[key]
            return 1
        return 0

    def expire(self, name: str) -> None:
        """The server dropping a key whose time-to-live ran out."""
        self.values.pop(name, None)


def make_store(settings: Settings, redis: FakeRedis | None = None) -> PreviewStore:
    client: PreviewRedis = redis or FakeRedis()  # type: ignore[assignment]
    return PreviewStore(client, settings)
