"""Phase G collection end to end through the mock provider (PW-2b).

The mock runs the real query builder, the real row parser, the real split driver and the
real fold; only the events are scripted. The tests pin the watermark rules, the honesty
states, admission and privacy of what reaches the database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app.collectors.pw_collector import PwCollector
from app.core.config import Settings, get_settings
from app.models.enums import (
    PwBucketMeasurement,
    PwPinnedMeasurement,
    PwScopeKind,
    PwWatchStatus,
)
from app.models.fleet import LogSourceGroup
from app.models.property_watch import PropertyWatch, PwBucket, PwValue, PwValueMetric
from app.providers.base import ProviderError
from app.providers.dto import PwSearchKind
from app.providers.mock import MockPwSearch, MockQRadarProvider
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

#: 10:07 with a 120 s settle: the latest closed bucket starts at 10:00.
NOW = datetime(2026, 10, 13, 10, 7, tzinfo=UTC)
STEP = timedelta(minutes=5)
USERNAME = [{"kind": "NORMALIZED", "name": "username"}]
USER_ACTION = [
    {"kind": "NORMALIZED", "name": "username"},
    {"kind": "CUSTOM_PROPERTY", "name": "Action"},
]


def cfg(**overrides: object) -> Settings:
    values = get_settings().model_dump()
    values.update(
        pw_collection_enabled=True,
        collection_interval_seconds=300,
        collection_max_backfill_intervals=0,
        search_cadence_settle_seconds=120,
        pw_max_searches_per_tick=40,
        pw_max_searches_per_watch_per_tick=12,
        pw_min_admission_events=3,
        pw_max_new_values_per_bucket=20,
        pw_max_active_values_per_watch=200,
    )
    values.update(overrides)
    return Settings(**values)


def scripted(events: dict[str, int], *, unparsed: int = 0):  # type: ignore[no-untyped-def]
    """A mock script: the same events in every bucket, one value of the first property each."""

    def script(key: int, names: tuple[str, ...]) -> list[dict]:
        out = [
            {"values": {name: value if i == 0 else "x" for i, name in enumerate(names)},
             "events": count}
            for value, count in events.items()
        ]
        if unparsed:
            out.append({"values": dict.fromkeys(names), "events": unparsed})
        return out

    return script


def provider_with(events: dict[str, int], **kw: int) -> MockQRadarProvider:
    provider = MockQRadarProvider(seed=1)
    provider.pw_script = scripted(events, **kw)
    return provider


async def add_watch(  # type: ignore[no-untyped-def]
    session,
    instance,
    *,
    source=None,
    scope_kind: PwScopeKind = PwScopeKind.LOG_SOURCE,
    properties=USERNAME,
    pinned=(),
    version: int = 1,
    through: datetime | None = None,
    **kw,
) -> PropertyWatch:
    if scope_kind is PwScopeKind.LOG_SOURCE and source is None:
        source = await make_log_source(
            session, instance, qradar_id=1000 + int(uuid.uuid4().int % 50000)
        )
    watch = PropertyWatch(
        instance_id=instance.id,
        name=f"w-{uuid.uuid4().hex[:10]}",
        scope_kind=scope_kind.value,
        log_source_id=source.id if scope_kind is PwScopeKind.LOG_SOURCE else None,
        scope_type_id=kw.pop("scope_type_id", None),
        scope_group_id=kw.pop("scope_group_id", None),
        properties=list(properties),
        pinned_values=[list(p) for p in pinned],
        detectors=[],
        watch_version=version,
        collected_through=through,
        **{"enabled": True, **kw},
    )
    session.add(watch)
    await session.flush()
    return watch


async def collect(  # type: ignore[no-untyped-def]
    session, instance, provider, *, now: datetime = NOW, settings: Settings | None = None
):
    report = await PwCollector(
        session, provider, settings=settings or cfg(), clock=lambda: now
    ).collect(instance)
    await session.commit()
    return report


async def buckets_of(session, watch) -> list[PwBucket]:  # type: ignore[no-untyped-def]
    return list(
        (
            await session.scalars(
                select(PwBucket)
                .where(PwBucket.watch_id == watch.id)
                .order_by(PwBucket.bucket_start)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def reload(session, watch) -> PropertyWatch:  # type: ignore[no-untyped-def]
    await session.refresh(watch)
    return watch


# ----------------------------------------------------------------------- watermark
async def test_the_first_run_seeds_the_watermark_at_the_latest_closed_bucket(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance)
    report = await collect(db_session, instance, provider_with({"alice": 10}))
    await reload(db_session, watch)
    rows = await buckets_of(db_session, watch)
    assert [b.bucket_start for b in rows] == [datetime(2026, 10, 13, 10, 0, tzinfo=UTC)]
    assert watch.collected_through == datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
    assert report.buckets_written == 1 and report.watermarks_advanced == 1
    assert rows[0].values_measurement == PwBucketMeasurement.EXACT.value
    assert rows[0].total_events == 10 and rows[0].parsed_events == 10


async def test_the_first_run_replays_no_history(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance)
    provider = provider_with({"alice": 10})
    await collect(db_session, instance, provider)
    starts = {s.window_start for s in provider.pw_searches}
    assert starts == {datetime(2026, 10, 13, 10, 0, tzinfo=UTC)}
    assert all(
        s.window_end == datetime(2026, 10, 13, 10, 5, tzinfo=UTC) for s in provider.pw_searches
    )
    assert len(await buckets_of(db_session, watch)) == 1


async def test_the_next_run_continues_from_the_watermark_without_overlap(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance)
    provider = provider_with({"alice": 10})
    await collect(db_session, instance, provider)
    later = NOW + timedelta(hours=1)
    await collect(db_session, instance, provider, now=later)
    await reload(db_session, watch)
    rows = await buckets_of(db_session, watch)
    starts = [b.bucket_start for b in rows]
    assert starts == [datetime(2026, 10, 13, 10, 0, tzinfo=UTC) + STEP * i for i in range(13)]
    assert watch.collected_through == starts[-1] == datetime(2026, 10, 13, 11, 0, tzinfo=UTC)
    # a third run within the same bucket has nothing to collect and searches nothing
    provider.pw_searches.clear()
    report = await collect(db_session, instance, provider, now=later + timedelta(seconds=30))
    assert provider.pw_searches == [] and report.watches_due == 0


# ----------------------------------------------------------------------- honesty
async def test_a_failed_search_holds_the_watermark_and_stores_not_collected(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, through=NOW - timedelta(hours=1, minutes=7))
    provider = provider_with({"alice": 10})

    def fail_totals(search: MockPwSearch) -> Exception | None:
        return ProviderError("boom") if search.search is PwSearchKind.TOTALS else None

    provider.pw_fault = fail_totals
    report = await collect(db_session, instance, provider)
    await reload(db_session, watch)
    rows = await buckets_of(db_session, watch)
    assert watch.collected_through == NOW - timedelta(hours=1, minutes=7)
    assert report.watermarks_advanced == 0 and report.buckets_written == 0
    assert len(rows) == 1
    gap = rows[0]
    assert gap.values_measurement == PwBucketMeasurement.NOT_COLLECTED.value
    assert gap.total_events is None and gap.parsed_events is None
    assert gap.untracked_events is None and gap.unparsed_events is None
    assert gap.totals_complete is False
    assert (
        await db_session.scalar(select(func.count()).select_from(PwValueMetric)) == 0
    )
    # the next healthy tick measures the same bucket and replaces the gap
    provider.pw_fault = None
    await collect(db_session, instance, provider)
    await reload(db_session, watch)
    rows = await buckets_of(db_session, watch)
    assert rows[0].bucket_start == gap.bucket_start
    assert rows[0].values_measurement == PwBucketMeasurement.EXACT.value
    assert watch.collected_through > NOW - timedelta(hours=1, minutes=7)


async def test_the_watermark_advances_only_past_the_buckets_that_completed(db_session) -> None:
    instance = await make_instance(db_session)
    start = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
    watch = await add_watch(db_session, instance, through=start - STEP)
    provider = provider_with({"alice": 10})
    cut = start + STEP * 6

    def fail_late_grouped(search: MockPwSearch) -> Exception | None:
        late = search.window_start >= cut
        return ProviderError("late") if late and search.search is PwSearchKind.GROUPED else None

    # every multi-bucket grouped search reads as truncated, so the driver splits by time
    provider.pw_fault = fail_late_grouped
    provider.pw_force_truncated = (
        lambda s: s.search is PwSearchKind.GROUPED and s.window_end - s.window_start > STEP
    )
    settings = cfg(pw_max_searches_per_tick=200, pw_max_searches_per_watch_per_tick=200)
    await collect(db_session, instance, provider, now=start + timedelta(hours=1, minutes=7),
                  settings=settings)
    await reload(db_session, watch)
    rows = await buckets_of(db_session, watch)
    good = [b for b in rows if b.values_measurement != PwBucketMeasurement.NOT_COLLECTED.value]
    assert [b.bucket_start for b in good] == [start + STEP * i for i in range(6)]
    assert watch.collected_through == start + STEP * 5
    gaps = [b for b in rows if b.values_measurement == PwBucketMeasurement.NOT_COLLECTED.value]
    assert [b.bucket_start for b in gaps] == [cut]


async def test_a_spent_budget_leaves_the_bucket_for_the_next_tick_and_no_zero(db_session) -> None:
    instance = await make_instance(db_session)
    first = await add_watch(db_session, instance)
    second = await add_watch(db_session, instance)
    provider = provider_with({"alice": 10})
    tight = cfg(pw_max_searches_per_tick=3, pw_max_searches_per_watch_per_tick=3)
    report = await collect(db_session, instance, provider, settings=tight)
    assert report.searches_sent == 3
    await reload(db_session, first)
    await reload(db_session, second)
    # the watches are ordered by creation then id, so which one ran first is not the point
    done = [w for w in (first, second) if w.collected_through is not None]
    starved = [w for w in (first, second) if w.collected_through is None]
    assert len(done) == 1 and len(starved) == 1
    # the starved watch's totals were never searched, so nothing is stored for it
    assert await buckets_of(db_session, starved[0]) == []
    assert (await db_session.scalar(select(func.count()).select_from(PwValue))) == 1
    await collect(db_session, instance, provider)
    await reload(db_session, starved[0])
    assert starved[0].collected_through == datetime(2026, 10, 13, 10, 0, tzinfo=UTC)


async def test_nothing_is_collected_while_the_family_is_off(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance)
    provider = provider_with({"alice": 10})
    report = await collect(
        db_session, instance, provider, settings=cfg(pw_collection_enabled=False)
    )
    assert report.status == "disabled" and provider.pw_searches == []
    assert await buckets_of(db_session, watch) == []


async def test_a_disabled_watch_is_not_collected(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, enabled=False)
    provider = provider_with({"alice": 10})
    await collect(db_session, instance, provider)
    assert provider.pw_searches == [] and await buckets_of(db_session, watch) == []


async def test_a_silent_scope_stores_a_complete_bucket_of_zero_events(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance)
    provider = MockQRadarProvider(seed=1)
    provider.pw_script = lambda key, names: []
    await collect(db_session, instance, provider)
    [bucket] = await buckets_of(db_session, watch)
    assert bucket.values_measurement == PwBucketMeasurement.EXACT.value
    assert bucket.total_events == 0 and bucket.parsed_events == 0
    assert bucket.totals_complete is True


# ---------------------------------------------------------------------- admission
async def test_admission_stores_only_admitted_values_and_counts_the_rest(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance)
    await collect(db_session, instance, provider_with({"alice": 10, "bob": 2, "carol": 1},
                                                      unparsed=5))
    [bucket] = await buckets_of(db_session, watch)
    keys = set(await db_session.scalars(select(PwValue.value_key)))
    assert keys == {"alice"}
    metrics = (await db_session.execute(select(PwValueMetric.events))).scalars().all()
    assert list(metrics) == [10]
    assert bucket.observed_values == 3 and bucket.admitted_values == 1
    assert bucket.below_admission_threshold == 2 and bucket.rejected_over_quota == 0
    assert bucket.admission_truncated is False
    assert bucket.total_events == 18 and bucket.parsed_events == 13
    assert bucket.unparsed_events == 5
    # the two refused values are summed into untracked_events, their text gone
    assert bucket.untracked_events == 3


async def test_a_value_that_never_reached_admission_is_in_no_table(db_session) -> None:
    instance = await make_instance(db_session)
    await add_watch(db_session, instance)
    secret = "hunter2-typed-into-the-username-box"
    await collect(db_session, instance, provider_with({"alice": 10, secret: 1}))
    tables = (
        await db_session.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        )
    ).scalars().all()
    assert "pw_bucket" in tables
    for table in tables:
        dump = (
            await db_session.execute(text(f'SELECT row_to_json(t)::text FROM "{table}" t'))  # noqa: S608
        ).scalars().all()
        assert not any(secret in row for row in dump), table
    # control: the admitted value is stored, so the scan can see value text
    assert "alice" in set(await db_session.scalars(select(PwValue.value_key)))


async def test_the_new_value_quota_and_the_ceiling_bound_admission(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance)
    events = {f"user{i:02d}": 100 - i for i in range(30)}
    await collect(
        db_session, instance, provider_with(events), settings=cfg(pw_max_new_values_per_bucket=5)
    )
    [bucket] = await buckets_of(db_session, watch)
    keys = sorted(await db_session.scalars(select(PwValue.value_key)))
    assert keys == [f"user{i:02d}" for i in range(5)]  # the busiest five
    assert bucket.admitted_values == 5 and bucket.rejected_over_quota == 25
    assert bucket.admission_truncated is True
    # the ceiling: 2 slots left of 3
    instance2 = await make_instance(db_session)
    watch2 = await add_watch(db_session, instance2)
    await collect(
        db_session, instance2, provider_with(events),
        settings=cfg(pw_max_active_values_per_watch=3, pw_max_pinned_values=3),
    )
    [b2] = await buckets_of(db_session, watch2)
    assert b2.admitted_values == 3 and b2.rejected_over_quota == 27
    assert b2.admission_truncated is True


async def test_the_fold_cut_marks_admission_truncated(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance)
    events = {f"user{i:02d}": 100 - i for i in range(12)}
    await collect(
        db_session, instance, provider_with(events), settings=cfg(pw_max_values_per_bucket=10)
    )
    [bucket] = await buckets_of(db_session, watch)
    assert bucket.observed_values == 12
    assert bucket.admitted_values == 10 and bucket.admission_truncated is True
    # the two cut values are not rejected by quota; they never left the fold
    assert bucket.rejected_over_quota == 0
    assert bucket.untracked_events == (100 - 10) + (100 - 11)


async def test_pinned_values_are_always_tracked_and_prove_their_zero(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, pinned=[["carol"], ["dave"]])
    await collect(db_session, instance, provider_with({"alice": 10, "carol": 1}))
    [bucket] = await buckets_of(db_session, watch)
    rows = {
        v.value_key: v for v in await db_session.scalars(select(PwValue))
    }
    assert set(rows) == {"alice", "carol", "dave"}
    assert rows["carol"].pinned and rows["dave"].pinned and not rows["alice"].pinned
    assert rows["carol"].tracked_since == bucket.bucket_start == rows["dave"].tracked_since
    metrics = dict(
        (
            await db_session.execute(select(PwValueMetric.pw_value_id, PwValueMetric.events))
        ).all()
    )
    assert metrics[rows["carol"].id] == 1  # below the threshold, still stored: it is pinned
    assert rows["dave"].id not in metrics  # absent from a whole pinned search: a measured zero
    assert bucket.pinned_measurement == PwPinnedMeasurement.EXACT.value
    assert bucket.query_provenance.get("pinned")


async def test_a_value_unobserved_for_the_active_days_is_retired_and_returns_new(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, pinned=[["pin"]])
    seen = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    stale = PwValue(
        watch_id=watch.id, watch_version=1, value_key="old", component_0="old",
        pinned=False, active=True, first_seen_at=seen - timedelta(days=30),
        last_seen_at=seen, tracked_since=seen - timedelta(days=30),
    )
    keep = PwValue(
        watch_id=watch.id, watch_version=1, value_key="pin", component_0="pin",
        pinned=True, active=True, first_seen_at=seen - timedelta(days=30),
        last_seen_at=None, tracked_since=seen - timedelta(days=30),
    )
    db_session.add_all([stale, keep])
    await db_session.flush()
    # NOW is 12 days after `seen`, the active window is 7
    await collect(db_session, instance, provider_with({"alice": 10}))
    await db_session.refresh(stale)
    await db_session.refresh(keep)
    assert stale.active is False
    assert stale.tracked_until == datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
    assert stale.retired_at == stale.tracked_until
    assert keep.active is True and keep.tracked_until is None
    # it returns later: a new tracking period, the first sighting kept
    later = NOW + timedelta(hours=1)
    await collect(db_session, instance, provider_with({"old": 9}), now=later)
    await db_session.refresh(stale)
    assert stale.active is True and stale.retired_at is None and stale.tracked_until is None
    assert stale.first_seen_at == seen - timedelta(days=30)
    assert stale.tracked_since == datetime(2026, 10, 13, 10, 5, tzinfo=UTC)


async def test_values_of_another_watch_version_are_retired(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, version=2)
    old = PwValue(
        watch_id=watch.id, watch_version=1, value_key="alice", component_0="alice",
        active=True, first_seen_at=NOW, last_seen_at=NOW, tracked_since=NOW,
    )
    db_session.add(old)
    await db_session.flush()
    await collect(db_session, instance, provider_with({"alice": 10}))
    rows = list(
        (await db_session.scalars(select(PwValue).order_by(PwValue.watch_version))).all()
    )
    await db_session.refresh(rows[0])
    assert [(r.watch_version, r.active) for r in rows] == [(1, False), (2, True)]
    assert rows[0].tracked_until is not None


async def test_a_combination_watch_stores_the_tuple_and_its_components(db_session) -> None:
    instance = await make_instance(db_session)
    await add_watch(db_session, instance, properties=USER_ACTION)
    await collect(db_session, instance, provider_with({"alice": 10}))
    [value] = list(await db_session.scalars(select(PwValue)))
    assert value.value_key == "alice\x1fx"
    assert (value.component_0, value.component_1) == ("alice", "x")


# ------------------------------------------------------------------------- scope
async def _group(session, instance, qradar_id, *, parent=None, synced=NOW):  # type: ignore[no-untyped-def]
    session.add(
        LogSourceGroup(
            instance_id=instance.id, qradar_id=qradar_id, name=f"g{qradar_id}",
            parent_qradar_id=parent, assignable=True, synced_at=synced,
        )
    )
    await session.flush()


async def test_a_group_resolves_through_its_descendants_and_searches_by_chunk(db_session) -> None:
    instance = await make_instance(db_session)
    await _group(db_session, instance, 1)
    await _group(db_session, instance, 2, parent=1)
    a = await make_log_source(db_session, instance, qradar_id=11, group_ids=[1])
    b = await make_log_source(db_session, instance, qradar_id=12, group_ids=[2])
    c = await make_log_source(db_session, instance, qradar_id=13, group_ids=[2])
    await make_log_source(db_session, instance, qradar_id=14, group_ids=[99])
    assert a and b and c
    watch = await add_watch(
        db_session, instance, scope_kind=PwScopeKind.LOG_SOURCE_GROUP, scope_group_id=1
    )
    provider = provider_with({"alice": 10})
    await collect(db_session, instance, provider, settings=cfg(pw_max_sources_per_search=2))
    grouped = [s.scope_ids for s in provider.pw_searches if s.search is PwSearchKind.GROUPED]
    assert grouped == [(11, 12), (13,)]
    [bucket] = await buckets_of(db_session, watch)
    assert bucket.scope_member_count == 3 and bucket.scope_fingerprint is not None
    assert bucket.scope_changed is False
    assert bucket.query_provenance["grouped"]["chunks"] == 2


async def test_a_membership_change_flags_the_first_bucket_and_the_grace_after_it(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    await _group(db_session, instance, 1)
    await make_log_source(db_session, instance, qradar_id=11, group_ids=[1])
    watch = await add_watch(
        db_session, instance, scope_kind=PwScopeKind.LOG_SOURCE_GROUP, scope_group_id=1
    )
    provider = provider_with({"alice": 10})
    settings = cfg(pw_scope_change_grace_buckets=2)
    await collect(db_session, instance, provider, settings=settings)
    await make_log_source(db_session, instance, qradar_id=12, group_ids=[1])
    await collect(db_session, instance, provider, now=NOW + timedelta(minutes=30),
                  settings=settings)
    rows = await buckets_of(db_session, watch)
    flags = [b.scope_changed for b in rows]
    # 10:00 original; 10:05.. 10:30 new membership: the first and two after it are flagged
    assert flags == [False, True, True, True, False, False, False]
    assert {b.scope_fingerprint for b in rows[1:]} != {rows[0].scope_fingerprint}


async def test_a_stale_group_inventory_writes_no_bucket_and_says_why(db_session) -> None:
    instance = await make_instance(db_session)
    await _group(db_session, instance, 1, synced=NOW - timedelta(hours=30))
    await make_log_source(db_session, instance, qradar_id=11, group_ids=[1])
    watch = await add_watch(
        db_session, instance, scope_kind=PwScopeKind.LOG_SOURCE_GROUP, scope_group_id=1
    )
    provider = provider_with({"alice": 10})
    await collect(db_session, instance, provider)
    await reload(db_session, watch)
    assert provider.pw_searches == [] and await buckets_of(db_session, watch) == []
    assert watch.last_status == PwWatchStatus.SCOPE_STALE.value
    assert watch.last_status_reason and "synced" in watch.last_status_reason
    assert watch.collected_through is None
    # the sync recovers: the status clears and collection starts
    await db_session.execute(
        text("UPDATE log_source_group SET synced_at = :t"), {"t": NOW - timedelta(minutes=5)}
    )
    await collect(db_session, instance, provider)
    await reload(db_session, watch)
    assert watch.last_status is None and watch.collected_through is not None


async def test_an_empty_or_too_large_scope_is_a_reason_not_a_zero(db_session) -> None:
    instance = await make_instance(db_session)
    await _group(db_session, instance, 1)
    await _group(db_session, instance, 2)
    for qid in (11, 12, 13):
        await make_log_source(db_session, instance, qradar_id=qid, group_ids=[2])
    empty = await add_watch(
        db_session, instance, scope_kind=PwScopeKind.LOG_SOURCE_GROUP, scope_group_id=1
    )
    big = await add_watch(
        db_session, instance, scope_kind=PwScopeKind.LOG_SOURCE_GROUP, scope_group_id=2
    )
    provider = provider_with({"alice": 10})
    await collect(
        db_session, instance, provider,
        settings=cfg(pw_max_sources_per_search=1, pw_max_chunks_per_watch=2),
    )
    await reload(db_session, empty)
    await reload(db_session, big)
    assert empty.last_status == PwWatchStatus.SCOPE_EMPTY.value
    assert big.last_status == PwWatchStatus.SCOPE_TOO_LARGE.value
    assert provider.pw_searches == []
    assert await db_session.scalar(select(func.count()).select_from(PwBucket)) == 0


async def test_a_type_scope_counts_the_synced_sources_and_has_no_fingerprint(db_session) -> None:
    instance = await make_instance(db_session)
    await make_log_source(db_session, instance, qradar_id=21, type_id=7)
    await make_log_source(db_session, instance, qradar_id=22, type_id=7)
    watch = await add_watch(
        db_session, instance, scope_kind=PwScopeKind.LOG_SOURCE_TYPE, scope_type_id=7
    )
    provider = provider_with({"alice": 10})
    await collect(db_session, instance, provider)
    [bucket] = await buckets_of(db_session, watch)
    assert bucket.scope_member_count == 2 and bucket.scope_fingerprint is None
    assert {s.scope_ids for s in provider.pw_searches} == {(7,)}


async def test_a_source_in_maintenance_marks_its_buckets(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(
        db_session, instance, qradar_id=31, maintenance_mode=True
    )
    watch = await add_watch(db_session, instance, source=source)
    await collect(db_session, instance, provider_with({"alice": 10}))
    [bucket] = await buckets_of(db_session, watch)
    assert bucket.in_maintenance is True


async def test_a_group_is_in_maintenance_only_when_every_resolved_member_is(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    db_session.add(
        LogSourceGroup(
            instance_id=instance.id,
            qradar_id=7,
            name="maintained group",
            parent_qradar_id=None,
            assignable=True,
            synced_at=NOW,
        )
    )
    first = await make_log_source(db_session, instance, qradar_id=701, group_ids=[7])
    second = await make_log_source(db_session, instance, qradar_id=702, group_ids=[7])
    first.maintenance_mode = True
    second.maintenance_mode = False
    watch = await add_watch(
        db_session,
        instance,
        scope_kind=PwScopeKind.LOG_SOURCE_GROUP,
        scope_group_id=7,
    )
    await collect(db_session, instance, provider_with({"alice": 10}))
    [bucket] = await buckets_of(db_session, watch)
    assert bucket.in_maintenance is False

    second.maintenance_mode = True
    await collect(db_session, instance, provider_with({"alice": 10}), now=NOW + STEP)
    buckets = await buckets_of(db_session, watch)
    assert [bucket.in_maintenance for bucket in buckets] == [False, True]


async def test_a_property_that_parsed_on_no_event_is_not_applicable_after_a_cadence_window(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    start = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
    watch = await add_watch(db_session, instance, through=start - STEP)
    provider = MockQRadarProvider(seed=1)
    provider.pw_script = lambda key, names: [
        {"values": dict.fromkeys(names), "events": 40}
    ]
    await collect(db_session, instance, provider, now=start + timedelta(hours=1, minutes=7))
    await reload(db_session, watch)
    assert watch.last_status == PwWatchStatus.NOT_APPLICABLE.value
    # values are back: the status clears
    await collect(
        db_session, instance, provider_with({"alice": 10}),
        now=start + timedelta(hours=2, minutes=7),
    )
    await reload(db_session, watch)
    assert watch.last_status is None


async def test_a_corrupt_stored_watch_is_skipped_without_stopping_the_others(db_session) -> None:
    instance = await make_instance(db_session)
    bad = await add_watch(
        db_session, instance, properties=[{"kind": "CUSTOM_PROPERTY", "name": "bad'name"}]
    )
    good = await add_watch(db_session, instance)
    report = await collect(db_session, instance, provider_with({"alice": 10}))
    await reload(db_session, bad)
    await reload(db_session, good)
    assert [w.status for w in report.watches if w.watch_id == bad.id] == ["failed"]
    assert bad.collected_through is None and good.collected_through is not None
    assert await buckets_of(db_session, bad) == []
