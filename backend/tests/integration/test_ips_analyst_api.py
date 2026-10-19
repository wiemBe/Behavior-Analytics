"""IPS analyst API (T-085): the unified anomaly list, the detail fallback, and
the IPS behavior reads.

DB-gated. Everything is seeded through committed sessions and read back over
HTTP, so the tests see exactly what an analyst's client sees. The seed rows
carry source IPs inside the stored, bounded contributor lists precisely so the
tests can assert those addresses never reach a response.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    EvidenceStatus,
    IpsAnomalyType,
    IpsDimension,
    IpsPreset,
    IpsProfileValidationStatus,
    Severity,
    WafAnomalyType,
    WafMeasurement,
    WafProfileValidationStatus,
)
from app.models.ips import (
    IpsAnomaly,
    IpsAnomalyTransition,
    IpsDetectorState,
    IpsEntity,
    IpsEntityBaseline,
    IpsEntityMetric,
    IpsSourceBaseline,
    IpsSourceBucket,
    IpsSourceProfile,
)
from app.models.log_source import LogSourceAnomaly
from app.models.waf import WafAnomaly, WafEndpoint, WafSourceProfile
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

#: A source address that exists only inside stored, bounded contributor lists.
CONTRIBUTOR_IP = "203.0.113.77"


def at(minutes: int) -> datetime:
    """A detection time `minutes` before the fixed NOW."""
    return NOW - timedelta(minutes=minutes)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def window(**extra):  # type: ignore[no-untyped-def]
    return {
        "since": _iso(NOW - timedelta(hours=3)),
        "until": _iso(NOW + timedelta(hours=1)),
        **extra,
    }


# ------------------------------------------------------------------ factories
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


def endpoint(instance_id, source_id, path="/login", **kw) -> WafEndpoint:  # type: ignore[no-untyped-def]
    defaults = dict(
        instance_id=instance_id, log_source_id=source_id, virtual_host=None,
        normalized_endpoint=path, http_method="POST",
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        first_seen_at=at(600), last_seen_at=at(1), tracked_since=at(600), is_active=True,
    )
    defaults.update(kw)
    return WafEndpoint(**defaults)


def waf_profile(instance_id, source_id, **kw) -> WafSourceProfile:  # type: ignore[no-untyped-def]
    defaults = dict(
        instance_id=instance_id, log_source_id=source_id, enabled=True, profile_version=2,
        url_property="URL", method_property="Method", product_label="lab-waf",
        validation_status=WafProfileValidationStatus.VALID, validated_profile_version=2,
        validated_at=at(60), validation_report={},
    )
    defaults.update(kw)
    return WafSourceProfile(**defaults)


def ips_profile(instance_id, source_id, **kw) -> IpsSourceProfile:  # type: ignore[no-untyped-def]
    defaults = dict(
        instance_id=instance_id, log_source_id=source_id, enabled=True, profile_version=2,
        product_label="lab-ips", preset=IpsPreset.CISCO_FTD, mapping={}, action_value_map={},
        validation_status=IpsProfileValidationStatus.VALID, validated_profile_version=2,
        validated_at=at(60), validation_report={},
    )
    defaults.update(kw)
    return IpsSourceProfile(**defaults)


def entity(instance_id, source_id, dimension=IpsDimension.DESTINATION_PORT, value_key="443/tcp",
           **kw) -> IpsEntity:  # type: ignore[no-untyped-def]
    defaults = dict(
        instance_id=instance_id, log_source_id=source_id, dimension=dimension,
        value_key=value_key, first_seen_at=at(600), last_seen_at=at(1), active=True,
        normalizer_version=1,
    )
    defaults.update(kw)
    return IpsEntity(**defaults)


def source_bucket(source_id, bucket_start, **kw) -> IpsSourceBucket:  # type: ignore[no-untyped-def]
    defaults = dict(
        log_source_id=source_id, bucket_start=bucket_start, bucket_seconds=3600,
        total_events=5000, total_records=5100,
        destination_port_measurement="EXACT", destination_port_coverage_ratio=0.98,
        destination_ip_measurement="EXACT", destination_ip_coverage_ratio=0.99,
        signature_measurement="EXACT", signature_coverage_ratio=0.97,
        source_ip_measurement="EXACT", source_ip_coverage_ratio=1.0,
        port_fanout_measurement="EXACT",
        action_measurement="NOT_COLLECTED",
        source_events=4800, distinct_sources=40, top1_events=700, top5_events=1200,
        max_distinct_ports=12,
        contributors={"SOURCE_IP": [{"source_ip": CONTRIBUTOR_IP, "events": 600}]},
        admitted={"DESTINATION_PORT": 30, "DESTINATION_IP": 10, "SIGNATURE": 5},
        below_admission_threshold={"DESTINATION_PORT": 2},
        rejected_over_quota={"DESTINATION_PORT": 4},
        untracked_entities={}, untracked_events={},
        admission_truncated={"DESTINATION_PORT": False, "DESTINATION_IP": False,
                             "SIGNATURE": False},
        profile_version=2, normalizer_version=1, in_maintenance=False,
        collected_at=bucket_start, query_provenance={"queries": ["SELECT sourceip FROM events"]},
    )
    defaults.setdefault("origin", "LIVE")
    defaults.update(kw)
    return IpsSourceBucket(**defaults)


#: The evidence of a port-entity spike, shaped exactly as `AnomalyEvidence.to_dict`
#: serializes it (`app/ips/detector.py`), including the bounded contributor list.
SPIKE_DETAILS = {
    "anomaly_type": "IPS_PORT_RATE_SPIKE", "signal": "ANOMALOUS", "severity": "MEDIUM",
    "observed_value": 812.0, "expected_value": 100.0, "baseline_low": 80.0,
    "baseline_high": 120.0, "deviation_score": 21.4, "threshold": 3.5, "sample_count": 12,
    "interval_start": "2026-10-13T09:00:00+00:00",
    "interval_end": "2026-10-13T10:00:00+00:00", "confidence": 0.9,
    "reason": "at least 812 events/bucket for destination port 443/tcp is above the median",
    "insufficient_data": False,
    "extra": {
        "evidence_version": 1, "detector_type": "IPS_PORT_RATE_SPIKE",
        "entity": {"dimension": "DESTINATION_PORT", "value_key": "443/tcp"},
        "observed": {"metric": "entity_events", "events": 812, "measurement": "LOWER_BOUND"},
        "expected": {"expected": 100.0, "median": 100.0, "p05": 80.0, "p95": 120.0,
                     "samples": 12, "completeness": 0.95, "baseline_version": 3},
        "absolute_delta": 712.0, "ratio": 8.12, "robust_score_status": "OK",
        "robust_z": 21.4, "observed_measurement": "LOWER_BOUND",
        "caveat": "More events on a port describe traffic volume, not intent.",
        "contributors": {"SOURCE_IP": [{"source_ip": CONTRIBUTOR_IP, "events": 600}]},
        "provenance": {"record": "ips_source_bucket", "bucket_start": "2026-10-13T09:00:00"},
    },
}

#: The evidence of a source-concentration spike (`app/ips/behavior.py`).
SHARE_DETAILS = {
    "anomaly_type": "IPS_SOURCE_CONCENTRATION_SPIKE", "signal": "ANOMALOUS",
    "severity": "MEDIUM", "observed_value": 0.7, "expected_value": 0.25,
    "baseline_low": 0.2, "baseline_high": 0.3, "deviation_score": 9.0, "threshold": 3.5,
    "sample_count": 12, "interval_start": "2026-10-13T09:00:00+00:00",
    "interval_end": "2026-10-13T10:00:00+00:00", "confidence": 0.85,
    "reason": "the largest source IP sent 700 of 1000 events (70.0%)",
    "insufficient_data": False,
    "extra": {
        "evidence_version": 1, "detector_type": "IPS_SOURCE_CONCENTRATION_SPIKE",
        "observed": {"metric": "top1_source_share", "proportion": 0.7, "numerator": 700,
                     "denominator": 1000, "top5_share": 0.9, "distinct_sources": 40,
                     "measurement": {"numerator": "EXACT", "denominator": "EXACT"}},
        "expected": {"expected": 0.25, "completeness": 1.0, "baseline_version": 2,
                     "numerator_sum": 2500.0, "denominator_sum": 10000.0},
        "percentage_point_change": 45.0, "absolute_delta": 0.45, "ratio": 2.8,
        "robust_score_status": "OK", "robust_z": 9.0, "observed_measurement": "EXACT",
        "caveat": "A dominant source IP describes traffic composition, not intent.",
        "contributors": {"SOURCE_IP": [{"source_ip": CONTRIBUTOR_IP, "events": 700}]},
    },
}


def ips_incident(subject_kind, minutes, detector_type, *, entity_id=None, log_source_id=None,
                 **kw) -> IpsAnomaly:  # type: ignore[no-untyped-def]
    defaults = dict(
        subject_kind=subject_kind, ips_entity_id=entity_id, log_source_id=log_source_id,
        detector_type=detector_type,
        fingerprint=f"{detector_type.value}-{uuid.uuid4().hex[:8]}",
        severity=Severity.MEDIUM, state=AnomalyState.OPEN, detected_at=at(minutes),
        anomaly_start=at(minutes), evidence_status=EvidenceStatus.NOT_REQUESTED,
        profile_version=2, normalizer_version=1, observed_measurement=WafMeasurement.EXACT,
        details=SPIKE_DETAILS if subject_kind == "IPS_ENTITY" else SHARE_DETAILS,
    )
    defaults.update(kw)
    return IpsAnomaly(**defaults)


def entity_baseline(ips_entity_id, hour, **kw) -> IpsEntityBaseline:  # type: ignore[no-untyped-def]
    defaults = dict(
        ips_entity_id=ips_entity_id, metric_name="entity_events", weekday=2, hour=hour,
        median=100.0, mad=10.0, p05=80.0, p95=120.0, sample_count=12, is_reliable=True,
        completeness=0.9, baseline_version=2, profile_version=2, normalizer_version=1,
        observations=[100.0, 101.0], excluded_sample_count=1, exclusion_counts={"PARTIAL": 1},
        metric_semantics="ips/entity_events/v1", window_start=at(600), window_end=at(1),
        computed_at=at(1),
    )
    defaults.update(kw)
    return IpsEntityBaseline(**defaults)


async def ips_world(s):  # type: ignore[no-untyped-def]
    """One Phase A, one WAF and one IPS source, with every family's rows chosen
    so each filter splits the kinds and the kinds apart."""
    inst = await make_instance(s)
    inst_other = await make_instance(s)
    fw = await make_log_source(s, inst_other, qradar_id=1, name="fw-1")
    ws = await make_log_source(s, inst, qradar_id=2, name="waf-1")
    isrc = await make_log_source(s, inst, qradar_id=3, name="ips-1")
    bare = await make_log_source(s, inst, qradar_id=4, name="ips-bare")
    quiet = await make_log_source(s, inst, qradar_id=5, name="ips-quiet")
    s.add_all([
        waf_profile(inst.id, ws.id),
        ips_profile(inst.id, isrc.id),
        # A profile that never collected: enabled, but nothing measured yet.
        ips_profile(inst.id, quiet.id),
    ])
    ep = endpoint(inst.id, ws.id)
    port = entity(inst.id, isrc.id, IpsDimension.DESTINATION_PORT, "443/tcp",
                  display_name="443/tcp")
    dst = entity(inst.id, isrc.id, IpsDimension.DESTINATION_IP, "10.10.20.15")
    sig = entity(inst.id, isrc.id, IpsDimension.SIGNATURE, "qid:5000123",
                 display_name="Exploit Kit Traffic")
    retired = entity(inst.id, isrc.id, IpsDimension.DESTINATION_PORT, "23/tcp",
                     active=False, retired_at=at(10), last_seen_at=at(400))
    s.add_all([ep, port, dst, sig, retired])
    await s.flush()

    # Buckets newest first in naming: b_new is the latest collected.
    b_new, b_sparse, b_old = at(20), at(30), at(40)
    s.add_all([
        source_bucket(isrc.id, b_new, destination_port_coverage_lower_bound=True),
        # A bucket whose source-IP search never ran: those facts stay unknown,
        # never zero, and port admission recorded losses this time.
        source_bucket(
            isrc.id, b_sparse,
            source_ip_measurement="NOT_COLLECTED", source_ip_coverage_ratio=None,
            source_events=None, distinct_sources=None, top1_events=None, top5_events=None,
            max_distinct_ports=None,
            rejected_over_quota={"DESTINATION_PORT": 4},
            below_admission_threshold={"DESTINATION_PORT": 2},
            admission_truncated={"DESTINATION_PORT": True, "DESTINATION_IP": False,
                                 "SIGNATURE": False},
        ),
        source_bucket(isrc.id, b_old),
    ])
    await s.flush()
    # Three buckets for the port entity, the oldest-joined pair first and the
    # last with no bucket record at all.
    s.add_all([
        IpsEntityMetric(ips_entity_id=port.id, bucket_start=b_old, bucket_seconds=3600,
                        events=812),
        IpsEntityMetric(ips_entity_id=port.id, bucket_start=b_sparse, bucket_seconds=3600,
                        events=90),
        IpsEntityMetric(ips_entity_id=port.id, bucket_start=at(10), bucket_seconds=3600,
                        events=5),
    ])
    # Cells of the port entity: two current, one reliable; plus one stale under
    # another normalizer version, which proves nothing about the entity.
    s.add_all([
        entity_baseline(port.id, 9),
        entity_baseline(port.id, 10, is_reliable=False, sample_count=2),
        entity_baseline(port.id, 11, normalizer_version=99, baseline_version=1),
        entity_baseline(sig.id, 9, median=40.0),
    ])
    # Source-level cells: a share metric pools its sums, a count metric does not.
    s.add_all([
        IpsSourceBaseline(
            log_source_id=isrc.id, metric_name="top1_source_share", weekday=2, hour=9,
            median=0.25, mad=0.02, sample_count=12, is_reliable=True, completeness=1.0,
            baseline_version=2, profile_version=2, normalizer_version=1, observations=[],
            excluded_sample_count=0, exclusion_counts={},
            metric_semantics="ips/top1_source_share/v1", numerator_sum=2500.0,
            denominator_sum=10000.0,
        ),
        IpsSourceBaseline(
            log_source_id=isrc.id, metric_name="max_distinct_ports", weekday=2, hour=9,
            median=18.0, mad=2.0, sample_count=12, is_reliable=True, completeness=1.0,
            baseline_version=2, profile_version=2, normalizer_version=1, observations=[],
            excluded_sample_count=0, exclusion_counts={},
            metric_semantics="ips/max_distinct_ports/v1",
        ),
    ])
    s.add_all([
        IpsDetectorState(subject_kind="IPS_ENTITY", ips_entity_id=port.id,
                         detector_type=IpsAnomalyType.IPS_PORT_RATE_SPIKE,
                         state=AnomalyState.OPEN),
        IpsDetectorState(subject_kind="IPS_ENTITY", ips_entity_id=port.id,
                         detector_type=IpsAnomalyType.IPS_NEW_DESTINATION_PORT,
                         state=AnomalyState.INSUFFICIENT_DATA),
        IpsDetectorState(subject_kind="IPS_ENTITY", ips_entity_id=sig.id,
                         detector_type=IpsAnomalyType.IPS_SIGNATURE_BURST,
                         state=AnomalyState.NORMAL),
    ])
    await s.flush()

    a_open = ips_incident(
        "IPS_ENTITY", 2, IpsAnomalyType.IPS_PORT_RATE_SPIKE, entity_id=port.id,
        severity=Severity.HIGH, observed_value=812.0, expected_value=100.0,
        deviation_ratio=8.12, robust_z=21.4, absolute_delta=712.0, baseline_version=3,
        observed_measurement=WafMeasurement.LOWER_BOUND,
    )
    a_candidate = ips_incident(
        "IPS_ENTITY", 6, IpsAnomalyType.IPS_NEW_DESTINATION_PORT, entity_id=port.id,
        state=AnomalyState.CANDIDATE, severity=Severity.MEDIUM,
    )
    a_sig = ips_incident(
        "IPS_ENTITY", 10, IpsAnomalyType.IPS_SIGNATURE_BURST, entity_id=sig.id,
        state=AnomalyState.RESOLVED, severity=Severity.LOW,
        evidence_status=EvidenceStatus.COMPLETE, resolved_at=at(5),
    )
    a_source = ips_incident(
        "IPS_SOURCE", 3, IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE,
        log_source_id=isrc.id, observed_value=0.7, expected_value=0.25,
        deviation_ratio=2.8, baseline_version=2,
    )
    s.add_all([
        phase_a(fw.id, 0, state=AnomalyState.OPEN, severity=Severity.HIGH),
        phase_a(fw.id, 4, state=AnomalyState.RESOLVED, severity=Severity.LOW,
                anomaly_type=AnomalyType.VOLUME_DROP, resolved_at=at(2),
                evidence_status=EvidenceStatus.COMPLETE),
        waf(ep.id, 1, state=AnomalyState.OPEN, severity=Severity.HIGH),
        waf(ep.id, 5, state=AnomalyState.RESOLVED, severity=Severity.LOW,
            detector_type=WafAnomalyType.WAF_BLOCK_RATIO_SPIKE,
            evidence_status=EvidenceStatus.COMPLETE),
        a_open, a_candidate, a_sig, a_source,
    ])
    await s.flush()
    s.add_all([
        IpsAnomalyTransition(ips_anomaly_id=a_open.id, from_state=None,
                             to_state=AnomalyState.CANDIDATE, occurred_at=at(3),
                             actor="ips-anomaly-engine"),
        IpsAnomalyTransition(ips_anomaly_id=a_open.id,
                             from_state=AnomalyState.CANDIDATE,
                             to_state=AnomalyState.OPEN, occurred_at=at(2),
                             reason="2 consecutive buckets", actor="ips-anomaly-engine",
                             observed_value=812.0, expected_value=100.0),
        IpsAnomalyTransition(ips_anomaly_id=a_source.id, from_state=None,
                             to_state=AnomalyState.OPEN, occurred_at=at(3),
                             actor="ips-anomaly-engine"),
    ])
    await s.flush()
    return dict(
        inst=inst.id, inst_other=inst_other.id, fw=fw.id, ws=ws.id, isrc=isrc.id,
        bare=bare.id, quiet=quiet.id, ep=ep.id, port=port.id, dst=dst.id, sig=sig.id,
        retired=retired.id, a_open=a_open.id, a_source=a_source.id,
        b_new=b_new, b_sparse=b_sparse, b_old=b_old,
    )


def world():  # type: ignore[no-untyped-def]
    return run(ips_world)


# ---------------------------------------------------------------- unified list
def test_mixed_list_interleaves_three_kinds_in_one_order_across_pages(db_schema) -> None:
    world()
    full = Client(READER).get("/api/v1/anomalies?limit=200").json()
    assert full["total"] == 8
    kinds = [i["subject_kind"] for i in full["items"]]
    assert kinds == [
        "LOG_SOURCE", "WAF_ENDPOINT", "IPS_ENTITY", "IPS_SOURCE",
        "LOG_SOURCE", "WAF_ENDPOINT", "IPS_ENTITY", "IPS_ENTITY",
    ]
    stamps = [i["detected_at"] for i in full["items"]]
    assert stamps == sorted(stamps, reverse=True)

    paged: list[str] = []
    for offset in (0, 3, 6):
        page = Client(READER).get(f"/api/v1/anomalies?limit=3&offset={offset}").json()
        assert page["total"] == 8
        paged += [i["id"] for i in page["items"]]
    assert paged == [i["id"] for i in full["items"]]
    assert len(set(paged)) == 8


def test_ips_item_names_its_subject_and_the_other_kinds_have_none(db_schema) -> None:
    w = world()
    items = Client(READER).get("/api/v1/anomalies?limit=200").json()["items"]
    by_id = {i["id"]: i for i in items}
    port_item = by_id[str(w["a_open"])]
    assert port_item["subject_kind"] == "IPS_ENTITY"
    assert port_item["anomaly_type"] == "IPS_PORT_RATE_SPIKE"
    assert port_item["log_source_id"] == str(w["isrc"])
    assert port_item["log_source_name"] == "ips-1"
    assert port_item["observed_measurement"] == "LOWER_BOUND"
    assert port_item["ips_entity"] == {
        "id": str(w["port"]), "dimension": "DESTINATION_PORT", "value_key": "443/tcp",
        "display_name": "443/tcp",
    }
    source_item = next(
        i for i in items if i["subject_kind"] == "IPS_SOURCE"
    )
    assert source_item["ips_entity"] is None
    assert source_item["log_source_id"] == str(w["isrc"])
    assert source_item["anomaly_type"] == "IPS_SOURCE_CONCENTRATION_SPIKE"
    for i in items:
        if i["subject_kind"] != "IPS_ENTITY":
            assert i["ips_entity"] is None
        assert set(i) >= {"ips_entity", "waf_endpoint"}


def shape(**params) -> tuple[int, int, int, int]:  # type: ignore[no-untyped-def]
    """(Phase A, WAF, IPS entity, IPS source) counts for a filter set."""
    body = Client(READER).get("/api/v1/anomalies", params={"limit": 200, **params}).json()
    kinds = [i["subject_kind"] for i in body["items"]]
    assert body["total"] == len(kinds)
    return (
        kinds.count("LOG_SOURCE"), kinds.count("WAF_ENDPOINT"),
        kinds.count("IPS_ENTITY"), kinds.count("IPS_SOURCE"),
    )


def test_each_filter_applies_to_the_ips_branch(db_schema) -> None:
    w = world()
    assert shape() == (2, 2, 3, 1)
    assert shape(state="OPEN") == (1, 1, 1, 1)
    assert shape(state="RESOLVED") == (1, 1, 1, 0)
    assert shape(severity="HIGH") == (1, 1, 1, 0)
    assert shape(severity="LOW") == (1, 1, 1, 0)
    assert shape(evidence_status="COMPLETE") == (1, 1, 1, 0)
    assert shape(active_only="true") == (1, 1, 2, 1)
    assert shape(log_source_id=str(w["isrc"])) == (0, 0, 3, 1)
    assert shape(log_source_id=str(w["fw"])) == (2, 0, 0, 0)
    assert shape(instance_id=str(w["inst"])) == (0, 2, 3, 1)
    assert shape(instance_id=str(w["inst_other"])) == (2, 0, 0, 0)
    assert shape(since=_iso(at(5))) == (2, 2, 1, 1)
    assert shape(until=_iso(at(5))) == (0, 0, 2, 0)


def test_filters_that_cannot_apply_to_ips_exclude_it(db_schema) -> None:
    w = world()
    # The other families' subject filters exclude the IPS branch.
    assert shape(subject_kind="LOG_SOURCE") == (2, 0, 0, 0)
    assert shape(subject_kind="WAF_ENDPOINT") == (0, 2, 0, 0)
    # A WAF endpoint id can only match WAF rows; IPS is excluded, not ignored.
    assert shape(waf_endpoint_id=str(w["ep"])) == (0, 2, 0, 0)
    assert shape(waf_endpoint_id=MISSING) == (0, 0, 0, 0)
    # An IPS entity id excludes the other families, as `waf_endpoint_id` does.
    assert shape(ips_entity_id=str(w["port"])) == (0, 0, 2, 0)
    assert shape(ips_entity_id=str(w["dst"])) == (0, 0, 0, 0)
    assert shape(ips_entity_id=MISSING) == (0, 0, 0, 0)
    # A type of one family excludes the other two.
    assert shape(anomaly_type="IPS_SIGNATURE_BURST") == (0, 0, 1, 0)
    assert shape(anomaly_type="IPS_SOURCE_CONCENTRATION_SPIKE") == (0, 0, 0, 1)
    assert shape(anomaly_type="VOLUME_SPIKE") == (1, 0, 0, 0)
    assert shape(anomaly_type="WAF_REQUEST_RATE_SPIKE") == (0, 1, 0, 0)


def test_ips_filters_combine_with_the_common_ones(db_schema) -> None:
    w = world()
    assert shape(subject_kind="IPS_ENTITY", log_source_id=str(w["isrc"])) == (0, 0, 3, 0)
    assert shape(subject_kind="IPS_SOURCE", active_only="true") == (0, 0, 0, 1)
    assert shape(ips_entity_id=str(w["port"]), state="OPEN") == (0, 0, 1, 0)
    assert shape(ips_entity_id=str(w["port"]), severity="LOW") == (0, 0, 0, 0)
    # Contradictory filters are an empty result, not a fallback to a family.
    assert shape(subject_kind="LOG_SOURCE", anomaly_type="IPS_SIGNATURE_BURST") == (
        0, 0, 0, 0
    )
    assert shape(subject_kind="IPS_SOURCE", ips_entity_id=str(w["port"])) == (0, 0, 0, 0)
    assert shape(waf_endpoint_id=str(w["ep"]), ips_entity_id=str(w["port"])) == (0, 0, 0, 0)
    # An unknown type is a client error, never silently "everything".
    assert Client(READER).get("/api/v1/anomalies?anomaly_type=NOPE").status_code == 422
    assert Client(READER).get(
        "/api/v1/anomalies", params={"subject_kind": "IPS_WHAT"}
    ).status_code == 422


def test_pagination_across_a_tie_keeps_every_row(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        w = await ips_world(s)
        s.add_all([
            ips_incident("IPS_ENTITY", 7, IpsAnomalyType.IPS_PORT_RATE_SPIKE,
                         entity_id=w["port"])
            for _ in range(5)
        ])
        await s.flush()
        return w["port"]

    port_id = run(setup)
    client = Client(READER)
    full = client.get(
        "/api/v1/anomalies", params={"limit": 200, "ips_entity_id": str(port_id)}
    ).json()
    # The world's two rows at minutes 2 and 6, plus five sharing minute 7.
    assert full["total"] == 7
    tied = [i["id"] for i in full["items"] if i["detected_at"] == _iso(at(7))]
    assert len(tied) == 5

    paged: list[str] = []
    for offset in (0, 2, 4, 6):
        page = client.get(
            "/api/v1/anomalies",
            params={"limit": 2, "offset": offset, "ips_entity_id": str(port_id)},
        ).json()
        paged += [i["id"] for i in page["items"]]
    assert sorted(paged) == sorted(i["id"] for i in full["items"])
    assert len(set(paged)) == 7


def test_unified_list_statement_count_is_two_at_any_size(db_schema) -> None:
    w = world()
    client = Client(READER)
    assert client.get("/api/v1/anomalies?limit=200").json()["total"] == 8
    small = client.statements

    async def more(s):  # type: ignore[no-untyped-def]
        inst = await s.get(IpsEntity, w["port"])
        s.add_all([
            entity(inst.instance_id, inst.log_source_id, IpsDimension.DESTINATION_IP,
                   f"10.10.30.{i}") for i in range(1, 30)
        ])
        await s.flush()
        s.add_all([
            ips_incident("IPS_SOURCE", 100 + i,
                         IpsAnomalyType.IPS_PORT_FANOUT_SPIKE,
                         log_source_id=inst.log_source_id)
            for i in range(25)
        ])
    run(more)
    body = client.get("/api/v1/anomalies?limit=200").json()
    assert body["total"] == 33
    assert len(body["items"]) == 33
    assert client.statements == small, (small, client.statements)
    # Pinned so a per-family or per-row query cannot hide behind "still constant".
    assert small == 2, small


# ---------------------------------------------------------------------- detail
def test_entity_incident_detail_carries_the_projection_and_transitions(db_schema) -> None:
    w = world()
    resp = Client(READER).get(f"/api/v1/anomalies/{w['a_open']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["subject_kind"] == "IPS_ENTITY"
    assert body["anomaly_type"] == "IPS_PORT_RATE_SPIKE"
    assert body["log_source_id"] == str(w["isrc"])
    assert body["log_source_name"] == "ips-1"
    assert body["observed_measurement"] == "LOWER_BOUND"
    assert body["baseline_version"] == 3
    assert body["ips_entity"]["id"] == str(w["port"])
    assert [t["to_state"] for t in body["transitions"]] == ["CANDIDATE", "OPEN"]
    # An IPS incident has no evidence package; the field is null, not fabricated.
    assert body["explanation_package"] is None
    detection = body["detection"]
    assert detection["observed_events"] == 812.0
    assert detection["expected_events"] == 100.0
    assert detection["expected_low"] == 80.0 and detection["expected_high"] == 120.0
    assert detection["baseline_version"] == 3
    assert detection["observed_measurement"] == "LOWER_BOUND"
    assert detection["ratio"] == 8.12 and detection["robust_z"] == 21.4
    assert detection["caveat"].startswith("More events")
    assert detection["observed_proportion"] is None
    # Nothing outside the whitelist leaks: no contributor IPs, no provenance.
    for forbidden in (CONTRIBUTOR_IP, "contributors", "provenance", "queries", "SELECT"):
        assert forbidden not in resp.text, forbidden


def test_source_incident_detail_names_the_log_source_and_no_entity(db_schema) -> None:
    w = world()
    resp = Client(READER).get(f"/api/v1/anomalies/{w['a_source']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["subject_kind"] == "IPS_SOURCE"
    assert body["ips_entity"] is None
    assert body["log_source_id"] == str(w["isrc"])
    assert body["log_source_name"] == "ips-1"
    assert body["anomaly_type"] == "IPS_SOURCE_CONCENTRATION_SPIKE"
    assert body["explanation_package"] is None
    detection = body["detection"]
    assert detection["observed_proportion"] == 0.7
    assert detection["expected_proportion"] == 0.25
    assert detection["observed_numerator"] == 700.0
    assert detection["observed_denominator"] == 1000.0
    assert detection["percentage_point_change"] == 45.0
    assert detection["observed_events"] is None
    assert [t["to_state"] for t in body["transitions"]] == ["OPEN"]
    assert CONTRIBUTOR_IP not in resp.text


def test_entity_incident_detail_has_no_caveat_when_the_evidence_recorded_none(db_schema) -> None:
    """An absent caveat is null, never an empty string and never an invented sentence."""

    async def setup(s):  # type: ignore[no-untyped-def]
        w = await ips_world(s)
        extra = {k: v for k, v in SPIKE_DETAILS["extra"].items() if k != "caveat"}
        a = ips_incident("IPS_ENTITY", 8, IpsAnomalyType.IPS_PORT_RATE_SPIKE,
                         entity_id=w["port"], details={**SPIKE_DETAILS, "extra": extra})
        s.add(a)
        await s.flush()
        return a.id

    body = Client(READER).get(f"/api/v1/anomalies/{run(setup)}").json()
    assert body["detection"]["caveat"] is None


def test_detail_fallback_still_reaches_phase_a_and_waf(db_schema) -> None:
    world()
    client = Client(READER)
    waf_items = client.get(
        "/api/v1/anomalies", params={"subject_kind": "WAF_ENDPOINT"}
    ).json()["items"]
    a_items = client.get(
        "/api/v1/anomalies", params={"subject_kind": "LOG_SOURCE"}
    ).json()["items"]
    assert client.get(f"/api/v1/anomalies/{waf_items[0]['id']}").json()[
        "subject_kind"
    ] == "WAF_ENDPOINT"
    assert client.get(f"/api/v1/anomalies/{a_items[0]['id']}").json()[
        "subject_kind"
    ] == "LOG_SOURCE"
    assert client.get(f"/api/v1/anomalies/{MISSING}").status_code == 404
    assert client.get("/api/v1/anomalies/not-a-uuid").status_code == 422


# ------------------------------------------------------------ IPS behavior reads
SOURCE_KEYS = {
    "log_source_id", "log_source_name", "product_label", "enabled", "profile_version",
    "validation_status", "validated_profile_version", "validated_at", "validation_current",
    "latest_bucket", "entities",
}


def test_ips_sources_report_profile_latest_bucket_and_pressure(db_schema) -> None:
    w = world()
    resp = Client(READER).get("/api/v1/behavior/ips/sources")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2  # ips-1 and ips-quiet; ips-bare has no profile
    by_name = {i["log_source_name"]: i for i in body["items"]}
    assert set(by_name) == {"ips-1", "ips-quiet"}
    one, quiet = by_name["ips-1"], by_name["ips-quiet"]
    assert set(one) == SOURCE_KEYS
    assert one["enabled"] is True and one["validation_current"] is True
    assert one["validation_status"] == "VALID"
    last = one["latest_bucket"]
    assert last["bucket_start"] == _iso(w["b_new"])
    assert last["total_events"] == 5000 and last["in_maintenance"] is False
    by_dimension = {d["dimension"]: d for d in last["dimensions"]}
    assert set(by_dimension) == {
        "DESTINATION_PORT", "DESTINATION_IP", "SIGNATURE", "SOURCE_IP", "PORT_FANOUT",
        "ACTION",
    }
    assert by_dimension["DESTINATION_PORT"]["measurement"] == "EXACT"
    assert by_dimension["DESTINATION_PORT"]["coverage_ratio"] == 0.98
    assert by_dimension["DESTINATION_PORT"]["coverage_lower_bound"] is True
    # Port fan-out counts ports, not events: it has no coverage to report.
    assert by_dimension["PORT_FANOUT"]["coverage_ratio"] is None
    assert by_dimension["ACTION"]["measurement"] == "NOT_COLLECTED"
    admission = {a["dimension"]: a for a in last["admission"]}
    assert set(admission) == {"DESTINATION_PORT", "DESTINATION_IP", "SIGNATURE"}
    assert admission["DESTINATION_PORT"]["admitted"] == 30
    assert admission["DESTINATION_PORT"]["rejected_over_quota"] == 4
    assert admission["DESTINATION_PORT"]["below_admission_threshold"] == 2
    # SIGNATURE admission recorded nothing: unknown, not zero.
    assert admission["SIGNATURE"]["below_admission_threshold"] is None
    pressure = {e["dimension"]: e for e in one["entities"]}
    ceiling = Settings().ips_max_active_entities_per_source_dimension
    assert pressure["DESTINATION_PORT"] == {
        "dimension": "DESTINATION_PORT", "active": 1, "total": 2, "active_ceiling": ceiling,
    }
    assert pressure["SIGNATURE"]["total"] == 1
    # A profile that never collected: unmeasured is null and zero counts are
    # counts of nothing, not measurements of nothing.
    assert quiet["latest_bucket"] is None
    assert all(e["active"] == 0 and e["total"] == 0 for e in quiet["entities"])
    assert CONTRIBUTOR_IP not in resp.text and "queries" not in resp.text


def test_a_disabled_profile_is_not_listed_among_the_sources(db_schema) -> None:
    w = world()

    async def disable(s):  # type: ignore[no-untyped-def]
        profile = await s.scalar(
            select(IpsSourceProfile).where(IpsSourceProfile.log_source_id == w["quiet"])
        )
        profile.enabled = False

    run(disable)
    body = Client(READER).get("/api/v1/behavior/ips/sources").json()
    assert body["total"] == 1
    assert [i["log_source_name"] for i in body["items"]] == ["ips-1"]


def test_ips_entities_inventory(db_schema) -> None:
    w = world()
    client = Client(READER)
    resp = client.get(f"/api/v1/behavior/ips/sources/{w['isrc']}/entities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 4
    by_key = {i["value_key"]: i for i in body["items"]}
    assert set(by_key["443/tcp"]) == {
        "id", "dimension", "value_key", "display_name", "first_seen_at", "last_seen_at",
        "active", "retired_at", "baseline", "state", "active_anomaly_count",
    }
    port = by_key["443/tcp"]
    # Two current cells, one reliable; the stale normalizer-99 cell counts for
    # nothing. Worst state across the port's detectors is OPEN; two of its
    # incidents are still active.
    assert port["baseline"] == {"cells_total": 2, "reliable_cells": 1}
    assert port["state"] == "OPEN"
    assert port["active_anomaly_count"] == 2
    assert by_key["qid:5000123"]["display_name"] == "Exploit Kit Traffic"
    assert by_key["qid:5000123"]["state"] == "NORMAL"
    # Never evaluated: null, not NORMAL.
    assert by_key["10.10.20.15"]["state"] is None
    assert by_key["10.10.20.15"]["baseline"] == {"cells_total": 0, "reliable_cells": 0}
    assert by_key["23/tcp"]["active"] is False
    assert by_key["23/tcp"]["retired_at"] is not None
    # The dimension filter narrows to one kind of entity.
    ports = client.get(
        f"/api/v1/behavior/ips/sources/{w['isrc']}/entities",
        params={"dimension": "DESTINATION_PORT"},
    ).json()
    assert ports["total"] == 2
    assert {i["value_key"] for i in ports["items"]} == {"443/tcp", "23/tcp"}
    paged = client.get(
        f"/api/v1/behavior/ips/sources/{w['isrc']}/entities", params={"limit": 2, "offset": 2}
    ).json()
    assert paged["total"] == 4 and len(paged["items"]) == 2


ENTITY_METRIC_KEYS = {
    "bucket_start", "bucket_seconds", "events", "measurement", "coverage_ratio",
    "coverage_lower_bound", "admission_truncated", "in_maintenance",
}


def test_ips_entity_metrics_keep_unmeasured_null_and_skip_absent_buckets(db_schema) -> None:
    w = world()
    client = Client(READER)
    resp = client.get(f"/api/v1/behavior/ips/entities/{w['port']}/metrics", params=window())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    rows = body["items"]
    assert [r["bucket_start"] for r in rows] == sorted(r["bucket_start"] for r in rows)
    assert all(set(r) == ENTITY_METRIC_KEYS for r in rows)
    full, sparse, orphan = rows  # ascending: at(40), at(30), at(10)
    assert full["events"] == 812
    assert full["measurement"] == "EXACT" and full["coverage_ratio"] == 0.98
    assert full["in_maintenance"] is False
    # The source's bucket exists: port admission was truncated this time.
    assert sparse["events"] == 90
    assert sparse["measurement"] == "EXACT"
    assert sparse["admission_truncated"] is True
    # No bucket record for this row: what it would have said is unknown, and an
    # unmeasured bucket is null, never 0.
    assert orphan["events"] == 5
    assert orphan["measurement"] is None and orphan["coverage_ratio"] is None
    assert orphan["in_maintenance"] is None and orphan["admission_truncated"] is None
    assert CONTRIBUTOR_IP not in resp.text
    assert "queries" not in resp.text and "provenance" not in resp.text
    # A bucket with no entity row is absent, not a zero: the entity has three
    # rows and the source three buckets.
    narrow = client.get(
        f"/api/v1/behavior/ips/entities/{w['port']}/metrics",
        params=window(**{"since": _iso(at(45)), "until": _iso(at(35))}),
    ).json()
    assert [r["bucket_start"] for r in narrow["items"]] == [_iso(w["b_old"])]
    assert client.get(
        f"/api/v1/behavior/ips/entities/{MISSING}/metrics", params=window()
    ).status_code == 404


BASELINE_KEYS = {
    "metric_name", "weekday", "hour", "median", "mad", "p05", "p95", "numerator_sum",
    "denominator_sum", "sample_count", "is_reliable", "completeness", "baseline_version",
    "profile_version", "normalizer_version", "excluded_sample_count", "metric_semantics",
    "window_start", "window_end", "computed_at",
}


def test_ips_entity_baselines_list_cells_with_reliability_and_versions(db_schema) -> None:
    w = world()
    client = Client(READER)
    body = client.get(f"/api/v1/behavior/ips/entities/{w['port']}/baselines").json()
    assert body["total"] == 3
    assert all(set(c) == BASELINE_KEYS for c in body["items"])
    reliability = {(c["metric_name"], c["hour"]): c["is_reliable"] for c in body["items"]}
    assert reliability == {
        ("entity_events", 9): True, ("entity_events", 10): False,
        ("entity_events", 11): True,
    }
    stale = next(c for c in body["items"] if c["hour"] == 11)
    assert stale["normalizer_version"] == 99
    current = next(c for c in body["items"] if c["hour"] == 9)
    assert current["profile_version"] == 2 and current["baseline_version"] == 2
    # An entity cell pools nothing: both sums stay null.
    assert current["numerator_sum"] is None and current["denominator_sum"] is None
    # Raw training observations are not served.
    assert "observations" not in client.get(
        f"/api/v1/behavior/ips/entities/{w['port']}/baselines"
    ).text
    only = client.get(
        f"/api/v1/behavior/ips/entities/{w['sig']}/baselines", params={"metric": "entity_events"}
    ).json()
    assert only["total"] == 1 and only["items"][0]["median"] == 40.0
    assert client.get(
        f"/api/v1/behavior/ips/entities/{MISSING}/baselines"
    ).status_code == 404


def test_ips_source_metrics_serve_the_series_the_source_detectors_judge(db_schema) -> None:
    w = world()
    client = Client(READER)
    resp = client.get(f"/api/v1/behavior/ips/sources/{w['isrc']}/metrics", params=window())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    rows = body["items"]
    assert [r["bucket_start"] for r in rows] == sorted(r["bucket_start"] for r in rows)
    _old, sparse, new = rows  # ascending: at(40), at(30), at(20)
    assert new["total_events"] == 5000 and new["total_records"] == 5100
    assert new["source_events"] == 4800 and new["distinct_sources"] == 40
    assert new["top1_events"] == 700 and new["top5_events"] == 1200
    assert new["max_distinct_ports"] == 12
    # Unmapped action: the split is unknown, never zeros.
    assert new["blocked_events"] is None and new["allowed_events"] is None
    assert new["unmapped_events"] is None
    assert new["dimensions"][0]["dimension"] == "DESTINATION_PORT"
    # The bucket whose source-IP search never ran: every fact of that search is
    # null, never 0.
    assert sparse["source_events"] is None and sparse["distinct_sources"] is None
    assert sparse["top1_events"] is None and sparse["top5_events"] is None
    assert sparse["max_distinct_ports"] is None
    # The bounded contributor lists the bucket holds are not served.
    assert CONTRIBUTOR_IP not in resp.text
    assert "queries" not in resp.text and "provenance" not in resp.text
    # A profiled source that never collected answers empty, not 404: it is an
    # IPS source with nothing measured yet.
    quiet = client.get(
        f"/api/v1/behavior/ips/sources/{w['quiet']}/metrics", params=window()
    )
    assert quiet.status_code == 200
    assert quiet.json()["total"] == 0


def test_ips_source_baselines_serve_pooled_sums_behind_ratio_cells(db_schema) -> None:
    w = world()
    client = Client(READER)
    body = client.get(f"/api/v1/behavior/ips/sources/{w['isrc']}/baselines").json()
    assert body["total"] == 2
    by_metric = {c["metric_name"]: c for c in body["items"]}
    share = by_metric["top1_source_share"]
    assert share["numerator_sum"] == 2500.0
    assert share["denominator_sum"] == 10000.0
    count = by_metric["max_distinct_ports"]
    assert count["numerator_sum"] is None and count["denominator_sum"] is None
    assert count["is_reliable"] is True and count["profile_version"] == 2
    assert "observations" not in str(body)
    only = client.get(
        f"/api/v1/behavior/ips/sources/{w['isrc']}/baselines",
        params={"metric": "max_distinct_ports"},
    ).json()
    assert only["total"] == 1
    empty = client.get(f"/api/v1/behavior/ips/sources/{w['quiet']}/baselines").json()
    assert empty == {"items": [], "total": 0, "limit": 200, "offset": 0}


# ----------------------------------------------------------- guards on all routes
NEW_ROUTES = [
    "/api/v1/behavior/ips/sources",
    "/api/v1/behavior/ips/sources/{src}/entities",
    "/api/v1/behavior/ips/sources/{src}/metrics",
    "/api/v1/behavior/ips/sources/{src}/baselines",
    "/api/v1/behavior/ips/entities/{ent}/metrics",
    "/api/v1/behavior/ips/entities/{ent}/baselines",
]


def _ips_urls(w):  # type: ignore[no-untyped-def]
    return [
        route.replace("{src}", str(w["isrc"])).replace("{ent}", str(w["port"]))
        for route in NEW_ROUTES
    ]


@pytest.mark.parametrize("route", [r for r in NEW_ROUTES if "{" in r])
def test_unknown_ids_are_404(db_schema, route: str) -> None:
    url = route.replace("{src}", MISSING).replace("{ent}", MISSING)
    params = window() if "metrics" in url else None
    assert Client(READER).get(url, params=params).status_code == 404


def test_a_log_source_without_an_ips_profile_is_404(db_schema) -> None:
    w = world()
    client = Client(READER)
    for suffix, params in (
        ("entities", None),
        ("metrics", window()),
        ("baselines", None),
    ):
        url = f"/api/v1/behavior/ips/sources/{w['bare']}/{suffix}"
        assert client.get(url, params=params).status_code == 404, url


@pytest.mark.parametrize("route", NEW_ROUTES)
def test_read_anomalies_is_required(db_schema, route: str) -> None:
    w = world()
    url = route.replace("{src}", str(w["isrc"])).replace("{ent}", str(w["port"]))
    params = window() if "metrics" in url else None
    assert Client(NOBODY).get(url, params=params).status_code == 403
    assert Client(ANOMALY_READER).get(url, params=params).status_code == 200
    assert Client(READER).get(url, params=params).status_code == 200


@pytest.mark.parametrize("route", [NEW_ROUTES[2], NEW_ROUTES[4]])
def test_range_beyond_the_ceiling_is_422(db_schema, route: str) -> None:
    w = world()
    url = route.replace("{src}", str(w["isrc"])).replace("{ent}", str(w["port"]))
    too_wide = {"since": _iso(NOW - timedelta(days=400)), "until": _iso(NOW)}
    backwards = {"since": _iso(NOW), "until": _iso(NOW - timedelta(hours=1))}
    for params in (too_wide, backwards):
        assert Client(READER).get(url, params=params).status_code == 422, params


def test_limit_is_clamped_to_the_page_size_ceiling(db_schema) -> None:
    w = world()
    client = Client(READER)
    settings = get_settings().model_copy(update={"api_max_page_size": 2})
    client.http.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    body = client.get(f"/api/v1/behavior/ips/sources/{w['isrc']}/entities?limit=50").json()
    assert body["limit"] == 2 and len(body["items"]) == 2
    assert body["total"] == 4


def test_ips_behavior_statement_counts_do_not_grow_with_the_data(db_schema) -> None:
    """Every behavior route costs a fixed number of statements: pinned at 3 and
    40 entities, so a per-row query cannot hide behind "still constant"."""
    w = world()
    client = Client(READER)
    urls = {
        "sources": "/api/v1/behavior/ips/sources",
        "entities": f"/api/v1/behavior/ips/sources/{w['isrc']}/entities",
        "entity_metrics": f"/api/v1/behavior/ips/entities/{w['port']}/metrics",
        "entity_baselines": f"/api/v1/behavior/ips/entities/{w['port']}/baselines",
        "source_metrics": f"/api/v1/behavior/ips/sources/{w['isrc']}/metrics",
        "source_baselines": f"/api/v1/behavior/ips/sources/{w['isrc']}/baselines",
    }
    params = {
        name: window() if "metrics" in name else None for name in urls
    }
    counts: dict[str, tuple[int, int]] = {}
    before: dict[str, int] = {}
    # The seeded source holds four entities (three active, one retired).

    async def seeded_entities(s):  # type: ignore[no-untyped-def]
        return len(
            (
                await s.scalars(
                    select(IpsEntity).where(IpsEntity.log_source_id == w["isrc"])
                )
            ).all()
        )

    assert run(seeded_entities) == 4
    for name, url in urls.items():
        assert client.get(url, params=params[name]).status_code == 200, name
        before[name] = client.statements

    async def grow(s):  # type: ignore[no-untyped-def]
        port = await s.get(IpsEntity, w["port"])
        inst_id, src_id = port.instance_id, port.log_source_id
        # To 40 entities on the source, plus rows behind every other route.
        s.add_all([
            entity(inst_id, src_id, IpsDimension.DESTINATION_IP, f"10.10.40.{i}")
            for i in range(1, 37)
        ])
        await s.flush()
        for i in range(30):
            s.add(source_bucket(src_id, at(200 + i)))
        await s.flush()
        entities = list(
            (
                await s.scalars(
                    select(IpsEntity).where(IpsEntity.log_source_id == src_id)
                )
            ).all()
        )
        s.add_all([
            entity_baseline(e.id, 12 + (i % 10)) for i, e in enumerate(entities)
        ])

    run(grow)
    for name, url in urls.items():
        assert client.get(url, params=params[name]).status_code == 200, name
        counts[name] = (before[name], client.statements)
    for name, (small, large) in counts.items():
        assert small == large, (name, small, large)
    # Pinned: a new per-row query cannot hide behind "still constant".
    assert {n: c[0] for n, c in counts.items()} == {
        "sources": 4, "entities": 6, "entity_metrics": 3, "entity_baselines": 3,
        "source_metrics": 3, "source_baselines": 3,
    }
