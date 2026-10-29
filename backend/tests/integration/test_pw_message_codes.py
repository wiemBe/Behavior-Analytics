"""MSG-4b: the stored property-watch sentences are written with their message code.

A status sentence, an incident explanation and a transition reason each keep their English text
column and gain a `{code, params}` beside it. The two are one fact: whenever a writer changes
the text it changes the code, and a writer that clears the text clears the code.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from app.i18n.messages import MessageRef, render
from app.models.enums import PwScopeKind, PwWatchStatus
from app.models.property_watch import PropertyWatch, PwAnomaly, PwAnomalyTransition
from tests.integration.factories import make_instance, make_log_source
from tests.integration.pw_engine_support import SPIKE, STEP, T0, add_cells, make_rig, plan_range
from tests.integration.test_pw_collection import (
    NOW,
    _group,
    add_watch,
    collect,
    provider_with,
    reload,
)
from tests.integration.test_pw_history_collection import flat_provider, hcfg, tick
from tests.integration.test_t138_cadence_follow_ups import _unparsed_provider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def stored(watch: PropertyWatch) -> dict[str, Any] | None:
    return watch.last_status_reason_i18n


def assert_pair(text: str | None, pair: dict[str, Any] | None) -> None:
    """The stored text is the English of the stored code: they cannot disagree."""
    if pair is None:
        return
    assert text == render(MessageRef(pair["code"], _raw(pair["params"])))


def _raw(params: dict[str, Any]) -> dict[str, Any]:
    """Wire parameters back to the raw values the sentence was rendered from."""
    out: dict[str, Any] = {}
    for name, value in params.items():
        if isinstance(value, dict) and value.get("kind") == "duration":
            out[name] = value["seconds"]
        elif isinstance(value, dict) and "code" in value:
            out[name] = MessageRef(value["code"], _raw(value["params"]))
        else:
            out[name] = value
    return out


# ---------------------------------------------------------------- collector status
async def test_a_stale_scope_is_stored_with_its_code_and_both_clear_together(db_session) -> None:
    instance = await make_instance(db_session)
    synced = NOW - timedelta(hours=30)
    await _group(db_session, instance, 1, synced=synced)
    await make_log_source(db_session, instance, qradar_id=11, group_ids=[1])
    watch = await add_watch(
        db_session, instance, scope_kind=PwScopeKind.LOG_SOURCE_GROUP, scope_group_id=1
    )
    await collect(db_session, instance, provider_with({"alice": 10}))
    await reload(db_session, watch)
    assert watch.last_status == PwWatchStatus.SCOPE_STALE.value
    assert watch.last_status_reason == (
        f"the group inventory was last synced at {synced.isoformat()}, older than 1 day, 0:00:00"
    )
    assert stored(watch) == {
        "code": "pw.scope.group_stale",
        "params": {
            "synced": synced.isoformat(),
            "limit": {"kind": "duration", "seconds": 86400.0},
        },
    }
    assert_pair(watch.last_status_reason, stored(watch))
    assert watch.status_reason_code == "pw.scope.group_stale"
    json.dumps(stored(watch))

    # the sync recovers: the status clears, and the code goes with its text
    await db_session.execute(
        update(type(watch).metadata.tables["log_source_group"]).values(
            synced_at=NOW - timedelta(minutes=5)
        )
    )
    await collect(db_session, instance, provider_with({"alice": 10}))
    await reload(db_session, watch)
    assert watch.last_status is None
    assert watch.last_status_reason is None and stored(watch) is None


async def test_a_group_too_large_and_an_empty_source_say_why_with_a_code(db_session) -> None:
    instance = await make_instance(db_session)
    await _group(db_session, instance, 1, synced=NOW)
    for i in range(3):
        await make_log_source(db_session, instance, qradar_id=20 + i, group_ids=[1])
    watch = await add_watch(
        db_session, instance, scope_kind=PwScopeKind.LOG_SOURCE_GROUP, scope_group_id=1
    )
    settings = hcfg(pw_max_sources_per_search=1, pw_max_chunks_per_watch=2)
    await collect(db_session, instance, provider_with({"alice": 10}), settings=settings)
    await reload(db_session, watch)
    assert watch.last_status == PwWatchStatus.SCOPE_TOO_LARGE.value
    assert watch.last_status_reason == "3 sources need 3 searches; the limit is 2"
    assert stored(watch) == {
        "code": "pw.scope.too_large",
        "params": {"sources": 3, "searches": 3, "limit": 2},
    }

    gone = await add_watch(
        db_session, instance, scope_kind=PwScopeKind.LOG_SOURCE_GROUP, scope_group_id=99
    )
    await collect(db_session, instance, provider_with({"alice": 10}), settings=settings)
    await reload(db_session, gone)
    assert gone.last_status_reason == "the log source group is not in the inventory"
    assert stored(gone) == {"code": "pw.scope.group_missing", "params": {}}


async def test_reading_history_is_stored_with_its_code_and_cleared_at_the_hand_off(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=2)
    provider = flat_provider()
    thin = hcfg(pw_bootstrap_searches_per_tick=2)
    await tick(db_session, instance, provider, settings=thin)
    await reload(db_session, watch)
    assert watch.last_status == PwWatchStatus.READING_HISTORY.value
    assert watch.last_status_reason == (
        "Reading history from QRadar: 1.0 of 2.0 days; nothing is judged until it finishes"
    )
    assert stored(watch) == {
        "code": "pw.status.reading_history",
        "params": {"done": 1.0, "total": 2.0},
    }
    assert_pair(watch.last_status_reason, stored(watch))

    await tick(db_session, instance, provider, settings=thin)
    await reload(db_session, watch)
    assert watch.last_status is None
    assert watch.last_status_reason is None and stored(watch) is None


async def test_not_applicable_is_stored_with_its_code(db_session) -> None:
    instance = await make_instance(db_session)
    start = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
    watch = await add_watch(db_session, instance, through=start - STEP)
    provider = _unparsed_provider()
    for k in range(12):
        await collect(db_session, instance, provider, now=start + STEP * k + timedelta(minutes=7))
    await reload(db_session, watch)
    assert watch.last_status == PwWatchStatus.NOT_APPLICABLE.value
    assert watch.last_status_reason == (
        "the property parsed on no event in scope; the values are not measured"
    )
    assert stored(watch) == {"code": "pw.status.not_applicable", "params": {}}


# ------------------------------------------------------------------ engine status
async def test_the_status_sentences_of_the_engine_follow_their_incidents_with_codes(
    db_session,
) -> None:
    rig = await make_rig(db_session)
    await add_cells(db_session, await rig.value("alice"))
    plan_range(rig.plan, 1, 3, {"alice": 500, "bob": 400})
    expected = {
        0: ("OK", "the latest collected bucket was evaluated", "pw.status.evaluated"),
        2: ("OPEN", "one or more value incidents are open", "pw.status.open"),
        3: ("RECOVERING", "one or more value incidents are recovering", "pw.status.recovering"),
    }
    for k in range(4):
        await rig.tick(k)
        if k in expected:
            watch = await rig.reload_watch()
            status, text, code = expected[k]
            assert (watch.last_status, watch.last_status_reason) == (status, text)
            assert stored(watch) == {"code": code, "params": {}}


async def test_a_failed_latest_search_is_stored_with_the_bucket_it_names(db_session) -> None:
    rig = await make_rig(db_session)
    await rig.ticks(0, 3)
    rig.fail = {3}
    await rig.tick(3)
    watch = await rig.reload_watch()
    bucket = (T0 + STEP * 3).isoformat()
    assert watch.last_status == PwWatchStatus.STALE.value
    assert watch.last_status_reason == (
        f"the latest search failed; bucket {bucket} is stored NOT_COLLECTED"
    )
    assert stored(watch) == {"code": "pw.status.latest_search_failed", "params": {"bucket": bucket}}
    rig.fail = set()
    await rig.tick(3)
    watch = await rig.reload_watch()
    assert watch.last_status == PwWatchStatus.OK.value
    assert stored(watch) == {"code": "pw.status.evaluated", "params": {}}
    assert_pair(watch.last_status_reason, stored(watch))


async def test_a_collection_status_the_engine_keeps_keeps_its_code_too(db_session) -> None:
    rig = await make_rig(db_session)
    await add_cells(db_session, await rig.value("alice"))
    await rig.collect(0)
    keep = {"code": "pw.scope.group_empty", "params": {}}
    await db_session.execute(
        update(PropertyWatch)
        .where(PropertyWatch.id == rig.watch.id)
        .values(
            last_status="SCOPE_EMPTY",
            last_status_reason="the group and its descendants hold no synced log source",
            last_status_reason_i18n=keep,
        )
    )
    await db_session.commit()
    await rig.evaluate(0)
    watch = await rig.reload_watch()
    assert watch.last_status == "SCOPE_EMPTY"
    assert stored(watch) == keep


async def test_a_status_written_without_a_code_leaves_no_old_code_behind(db_session) -> None:
    rig = await make_rig(db_session)
    await add_cells(db_session, await rig.value("alice"))
    await rig.collect(0)
    await db_session.execute(
        update(PropertyWatch)
        .where(PropertyWatch.id == rig.watch.id)
        .values(last_status="SCOPE_STALE", last_status_reason="stale", last_status_reason_i18n=None)
    )
    await db_session.commit()
    await rig.evaluate(0)
    watch = await rig.reload_watch()
    assert (watch.last_status_reason, stored(watch)) == ("stale", None)


# ---------------------------------------------------------- incidents and transitions
async def _spike(db_session) -> tuple[Any, PwAnomaly]:  # type: ignore[no-untyped-def]
    rig = await make_rig(db_session)
    await add_cells(db_session, await rig.value("alice"))
    plan_range(rig.plan, 1, 3, {"alice": 500, "bob": 400})
    await rig.ticks(0, 3)
    [anomaly] = await rig.anomalies(SPIKE)
    return rig, anomaly


async def test_an_incident_is_stored_with_the_code_of_its_explanation(db_session) -> None:
    _, anomaly = await _spike(db_session)
    assert anomaly.state == "OPEN"
    assert anomaly.explanation == (
        "500 events is above the median 100.0 (+400, 5.00x); robust z=26.98 (threshold +3.5)"
    )
    wire = anomaly.explanation_i18n
    assert wire is not None and wire["code"] == "pw.detector.spike_fired"
    basis = wire["params"].pop("basis")
    assert wire["params"] == {"observed": 500.0, "median": 100.0, "delta": 400.0, "ratio": 5.0}
    assert basis["code"] == "pw.detector.basis_z_high"
    assert basis["params"] == {"score": pytest.approx(26.98, abs=0.005), "threshold": 3.5}
    wire["params"]["basis"] = basis
    assert anomaly.explanation_code == "pw.detector.spike_fired"
    assert anomaly.suppression_reason_i18n is None
    assert anomaly.details["reason"] == anomaly.explanation
    assert anomaly.details["reason_code"] == "pw.detector.spike_fired"
    assert anomaly.details["reason_params"] == anomaly.explanation_i18n["params"]  # type: ignore[index]
    json.dumps(anomaly.details)


async def test_a_later_bucket_refreshes_the_text_and_the_code_together(db_session) -> None:
    rig, anomaly = await _spike(db_session)
    first = anomaly.explanation
    rig.plan[3] = {"alice": 700, "bob": 400}
    await rig.tick(3)
    [anomaly] = await rig.anomalies(SPIKE)
    assert anomaly.explanation != first
    assert anomaly.explanation == (
        "700 events is above the median 100.0 (+600, 7.00x); robust z=40.47 (threshold +3.5)"
    )
    assert anomaly.explanation_i18n["params"]["observed"] == 700.0  # type: ignore[index]


async def test_a_resolved_transition_stores_its_code_and_the_lifecycle_reasons_are_coded(
    db_session,
) -> None:
    rig, anomaly = await _spike(db_session)
    await db_session.execute(
        update(PropertyWatch).where(PropertyWatch.id == rig.watch.id).values(watch_version=2)
    )
    await db_session.commit()
    plan_range(rig.plan, 3, 6, {"alice": 500, "bob": 400})
    await rig.tick(3)
    rows = await rig.transitions(anomaly)
    resolved = rows[-1]
    assert str(resolved.to_state) == "RESOLVED"
    assert resolved.reason == "watch definition changed"
    assert resolved.reason_i18n == {"code": "pw.transition.definition_changed", "params": {}}
    # the lifecycle's own reasons (Family 2, MSG-5) carry their code, rendered from it
    assert rows[:-1]
    for row in rows[:-1]:
        assert row.reason_i18n is not None
        assert row.reason_code is not None and row.reason_code.startswith("anomaly.lifecycle.")
        assert row.reason == render(MessageRef(row.reason_code, row.reason_params or {}))
    assert resolved.reason_code == "pw.transition.definition_changed"


async def test_a_disabled_watch_resolves_its_incident_with_a_coded_reason(
    db_session,
) -> None:
    from app.property_watch.engine import PwAnomalyEngine
    from app.services import property_watches as svc

    rig, anomaly = await _spike(db_session)
    anomaly_id = anomaly.id
    watch = await rig.reload_watch()
    resolved = await PwAnomalyEngine(db_session, settings=rig.settings).resolve_watch_incidents(
        watch, reason=svc.MESSAGE_WATCH_DISABLED
    )
    await db_session.commit()
    assert resolved == 1
    db_session.expire_all()
    last = (
        await db_session.scalars(
            select(PwAnomalyTransition)
            .where(PwAnomalyTransition.pw_anomaly_id == anomaly_id)
            .order_by(PwAnomalyTransition.occurred_at.desc(), PwAnomalyTransition.created_at.desc())
        )
    ).first()
    assert last is not None
    assert last.reason == "watch disabled; no longer evaluated"
    assert last.reason_i18n == {"code": "pw.transition.watch_disabled", "params": {}}
