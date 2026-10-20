"""The history bootstrap's API and service (PW-13d): requests, reads, the budget, evidence.

The API writes `history_days` and nothing else of the bootstrap (the collector owns the other
`history_*` columns), so every read state here is seeded directly with PW-13b's own column
builders, never by hand.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.history.planner import Gap
from app.models.enums import PwBucketOrigin, PwHistoryState
from app.models.property_watch import PropertyWatch, PwBucket, PwValue, PwValueMetric
from app.property_watch.bootstrap import (
    begin_reading,
    handoff_columns,
    history_columns,
    pw_planner_settings,
    start_progress,
)
from tests.integration.pw_engine_support import NEW_VALUE, STEP, T0, history_buckets, make_rig
from tests.integration.pw_engine_support import plan_range as plan
from tests.integration.test_property_watches_api import (
    ACTION,
    NIL,
    NOBODY,
    PREFIX,
    _anonymous,
    _audits,
    _body,
    _bucket,
    _bucket_start,
    _client,
    _create,
    _principal,
    _seed,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the history API integration tests",
    ),
]

BOUNDARY = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
PLANNER = pw_planner_settings(
    get_settings().model_copy(update={"collection_interval_seconds": 300}), cadence_seconds=300
)
B = PLANNER.bucket
ENGINEER = "detection_engineer"
HISTORY_COLUMNS = (
    "history_days", "history_state", "history_target_start", "history_floor", "history_through",
    "history_end", "history_started_at", "history_finished_at", "history_progress",
)


# ------------------------------------------------------------------------------ helpers
def _progress(buckets: int = 576, *, gaps: bool = True):  # type: ignore[no-untyped-def]
    """A reading that has read `buckets` buckets from the floor (576 = two days)."""
    base = start_progress(history_days=28, boundary=BOUNDARY, now=BOUNDARY, settings=PLANNER)
    floor = base.target_start
    return replace(
        base,
        floor=floor,
        first_event_bucket=floor,
        through=floor + (buckets - 1) * B,
        searches=7,
        buckets_read=buckets,
        gaps=(
            (
                Gap(floor + 10 * B, floor + 14 * B, "could not be read after 3 attempts"),
                Gap(floor + 40 * B, floor + 41 * B, "could not be read after 3 attempts"),
            )
            if gaps
            else ()
        ),
    )


def _reading_columns(buckets: int = 576) -> dict[str, object]:
    return history_columns(
        _progress(buckets), state=PwHistoryState.READING, reason="reading 576 buckets"
    )


def _finished_columns(state: PwHistoryState, reason: str) -> dict[str, object]:
    return handoff_columns(
        _progress(), state=state, reason=reason, boundary=BOUNDARY, now=BOUNDARY, settings=PLANNER
    )


async def _set(session, watch_id: str, **columns: object) -> None:  # type: ignore[no-untyped-def]
    await session.execute(
        update(PropertyWatch)
        .where(PropertyWatch.id == uuid.UUID(watch_id))
        # keep the optimistic token: a seed is not an edit
        .values(updated_at=PropertyWatch.updated_at, **columns)
    )
    await session.commit()


async def _row(session, watch_id: str) -> PropertyWatch:  # type: ignore[no-untyped-def]
    session.expire_all()
    return await session.get(PropertyWatch, uuid.UUID(watch_id))


async def _history_audits(session) -> list:  # type: ignore[no-untyped-def]
    return await _audits(session, "property_watch.history_request")


def _message(response) -> str:  # type: ignore[no-untyped-def]
    detail = response.json()["detail"]
    return detail["message"] if isinstance(detail, dict) else str(detail)


def _engineer(db_schema: str, **overrides: object):  # type: ignore[no-untyped-def]
    return _client(db_schema, _principal(ENGINEER), **overrides)


# ----------------------------------------------------------------------------- guards
async def test_the_history_route_needs_a_session_and_the_write_permission(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    made = _create(_engineer(db_schema), "guarded", history_days=0)
    url = f"{PREFIX}/{made['id']}/history"
    assert _anonymous(db_schema).post(url, json={"history_days": 7}).status_code == 401
    for role in ("viewer", "analyst"):
        denied = _client(db_schema, _principal(role)).post(url, json={"history_days": 7})
        assert denied.status_code == 403, role
        assert denied.json()["detail"] == "missing required permission: property_watch:write"
    # Neither the request nor a body that names `history_days` slipped past the guard.
    viewer = _client(db_schema, _principal("viewer"))
    assert viewer.post(PREFIX, json=_body("nope", history_days=7)).status_code == 403
    assert viewer.patch(
        f"{PREFIX}/{made['id']}",
        json={"expected_updated_at": made["updated_at"], "history_days": 7},
    ).status_code == 403
    assert (await _row(db_session, made["id"])).history_days == 0
    for role, days in ((ENGINEER, 7), ("admin", 14)):
        allowed = _client(db_schema, _principal(role)).post(url, json={"history_days": days})
        assert allowed.status_code == 200, (role, allowed.text)
        assert allowed.json()["history"]["days_requested"] == days
    nobody = _client(db_schema, NOBODY).post(url, json={"history_days": 21})
    assert nobody.status_code == 403
    assert nobody.json()["detail"] == "missing required permission: read:property_watches"


async def test_an_unknown_watch_is_404(db_session, db_schema: str) -> None:
    await _seed(db_session)
    response = _engineer(db_schema).post(f"{PREFIX}/{NIL}/history", json={"history_days": 7})
    assert response.status_code == 404


async def test_the_history_body_takes_no_aql_and_no_window(db_session, db_schema: str) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "closed", history_days=0)
    url = f"{PREFIX}/{made['id']}/history"
    for extra in ({"aql": "SELECT 1"}, {"window_start": "2026-01-01T00:00:00Z"},
                  {"query": "x"}, {"history_end": "2026-01-01T00:00:00Z"}):
        refused = client.post(url, json={"history_days": 7, **extra})
        assert refused.status_code == 422, extra
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={"history_days": "seven"}).status_code == 422
    assert (await _row(db_session, made["id"])).history_days == 0
    assert await _history_audits(db_session) == []


# ------------------------------------------------------------------------- validation
@pytest.mark.parametrize("days", [0, 7, 28])
async def test_create_accepts_zero_the_minimum_and_the_maximum(
    db_session, db_schema: str, days: int
) -> None:
    await _seed(db_session)
    made = _create(_engineer(db_schema), f"days-{days}", history_days=days)
    assert made["history"]["days_requested"] == days
    assert made["history"]["state"] == "NOT_REQUESTED"
    assert (await _row(db_session, made["id"])).history_days == days


@pytest.mark.parametrize("days", [6, 29, -1])
async def test_create_refuses_a_value_outside_zero_or_the_range(
    db_session, db_schema: str, days: int
) -> None:
    await _seed(db_session)
    refused = _engineer(db_schema).post(PREFIX, json=_body("outside", history_days=days))
    assert refused.status_code == 422
    assert "between 7 and 28" in _message(refused) and str(days) in _message(refused)
    assert refused.json()["detail"]["reasons"]


async def test_create_reports_every_reason_at_once(db_session, db_schema: str) -> None:
    await _seed(db_session)
    refused = _engineer(db_schema).post(
        PREFIX, json=_body("both", history_days=99, properties=[])
    )
    assert refused.status_code == 422
    assert len(refused.json()["detail"]["reasons"]) >= 2


async def test_create_refuses_an_explicit_null(db_session, db_schema: str) -> None:
    await _seed(db_session)
    refused = _engineer(db_schema).post(PREFIX, json=_body("nulled", history_days=None))
    assert refused.status_code == 422
    assert "use 0" in _message(refused)


async def test_an_omitted_history_days_is_the_setting_with_the_switch_on_and_null_off(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    on = _create(_engineer(db_schema, pw_bootstrap_days=21), "default-on")
    assert on["history"]["days_requested"] == 21
    off = _create(_engineer(db_schema, pw_bootstrap_enabled=False), "default-off")
    assert off["history"]["days_requested"] is None
    assert (await _row(db_session, off["id"])).history_days is None


async def test_an_explicit_value_with_the_switch_off_is_stored_as_asked(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    made = _create(
        _engineer(db_schema, pw_bootstrap_enabled=False), "asked-off", history_days=14
    )
    assert made["history"]["days_requested"] == 14
    assert (await _row(db_session, made["id"])).history_days == 14


@pytest.mark.parametrize("days", [6, 29])
async def test_patch_and_post_refuse_a_value_outside_the_range(
    db_session, db_schema: str, days: int
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "range", history_days=0)
    refused = client.patch(
        f"{PREFIX}/{made['id']}",
        json={"expected_updated_at": made["updated_at"], "history_days": days},
    )
    assert refused.status_code == 422 and "between 7 and 28" in _message(refused)
    refused = client.post(f"{PREFIX}/{made['id']}/history", json={"history_days": days})
    assert refused.status_code == 422 and "between 7 and 28" in _message(refused)
    assert (await _row(db_session, made["id"])).history_days == 0
    assert await _history_audits(db_session) == []


async def test_patch_refuses_a_null_history_days(db_session, db_schema: str) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "nullpatch")
    refused = client.patch(
        f"{PREFIX}/{made['id']}",
        json={"expected_updated_at": made["updated_at"], "history_days": None},
    )
    assert refused.status_code == 422 and "history_days must not be null" in _message(refused)


async def test_patch_changes_the_days_and_audits_them_in_the_snapshot(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "patched", history_days=0)
    out = client.patch(
        f"{PREFIX}/{made['id']}",
        json={"expected_updated_at": made["updated_at"], "history_days": 14},
    )
    assert out.status_code == 200, out.text
    assert out.json()["history"]["days_requested"] == 14
    [created] = await _audits(db_session, "property_watch.create")
    assert created.detail["after"]["history_days"] == 0
    [updated] = await _audits(db_session, "property_watch.update")
    assert updated.detail["before"]["history_days"] == 0
    assert updated.detail["after"]["history_days"] == 14
    row = await _row(db_session, made["id"])
    assert row.history_state == "NOT_REQUESTED" and row.history_progress == {}


# ------------------------------------------------------------------------------ the 409s
async def test_post_history_sets_the_request_audits_it_and_touches_nothing_else(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "request", history_days=0)
    before = await _row(db_session, made["id"])
    others = {c: getattr(before, c) for c in HISTORY_COLUMNS if c != "history_days"}
    out = client.post(f"{PREFIX}/{made['id']}/history", json={"history_days": 21})
    assert out.status_code == 200, out.text
    assert out.json()["history"]["days_requested"] == 21
    assert out.json()["history"]["estimate"] is not None
    after = await _row(db_session, made["id"])
    assert after.history_days == 21
    assert {c: getattr(after, c) for c in others} == others, "the API writes no bootstrap state"
    assert after.collected_through is None and after.watch_version == before.watch_version
    [audit] = await _history_audits(db_session)
    assert audit.detail == {"history_days": 21, "previous": 0}
    assert audit.object_id == made["id"] and audit.actor == ENGINEER


async def test_the_same_value_again_is_a_200_with_no_audit_row(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "same", history_days=14)
    again = client.post(f"{PREFIX}/{made['id']}/history", json={"history_days": 14})
    assert again.status_code == 200 and again.json()["history"]["days_requested"] == 14
    patched = client.patch(
        f"{PREFIX}/{made['id']}",
        json={"expected_updated_at": made["updated_at"], "history_days": 14},
    )
    assert patched.status_code == 200
    assert await _history_audits(db_session) == []
    assert await _audits(db_session, "property_watch.update") == []
    # The same value on a watch that is already reading is also a no-op, not a 409.
    await _set(db_session, made["id"], **_reading_columns())
    assert client.post(
        f"{PREFIX}/{made['id']}/history", json={"history_days": 14}
    ).status_code == 200
    assert await _history_audits(db_session) == []


@pytest.mark.parametrize(
    "state", [PwHistoryState.READING, PwHistoryState.COMPLETE, PwHistoryState.PARTIAL,
              PwHistoryState.UNAVAILABLE]
)
async def test_a_watch_that_started_reading_is_409_naming_the_state(
    db_session, db_schema: str, state: PwHistoryState
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, f"started-{state.value.lower()}", history_days=14)
    columns = (
        _reading_columns()
        if state is PwHistoryState.READING
        else _finished_columns(state, "finished")
    )
    columns["collected_through"] = None
    columns["evaluated_through"] = None
    await _set(db_session, made["id"], **columns)
    before = await _row(db_session, made["id"])
    snapshot = {c: getattr(before, c) for c in HISTORY_COLUMNS}
    for body in (
        lambda: client.post(f"{PREFIX}/{made['id']}/history", json={"history_days": 21}),
        lambda: client.patch(
            f"{PREFIX}/{made['id']}",
            json={"expected_updated_at": made["updated_at"], "history_days": 21},
        ),
    ):
        refused = body()
        assert refused.status_code == 409, refused.text
        assert state.value in _message(refused)
        assert "create a new watch to read history" in _message(refused)
    after = await _row(db_session, made["id"])
    assert {c: getattr(after, c) for c in HISTORY_COLUMNS} == snapshot
    assert await _history_audits(db_session) == []


@pytest.mark.parametrize("mark", ["collected_through", "evaluated_through"])
async def test_a_watermark_is_409_even_with_no_state_and_no_bucket(
    db_session, db_schema: str, mark: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, f"marked-{mark}", history_days=0)
    await _set(db_session, made["id"], **{mark: _bucket_start(3)})
    refused = client.post(f"{PREFIX}/{made['id']}/history", json={"history_days": 14})
    assert refused.status_code == 409
    assert mark in _message(refused) and "create a new watch" in _message(refused)
    assert (await _row(db_session, made["id"])).history_days == 0


async def test_a_stored_bucket_of_the_current_version_is_409_with_the_count(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "stored", history_days=0)
    db_session.add_all([_bucket(made["id"], 3), _bucket(made["id"], 2)])
    await db_session.commit()
    refused = client.post(f"{PREFIX}/{made['id']}/history", json={"history_days": 14})
    assert refused.status_code == 409
    assert "2 stored bucket(s)" in _message(refused)
    assert "create a new watch to read history" in _message(refused)
    patched = client.patch(
        f"{PREFIX}/{made['id']}",
        json={"expected_updated_at": made["updated_at"], "history_days": 14},
    )
    assert patched.status_code == 409 and "2 stored bucket(s)" in _message(patched)
    assert (await _row(db_session, made["id"])).history_days == 0


async def test_a_bucket_of_an_older_version_only_does_not_block(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "older", history_days=0)
    db_session.add(_bucket(made["id"], 3, watch_version=1))
    await db_session.commit()
    await _set(db_session, made["id"], watch_version=2)
    allowed = client.post(f"{PREFIX}/{made['id']}/history", json={"history_days": 14})
    assert allowed.status_code == 200, allowed.text
    assert (await _row(db_session, made["id"])).history_days == 14


# --------------------------------------------------------------------------- pinned rows
SMALL_ROWS = {"pw_max_rows_per_search": 10}  # a row target of 8 rows per search
PINS = [[f"user{n}"] for n in range(9)]  # 9 pinned rows per bucket against a target of 8


async def test_create_refuses_pins_that_cannot_fit_one_history_search(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema, **SMALL_ROWS)
    refused = client.post(PREFIX, json=_body("wide-pins", pinned_values=PINS, history_days=14))
    assert refused.status_code == 422
    assert "9 rows per bucket" in _message(refused) and "8 rows" in _message(refused)
    # The default request (history on) is a history request too.
    assert client.post(PREFIX, json=_body("wide-default", pinned_values=PINS)).status_code == 422
    # No history asked: the same pins are fine.
    assert client.post(
        PREFIX, json=_body("wide-none", pinned_values=PINS, history_days=0)
    ).status_code == 201
    # Exactly the target is allowed.
    assert client.post(
        PREFIX, json=_body("exact", pinned_values=PINS[:8], history_days=14)
    ).status_code == 201


async def test_a_combination_watch_counts_the_product_of_its_components(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema, **SMALL_ROWS)
    properties = [{"kind": "NORMALIZED", "name": "username"}, ACTION]
    pins = [[f"u{a}", f"a{b}"] for a in range(2) for b in range(5)]  # 2 x 5 = 10 > 8
    refused = client.post(
        PREFIX, json=_body("combo", properties=properties, pinned_values=pins, history_days=14)
    )
    assert refused.status_code == 422 and "10 rows per bucket" in _message(refused)


async def test_patch_checks_the_pins_when_they_change_on_a_watch_that_will_read(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema, **SMALL_ROWS)
    made = _create(client, "pins-patch", pinned_values=PINS[:3], history_days=14)
    refused = client.patch(
        f"{PREFIX}/{made['id']}",
        json={"expected_updated_at": made["updated_at"], "pinned_values": PINS},
    )
    assert refused.status_code == 422 and "9 rows per bucket" in _message(refused)
    # Asking for history on a watch whose pins are already too wide is refused too.
    wide = _create(client, "pins-wide", pinned_values=PINS, history_days=0)
    refused = client.patch(
        f"{PREFIX}/{wide['id']}",
        json={"expected_updated_at": wide["updated_at"], "history_days": 14},
    )
    assert refused.status_code == 422 and "9 rows per bucket" in _message(refused)
    # A watch that read already (COMPLETE) may change its pins: nothing reads again.
    await _set(db_session, made["id"], **_finished_columns(PwHistoryState.COMPLETE, "done"))
    ok = client.patch(
        f"{PREFIX}/{made['id']}",
        json={"expected_updated_at": made["updated_at"], "pinned_values": PINS},
    )
    assert ok.status_code == 200, ok.text


async def test_changing_the_pins_of_a_reading_watch_is_checked_on_patch_and_put(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema, **SMALL_ROWS)
    made = _create(client, "pins-reading", pinned_values=PINS[:3], history_days=14)
    await _set(db_session, made["id"], **_reading_columns())
    refused = client.patch(
        f"{PREFIX}/{made['id']}",
        json={"expected_updated_at": made["updated_at"], "pinned_values": PINS},
    )
    assert refused.status_code == 422 and "9 rows per bucket" in _message(refused)
    pins = client.put(
        f"{PREFIX}/{made['id']}/pins",
        json={"expected_updated_at": made["updated_at"], "pinned_values": PINS},
    )
    assert pins.status_code == 422 and "9 rows per bucket" in _message(pins)
    assert len((await _row(db_session, made["id"])).pinned_values) == 3


async def test_post_history_refuses_pins_that_cannot_fit(db_session, db_schema: str) -> None:
    await _seed(db_session)
    client = _engineer(db_schema, **SMALL_ROWS)
    made = _create(client, "pins-post", pinned_values=PINS, history_days=0)
    refused = client.post(f"{PREFIX}/{made['id']}/history", json={"history_days": 14})
    assert refused.status_code == 422
    assert "9 rows per bucket" in _message(refused) and "8 rows" in _message(refused)
    assert (await _row(db_session, made["id"])).history_days == 0
    # Asking for no history is always allowed.
    zero = client.post(f"{PREFIX}/{made['id']}/history", json={"history_days": 0})
    assert zero.status_code == 200


# ------------------------------------------------------------------------------- the read
async def _watch_in_state(db_session, db_schema: str, name: str, columns: dict) -> dict:  # type: ignore[no-untyped-def]
    made = _create(_engineer(db_schema), name, history_days=28)
    if columns:
        await _set(db_session, made["id"], **columns)
    return made


async def _history_of(db_schema: str, watch_id: str) -> dict:
    out = _client(db_schema, _principal("viewer")).get(f"{PREFIX}/{watch_id}")
    assert out.status_code == 200, out.text
    return out.json()["history"]


async def test_a_watch_that_was_not_requested_reads_as_nothing_read(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    made = await _watch_in_state(db_session, db_schema, "fresh", {})
    history = await _history_of(db_schema, made["id"])
    assert history["state"] == "NOT_REQUESTED" and history["days_requested"] == 28
    for key in ("target_start", "floor", "through", "end", "days_read", "searches",
                "buckets_read", "gaps", "reason", "started_at", "finished_at"):
        assert history[key] is None, key
    assert history["progress_unreadable"] is False


async def test_a_reading_with_a_progress_shows_its_counters_gaps_and_days(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    made = await _watch_in_state(db_session, db_schema, "reading", _reading_columns())
    history = await _history_of(db_schema, made["id"])
    floor = BOUNDARY - timedelta(days=28)
    assert history["state"] == "READING"
    assert history["searches"] == 7 and history["buckets_read"] == 576
    assert history["days_read"] == 2.0
    assert datetime.fromisoformat(history["floor"]) == floor
    assert datetime.fromisoformat(history["through"]) == floor + 575 * B
    assert history["end"] is None and history["finished_at"] is None
    assert history["reason"] == "reading 576 buckets"
    assert [g["reason"] for g in history["gaps"]] == ["could not be read after 3 attempts"] * 2
    assert datetime.fromisoformat(history["gaps"][0]["start"]) == floor + 10 * B
    assert datetime.fromisoformat(history["gaps"][0]["end"]) == floor + 14 * B
    assert history["progress_unreadable"] is False


async def test_days_read_comes_from_floor_and_through_never_from_the_end(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    made = await _watch_in_state(
        db_session, db_schema, "days", _finished_columns(PwHistoryState.COMPLETE, "done")
    )
    history = await _history_of(db_schema, made["id"])
    assert datetime.fromisoformat(history["end"]) == BOUNDARY
    assert history["days_read"] == 2.0, "the 28 days to history_end are never what was read"
    await _set(db_session, made["id"], history_through=None)
    # a half-cleared row does not decode, and does not guess either
    assert (await _history_of(db_schema, made["id"]))["days_read"] is None


async def test_a_reading_with_an_empty_progress_says_it_starts_at_the_next_collection(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    columns = history_columns(None, state=PwHistoryState.READING, reason="")
    made = await _watch_in_state(db_session, db_schema, "restarted", columns)
    history = await _history_of(db_schema, made["id"])
    assert history["state"] == "READING"
    assert history["reason"] == "the reading starts at the next collection"
    assert history["searches"] is None and history["buckets_read"] is None
    assert history["gaps"] is None and history["progress_unreadable"] is False


async def test_a_partial_with_an_empty_progress_says_the_switch_was_off(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    begun = begin_reading(
        history_days=28, boundary=BOUNDARY, now=BOUNDARY, enabled=False, settings=PLANNER
    )
    assert begun.state is PwHistoryState.PARTIAL
    made = await _watch_in_state(db_session, db_schema, "switched-off", begun.columns)
    history = await _history_of(db_schema, made["id"])
    assert history["state"] == "PARTIAL"
    assert history["reason"] == "history reading is switched off"
    assert history["searches"] is None and history["gaps"] is None
    assert history["days_read"] is None and history["progress_unreadable"] is False


@pytest.mark.parametrize("state", [PwHistoryState.PARTIAL, PwHistoryState.COMPLETE])
async def test_a_finished_reading_shows_its_end_and_its_stored_reason(
    db_session, db_schema: str, state: PwHistoryState
) -> None:
    await _seed(db_session)
    reason = "QRadar holds events of this scope from 2026-10-10"
    made = await _watch_in_state(
        db_session, db_schema, f"done-{state.value}", _finished_columns(state, reason)
    )
    history = await _history_of(db_schema, made["id"])
    assert history["state"] == state.value and history["reason"] == reason
    assert datetime.fromisoformat(history["end"]) == BOUNDARY
    assert datetime.fromisoformat(history["finished_at"]) == BOUNDARY
    assert history["days_read"] == 2.0 and history["buckets_read"] == 576
    assert len(history["gaps"]) == 2


async def test_an_unavailable_reading_has_no_floor_and_no_days(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    nothing = replace(_progress(0, gaps=False), floor=None, first_event_bucket=None, through=None,
                      buckets_read=0, searches=3)
    columns = handoff_columns(
        nothing, state=PwHistoryState.UNAVAILABLE, reason="QRadar holds no events of this scope",
        boundary=BOUNDARY, now=BOUNDARY, settings=PLANNER,
    )
    made = await _watch_in_state(db_session, db_schema, "unavailable", columns)
    history = await _history_of(db_schema, made["id"])
    assert history["state"] == "UNAVAILABLE" and history["days_read"] is None
    assert history["floor"] is None and history["through"] is None and history["end"] is None
    assert history["searches"] == 3 and history["buckets_read"] == 0 and history["gaps"] == []
    assert history["reason"] == "QRadar holds no events of this scope"


@pytest.mark.parametrize("damage", ["junk", "column", "no reason"])
async def test_a_progress_that_does_not_read_back_is_unreadable_never_zero(
    db_session, db_schema: str, damage: str
) -> None:
    await _seed(db_session)
    columns = _finished_columns(PwHistoryState.PARTIAL, "finished")
    if damage == "junk":
        columns["history_progress"] = {"junk": 1}
    elif damage == "column":
        columns["history_floor"] = BOUNDARY - timedelta(days=3)
    else:
        columns["history_progress"] = {k: v for k, v in columns["history_progress"].items()
                                       if k != "adapter"}
    made = await _watch_in_state(db_session, db_schema, f"broken-{damage}".replace(" ", "-"),
                                 columns)
    history = await _history_of(db_schema, made["id"])
    assert history["progress_unreadable"] is True
    assert history["state"] == "PARTIAL"
    for key in ("searches", "buckets_read", "gaps", "days_read"):
        assert history[key] is None, key
    assert history["reason"] == "the stored history progress could not be read back"


async def test_the_list_carries_the_history_of_every_watch_without_an_estimate(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    made = await _watch_in_state(db_session, db_schema, "listed", _reading_columns())
    rows = _client(db_schema, _principal("viewer")).get(PREFIX).json()
    [row] = [r for r in rows if r["id"] == made["id"]]
    assert row["history"]["state"] == "READING" and row["history"]["days_read"] == 2.0
    assert row["history"]["estimate"] is None, "the estimate is a detail read"


# ----------------------------------------------------------------------------- estimate
async def test_the_estimate_is_on_the_detail_of_a_watch_that_has_asked_and_not_finished(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    engineer = _engineer(db_schema)
    asked = _create(engineer, "est-asked", history_days=28)
    est = asked["history"]["estimate"]
    assert est["cadence_seconds"] == asked["cadence_seconds"] > 0
    assert (est["searches_min"], est["searches_max"]) == (15, 561)
    assert (est["ticks_min"], est["ticks_max"]) == (5, 47)
    assert "QRadar holds the whole window" in est["assumptions"]
    assert "search time per search is not modelled" in est["assumptions"]
    pinned = _create(engineer, "est-pins", history_days=28, pinned_values=[["alice"]])
    assert (pinned["history"]["estimate"]["searches_min"],
            pinned["history"]["estimate"]["searches_max"]) == (20, 617)
    none = _create(engineer, "est-none", history_days=0)
    assert none["history"]["estimate"] is None
    await _set(db_session, asked["id"], **_reading_columns())
    reading = _client(db_schema, _principal("viewer")).get(f"{PREFIX}/{asked['id']}").json()
    assert reading["history"]["estimate"] is not None
    await _set(db_session, asked["id"], **_finished_columns(PwHistoryState.COMPLETE, "done"))
    done = _client(db_schema, _principal("viewer")).get(f"{PREFIX}/{asked['id']}").json()
    assert done["history"]["estimate"] is None


# ----------------------------------------------------------------------- statement counts
async def test_the_list_costs_the_same_statements_for_one_and_ten_watches(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    engineer = _engineer(db_schema)
    reader = _client(db_schema, _principal("viewer"))
    first = _create(engineer, "count-0", history_days=28)
    await _set(db_session, first["id"], **_reading_columns())
    assert reader.get(PREFIX).status_code == 200
    reader.statements.clear()  # type: ignore[attr-defined]
    assert reader.get(PREFIX).status_code == 200
    one = len(reader.statements)  # type: ignore[attr-defined]
    states = [
        _reading_columns(),
        _finished_columns(PwHistoryState.COMPLETE, "done"),
        _finished_columns(PwHistoryState.PARTIAL, "cut"),
        history_columns(None, state=PwHistoryState.READING, reason=""),
        {},
    ]
    for index in range(1, 10):
        made = _create(engineer, f"count-{index}", history_days=28)
        columns = states[index % len(states)]
        if columns:
            await _set(db_session, made["id"], **columns)
    reader.statements.clear()  # type: ignore[attr-defined]
    rows = reader.get(PREFIX).json()
    assert len(rows) == 10
    assert len(reader.statements) == one  # type: ignore[attr-defined]


async def test_the_catalog_adds_exactly_one_statement_whatever_is_reading(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    engineer = _engineer(db_schema)
    reader = _client(db_schema, _principal("viewer"))
    reader.get(f"{PREFIX}/catalog")
    reader.statements.clear()  # type: ignore[attr-defined]
    empty = reader.get(f"{PREFIX}/catalog").json()
    quiet = len(reader.statements)  # type: ignore[attr-defined]
    counting = [s for s in reader.statements if re.search(r"history_state\s*=", s)]  # type: ignore[attr-defined]
    assert len(counting) == 1, "reading_count is one statement"
    assert empty["history"]["reading_count"] == 0
    for index in range(5):
        made = _create(engineer, f"cat-{index}", history_days=28)
        await _set(db_session, made["id"], **_reading_columns())
    reader.statements.clear()  # type: ignore[attr-defined]
    busy = reader.get(f"{PREFIX}/catalog").json()
    assert busy["history"]["reading_count"] == 5
    assert len(reader.statements) == quiet  # type: ignore[attr-defined]


async def test_the_catalog_states_the_history_bounds_and_the_free_budget(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    reader = _client(
        db_schema, _principal("viewer"), pw_bootstrap_days=21, pw_bootstrap_searches_per_tick=9,
        pw_bootstrap_seconds_per_tick=120,
    )
    body = reader.get(f"{PREFIX}/catalog").json()
    history = body["history"]
    assert history == {
        "enabled": True, "days_default": 21, "days_min": 7, "days_max": 21,
        "searches_per_tick": 9, "seconds_per_tick": 120, "reading_count": 0,
        "searches_free": body["search_budget"] - body["budget_used"],
    }
    off = _client(db_schema, _principal("viewer"), pw_bootstrap_enabled=False)
    assert off.get(f"{PREFIX}/catalog").json()["history"]["enabled"] is False


# ------------------------------------------------------------------- the budget at enable
def _budget(budget: int) -> dict[str, object]:
    return {"pw_max_searches_per_tick": budget, "pw_max_searches_per_watch_per_tick": 3}


def _enable(client, made: dict):  # type: ignore[no-untyped-def]
    return client.patch(
        f"{PREFIX}/{made['id']}",
        json={"expected_updated_at": made["updated_at"], "enabled": True},
    )


async def test_enabling_a_history_watch_needs_the_smallest_step_free(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    # a watch without pins costs 2 live searches and needs 2 more for a history step
    tight = _engineer(db_schema, **_budget(3))
    made = _create(tight, "tight", history_days=28)
    refused = _enable(tight, made)
    assert refused.status_code == 409, refused.text
    message = refused.json()["detail"]
    assert "at least 2 free searches per tick" in message
    assert "but 1 are left" in message and "live cost of 2 of 3" in message
    assert (await _row(db_session, made["id"])).enabled is False
    exact = _engineer(db_schema, **_budget(4))
    ok = _enable(exact, _create(exact, "exact", history_days=28))
    assert ok.status_code == 200, ok.text
    assert ok.json()["history"]["estimate"] is not None, "the enable response carries the estimate"


async def test_a_pinned_history_watch_needs_three_free_searches(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    tight = _engineer(db_schema, **_budget(5))
    made = _create(tight, "pin-tight", history_days=28, pinned_values=[["alice"]])
    refused = _enable(tight, made)
    assert refused.status_code == 409
    message = refused.json()["detail"]
    assert "at least 3 free searches per tick" in message and "but 2 are left" in message
    exact = _engineer(db_schema, **_budget(6))
    ok = _enable(exact, _create(exact, "pin-exact", history_days=28, pinned_values=[["alice"]]))
    assert ok.status_code == 200, ok.text


async def test_every_enabled_watchs_live_cost_comes_off_what_is_free(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema, **_budget(6))
    quiet = _create(client, "quiet", history_days=0)
    assert _enable(client, quiet).status_code == 200  # live cost 2 of 6
    asking = _create(client, "asking", history_days=28, pinned_values=[["alice"]])  # live 3
    refused = _enable(client, asking)
    assert refused.status_code == 409
    assert "but 1 are left" in refused.json()["detail"] and "5 of 6" in refused.json()["detail"]


async def test_a_watch_without_history_keeps_todays_rules_exactly(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema, **_budget(2))  # exactly its live cost, nothing left over
    made = _create(client, "no-history", history_days=0)
    assert _enable(client, made).status_code == 200


async def test_a_watch_that_never_asked_keeps_todays_rules_exactly(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    unset = _create(_engineer(db_schema, pw_bootstrap_enabled=False), "unset")
    assert unset["history"]["days_requested"] is None
    tight = _engineer(db_schema, **_budget(2))
    assert _enable(tight, unset).status_code == 200


async def test_a_watch_whose_reading_finished_needs_no_step(db_session, db_schema: str) -> None:
    await _seed(db_session)
    client = _engineer(db_schema, **_budget(2))
    made = _create(client, "finished", history_days=28)
    await _set(db_session, made["id"], **_finished_columns(PwHistoryState.COMPLETE, "done"))
    assert _enable(client, made).status_code == 200


async def test_with_the_switch_off_there_is_no_history_reason(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema, pw_bootstrap_enabled=False, **_budget(2))
    made = _create(client, "off", history_days=28)
    assert _enable(client, made).status_code == 200


async def test_asking_for_history_on_an_enabled_watch_checks_the_budget(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    tight = _engineer(db_schema, **_budget(3))
    made = _create(tight, "later", history_days=0)
    assert _enable(tight, made).status_code == 200
    refused = tight.post(f"{PREFIX}/{made['id']}/history", json={"history_days": 14})
    assert refused.status_code == 409 and "at least 2 free searches" in _message(refused)
    assert (await _row(db_session, made["id"])).history_days == 0
    assert await _history_audits(db_session) == []
    roomy = _engineer(db_schema, **_budget(4))
    ok = roomy.post(f"{PREFIX}/{made['id']}/history", json={"history_days": 14})
    assert ok.status_code == 200 and ok.json()["history"]["days_requested"] == 14
    assert [a.detail for a in await _history_audits(db_session)] == [
        {"history_days": 14, "previous": 0}
    ]


async def test_a_reading_watch_is_also_asked_for_a_step_at_enable(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    tight = _engineer(db_schema, **_budget(3))
    made = _create(tight, "reading-tight", history_days=28)
    await _set(db_session, made["id"], **_reading_columns())
    refused = _enable(tight, made)
    assert refused.status_code == 409 and "free searches per tick" in refused.json()["detail"]


# ---------------------------------------------------------------- the codes of a 409 (T-145)
async def test_the_history_budget_refusal_carries_its_code(
    db_session, db_schema: str
) -> None:
    """The 409 names its reason and codes it, so the browser never reads the wording."""
    await _seed(db_session)
    tight = _engineer(db_schema, **_budget(3))
    made = _create(tight, "coded-history", history_days=28)
    refused = _enable(tight, made)
    assert refused.status_code == 409, refused.text
    assert refused.json()["codes"] == ["HISTORY_BUDGET"]
    assert "at least 2 free searches per tick" in refused.json()["detail"]
    assert (await _row(db_session, made["id"])).enabled is False


async def test_a_tick_budget_and_a_history_budget_refusal_carry_both_codes(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    tight = _engineer(db_schema, **_budget(3))
    quiet = _create(tight, "coded-quiet", history_days=0)
    assert _enable(tight, quiet).status_code == 200
    # The pair is over the tick's budget and the history step has nothing free: both reasons,
    # in the order the service produces them.
    asking = _create(tight, "coded-both", history_days=28)
    refused = _enable(tight, asking)
    assert refused.status_code == 409, refused.text
    assert refused.json()["codes"] == ["TICK_BUDGET", "HISTORY_BUDGET"]
    assert "searches per tick against a budget of 3" in refused.json()["detail"]
    assert "free searches per tick" in refused.json()["detail"]


async def test_the_history_request_refusal_carries_the_history_code(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    tight = _engineer(db_schema, **_budget(3))
    made = _create(tight, "coded-request", history_days=0)
    assert _enable(tight, made).status_code == 200
    refused = tight.post(f"{PREFIX}/{made['id']}/history", json={"history_days": 14})
    assert refused.status_code == 409, refused.text
    assert refused.json()["codes"] == ["HISTORY_BUDGET"]
    assert "at least 2 free searches" in refused.json()["detail"]
    assert (await _row(db_session, made["id"])).history_days == 0


# ---------------------------------------------------------------- the history is the collector's
async def test_disabling_and_enabling_a_reading_watch_leaves_every_history_column(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "toggle", history_days=28)
    await _set(db_session, made["id"], **_reading_columns())
    before = await _row(db_session, made["id"])
    snapshot = {c: getattr(before, c) for c in HISTORY_COLUMNS}
    assert snapshot["history_state"] == "READING" and snapshot["history_progress"]
    token = made["updated_at"]
    for enabled in (True, False, True):
        out = client.patch(
            f"{PREFIX}/{made['id']}", json={"expected_updated_at": token, "enabled": enabled}
        )
        assert out.status_code == 200, out.text
        token = out.json()["updated_at"]
        current = await _row(db_session, made["id"])
        assert {c: getattr(current, c) for c in HISTORY_COLUMNS} == snapshot
    assert (await _row(db_session, made["id"])).enabled is True


async def test_a_version_bump_leaves_the_history_to_the_collector(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "bump", history_days=28)
    await _set(db_session, made["id"], **_reading_columns())
    snapshot = {c: getattr(await _row(db_session, made["id"]), c) for c in HISTORY_COLUMNS}
    out = client.patch(
        f"{PREFIX}/{made['id']}",
        json={"expected_updated_at": made["updated_at"],
              "properties": [{"kind": "NORMALIZED", "name": "username"}]},
    )
    assert out.status_code == 200 and out.json()["watch_version"] == 2
    current = await _row(db_session, made["id"])
    assert {c: getattr(current, c) for c in HISTORY_COLUMNS} == snapshot
    assert current.collected_through is None


# ------------------------------------------------------------------------------- origin
async def test_buckets_and_series_carry_their_origin(db_session, db_schema: str) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "origin", history_days=0)
    watch_id = uuid.UUID(made["id"])
    db_session.add_all([
        _bucket(made["id"], 3, origin=PwBucketOrigin.HISTORY),
        _bucket(made["id"], 2, origin=PwBucketOrigin.LIVE),
    ])
    value = PwValue(watch_id=watch_id, watch_version=1, value_key="alice",
                    tracked_since=_bucket_start(5), first_seen_at=_bucket_start(5))
    db_session.add(value)
    await db_session.flush()
    for back in (3, 2):
        db_session.add(PwValueMetric(pw_value_id=value.id, bucket_start=_bucket_start(back),
                                     events=5, bucket_seconds=300))
    await db_session.commit()
    reader = _client(db_schema, _principal("viewer"))
    buckets = reader.get(f"{PREFIX}/{made['id']}/buckets", params={"hours": 2}).json()
    assert [b["origin"] for b in buckets] == ["HISTORY", "LIVE"]
    series = reader.get(
        f"{PREFIX}/{made['id']}/series", params=[("value_ids", str(value.id)), ("hours", 2)]
    ).json()
    assert [p["origin"] for p in series["series"][0]["points"]] == ["HISTORY", "LIVE"]


# ----------------------------------------------------------------------------- evidence
async def test_a_new_value_incident_carries_the_history_it_was_judged_against(
    db_session, db_schema: str
) -> None:
    rig = await make_rig(
        db_session, detectors=(NEW_VALUE,), values=("bob",), pw_new_value_history_days=1
    )
    rig.default = {"bob": 400}
    day = timedelta(days=1)
    floor = T0 - day
    # the older half of the window was read from QRadar's past, the newer half collected live
    await history_buckets(db_session, rig.watch, start=floor, stop=T0 - day // 4,
                          origin=PwBucketOrigin.HISTORY)
    await history_buckets(db_session, rig.watch, start=T0 - day // 4, stop=T0)
    rig.watch.history_floor = floor
    await db_session.commit()
    plan(rig.plan, 0, 4, {"bob": 400, "mallory": 30})
    await rig.ticks(0, 3)
    [incident] = await rig.anomalies(NEW_VALUE)
    assert incident.details["extra"]["history_buckets_verified"] == 288
    assert incident.details["extra"]["history_buckets_from_history"] == 216
    assert incident.details["extra"]["history_floor"] == floor.isoformat()
    reader = _client(db_schema, _principal("viewer"))
    out = reader.get(f"/api/v1/anomalies/{incident.id}").json()["detection"]
    assert out["history_buckets_verified"] == 288
    assert out["history_buckets_from_history"] == 216
    assert datetime.fromisoformat(out["history_floor"]) == floor
    assert timedelta(minutes=5) == STEP


async def test_a_watch_without_a_floor_reports_none_and_zero_history_buckets(
    db_session, db_schema: str
) -> None:
    rig = await make_rig(
        db_session, detectors=(NEW_VALUE,), values=("bob",), pw_new_value_history_days=1
    )
    rig.default = {"bob": 400}
    await history_buckets(db_session, rig.watch, start=T0 - timedelta(days=1), stop=T0)
    plan(rig.plan, 0, 4, {"bob": 400, "mallory": 30})
    await rig.ticks(0, 3)
    [incident] = await rig.anomalies(NEW_VALUE)
    assert incident.details["extra"]["history_buckets_from_history"] == 0
    assert incident.details["extra"]["history_floor"] is None
    out = _client(db_schema, _principal("viewer")).get(
        f"/api/v1/anomalies/{incident.id}"
    ).json()["detection"]
    assert out["history_buckets_from_history"] == 0 and out["history_floor"] is None


async def test_an_incident_stored_before_the_bootstrap_answers_null_never_zero(
    db_session, db_schema: str
) -> None:
    rig = await make_rig(
        db_session, detectors=(NEW_VALUE,), values=("bob",), pw_new_value_history_days=1
    )
    rig.default = {"bob": 400}
    await history_buckets(db_session, rig.watch, start=T0 - timedelta(days=1), stop=T0)
    plan(rig.plan, 0, 4, {"bob": 400, "mallory": 30})
    await rig.ticks(0, 3)
    [incident] = await rig.anomalies(NEW_VALUE)
    extra = dict(incident.details["extra"])
    for key in ("history_buckets_from_history", "history_floor"):
        extra.pop(key)
    details = dict(incident.details)
    details["extra"] = extra
    incident.details = details
    await db_session.commit()
    out = _client(db_schema, _principal("viewer")).get(
        f"/api/v1/anomalies/{incident.id}"
    ).json()["detection"]
    assert out["history_buckets_verified"] == 288
    assert out["history_buckets_from_history"] is None and out["history_floor"] is None


async def test_the_bucket_rows_are_unchanged_by_reading_the_history_api(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    made = _create(_engineer(db_schema), "inert", history_days=14)
    db_session.add(_bucket(made["id"], 3))
    await db_session.commit()
    reader = _client(db_schema, _principal("viewer"))
    for _ in range(2):
        assert reader.get(f"{PREFIX}/{made['id']}").status_code == 200
    rows = (await db_session.scalars(select(PwBucket))).all()
    assert len(rows) == 1
