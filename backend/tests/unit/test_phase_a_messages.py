"""MSG-5: the Phase A detector sentences and the lifecycle transition reasons as message codes.

Every converted site is pinned the same way: the English text the old code built (these literals
were taken from the HEAD detectors and lifecycle, 2026-10-29), the code, and the wire params.
The guards at the end keep a future site from being added uncoded.
"""

from __future__ import annotations

import ast
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.anomaly import detectors
from app.anomaly.detectors import (
    BaselineCell,
    DetectionContext,
    MetricPoint,
    detect_cardinality_drop,
    detect_collection_error,
    detect_no_events,
    detect_parsing_degradation,
    detect_repeated_payload,
    detect_timestamp_delay,
    detect_unknown_event_spike,
    detect_volume_drop,
    detect_volume_spike,
)
from app.anomaly.engine import MESSAGE_SUPPRESSED_BY_NO_EVENTS, AnomalyEngine, EvaluationReport
from app.anomaly.evidence import AnomalyEvidence, healthy, insufficient, sentence, unknown
from app.anomaly.lifecycle import LifecycleInput, advance_counters, next_state
from app.anomaly.thresholds import Thresholds
from app.core.config import Settings
from app.i18n.messages import REGISTRY, MessageRef, render
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    DetectorSignal,
)
from app.models.log_source import AnomalyStateTransition, LogSourceAnomaly
from app.models.waf import WafAnomalyTransition
from app.providers.base import LogSourceTelemetry

START = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)
BUCKET = 300
NBSP = chr(0xA0)


def _point(**kw: Any) -> MetricPoint:
    base: dict[str, Any] = dict(
        interval_start=START,
        interval_end=START + timedelta(seconds=BUCKET),
        completeness=BucketCompleteness.COMPLETE,
        event_count=600,
        average_eps=2.0,
        peak_eps=2.0,
        last_event_at=None,
        event_delay_seconds=1.0,
        unknown_event_count=0,
        stored_event_count=600,
        parsed_username_ratio=0.9,
        parsed_source_ip_ratio=0.9,
        distinct_qid_count=3,
        distinct_username_count=3,
        distinct_source_ip_count=30,
        collection_error_count=0,
        payload_signature=None,
    )
    base.update(kw)
    return MetricPoint(**base)


def _cell(median: float = 2.0, **kw: Any) -> BaselineCell:
    base: dict[str, Any] = dict(
        median=median,
        mad=0.15,
        p05=median * 0.85,
        p95=median * 1.15,
        sample_count=30,
        is_reliable=True,
        completeness=1.0,
        baseline_version=2,
    )
    base.update(kw)
    return BaselineCell(**base)


def _ctx(
    point: MetricPoint,
    baselines: dict[str, BaselineCell] | None = None,
    *,
    thresholds: Thresholds | None = None,
    **kw: Any,
) -> DetectionContext:
    return DetectionContext(
        point=point,
        baselines=baselines if baselines is not None else {"average_eps": _cell()},
        thresholds=thresholds or Thresholds(),
        expected_interval_seconds=float(BUCKET),
        **kw,
    )


@dataclass
class Case:
    id: str
    detector: Callable[[DetectionContext], AnomalyEvidence]
    ctx: DetectionContext
    text: str
    code: str
    params: dict[str, Any] = field(default_factory=dict)
    signal: DetectorSignal | None = None


def _zero_bucket(**kw: Any) -> MetricPoint:
    return _point(event_count=0, average_eps=0.0, stored_event_count=0, **kw)


def _events_cell(median: float = 600.0) -> dict[str, BaselineCell]:
    return {"event_count": _cell(median)}


def _percent(ratio: float) -> dict[str, Any]:
    return {"kind": "percent", "ratio": ratio, "precision": 0}


_DET = "anomaly.detector."

