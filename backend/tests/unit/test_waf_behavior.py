"""Guard matrix for the Phase B.2 behaviour detectors (`app.waf.behavior`).

Every detector is a conjunction of guards, so each guard is exercised failing on
its own with everything else passing. The module contract being pinned:

  * a breakdown that was not collected or failed is INSUFFICIENT_DATA, never zero;
  * HEALTHY is returned only where the anomaly is provably impossible;
  * under INCREASE_ONLY (lower-bound numerator) every would-be HEALTHY exit is
    INSUFFICIENT_DATA, and raising the numerator never un-fires an anomaly.

Assertions on reasons use stable key phrases only.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest

from app.models.enums import (
    BucketCompleteness,
    DetectorSignal,
    Severity,
    WafAnomalyType,
    WafMeasurement,
    WafTelemetryStatus,
)
from app.waf.baseline import TOP_CLIENT_SHARE_METRIC
from app.waf.behavior import (
    NoveltyFacts,
    WafBehaviorThresholds,
    contributors,
    detect_block_ratio_spike,
    detect_client_concentration_spike,
    detect_client_error_ratio_spike,
    detect_error_ratio_spike,
    detect_new_endpoint_access,
)
from app.waf.detector import WafBaselineCell, WafDetectionContext, WafPoint, WafThresholds
from app.waf.inputs import HistoryFacts
from app.waf.measurement import WafVerdictScope
from app.waf.observations import BreakdownCounts, BreakdownStatus, EndpointObservation

START = datetime(2026, 8, 3, 19, 25, tzinfo=UTC)
EXACT = WafTelemetryStatus.EXACT
LOWER = WafTelemetryStatus.LOWER_BOUND
NOT_COLLECTED = WafTelemetryStatus.NOT_COLLECTED
FAILED = WafTelemetryStatus.FAILED
FULL = WafVerdictScope.FULL
INC = WafVerdictScope.INCREASE_ONLY
NONE = WafVerdictScope.NONE
ENTITY = {"http_method": "GET", "normalized_endpoint": "/login"}
LOWER_REASON = "the count is a lower bound"

SIGNAL = DetectorSignal


# ------------------------------------------------------------------- factories
def cell(
    *,
    numerator: float | None = 100.0,
    denominator: float | None = 1000.0,
    median: float = 0.10,
    mad: float = 0.02,
    p05: float | None = 0.05,
    p95: float | None = 0.15,
    reliable: bool = True,
    samples: int = 20,
) -> WafBaselineCell:
    return WafBaselineCell(
        median=median, mad=mad, p05=p05, p95=p95, sample_count=samples,
        is_reliable=reliable, completeness=1.0, numerator_sum=numerator,
        denominator_sum=denominator, metric_semantics="sem-v1",
    )


def observation(
    *,
    request_count: int = 100,
    counts: BreakdownCounts | None = None,
    breakdowns: BreakdownStatus | None = None,
    measurement: WafMeasurement | None = WafMeasurement.EXACT,
) -> EndpointObservation:
    return EndpointObservation(
        bucket_start=START, bucket_seconds=300, request_count=request_count,
        distinct_client_count=None, completeness=BucketCompleteness.COMPLETE,
        endpoint_coverage_ratio=1.0, metric_semantics="sem-v1", measurement=measurement,
        in_maintenance=False, normalizer_version=1,
        breakdowns=breakdowns or BreakdownStatus(), counts=counts or BreakdownCounts(),
    )


def context(obs: EndpointObservation, **overrides: Any) -> WafDetectionContext:
    base: dict[str, Any] = {
        "point": WafPoint(
            bucket_start=START, bucket_end=START + timedelta(seconds=300),
            completeness=BucketCompleteness.COMPLETE, request_count=obs.request_count,
            endpoint_coverage_ratio=1.0,
        ),
        "baseline": cell(),
        "thresholds": WafThresholds(),
        "observation": obs,
        "behavior": WafBehaviorThresholds(),
        "verdict_scope": FULL,
        "extra": {"entity": ENTITY},
    }
    base.update(overrides)
    return WafDetectionContext(**base)


# The three ratio definitions: (detector, type, breakdown field, count builder).
def _block(k: int, n: int, u: int) -> BreakdownCounts:
    return BreakdownCounts(action_events=n, blocked=k, unmapped_actions=u)


def _server(k: int, n: int, u: int) -> BreakdownCounts:
    return BreakdownCounts(response_code_events=n, server_errors=k, unclassified_responses=u)


def _client(k: int, n: int, u: int) -> BreakdownCounts:
    return BreakdownCounts(response_code_events=n, client_errors=k, unclassified_responses=u)


RATIOS = {
    "block": (detect_block_ratio_spike, WafAnomalyType.WAF_BLOCK_RATIO_SPIKE, "action", _block),
    "server": (detect_error_ratio_spike, WafAnomalyType.WAF_ERROR_RATIO_SPIKE,
               "response_code", _server),
    "client": (detect_client_error_ratio_spike, WafAnomalyType.WAF_CLIENT_ERROR_RATIO_SPIKE,
               "response_code", _client),
}
RATIO_IDS = list(RATIOS)


def ratio_ctx(
    kind: str,
    k: int,
    n: int = 100,
    *,
    uncertain: int = 0,
    request_count: int | None = None,
    status: WafTelemetryStatus = EXACT,
    scope: WafVerdictScope | None = FULL,
    top_clients: tuple[dict, ...] | None = None,
    **overrides: Any,
) -> WafDetectionContext:
    _, _, field, build = RATIOS[kind]
    counts = build(k, n, uncertain)
    if top_clients is not None:
        counts = BreakdownCounts(
            **{**counts.__dict__, "client_events": n, "top_clients": top_clients,
               "client_sources": len(top_clients)}
        )
    breakdowns = BreakdownStatus(**{field: status, "client": EXACT})
    obs = observation(
        request_count=n if request_count is None else request_count,
        counts=counts, breakdowns=breakdowns,
    )
    overrides.setdefault("verdict_scope", scope)
    overrides.setdefault("scope_reason", "a numerator is a lower bound")
    return context(obs, **overrides)


def run(kind: str, ctx: WafDetectionContext):
    return RATIOS[kind][0](ctx)


def assert_insufficient(v, *phrases: str) -> None:
    assert v.signal is SIGNAL.UNKNOWN
    assert v.evidence.insufficient_data is True
    assert not v.is_anomalous
    for p in phrases:
        assert p in v.evidence.reason, (p, v.evidence.reason)


def assert_healthy(v) -> None:
    assert v.signal is SIGNAL.HEALTHY, v.evidence.reason


@pytest.fixture(params=RATIO_IDS)
def kind(request: pytest.FixtureRequest) -> str:
    return request.param


# ---------------------------------------------------------------- ratio: inputs
class TestRatioInputs:
    @pytest.mark.parametrize("status", [NOT_COLLECTED, FAILED])
    def test_unavailable_breakdown_is_insufficient_never_zero(self, kind, status) -> None:
        v = run(kind, ratio_ctx(kind, 40, status=status))
        assert_insufficient(v, "not zero")
        assert v.evidence.reason.count("breakdown") >= 1
        word = "not collected" if status is NOT_COLLECTED else "failed"
        assert word in v.evidence.reason
        assert v.detector_type is RATIOS[kind][1]

    def test_unavailable_names_the_breakdown(self) -> None:
        assert "ACTION" in run("block", ratio_ctx("block", 40, status=FAILED)).evidence.reason
        for k in ("server", "client"):
            reason = run(k, ratio_ctx(k, 40, status=FAILED)).evidence.reason
            assert "RESPONSE_CODE" in reason

    @pytest.mark.parametrize("scope", [FULL, INC, NONE])
    def test_unavailable_wins_over_every_scope(self, kind, scope) -> None:
        v = run(kind, ratio_ctx(kind, 40, status=NOT_COLLECTED, scope=scope))
        assert_insufficient(v, "not zero")

    def test_only_the_detectors_own_breakdown_is_consulted(self, kind) -> None:
        _, _, field, _ = RATIOS[kind]
        other = "response_code" if field == "action" else "action"
        ctx = ratio_ctx(kind, 40)
        ctx.observation = observation(
            counts=ctx.observation.counts,
            breakdowns=BreakdownStatus(**{field: EXACT, other: FAILED, "client": FAILED}),
        )
        assert run(kind, ctx).is_anomalous

    def test_scope_none_is_insufficient_with_the_scope_reason(self, kind) -> None:
        v = run(kind, ratio_ctx(kind, 40, scope=NONE, scope_reason="semantics unknown"))
        assert_insufficient(v, "semantics unknown")

    def test_scope_none_without_reason_still_insufficient(self, kind) -> None:
        v = run(kind, ratio_ctx(kind, 40, scope=NONE, scope_reason=None))
        assert_insufficient(v)

    def test_unset_scope_on_an_exact_point_is_full(self, kind) -> None:
        # No grant at all: FULL for an exact request count.
        assert run(kind, ratio_ctx(kind, 40, scope=None)).is_anomalous

    @pytest.mark.parametrize("scope", [FULL, INC])
    def test_coverage_below_the_floor_is_insufficient(self, kind, scope) -> None:
        # 85 of 100 requests carried the field: 85% < 90%.
        v = run(kind, ratio_ctx(kind, 40, 85, request_count=100, scope=scope))
        assert_insufficient(v, "coverage floor")

    def test_coverage_at_the_floor_passes(self, kind) -> None:
        assert run(kind, ratio_ctx(kind, 45, 90, request_count=100)).is_anomalous

    def test_denominator_above_request_count_is_capped_not_an_error(self, kind) -> None:
        assert run(kind, ratio_ctx(kind, 40, 100, request_count=80)).is_anomalous

    def test_coverage_is_checked_before_the_numerator_minimum(self, kind) -> None:
        # Would be HEALTHY on the numerator; low coverage must win.
        assert_insufficient(
            run(kind, ratio_ctx(kind, 3, 60, request_count=100)), "coverage floor"
        )


class TestRatioBaseline:
    def test_incompatible_baseline(self, kind) -> None:
        v = run(kind, ratio_ctx(kind, 40, baseline_incompatibility="built under semantics v0"))
        assert_insufficient(v, "built under semantics v0")

    def test_no_cell(self, kind) -> None:
        assert_insufficient(run(kind, ratio_ctx(kind, 40, baseline=None)), "baseline")

    def test_unreliable_cell(self, kind) -> None:
        v = run(kind, ratio_ctx(kind, 40, baseline=cell(reliable=False, samples=2)))
        assert_insufficient(v, "2 material bucket")
        assert v.evidence.sample_count == 2

    @pytest.mark.parametrize("denominator", [0.0, None])
    def test_no_pooled_denominator(self, kind, denominator) -> None:
        v = run(kind, ratio_ctx(kind, 40, baseline=cell(denominator=denominator)))
        assert_insufficient(v, "no pooled denominator")

    def test_baseline_exits_beat_the_numerator_minimum(self, kind) -> None:
        # k=0 would be HEALTHY; a bad baseline must still be INSUFFICIENT.
        assert_insufficient(run(kind, ratio_ctx(kind, 0, baseline=None)))

    def test_expected_is_pooled_not_the_median(self, kind) -> None:
        # Pooled 300/1000 = 0.30; median says 0.10. Observed 0.40 is only +10 pp
        # and 1.33x against the pooled value, so it must not fire.
        v = run(kind, ratio_ctx(kind, 40, baseline=cell(numerator=300, median=0.10)))
        assert_healthy(v)
        assert v.evidence.expected_value == pytest.approx(0.30)


# ------------------------------------------------------------ ratio: guard pairs
def _guard_cases() -> list[tuple[str, int, int, WafBaselineCell]]:
    """(label, k, n, cell) each failing exactly one guard, others passing."""
    return [
        ("numerator", 9, 100, cell()),
        ("numerator_ignores_small_denominator", 9, 40, cell()),
        ("rise", 12, 200, cell(numerator=30, median=0.03, mad=0.01, p95=0.05)),
        ("multiple", 50, 100, cell(numerator=300, median=0.30, mad=0.05, p95=0.4)),
        ("robust_z", 25, 100, cell(mad=0.10, p95=0.3)),
        ("mad_zero_within_p95", 20, 100, cell(mad=0.0, p95=0.20)),
    ]


GUARD_CASES = _guard_cases()
GUARD_IDS = [c[0] for c in GUARD_CASES]


class TestRatioProvablyNormalExits:
    @pytest.mark.parametrize(("label", "k", "n", "base"), GUARD_CASES, ids=GUARD_IDS)
    def test_full_scope_is_healthy(self, kind, label, k, n, base) -> None:
        v = run(kind, ratio_ctx(kind, k, n, baseline=base))
        assert_healthy(v)
        assert v.evidence.observed_value == pytest.approx(k / n, abs=1e-6)

    @pytest.mark.parametrize(("label", "k", "n", "base"), GUARD_CASES, ids=GUARD_IDS)
    def test_increase_only_is_insufficient_instead(self, kind, label, k, n, base) -> None:
        v = run(kind, ratio_ctx(kind, k, n, baseline=base, scope=INC,
                                scope_reason="some values were unmapped"))
        assert_insufficient(v, "lower bound", "some values were unmapped")

    def test_each_guard_names_itself(self, kind) -> None:
        reasons = {
            label: run(kind, ratio_ctx(kind, k, n, baseline=base)).evidence.reason
            for label, k, n, base in GUARD_CASES
        }
        assert "minimum" in reasons["numerator"]
        assert "pp minimum rise" in reasons["rise"]
        assert "below 2.00x" in reasons["multiple"]
        assert "robust z" in reasons["robust_z"]
        assert "p95" in reasons["mad_zero_within_p95"]

    def test_small_numerator_is_healthy_whatever_the_denominator(self, kind) -> None:
        for n in (5, 40, 100, 10_000):
            assert_healthy(run(kind, ratio_ctx(kind, 9, n)))

    def test_numerator_minimum_is_inclusive(self, kind) -> None:
        # k=10 clears the numerator guard, so a later guard decides (here: n too small).
        assert_insufficient(run(kind, ratio_ctx(kind, 10, 40)), "minimum denominator")


class TestRatioDenominator:
    @pytest.mark.parametrize("scope", [FULL, INC])
    def test_small_denominator_is_insufficient_never_healthy(self, kind, scope) -> None:
        v = run(kind, ratio_ctx(kind, 20, 49, scope=scope))
        assert_insufficient(v, "minimum denominator")

    def test_denominator_minimum_is_inclusive(self, kind) -> None:
        assert run(kind, ratio_ctx(kind, 25, 50)).is_anomalous


class TestRatioMadZeroBand:
    def test_within_p95_is_not_anomalous(self, kind) -> None:
        v = run(kind, ratio_ctx(kind, 20, 100, baseline=cell(mad=0.0, p95=0.20)))
        assert not v.is_anomalous

    def test_above_p95_is_anomalous(self, kind) -> None:
        v = run(kind, ratio_ctx(kind, 25, 100, baseline=cell(mad=0.0, p95=0.20)))
        assert v.is_anomalous
        extra = v.evidence.extra
        assert extra["robust_score_status"] == "DEGENERATE"
        assert extra["fallback_bound"] == pytest.approx(0.20)
        assert "MAD=0" in v.evidence.reason

    def test_missing_p95_falls_back_to_the_median(self, kind) -> None:
        v = run(kind, ratio_ctx(kind, 25, 100, baseline=cell(mad=0.0, p95=None)))
        assert v.is_anomalous
        assert v.evidence.extra["fallback_bound"] == pytest.approx(0.10)

    def test_positive_mad_reports_the_z(self, kind) -> None:
        extra = run(kind, ratio_ctx(kind, 40)).evidence.extra
        assert extra["robust_score_status"] == "OK"
        assert extra["robust_z"] == pytest.approx((0.4 - 0.1) / (0.02 * 1.4826), abs=1e-2)

    def test_z_threshold_is_the_behavior_setting(self, kind) -> None:
        ctx = ratio_ctx(kind, 25, 100, baseline=cell(mad=0.10, p95=0.3))
        assert_healthy(run(kind, ctx))  # z about 1.0 against 3.5
        ctx.behavior = WafBehaviorThresholds(deviation_z=0.5)
        assert run(kind, ctx).is_anomalous


# ---------------------------------------------------------- ratio: expected zero
class TestRatioZeroExpected:
    ZERO: ClassVar[dict] = dict(numerator=0, median=0.0, mad=0.0, p05=0.0, p95=0.0)

    def test_multiple_guard_is_skipped_and_pp_guard_decides(self, kind) -> None:
        v = run(kind, ratio_ctx(kind, 15, 100, baseline=cell(**self.ZERO)))
        assert v.is_anomalous
        assert v.evidence.extra["ratio"] is None
        assert "x)" not in v.evidence.reason
        assert v.evidence.expected_value == 0.0

    def test_rise_below_minimum_is_still_healthy_under_full(self, kind) -> None:
        assert_healthy(run(kind, ratio_ctx(kind, 10, 200, baseline=cell(**self.ZERO))))

    def test_rise_below_minimum_is_insufficient_under_increase_only(self, kind) -> None:
        v = run(kind, ratio_ctx(kind, 10, 200, baseline=cell(**self.ZERO), scope=INC))
        assert_insufficient(v, "lower bound")

    def test_missing_numerator_sum_is_a_zero_expected(self, kind) -> None:
        v = run(kind, ratio_ctx(kind, 15, 100, baseline=cell(**{**self.ZERO, "numerator": None})))
        assert v.is_anomalous

    def test_severity_rests_on_the_rise_alone(self, kind) -> None:
        medium = run(kind, ratio_ctx(kind, 15, 100, baseline=cell(**self.ZERO)))
        high = run(kind, ratio_ctx(kind, 30, 100, baseline=cell(**self.ZERO)))
        assert medium.evidence.severity is Severity.MEDIUM
        assert high.evidence.severity is Severity.HIGH


# ----------------------------------------------------------- ratio: anomalous
class TestRatioAnomalous:
    def test_verdict_fields(self, kind) -> None:
        v = run(kind, ratio_ctx(kind, 40, uncertain=3))
        ev = v.evidence
        assert v.detector_type is RATIOS[kind][1] and ev.anomaly_type is RATIOS[kind][1]
        assert ev.signal is SIGNAL.ANOMALOUS and v.is_anomalous
        assert ev.observed_value == pytest.approx(0.4)
        assert ev.expected_value == pytest.approx(0.1)
        assert ev.baseline_low == 0.05 and ev.baseline_high == 0.15
        assert ev.sample_count == 20
        assert ev.threshold == 3.5
        assert ev.deviation_score == pytest.approx(ev.extra["robust_z"])
        assert ev.interval_start == START
        assert 0.0 < ev.confidence <= 1.0
        assert "GET /login" in ev.reason
        assert "at least" not in ev.reason

    @pytest.mark.parametrize(
        ("k", "base", "severity"),
        [
            (25, cell(), Severity.MEDIUM),  # +15 pp, 2.5x
            (35, cell(), Severity.MEDIUM),  # +25 pp but only 3.5x
            # 5x the expected but only +16 pp.
            (20, cell(numerator=40, median=0.04, mad=0.01, p95=0.08), Severity.MEDIUM),
            (40, cell(), Severity.HIGH),  # +30 pp and 4x
            (60, cell(), Severity.HIGH),
        ],
        ids=["both-low", "rise-high-mult-low", "mult-high-rise-low", "both-high", "extreme"],
    )
    def test_severity_boundaries(self, kind, k, base, severity) -> None:
        v = run(kind, ratio_ctx(kind, k, 100, baseline=base))
        assert v.is_anomalous
        assert v.evidence.severity is severity

    def test_minimum_rise_and_multiple_boundaries_are_inclusive(self, kind) -> None:
        # +10 pp and exactly 2.0x, MAD=0 with the observed value above p95.
        v = run(kind, ratio_ctx(kind, 20, 100, baseline=cell(mad=0.0, p95=0.15)))
        assert v.is_anomalous
        assert v.evidence.severity is Severity.MEDIUM

    def test_full_scope_evidence(self, kind) -> None:
        _, _, _, _ = RATIOS[kind]
        ev = run(kind, ratio_ctx(kind, 40, uncertain=3)).evidence
        x = ev.extra
        assert x["evidence_version"] == 1
        assert x["observed"]["numerator"] == 40
        assert x["observed"]["denominator"] == 100
        assert x["observed"]["uncertain"] == 3
        assert x["observed"]["measurement"] == {"numerator": "EXACT", "denominator": "EXACT"}
        assert x["observed_measurement"] == "EXACT"
        assert x["expected"]["numerator_sum"] == 100
        assert x["expected"]["denominator_sum"] == 1000
        assert x["expected"]["proportion"] == pytest.approx(0.1)
        assert x["percentage_point_change"] == pytest.approx(30.0)
        assert x["ratio"] == pytest.approx(4.0)
        assert x["absolute_deviation"] == pytest.approx(0.3)
        assert x["measurement_completeness"]["breakdown_status"] == "EXACT"
        assert x["entity"] == ENTITY
        assert isinstance(x["caveat"], str) and x["caveat"]
        assert x["window"]["start"] == START.isoformat()

    def test_caveats_are_distinct_and_never_assert_intent(self) -> None:
        caveats = {
            k: run(k, ratio_ctx(k, 40)).evidence.extra["caveat"] for k in RATIO_IDS
        }
        assert len(set(caveats.values())) == 3
        assert "attack succeeded" in caveats["block"]
        assert "malicious" in caveats["server"]
        assert "not by itself a block" in caveats["client"]

    def test_increase_only_marks_the_numerator_a_lower_bound(self, kind) -> None:
        v = run(kind, ratio_ctx(kind, 40, scope=INC))
        assert v.is_anomalous
        x = v.evidence.extra
        assert x["observed"]["measurement"] == {
            "numerator": "LOWER_BOUND", "denominator": "EXACT"
        }
        assert x["observed_measurement"] == "LOWER_BOUND"
        assert v.evidence.reason.startswith("at least ")
        assert "(at least 40.0%)" in v.evidence.reason

    def test_contributors_are_attached_and_bounded(self, kind) -> None:
        tops = ({"source_ip": "203.0.113.7", "requests": 25},)
        v = run(kind, ratio_ctx(kind, 40, top_clients=tops))
        c = v.evidence.extra["contributors"]
        assert c["dimension"] == "SOURCE_IP"
        assert c["items"][0]["source_ip"] == "203.0.113.7"
        assert "all requests to the endpoint" in c["scope"]
        # The raw IP appears nowhere else in the evidence or the reason.
        stripped = {k: val for k, val in v.evidence.extra.items() if k != "contributors"}
        assert "203.0.113.7" not in json.dumps(stripped)
        assert "203.0.113.7" not in v.evidence.reason

    def test_volume_context_uses_the_request_baseline_when_loaded(self, kind) -> None:
        from app.waf.baseline import WAF_BASELINE_METRIC

        vol = cell(median=80.0, mad=5.0, p05=None, p95=None)
        v = run(kind, ratio_ctx(kind, 40, baselines={WAF_BASELINE_METRIC: vol}))
        volume = v.evidence.extra["volume"]
        assert volume["observed_requests"] == 100
        assert volume["expected_requests"] == 80.0
        assert volume["ratio"] == 1.25
        assert run(kind, ratio_ctx(kind, 40)).evidence.extra["volume"]["expected_requests"] is None


# -------------------------------------------------------------- monotonicity
SWEEP_BASES = {
    "positive_mad": cell(),
    "mad_zero": cell(mad=0.0, p95=0.20),
    "zero_expected": cell(numerator=0, median=0.0, mad=0.0, p05=0.0, p95=0.0),
    "wide_mad": cell(mad=0.04, p95=0.3),
}


@pytest.mark.parametrize("base_id", list(SWEEP_BASES))
@pytest.mark.parametrize("n", [50, 100, 400])
def test_raising_the_numerator_never_unfires_under_increase_only(kind, base_id, n) -> None:
    fired = False
    ever_fired = False
    for k in range(0, n + 1):
        v = run(kind, ratio_ctx(kind, k, n, baseline=SWEEP_BASES[base_id], scope=INC))
        # Under a lower-bound scope a verdict is never NORMAL.
        assert v.signal is not SIGNAL.HEALTHY
        if fired:
            assert v.is_anomalous, f"k={k} un-fired after an anomalous smaller k"
        fired = fired or v.is_anomalous
        ever_fired = ever_fired or fired
    assert ever_fired


# =================================================================== concentration
SHARE_CELL = dict(numerator=100, denominator=1000, median=0.10, mad=0.01, p05=0.05, p95=0.15)


def conc_ctx(
    top1: int,
    n: int = 100,
    *,
    top5: int | None = None,
    request_count: int | None = None,
    status: WafTelemetryStatus = EXACT,
    scope: WafVerdictScope | None = FULL,
    top_clients: tuple[dict, ...] | None = None,
    base: WafBaselineCell | None | str = "default",
    **overrides: Any,
) -> WafDetectionContext:
    counts = BreakdownCounts(
        client_events=n, top1_client_requests=top1,
        top5_client_requests=top5 if top5 is not None else min(n, top1 + 10),
        client_sources=12, top_clients=top_clients,
    )
    obs = observation(
        request_count=n if request_count is None else request_count,
        counts=counts, breakdowns=BreakdownStatus(client=status, client_semantics="ip-v1"),
    )
    overrides.setdefault("verdict_scope", scope)
    overrides.setdefault("scope_reason", "composition needs exact measurements")
    overrides.setdefault("baseline", cell(**SHARE_CELL) if base == "default" else base)
    return context(obs, **overrides)


class TestConcentrationInputs:
    @pytest.mark.parametrize("status", [NOT_COLLECTED, FAILED])
    def test_unavailable_breakdown(self, status) -> None:
        v = detect_client_concentration_spike(conc_ctx(60, status=status))
        assert_insufficient(v, "not zero", "SOURCE_IP")

    @pytest.mark.parametrize("scope", [INC, NONE])
    def test_only_full_scope_supports_a_composition_verdict(self, scope) -> None:
        # Even a clearly concentrated bucket: the top contributor needs exact counts.
        v = detect_client_concentration_spike(
            conc_ctx(60, scope=scope, scope_reason="needs exact counts")
        )
        assert_insufficient(v, "needs exact counts")

    def test_scope_reason_default(self) -> None:
        v = detect_client_concentration_spike(conc_ctx(60, scope=NONE, scope_reason=None))
        assert_insufficient(v, "exact measurements")

    def test_coverage_below_floor(self) -> None:
        v = detect_client_concentration_spike(conc_ctx(60, 85, request_count=100))
        assert_insufficient(v, "coverage floor", "source IP")

    def test_baseline_exits(self) -> None:
        d = detect_client_concentration_spike
        assert_insufficient(
            d(conc_ctx(60, baseline_incompatibility="old identity")), "old identity"
        )
        assert_insufficient(d(conc_ctx(60, base=None)), TOP_CLIENT_SHARE_METRIC)
        assert_insufficient(d(conc_ctx(60, base=cell(reliable=False, samples=1))), "1 material")
        assert_insufficient(
            d(conc_ctx(60, base=cell(**{**SHARE_CELL, "denominator": 0}))), "no pooled denominator"
        )


# ------------------------------------------- source-level coverage (decision D-3)
def _with_source_coverage(ctx: WafDetectionContext, ratio: float) -> WafDetectionContext:
    ctx.observation = replace(ctx.observation, endpoint_coverage_ratio=ratio)
    return ctx


class TestSourceCoverage:
    """Breakdown coverage is 100% throughout; only the source-level coverage varies."""

    def test_ratio_low_source_coverage_is_insufficient(self, kind) -> None:
        v = run(kind, _with_source_coverage(ratio_ctx(kind, 40), 0.85))
        assert_insufficient(v, "85%", "90%", "bounds nothing")
        assert v.detector_type is RATIOS[kind][1]

    def test_ratio_at_the_source_floor_still_reaches_its_verdict(self, kind) -> None:
        assert run(kind, _with_source_coverage(ratio_ctx(kind, 40), 0.9)).is_anomalous
        assert_healthy(run(kind, _with_source_coverage(ratio_ctx(kind, 10), 0.9)))

    def test_ratio_source_coverage_is_checked_before_breakdown_coverage(self, kind) -> None:
        ctx = _with_source_coverage(ratio_ctx(kind, 40, 85, request_count=100), 0.5)
        assert_insufficient(run(kind, ctx), "50%", "bounds nothing")

    def test_concentration_low_source_coverage_is_insufficient(self) -> None:
        ctx = _with_source_coverage(conc_ctx(60), 0.85)
        v = detect_client_concentration_spike(ctx)
        assert_insufficient(v, "85%", "90%", "bounds nothing")

    def test_concentration_at_the_source_floor_still_reaches_its_verdict(self) -> None:
        v = detect_client_concentration_spike(_with_source_coverage(conc_ctx(60), 0.9))
        assert v.is_anomalous
        v = detect_client_concentration_spike(_with_source_coverage(conc_ctx(10), 0.9))
        assert_healthy(v)

    def test_concentration_source_coverage_is_checked_before_breakdown_coverage(self) -> None:
        ctx = _with_source_coverage(conc_ctx(60, 85, request_count=100), 0.5)
        assert_insufficient(detect_client_concentration_spike(ctx), "50%", "bounds nothing")


class TestConcentrationGuards:
    def test_small_top_client_is_healthy_under_full(self) -> None:
        # Against a zero expected share, 38% would clear every other guard.
        zero = cell(numerator=0, median=0.0, mad=0.0, p05=0.0, p95=0.0)
        v = detect_client_concentration_spike(conc_ctx(19, 50, base=zero))
        assert_healthy(v)
        assert "minimum" in v.evidence.reason

    def test_small_top_client_is_insufficient_under_a_lower_bound_scope(self) -> None:
        # Concentration never runs under INCREASE_ONLY; the scope exit comes first.
        assert_insufficient(detect_client_concentration_spike(conc_ctx(19, scope=INC)))

    def test_top_client_minimum_is_inclusive(self) -> None:
        assert_insufficient(
            detect_client_concentration_spike(conc_ctx(20, 40)), "minimum"
        )

    def test_small_endpoint_total_is_insufficient(self) -> None:
        v = detect_client_concentration_spike(conc_ctx(30, 49))
        assert_insufficient(v, "49 requests", "composition verdict")

    def test_endpoint_minimum_is_inclusive(self) -> None:
        assert detect_client_concentration_spike(conc_ctx(35, 50)).is_anomalous

    def test_small_rise_is_healthy(self) -> None:
        base = cell(**{**SHARE_CELL, "numerator": 300, "median": 0.3, "mad": 0.02, "p95": 0.4})
        v = detect_client_concentration_spike(conc_ctx(45, base=base))
        assert_healthy(v)
        assert "pp minimum rise" in v.evidence.reason

    def test_low_robust_z_is_healthy(self) -> None:
        base = cell(**{**SHARE_CELL, "mad": 0.2, "p95": 0.5})
        v = detect_client_concentration_spike(conc_ctx(35, base=base))
        assert_healthy(v)
        assert "robust z" in v.evidence.reason

    def test_mad_zero_band(self) -> None:
        base = cell(**{**SHARE_CELL, "mad": 0.0, "p95": 0.40})
        assert_healthy(detect_client_concentration_spike(conc_ctx(35, base=base)))
        hot = detect_client_concentration_spike(conc_ctx(45, base=base))
        assert hot.is_anomalous
        assert hot.evidence.extra["robust_score_status"] == "DEGENERATE"

    def test_expected_share_is_pooled(self) -> None:
        base = cell(**{**SHARE_CELL, "numerator": 500, "median": 0.1})  # pooled 0.5
        assert_healthy(detect_client_concentration_spike(conc_ctx(60, base=base)))


class TestConcentrationAnomalous:
    TOPS = (
        {"source_ip": "198.51.100.9", "requests": 60},
        {"source_ip": "203.0.113.7", "requests": 10},
    )

    def test_verdict_and_evidence(self) -> None:
        v = detect_client_concentration_spike(
            conc_ctx(60, top5=80, top_clients=self.TOPS)
        )
        ev, x = v.evidence, v.evidence.extra
        assert v.detector_type is WafAnomalyType.WAF_CLIENT_CONCENTRATION_SPIKE
        assert ev.signal is SIGNAL.ANOMALOUS
        assert ev.observed_value == pytest.approx(0.6)
        assert ev.expected_value == pytest.approx(0.1)
        assert ev.threshold == 3.5 and ev.sample_count == 20
        assert x["observed"]["metric"] == TOP_CLIENT_SHARE_METRIC
        assert x["observed"]["numerator"] == 60 and x["observed"]["denominator"] == 100
        assert x["observed"]["top5_share"] == pytest.approx(0.8)
        assert x["observed"]["distinct_sources"] == 12
        assert x["observed_measurement"] == "EXACT"
        assert x["metric_identity"] == "ip-v1"
        assert x["percentage_point_change"] == pytest.approx(50.0)
        assert "intent" in x["caveat"]
        assert "proxy" in x["client_identity"]
        assert "five largest sent 80.0%" in ev.reason
        assert "no reliable volume baseline" in ev.reason

    def test_contributors_list_is_the_only_place_ips_appear(self) -> None:
        v = detect_client_concentration_spike(conc_ctx(60, top_clients=self.TOPS))
        c = v.evidence.extra["contributors"]
        assert c["status"] == "AVAILABLE"
        assert [i["source_ip"] for i in c["items"]] == ["198.51.100.9", "203.0.113.7"]
        assert [i["share"] for i in c["items"]] == [0.6, 0.1]
        assert c["distinct_sources"] == 12
        rest = {k: val for k, val in v.evidence.extra.items() if k != "contributors"}
        blob = json.dumps(rest, default=str) + v.evidence.reason
        assert "198.51.100.9" not in blob and "203.0.113.7" not in blob

    def test_the_volume_baseline_is_stated_when_present(self) -> None:
        from app.waf.baseline import WAF_BASELINE_METRIC

        v = detect_client_concentration_spike(
            conc_ctx(60, baselines={WAF_BASELINE_METRIC: cell(median=90.0, mad=5.0)})
        )
        assert "against an expected 90.0" in v.evidence.reason

    @pytest.mark.parametrize(
        ("top1", "severity"), [(35, Severity.MEDIUM), (45, Severity.MEDIUM), (60, Severity.HIGH)]
    )
    def test_severity(self, top1, severity) -> None:
        # Expected 10%: +25 pp, +35 pp are MEDIUM; +50 pp (2 x 20 pp or more) is HIGH.
        assert detect_client_concentration_spike(conc_ctx(top1)).evidence.severity is severity


class TestContributorsHelper:
    def _obs(self, status, **counts) -> EndpointObservation:
        return observation(
            counts=BreakdownCounts(**counts), breakdowns=BreakdownStatus(client=status)
        )

    @pytest.mark.parametrize("status", [NOT_COLLECTED, FAILED])
    def test_unavailable_lists_nothing(self, status) -> None:
        c = contributors(self._obs(status, top_clients=({"source_ip": "1.1.1.1", "requests": 9},)),
                         min_top_requests=20)
        assert c["status"] == "UNAVAILABLE" and c["items"] == []
        assert "1.1.1.1" not in json.dumps(c)

    def test_exact_gives_shares_and_exhaustiveness(self) -> None:
        c = contributors(
            self._obs(EXACT, client_events=200, client_sources=1,
                      top_clients=({"source_ip": "1.1.1.1", "requests": 50},)),
            min_top_requests=20,
        )
        assert c["status"] == "AVAILABLE"
        assert c["items"][0]["share"] == 0.25
        assert c["exhaustive"] is True

    def test_not_exhaustive_when_more_sources_than_listed(self) -> None:
        c = contributors(
            self._obs(EXACT, client_events=200, client_sources=5,
                      top_clients=({"source_ip": "1.1.1.1", "requests": 50},)),
            min_top_requests=20,
        )
        assert c["exhaustive"] is False

    def test_truncated_withholds_shares(self) -> None:
        c = contributors(
            self._obs(LOWER, client_events=200, client_sources=1,
                      top_clients=({"source_ip": "1.1.1.1", "requests": 50},)),
            min_top_requests=20,
        )
        assert c["status"] == "TRUNCATED"
        assert c["items"][0]["share"] is None
        assert c["exhaustive"] is False and c["distinct_sources"] is None
        assert "lower bounds" in c["note"]

    def test_no_item_reaching_the_minimum_is_explained(self) -> None:
        c = contributors(self._obs(EXACT, client_events=10, top_clients=()), min_top_requests=20)
        assert c["items"] == [] and "20 requests" in c["note"]

    def test_input_items_are_not_mutated(self) -> None:
        item = {"source_ip": "1.1.1.1", "requests": 50}
        contributors(
            self._obs(EXACT, client_events=100, top_clients=(item,)), min_top_requests=20
        )
        assert "share" not in item


# ================================================================== new endpoint
FIRST_SEEN = START - timedelta(minutes=10)


def novelty(**overrides: Any) -> NoveltyFacts:
    base: dict[str, Any] = {
        "first_seen_at": FIRST_SEEN,
        "window_end": START + timedelta(hours=1),
        "within_window": True,
        "history": HistoryFacts(complete=True),
        "history_start": START - timedelta(days=7),
        "history_end": FIRST_SEEN,
        "history_buckets": 2016,
        "appearance_flood": None,
        "requests_since_first_seen": 30,
        "buckets_with_traffic": 3,
        "lower_bound": False,
    }
    base.update(overrides)
    return NoveltyFacts(**base)


def new_ctx(
    facts: NoveltyFacts | None | str = "default",
    *,
    scope: WafVerdictScope | None = FULL,
    scope_reason: str | None = None,
    top_clients: tuple[dict, ...] | None = None,
) -> WafDetectionContext:
    obs = observation(
        counts=BreakdownCounts(client_events=30, top_clients=top_clients, client_sources=1),
        breakdowns=BreakdownStatus(client=EXACT),
    )
    return context(
        obs, novelty=novelty() if facts == "default" else facts,
        verdict_scope=scope, scope_reason=scope_reason, baseline=None,
    )


class TestNewEndpointExits:
    def test_no_novelty_facts_is_insufficient(self) -> None:
        assert_insufficient(
            detect_new_endpoint_access(new_ctx(None)), "first appearance was not established"
        )

    def test_a_foreign_novelty_object_is_not_trusted(self) -> None:
        assert_insufficient(detect_new_endpoint_access(new_ctx({"within_window": True})))

    def test_outside_the_window_is_healthy_established(self) -> None:
        v = detect_new_endpoint_access(new_ctx(novelty(within_window=False)))
        assert_healthy(v)
        assert "established endpoint" in v.evidence.reason
        assert "12-bucket" in v.evidence.reason
        assert v.evidence.observed_value is None

    @pytest.mark.parametrize("scope", [INC, NONE])
    def test_established_is_healthy_even_without_a_full_scope(self, scope) -> None:
        # Provable from first_seen_at alone; no history claim is needed.
        v = detect_new_endpoint_access(new_ctx(novelty(within_window=False), scope=scope))
        assert_healthy(v)

    def test_scope_none_names_the_history_reason(self) -> None:
        v = detect_new_endpoint_access(new_ctx(
            novelty(history=HistoryFacts(complete=False, reason="bucket 12 was never collected")),
            scope=NONE, scope_reason="scope reason",
        ))
        assert_insufficient(v, "novelty cannot be established", "bucket 12 was never collected")

    def test_scope_none_falls_back_to_the_scope_reason_then_a_default(self) -> None:
        v = detect_new_endpoint_access(new_ctx(
            novelty(history=HistoryFacts(complete=False)), scope=NONE, scope_reason="scope reason"
        ))
        assert_insufficient(v, "scope reason")
        v = detect_new_endpoint_access(new_ctx(
            novelty(history=HistoryFacts(complete=False)), scope=NONE
        ))
        assert_insufficient(v, "history is incomplete")

    def test_appearance_flood_is_insufficient(self) -> None:
        v = detect_new_endpoint_access(new_ctx(novelty(
            appearance_flood="admission was truncated in the first-appearance bucket",
            requests_since_first_seen=500, buckets_with_traffic=9,
        )))
        assert_insufficient(v, "admission was truncated")

    def test_flood_wins_over_materiality_and_over_a_small_count(self) -> None:
        v = detect_new_endpoint_access(new_ctx(novelty(
            appearance_flood="discovery truncated", requests_since_first_seen=1,
            buckets_with_traffic=1,
        )))
        assert_insufficient(v, "discovery truncated")

    def test_scope_none_wins_over_flood(self) -> None:
        v = detect_new_endpoint_access(new_ctx(novelty(appearance_flood="flood"), scope=NONE))
        assert_insufficient(v, "novelty cannot be established")


class TestNewEndpointMateriality:
    SMALL: ClassVar[list] = [
        ("requests", dict(requests_since_first_seen=19, buckets_with_traffic=5)),
        ("buckets", dict(requests_since_first_seen=500, buckets_with_traffic=1)),
        ("both", dict(requests_since_first_seen=3, buckets_with_traffic=1)),
    ]

    @pytest.mark.parametrize(("label", "kw"), SMALL, ids=[s[0] for s in SMALL])
    def test_below_materiality_is_healthy_under_full(self, label, kw) -> None:
        v = detect_new_endpoint_access(new_ctx(novelty(**kw)))
        assert_healthy(v)
        assert "has not reached 20 requests in 2 buckets" in v.evidence.reason
        assert v.evidence.observed_value == float(kw["requests_since_first_seen"])
        assert v.evidence.expected_value == 0.0

    @pytest.mark.parametrize(("label", "kw"), SMALL, ids=[s[0] for s in SMALL])
    def test_below_materiality_is_insufficient_under_increase_only(self, label, kw) -> None:
        v = detect_new_endpoint_access(new_ctx(novelty(**kw), scope=INC))
        assert_insufficient(v, "lower bounds")

    @pytest.mark.parametrize(("label", "kw"), SMALL, ids=[s[0] for s in SMALL])
    def test_below_materiality_is_insufficient_when_the_counts_are_lower_bounds(
        self, label, kw
    ) -> None:
        v = detect_new_endpoint_access(new_ctx(novelty(lower_bound=True, **kw)))
        assert_insufficient(v, "lower bounds")

    def test_materiality_minimums_are_inclusive(self) -> None:
        v = detect_new_endpoint_access(new_ctx(
            novelty(requests_since_first_seen=20, buckets_with_traffic=2)
        ))
        assert v.is_anomalous

    def test_thresholds_come_from_the_behavior_settings(self) -> None:
        ctx = new_ctx(novelty(requests_since_first_seen=30, buckets_with_traffic=3))
        ctx.behavior = WafBehaviorThresholds(new_endpoint_min_requests=31)
        assert_healthy(detect_new_endpoint_access(ctx))
        ctx.behavior = WafBehaviorThresholds(new_endpoint_min_buckets=4)
        assert_healthy(detect_new_endpoint_access(ctx))


class TestNewEndpointAnomalous:
    def test_exact(self) -> None:
        v = detect_new_endpoint_access(new_ctx())
        ev, x = v.evidence, v.evidence.extra
        assert v.detector_type is WafAnomalyType.WAF_NEW_ENDPOINT_ACCESS
        assert ev.signal is SIGNAL.ANOMALOUS
        assert ev.severity is Severity.MEDIUM
        assert ev.observed_value == 30.0 and ev.expected_value == 0.0
        assert ev.threshold == 20.0
        assert ev.sample_count == 2016
        assert ev.baseline_low is None and ev.baseline_high is None
        assert ev.deviation_score is None
        assert "at least" not in ev.reason
        assert "GET /login" in ev.reason and "3 buckets" in ev.reason
        assert x["observed"]["requests"] == 30
        assert x["observed"]["buckets_with_traffic"] == 3
        assert x["observed"]["measurement"] == {"requests": "EXACT"}
        assert x["observed"]["first_seen_at"] == FIRST_SEEN.isoformat()
        assert x["observed_measurement"] == "EXACT"
        assert x["expected"]["history_buckets_verified"] == 2016
        assert x["expected"]["history_start"] == (START - timedelta(days=7)).isoformat()
        assert "3 requests" in x["expected"]["claim"]
        assert x["materiality"]["min_requests"] == 20
        assert x["materiality"]["min_buckets"] == 2
        assert x["measurement_completeness"]["history"] == "COMPLETE"
        assert x["absolute_delta"] == 30.0
        assert "deployment" in x["caveat"]
        assert x["provenance"]["record"] == "waf_endpoint"

    def test_the_claim_is_not_never_requested(self) -> None:
        reason = detect_new_endpoint_access(new_ctx()).evidence.reason
        assert "never requested 3 or more times in one bucket" in reason

    @pytest.mark.parametrize(
        ("scope", "bound"), [(INC, False), (FULL, True), (INC, True)],
        ids=["scope-lower", "novelty-lower", "both"],
    )
    def test_lower_bound_fires_when_it_still_clears_materiality(self, scope, bound) -> None:
        v = detect_new_endpoint_access(new_ctx(novelty(lower_bound=bound), scope=scope))
        assert v.is_anomalous
        assert v.evidence.reason.count("at least 30 requests") == 1
        assert v.evidence.extra["observed"]["measurement"] == {"requests": "LOWER_BOUND"}
        assert v.evidence.extra["observed_measurement"] == "LOWER_BOUND"

    def test_contributors_are_the_only_place_raw_ips_appear(self) -> None:
        tops = ({"source_ip": "192.0.2.44", "requests": 22},)
        v = detect_new_endpoint_access(new_ctx(top_clients=tops))
        c = v.evidence.extra["contributors"]
        assert c["items"][0]["source_ip"] == "192.0.2.44"
        rest = {k: val for k, val in v.evidence.extra.items() if k != "contributors"}
        assert "192.0.2.44" not in json.dumps(rest, default=str) + v.evidence.reason


# ------------------------------------------------------------------ type sanity
def test_detectors_are_pure_and_repeatable() -> None:
    ctx = ratio_ctx("block", 40)
    a: Callable = detect_block_ratio_spike
    assert a(ctx).evidence.reason == a(ctx).evidence.reason
