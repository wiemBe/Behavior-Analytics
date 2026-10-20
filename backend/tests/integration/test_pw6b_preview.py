"""PW-6b: the F.1 preview for a PW_VALUE rule (design §G.10.2, §F.7).

The replay is compared with the engine on the same rows: the same breach opens where the
engine opens it. A missing bucket is NOT_MEASURED, a measured zero follows PW-6's rule only,
a LOWER_BOUND bucket stays a lower bound, and the whole preview is one metric statement.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.custom_rules.evaluate import latest_closed_bucket
from app.custom_rules.preview import replay
from app.models.custom_rule import CustomRuleBreach
from app.models.enums import CustomRuleMetric, CustomRuleSubjectKind, PwBucketOrigin
from app.models.property_watch import PropertyWatch, PwValueMetric
from tests.integration.factories import make_instance, make_log_source, utc
from tests.integration.test_custom_rules_pw_value import (
    PREFIX,
    _api_world,
    _arm,
    _breaches,
    _bucket,
    _rule,
    _run,
    _value,
    _watch,
)
from tests.integration.test_waf_scale import counting

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run PW-6b integration tests",
    ),
]

NOW = utc(h=10, mi=22)
FLOOR = 0.9


async def _replay(session, value, *, floor=FLOOR, **kw):  # type: ignore[no-untyped-def]
    args = {
        "metric": CustomRuleMetric.PW_VALUE_EVENTS,
        "operator": "GT",
        "threshold_value": 100.0,
        "sustain_buckets": 2,
        "recovery_buckets": 1,
        "severity": "HIGH",
        "subject_kind": CustomRuleSubjectKind.PW_VALUE,
        "log_source_id": None,
        "source_class": None,
        "waf_endpoint_id": None,
        "ips_entity_id": None,
        "pw_value_id": value.id,
        "pw_coverage_floor": floor,
        "hours": 1,
        "interval_seconds": 300,
        "now": NOW,
    }
    args.update(kw)
    return await replay(session, **args)


def _metric(value, minute: int, events: int) -> PwValueMetric:  # type: ignore[no-untyped-def]
    return PwValueMetric(
        pw_value_id=value.id, bucket_start=utc(h=10, mi=minute), events=events, bucket_seconds=300
    )


async def test_the_preview_shows_the_breach_the_engine_opens_on_the_same_rows(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1, name="fw")
    watch = await _watch(db_session, instance, source)
    value = await _value(db_session, watch)
    db_session.add_all([_bucket(watch, 5), _bucket(watch, 10), _bucket(watch, 15)])
    db_session.add_all([_metric(value, 5, 150), _metric(value, 10, 300)])  # 10:15 is a zero
    rule = _rule("deny-flood", value, sustain_buckets=2, recovery_buckets=1)
    db_session.add(rule)
    await _arm(db_session)
    await db_session.flush()

    with counting(db_session) as statements:
        result = await _replay(db_session, value)
    assert len(statements) == 1 and "pw_value_metric" in statements[0]

    by_minute = {b.bucket_start: b for b in result.buckets}
    assert [by_minute[utc(h=10, mi=m)].measurement for m in (5, 10, 15)] == ["EXACT"] * 3
    assert [by_minute[utc(h=10, mi=m)].value for m in (5, 10, 15)] == [150.0, 300.0, 0.0]
    assert by_minute[utc(h=10, mi=10)].completeness == "EXACT"
    assert result.unit == "events/bucket"
    # every other bucket of the window has no row: unmeasured, never a zero
    outside = [b for b in result.buckets if b.bucket_start not in by_minute or b.value is None]
    assert outside and all(b.measurement == "NOT_MEASURED" and b.value is None for b in outside)
    assert len(result.buckets) > 3  # the grid is wider than the three planted buckets

    await _run(db_session)
    stored = (await _breaches(db_session))[0]
    assert result.fired_count == 1
    assert result.breach_intervals[0].start == stored.first_bucket_start
    assert result.breach_intervals[0].end == stored.last_bucket_start
    assert stored.state == "RESOLVED"


async def test_history_is_hidden_from_f1_during_the_kind_watermark_race_but_previewed(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=101, name="fw")
    # The shared PW_VALUE watermark may have been advanced by a rule on another watch.
    watch_b = await _watch(db_session, instance, source)
    value_b = await _value(db_session, watch_b, key="B")
    db_session.add(_rule("watch-b", value_b, threshold_value=1_000_000.0))
    await _arm(db_session)  # 10:00, before watch A's last history bucket

    watch_a = await _watch(db_session, instance, source)
    value_a = await _value(db_session, watch_a, key="A")
    db_session.add(_bucket(watch_a, 10, origin=PwBucketOrigin.HISTORY))
    db_session.add(_metric(value_a, 10, 500))
    db_session.add(_rule("created-during-reading", value_a, sustain_buckets=1))
    await db_session.flush()

    report = await _run(db_session)
    assert report.opened == 0
    assert await _breaches(db_session) == []

    preview = await _replay(db_session, value_a, sustain_buckets=1)
    point = next(bucket for bucket in preview.buckets if bucket.bucket_start == utc(h=10, mi=10))
    assert point.value == 500.0 and point.measurement == "EXACT"
    assert preview.fired_count == 1


async def test_a_missing_bucket_a_lower_bound_and_maintenance(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=2, name="fw")
    watch = await _watch(db_session, instance, source)
    value = await _value(db_session, watch)
    db_session.add_all(
        [
            # 10:00 has no bucket row at all
            _bucket(watch, 5, in_maintenance=True),
            _bucket(
                watch, 10, values_measurement="LOWER_BOUND", pinned_measurement="LOWER_BOUND"
            ),
            _bucket(watch, 15, pinned_measurement="LOWER_BOUND"),  # cut: no row proves nothing
        ]
    )
    db_session.add_all([_metric(value, 5, 150), _metric(value, 10, 150)])
    await db_session.flush()

    result = await _replay(db_session, value, sustain_buckets=1)
    by_minute = {b.bucket_start: b for b in result.buckets}
    assert by_minute[utc(h=10, mi=0)].measurement == "NOT_MEASURED"
    assert by_minute[utc(h=10, mi=0)].value is None
    assert by_minute[utc(h=10, mi=5)].in_maintenance is True
    lower = by_minute[utc(h=10, mi=10)]
    assert lower.measurement == "LOWER_BOUND" and lower.value == 150.0
    cut = by_minute[utc(h=10, mi=15)]
    assert cut.measurement == "NOT_MEASURED" and cut.value is None
    # the lower bound proves an increase (GT), the maintenance bucket pauses the counters
    assert result.fired_count == 1
    assert result.breach_intervals[0].start == utc(h=10, mi=10)


async def test_a_zero_needs_a_pinned_exact_covered_value(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=3, name="fw")
    watch = await _watch(db_session, instance, source)
    other = await _watch(db_session, instance, source)
    pinned = await _value(db_session, watch)
    unpinned = await _value(db_session, other, pinned=False)
    db_session.add_all([_bucket(watch, 10), _bucket(other, 10)])
    await db_session.flush()

    exact = await _replay(db_session, pinned, operator="LT", threshold_value=5.0)
    zero = {b.bucket_start: b for b in exact.buckets}[utc(h=10, mi=10)]
    assert zero.value == 0.0 and zero.measurement == "EXACT"

    miss = await _replay(db_session, unpinned, operator="LT", threshold_value=5.0)
    assert miss.fired_count == 0
    none = {b.bucket_start: b for b in miss.buckets}[utc(h=10, mi=10)]
    assert none.value is None and none.measurement == "NOT_MEASURED"

    # a coverage floor above the bucket's parsed share removes the zero
    watch_bucket = _bucket(watch, 15, parsed_events=50, unparsed_events=50)
    db_session.add(watch_bucket)
    await db_session.flush()
    low = {b.bucket_start: b for b in (await _replay(db_session, pinned)).buckets}
    assert low[utc(h=10, mi=15)].value is None


async def test_without_a_coverage_floor_or_a_value_nothing_loads(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=4, name="fw")
    watch = await _watch(db_session, instance, source)
    value = await _value(db_session, watch)
    db_session.add(_bucket(watch, 10))
    await db_session.flush()
    with counting(db_session) as statements:
        no_floor = await _replay(db_session, value, floor=None)
        no_value = await _replay(db_session, value, pw_value_id=None)
    assert statements == []
    for result in (no_floor, no_value):
        assert result.measured_count == 0 and result.fired_count == 0
        assert all(b.value is None for b in result.buckets)


async def test_both_preview_routes_carry_the_pw_value(db_session, db_schema: str) -> None:
    client, watch, pinned, _auto, _instance = await _api_world(db_session, db_schema)
    now = datetime.now(UTC)
    closed = latest_closed_bucket(now, 300)
    stamps = [closed - timedelta(seconds=300 * i) for i in (2, 1, 0)]
    pinned.tracked_since = stamps[0] - timedelta(hours=2)
    watch_row = await db_session.get(PropertyWatch, uuid.UUID(watch["id"]))
    assert watch_row is not None
    for stamp in stamps:
        db_session.add(
            _bucket(watch_row, 0, bucket_start=stamp, collected_at=stamp)
        )
    db_session.add_all(
        [
            PwValueMetric(pw_value_id=pinned.id, bucket_start=stamps[0], events=900,
                          bucket_seconds=300),
            PwValueMetric(pw_value_id=pinned.id, bucket_start=stamps[1], events=900,
                          bucket_seconds=300),
        ]
    )
    await db_session.commit()

    body = {
        "name": "deny-flood",
        "subject_kind": "PW_VALUE",
        "pw_value_id": str(pinned.id),
        "metric": "pw_value_events",
        "operator": "GT",
        "threshold_value": 500,
        "sustain_buckets": 2,
        "recovery_buckets": 1,
    }
    draft = client.post(f"{PREFIX}/preview?hours=1",
        json={k: v for k, v in body.items() if k != "name"},
    )
    assert draft.status_code == 200, draft.text
    out = draft.json()
    measured = [b for b in out["buckets"] if b["measurement"] != "NOT_MEASURED"]
    assert len(measured) == 3 and out["fired_count"] == 1, out
    assert out["unit"] == "events/bucket"

    created = client.post(PREFIX, json=body)
    assert created.status_code == 201, created.text
    stored = client.get(f"{PREFIX}/{created.json()['id']}/preview?hours=1")
    assert stored.status_code == 200
    assert stored.json()["buckets"] == out["buckets"]
    assert stored.json()["fired_count"] == 1
    assert (await db_session.scalars(select(CustomRuleBreach))).all() == []