CASES: list[Case] = [
    # --- silence --------------------------------------------------------------------------
    Case(
        "no_events.incomplete",
        detect_no_events,
        _ctx(_zero_bucket(completeness=BucketCompleteness.PARTIAL), _events_cell()),
        "bucket is partial; absence of events is not evidence of silence",
        _DET + "no_events_bucket_incomplete",
        {"completeness": "PARTIAL"},
        DetectorSignal.UNKNOWN,
    ),
    Case(
        "no_events.not_expected",
        detect_no_events,
        _ctx(_zero_bucket(), _events_cell(), is_expected_active=False),
        "source is not expected to be active this interval",
        _DET + "no_events_not_expected",
        {},
        DetectorSignal.HEALTHY,
    ),
    Case(
        "no_events.normally_inactive",
        detect_no_events,
        _ctx(_zero_bucket(), _events_cell(0.0)),
        "this source is normally inactive at this hour",
        _DET + "no_events_normally_inactive",
        {},
        DetectorSignal.HEALTHY,
    ),
    Case(
        "no_events.no_baseline",
        detect_no_events,
        _ctx(_zero_bucket(), {}),
        "no reliable activity baseline for this weekday/hour cell yet",
        _DET + "no_events_no_baseline",
        {},
        DetectorSignal.UNKNOWN,
    ),
    Case(
        "no_events.grace",
        detect_no_events,
        _ctx(_zero_bucket(), _events_cell()),
        "empty bucket within the 1-bucket silence grace period",
        _DET + "no_events_grace",
        {"grace": 1},
        DetectorSignal.HEALTHY,
    ),
    Case(
        "no_events.streak",
        detect_no_events,
        _ctx(_zero_bucket(), _events_cell(), preceding_empty_buckets=2),
        "no events for 3 consecutive bucket(s) where the baseline expects about 600 events",
        _DET + "no_events_streak",
        {"streak": 3, "expected": 600.0},
        DetectorSignal.ANOMALOUS,
    ),
    Case(
        "no_events.healthy_default",
        detect_no_events,
        _ctx(_point(), _events_cell()),
        "within expected range",
        _DET + "within_range",
        {},
        DetectorSignal.HEALTHY,
    ),
    # --- volume ---------------------------------------------------------------------------
    Case(
        "volume.incomplete",
        detect_volume_spike,
        _ctx(_point(completeness=BucketCompleteness.MISSING)),
        "metric bucket is missing, not a whole observation",
        _DET + "volume_bucket_incomplete",
        {"completeness": "MISSING"},
        DetectorSignal.UNKNOWN,
    ),
    Case(
        "volume.no_baseline",
        detect_volume_drop,
        _ctx(_point(), {}),
        "no volume baseline for this weekday/hour cell yet",
        _DET + "volume_no_baseline",
        {},
        DetectorSignal.UNKNOWN,
    ),
    Case(
        "volume.baseline_samples",
        detect_volume_spike,
        _ctx(_point(), {"average_eps": _cell(is_reliable=False, sample_count=4)}),
        "baseline has 4 sample(s), below the minimum required for a defensible verdict",
        _DET + "volume_baseline_samples",
        {"samples": 4},
        DetectorSignal.UNKNOWN,
    ),
    Case(
        "volume.min_events",
        detect_volume_spike,
        _ctx(_point(average_eps=0.125, event_count=37), {"average_eps": _cell(0.125)}),
        "38 events/bucket is below the 50 minimum for a defensible volume verdict",
        _DET + "volume_min_events",
        {"events": 37.5, "minimum": 50.0},
        DetectorSignal.HEALTHY,
    ),
    Case(
        "volume.drop_undefined",
        detect_volume_drop,
        _ctx(
            _point(average_eps=1.0),
            {"average_eps": _cell(0.0, mad=0.0)},
            thresholds=Thresholds(min_bucket_events=0.0),
        ),
        "baseline expects no traffic; a drop is not defined",
        _DET + "volume_drop_undefined",
        {},
        DetectorSignal.HEALTHY,
    ),
    Case(
        "volume.ratio_not_reached",
        detect_volume_spike,
        _ctx(_point(average_eps=3.0)),
        "observed/expected ratio 1.50 has not reached 2.00",
        _DET + "volume_ratio_not_reached",
        {"ratio": 1.5, "limit": 2.0},
        DetectorSignal.HEALTHY,
    ),
    Case(
        "volume.ratio_not_fallen",
        detect_volume_drop,
        _ctx(_point(average_eps=1.5)),
        "observed/expected ratio 0.75 has not fallen to 0.50",
        _DET + "volume_ratio_not_fallen",
        {"ratio": 0.75, "limit": 0.5},
        DetectorSignal.HEALTHY,
    ),
    Case(
        "volume.delta_below_min",
        detect_volume_spike,
        _ctx(
            _point(average_eps=0.8),
            {"average_eps": _cell(0.4)},
            thresholds=Thresholds(min_absolute_delta_events=500.0),
        ),
        "change of 120 events/bucket is below the 500 minimum absolute delta",
        _DET + "volume_delta_below_min",
        {"delta": 120.0, "minimum": 500.0},
        DetectorSignal.HEALTHY,
    ),
    Case(
        "volume.z_not_reached",
        detect_volume_spike,
        _ctx(_point(average_eps=4.5), {"average_eps": _cell(mad=3.0)}),
        "robust z=0.56 has not reached the ±3.5 threshold",
        _DET + "volume_z_not_reached",
        {"score": 0.562, "threshold": 3.5},
        DetectorSignal.HEALTHY,
    ),
    Case(
        "volume.mad_inside_band",
        detect_volume_spike,
        _ctx(_point(average_eps=5.0), {"average_eps": _cell(mad=0.0, p05=1.0, p95=50.0)}),
        "robust score unavailable (MAD=0) and observed value is inside the expected band "
        "[1.00, 50.00]",
        _DET + "volume_mad_inside_band",
        {"lower": 1.0, "upper": 50.0},
        DetectorSignal.HEALTHY,
    ),
    Case(
        "volume.spike_fired",
        detect_volume_spike,
        _ctx(_point(average_eps=6.0)),
        "average EPS 6.00 is above the baseline median 2.00 (3.00x expected, 1200 "
        "events/bucket); robust z=17.99 (threshold ±3.5)",
        _DET + "volume_above",
        {
            "observed": 6.0,
            "expected": 2.0,
            "ratio_text": {"code": _DET + "ratio_times_expected", "params": {"ratio": 3.0}},
            "delta": 1200.0,
            "basis": {"code": _DET + "basis_z", "params": {"score": 17.99, "threshold": 3.5}},
        },
        DetectorSignal.ANOMALOUS,
    ),
    Case(
        "volume.drop_fired",
        detect_volume_drop,
        _ctx(_point(average_eps=0.4)),
        "average EPS 0.40 is below the baseline median 2.00 (0.20x expected, 480 events/bucket); "
        "robust z=-7.19 (threshold ±3.5)",
        _DET + "volume_below",
        {
            "observed": 0.4,
            "expected": 2.0,
            "ratio_text": {"code": _DET + "ratio_times_expected", "params": {"ratio": 0.2}},
            "delta": 480.0,
            "basis": {"code": _DET + "basis_z", "params": {"score": -7.19, "threshold": 3.5}},
        },
        DetectorSignal.ANOMALOUS,
    ),
    Case(
        "volume.spike_from_zero",
        detect_volume_spike,
        _ctx(_point(average_eps=5.0), {"average_eps": _cell(0.0, mad=0.0, p05=0.0, p95=0.0)}),
        "average EPS 5.00 is above the baseline median 0.00 (expected no traffic, 1500 "
        "events/bucket); robust score unavailable (MAD=0); observed value is outside the "
        "expected band [0.00, 0.00]",
        _DET + "volume_above",
        {
            "observed": 5.0,
            "expected": 0.0,
            "ratio_text": {"code": _DET + "ratio_no_traffic", "params": {}},
            "delta": 1500.0,
            "basis": {
                "code": _DET + "basis_mad_outside",
                "params": {"lower": 0.0, "upper": 0.0},
            },
        },
        DetectorSignal.ANOMALOUS,
    ),
    # --- parsing, unknown events, delay --------------------------------------------------
    Case(
        "parsing.no_data",
        detect_parsing_degradation,
        _ctx(_point(parsed_username_ratio=None, parsed_source_ip_ratio=None)),
        "no parse-ratio data",
        _DET + "parsing_no_data",
        {},
        DetectorSignal.UNKNOWN,
    ),
    Case(
        "parsing.below_floor",
        detect_parsing_degradation,
        _ctx(_point(parsed_username_ratio=0.3)),
        "field-extraction ratio 0.30 fell below floor 0.50",
        _DET + "parsing_below_floor",
        {"ratio": 0.3, "floor": 0.5},
        DetectorSignal.ANOMALOUS,
    ),
    Case(
        "parsing.healthy",
        detect_parsing_degradation,
        _ctx(_point()),
        "within expected range",
        _DET + "within_range",
        {},
        DetectorSignal.HEALTHY,
    ),
    Case(
        "unknown.no_events",
        detect_unknown_event_spike,
        _ctx(_zero_bucket()),
        "no events to classify",
        _DET + "unknown_no_events",
        {},
        DetectorSignal.UNKNOWN,
    ),
    Case(
        "unknown.above_ceiling",
        detect_unknown_event_spike,
        _ctx(_point(unknown_event_count=400)),
        "unknown-event ratio 0.67 exceeds ceiling 0.20",
        _DET + "unknown_above_ceiling",
        {"ratio": 400 / 600, "ceiling": 0.2},
        DetectorSignal.ANOMALOUS,
    ),
    Case(
        "delay.no_measurement",
        detect_timestamp_delay,
        _ctx(_point(event_delay_seconds=None)),
        "no delay measurement",
        _DET + "delay_no_measurement",
        {},
        DetectorSignal.UNKNOWN,
    ),
    Case(
        "delay.exceeds",
        detect_timestamp_delay,
        _ctx(_point(event_delay_seconds=901.4)),
        "event delay 901s exceeds 900s",
        _DET + "delay_exceeds",
        {"delay": 901.4, "limit": 900.0},
        DetectorSignal.ANOMALOUS,
    ),
    # --- cardinality, collection errors, repeated payload ---------------------------------
    Case(
        "cardinality.no_baseline",
        detect_cardinality_drop,
        _ctx(_point(), {}),
        "no reliable cardinality baseline",
        _DET + "cardinality_no_baseline",
        {},
        DetectorSignal.UNKNOWN,
    ),
    Case(
        "cardinality.dropped",
        detect_cardinality_drop,
        _ctx(_point(distinct_source_ip_count=3), {"distinct_source_ip_count": _cell(40.0)}),
        f"distinct source IPs 3 dropped below 40{NBSP}% of baseline median 40",
        _DET + "cardinality_dropped",
        {"count": 3, "fraction": _percent(0.4), "median": 40.0},
        DetectorSignal.ANOMALOUS,
    ),
    Case(
        "collection.errors",
        detect_collection_error,
        _ctx(_point(collection_error_count=3)),
        "3 collection error(s) in the interval",
        _DET + "collection_errors",
        {"count": 3},
        DetectorSignal.ANOMALOUS,
    ),
    Case(
        "payload.no_signature",
        detect_repeated_payload,
        _ctx(_point()),
        "no payload signature",
        _DET + "payload_no_signature",
        {},
        DetectorSignal.UNKNOWN,
    ),
    Case(
        "payload.repeated",
        detect_repeated_payload,
        _ctx(_point(payload_signature="a"), recent_signatures=["b", "a", "a"]),
        "identical payload signature repeated for 3 consecutive intervals",
        _DET + "payload_repeated",
        {"streak": 3},
        DetectorSignal.ANOMALOUS,
    ),
]


