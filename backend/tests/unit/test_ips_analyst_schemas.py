"""IPS analyst API projections: the subject model, whitelist and filter applicability."""

from __future__ import annotations

import uuid

from app.main import create_app
from app.models.enums import IpsAnomalyType
from app.repositories.anomaly import AnomalyRepository
from app.schemas.anomaly import (
    AnomalyListItem,
    IpsDetectionDetailOut,
    IpsEntityRefOut,
    SubjectKind,
    ips_detection_detail,
)

# Shaped exactly as `AnomalyEvidence.to_dict()` serializes a port-entity spike,
# including the nested `extra` sections the detectors write (`app/ips/detector.py`).
SPIKE = {
    "anomaly_type": "IPS_PORT_RATE_SPIKE",
    "signal": "ANOMALOUS",
    "severity": "MEDIUM",
    "observed_value": 812.0,
    "expected_value": 100.0,
    "baseline_low": 80.0,
    "baseline_high": 120.0,
    "deviation_score": 21.4,
    "threshold": 3.5,
    "sample_count": 12,
    "interval_start": "2026-10-13T09:00:00+00:00",
    "interval_end": "2026-10-13T10:00:00+00:00",
    "confidence": 0.9,
    "reason": "at least 812 events/bucket for destination port 443/tcp is above the median",
    "extra": {
        "evidence_version": 1,
        "detector_type": "IPS_PORT_RATE_SPIKE",
        "entity": {"dimension": "DESTINATION_PORT", "value_key": "443/tcp",
                   "display_name": "443/tcp"},
        "observed": {"metric": "entity_events", "events": 812, "measurement": "LOWER_BOUND"},
        "expected": {
            "expected": 100.0, "median": 100.0, "p05": 80.0, "p95": 120.0, "samples": 12,
            "completeness": 0.95, "baseline_version": 3, "numerator_sum": None,
            "denominator_sum": None, "metric_identity": "ips/entity_events/v1",
        },
        "absolute_delta": 712.0,
        "ratio": 8.12,
        "robust_score_status": "OK",
        "robust_z": 21.4,
        "observed_measurement": "LOWER_BOUND",
        "caveat": "More events on a port describe traffic volume, not intent.",
        "window": {"start": "2026-10-13T09:00:00+00:00", "end": "2026-10-13T10:00:00+00:00"},
        "contributors": {"SOURCE_IP": [{"source_ip": "203.0.113.77", "events": 600}]},
        "provenance": {"record": "ips_source_bucket", "bucket_start": "2026-10-13T09:00:00"},
        "queries": ["SELECT sourceip FROM events"],
    },
    "insufficient_data": False,
}

DETECTION_KEYS = set(IpsDetectionDetailOut.model_fields)


def test_spike_evidence_projects_only_whitelisted_keys() -> None:
    out = ips_detection_detail(SPIKE)
    assert out is not None
    dumped = out.model_dump()
    assert set(dumped) == DETECTION_KEYS
    assert dumped["observed_events"] == 812.0
    assert dumped["expected_events"] == 100.0
    assert dumped["observed_measurement"] == "LOWER_BOUND"
    assert dumped["baseline_version"] == 3 and dumped["baseline_completeness"] == 0.95
    assert dumped["caveat"].startswith("More events")
    # An unknown key anywhere is dropped, never passed through. The reason
    # sentence may name the entity itself; its identity is public via
    # `ips_entity`, and what must not leak is everything around it.
    blob = out.model_dump_json()
    for leaked in ("203.0.113.77", "contributors", "provenance", "SELECT", "queries",
                   "metric_identity", "entity_events", "evidence_version"):
        assert leaked not in blob, leaked


def test_share_evidence_reads_the_pooled_proportion_pair() -> None:
    details = {
        "reason": "the largest source IP sent 700 of 1000 events",
        "threshold": 3.5,
        "sample_count": 12,
        "extra": {
            "observed": {"metric": "top1_source_share", "proportion": 0.7, "numerator": 700,
                         "denominator": 1000, "measurement": {"numerator": "EXACT",
                                                              "denominator": "EXACT"}},
            "expected": {"expected": 0.25, "completeness": 1.0, "baseline_version": 2},
            "percentage_point_change": 45.0,
            "absolute_delta": 0.45,
            "ratio": 2.8,
            "observed_measurement": "EXACT",
            "contributors": {"SOURCE_IP": [{"source_ip": "198.51.100.2", "events": 700}]},
        },
    }
    out = ips_detection_detail(details)
    assert out is not None
    assert (out.observed_proportion, out.expected_proportion) == (0.7, 0.25)
    assert (out.observed_numerator, out.observed_denominator) == (700.0, 1000.0)
    assert out.percentage_point_change == 45.0
    # The count-shaped fields stay None: a proportion is not an event count.
    assert out.observed_events is None and out.expected_events is None
    assert out.observed_distinct_ports is None and out.expected_distinct_ports is None
    assert "198.51.100.2" not in out.model_dump_json()


def test_fanout_evidence_exposes_the_distinct_port_pair() -> None:
    out = ips_detection_detail({
        "extra": {
            "observed": {"metric": "max_distinct_ports", "max_distinct_ports": 64,
                         "measurement": "LOWER_BOUND"},
            "expected": {"expected": 18.0, "completeness": 1.0, "baseline_version": 1},
            "absolute_delta": 46.0,
            "observed_measurement": "LOWER_BOUND",
            "contributors": {"PORT_FANOUT": [{"source_ip": "203.0.113.9",
                                              "distinct_ports": 64}]},
        },
    })
    assert out is not None
    assert (out.observed_distinct_ports, out.expected_distinct_ports) == (64.0, 18.0)
    assert out.observed_measurement == "LOWER_BOUND"
    assert out.observed_events is None and out.observed_proportion is None
    assert "203.0.113.9" not in out.model_dump_json()


