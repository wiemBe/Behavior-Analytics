"""Every sentence the property-watch detectors can write, one scenario each.

`tests/unit/test_pw_messages.py` pins the English each scenario renders against a literal table
(the sentences the code wrote before MSG-4b gave them codes) and the code each one carries. The
scenarios run through the public detector entry points only, so they are independent of how a
sentence is built.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

from app.anomaly.evidence import AnomalyEvidence
from app.models.enums import PwBucketMeasurement, PwWatchStatus, WafMeasurement
from app.property_watch.detectors import (
    PwComponentFact,
    PwDetectionContext,
    PwReadingPoint,
    PwThresholds,
    detect_new_combination,
    detect_new_value,
    detect_value_disappeared,
    detect_value_drop,
    detect_value_spike,
)
from app.property_watch.facts import PwBucketFact, PwReading, PwReadingKind
from app.waf.inputs import HistoryFacts
from tests.unit.test_property_watch_detection import (
    NOW,
    bucket,
    cell,
    context,
    measured_zero,
    novelty,
    novelty_context,
    reading,
)

Scenario = Callable[[], AnomalyEvidence]

LOWER = WafMeasurement.LOWER_BOUND


def _spike(ctx: PwDetectionContext) -> Scenario:
    return lambda: detect_value_spike(ctx).evidence


def _drop(ctx: PwDetectionContext) -> Scenario:
    return lambda: detect_value_drop(ctx).evidence


def _gone(ctx: PwDetectionContext) -> Scenario:
    return lambda: detect_value_disappeared(ctx).evidence


def _new_value(ctx: PwDetectionContext) -> Scenario:
    return lambda: detect_new_value(ctx).evidence


def _combination(ctx: PwDetectionContext) -> Scenario:
    return lambda: detect_new_combination(ctx).evidence


def _disappearance(current: PwReading | None = None) -> PwDetectionContext:
    points = tuple(
        PwReadingPoint(bucket(NOW - timedelta(minutes=5 * index)), measured_zero())
        for index in range(6)
    )
    return replace(
        context(value=current or measured_zero()),
        last_seen_at=NOW - timedelta(hours=1),
        recent=points,
    )


def _known(measurement: WafMeasurement = WafMeasurement.EXACT) -> tuple[PwComponentFact, ...]:
    return (
        PwComponentFact("alice", True, measurement),
        PwComponentFact("DENY", True, measurement),
    )


def _unknown(below: bool) -> tuple[PwComponentFact, ...]:
    return (
        PwComponentFact("alice", True, WafMeasurement.EXACT),
        PwComponentFact("DENY", False, None, seen_below_admission=below),
    )


def _no_bucket() -> PwDetectionContext:
    return PwDetectionContext(
        watch_status=PwWatchStatus.OK,
        bucket=None,
        reading=PwReading(PwReadingKind.NO_BUCKET, None, None),
    )


def _no_baseline() -> PwDetectionContext:
    return replace(context(), baseline=None)


def _silent_coverage() -> PwBucketFact:
    return bucket(parsed=0, total=0)


def _window_member(**change: object) -> PwDetectionContext:
    ctx = _disappearance()
    recent = list(ctx.recent)
    victim = recent[3]
    if "bucket" in change:
        recent[3] = PwReadingPoint(change["bucket"], victim.reading)  # type: ignore[arg-type]
    if "reading" in change:
        recent[3] = PwReadingPoint(victim.bucket, change["reading"])  # type: ignore[arg-type]
    if change.get("missing"):
        del recent[3]
    return replace(ctx, recent=tuple(recent))


SCENARIOS: dict[str, Scenario] = {
    # --- guards ---
    "no_status": _spike(context(status=None)),
    "watch_blocked": _spike(context(status=PwWatchStatus.SCOPE_STALE)),
    "bucket_missing": _spike(_no_bucket()),
    "bucket_not_collected": _spike(
        context(
            fact=bucket(measurement=PwBucketMeasurement.NOT_COLLECTED),
            value=reading(None, None, kind=PwReadingKind.UNKNOWN),
        )
    ),
    "bucket_membership": _spike(context(fact=bucket(scope_changed=True))),
    "bucket_not_parsed": _spike(context(fact=bucket(parsed=0, total=100))),
    "no_baseline": _spike(_no_baseline()),
    "baseline_version": _spike(context(baseline=cell(version=2))),
    "baseline_few_visits": _spike(context(baseline=cell(samples=12, reliable=False))),
    "baseline_few_visits_one": _spike(
        context(
            baseline=cell(samples=12, reliable=False),
            thresholds=PwThresholds(baseline_min_visits=1),
        )
    ),
    "baseline_samples": _spike(context(baseline=cell(samples=5))),
    "count_unknown": _spike(context(value=reading(None, None, kind=PwReadingKind.UNKNOWN))),
    "inputs_unsupported": _drop(context(value=reading(5, LOWER))),
    "coverage_below": _drop(context(fact=bucket(parsed=62, total=100))),
    "coverage_unknown": _drop(context(fact=_silent_coverage())),
    # --- value spike ---
    "spike_min_events": _spike(context(value=reading(12))),
    "ratio_not_reached": _spike(context(value=reading(150), baseline=cell(median=100))),
    "spike_delta_below_min": _spike(context(value=reading(45), baseline=cell(median=20))),
    "z_not_reached_high": _spike(
        context(value=reading(250), baseline=cell(median=100, mad=60))
    ),
    "mad_within_p95": _spike(
        context(value=reading(130), baseline=cell(median=60, mad=0, p95=200))
    ),
    "spike_not_proven_lower": _spike(
        context(
            fact=bucket(measurement=PwBucketMeasurement.LOWER_BOUND),
            value=reading(12, LOWER),
        )
    ),
    "spike_fired": _spike(context(value=reading(520))),
    "spike_fired_lower": _spike(
        context(
            fact=bucket(measurement=PwBucketMeasurement.LOWER_BOUND),
            value=reading(520, LOWER),
        )
    ),
    "spike_fired_mad_zero": _spike(
        context(value=reading(520), baseline=cell(median=100, mad=0, p95=120))
    ),
    "spike_zero_median": _spike(
        context(value=reading(90), baseline=cell(median=0, mad=10, p05=0, p95=0))
    ),
    "spike_zero_median_lower": _spike(
        context(
            fact=bucket(measurement=PwBucketMeasurement.LOWER_BOUND),
            value=reading(90, LOWER),
            baseline=cell(median=0, mad=10, p05=0, p95=0),
        )
    ),
    # --- value drop ---
    "drop_median_floor": _drop(context(value=reading(5), baseline=cell(median=12))),
    "drop_ratio_above": _drop(context(value=reading(80), baseline=cell(median=100))),
    "drop_delta_below_min": _drop(
        context(value=reading(20), baseline=cell(median=60, mad=10, p05=40))
    ),
    "z_not_reached_low": _drop(
        context(value=reading(30), baseline=cell(median=100, mad=60))
    ),
    "mad_within_p05": _drop(
        context(value=reading(10), baseline=cell(median=100, mad=0, p05=5))
    ),
    "drop_fired": _drop(context(value=reading(10), baseline=cell(median=100, mad=10))),
    "drop_fired_mad_zero": _drop(
        context(value=reading(0), baseline=cell(median=100, mad=0, p05=80))
    ),
    # --- value disappeared ---
    "last_seen_unknown": _gone(replace(_disappearance(), last_seen_at=None)),
    "not_active": _gone(replace(_disappearance(), last_seen_at=NOW - timedelta(days=8))),
    "disappear_median_floor": _gone(replace(_disappearance(), baseline=cell(median=2))),
    "observed_positive": _gone(replace(_disappearance(), reading=reading(14))),
    "window_bucket_missing": _gone(_window_member(missing=True)),
    "window_membership": _gone(
        _window_member(bucket=bucket(NOW - timedelta(minutes=15), scope_changed=True))
    ),
    "window_count_unknown": _gone(
        _window_member(reading=reading(None, LOWER, kind=PwReadingKind.UNKNOWN))
    ),
    "window_coverage": _gone(
        _window_member(bucket=bucket(NOW - timedelta(minutes=15), parsed=50, total=100))
    ),
    "streak_short": _gone(
        replace(
            _disappearance(),
            recent=(
                *_disappearance().recent[:3],
                PwReadingPoint(bucket(NOW - timedelta(minutes=15)), reading(5)),
            ),
        )
    ),
    "disappeared_fired": _gone(_disappearance()),
    # --- new value ---
    "novelty_unestablished": _new_value(novelty_context(None)),
    "established_hand_off": _new_value(
        novelty_context(
            novelty(first_seen=NOW - timedelta(minutes=5), established_at_hand_off=NOW)
        )
    ),
    "established_outside_window": _new_value(
        novelty_context(novelty(first_seen=NOW - timedelta(hours=2)))
    ),
    "pinned_never_new": _new_value(novelty_context(novelty(pinned=True))),
    "learning_unknown": _new_value(novelty_context(novelty(first_collected=None))),
    "learning_day": _new_value(
        novelty_context(novelty(first_collected=NOW - timedelta(days=1)))
    ),
    "grace_membership": _new_value(novelty_context(novelty(membership_changed=True))),
    "history_incomplete_text": _new_value(
        novelty_context(novelty(history=HistoryFacts(False, "one bucket was never collected")))
    ),
    "history_incomplete_default": _new_value(
        novelty_context(novelty(history=HistoryFacts(False)))
    ),
    "admission_truncated": _new_value(
        novelty_context(novelty(appearance=bucket(NOW - timedelta(minutes=5), rejected=1)))
    ),
    "materiality_short": _new_value(novelty_context(novelty(events=10))),
    "materiality_lower_bound": _new_value(
        novelty_context(novelty(events=10, materiality=LOWER))
    ),
    "new_fired": _new_value(novelty_context(novelty())),
    "new_fired_lower": _new_value(
        novelty_context(novelty(events=40, traffic_buckets=3, materiality=LOWER))
    ),
    "new_value_is_combination": _new_value(
        novelty_context(novelty(property_count=2, components=_known()))
    ),
    # --- new combination ---
    "one_property": _combination(novelty_context(novelty(property_count=1))),
    "components_missing": _combination(
        novelty_context(novelty(property_count=2, components=()))
    ),
    "component_untracked": _combination(
        novelty_context(novelty(property_count=2, components=_unknown(False)))
    ),
    "component_untracked_below_admission": _combination(
        novelty_context(novelty(property_count=2, components=_unknown(True)))
    ),
    "combination_fired": _combination(
        novelty_context(novelty(property_count=2, components=_known()))
    ),
    "combination_fired_lower": _combination(
        novelty_context(
            novelty(property_count=2, components=_known(LOWER), materiality=LOWER, events=40)
        )
    ),
    "combination_materiality_short": _combination(
        novelty_context(novelty(property_count=2, components=_known(), events=3))
    ),
}