def _close(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and actual.keys() == expected.keys()
            and all(_close(actual[k], v) for k, v in expected.items())
        )
    if isinstance(expected, float):
        return isinstance(actual, (int, float)) and actual == pytest.approx(expected, abs=0.01)
    return bool(actual == expected)


class TestDetectorSentences:
    @pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
    def test_text_code_and_params(self, case: Case) -> None:
        ev = case.detector(case.ctx)
        assert ev.reason == case.text
        assert ev.reason_message is not None
        wire = ev.reason_message.to_wire()
        assert wire["code"] == case.code
        assert _close(wire["params"], case.params), wire["params"]
        if case.signal is not None:
            assert ev.signal is case.signal

    @pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
    def test_the_text_is_the_registry_rendering_of_the_reference(self, case: Case) -> None:
        ev = case.detector(case.ctx)
        assert ev.reason_message is not None
        assert render(ev.reason_message) == ev.reason

    @pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
    def test_the_stored_details_carry_the_code_beside_the_reason(self, case: Case) -> None:
        stored = case.detector(case.ctx).to_dict()
        assert stored["reason"] == case.text
        assert stored["reason_code"] == case.code
        assert _close(stored["reason_params"], case.params)
        assert "reason_message" not in stored

    def test_every_detector_code_is_pinned_by_a_case(self) -> None:
        nested: set[str] = set()

        def walk(params: dict[str, Any]) -> None:
            for value in params.values():
                if isinstance(value, dict) and "code" in value:
                    nested.add(value["code"])
                    walk(value["params"])

        for case in CASES:
            walk(case.params)
        pinned = {case.code for case in CASES} | nested
        registered = {s.code for s in REGISTRY if s.code.startswith(_DET)}
        # The engine's own overwrite is pinned in TestEngineSentences.
        assert registered - pinned == {_DET + "suppressed_by_no_events"}


