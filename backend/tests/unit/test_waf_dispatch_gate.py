"""WAF dispatch is gated by the WAF_COLLECTION cadence and sends nothing off-slot."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import get_settings
from app.models.enums import SearchFamily
from app.workers import tasks
from app.workers.celery_app import celery_app


class _Session:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _Context:
    def __init__(self, session: _Session) -> None:
        self.session = session

    async def __aenter__(self) -> _Session:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        return None


class _Maker:
    def __init__(self, events: list[str] | None = None) -> None:
        self.sessions: list[_Session] = []
        self.events = events if events is not None else []

    def __call__(self) -> _Context:
        session = _Session()
        self.sessions.append(session)
        self.events.append("session")
        return _Context(session)


def _enable(monkeypatch, *, enabled: bool = True) -> None:  # type: ignore[no-untyped-def]
    settings = get_settings().model_copy(update={"waf_collection_enabled": enabled})
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)


def _cadence(events: list[str], recorded: list[dict], *, claim: object):  # type: ignore[no-untyped-def]
    class Cadence:
        def __init__(self, *args: object) -> None:
            pass

        async def claim_slot(self, family: SearchFamily):  # type: ignore[no-untyped-def]
            assert family == SearchFamily.WAF_COLLECTION
            events.append("claim")
            return claim

        async def record_run(self, family: SearchFamily, **values: object) -> None:
            assert family == SearchFamily.WAF_COLLECTION
            events.append("record")
            recorded.append(values)

    return Cadence


def test_dispatch_ticks_every_minute() -> None:
    assert celery_app.conf.beat_schedule["dispatch-waf-collection"]["schedule"] == 60.0


@pytest.mark.asyncio
async def test_a_tick_that_is_not_due_enqueues_nothing_and_lists_no_sources(monkeypatch) -> None:
    _enable(monkeypatch)
    events: list[str] = []
    recorded: list[dict] = []
    maker = _Maker(events)
    sent: list[str] = []

    async def must_not_run(*_a: object, **_k: object):  # type: ignore[no-untyped-def]
        raise AssertionError("an off-slot tick must not read the sources")

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "eligible_profile_ids", must_not_run)
    monkeypatch.setattr(
        "app.services.search_cadence.SearchCadenceService", _cadence(events, recorded, claim=None)
    )

    assert await tasks.dispatch_waf_collection(enqueue=sent.append) == {
        "status": "not_due",
        "family": "WAF_COLLECTION",
    }
    assert sent == [] and recorded == []
    # The claim is committed in its own session, then nothing else happens.
    assert events == ["session", "claim"]
    assert [s.commits for s in maker.sessions] == [1]


@pytest.mark.asyncio
async def test_a_due_tick_enqueues_each_source_then_records_dispatched(monkeypatch) -> None:
    _enable(monkeypatch)
    events: list[str] = []
    recorded: list[dict] = []
    maker = _Maker(events)
    ids = [uuid.uuid4(), uuid.uuid4()]

    async def eligible(_session: object, _settings: object):  # type: ignore[no-untyped-def]
        events.append("eligible")
        return ids, {"profile_disabled": 1}

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "eligible_profile_ids", eligible)
    monkeypatch.setattr(
        "app.services.search_cadence.SearchCadenceService",
        _cadence(events, recorded, claim=object()),
    )

    def send(profile_id: str) -> None:
        events.append(f"send:{profile_id}")

    result = await tasks.dispatch_waf_collection(enqueue=send)

    assert result == {"status": "ok", "dispatched": 2, "skipped": {"profile_disabled": 1}}
    assert recorded == [
        {"outcome": "dispatched", "searches": None, "buckets": None, "splits": None}
    ]
    # Claim first, then the sources, then the sends, and the record last.
    assert [e for e in events if e != "session"] == [
        "claim", "eligible", f"send:{ids[0]}", f"send:{ids[1]}", "record",
    ]
    assert [s.commits for s in maker.sessions if s.commits] == [1, 1]


@pytest.mark.asyncio
async def test_a_due_tick_with_no_eligible_source_records_no_sources(monkeypatch) -> None:
    _enable(monkeypatch)
    events: list[str] = []
    recorded: list[dict] = []
    sent: list[str] = []

    async def eligible(_session: object, _settings: object):  # type: ignore[no-untyped-def]
        return [], {"no_profile": 3}

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: _Maker(events))
    monkeypatch.setattr(tasks, "eligible_profile_ids", eligible)
    monkeypatch.setattr(
        "app.services.search_cadence.SearchCadenceService",
        _cadence(events, recorded, claim=object()),
    )

    result = await tasks.dispatch_waf_collection(enqueue=sent.append)

    assert result == {"status": "ok", "dispatched": 0, "skipped": {"no_profile": 3}}
    assert sent == []
    assert recorded == [
        {"outcome": "no-sources", "searches": None, "buckets": None, "splits": None}
    ]


@pytest.mark.asyncio
async def test_disabled_returns_before_the_gate_is_touched(monkeypatch) -> None:
    _enable(monkeypatch, enabled=False)

    def no_session() -> object:
        raise AssertionError("a disabled WAF must not open a session")

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: no_session)
    sent: list[str] = []

    assert await tasks.dispatch_waf_collection(enqueue=sent.append) == {
        "status": "disabled",
        "dispatched": 0,
    }
    assert sent == []


@pytest.mark.asyncio
async def test_the_per_source_task_is_not_gated(monkeypatch) -> None:
    """`collect_waf_source` runs whenever it is enqueued; it never claims a slot."""
    seen: list[str] = []

    class Cadence:
        def __init__(self, *args: object) -> None:
            raise AssertionError("collect_waf_source must not consult the cadence")

    async def run(session: object, profile_id: object, **_kw: object) -> dict:
        seen.append(str(profile_id))
        return {"status": "ok"}

    monkeypatch.setattr("app.services.search_cadence.SearchCadenceService", Cadence)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: _Maker())
    monkeypatch.setattr(tasks, "run_waf_source", run)
    monkeypatch.setattr(tasks, "NotificationDispatcher", lambda *a, **k: object())

    pid = uuid.uuid4()
    assert await tasks.collect_waf_source(str(pid)) == {"status": "ok"}
    assert seen == [str(pid)]
