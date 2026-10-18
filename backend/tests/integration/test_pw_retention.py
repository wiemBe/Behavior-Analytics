"""Property-watch retention is bounded and removes aged facts only (design §G.7.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, insert, select

from app.models.enums import AnomalyState
from app.models.property_watch import PwAnomaly, PwBucket, PwValue, PwValueMetric
from app.services import pw_retention
from app.services.pw_retention import BLOCKING_INCIDENT_STATES, PwRetention
from tests.integration.factories import make_instance
from tests.integration.test_pw_collection import add_watch, cfg

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 10, 13, 10, 7, tzinfo=UTC)
OLD = NOW - timedelta(days=40)
FRESH = NOW - timedelta(days=1)


async def count(session, model) -> int:  # type: ignore[no-untyped-def]
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


def bucket_row(watch_id, at):  # type: ignore[no-untyped-def]
    return dict(
        watch_id=watch_id, bucket_start=at, bucket_seconds=300, watch_version=1,
        total_events=10, parsed_events=10, unparsed_events=0, untracked_events=0,
        values_measurement="EXACT", pinned_measurement="NOT_REQUESTED", totals_complete=True,
        observed_values=1, tracked_values_observed=1, admitted_values=0,
        below_admission_threshold=0, rejected_over_quota=0, admission_truncated=False,
        scope_changed=False, in_maintenance=False, query_provenance={}, collected_at=at,
    )


def value_row(watch_id, key, *, active, retired_at):  # type: ignore[no-untyped-def]
    return dict(
        watch_id=watch_id, watch_version=1, value_key=key, component_0=key, pinned=False,
        active=active, retired_at=retired_at, first_seen_at=OLD, last_seen_at=OLD,
        tracked_since=OLD, truncated=False,
    )


async def seeded(session):  # type: ignore[no-untyped-def]
    instance = await make_instance(session)
    watch = await add_watch(session, instance)
    await session.execute(
        insert(PwBucket),
        [bucket_row(watch.id, OLD + timedelta(minutes=5 * i)) for i in range(7)]
        + [bucket_row(watch.id, FRESH)],
    )
    await session.execute(
        insert(PwValue),
        [
            value_row(watch.id, "gone", active=False, retired_at=OLD),
            value_row(watch.id, "retired-but-recent-metric", active=False, retired_at=OLD),
            value_row(watch.id, "active-and-old", active=True, retired_at=None),
            value_row(watch.id, "retired-recently", active=False, retired_at=FRESH),
        ],
    )
    pairs = (await session.execute(select(PwValue.id, PwValue.value_key))).all()
    ids = {key: vid for vid, key in pairs}
    await session.execute(
        insert(PwValueMetric),
        [
            dict(pw_value_id=ids["gone"], bucket_start=OLD, events=3, bucket_seconds=300),
            dict(pw_value_id=ids["active-and-old"], bucket_start=OLD, events=3,
                 bucket_seconds=300),
            dict(pw_value_id=ids["retired-but-recent-metric"], bucket_start=FRESH, events=3,
                 bucket_seconds=300),
        ],
    )
    await session.commit()


async def test_retention_removes_aged_buckets_and_metrics_then_retired_values(db_session) -> None:
    await seeded(db_session)
    report = await PwRetention(db_session, settings=cfg(), clock=lambda: NOW).run()
    # `gone` is retired and its last metric aged out in this very run: it leaves with it
    assert report.deleted == {"pw_value_metric": 2, "pw_bucket": 7, "pw_value": 1}
    assert report.incomplete == []
    report = await PwRetention(db_session, settings=cfg(), clock=lambda: NOW).run()
    assert report.deleted == {"pw_value_metric": 0, "pw_bucket": 0, "pw_value": 0}
    keys = set(await db_session.scalars(select(PwValue.value_key)))
    assert keys == {"active-and-old", "retired-but-recent-metric", "retired-recently"}
    assert await count(db_session, PwBucket) == 1
    assert await count(db_session, PwValueMetric) == 1


async def test_retention_works_in_bounded_batches_and_reports_what_is_left(db_session) -> None:
    await seeded(db_session)
    settings = cfg(pw_retention_batch_size=100, pw_retention_max_batches=1)
    # one batch of 100 would take every aged row; shrink the batch below the data
    small = settings.model_copy(update={"pw_retention_batch_size": 3})
    report = await PwRetention(db_session, settings=small, clock=lambda: NOW).run()
    assert report.deleted["pw_bucket"] == 3
    assert report.incomplete == ["pw_bucket"]
    assert await count(db_session, PwBucket) == 5
    # a second run continues where the first stopped
    report = await PwRetention(db_session, settings=small, clock=lambda: NOW).run()
    assert report.deleted["pw_bucket"] == 3
    assert await count(db_session, PwBucket) == 2


async def test_every_retention_table_obeys_the_per_run_batch_bound(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance)
    starts = [OLD + timedelta(minutes=5 * i) for i in range(7)]
    await db_session.execute(insert(PwBucket), [bucket_row(watch.id, at) for at in starts])
    await db_session.execute(
        insert(PwValue),
        [
            value_row(watch.id, f"retired-{index}", active=False, retired_at=OLD)
            for index in range(7)
        ]
        + [
            value_row(watch.id, f"bare-{index}", active=False, retired_at=OLD)
            for index in range(7)
        ],
    )
    pairs = (await db_session.execute(select(PwValue.id, PwValue.value_key))).all()
    ids = {key: value_id for value_id, key in pairs}
    await db_session.execute(
        insert(PwValueMetric),
        [
            dict(
                pw_value_id=ids[f"retired-{index}"],
                bucket_start=starts[index],
                events=3,
                bucket_seconds=300,
            )
            for index in range(7)
        ],
    )
    await db_session.commit()

    bounded = cfg(pw_retention_batch_size=100, pw_retention_max_batches=1).model_copy(
        update={"pw_retention_batch_size": 3}
    )
    report = await PwRetention(db_session, settings=bounded, clock=lambda: NOW).run()

    assert report.deleted == {"pw_value_metric": 3, "pw_bucket": 3, "pw_value": 3}
    assert report.incomplete == ["pw_value_metric", "pw_bucket", "pw_value"]
    assert await count(db_session, PwValueMetric) == 4
    assert await count(db_session, PwBucket) == 4
    assert await count(db_session, PwValue) == 11


async def test_an_exact_final_batch_is_complete(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance)
    await db_session.execute(
        insert(PwBucket),
        [bucket_row(watch.id, OLD + timedelta(minutes=5 * index)) for index in range(3)],
    )
    await db_session.commit()

    bounded = cfg(pw_retention_batch_size=100, pw_retention_max_batches=1).model_copy(
        update={"pw_retention_batch_size": 3}
    )
    report = await PwRetention(db_session, settings=bounded, clock=lambda: NOW).run()

    assert report.deleted["pw_bucket"] == 3
    assert "pw_bucket" not in report.incomplete


async def test_retention_runs_while_collection_is_off(db_session) -> None:
    await seeded(db_session)
    off = cfg(pw_collection_enabled=False)
    report = await PwRetention(db_session, settings=off, clock=lambda: NOW).run()
    assert report.deleted["pw_bucket"] == 7


# ------------------------------------------------ a value an active incident protects (PW-3c)
async def _value_with_incident(db_session, state: str) -> None:  # type: ignore[no-untyped-def]
    """One retired, metric-less value whose incident is in `state`."""
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance)
    await db_session.execute(
        insert(PwValue), [value_row(watch.id, "gone", active=False, retired_at=OLD)]
    )
    value_id = await db_session.scalar(select(PwValue.id))
    db_session.add(
        PwAnomaly(
            watch_id=watch.id,
            pw_value_id=value_id,
            value_key="gone",
            detector_type="PW_VALUE_SPIKE",
            fingerprint="pw_anomaly:gone",
            severity="HIGH",
            state=state,
            detected_at=OLD,
            watch_version=1,
        )
    )
    await db_session.commit()


@pytest.mark.parametrize("state", [AnomalyState.OPEN.value, AnomalyState.RECOVERING.value])
async def test_retention_keeps_a_value_whose_incident_is_still_active(
    db_session, state: str
) -> None:
    """PW-3c item 2: deleting the value would leave the investigation without its subject."""
    await _value_with_incident(db_session, state)
    report = await PwRetention(db_session, settings=cfg(), clock=lambda: NOW).run()
    assert report.deleted["pw_value"] == 0
    assert report.blocked == {"pw_value": 1}
    assert await count(db_session, PwValue) == 1


async def test_retention_deletes_a_value_whose_incident_has_ended(db_session) -> None:
    await _value_with_incident(db_session, AnomalyState.RESOLVED.value)
    report = await PwRetention(db_session, settings=cfg(), clock=lambda: NOW).run()
    assert report.deleted["pw_value"] == 1
    assert report.blocked == {"pw_value": 0}
    assert await count(db_session, PwValue) == 0


async def test_the_value_delete_protects_exactly_the_active_incident_states() -> None:
    """The raw SQL's `IN` list is pinned to `AnomalyState`, so the two cannot drift."""
    expected = {AnomalyState.OPEN.value, AnomalyState.RECOVERING.value}
    assert set(BLOCKING_INCIDENT_STATES) == expected
    step = next(s for s in pw_retention._STATEMENTS if s.table == "pw_value")
    for state in BLOCKING_INCIDENT_STATES:
        assert f"'{state}'" in step.delete
        assert f"'{state}'" in step.remaining
        assert step.blocked is not None and f"'{state}'" in step.blocked
    for state in (AnomalyState.CANDIDATE.value, AnomalyState.RESOLVED.value):
        assert f"'{state}'" not in step.delete