class TestUncodedSitesCannotReturn:
    """A detector sentence is built from a reference, never from a literal or an f-string."""

    SOURCE = Path(detectors.__file__)

    def _calls(self) -> list[ast.Call]:
        tree = ast.parse(self.SOURCE.read_text())
        return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]

    def test_no_reason_keyword_is_a_literal(self) -> None:
        offenders = [
            ast.unparse(kw.value)
            for call in self._calls()
            for kw in call.keywords
            if kw.arg == "reason" and isinstance(kw.value, (ast.Constant, ast.JoinedStr))
        ]
        assert offenders == []

    def test_every_verdict_helper_call_passes_a_reference(self) -> None:
        names = {"healthy", "unknown", "insufficient"}
        missing = [
            ast.unparse(call.func)
            for call in self._calls()
            if isinstance(call.func, ast.Name)
            and call.func.id in names
            and "reason_message" not in {kw.arg for kw in call.keywords}
        ]
        assert missing == []

    def test_every_anomalous_evidence_pairs_its_text_with_its_reference(self) -> None:
        for call in self._calls():
            if isinstance(call.func, ast.Name) and call.func.id == "AnomalyEvidence":
                kws = {kw.arg for kw in call.keywords}
                assert {"reason", "reason_message"} <= kws


class TestEvidenceHelpers:
    def _interval(self) -> dict[str, datetime]:
        return {"interval_start": START, "interval_end": START + timedelta(seconds=BUCKET)}

    def test_a_reference_renders_the_stored_sentence(self) -> None:
        ev = unknown(
            AnomalyType.NO_EVENTS,
            reason_message=MessageRef(_DET + "parsing_no_data"),
            **self._interval(),
        )
        assert ev.reason == "no parse-ratio data"

    def test_a_text_that_disagrees_with_its_reference_is_refused(self) -> None:
        with pytest.raises(ValueError, match="differs"):
            sentence("something else", MessageRef(_DET + "parsing_no_data"))

    def test_a_text_that_agrees_with_its_reference_is_kept(self) -> None:
        assert sentence("no parse-ratio data", MessageRef(_DET + "parsing_no_data")) == (
            "no parse-ratio data"
        )

    def test_a_verdict_with_neither_is_refused(self) -> None:
        with pytest.raises(ValueError, match="reason or a message"):
            unknown(AnomalyType.NO_EVENTS, **self._interval())
        with pytest.raises(ValueError, match="reason or a message"):
            insufficient(AnomalyType.NO_EVENTS, **self._interval())

    def test_a_text_without_a_reference_stays_uncoded(self) -> None:
        ev = insufficient(AnomalyType.NO_EVENTS, reason="a WAF sentence", **self._interval())
        assert ev.reason == "a WAF sentence"
        assert ev.reason_message is None
        assert "reason_code" not in ev.to_dict()

    def test_the_healthy_default_stays_uncoded_for_other_families(self) -> None:
        ev = healthy(AnomalyType.NO_EVENTS, **self._interval())
        assert ev.reason == "within expected range"
        assert ev.reason_message is None
        assert ev.reason_wire is None

    def test_reason_wire_is_the_reference_in_wire_form(self) -> None:
        ev = unknown(
            AnomalyType.NO_EVENTS,
            reason_message=MessageRef(_DET + "delay_no_measurement"),
            **self._interval(),
        )
        assert ev.reason_wire == {"code": _DET + "delay_no_measurement", "params": {}}