def test_missing_and_malformed_values_are_none_never_zero() -> None:
    assert ips_detection_detail(None) is None
    assert ips_detection_detail({}) is None
    out = ips_detection_detail({
        "baseline_low": True, "threshold": "3.5", "sample_count": None,
        "extra": {"observed": {"events": None}, "expected": {"expected": "100"},
                  "ratio": False, "robust_score_status": "BOGUS",
                  "observed_measurement": "ROUGHLY", "absolute_delta": "many",
                  "caveat": 7},
    })
    assert out is not None
    assert out.expected_low is None and out.threshold is None
    assert out.baseline_sample_count is None
    assert out.observed_events is None and out.expected_events is None
    assert out.ratio is None and out.absolute_delta is None
    assert out.robust_score_status is None and out.observed_measurement is None
    assert out.caveat is None


def test_subject_model_accepts_ips_kinds_and_the_entity_reference() -> None:
    assert {SubjectKind.IPS_ENTITY.value, SubjectKind.IPS_SOURCE.value} == {
        "IPS_ENTITY", "IPS_SOURCE"
    }
    entity = IpsEntityRefOut(
        id=uuid.uuid4(), dimension="SIGNATURE", value_key="qid:5000123",
        display_name="Exploit Kit Traffic",
    )
    item = AnomalyListItem.model_validate(
        {
            "id": uuid.uuid4(),
            "subject_kind": "IPS_ENTITY",
            "log_source_id": uuid.uuid4(),
            "ips_entity": entity.model_dump(),
            "anomaly_type": "IPS_SIGNATURE_BURST",
            "state": "OPEN",
            "severity": "MEDIUM",
            "detected_at": "2026-10-13T09:00:00Z",
            "evidence_status": "NOT_REQUESTED",
        }
    )
    assert item.ips_entity is not None
    assert item.ips_entity.value_key == "qid:5000123"
    assert item.anomaly_type == IpsAnomalyType.IPS_SIGNATURE_BURST
    # Additive only: the additive key serializes, and WAF/Phase A items keep
    # their shape with `ips_entity` null.
    waf_shaped = AnomalyListItem.model_validate(
        {
            "id": uuid.uuid4(),
            "log_source_id": uuid.uuid4(),
            "anomaly_type": "VOLUME_SPIKE",
            "state": "OPEN",
            "severity": "LOW",
            "detected_at": "2026-10-13T09:00:00Z",
            "evidence_status": "NOT_REQUESTED",
        }
    )
    assert waf_shaped.ips_entity is None
    assert waf_shaped.subject_kind == SubjectKind.LOG_SOURCE


def test_filter_applicability_covers_the_ips_family() -> None:
    """Every filter combination that keeps or excludes the IPS branch."""
    applicable = AnomalyRepository._applicable_kinds
    none = dict(
        subject_kind=None, waf_endpoint_id=None, ips_entity_id=None, anomaly_type=None
    )
    assert applicable(**none) == (True, True, True)
    # The IPS subject kinds narrow to the IPS family alone.
    assert applicable(**{**none, "subject_kind": SubjectKind.IPS_ENTITY}) == (
        False, False, True
    )
    assert applicable(**{**none, "subject_kind": SubjectKind.IPS_SOURCE}) == (
        False, False, True
    )
    # The other families' subject filters exclude IPS.
    assert applicable(**{**none, "subject_kind": SubjectKind.LOG_SOURCE}) == (
        True, False, False
    )
    assert applicable(**{**none, "subject_kind": SubjectKind.WAF_ENDPOINT}) == (
        False, True, False
    )
    # A WAF endpoint id cannot match an IPS row: IPS is excluded, not ignored.
    assert applicable(**{**none, "waf_endpoint_id": uuid.uuid4()}) == (False, True, False)
    # An IPS entity id excludes the other families, as `waf_endpoint_id` does.
    assert applicable(**{**none, "ips_entity_id": uuid.uuid4()}) == (False, False, True)
    # A type of one family excludes the other two.
    assert applicable(**{**none, "anomaly_type": "IPS_PORT_FANOUT_SPIKE"}) == (
        False, False, True
    )
    assert applicable(**{**none, "anomaly_type": "VOLUME_SPIKE"}) == (True, False, False)
    assert applicable(**{**none, "anomaly_type": "WAF_REQUEST_RATE_SPIKE"}) == (
        False, True, False
    )
    # Contradictory filters are an empty result, never a fallback to a family.
    assert applicable(
        subject_kind=SubjectKind.IPS_SOURCE, waf_endpoint_id=None,
        ips_entity_id=uuid.uuid4(), anomaly_type=None,
    ) == (False, False, False)
    assert applicable(
        subject_kind=SubjectKind.WAF_ENDPOINT, waf_endpoint_id=None,
        ips_entity_id=None, anomaly_type="IPS_SIGNATURE_BURST",
    ) == (False, False, False)
    assert applicable(
        subject_kind=None, waf_endpoint_id=uuid.uuid4(),
        ips_entity_id=uuid.uuid4(), anomaly_type=None,
    ) == (False, False, False)


def test_every_ips_behavior_route_is_get_only() -> None:
    paths = {
        p: set(ops)
        for p, ops in create_app().openapi()["paths"].items()
        if "/behavior/ips" in p
    }
    assert len(paths) == 6, sorted(paths)
    assert all(methods == {"get"} for methods in paths.values()), paths
