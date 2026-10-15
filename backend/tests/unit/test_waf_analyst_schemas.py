"""WAF analyst API projections: whitelists, unmeasured values and filter applicability."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.api.routes.anomalies import _validation_report, _worst_state
from app.main import create_app
from app.models.enums import AnomalyState
from app.repositories.anomaly import AnomalyRepository
from app.schemas.anomaly import (
    SubjectKind,
    WafDetectionDetailOut,
    WafMetricBucketOut,
    waf_detection_detail,
)
from app.waf.behavior import BLOCK_RATIO
from tests.unit.test_waf_behavior import ratio_ctx, run

VOLUME = {
    "reason": "812 requests above median",
    "baseline_low": 80.0, "baseline_high": 120.0, "threshold": 3.5, "sample_count": 12,
    "queries": ["SELECT 1"],
    "extra": {
        "observed_requests": 812.0, "expected_requests": 100.0,
        "absolute_delta_requests": 712.0, "endpoint_coverage_ratio": 0.97,
        "baseline_completeness": 0.9, "baseline_version": 4,
        "request_count_measurement": "LOWER_BOUND", "ratio": 8.12,
        "robust_score_status": "DEGENERATE", "robust_z": 21.4, "fallback_bound": 130.0,
        "distinct_client_count": 7, "contributors": [{"source_ip": "203.0.113.1"}],
        "entity": {"path": "/raw"},
    },
}


def test_volume_evidence_projects_only_whitelisted_keys() -> None:
    out = waf_detection_detail(VOLUME, normalizer_version=3)
    assert out is not None
    dumped = out.model_dump()
    assert set(dumped) == set(WafDetectionDetailOut.model_fields)
    assert dumped["observed_requests"] == 812.0 and dumped["normalizer_version"] == 3
    assert dumped["request_count_measurement"] == "LOWER_BOUND"
    assert dumped["robust_score_status"] == "DEGENERATE"
    blob = out.model_dump_json()
    for leaked in ("203.0.113.1", "/raw", "SELECT", "contributors", "distinct_client_count"):
        assert leaked not in blob


def test_ratio_evidence_reads_nested_sections_by_key() -> None:
    out = waf_detection_detail({
        "extra": {
            "observed": {"proportion": 0.4, "numerator": 40, "denominator": 100,
                         "numerator_label": "blocked", "uncertain": 3},
            "expected": {"proportion": 0.05, "baseline_version": 2, "completeness": 0.8,
                         "numerator_sum": 9.0},
            "percentage_point_change": 35.0, "ratio": 8.0,
            "contributors": {"clients": [{"source_ip": "198.51.100.2"}]},
        }
    })
    assert out is not None
    assert (out.observed_proportion, out.expected_proportion) == (0.4, 0.05)
    assert (out.observed_numerator, out.observed_denominator) == (40.0, 100.0)
    assert out.baseline_version == 2 and out.baseline_completeness == 0.8
    assert out.percentage_point_change == 35.0
    assert "198.51.100.2" not in out.model_dump_json()


NEW_ENDPOINT_CAVEAT = "A newly requested endpoint may be a deployment as easily as a probe."


def test_caveat_is_forwarded_from_the_evidence() -> None:
    out = waf_detection_detail({"extra": {"caveat": NEW_ENDPOINT_CAVEAT}})
    assert out is not None
    assert out.caveat == NEW_ENDPOINT_CAVEAT


def test_caveat_is_none_when_absent_and_when_malformed() -> None:
    absent = waf_detection_detail({"extra": {"ratio": 2.0}})
    assert absent is not None and absent.caveat is None
    malformed = waf_detection_detail({"extra": {"caveat": 3}})
    assert malformed is not None and malformed.caveat is None


def test_a_real_detectors_evidence_projects_its_own_caveat() -> None:
    verdict = run("block", ratio_ctx("block", 40))
    assert verdict.is_anomalous
    out = waf_detection_detail(verdict.evidence.to_dict())
    assert out is not None
    assert out.caveat == BLOCK_RATIO.caveat


def test_missing_and_malformed_values_are_none_never_zero() -> None:
    assert waf_detection_detail(None) is None
    assert waf_detection_detail({}) is None
    out = waf_detection_detail({
        "baseline_low": True, "threshold": "3.5", "sample_count": None,
        "extra": {"observed_requests": None, "ratio": False, "robust_score_status": "BOGUS",
                  "request_count_measurement": "ROUGHLY", "expected_requests": "100"},
    })
    assert out is not None
    assert out.expected_low is None and out.threshold is None
    assert out.baseline_sample_count is None
    assert out.observed_requests is None and out.ratio is None
    assert out.expected_requests is None
    assert out.robust_score_status is None and out.request_count_measurement is None


def test_unmeasured_bucket_values_serialize_as_null() -> None:
    bucket = WafMetricBucketOut(
        bucket_start=datetime(2026, 10, 13, tzinfo=UTC), bucket_seconds=300,
        completeness="PARTIAL", partial=True, normalizer_version=1,
    )
    dumped = bucket.model_dump()
    assert dumped["request_count"] is None
    assert dumped["endpoint_coverage_ratio"] is None
    assert dumped["blocked_count"] is None and dumped["distinct_client_count"] is None


def test_validation_report_keeps_counts_and_drops_queries_and_values() -> None:
    out = _validation_report({
        "status": "VALID", "reasons": ["ok", 7], "total_events": 90, "total_query": "SELECT",
        "log_source_found": True,
        "fields": [
            "not-a-dict",
            {"field": "ACTION", "property_name": "Act", "status": "AVAILABLE",
             "used_by_collection": True, "populated_events": 5, "populated_ratio": True,
             "discovered_values": [{"value": "DENY"}], "queries": ["SELECT Act"]},
        ],
    })
    assert out is not None
    assert out.reasons == ["ok"]
    (field,) = out.fields
    assert field.discovered_value_count == 1
    assert field.populated_ratio is None  # a bool is not a ratio
    assert field.distinct_values is None
    blob = out.model_dump_json()
    assert "SELECT" not in blob and "DENY" not in blob and "queries" not in blob
    assert _validation_report({}) is None
    assert _validation_report(None) is None


def test_worst_state_never_calls_an_unjudged_endpoint_normal() -> None:
    assert _worst_state([]) is None
    assert _worst_state(["NORMAL", "INSUFFICIENT_DATA"]) is AnomalyState.INSUFFICIENT_DATA
    assert _worst_state(["NORMAL", "OPEN", "CANDIDATE"]) is AnomalyState.OPEN
    assert _worst_state(["NORMAL"]) is AnomalyState.NORMAL


def test_filter_applicability_per_kind() -> None:
    applicable = AnomalyRepository._applicable_kinds
    none = dict(
        subject_kind=None, waf_endpoint_id=None, ips_entity_id=None, anomaly_type=None
    )
    # (Phase A, WAF, IPS): an unfiltered list matches every family.
    assert applicable(**none) == (True, True, True)
    assert applicable(**{**none, "subject_kind": SubjectKind.LOG_SOURCE}) == (True, False, False)
    assert applicable(**{**none, "subject_kind": SubjectKind.WAF_ENDPOINT}) == (False, True, False)
    assert applicable(**{**none, "waf_endpoint_id": uuid.uuid4()}) == (False, True, False)
    assert applicable(**{**none, "ips_entity_id": uuid.uuid4()}) == (False, False, True)
    assert applicable(**{**none, "anomaly_type": "VOLUME_SPIKE"}) == (True, False, False)
    assert applicable(**{**none, "anomaly_type": "WAF_BLOCK_RATIO_SPIKE"}) == (False, True, False)
    assert applicable(**{**none, "anomaly_type": "IPS_SIGNATURE_BURST"}) == (False, False, True)
    assert applicable(
        subject_kind=SubjectKind.LOG_SOURCE, waf_endpoint_id=None, ips_entity_id=None,
        anomaly_type="WAF_BLOCK_RATIO_SPIKE",
    ) == (False, False, False)


def test_every_waf_behavior_route_is_get_only() -> None:
    paths = {
        p: set(ops)
        for p, ops in create_app().openapi()["paths"].items()
        if "/behavior/waf" in p
    }
    assert len(paths) == 5, sorted(paths)
    assert all(methods == {"get"} for methods in paths.values()), paths
