"""FIX-2: WAF status is a fixed number of reads, and a missing watermark is unknown."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from app.collectors.waf_endpoint_collector import watermark_collector
from app.core.config import get_settings
from app.models.enums import BucketCompleteness, WafMeasurement, WafProfileValidationStatus
from app.models.monitoring import CollectionWatermark
from app.models.waf import WafEndpoint, WafSourceBucket, WafSourceProfile
from app.services.waf_pipeline import evaluation_collector
from app.services.waf_status import waf_source_statuses
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
