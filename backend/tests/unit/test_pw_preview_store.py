"""The preview's lock and record (SCALE-2h3): who holds a watch, what a record keeps.

Against the dict-backed Redis stand-in of `pw_preview_fakes`; the real server's expiry timing
and `NX` are not exercised here.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.services.property_watch_preview import (
    KEY_PREFIX,
    LOCK_PREFIX,
    PreviewStore,
    lock_ttl_seconds,
)
from tests.unit.pw_preview_fakes import FakeRedis, make_store

NOW = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)


def _store(**overrides: object) -> tuple[PreviewStore, FakeRedis]:
    redis = FakeRedis()
    settings = get_settings().model_copy(update=overrides)
    return make_store(settings, redis), redis


def test_the_second_taker_is_refused_and_told_since_when() -> None:
    store, _ = _store()
    watch = uuid.uuid4()
    assert store.acquire(watch, "first", NOW) is None
    since = store.acquire(watch, "second", NOW + timedelta(seconds=30))
    assert since == NOW.isoformat()
    assert store.acquire(uuid.uuid4(), "elsewhere", NOW) is None, "the lock is per watch"


def test_only_the_owner_releases_the_lock() -> None:
    store, redis = _store()
    watch = uuid.uuid4()
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    assert store.acquire(watch, first, NOW) is None
    store.release(watch, second)
    assert f"{LOCK_PREFIX}{watch}" in redis.values, "a stranger's release leaves the lock"
    store.release(watch, first)
    assert f"{LOCK_PREFIX}{watch}" not in redis.values
    assert store.acquire(watch, second, NOW) is None


def test_a_lock_that_expired_cannot_be_released_over_its_successor() -> None:
    store, redis = _store()
    watch = uuid.uuid4()
    old, new = str(uuid.uuid4()), str(uuid.uuid4())
    store.acquire(watch, old, NOW)
    redis.expire(f"{LOCK_PREFIX}{watch}")
    assert store.acquire(watch, new, NOW) is None
    store.release(watch, old)
    assert f"{LOCK_PREFIX}{watch}" in redis.values


def test_the_lock_outlives_two_searches_and_the_record_outlives_the_lock() -> None:
    store, redis = _store(ariel_provider_poll_seconds=120, pw_preview_result_ttl_seconds=60)
    watch, preview = uuid.uuid4(), str(uuid.uuid4())
    store.acquire(watch, preview, NOW)
    assert redis.ttls[f"{LOCK_PREFIX}{watch}"] == lock_ttl_seconds(store._settings) == 300
    store.create(preview, watch, "analyst", NOW)
    assert redis.ttls[KEY_PREFIX + preview] == 300, "queued: at least as long as the lock"


def test_a_finished_record_keeps_its_result_for_the_configured_ttl_only() -> None:
    store, redis = _store(pw_preview_result_ttl_seconds=123)
    watch, preview = uuid.uuid4(), str(uuid.uuid4())
    store.create(preview, watch, "analyst", NOW)
    record = store.get(preview)
    assert record is not None
    assert record["watch_id"] == str(watch) and record["requested_by"] == "analyst"
    store.update(record, "DONE", result={"mode": "PREVIEW"})
    assert redis.ttls[KEY_PREFIX + preview] == 123
    done = store.get(preview)
    assert done is not None and done["status"] == "DONE" and done["result"] == {"mode": "PREVIEW"}
    store.update(done, "FAILED", result=None, reason_code="pw.preview.worker_failed")
    assert redis.ttls[KEY_PREFIX + preview] == 123


def test_a_missing_or_corrupt_record_reads_as_nothing() -> None:
    store, redis = _store()
    assert store.get("nope") is None
    redis.set(KEY_PREFIX + "bad", "{not json")
    redis.set(KEY_PREFIX + "list", json.dumps([1, 2]))
    assert store.get("bad") is None and store.get("list") is None


def test_an_open_record_older_than_the_lock_has_no_worker_behind_it() -> None:
    store, _ = _store(ariel_provider_poll_seconds=120)
    preview, watch = str(uuid.uuid4()), uuid.uuid4()
    store.create(preview, watch, "analyst", NOW)
    record = store.get(preview)
    assert record is not None
    assert not store.is_lost(record, NOW + timedelta(seconds=300))
    assert store.is_lost(record, NOW + timedelta(seconds=301))
    store.update(record, "DONE", result={})
    done = store.get(preview)
    assert done is not None and not store.is_lost(done, NOW + timedelta(days=1))
    assert store.is_lost({"status": "RUNNING"}, NOW), "a record with no time is not trusted"


def test_the_result_ttl_has_a_floor_of_a_minute() -> None:
    assert Settings.model_fields["pw_preview_result_ttl_seconds"].default == 900
    with pytest.raises(ValidationError):
        Settings(pw_preview_result_ttl_seconds=59)  # type: ignore[call-arg]
