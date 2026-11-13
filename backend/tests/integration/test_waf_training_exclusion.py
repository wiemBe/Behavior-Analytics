"""SOC-13e: WAF baseline training honours analyst exclusions.

The rebuild under test is `WafBaselineBuilder.rebuild_endpoints`, which is the
only production rebuild. `run_waf_source` is the scheduled caller.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.anomaly import training_exclusions as te
from app.anomaly.seasonal import seasonal_key
from app.core.config import Settings
from app.history.origin import HistoryOrigin
from app.models.enums import (
    AnomalyState,
    BucketCompleteness,
    DetectorSignal,
    EvidenceStatus,
    Severity,
    WafAnomalyType,
    WafBreakdown,
    WafMeasurement,
    WafTelemetryStatus,
)
from app.models.identity import AuditLog
from app.models.monitoring import CollectionWatermark
from app.models.waf import (
    WafAnomaly,
    WafEndpoint,
    WafEndpointBaseline,
    WafEndpointMetric,
    WafSourceBucket,
)
from app.security.rbac import Principal
from app.waf.baseline import WafBaselineBuilder
from app.waf.detector import (
    WafDetectionContext,
    WafPoint,
    WafThresholds,
    detect_request_rate_spike,
)
from app.waf.engine import WafAnomalyEngine
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from app.waf.semantics import breakdown_semantics, request_count_semantics
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_fleet_api import Client, run
from tests.integration.test_waf_productionization import (
    StubWafProvider,
    cfg,
    run_at,
    source_with_profile,
)
from tests.integration.test_waf_scale import counting

pytestmark = [pytest.mark.integration]

T0 = datetime(2026, 7, 1, 10, tzinfo=UTC)
STEP = timedelta(minutes=5)
WEEK = timedelta(days=7)
REQUEST_IDENTITY = request_count_semantics(
    aggregation="SUM(eventcount)",
    normalizer_version=ENDPOINT_NORMALIZER_VERSION,
    profile_version=1,
)
ACTION_IDENTITY = breakdown_semantics(
    WafBreakdown.ACTION.value,
    aggregation="SUM(eventcount)",
    normalizer_version=ENDPOINT_NORMALIZER_VERSION,
    profile_version=1,
)

TRIAGER = Principal("ada", permissions=frozenset({"read:anomalies", "anomaly:triage"}))
READER = Principal("rita", permissions=frozenset({"read:*"}))


def builder_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "encryption_key": "x" * 44,
        "baseline_min_samples": 1,
        "baseline_lookback_days": 120,
        "waf_min_endpoint_coverage": 0.5,
        "waf_ratio_min_denominator": 1,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def source_bucket(
    source_id: uuid.UUID,
    start: datetime,
    *,
    seconds: int = 300,
    origin: str = HistoryOrigin.LIVE.value,
    action: WafTelemetryStatus = WafTelemetryStatus.EXACT,
) -> WafSourceBucket:
    exact = action is WafTelemetryStatus.EXACT
    return WafSourceBucket(
        log_source_id=source_id,
        bucket_start=start,
        bucket_seconds=seconds,
        origin=origin,
        profile_version=1,
        metric_semantics=REQUEST_IDENTITY,
        completeness=BucketCompleteness.COMPLETE,
        endpoint_coverage_ratio=1.0,
        total_events=200,
        parsed_events=200,
        group_count=1,
        groups_truncated=False,
        unusable_groups=0,
        observed_endpoints=1,
        tracked_endpoints_observed=1,
        admitted_endpoints=0,
        below_admission_threshold=0,
        rejected_over_quota=0,
        untracked_endpoints=0,
        untracked_requests=0,
        admission_truncated=False,
        zero_filled_endpoints=0,
        request_count_measurement=WafMeasurement.EXACT,
        sparse_endpoint_rows=True,
        in_maintenance=False,
        action_measurement=action,
        action_semantics=ACTION_IDENTITY if exact else None,
        action_events=200 if exact else None,
        query_provenance={},
    )


def metric_row(
    endpoint_id: uuid.UUID,
    start: datetime,
    request_count: int,
    *,
    blocked: int | None = None,
) -> WafEndpointMetric:
    counts: dict[str, int] = {}
    if blocked is not None:
        counts = {
            "action_events": request_count,
            "blocked_count": blocked,
            "unmapped_action_count": 0,
        }
    return WafEndpointMetric(
        waf_endpoint_id=endpoint_id,
        bucket_start=start,
        bucket_seconds=300,
        request_count=request_count,
        endpoint_coverage_ratio=1.0,
        completeness=BucketCompleteness.COMPLETE,
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        query_provenance={},
        **counts,
    )


async def make_endpoints(session, n: int = 1, qradar_id: int = 312):  # type: ignore[no-untyped-def]
    instance = await make_instance(session)
    source = await make_log_source(
        session, instance, qradar_id=qradar_id, name=f"waf-{qradar_id}"
    )
    endpoints = []
    for i in range(n):
        endpoint = WafEndpoint(
            instance_id=instance.id,
            log_source_id=source.id,
            normalized_endpoint=f"/app/{i}",
            http_method="GET",
            normalizer_version=ENDPOINT_NORMALIZER_VERSION,
            first_seen_at=T0 - WEEK,
            last_seen_at=T0 + 12 * WEEK,
            tracked_since=T0 - WEEK,
            is_active=True,
        )
        session.add(endpoint)
        endpoints.append(endpoint)
    await session.flush()
    return source, endpoints


def plant(session, source, endpoints, starts, values, *, blocked=None) -> None:  # type: ignore[no-untyped-def]
    session.add_all(source_bucket(source.id, start) for start in starts)
    for endpoint in endpoints:
        for start, value in zip(starts, values, strict=True):
            session.add(metric_row(
                endpoint.id, start, value,
                blocked=None if blocked is None else blocked(value),
            ))


async def rebuild(session, source, endpoints, now, *, identities=None, settings=None):  # type: ignore[no-untyped-def]
    builder = WafBaselineBuilder(session, settings=settings or builder_settings())
    return await builder.rebuild_endpoints(
        list(endpoints),
        semantics=REQUEST_IDENTITY,
        breakdown_identities=identities,
        now=now,
        log_source=source,
    )


async def cell_for(session, endpoint_id, start, metric="request_count"):  # type: ignore[no-untyped-def]
    weekday, hour = seasonal_key(start)
    return await session.scalar(
        select(WafEndpointBaseline).where(
            WafEndpointBaseline.waf_endpoint_id == endpoint_id,
            WafEndpointBaseline.metric_name == metric,
            WafEndpointBaseline.weekday == weekday,
            WafEndpointBaseline.hour == hour,
        )
    )


async def exclude(session, endpoint_id, start, end):  # type: ignore[no-untyped-def]
    result = await te.create(
        session,
        origin_family="waf",
        origin_anomaly_id=uuid.uuid4(),
        subject_kind="endpoint",
        subject_id=endpoint_id,
        scope_key=te.WAF_ENDPOINT_SCOPE,
        starts_at=start,
        ends_at=end,
        reason="analyst removed this window",
        created_by="ada",
        lookback_start=start - timedelta(days=30),
    )
    return result.row


def _anchor() -> datetime:
    """A recent hour: inside the default lookback, and aligned to 300s and to 600s."""
    return (datetime.now(UTC) - timedelta(days=2)).replace(minute=0, second=0, microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _body(start: datetime, end: datetime, reason: str = "change window") -> dict:
    return {"starts_at": _iso(start), "ends_at": _iso(end), "reason": reason}


def _url(anomaly_id: uuid.UUID, family: str = "waf") -> str:
    return f"/api/v1/anomalies/{family}/{anomaly_id}/training-exclusions"


def _code(response) -> str:  # type: ignore[no-untyped-def]
    body = response.json()
    detail = body.get("detail")
    if isinstance(detail, dict) and "code" in detail:
        return str(detail["code"])
    return str(body["detail_code"])


async def _seed_waf(  # type: ignore[no-untyped-def]
    session,
    *,
    state=AnomalyState.RESOLVED,
    buckets=True,
    detected: datetime | None = None,
    resolved: datetime | None = None,
):
    detected = detected or _anchor()
    if resolved is None and state == AnomalyState.RESOLVED:
        resolved = detected
    instance = await make_instance(session)
    source = await make_log_source(session, instance, qradar_id=4401, name="waf-api")
    endpoint = WafEndpoint(
        instance_id=instance.id,
        log_source_id=source.id,
        normalized_endpoint="/login",
        http_method="POST",
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        first_seen_at=detected,
        last_seen_at=detected,
        tracked_since=detected,
        is_active=True,
    )
    session.add(endpoint)
    await session.flush()
    anomaly = WafAnomaly(
        waf_endpoint_id=endpoint.id,
        detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
        severity=Severity.HIGH,
        state=state,
        detected_at=detected,
        resolved_at=resolved,
        evidence_status=EvidenceStatus.NOT_REQUESTED,
        details={},
    )
    session.add(anomaly)
    if buckets:
        session.add(source_bucket(source.id, detected))
    await session.flush()
    return anomaly.id, endpoint.id


def seed_waf(**kw):  # type: ignore[no-untyped-def]
    async def work(session):  # type: ignore[no-untyped-def]
        return await _seed_waf(session, **kw)

    return run(work)


# --- training ----------------------------------------------------------------------


async def test_excluded_interval_changes_the_request_and_ratio_bands(db_session) -> None:
    """One scope drops the bucket from the request cell and the ratio cell."""
    source, (treated, control) = await make_endpoints(db_session, 2)
    starts = [T0 + i * WEEK for i in range(4)]
    values = [50, 52, 48, 900]
    plant(
        db_session, source, (treated, control), starts, values,
        blocked=lambda value: 450 if value == 900 else 10,
    )
    await db_session.flush()
    await exclude(db_session, treated.id, starts[-1], starts[-1] + STEP)
    identities = {WafBreakdown.ACTION: ACTION_IDENTITY}

    await rebuild(db_session, source, (treated, control), starts[-1] + STEP, identities=identities)

    treated_requests = await cell_for(db_session, treated.id, starts[0])
    control_requests = await cell_for(db_session, control.id, starts[0])
    treated_ratio = await cell_for(db_session, treated.id, starts[0], "block_ratio")
    control_ratio = await cell_for(db_session, control.id, starts[0], "block_ratio")
    assert 900.0 not in treated_requests.observations
    assert treated_requests.sample_count == 3
    assert treated_requests.p95 < control_requests.p95
    assert 900.0 in control_requests.observations
    assert treated_requests.exclusion_counts["analyst_exclusion"] == 1
    assert treated_requests.excluded_sample_count == sum(treated_requests.exclusion_counts.values())
    assert "analyst_exclusion" not in control_requests.exclusion_counts
    assert 0.5 not in treated_ratio.observations
    assert 0.5 in control_ratio.observations
    assert treated_ratio.exclusion_counts["analyst_exclusion"] == 1
    assert treated_ratio.numerator_sum == 30
    assert control_ratio.numerator_sum == 480


async def test_revoked_exclusion_trains_as_if_it_had_never_been_there(db_session) -> None:
    source, (endpoint,) = await make_endpoints(db_session)
    starts = [T0 + i * WEEK for i in range(4)]
    plant(db_session, source, (endpoint,), starts, [50, 52, 48, 900])
    await db_session.flush()
    row = await exclude(db_session, endpoint.id, starts[-1], starts[-1] + STEP)
    now = starts[-1] + STEP
    await rebuild(db_session, source, (endpoint,), now)
    assert 900.0 not in (await cell_for(db_session, endpoint.id, starts[0])).observations

    await te.revoke(db_session, row.id, revoked_by="ada", reason="put the bucket back")
    await rebuild(db_session, source, (endpoint,), now)
    cell = await cell_for(db_session, endpoint.id, starts[0])
    assert 900.0 in cell.observations
    assert "analyst_exclusion" not in cell.exclusion_counts


async def test_a_fully_excluded_cell_is_insufficient_for_the_detector(db_session) -> None:
    source, (endpoint,) = await make_endpoints(db_session)
    kept = [T0.replace(hour=11) + i * WEEK for i in range(3)]
    dropped = [T0 + i * WEEK for i in range(3)]
    plant(db_session, source, (endpoint,), kept, [40, 42, 38])
    plant(db_session, source, (endpoint,), dropped, [40, 42, 38])
    await db_session.flush()
    now = dropped[-1] + STEP
    await rebuild(db_session, source, (endpoint,), now)
    before = await cell_for(db_session, endpoint.id, dropped[0])
    kept_before = await cell_for(db_session, endpoint.id, kept[0])
    assert before.is_reliable and kept_before.is_reliable
    kept_version = kept_before.baseline_version
    kept_observations = list(kept_before.observations)
    median_before = before.median
    version_before = before.baseline_version

    for start in dropped:
        await exclude(db_session, endpoint.id, start, start + STEP)
    # A point span per bucket. One span from the first hour to the last would also
    # cover the later hour-10 buckets, and those would no longer be analyst exclusions.
    for start in kept:
        db_session.add(WafAnomaly(
            waf_endpoint_id=endpoint.id,
            detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
            severity=Severity.HIGH,
            state=AnomalyState.RESOLVED,
            detected_at=start,
            resolved_at=start,
            evidence_status=EvidenceStatus.NOT_REQUESTED,
            details={},
        ))
    await db_session.flush()
    await rebuild(db_session, source, (endpoint,), now)

    emptied = await cell_for(db_session, endpoint.id, dropped[0])
    untouched = await cell_for(db_session, endpoint.id, kept[0])
    assert emptied.is_reliable is False
    assert emptied.sample_count == 0
    assert emptied.observations == []
    assert emptied.p05 is None and emptied.p95 is None
    assert emptied.completeness == 0
    assert emptied.baseline_version == version_before + 1
    assert emptied.median == median_before
    verdict = detect_request_rate_spike(WafDetectionContext(
        point=WafPoint(
            bucket_start=dropped[-1] + WEEK,
            bucket_end=dropped[-1] + WEEK + STEP,
            completeness=BucketCompleteness.COMPLETE,
            request_count=900,
            endpoint_coverage_ratio=1.0,
            measurement=WafMeasurement.EXACT,
        ),
        baseline=WafAnomalyEngine._cell(emptied),
        thresholds=WafThresholds(),
    ))
    assert verdict.signal is DetectorSignal.UNKNOWN
    assert verdict.evidence.insufficient_data is True
    assert verdict.evidence.expected_value is None

    assert untouched.is_reliable is True
    assert untouched.baseline_version == kept_version
    assert untouched.observations == kept_observations
    healthy = detect_request_rate_spike(WafDetectionContext(
        point=WafPoint(
            bucket_start=kept[-1] + WEEK,
            bucket_end=kept[-1] + WEEK + STEP,
            completeness=BucketCompleteness.COMPLETE,
            request_count=40,
            endpoint_coverage_ratio=1.0,
            measurement=WafMeasurement.EXACT,
        ),
        baseline=WafAnomalyEngine._cell(untouched),
        thresholds=WafThresholds(),
    ))
    assert healthy.signal is DetectorSignal.HEALTHY
    assert healthy.evidence.insufficient_data is False


async def test_automatic_and_analyst_overlap_is_counted_once(db_session) -> None:
    source, (endpoint,) = await make_endpoints(db_session)
    starts = [T0 + i * WEEK for i in range(6)]
    plant(db_session, source, (endpoint,), starts, [50, 51, 49, 52, 48, 50])
    anomaly = WafAnomaly(
        waf_endpoint_id=endpoint.id,
        detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
        severity=Severity.HIGH,
        state=AnomalyState.RESOLVED,
        detected_at=starts[0],
        resolved_at=starts[0],
        evidence_status=EvidenceStatus.NOT_REQUESTED,
        details={},
    )
    db_session.add(anomaly)
    await db_session.flush()
    await exclude(db_session, endpoint.id, starts[0], starts[0] + STEP)
    await exclude(db_session, endpoint.id, starts[1], starts[1] + STEP)

    await rebuild(db_session, source, (endpoint,), starts[-1] + STEP)
    cell = await cell_for(db_session, endpoint.id, starts[0])
    assert cell.exclusion_counts["known_anomaly"] == 1
    assert cell.exclusion_counts["analyst_exclusion"] == 1
    assert cell.sample_count == 4
    assert cell.excluded_sample_count == sum(cell.exclusion_counts.values())


async def test_rebuild_reads_exclusions_once_and_invalidates_once(db_session) -> None:
    source, endpoints = await make_endpoints(db_session, 4)
    starts = [T0, T0 + WEEK]
    plant(db_session, source, endpoints, starts, [40, 41])
    await db_session.flush()
    settings = builder_settings(waf_baseline_rebuild_chunk_size=1)
    now = starts[-1] + STEP

    with counting(db_session) as first:
        await rebuild(db_session, source, endpoints, now, settings=settings)
    assert _mentions(first, "baseline_training_exclusion") == 1
    assert _cell_updates(first) == 0

    for endpoint in endpoints:
        for start in starts:
            await exclude(db_session, endpoint.id, start, start + STEP)
    with counting(db_session) as second:
        await rebuild(db_session, source, endpoints[:1], now, settings=settings)
    assert _mentions(second, "baseline_training_exclusion") == 1

    with counting(db_session) as third:
        await rebuild(db_session, source, endpoints, now, settings=settings)
    assert _mentions(third, "baseline_training_exclusion") == 1
    assert _cell_updates(third) == 1
    for endpoint in endpoints:
        cell = await cell_for(db_session, endpoint.id, starts[0])
        assert cell.is_reliable is False
        assert cell.sample_count == 0


def _mentions(statements: list[str], needle: str) -> int:
    return sum(1 for sql in statements if needle in sql.lower())


def _cell_updates(statements: list[str]) -> int:
    return sum(
        1
        for sql in statements
        if " ".join(sql.split()).upper().startswith("UPDATE WAF_ENDPOINT_BASELINE")
    )


async def test_scheduled_run_trains_with_the_exclusion(db_session) -> None:
    """`run_waf_source` is the Celery caller. Collection and evaluation are already caught up."""
    settings = cfg(baseline_min_samples=1)
    provider = StubWafProvider()
    profile, source = await source_with_profile(db_session, provider, settings)
    last = datetime(2026, 9, 23, 10, tzinfo=UTC)
    starts = [last - i * WEEK for i in range(3, -1, -1)]
    values = [40, 42, 38, 900]
    endpoint = WafEndpoint(
        instance_id=source.instance_id,
        log_source_id=source.id,
        normalized_endpoint="/checkout",
        http_method="POST",
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        first_seen_at=starts[0],
        last_seen_at=last,
        tracked_since=starts[0],
        is_active=True,
    )
    db_session.add(endpoint)
    await db_session.flush()
    plant(db_session, source, (endpoint,), starts, values)
    for collector in (
        f"waf_endpoint_metric:{source.qradar_id}",
        f"waf_endpoint_evaluation:{source.qradar_id}",
    ):
        db_session.add(CollectionWatermark(
            instance_id=source.instance_id, collector=collector, watermark_at=last,
        ))
    await db_session.flush()

    first = await run_at(db_session, profile, provider, settings, last)
    assert first["status"] == "ok", first
    assert first["intervals_collected"] == 0
    assert first["baseline_endpoints_rebuilt"] == 1
    trained = await cell_for(db_session, endpoint.id, starts[0])
    assert 900.0 in trained.observations

    await exclude(db_session, endpoint.id, last, last + STEP)
    second = await run_at(db_session, profile, provider, settings, last)
    assert second["status"] == "ok", second
    retrained = await cell_for(db_session, endpoint.id, starts[0])
    assert 900.0 not in retrained.observations
    assert retrained.exclusion_counts["analyst_exclusion"] == 1


# --- API ---------------------------------------------------------------------------


def test_waf_create_accepts_a_resolved_anomaly(db_schema) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, endpoint_id = seed_waf()
    start = _anchor()
    response = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + STEP))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["origin_family"] == "waf"
    assert body["subject_kind"] == "endpoint"
    assert body["subject_id"] == str(endpoint_id)
    assert body["scope_key"] == "waf:endpoint"
    assert body["state"] == "PENDING_REBUILD"
    assert body["created_by"] == "ada"

    again = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + STEP))
    assert again.status_code == 200
    assert again.json()["id"] == body["id"]


def test_waf_guards_match_phase_a(db_schema) -> None:  # type: ignore[no-untyped-def]
    start = _anchor()
    open_id, _ = seed_waf(state=AnomalyState.OPEN)
    assert _code(Client(TRIAGER).post(_url(open_id), json=_body(start, start + STEP))) == (
        "baseline_exclusion.anomaly_active"
    )
    missing = Client(TRIAGER).post(_url(uuid.uuid4()), json=_body(start, start + STEP))
    assert missing.status_code == 404
    assert _code(missing) == "baseline_exclusion.not_found"

    no_bucket, _ = seed_waf(buckets=False)
    assert _code(Client(TRIAGER).post(_url(no_bucket), json=_body(start, start + STEP))) == (
        "baseline_exclusion.interval_invalid"
    )

    anomaly_id, _ = seed_waf()
    refused = Client(READER).post(_url(anomaly_id), json=_body(start, start + STEP))
    assert refused.status_code == 403
    blank = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + STEP, reason="  "))
    assert _code(blank) == "baseline_exclusion.reason_invalid"
    # Family is refused before the reason is read, so a blank reason cannot hide it.
    # SOC-13g accepts `ips`, so the probe is a family that is still refused.
    early = Client(TRIAGER).post(
        _url(uuid.uuid4(), "property_watch"), json=_body(start, start + STEP, reason="  ")
    )
    assert early.status_code == 422
    assert _code(early) == "baseline_exclusion.family_unsupported"

    narrowed = Client(TRIAGER).post(
        _url(anomaly_id), json=_body(start + STEP, start + 2 * STEP)
    )
    assert _code(narrowed) == "baseline_exclusion.interval_not_widened"
    too_long = Client(TRIAGER).post(
        _url(anomaly_id), json=_body(start, start + timedelta(days=7, minutes=5))
    )
    assert _code(too_long) == "baseline_exclusion.interval_too_long"

    created = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + STEP))
    assert created.status_code == 201
    overlap = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + 2 * STEP))
    assert overlap.status_code == 409
    assert _code(overlap) == "baseline_exclusion.overlap"

    audits = run(_audits)
    assert audits
    assert all("change window" not in str(row.detail) for row in audits)


async def _audits(session):  # type: ignore[no-untyped-def]
    return list((await session.scalars(
        select(AuditLog).where(AuditLog.action == "baseline_exclusion.create")
    )).all())


# SOC-13g accepts `ips`; tests/integration/test_ips_training_exclusion.py pins that.
@pytest.mark.parametrize("family", ["property_watch", "rule", "nonsense"])
def test_other_families_stay_unsupported(db_schema, family: str) -> None:
    start = _anchor()
    response = Client(TRIAGER).post(_url(uuid.uuid4(), family), json=_body(start, start + STEP))
    assert response.status_code == 422
    assert _code(response) == "baseline_exclusion.family_unsupported"


def test_bucket_width_comes_from_the_live_row(db_schema) -> None:  # type: ignore[no-untyped-def]
    detected = _anchor()
    history_only, _ = seed_waf(buckets=False, detected=detected, resolved=detected)

    async def history(session):  # type: ignore[no-untyped-def]
        anomaly = await session.get(WafAnomaly, history_only)
        endpoint = await session.get(WafEndpoint, anomaly.waf_endpoint_id)
        session.add(source_bucket(
            endpoint.log_source_id, detected, seconds=600, origin=HistoryOrigin.HISTORY.value,
        ))

    run(history)
    # 10:05 is a 300-second boundary and not a 600-second one. History cannot supply the width.
    asked = _body(detected, detected + STEP)
    assert _code(Client(TRIAGER).post(_url(history_only), json=asked)) == (
        "baseline_exclusion.interval_invalid"
    )

    anomaly_id, _ = seed_waf(buckets=False, detected=detected, resolved=detected)

    async def both(session):  # type: ignore[no-untyped-def]
        anomaly = await session.get(WafAnomaly, anomaly_id)
        endpoint = await session.get(WafEndpoint, anomaly.waf_endpoint_id)
        session.add(source_bucket(
            endpoint.log_source_id, detected - STEP, seconds=300, origin=HistoryOrigin.LIVE.value,
        ))
        session.add(source_bucket(
            endpoint.log_source_id, detected, seconds=600, origin=HistoryOrigin.HISTORY.value,
        ))

    run(both)
    accepted = Client(TRIAGER).post(_url(anomaly_id), json=asked)
    assert accepted.status_code == 201, accepted.text


def test_applied_follows_the_waf_cell_computed_at(db_schema) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, endpoint_id = seed_waf()
    start = _anchor()
    created = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + STEP))
    assert created.status_code == 201
    exclusion_id = created.json()["id"]
    created_at = datetime.fromisoformat(created.json()["created_at"])

    async def stamp(session, at: datetime) -> None:
        cell = await session.scalar(
            select(WafEndpointBaseline).where(
                WafEndpointBaseline.waf_endpoint_id == endpoint_id
            )
        )
        if cell is None:
            session.add(WafEndpointBaseline(
                waf_endpoint_id=endpoint_id,
                metric_name="request_count",
                weekday=3,
                hour=10,
                median=40.0,
                mad=1.0,
                sample_count=4,
                is_reliable=True,
                computed_at=at,
                normalizer_version=ENDPOINT_NORMALIZER_VERSION,
                observations=[40.0],
            ))
        else:
            cell.computed_at = at

    run(lambda session: stamp(session, created_at - timedelta(hours=1)))
    pending = _listed(endpoint_id, "PENDING_REBUILD")
    assert [item["id"] for item in pending] == [exclusion_id]
    assert _listed(endpoint_id, "APPLIED") == []

    run(lambda session: stamp(session, created_at + timedelta(minutes=1)))
    applied = _listed(endpoint_id, "APPLIED")
    assert [item["id"] for item in applied] == [exclusion_id]
    assert applied[0]["state"] == "APPLIED"
    assert _listed(endpoint_id, "PENDING_REBUILD") == []

    revoked = Client(TRIAGER).post(
        f"/api/v1/behavior/training-exclusions/{exclusion_id}/revoke",
        json={"reason": "no longer wanted"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["state"] == "REVOKED"
    assert _listed(endpoint_id, "APPLIED") == []


def _listed(endpoint_id, state: str) -> list[dict]:
    response = Client(TRIAGER).get(
        "/api/v1/behavior/training-exclusions",
        params={"subject_kind": "endpoint", "subject_id": str(endpoint_id), "state": state},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]
