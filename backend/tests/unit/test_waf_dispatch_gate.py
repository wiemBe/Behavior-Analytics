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


def _cadence_with_claims(
    events: list[str],
    recorded: list[dict],
    *,
    claims: list[object],
    fail_records: int = 0,
):  # type: ignore[no-untyped-def]
    """A cadence that yields queued slots in order and can fail its first writes."""

    class Cadence:
        def __init__(self, *args: object) -> None:
            pass

        async def claim_slot(self, family: SearchFamily):  # type: ignore[no-untyped-def]
            assert family == SearchFamily.WAF_COLLECTION
            events.append("claim")
            return claims.pop(0) if claims else None

        async def record_run(self, family: SearchFamily, **values: object) -> None:
            assert family == SearchFamily.WAF_COLLECTION
            events.append("record")
            recorded.append(values)
            nonlocal fail_records
            if fail_records:
                fail_records -= 1
                raise RuntimeError("cadence write rejected")

    return Cadence


@pytest.mark.asyncio
async def test_a_failed_first_send_records_failed_with_unknown_counts(monkeypatch, caplog) -> None:
    _enable(monkeypatch)
    events: list[str] = []
    recorded: list[dict] = []
    maker = _Maker(events)
    ids = [uuid.uuid4(), uuid.uuid4()]

    async def eligible(_session: object, _settings: object):  # type: ignore[no-untyped-def]
        events.append("eligible")
        return ids, {}

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "eligible_profile_ids", eligible)
    monkeypatch.setattr(
        "app.services.search_cadence.SearchCadenceService",
        _cadence_with_claims(events, recorded, claims=[object()]),
    )

    def send(profile_id: str) -> None:
        events.append("send-fail")
        raise RuntimeError("broker password=s3cr3t")

    with pytest.raises(RuntimeError, match="broker"):
        await tasks.dispatch_waf_collection(enqueue=send)

    assert recorded == [
        {"outcome": "failed", "searches": None, "buckets": None, "splits": None}
    ]
    assert [e for e in events if e != "session"] == ["claim", "eligible", "send-fail", "record"]
    # Neither the broker credential nor its error text is persisted or logged.
    assert "s3cr3t" not in caplog.text and "password" not in caplog.text
    assert "s3cr3t" not in str(recorded)


@pytest.mark.asyncio
async def test_a_failed_later_send_keeps_counts_unknown(monkeypatch, caplog) -> None:
    _enable(monkeypatch)
    events: list[str] = []
    recorded: list[dict] = []
    ids = [uuid.uuid4(), uuid.uuid4()]

    async def eligible(_session: object, _settings: object):  # type: ignore[no-untyped-def]
        events.append("eligible")
        return ids, {}

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: _Maker(events))
    monkeypatch.setattr(tasks, "eligible_profile_ids", eligible)
    monkeypatch.setattr(
        "app.services.search_cadence.SearchCadenceService",
        _cadence_with_claims(events, recorded, claims=[object()]),
    )

    calls = 0

    def send(profile_id: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("broker refused task")

    with pytest.raises(RuntimeError, match="broker"):
        await tasks.dispatch_waf_collection(enqueue=send)

    assert recorded == [
        {"outcome": "failed", "searches": None, "buckets": None, "splits": None}
    ]
    assert [e for e in events if e != "session"] == [
        "claim", "eligible", "record",
    ]
    assert "broker refused task" not in caplog.text


@pytest.mark.asyncio
async def test_an_eligibility_failure_records_failed_without_sending(monkeypatch) -> None:
    _enable(monkeypatch)
    events: list[str] = []
    recorded: list[dict] = []
    sent: list[str] = []

    async def eligible(_session: object, _settings: object):  # type: ignore[no-untyped-def]
        events.append("eligible")
        raise ValueError("profile query failed")

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: _Maker(events))
    monkeypatch.setattr(tasks, "eligible_profile_ids", eligible)
    monkeypatch.setattr(
        "app.services.search_cadence.SearchCadenceService",
        _cadence_with_claims(events, recorded, claims=[object()]),
    )

    with pytest.raises(ValueError, match="profile query failed"):
        await tasks.dispatch_waf_collection(enqueue=sent.append)

    assert sent == []
    assert recorded == [
        {"outcome": "failed", "searches": None, "buckets": None, "splits": None}
    ]
    assert [e for e in events if e != "session"] == ["claim", "eligible", "record"]