# --------------------------------------------------------------------------- lifecycle
def _life(
    state: AnomalyState,
    signal: DetectorSignal,
    *,
    anomalous: int = 0,
    healthy_n: int = 0,
    open_after: int = 2,
    resolve_after: int = 3,
    insufficient_data: bool = False,
    suppressed: bool = False,
) -> LifecycleInput:
    return LifecycleInput(
        state=state,
        signal=signal,
        consecutive_anomalous=anomalous,
        consecutive_healthy=healthy_n,
        open_after=open_after,
        resolve_after=resolve_after,
        insufficient_data=insufficient_data,
        suppressed=suppressed,
    )


S = AnomalyState
G = DetectorSignal
_LC = "anomaly.lifecycle."

LIFECYCLE_CASES: list[tuple[str, LifecycleInput, AnomalyState, str, str, dict[str, Any]]] = [
    (
        "already_suppressed",
        _life(S.SUPPRESSED, G.ANOMALOUS, suppressed=True),
        S.SUPPRESSED,
        "already suppressed",
        "already_suppressed",
        {},
    ),
    (
        "suppressed",
        _life(S.NORMAL, G.ANOMALOUS, suppressed=True),
        S.SUPPRESSED,
        "suppressed",
        "suppressed",
        {},
    ),
    (
        "suppression_lifted",
        _life(S.SUPPRESSED, G.HEALTHY),
        S.NORMAL,
        "suppression lifted",
        "suppression_lifted",
        {},
    ),
    (
        "no_verdict",
        _life(S.NORMAL, G.UNKNOWN, insufficient_data=True),
        S.INSUFFICIENT_DATA,
        "no verdict possible",
        "no_verdict",
        {},
    ),
    (
        "hold_not_judgeable",
        _life(S.OPEN, G.UNKNOWN, insufficient_data=True),
        S.OPEN,
        "holding state; bucket not judgeable",
        "hold_not_judgeable",
        {},
    ),
    (
        "hold_unknown",
        _life(S.OPEN, G.UNKNOWN),
        S.OPEN,
        "holding state; detector returned unknown",
        "hold_unknown",
        {},
    ),
    (
        "opened (one bucket)",
        _life(S.NORMAL, G.ANOMALOUS, anomalous=1, open_after=1),
        S.OPEN,
        "1 consecutive abnormal bucket(s)",
        "opened",
        {"count": 1},
    ),
    (
        "opened (many buckets)",
        _life(S.RESOLVED, G.ANOMALOUS, anomalous=1204, open_after=3),
        S.OPEN,
        "1204 consecutive abnormal bucket(s)",
        "opened",
        {"count": 1204},
    ),
    (
        "first_abnormal",
        _life(S.NORMAL, G.ANOMALOUS, anomalous=1),
        S.CANDIDATE,
        "first abnormal bucket",
        "first_abnormal",
        {},
    ),
    (
        "confirmed",
        _life(S.CANDIDATE, G.ANOMALOUS, anomalous=2, open_after=2),
        S.OPEN,
        "2 consecutive abnormal bucket(s) reached the confirmation threshold of 2",
        "confirmed",
        {"count": 2, "threshold": 2},
    ),
    (
        "confirmed (count above threshold)",
        _life(S.CANDIDATE, G.ANOMALOUS, anomalous=5, open_after=3),
        S.OPEN,
        "5 consecutive abnormal bucket(s) reached the confirmation threshold of 3",
        "confirmed",
        {"count": 5, "threshold": 3},
    ),
    (
        "still_confirming",
        _life(S.CANDIDATE, G.ANOMALOUS, anomalous=1, open_after=3),
        S.CANDIDATE,
        "still confirming",
        "still_confirming",
        {},
    ),
    (
        "relapsed",
        _life(S.RECOVERING, G.ANOMALOUS, anomalous=1),
        S.OPEN,
        "relapsed before recovery completed",
        "relapsed",
        {},
    ),
    (
        "still_abnormal",
        _life(S.OPEN, G.ANOMALOUS, anomalous=4),
        S.OPEN,
        "still abnormal",
        "still_abnormal",
        {},
    ),
    (
        "returned_to_normal",
        _life(S.CANDIDATE, G.HEALTHY, healthy_n=1),
        S.NORMAL,
        "returned to normal before opening",
        "returned_to_normal",
        {},
    ),
    (
        "resolved_one_normal",
        _life(S.OPEN, G.HEALTHY, healthy_n=1, resolve_after=1),
        S.RESOLVED,
        "1 normal bucket observed",
        "resolved_one_normal",
        {},
    ),
    (
        "normal_observed",
        _life(S.OPEN, G.HEALTHY, healthy_n=1, resolve_after=3),
        S.RECOVERING,
        "normal bucket observed",
        "normal_observed",
        {},
    ),
    (
        "recovered",
        _life(S.RECOVERING, G.HEALTHY, healthy_n=3, resolve_after=3),
        S.RESOLVED,
        "3 consecutive normal bucket(s) reached the recovery threshold of 3",
        "recovered",
        {"count": 3, "threshold": 3},
    ),
    (
        "recovered (many buckets)",
        _life(S.RECOVERING, G.HEALTHY, healthy_n=1204, resolve_after=2),
        S.RESOLVED,
        "1204 consecutive normal bucket(s) reached the recovery threshold of 2",
        "recovered",
        {"count": 1204, "threshold": 2},
    ),
    (
        "still_recovering",
        _life(S.RECOVERING, G.HEALTHY, healthy_n=1, resolve_after=3),
        S.RECOVERING,
        "still recovering",
        "still_recovering",
        {},
    ),
    (
        "baseline_adequate",
        _life(S.INSUFFICIENT_DATA, G.HEALTHY, healthy_n=1),
        S.NORMAL,
        "baseline became adequate",
        "baseline_adequate",
        {},
    ),
    (
        "within_range",
        _life(S.NORMAL, G.HEALTHY, healthy_n=1),
        S.NORMAL,
        "within expected range",
        "within_range",
        {},
    ),
]


