"""FIX-2: WAF status is a fixed number of reads, and a missing watermark is unknown."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from app.collectors.waf_endpoint_collector import watermark_collector
from app.core.config import get_settings
from app.models.enums import BucketCompleteness, WafMeasurement, WafProfileValidationStatus
from app.models.history_backfill import HistoryBackfillScope
from app.models.monitoring import CollectionWatermark
from app.models.waf import WafEndpoint, WafSourceBucket, WafSourceProfile
from app.services.waf_pipeline import evaluation_collector, scheduling_record
from app.services.waf_status import waf_collection_health, waf_source_statuses
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

T0 = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
NOW = T0 + timedelta(hours=1)


@contextmanager
def recorded_statements(session):  # type: ignore[no-untyped-def]
    statements: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _profile(instance_id, source_id) -> WafSourceProfile:  # type: ignore[no-untyped-def]
    return WafSourceProfile(
        instance_id=instance_id, log_source_id=source_id, enabled=True, profile_version=2,
        url_property="URL", method_property="Method", product_label="lab-waf",
        validation_status=WafProfileValidationStatus.VALID, validated_profile_version=2,
        validated_at=T0, validation_report={},
    )


def _bucket(source_id, start: datetime, **kw) -> WafSourceBucket:  # type: ignore[no-untyped-def]
    defaults = dict(
        log_source_id=source_id, bucket_start=start, bucket_seconds=300,
        profile_version=2, metric_semantics="sem", completeness=BucketCompleteness.COMPLETE,
        endpoint_coverage_ratio=0.97, total_events=500, parsed_events=485, group_count=3,
        groups_truncated=False, unusable_groups=0, observed_endpoints=3,
        tracked_endpoints_observed=3, admitted_endpoints=0, below_admission_threshold=0,
        rejected_over_quota=0, untracked_endpoints=0, untracked_requests=0,
        admission_truncated=False, zero_filled_endpoints=0,
        request_count_measurement=WafMeasurement.EXACT, sparse_endpoint_rows=True,
        in_maintenance=False, collection_source="TEST", query_provenance={"queries": ["AQL"]},
        origin="LIVE",
    )
    defaults.update(kw)
    return WafSourceBucket(**defaults)


def _endpoint(instance_id, source_id, path: str, **kw) -> WafEndpoint:  # type: ignore[no-untyped-def]
    defaults = dict(
        instance_id=instance_id, log_source_id=source_id, virtual_host=None,
        normalized_endpoint=path, http_method="GET",
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        first_seen_at=T0, last_seen_at=T0, tracked_since=T0, is_active=True,
    )
    defaults.update(kw)
    return WafEndpoint(**defaults)


async def _add_source(session, inst, qradar_id: int):  # type: ignore[no-untyped-def]
    source = await make_log_source(
        session, inst, qradar_id=qradar_id, name=f"waf-{qradar_id}"
    )
    session.add(_profile(inst.id, source.id))
    await session.flush()
    return source


async def test_no_profiles_is_an_empty_status(db_session) -> None:  # type: ignore[no-untyped-def]
    assert await waf_source_statuses(
        db_session, settings=get_settings(), now=NOW
    ) == []


async def test_grouped_status_keeps_pending_and_unknown_failures(db_session) -> None:  # type: ignore[no-untyped-def]
    inst = await make_instance(db_session)
    measured = await _add_source(db_session, inst, 1)
    bare = await _add_source(db_session, inst, 2)
    held = await _add_source(db_session, inst, 3)
    db_session.add_all([
        CollectionWatermark(
            instance_id=inst.id, collector=watermark_collector(measured),
            watermark_at=T0 + timedelta(minutes=10), consecutive_failures=4, last_error="timeout",
        ),
        CollectionWatermark(
            instance_id=inst.id, collector=evaluation_collector(measured), watermark_at=T0,
        ),
        CollectionWatermark(
            instance_id=inst.id, collector=watermark_collector(held),
            watermark_at=None, consecutive_failures=0,
        ),
        _bucket(measured.id, T0),
        _bucket(measured.id, T0 + timedelta(minutes=5)),
        _bucket(
            measured.id, T0 + timedelta(minutes=10),
            request_count_measurement=WafMeasurement.LOWER_BOUND,
        ),
        _bucket(measured.id, T0 + timedelta(minutes=15), admission_truncated=True),
        _bucket(measured.id, NOW - timedelta(days=2), admission_truncated=True),
        _endpoint(inst.id, measured.id, "/a", baseline_rebuilt_at=T0),
        _endpoint(inst.id, measured.id, "/b"),
        _endpoint(inst.id, measured.id, "/c", is_active=False),
        _endpoint(
            inst.id, measured.id, "/d", normalizer_version=ENDPOINT_NORMALIZER_VERSION + 1
        ),
    ])
    await db_session.flush()

    statuses = {
        row.qradar_log_source_id: row
        for row in await waf_source_statuses(db_session, settings=get_settings(), now=NOW)
    }
    assert set(statuses) == {1, 2, 3}

    got = statuses[1]
    assert got.consecutive_failures == 4
    assert got.last_error == "timeout"
    assert got.pending_evaluation_buckets == 2
    assert got.admission_truncated_buckets_24h == 1
    assert got.lower_bound_buckets_24h == 1
    assert got.active_endpoints == 2
    assert got.endpoints_without_baseline_rebuild == 1
    assert got.last_bucket is not None
    assert got.last_bucket.bucket_start == T0 + timedelta(minutes=15)
    assert got.last_bucket.admission_truncated is True

    assert statuses[2].consecutive_failures is None
    assert statuses[2].last_bucket is None
    assert statuses[2].pending_evaluation_buckets == 0
    assert statuses[2].admission_truncated_buckets_24h == 0
    assert statuses[2].lower_bound_buckets_24h == 0
    assert statuses[2].active_endpoints == 0
    assert bare.qradar_id == 2

    assert statuses[3].consecutive_failures == 0
    assert statuses[3].pending_evaluation_buckets == 0
    assert held.qradar_id == 3


async def test_status_statement_count_is_the_same_for_two_and_six_sources(db_session) -> None:  # type: ignore[no-untyped-def]
    inst = await make_instance(db_session)
    settings = get_settings()

    async def more(start: int, count: int) -> None:
        for qradar_id in range(start, start + count):
            source = await _add_source(db_session, inst, qradar_id)
            db_session.add(CollectionWatermark(
                instance_id=inst.id, collector=watermark_collector(source),
                watermark_at=T0, consecutive_failures=0,
            ))
            db_session.add(_bucket(source.id, T0))
        await db_session.flush()

    await more(10, 2)
    with recorded_statements(db_session) as first_sql:
        await waf_source_statuses(db_session, settings=settings, now=NOW)
    await more(12, 4)
    with recorded_statements(db_session) as second_sql:
        await waf_source_statuses(db_session, settings=settings, now=NOW)

    assert len(first_sql) == len(second_sql) == 6, (
        f"2 sources: {len(first_sql)}\n" + "\n".join(first_sql)
        + f"\n6 sources: {len(second_sql)}\n" + "\n".join(second_sql)
    )


# --- FIX-71e: per-source collection health for the behavior list -------------


def _health_settings(**update):  # type: ignore[no-untyped-def]
    return get_settings().model_copy(update={"waf_collection_enabled": True, **update})


async def _health_source(  # type: ignore[no-untyped-def]
    session, inst, qradar_id, *, collection=None, scheduling=None, run_at=None, failures=0,
    profile=None, monitoring=True,
):
    """A source whose records are exactly the given watermark metadata."""
    source = await make_log_source(
        session, inst, qradar_id=qradar_id, name=f"h-{qradar_id}", monitoring_enabled=monitoring
    )
    prof = _profile(inst.id, source.id)
    for key, value in (profile or {}).items():
        setattr(prof, key, value)
    session.add(prof)
    if collection is not None:
        session.add(CollectionWatermark(
            instance_id=inst.id, collector=watermark_collector(source), last_run_at=run_at,
            consecutive_failures=failures, collection_metadata=collection,
            last_error="SELECT secret FROM events -- Bearer abc",
        ))
    if scheduling is not None:
        session.add(CollectionWatermark(
            instance_id=inst.id, collector=scheduling_record(source),
            collection_metadata=scheduling,
        ))
    await session.flush()
    return source, prof


def _iso(minutes_ago: int) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


async def _health_of(session, source, prof, **settings):  # type: ignore[no-untyped-def]
    got = await waf_collection_health(
        session, [(prof, source)], settings=_health_settings(**settings)
    )
    return got[source.id]


async def test_health_no_record_is_not_a_healthy_source(db_session) -> None:  # type: ignore[no-untyped-def]
    inst = await make_instance(db_session)
    source, prof = await _health_source(db_session, inst, 1)
    health = await _health_of(db_session, source, prof)
    assert health.state == "no_run_recorded"
    assert health.last_run_at is None and health.consecutive_failures is None
    assert health.intervals_due is None and health.intervals_collected is None


async def test_health_failure_names_stage_and_class_never_the_error_text(db_session) -> None:  # type: ignore[no-untyped-def]
    inst = await make_instance(db_session)
    source, prof = await _health_source(
        db_session, inst, 1, run_at=NOW, failures=3,
        collection={
            "last_failure_at": _iso(5), "last_error_class": "ProviderUnavailableError",
            "last_failure_stage": "collect", "last_success_at": _iso(60),
        },
    )
    health = await _health_of(db_session, source, prof)
    assert health.state == "failed"
    assert (health.failure_stage, health.failure_class) == ("collect", "ProviderUnavailableError")
    assert health.consecutive_failures == 3
    assert "secret" not in health.model_dump_json() and "Bearer" not in health.model_dump_json()
    # An unknown stage or a class that is not an identifier is dropped, not shown.
    source2, prof2 = await _health_source(
        db_session, inst, 2, run_at=NOW,
        collection={
            "last_failure_at": _iso(5), "last_error_class": "boom: SELECT 1",
            "last_failure_stage": "SELECT 1",
        },
    )
    odd = await _health_of(db_session, source2, prof2)
    assert odd.state == "failed" and odd.failure_stage is None and odd.failure_class is None


async def test_health_a_failure_older_than_a_success_is_not_current(db_session) -> None:  # type: ignore[no-untyped-def]
    inst = await make_instance(db_session)
    source, prof = await _health_source(
        db_session, inst, 1, run_at=NOW,
        collection={
            "last_failure_at": _iso(60), "last_success_at": _iso(5),
            "last_run_intervals": 1, "last_run_intervals_due": 1,
        },
    )
    health = await _health_of(db_session, source, prof)
    assert health.state == "collecting"
    assert health.failure_class is None and health.failure_stage is None


async def test_health_skips_busy_capacity_and_a_later_failure_wins(db_session) -> None:  # type: ignore[no-untyped-def]
    inst = await make_instance(db_session)
    busy, p1 = await _health_source(
        db_session, inst, 1, run_at=NOW, collection={},
        scheduling={"last_skip_reason": "source_locked", "last_skipped_at": _iso(2)},
    )
    cap, p2 = await _health_source(
        db_session, inst, 2, run_at=NOW, collection={},
        scheduling={"last_skip_reason": "global_capacity", "last_skipped_at": _iso(2)},
    )
    both, p3 = await _health_source(
        db_session, inst, 3, run_at=NOW,
        collection={"last_failure_at": _iso(1), "last_error_class": "ValueError"},
        scheduling={"last_skip_reason": "source_locked", "last_skipped_at": _iso(9)},
    )
    assert (await _health_of(db_session, busy, p1)).state == "source_busy"
    capacity = await _health_of(db_session, cap, p2)
    assert capacity.state == "capacity_unavailable"
    assert capacity.skip_reason == "global_capacity" and capacity.skipped_at is not None
    assert (await _health_of(db_session, both, p3)).state == "failed"
    # A skip recorded after the failure is the latest word on the source.
    later, p5 = await _health_source(
        db_session, inst, 5, run_at=NOW,
        collection={"last_failure_at": _iso(9), "last_error_class": "ValueError"},
        scheduling={"last_skip_reason": "source_locked", "last_skipped_at": _iso(1)},
    )
    assert (await _health_of(db_session, later, p5)).state == "source_busy"
    # A skip reason this build does not know is not shown as a state.
    odd, p4 = await _health_source(
        db_session, inst, 4, run_at=NOW, collection={},
        scheduling={"last_skip_reason": "queue lost", "last_skipped_at": _iso(2)},
    )
    assert (await _health_of(db_session, odd, p4)).skip_reason is None


async def test_health_not_eligible_reasons(db_session) -> None:  # type: ignore[no-untyped-def]
    inst = await make_instance(db_session)
    disabled_global, p1 = await _health_source(db_session, inst, 1)
    mon, p2 = await _health_source(db_session, inst, 2, monitoring=False)
    stale, p3 = await _health_source(
        db_session, inst, 3, profile={"profile_version": 3}
    )
    off, p4 = await _health_source(db_session, inst, 4, profile={"enabled": False})
    inconclusive, p5 = await _health_source(
        db_session, inst, 5,
        profile={"validation_status": WafProfileValidationStatus.INCONCLUSIVE},
    )
    assert (await _health_of(db_session, disabled_global, p1, waf_collection_enabled=False)
            ).ineligible_code == "collection_disabled"
    assert (await _health_of(db_session, mon, p2)).ineligible_code == "monitoring_disabled"
    assert (await _health_of(db_session, stale, p3)).ineligible_code == "validation_stale"
    assert (await _health_of(db_session, off, p4)).ineligible_code == "profile_disabled"
    assert (
        await _health_of(db_session, inconclusive, p5)
    ).ineligible_code == "validation_not_valid"
    for src, prof in ((mon, p2), (stale, p3), (off, p4)):
        assert (await _health_of(db_session, src, prof)).state == "not_eligible"
    # Ineligible outranks recorded history: an old success does not make it collecting.
    old, p6 = await _health_source(
        db_session, inst, 6, run_at=NOW, monitoring=False,
        collection={"last_success_at": _iso(5), "last_run_intervals": 1,
                    "last_run_intervals_due": 1},
    )
    assert (await _health_of(db_session, old, p6)).state == "not_eligible"


async def test_health_ran_without_storing_a_bucket_and_other_run_states(db_session) -> None:  # type: ignore[no-untyped-def]
    inst = await make_instance(db_session)
    none_stored, p1 = await _health_source(
        db_session, inst, 1, run_at=NOW,
        collection={"last_run_intervals": 0, "last_run_intervals_due": 3},
    )
    idle, p2 = await _health_source(
        db_session, inst, 2, run_at=NOW,
        collection={"last_run_intervals": 0, "last_run_intervals_due": 0,
                    "last_success_at": _iso(1)},
    )
    budget, p3 = await _health_source(
        db_session, inst, 3, run_at=NOW,
        collection={"last_run_intervals": 0, "last_run_intervals_due": 3,
                    "last_run_partial_reason": "search_cap_reached"},
    )
    ok, p4 = await _health_source(
        db_session, inst, 4, run_at=NOW,
        collection={"last_run_intervals": 2, "last_run_intervals_due": 2,
                    "last_success_at": _iso(1)},
    )
    got = {
        "none": await _health_of(db_session, none_stored, p1),
        "idle": await _health_of(db_session, idle, p2),
        "budget": await _health_of(db_session, budget, p3),
        "ok": await _health_of(db_session, ok, p4),
    }
    assert {k: v.state for k, v in got.items()} == {
        "none": "no_progress", "idle": "no_interval_due",
        "budget": "budget_exhausted", "ok": "collecting",
    }
    assert (got["none"].intervals_due, got["none"].intervals_collected) == (3, 0)


async def test_health_history_reading_is_its_own_state(db_session) -> None:  # type: ignore[no-untyped-def]
    inst = await make_instance(db_session)
    reading, p1 = await _health_source(db_session, inst, 1, run_at=NOW, collection={})
    other, p2 = await _health_source(db_session, inst, 2, run_at=NOW, collection={})
    for qradar_id, state in ((1, "READING"), (2, "COMPLETE")):
        db_session.add(HistoryBackfillScope(
            instance_id=inst.id, family="WAF", scope_key=str(qradar_id), state=state,
            progress={},
        ))
    await db_session.flush()
    assert (await _health_of(db_session, reading, p1)).state == "history_reading"
    assert (await _health_of(db_session, other, p2)).state != "history_reading"


async def test_health_statement_count_is_two_for_two_and_for_six_sources(db_session) -> None:  # type: ignore[no-untyped-def]
    inst = await make_instance(db_session)
    settings = _health_settings()
    pairs = []
    for qradar_id in range(1, 3):
        pairs.append(await _health_source(db_session, inst, qradar_id, run_at=NOW, collection={}))
    with recorded_statements(db_session) as first:
        await waf_collection_health(
            db_session, [(p, s) for s, p in pairs], settings=settings
        )
    for qradar_id in range(3, 7):
        pairs.append(await _health_source(db_session, inst, qradar_id, run_at=NOW, collection={}))
    with recorded_statements(db_session) as second:
        await waf_collection_health(
            db_session, [(p, s) for s, p in pairs], settings=settings
        )
    assert len(first) == len(second) == 2, "\n".join(first + second)
    with recorded_statements(db_session) as empty:
        assert await waf_collection_health(db_session, [], settings=settings) == {}
    assert empty == []


async def test_behavior_list_rows_cost_the_same_for_two_and_six_sources(db_session) -> None:  # type: ignore[no-untyped-def]
    """The behavior list reads health in bulk: no statement per source (AGENTS 2.9)."""
    from app.api.routes.anomalies import _waf_source_rows
    from app.repositories.anomaly import AnomalyRepository

    inst = await make_instance(db_session)
    settings = _health_settings()
    repo = AnomalyRepository(db_session)
    pairs: list = []

    async def rows():  # type: ignore[no-untyped-def]
        with recorded_statements(db_session) as sql:
            out = await _waf_source_rows(
                repo, [(p, s) for s, p in pairs], settings, T0, NOW
            )
        return out, sql

    for qradar_id in range(1, 3):
        pairs.append(await _health_source(db_session, inst, qradar_id, run_at=NOW, collection={}))
    first_rows, first = await rows()
    for qradar_id in range(3, 7):
        pairs.append(await _health_source(db_session, inst, qradar_id, run_at=NOW, collection={}))
    second_rows, second = await rows()
    assert len(first_rows) == 2 and len(second_rows) == 6
    assert len(first) == len(second), "\n".join(first + second)
    assert all(r.collection.state in {"collecting", "no_interval_due", "no_progress"}
               or r.collection.state == "no_run_recorded" for r in second_rows)