@pytest.mark.asyncio
async def test_a_failed_cadence_write_is_retried_as_failed_then_reraised(monkeypatch) -> None:
    """A failed success-write cannot leave the old outcome on the row."""
    _enable(monkeypatch)
    events: list[str] = []
    recorded: list[dict] = []
    sent: list[str] = []

    async def eligible(_session: object, _settings: object):  # type: ignore[no-untyped-def]
        return [uuid.uuid4()], {}

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: _Maker(events))
    monkeypatch.setattr(tasks, "eligible_profile_ids", eligible)
    monkeypatch.setattr(
        "app.services.search_cadence.SearchCadenceService",
        _cadence_with_claims(events, recorded, claims=[object()], fail_records=1),
    )

    with pytest.raises(RuntimeError, match="cadence write rejected"):
        await tasks.dispatch_waf_collection(enqueue=sent.append)

    assert len(sent) == 1
    assert recorded == [
        {"outcome": "dispatched", "searches": None, "buckets": None, "splits": None},
        {"outcome": "failed", "searches": None, "buckets": None, "splits": None},
    ]


@pytest.mark.asyncio
async def test_the_next_slot_resumes_after_a_failed_dispatch(monkeypatch) -> None:
    _enable(monkeypatch)
    events: list[str] = []
    recorded: list[dict] = []
    sent: list[str] = []

    async def eligible(_session: object, _settings: object):  # type: ignore[no-untyped-def]
        return [uuid.uuid4()], {}

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: _Maker(events))
    monkeypatch.setattr(tasks, "eligible_profile_ids", eligible)
    monkeypatch.setattr(
        "app.services.search_cadence.SearchCadenceService",
        _cadence_with_claims(events, recorded, claims=[object(), object()]),
    )

    def fail(_profile_id: str) -> None:
        raise RuntimeError("broker down")

    with pytest.raises(RuntimeError, match="broker down"):
        await tasks.dispatch_waf_collection(enqueue=fail)
    assert await tasks.dispatch_waf_collection(enqueue=sent.append) == {
        "status": "ok", "dispatched": 1, "skipped": {},
    }

    assert recorded[-1] == {
        "outcome": "dispatched", "searches": None, "buckets": None, "splits": None,
    }
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_a_failed_dispatch_does_not_resend_in_the_same_slot(monkeypatch) -> None:
    _enable(monkeypatch)
    events: list[str] = []
    recorded: list[dict] = []
    sent: list[str] = []

    async def eligible(_session: object, _settings: object):  # type: ignore[no-untyped-def]
        return [uuid.uuid4()], {}

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: _Maker(events))
    monkeypatch.setattr(tasks, "eligible_profile_ids", eligible)
    # Only the first tick owns the slot; the next tick sees the same claimed slot.
    monkeypatch.setattr(
        "app.services.search_cadence.SearchCadenceService",
        _cadence_with_claims(events, recorded, claims=[object(), None]),
    )

    def fail(_profile_id: str) -> None:
        raise RuntimeError("broker down")

    with pytest.raises(RuntimeError, match="broker down"):
        await tasks.dispatch_waf_collection(enqueue=fail)
    assert await tasks.dispatch_waf_collection(enqueue=sent.append) == {
        "status": "not_due", "family": "WAF_COLLECTION",
    }
    assert sent == []
    assert len(recorded) == 1 and recorded[0]["outcome"] == "failed"


@pytest.mark.asyncio
async def test_the_per_source_task_runs_while_the_switch_is_on_and_never_claims_a_slot(
    monkeypatch,
) -> None:
    """With the effective WAF switch on, `collect_waf_source` runs; it never claims a slot."""
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


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [None, "feature_switch_unreadable"])
async def test_the_per_source_task_is_gated_by_the_effective_switch(monkeypatch, reason) -> None:
    """A switch turned off (or an unreadable table) after the fan-out stops the next source."""
    off = get_settings().model_copy(update={"waf_collection_enabled": False})

    async def effective(_task: str) -> tuple[object, str | None]:
        return off, reason

    async def must_not_run(*_a: object, **_k: object) -> dict:
        raise AssertionError("a disabled family must not run the source")

    def no_provider(*_a: object, **_k: object) -> object:
        raise AssertionError("a disabled family must not build a provider")

    monkeypatch.setattr(tasks, "_effective_settings", effective)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: _Maker())
    monkeypatch.setattr(tasks, "run_waf_source", must_not_run)
    monkeypatch.setattr(tasks, "build_provider_for_instance", no_provider)

    pid = str(uuid.uuid4())
    expected = {"status": "disabled", "profile_id": pid}
    if reason:
        expected["reason"] = reason
    assert await tasks.collect_waf_source(pid) == expected