class TestLifecycleReasons:
    @pytest.mark.parametrize("row", LIFECYCLE_CASES, ids=lambda r: r[0])
    def test_state_text_code_and_params(
        self, row: tuple[str, LifecycleInput, AnomalyState, str, str, dict[str, Any]]
    ) -> None:
        _, inp, state, text, code, params = row
        decision = next_state(inp)
        assert decision.state is state
        assert decision.reason == text
        assert decision.message.to_wire() == {"code": _LC + code, "params": params}

    def test_every_lifecycle_code_is_pinned_by_a_case(self) -> None:
        registered = {s.code for s in REGISTRY if s.code.startswith(_LC)}
        assert registered == {_LC + row[4] for row in LIFECYCLE_CASES}

    def test_no_state_or_input_yields_a_reason_without_a_reference(self) -> None:
        reached: set[str] = set()
        for (
            state,
            signal,
            anomalous,
            healthy_n,
            resolve_after,
            insufficient_data,
            suppressed,
        ) in itertools.product(
            list(AnomalyState),
            list(DetectorSignal),
            (0, 1, 2, 3),
            (0, 1, 2, 3),
            (1, 3),
            (False, True),
            (False, True),
        ):
            decision = next_state(
                _life(
                    state,
                    signal,
                    anomalous=anomalous,
                    healthy_n=healthy_n,
                    resolve_after=resolve_after,
                    insufficient_data=insufficient_data,
                    suppressed=suppressed,
                )
            )
            assert decision.message.code.startswith(_LC)
            assert decision.reason == render(decision.message)
            reached.add(decision.message.code)
        assert reached == {s.code for s in REGISTRY if s.code.startswith(_LC)}

    def test_the_reason_cannot_be_set_apart_from_the_reference(self) -> None:
        decision = next_state(_life(S.NORMAL, G.ANOMALOUS, anomalous=1))
        with pytest.raises(AttributeError):
            decision.reason = "another sentence"  # type: ignore[misc]

    def test_the_counters_step_is_unchanged(self) -> None:
        assert advance_counters(G.ANOMALOUS, 2, 1) == (3, 0)
        assert advance_counters(G.HEALTHY, 2, 1) == (0, 2)
        assert advance_counters(G.UNKNOWN, 2, 1) == (2, 1)


