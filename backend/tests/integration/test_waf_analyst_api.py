"""WAF analyst API (T-057): the unified anomaly list and detail, and WAF behavior reads.

DB-gated. Everything is seeded through committed sessions and read back over HTTP,
so the tests see exactly what an analyst's client sees.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings, get_settings
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    EvidenceStatus,
    Severity,
    WafAnomalyType,
    WafMeasurement,
    WafProfileValidationStatus,
    WafTelemetryStatus,
)
from app.models.log_source import AnomalyStateTransition, LogSourceAnomaly
from app.models.waf import (
    WafAnomaly,
    WafAnomalyTransition,
    WafDetectorState,
    WafEndpoint,
    WafEndpointBaseline,
    WafEndpointMetric,
    WafSourceBucket,
    WafSourceProfile,
)
from app.security.rbac import Principal
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_fleet_api import Client, run

pytestmark = pytest.mark.integration

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
ANOMALY_READER = Principal(subject="ar", permissions=frozenset({"read:anomalies"}))
NOBODY = Principal(subject="nobody", permissions=frozenset())
MISSING = "00000000-0000-0000-0000-000000000000"

NOW = (datetime.now(UTC) - timedelta(minutes=30)).replace(microsecond=0)


def at(minutes: int) -> datetime:
    """A detection time `minutes` before the fixed NOW."""
    return NOW - timedelta(minutes=minutes)


def phase_a(source_id, minutes, **kw) -> LogSourceAnomaly:  # type: ignore[no-untyped-def]
    defaults = dict(
        log_source_id=source_id, anomaly_type=AnomalyType.VOLUME_SPIKE,
        severity=Severity.HIGH, state=AnomalyState.OPEN, detected_at=at(minutes),
        evidence_status=EvidenceStatus.NOT_REQUESTED,
    )
    defaults.update(kw)
    return LogSourceAnomaly(**defaults)


def waf(endpoint_id, minutes, **kw) -> WafAnomaly:  # type: ignore[no-untyped-def]
    defaults = dict(
        waf_endpoint_id=endpoint_id, detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
        severity=Severity.MEDIUM, state=AnomalyState.OPEN, detected_at=at(minutes),
        evidence_status=EvidenceStatus.NOT_REQUESTED,
    )
    defaults.update(kw)
    return WafAnomaly(**defaults)


def endpoint(instance_id, source_id, path="/login", method="POST", host=None, **kw):  # type: ignore[no-untyped-def]
    defaults = dict(
        instance_id=instance_id, log_source_id=source_id, virtual_host=host,
        normalized_endpoint=path, http_method=method,
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        first_seen_at=at(600), last_seen_at=at(1), tracked_since=at(600), is_active=True,
    )
    defaults.update(kw)
    return WafEndpoint(**defaults)


def profile(instance_id, source_id, **kw) -> WafSourceProfile:  # type: ignore[no-untyped-def]
    defaults = dict(
        instance_id=instance_id, log_source_id=source_id, enabled=True, profile_version=2,
        url_property="URL", method_property="Method", product_label="lab-waf",
        validation_status=WafProfileValidationStatus.VALID, validated_profile_version=2,
        validated_at=at(60), validation_report={},
    )
    defaults.update(kw)
    return WafSourceProfile(**defaults)


def source_bucket(source_id, bucket_start, **kw) -> WafSourceBucket:  # type: ignore[no-untyped-def]
    defaults = dict(
        log_source_id=source_id, bucket_start=bucket_start, bucket_seconds=300,
        profile_version=2, metric_semantics="sem", completeness=BucketCompleteness.COMPLETE,
        endpoint_coverage_ratio=0.97, total_events=500, parsed_events=485, group_count=3,
        groups_truncated=False, unusable_groups=0, observed_endpoints=3,
        tracked_endpoints_observed=3, admitted_endpoints=0, below_admission_threshold=0,
        rejected_over_quota=0, untracked_endpoints=0, untracked_requests=0,
        admission_truncated=False, zero_filled_endpoints=0,
        request_count_measurement=WafMeasurement.EXACT, sparse_endpoint_rows=True,
        in_maintenance=False, collection_source="TEST", query_provenance={"queries": ["AQL"]},
    )
    defaults.update(kw)
    defaults.setdefault("origin", "LIVE")
    return WafSourceBucket(**defaults)


async def world(s, *, a_rows: int = 3, w_rows: int = 3):  # type: ignore[no-untyped-def]
    """A Phase A source and a WAF source with `a_rows`/`w_rows` interleaved anomalies.

    Phase A rows sit at even minutes (0, 2, ...), WAF rows at odd ones (1, 3, ...),
    so the merged order alternates and any per-kind concatenation is visible.
    """
    inst = await make_instance(s)
    fw = await make_log_source(s, inst, qradar_id=1, name="fw-1")
    ws = await make_log_source(s, inst, qradar_id=2, name="waf-1")
    s.add(profile(inst.id, ws.id, virtual_host_property=None))
    ep = endpoint(inst.id, ws.id)
    s.add(ep)
    await s.flush()
    s.add_all([phase_a(fw.id, 2 * i) for i in range(a_rows)])
    s.add_all([waf(ep.id, 2 * i + 1) for i in range(w_rows)])
    await s.flush()
    return inst, fw, ws, ep


# ---------------------------------------------------------------- unified list
def test_mixed_list_interleaves_both_kinds_in_one_order_across_pages(db_schema) -> None:
    run(lambda s: world(s, a_rows=4, w_rows=4))
    client = Client(READER)
    full = client.get("/api/v1/anomalies?limit=200").json()
    assert full["total"] == 8
    kinds = [i["subject_kind"] for i in full["items"]]
    assert kinds == ["LOG_SOURCE", "WAF_ENDPOINT"] * 4
    stamps = [i["detected_at"] for i in full["items"]]
    assert stamps == sorted(stamps, reverse=True)

    paged: list[str] = []
    for offset in (0, 3, 6):
        page = client.get(f"/api/v1/anomalies?limit=3&offset={offset}").json()
        assert page["total"] == 8
        paged += [i["id"] for i in page["items"]]
    assert paged == [i["id"] for i in full["items"]]
    assert len(set(paged)) == 8


def test_waf_item_names_its_subject_and_phase_a_item_has_none(db_schema) -> None:
    run(lambda s: world(s, a_rows=1, w_rows=1))
    items = Client(READER).get("/api/v1/anomalies").json()["items"]
    a = next(i for i in items if i["subject_kind"] == "LOG_SOURCE")
    w = next(i for i in items if i["subject_kind"] == "WAF_ENDPOINT")
    assert a["waf_endpoint"] is None
    assert a["anomaly_type"] == "VOLUME_SPIKE" and a["observed_measurement"] is None
    assert w["log_source_name"] == "waf-1" and a["log_source_name"] == "fw-1"
    assert w["anomaly_type"] == "WAF_REQUEST_RATE_SPIKE"
    assert set(w["waf_endpoint"]) == {
        "id", "normalized_endpoint", "http_method", "virtual_host",
        "virtual_host_available", "normalizer_version",
    }
    assert w["waf_endpoint"]["normalized_endpoint"] == "/login"
    assert w["waf_endpoint"]["virtual_host"] is None
    assert w["waf_endpoint"]["virtual_host_available"] is False
    assert w["observed_measurement"] == "EXACT"


def test_virtual_host_available_follows_the_profile_mapping_or_a_stored_host(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        mapped = await make_log_source(s, inst, qradar_id=1, name="waf-mapped")
        bare = await make_log_source(s, inst, qradar_id=2, name="waf-bare")
        s.add_all([
            profile(inst.id, mapped.id, virtual_host_property="Host"),
            profile(inst.id, bare.id),
        ])
        e_mapped = endpoint(inst.id, mapped.id, path="/m")
        e_bare = endpoint(inst.id, bare.id, path="/b")
        e_host = endpoint(inst.id, bare.id, path="/h", host="a.example")
        s.add_all([e_mapped, e_bare, e_host])
        await s.flush()
        s.add_all([waf(e_mapped.id, 1), waf(e_bare.id, 2), waf(e_host.id, 3)])

    run(setup)
    items = Client(READER).get("/api/v1/anomalies").json()["items"]
    available = {
        i["waf_endpoint"]["normalized_endpoint"]: i["waf_endpoint"]["virtual_host_available"]
        for i in items
    }
    assert available == {"/m": True, "/b": False, "/h": True}


@pytest.fixture
def filtered_world(db_schema):  # type: ignore[no-untyped-def]
    """Rows chosen so every filter splits each kind and the kinds apart."""

    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        other = await make_instance(s)
        fw = await make_log_source(s, inst, qradar_id=1, name="fw")
        fw_other = await make_log_source(s, other, qradar_id=3, name="fw-other")
        ws = await make_log_source(s, inst, qradar_id=2, name="waf")
        ws_other = await make_log_source(s, other, qradar_id=4, name="waf-other")
        e1 = endpoint(inst.id, ws.id, path="/one")
        e2 = endpoint(inst.id, ws.id, path="/two")
        e3 = endpoint(other.id, ws_other.id, path="/three")
        s.add_all([e1, e2, e3])
        await s.flush()
        s.add_all([
            phase_a(fw.id, 10, state=AnomalyState.OPEN, severity=Severity.HIGH),
            phase_a(fw.id, 20, state=AnomalyState.RESOLVED, severity=Severity.LOW,
                    anomaly_type=AnomalyType.VOLUME_DROP, resolved_at=at(15),
                    evidence_status=EvidenceStatus.COMPLETE),
            phase_a(fw_other.id, 30, state=AnomalyState.OPEN, severity=Severity.HIGH),
            waf(e1.id, 11, state=AnomalyState.OPEN, severity=Severity.HIGH),
            waf(e1.id, 21, state=AnomalyState.RESOLVED, severity=Severity.LOW,
                detector_type=WafAnomalyType.WAF_BLOCK_RATIO_SPIKE,
                evidence_status=EvidenceStatus.COMPLETE),
            waf(e2.id, 31, state=AnomalyState.CANDIDATE, severity=Severity.MEDIUM),
            waf(e3.id, 41, state=AnomalyState.OPEN, severity=Severity.HIGH),
        ])
        await s.flush()
        return dict(inst=inst.id, other=other.id, fw=fw.id, ws=ws.id, e1=e1.id, e2=e2.id)

    return run(setup)


def shape(**params) -> tuple[int, int, int]:  # type: ignore[no-untyped-def]
    """(Phase A rows, WAF rows, total) for a filter set."""
    body = Client(READER).get("/api/v1/anomalies", params={"limit": 200, **params}).json()
    kinds = [i["subject_kind"] for i in body["items"]]
    assert body["total"] == len(kinds)
    return kinds.count("LOG_SOURCE"), kinds.count("WAF_ENDPOINT"), body["total"]


def test_each_filter_applies_to_each_kind(filtered_world) -> None:
    w = filtered_world
    assert shape() == (3, 4, 7)
    assert shape(state="OPEN") == (2, 2, 4)
    assert shape(severity="HIGH") == (2, 2, 4)
    assert shape(severity="LOW") == (1, 1, 2)
    assert shape(evidence_status="COMPLETE") == (1, 1, 2)
    assert shape(active_only="true") == (2, 3, 5)
    assert shape(log_source_id=str(w["fw"])) == (2, 0, 2)
    assert shape(log_source_id=str(w["ws"])) == (0, 3, 3)
    assert shape(instance_id=str(w["inst"])) == (2, 3, 5)
    assert shape(instance_id=str(w["other"])) == (1, 1, 2)
    assert shape(since=(at(25)).isoformat().replace("+00:00", "Z")) == (2, 2, 4)
    assert shape(until=(at(25)).isoformat().replace("+00:00", "Z")) == (1, 2, 3)


def test_filters_that_cannot_apply_to_a_kind_exclude_it(filtered_world) -> None:
    w = filtered_world
    # subject_kind selects the family outright.
    assert shape(subject_kind="LOG_SOURCE") == (3, 0, 3)
    assert shape(subject_kind="WAF_ENDPOINT") == (0, 4, 4)
    # A WAF endpoint id can only match WAF rows; Phase A is excluded, not ignored.
    assert shape(waf_endpoint_id=str(w["e1"])) == (0, 2, 2)
    assert shape(waf_endpoint_id=str(w["e2"])) == (0, 1, 1)
    assert shape(waf_endpoint_id=MISSING) == (0, 0, 0)
    # A type of one family excludes the other family.
    assert shape(anomaly_type="WAF_BLOCK_RATIO_SPIKE") == (0, 1, 1)
    assert shape(anomaly_type="WAF_REQUEST_RATE_SPIKE") == (0, 3, 3)
    assert shape(anomaly_type="VOLUME_DROP") == (1, 0, 1)
    assert shape(anomaly_type="VOLUME_SPIKE") == (2, 0, 2)
    # Contradictory filters are an empty result, not a fallback to either family.
    assert shape(subject_kind="LOG_SOURCE", anomaly_type="WAF_REQUEST_RATE_SPIKE") == (0, 0, 0)
    assert shape(subject_kind="LOG_SOURCE", waf_endpoint_id=str(w["e1"])) == (0, 0, 0)
    assert shape(subject_kind="WAF_ENDPOINT", anomaly_type="VOLUME_SPIKE") == (0, 0, 0)
    # An unknown type is a client error, never silently "everything".
    assert Client(READER).get("/api/v1/anomalies?anomaly_type=NOPE").status_code == 422


def test_waf_filters_combine_with_the_common_ones(filtered_world) -> None:
    w = filtered_world
    assert shape(waf_endpoint_id=str(w["e1"]), state="OPEN") == (0, 1, 1)
    assert shape(waf_endpoint_id=str(w["e1"]), severity="LOW", evidence_status="COMPLETE") == (
        0, 1, 1)
    assert shape(subject_kind="WAF_ENDPOINT", log_source_id=str(w["ws"]), active_only="true") == (
        0, 2, 2)


def test_unified_list_statement_count_is_constant(db_schema) -> None:
    ids = run(lambda s: world(s, a_rows=3, w_rows=2))
    client = Client(READER)
    assert client.get("/api/v1/anomalies?limit=200").json()["total"] == 5
    small = client.statements

    async def more(s):  # type: ignore[no-untyped-def]
        inst, fw, ws, _ep = ids
        eps = [endpoint(inst.id, ws.id, path=f"/p{i}") for i in range(10)]
        s.add_all(eps)
        await s.flush()
        s.add_all([phase_a(fw.id, 100 + i) for i in range(25)])
        s.add_all([waf(eps[i % 10].id, 200 + i) for i in range(30)])

    run(more)
    body = client.get("/api/v1/anomalies?limit=200").json()
    assert body["total"] == 60
    assert len(body["items"]) == 60
    assert client.statements == small, (small, client.statements)
    # Measured for the report; a count query plus a page query, whatever the size.
    assert small == 2, small


# ---------------------------------------------------------------------- detail
#: The detector's fixed interpretive caveat, as the evidence stores it (T-093).
WAF_CAVEAT = "A request-rate rise describes traffic volume, not intent."
WAF_DETAILS = {
    "reason": "812 requests is above the baseline median 100.0",
    "baseline_low": 80.0,
    "baseline_high": 120.0,
    "threshold": 3.5,
    "sample_count": 12,
    "signal": "ANOMALOUS",
    "queries": ["SELECT secret FROM events"],
    "extra": {
        "observed_requests": 812.0,
        "expected_requests": 100.0,
        "absolute_delta_requests": 712.0,
        "endpoint_coverage_ratio": 0.97,
        "baseline_completeness": 0.9,
        "baseline_version": 4,
        "request_count_measurement": "EXACT",
        "distinct_client_count": 7,
        "caveat": WAF_CAVEAT,
        "ratio": 8.12,
        "robust_score_status": "OK",
        "robust_z": 21.4,
        "contributors": [{"source_ip": "203.0.113.77", "requests": 600}],
        "entity": {"path": "/secret/raw/path"},
    },
}
WAF_DETECTION_KEYS = {
    "reason", "expected_low", "expected_high", "threshold", "baseline_sample_count",
    "baseline_completeness", "baseline_version", "normalizer_version", "observed_requests",
    "expected_requests", "absolute_delta_requests", "endpoint_coverage_ratio",
    "request_count_measurement", "observed_proportion", "expected_proportion",
    "observed_numerator", "observed_denominator", "percentage_point_change", "caveat", "ratio",
    "ratio_basis", "robust_score_status", "robust_z", "fallback_bound",
}


def seed_waf_incident():  # type: ignore[no-untyped-def]
    async def setup(s):  # type: ignore[no-untyped-def]
        _inst, _fw, _ws, ep = await world(s, a_rows=0, w_rows=0)
        a = waf(ep.id, 5, details=WAF_DETAILS, explanation="812 requests", baseline_version=4,
                observed_value=812.0, expected_value=100.0, deviation_ratio=8.12,
                observed_measurement=WafMeasurement.LOWER_BOUND, normalizer_version=1,
                opened_at=at(5), anomaly_start=at(5))
        s.add(a)
        await s.flush()
        s.add_all([
            WafAnomalyTransition(anomaly_id=a.id, from_state=AnomalyState.CANDIDATE,
                                 to_state=AnomalyState.OPEN, occurred_at=at(4),
                                 reason="2 consecutive buckets", actor="waf-anomaly-engine",
                                 observed_value=812.0, expected_value=100.0),
            WafAnomalyTransition(anomaly_id=a.id, from_state=None,
                                 to_state=AnomalyState.CANDIDATE, occurred_at=at(5),
                                 actor="waf-anomaly-engine"),
        ])
        await s.flush()
        return a.id, ep.id

    return run(setup)


def test_waf_detail_carries_the_projection_and_transitions(db_schema) -> None:
    anomaly_id, endpoint_id = seed_waf_incident()
    resp = Client(READER).get(f"/api/v1/anomalies/{anomaly_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["subject_kind"] == "WAF_ENDPOINT"
    assert body["anomaly_type"] == "WAF_REQUEST_RATE_SPIKE"
    assert body["waf_endpoint"]["id"] == str(endpoint_id)
    assert body["log_source_name"] == "waf-1"
    assert body["observed_measurement"] == "LOWER_BOUND"
    assert body["baseline_version"] == 4
    assert [t["to_state"] for t in body["transitions"]] == ["CANDIDATE", "OPEN"]
    assert "observed_measurement" not in body["transitions"][0]
    # Phase A-only sections are absent, not fabricated.
    assert body["explanation_package"] is None
    detection = body["detection"]
    assert set(detection) == WAF_DETECTION_KEYS
    assert detection["observed_requests"] == 812.0
    assert detection["expected_requests"] == 100.0
    assert detection["endpoint_coverage_ratio"] == 0.97
    assert detection["normalizer_version"] == 1
    assert detection["baseline_version"] == 4
    assert detection["request_count_measurement"] == "EXACT"
    assert detection["caveat"] == WAF_CAVEAT
    assert detection["ratio_basis"] is None and detection["fallback_bound"] is None
    # Nothing outside the whitelist leaks: no queries, no contributor IPs, no paths.
    text = resp.text
    for forbidden in ("queries", "SELECT secret", "203.0.113.77", "/secret/raw/path",
                      "contributors", "distinct_client_count"):
        assert forbidden not in text, forbidden


def seed_waf_incident_without_a_caveat():  # type: ignore[no-untyped-def]
    """The same incident, whose evidence recorded no caveat at all."""

    async def setup(s):  # type: ignore[no-untyped-def]
        _inst, _fw, _ws, ep = await world(s, a_rows=0, w_rows=0)
        extra = {k: v for k, v in WAF_DETAILS["extra"].items() if k != "caveat"}
        a = waf(ep.id, 5, details={**WAF_DETAILS, "extra": extra})
        s.add(a)
        await s.flush()
        return a.id

    return run(setup)


def test_waf_detail_has_no_caveat_when_the_evidence_recorded_none(db_schema) -> None:
    """An absent caveat is null, never an empty string and never an invented sentence."""
    body = Client(READER).get(f"/api/v1/anomalies/{seed_waf_incident_without_a_caveat()}").json()
    assert body["detection"]["caveat"] is None


def test_phase_a_detail_is_unchanged_by_the_unification(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        fw = await make_log_source(s, inst, qradar_id=1, name="fw-1")
        a = phase_a(fw.id, 3, details={
            "reason": "r", "baseline_low": 1.0, "baseline_high": 2.0, "threshold": 3.5,
            "sample_count": 30, "extra": {"observed_eps": 6.0, "ratio": 3.0},
        })
        s.add(a)
        await s.flush()
        s.add(AnomalyStateTransition(
            anomaly_id=a.id, from_state=AnomalyState.CANDIDATE, to_state=AnomalyState.OPEN,
            occurred_at=at(3), actor="anomaly-engine"))
        return a.id

    body = Client(READER).get(f"/api/v1/anomalies/{run(setup)}").json()
    assert body["subject_kind"] == "LOG_SOURCE" and body["waf_endpoint"] is None
    assert set(body["detection"]) == {
        "reason", "reason_code", "reason_params", "expected_low", "expected_high", "threshold",
        "baseline_sample_count", "baseline_completeness", "baseline_version", "observed_eps",
        "expected_eps",
        "observed_events", "expected_events", "absolute_delta_events", "bucket_seconds",
        "ratio", "ratio_basis", "robust_score_status", "robust_z", "fallback_bound",
    }
    assert body["detection"]["observed_eps"] == 6.0
    assert [t["to_state"] for t in body["transitions"]] == ["OPEN"]
    assert set(body) >= {"explanation_package", "baseline_version", "policy_version"}


def test_unknown_anomaly_id_is_404_and_a_malformed_one_is_422(db_schema) -> None:
    client = Client(READER)
    assert client.get(f"/api/v1/anomalies/{MISSING}").status_code == 404
    assert client.get("/api/v1/anomalies/not-a-uuid").status_code == 422


# ----------------------------------------------------------- WAF behavior reads
def seed_behavior():  # type: ignore[no-untyped-def]
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        ws = await make_log_source(s, inst, qradar_id=2, name="waf-1")
        bare = await make_log_source(s, inst, qradar_id=3, name="waf-2")
        report = {
            "status": "VALID", "reasons": ["ok"], "log_source_found": True,
            "total_events": 9000, "window_start": "2026-10-13T08:00:00+00:00",
            "window_end": "2026-10-13T08:15:00+00:00", "total_query": "SELECT total FROM events",
            "profile_version": 2, "validated_at": "2026-10-13T08:16:00+00:00",
            "fields": [{
                "field": "METHOD", "property_name": "Method", "status": "AVAILABLE",
                "used_by_collection": True, "populated_events": 8990, "populated_ratio": 0.99,
                "distinct_values": 4, "plausible_ratio": 1.0, "sampled_events": 8990,
                "values_truncated": False,
                "discovered_values": [{"value": "GET", "events": 5000}, {"value": "POST"}],
                "detail": None, "queries": ["SELECT Method FROM events GROUP BY Method"],
            }],
        }
        s.add_all([
            profile(inst.id, ws.id, virtual_host_property="Host", validation_report=report),
            profile(inst.id, bare.id, validation_status=WafProfileValidationStatus.UNVALIDATED,
                    validated_profile_version=None, validated_at=None, enabled=False),
        ])
        ep = endpoint(inst.id, ws.id, path="/login")
        old = endpoint(inst.id, ws.id, path="/old", is_active=False, retired_at=at(30),
                       last_seen_at=at(400))
        quiet = endpoint(inst.id, ws.id, path="/quiet")
        s.add_all([ep, old, quiet])
        await s.flush()
        b1, b2, b3 = at(20), at(15), at(10)
        s.add_all([
            source_bucket(ws.id, b1),
            source_bucket(ws.id, b2, completeness=BucketCompleteness.PARTIAL,
                          endpoint_coverage_ratio=0.6, admission_truncated=True,
                          rejected_over_quota=40, below_admission_threshold=5,
                          untracked_requests=900,
                          request_count_measurement=WafMeasurement.LOWER_BOUND,
                          action_measurement=WafTelemetryStatus.EXACT),
            source_bucket(ws.id, b3, groups_truncated=True),
        ])
        await s.flush()
        s.add_all([
            WafEndpointMetric(
                waf_endpoint_id=ep.id, bucket_start=b1, bucket_seconds=300, request_count=120,
                endpoint_coverage_ratio=0.97, completeness=BucketCompleteness.COMPLETE,
                normalizer_version=1, distinct_client_count=5, blocked_count=3, action_events=120,
                top_clients=[{"source_ip": "198.51.100.9", "requests": 70}],
                query_provenance={"queries": ["AQL"]},
            ),
            WafEndpointMetric(
                waf_endpoint_id=ep.id, bucket_start=b2, bucket_seconds=300, request_count=90,
                endpoint_coverage_ratio=0.6, completeness=BucketCompleteness.PARTIAL,
                normalizer_version=1,
            ),
            # No source bucket record for this one: the outer join must keep the row.
            WafEndpointMetric(
                waf_endpoint_id=ep.id, bucket_start=at(100), bucket_seconds=300,
                request_count=0, endpoint_coverage_ratio=1.0,
                completeness=BucketCompleteness.COMPLETE, normalizer_version=1,
            ),
        ])
        for hour, reliable in ((9, True), (10, False)):
            s.add(WafEndpointBaseline(
                waf_endpoint_id=ep.id, metric_name="request_count", weekday=2, hour=hour,
                median=100.0, mad=10.0, p05=80.0, p95=120.0, sample_count=12 if reliable else 2,
                is_reliable=reliable, baseline_version=4, normalizer_version=1,
                observations=[100.0, 101.0], completeness=0.9, excluded_sample_count=1,
                exclusion_counts={"PARTIAL": 1}, metric_semantics="sem"))
        s.add(WafEndpointBaseline(
            waf_endpoint_id=ep.id, metric_name="block_ratio", weekday=2, hour=9, median=0.1,
            mad=0.01, sample_count=12, is_reliable=True, baseline_version=1,
            normalizer_version=1, observations=[], completeness=1.0, excluded_sample_count=0,
            exclusion_counts={}))
        s.add_all([
            WafDetectorState(waf_endpoint_id=ep.id,
                             detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
                             state=AnomalyState.OPEN),
            WafDetectorState(waf_endpoint_id=ep.id,
                             detector_type=WafAnomalyType.WAF_BLOCK_RATIO_SPIKE,
                             state=AnomalyState.NORMAL),
            WafDetectorState(waf_endpoint_id=quiet.id,
                             detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
                             state=AnomalyState.NORMAL),
            WafDetectorState(waf_endpoint_id=quiet.id,
                             detector_type=WafAnomalyType.WAF_BLOCK_RATIO_SPIKE,
                             state=AnomalyState.INSUFFICIENT_DATA),
        ])
        s.add(waf(ep.id, 3))
        await s.flush()
        return dict(ws=ws.id, bare=bare.id, ep=ep.id, old=old.id, quiet=quiet.id,
                    b1=b1, b2=b2)

    return run(setup)


def window(**extra) -> dict:  # type: ignore[no-untyped-def]
    return {"since": (NOW - timedelta(hours=3)).isoformat().replace("+00:00", "Z"),
            "until": (NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z"), **extra}


SOURCE_KEYS = {
    "log_source_id", "log_source_name", "product_label", "enabled", "profile_version",
    "validation_status", "validated_profile_version", "validated_at", "validation_current",
    "virtual_host_available", "latest_bucket", "active_endpoints", "total_endpoints",
    "active_endpoint_ceiling", "admission",
}


def test_waf_sources_report_profile_coverage_and_overflow(db_schema) -> None:
    seed_behavior()
    resp = Client(READER).get("/api/v1/behavior/waf/sources", params=window())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    by_name = {i["log_source_name"]: i for i in body["items"]}
    waf1, waf2 = by_name["waf-1"], by_name["waf-2"]
    assert set(waf1) == SOURCE_KEYS
    assert waf1["validation_status"] == "VALID" and waf1["validation_current"] is True
    assert waf1["virtual_host_available"] is True and waf2["virtual_host_available"] is False
    assert waf1["active_endpoints"] == 2 and waf1["total_endpoints"] == 3
    assert waf1["active_endpoint_ceiling"] == Settings().waf_max_active_endpoints_per_source
    last = waf1["latest_bucket"]
    assert last["groups_truncated"] is True and last["coverage_measurement"] == "LOWER_BOUND"
    assert last["request_count_measurement"] == "EXACT"
    assert waf1["admission"] == {
        "buckets_in_range": 3, "buckets_admission_truncated": 1, "rejected_over_quota": 40,
        "below_admission_threshold": 5, "untracked_requests": 900,
    }
    # A source never collected: unmeasured is null, and "none refused" is not claimed.
    assert waf2["latest_bucket"] is None
    assert waf2["admission"] == {
        "buckets_in_range": 0, "buckets_admission_truncated": None, "rejected_over_quota": None,
        "below_admission_threshold": None, "untracked_requests": None,
    }
    assert waf2["validation_status"] == "UNVALIDATED" and waf2["validation_current"] is False
    assert waf2["enabled"] is False


def test_waf_source_detail_serves_counts_only(db_schema) -> None:
    w = seed_behavior()
    resp = Client(READER).get(f"/api/v1/behavior/waf/sources/{w['ws']}", params=window())
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == SOURCE_KEYS | {"validation_report"}
    report = body["validation_report"]
    assert set(report) == {
        "status", "reasons", "reason_codes", "log_source_found", "total_events",
        "window_start", "window_end", "profile_version", "validated_at", "fields",
    }
    assert set(report["fields"][0]) == {
        "field", "property_name", "status", "used_by_collection", "populated_events",
        "populated_ratio", "distinct_values", "plausible_ratio", "sampled_events",
        "values_truncated", "discovered_value_count", "detail", "detail_code", "detail_params",
    }
    assert report["reason_codes"] is None
    assert report["fields"][0]["detail_code"] is None
    assert report["fields"][0]["detail_params"] is None
    assert report["fields"][0]["discovered_value_count"] == 2
    assert report["total_events"] == 9000
    for forbidden in ("queries", "SELECT", "total_query", "discovered_values", "GET"):
        assert forbidden not in resp.text, forbidden
    # Never validated: no report, not an empty one.
    never = Client(READER).get(f"/api/v1/behavior/waf/sources/{w['bare']}", params=window())
    assert never.json()["validation_report"] is None


def test_waf_endpoint_inventory(db_schema) -> None:
    w = seed_behavior()
    client = Client(READER)
    resp = client.get(f"/api/v1/behavior/waf/sources/{w['ws']}/endpoints")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    by_path = {i["normalized_endpoint"]: i for i in body["items"]}
    assert set(by_path["/login"]) == {
        "id", "normalized_endpoint", "http_method", "virtual_host", "normalizer_version",
        "first_seen_at", "last_seen_at", "is_active", "retired_at", "tracked_since", "baseline",
        "baseline_rebuilt_at", "state", "active_anomaly_count",
    }
    login = by_path["/login"]
    assert login["baseline"] == {"cells_total": 2, "reliable_cells": 1}
    assert login["state"] == "OPEN" and login["active_anomaly_count"] == 1
    # Not every detector judged it: unjudged is not NORMAL.
    assert by_path["/quiet"]["state"] == "INSUFFICIENT_DATA"
    # Never evaluated: null, with no baseline cells counted as zero adequacy claims.
    assert by_path["/old"]["state"] is None
    assert by_path["/old"]["is_active"] is False and by_path["/old"]["retired_at"] is not None
    assert by_path["/old"]["baseline"] == {"cells_total": 0, "reliable_cells": 0}
    active = client.get(f"/api/v1/behavior/waf/sources/{w['ws']}/endpoints?active_only=true")
    assert {i["normalized_endpoint"] for i in active.json()["items"]} == {"/login", "/quiet"}
    seen = client.get(f"/api/v1/behavior/waf/sources/{w['ws']}/endpoints", params=window())
    assert {i["normalized_endpoint"] for i in seen.json()["items"]} == {"/login", "/quiet"}
    paged = client.get(f"/api/v1/behavior/waf/sources/{w['ws']}/endpoints?limit=2&offset=2")
    assert paged.json()["total"] == 3 and len(paged.json()["items"]) == 1


METRIC_KEYS = {
    "bucket_start", "bucket_seconds", "request_count", "request_count_measurement",
    "completeness", "origin", "partial", "endpoint_coverage_ratio", "in_maintenance",
    "normalizer_version", "distinct_client_count", "action_measurement",
    "response_code_measurement", "client_measurement", "blocked_count", "action_events",
    "unmapped_action_count", "response_code_events", "client_error_count",
    "server_error_count", "unclassified_response_count", "client_events", "client_sources",
    "top1_client_requests", "top5_client_requests",
}


def test_waf_metrics_keep_unmeasured_null_and_flag_partial(db_schema) -> None:
    w = seed_behavior()
    resp = Client(READER).get(f"/api/v1/behavior/waf/endpoints/{w['ep']}/metrics",
                              params=window())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    rows = body["items"]
    assert [r["bucket_start"] for r in rows] == sorted(r["bucket_start"] for r in rows)
    assert all(set(r) == METRIC_KEYS for r in rows)
    orphan, complete, partial = rows  # ascending: at(100), at(20), at(15)
    assert complete["partial"] is False and complete["completeness"] == "COMPLETE"
    assert complete["blocked_count"] == 3 and complete["distinct_client_count"] == 5
    assert complete["action_measurement"] == "NOT_COLLECTED"
    # Never collected: null, not 0.
    assert complete["unmapped_action_count"] is None
    assert complete["response_code_events"] is None and complete["client_events"] is None
    assert complete["top1_client_requests"] is None
    assert partial["partial"] is True and partial["endpoint_coverage_ratio"] == 0.6
    assert partial["request_count_measurement"] == "LOWER_BOUND"
    assert partial["action_measurement"] == "EXACT"
    assert partial["distinct_client_count"] is None and partial["blocked_count"] is None
    # No source bucket record for the row: what it would have said is unknown.
    assert orphan["request_count_measurement"] is None and orphan["in_maintenance"] is None
    assert orphan["action_measurement"] is None
    # The marker rides the source's bucket record: the two rows that have one
    # say where it came from, the orphan row cannot say and stays unknown.
    assert complete["origin"] == "LIVE" and partial["origin"] == "LIVE"
    assert orphan["origin"] is None
    assert "198.51.100.9" not in resp.text and "top_clients" not in resp.text
    assert "queries" not in resp.text and "provenance" not in resp.text
    narrow = Client(READER).get(
        f"/api/v1/behavior/waf/endpoints/{w['ep']}/metrics",
        params={"since": (NOW - timedelta(minutes=25)).isoformat().replace("+00:00", "Z"),
                "until": (NOW - timedelta(minutes=12)).isoformat().replace("+00:00", "Z")},
    ).json()
    assert narrow["total"] == 2


def test_waf_baselines_list_cells_with_reliability_and_versions(db_schema) -> None:
    w = seed_behavior()
    client = Client(READER)
    body = client.get(f"/api/v1/behavior/waf/endpoints/{w['ep']}/baselines").json()
    assert body["total"] == 3
    cell = body["items"][-1]
    assert body["items"][0]["metric_name"] == "block_ratio"
    assert set(cell) == {
        "metric_name", "weekday", "hour", "median", "mad", "p05", "p95", "sample_count",
        "is_reliable", "completeness", "baseline_version", "normalizer_version",
        "excluded_sample_count", "metric_semantics", "window_start", "window_end",
        "computed_at",
        "numerator_sum", "denominator_sum",
    }
    reliability = {(c["metric_name"], c["hour"]): c["is_reliable"] for c in body["items"]}
    assert reliability == {("request_count", 9): True, ("request_count", 10): False,
                           ("block_ratio", 9): True}
    # Raw training observations are not served.
    text = json.dumps(body)
    assert "observations" not in text and "exclusion_counts" not in text
    only = client.get(f"/api/v1/behavior/waf/endpoints/{w['ep']}/baselines?metric=block_ratio")
    assert only.json()["total"] == 1
    empty = client.get(f"/api/v1/behavior/waf/endpoints/{w['quiet']}/baselines").json()
    assert empty == {"items": [], "total": 0, "limit": 200, "offset": 0}


def test_waf_baselines_serve_the_pooled_sums_behind_ratio_cells(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        _inst, _fw, _ws, ep = await world(s, a_rows=0, w_rows=0)
        s.add_all([
            # A ratio metric pools its numerator and denominator over the training
            # buckets, so the detector's expected proportion is their quotient.
            WafEndpointBaseline(
                waf_endpoint_id=ep.id, metric_name="block_ratio", weekday=2, hour=9,
                median=0.05, mad=0.01, sample_count=12, is_reliable=True, baseline_version=1,
                normalizer_version=1, observations=[], completeness=1.0,
                excluded_sample_count=0, exclusion_counts={},
                numerator_sum=12.0, denominator_sum=480.0),
            # A count metric has no proportion to pool: both sums stay NULL.
            WafEndpointBaseline(
                waf_endpoint_id=ep.id, metric_name="request_count", weekday=2, hour=9,
                median=100.0, mad=10.0, sample_count=12, is_reliable=True, baseline_version=1,
                normalizer_version=1, observations=[], completeness=1.0,
                excluded_sample_count=0, exclusion_counts={}),
        ])
        await s.flush()
        return ep.id

    ep_id = run(setup)
    body = Client(READER).get(f"/api/v1/behavior/waf/endpoints/{ep_id}/baselines").json()
    assert body["total"] == 2
    by_metric = {c["metric_name"]: c for c in body["items"]}
    ratio = by_metric["block_ratio"]
    assert ratio["numerator_sum"] == 12.0
    assert ratio["denominator_sum"] == 480.0
    count = by_metric["request_count"]
    assert count["numerator_sum"] is None
    assert count["denominator_sum"] is None


# ----------------------------------------------------------- guards on all routes
NEW_ROUTES = [
    "/api/v1/behavior/waf/sources",
    "/api/v1/behavior/waf/sources/{src}",
    "/api/v1/behavior/waf/sources/{src}/endpoints",
    "/api/v1/behavior/waf/endpoints/{ep}/metrics",
    "/api/v1/behavior/waf/endpoints/{ep}/baselines",
]


@pytest.mark.parametrize("route", [r for r in NEW_ROUTES if "{" in r])
def test_unknown_ids_are_404(db_schema, route: str) -> None:
    url = route.replace("{src}", MISSING).replace("{ep}", MISSING)
    assert Client(READER).get(url).status_code == 404


@pytest.mark.parametrize("route", NEW_ROUTES)
def test_read_anomalies_is_required(db_schema, route: str) -> None:
    w = seed_behavior()
    url = route.replace("{src}", str(w["ws"])).replace("{ep}", str(w["ep"]))
    assert Client(NOBODY).get(url).status_code == 403
    assert Client(ANOMALY_READER).get(url).status_code == 200
    assert Client(READER).get(url).status_code == 200
    assert Client(NOBODY).get("/api/v1/anomalies").status_code == 403
    assert Client(NOBODY).get(f"/api/v1/anomalies/{MISSING}").status_code == 403


@pytest.mark.parametrize("route", [NEW_ROUTES[0], NEW_ROUTES[1], NEW_ROUTES[3]])
def test_range_beyond_the_ceiling_is_422(db_schema, route: str) -> None:
    w = seed_behavior()
    url = route.replace("{src}", str(w["ws"])).replace("{ep}", str(w["ep"]))
    too_wide = {"since": (NOW - timedelta(days=400)).isoformat().replace("+00:00", "Z"),
                "until": NOW.isoformat().replace("+00:00", "Z")}
    assert Client(READER).get(url, params=too_wide).status_code == 422
    backwards = {"since": NOW.isoformat().replace("+00:00", "Z"),
                 "until": (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")}
    assert Client(READER).get(url, params=backwards).status_code == 422


def test_inventory_range_is_validated_when_given(db_schema) -> None:
    w = seed_behavior()
    too_wide = {"since": (NOW - timedelta(days=400)).isoformat().replace("+00:00", "Z"),
                "until": NOW.isoformat().replace("+00:00", "Z")}
    url = f"/api/v1/behavior/waf/sources/{w['ws']}/endpoints"
    assert Client(READER).get(url, params=too_wide).status_code == 422


def test_unified_list_range_ceiling_is_unchanged(db_schema) -> None:
    too_wide = {"since": (NOW - timedelta(days=400)).isoformat().replace("+00:00", "Z"),
                "until": NOW.isoformat().replace("+00:00", "Z")}
    assert Client(READER).get("/api/v1/anomalies", params=too_wide).status_code == 422


def test_limit_is_clamped_to_the_page_size_ceiling(db_schema) -> None:
    w = seed_behavior()
    client = Client(READER)
    settings = get_settings().model_copy(update={"api_max_page_size": 2})
    client.http.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    ep = w["ep"]
    for url, count in (
        ("/api/v1/behavior/waf/sources?limit=50", 2),
        (f"/api/v1/behavior/waf/sources/{w['ws']}/endpoints?limit=50", 2),
        (f"/api/v1/behavior/waf/endpoints/{ep}/metrics?limit=50", 2),
        (f"/api/v1/behavior/waf/endpoints/{ep}/baselines?limit=50", 2),
    ):
        body = client.get(url, params=window() if "baselines" not in url else None).json()
        assert body["limit"] == 2 and len(body["items"]) == count, url
        assert body["total"] >= 2
    assert client.get("/api/v1/anomalies?limit=50").json()["items"] != []
    assert len(client.get("/api/v1/anomalies?limit=50").json()["items"]) <= 2
    assert client.get("/api/v1/behavior/waf/sources?limit=201").status_code == 422


def test_behavior_statement_counts_do_not_grow_with_the_data(db_schema) -> None:
    w = seed_behavior()
    client = Client(READER)
    counts: dict[str, tuple[int, int]] = {}
    urls = {
        "sources": "/api/v1/behavior/waf/sources",
        "source": f"/api/v1/behavior/waf/sources/{w['ws']}",
        "endpoints": f"/api/v1/behavior/waf/sources/{w['ws']}/endpoints",
        "metrics": f"/api/v1/behavior/waf/endpoints/{w['ep']}/metrics",
        "baselines": f"/api/v1/behavior/waf/endpoints/{w['ep']}/baselines",
    }
    before = {}
    for name, url in urls.items():
        assert client.get(url, params=window()).status_code == 200
        before[name] = client.statements

    async def grow(s):  # type: ignore[no-untyped-def]
        ep = await s.get(WafEndpoint, w["ep"])
        inst = ep.instance_id
        for i in range(8):
            ls = await make_log_source(s, _Instance(inst), qradar_id=100 + i, name=f"waf-x{i}")
            s.add(profile(inst, ls.id))
        s.add_all([endpoint(inst, ep.log_source_id, path=f"/g{i}") for i in range(25)])
        await s.flush()
        for i in range(25):
            s.add(WafEndpointMetric(
                waf_endpoint_id=w["ep"], bucket_start=at(200 + 5 * i), bucket_seconds=300,
                request_count=1, endpoint_coverage_ratio=1.0,
                completeness=BucketCompleteness.COMPLETE, normalizer_version=1))

    run(grow)
    for name, url in urls.items():
        assert client.get(url, params=window()).status_code == 200
        counts[name] = (before[name], client.statements)
    for name, (small, large) in counts.items():
        assert small == large, (name, small, large)
    # Pinned so a new per-row query cannot hide behind "still constant".
    assert {n: c[0] for n, c in counts.items()} == {
        "sources": 6, "source": 5, "endpoints": 6, "metrics": 3, "baselines": 3,
    }


class _Instance:
    def __init__(self, id_: uuid.UUID) -> None:
        self.id = id_
