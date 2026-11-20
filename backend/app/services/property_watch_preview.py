"""The queued property-watch preview: its per-watch lock and its short-lived record (SCALE-2h3).

A preview persists nothing in PostgreSQL besides its audit row and search-log rows (design
§G.11). Its state and its result therefore live in Redis only, under one key per preview
(``pw:preview:<id>``) with an expiry (`pw_preview_result_ttl_seconds`). The key holds the
watch id and the requester beside the result, so the read refuses an id that belongs to
another watch or another user; the id alone is never trusted.

One preview of a watch runs at a time: ``pw:preview:lock:<watch id>`` is ``SET NX`` with a
time-to-live of twice the provider's poll budget plus a minute, so a worker that dies cannot
hold the watch for longer than a preview can last. The lock is released by its owner only.

Celery's own result backend is not used for this: its expiry is global (days), an unknown
task id there reads as PENDING (indistinguishable from queued), and it cannot hold the
requester before the task starts. The task is registered with ``ignore_result=True``.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Literal, Protocol, cast

from redis import Redis

from app.core.config import Settings

PreviewState = Literal["QUEUED", "RUNNING", "DONE", "FAILED"]

KEY_PREFIX = "pw:preview:"
LOCK_PREFIX = "pw:preview:lock:"

#: Delete the lock only while it still names this preview, so a task that outlives the lock's
#: expiry cannot release the lock of the preview that replaced it.
_RELEASE_SCRIPT = (
    "local v = redis.call('GET', KEYS[1]) "
    "if v and string.sub(v, 1, string.len(ARGV[1])) == ARGV[1] "
    "then return redis.call('DEL', KEYS[1]) end return 0"
)


class PreviewRedis(Protocol):
    """The slice of the Redis client this module uses; a test stands in for it."""

    def set(self, name: str, value: str, *, nx: bool = ..., ex: int | None = ...) -> Any: ...

    def get(self, name: str) -> str | None: ...

    def delete(self, *names: str) -> Any: ...

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Any: ...


def lock_ttl_seconds(settings: Settings) -> int:
    """Two searches, each polled for at most the provider's poll budget, plus a minute."""
    return 2 * settings.ariel_provider_poll_seconds + 60


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class PreviewStore:
    """The lock and the record of queued previews. Blocking calls: the route threads them."""

    def __init__(self, client: PreviewRedis, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    @property
    def result_ttl(self) -> int:
        return self._settings.pw_preview_result_ttl_seconds

    @property
    def lock_ttl(self) -> int:
        return lock_ttl_seconds(self._settings)

    @property
    def open_ttl(self) -> int:
        """A queued or running record lives at least as long as the lock that guards it."""
        return max(self.lock_ttl, self.result_ttl)

    # ------------------------------------------------------------------------- lock
    def acquire(self, watch_id: uuid.UUID, preview_id: str, now: datetime) -> str | None:
        """Take the watch's lock; `None` when taken, else the UTC time the holder took it."""
        key = f"{LOCK_PREFIX}{watch_id}"
        value = f"{preview_id} {_iso(now)}"
        for _ in range(2):
            if self._client.set(key, value, nx=True, ex=self.lock_ttl):
                return None
            held = self._client.get(key)
            if held:
                _, _, since = held.partition(" ")
                return since or _iso(now)
        # The holder released between our two reads: report the moment of this refusal.
        return _iso(now)

    def release(self, watch_id: uuid.UUID, preview_id: str) -> None:
        self._client.eval(_RELEASE_SCRIPT, 1, f"{LOCK_PREFIX}{watch_id}", preview_id)

    # ----------------------------------------------------------------------- record
    def create(
        self, preview_id: str, watch_id: uuid.UUID, requested_by: str, now: datetime
    ) -> None:
        record = {
            "preview_id": preview_id,
            "watch_id": str(watch_id),
            "requested_by": requested_by,
            "requested_at": _iso(now),
            "status": "QUEUED",
            "result": None,
            "reason_code": None,
            "reason_params": None,
        }
        self._client.set(KEY_PREFIX + preview_id, json.dumps(record), ex=self.open_ttl)

    def get(self, preview_id: str) -> dict[str, Any] | None:
        raw = self._client.get(KEY_PREFIX + preview_id)
        if raw is None:
            return None
        try:
            record = json.loads(raw)
        except ValueError:
            return None
        return cast(dict[str, Any], record) if isinstance(record, dict) else None

    def delete(self, preview_id: str) -> None:
        self._client.delete(KEY_PREFIX + preview_id)

    def update(self, record: dict[str, Any], status: PreviewState, **fields: Any) -> None:
        """Write the new state; a finished preview keeps its result for `result_ttl`."""
        record = {**record, "status": status, **fields}
        ttl = self.open_ttl if status in ("QUEUED", "RUNNING") else self.result_ttl
        self._client.set(KEY_PREFIX + str(record["preview_id"]), json.dumps(record), ex=ttl)

    def is_lost(self, record: dict[str, Any], now: datetime) -> bool:
        """A queued or running record older than the lock can last has no worker behind it."""
        if record.get("status") not in ("QUEUED", "RUNNING"):
            return False
        try:
            requested = datetime.fromisoformat(str(record["requested_at"]))
        except (KeyError, ValueError):
            return True
        return now - requested > timedelta(seconds=self.lock_ttl)


@lru_cache(maxsize=4)
def _client(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)


def get_preview_store(settings: Settings) -> PreviewStore:
    """The store on the application's Redis. A test overrides this."""
    return PreviewStore(cast(PreviewRedis, _client(settings.redis_url)), settings)