# --------------------------------------------------------------------------- the engine
class TestEngineSentences:
    def test_the_demoted_verdict_replaces_text_and_reference_together(self) -> None:
        engine = AnomalyEngine(
            None,  # type: ignore[arg-type]
            settings=Settings(encryption_key="x" * 44),
        )
        silent = _ctx(
            _zero_bucket(),
            {
                "event_count": _cell(600.0),
                "average_eps": _cell(2.0),
                "distinct_source_ip_count": _cell(40.0),
            },
            preceding_empty_buckets=2,
            is_expected_active=True,
        )
        report = EvaluationReport(log_source_id=None, interval_start=None)  # type: ignore[arg-type]
        results = engine._run_detectors(silent, frozenset(LogSourceTelemetry), report)
        by_type = {e.anomaly_type: e for e in results}
        assert by_type[AnomalyType.NO_EVENTS].is_anomalous
        demoted = [
            e
            for e in results
            if e.anomaly_type
            in {
                AnomalyType.VOLUME_DROP,
                AnomalyType.PARSING_DEGRADATION,
                AnomalyType.UNKNOWN_EVENT_SPIKE,
                AnomalyType.CARDINALITY_DROP,
                AnomalyType.REPEATED_PAYLOAD,
            }
        ]
        assert demoted
        for e in demoted:
            assert e.signal is DetectorSignal.UNKNOWN
            assert e.reason == "suppressed: no events this interval"
            assert e.reason_message == MESSAGE_SUPPRESSED_BY_NO_EVENTS
            assert e.to_dict()["reason_code"] == _DET + "suppressed_by_no_events"

    def test_the_engine_sentences_render_the_old_english(self) -> None:
        assert render(MessageRef("anomaly.suppression.maintenance")) == "maintenance window"
        assert render(MessageRef("anomaly.resolution.recovered")) == "condition recovered"


# --------------------------------------------------------------------------- the models
class TestStoredReferences:
    def test_a_stored_reference_reads_back_as_code_and_params(self) -> None:
        anomaly = LogSourceAnomaly()
        anomaly.explanation_i18n = {"code": _DET + "collection_errors", "params": {"count": 3}}
        assert anomaly.explanation_code == _DET + "collection_errors"
        assert anomaly.explanation_params == {"count": 3}

    @pytest.mark.parametrize(
        "stored",
        [None, {}, {"code": 4, "params": {}}, {"code": "x.y", "params": []}, "text", ["a"]],
    )
    def test_a_missing_or_malformed_reference_reads_as_no_code(self, stored: Any) -> None:
        anomaly = LogSourceAnomaly()
        anomaly.explanation_i18n = stored
        assert anomaly.explanation_code is None
        assert anomaly.explanation_params is None

    @pytest.mark.parametrize("model", [AnomalyStateTransition, WafAnomalyTransition])
    def test_a_transition_reads_its_reason_reference(self, model: Any) -> None:
        row = model()
        assert row.reason_code is None
        row.reason_i18n = {"code": _LC + "opened", "params": {"count": 2}}
        assert row.reason_code == _LC + "opened"
        assert row.reason_params == {"count": 2}
