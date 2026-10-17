from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.anomaly.capabilities import (
    PW_BASELINE_METRICS,
    PW_DETECTORS,
    BehaviorDomain,
    DetectorStatus,
    detector_status,
    pw_input_scope,
)
from app.models.enums import (
    AnomalyType,
    DetectorSignal,
    IpsAnomalyType,
    PwAnomalyType,
    PwBucketMeasurement,
    PwPinnedMeasurement,
    PwWatchStatus,
    WafAnomalyType,
    WafMeasurement,
)
from app.property_watch.baseline import PwBaselineCell, PwBaselineReading, build_value_baseline
from app.property_watch.detectors import (
    PwComponentFact,
    PwDetectionContext,
    PwNoveltyFacts,
    PwReadingPoint,
    PwThresholds,
    detect_new_combination,
    detect_new_value,
    detect_value_disappeared,
    detect_value_drop,
    detect_value_spike,
)
from app.property_watch.facts import (
    PwBucketFact,
    PwReading,
    PwReadingKind,
)
from app.property_watch.history import verify_value_history
from app.property_watch.inputs import PW_VALUE_EVENTS_METRIC, PwObservation
from app.waf.inputs import HistoryFacts
from app.waf.measurement import WafVerdictScope

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def bucket(
    when: datetime = NOW,
    *,
    measurement: PwBucketMeasurement = PwBucketMeasurement.EXACT,
    parsed: int | None = 100,
    total: int | None = 100,
    version: int = 1,
    scope_changed: bool = False,
    maintenance: bool = False,
    admission_truncated: bool = False,
    rejected: int = 0,
) -> PwBucketFact:
    collected = measurement is not PwBucketMeasurement.NOT_COLLECTED
    return PwBucketFact(
        bucket_start=when,
        bucket_seconds=300,
        watch_version=version,
        totals_complete=collected,
        values_measurement=measurement,
        pinned_measurement=PwPinnedMeasurement.NOT_REQUESTED,
        total_events=total if collected else None,
        parsed_events=parsed if collected else None,
        untracked_events=0 if measurement is PwBucketMeasurement.EXACT else None,
        scope_changed=scope_changed,
        in_maintenance=maintenance,
        admission_truncated=admission_truncated,
        rejected_over_quota=rejected,
    )


def reading(
    events: int | None = 250,
    measurement: WafMeasurement | None = WafMeasurement.EXACT,
    *,
    kind: PwReadingKind = PwReadingKind.COUNT,
) -> PwReading:
    return PwReading(kind, events, measurement)


def measured_zero() -> PwReading:
    return reading(0, WafMeasurement.EXACT, kind=PwReadingKind.MEASURED_ZERO)


def cell(
    *,
    median: float = 100.0,
    mad: float = 10.0,
    p05: float = 80.0,
    p95: float = 120.0,
    samples: int = 10,
    reliable: bool = True,
    version: int = 1,
) -> PwBaselineCell:
    return PwBaselineCell(
        metric_name=PW_VALUE_EVENTS_METRIC,
        weekday=NOW.isoweekday(),
        hour=NOW.hour,
        median=median,
        mad=mad,
        p05=p05,
        p95=p95,
        sample_count=samples,
        is_reliable=reliable,
        completeness=1.0,
        observations=[median] * samples,
        watch_version=version,
    )


def context(
    *,
    fact: PwBucketFact | None = None,
    value: PwReading | None = None,
    baseline: PwBaselineCell | None = None,
    status: PwWatchStatus | None = PwWatchStatus.OK,
    thresholds: PwThresholds | None = None,
) -> PwDetectionContext:
    return PwDetectionContext(
        watch_status=status,
        bucket=fact if fact is not None else bucket(),
        reading=value if value is not None else reading(),
        baseline=baseline if baseline is not None else cell(),
        thresholds=thresholds or PwThresholds(),
    )


def novelty(
    *,
    history: HistoryFacts | None = None,
    first_seen: datetime = NOW - timedelta(minutes=5),
    first_collected: datetime | None = NOW - timedelta(days=8),
    appearance: PwBucketFact | None = None,
    events: int = 30,
    traffic_buckets: int = 2,
    materiality: WafMeasurement = WafMeasurement.EXACT,
    pinned: bool = False,
    membership_changed: bool = False,
    property_count: int = 1,
    components: tuple[PwComponentFact, ...] = (),
) -> PwNoveltyFacts:
    return PwNoveltyFacts(
        first_seen_at=first_seen,
        history=history or HistoryFacts(complete=True),
        history_start=first_seen - timedelta(days=7),
        history_end=first_seen,
        history_buckets=2016,
        appearance_bucket=appearance or bucket(first_seen),
        events_since_first_seen=events,
        buckets_with_traffic=traffic_buckets,
        materiality_measurement=materiality,
        watch_first_collected_at=first_collected,
        pinned=pinned,
        membership_changed=membership_changed,
        property_count=property_count,
        components=components,
    )


def novelty_context(facts: PwNoveltyFacts | None) -> PwDetectionContext:
    return PwDetectionContext(
        watch_status=PwWatchStatus.OK,
        bucket=bucket(),
        reading=reading(10),
        novelty=facts,
    )


class TestCapabilities:
    def test_every_pw_type_has_one_operational_spec(self) -> None:
        assert set(PW_DETECTORS) == set(PwAnomalyType)
        assert {PW_VALUE_EVENTS_METRIC} == PW_BASELINE_METRICS
        assert {spec.domain for spec in PW_DETECTORS.values()} == {BehaviorDomain.PW_VALUE}

    def test_collection_switch_and_unknown_metric_status(self) -> None:
        spec = PW_DETECTORS[PwAnomalyType.PW_VALUE_SPIKE]
        assert detector_status(spec, SimpleNamespace(pw_collection_enabled=False)) is (
            DetectorStatus.NOT_ENABLED
        )
        assert detector_status(spec, SimpleNamespace(pw_collection_enabled=True)) is (
            DetectorStatus.OPERATIONAL
        )
        unsupported = replace(spec, baseline_metrics=frozenset({"not-built"}))
        assert detector_status(unsupported) is DetectorStatus.UNSUPPORTED

    def test_pw_names_never_widen_other_persisted_enums(self) -> None:
        for enum in (AnomalyType, WafAnomalyType, IpsAnomalyType):
            assert not any(member.value.startswith("PW_") for member in enum)

    def test_scope_comes_from_declared_inputs(self) -> None:
        obs = PwObservation(bucket(), reading(250, WafMeasurement.LOWER_BOUND))
        assert pw_input_scope(PwAnomalyType.PW_VALUE_SPIKE, obs).scope is (
            WafVerdictScope.INCREASE_ONLY
        )
        assert pw_input_scope(PwAnomalyType.PW_VALUE_DROP, obs).scope is WafVerdictScope.NONE


class TestBaselineBuilder:
    def test_each_exclusion_is_tallied_and_a_thin_cell_is_stored(self) -> None:
        points: list[PwBaselineReading] = []

        def add(
            week: int,
            *,
            fact: PwBucketFact | None = None,
            value: PwReading | None = None,
        ) -> None:
            when = NOW + timedelta(weeks=week)
            points.append(
                PwBaselineReading(
                    fact or bucket(when),
                    value or reading(100, WafMeasurement.EXACT),
                )
            )

        add(
            0,
            fact=bucket(NOW, measurement=PwBucketMeasurement.LOWER_BOUND),
            value=reading(80, WafMeasurement.LOWER_BOUND),
        )
        add(1, value=reading(None, None, kind=PwReadingKind.UNKNOWN))
        add(2, fact=bucket(NOW + timedelta(weeks=2), parsed=20, total=100))
        add(3, fact=bucket(NOW + timedelta(weeks=3), scope_changed=True))
        add(4, fact=bucket(NOW + timedelta(weeks=4), maintenance=True))
        add(5, fact=bucket(NOW + timedelta(weeks=5), version=2))
        incident_at = NOW + timedelta(weeks=6)
        add(6)
        add(7)
        add(8)

        cells, tally = build_value_baseline(
            points,
            watch_version=1,
            min_coverage=0.9,
            baseline_min_samples=3,
            incident_spans=((incident_at, incident_at),),
        )
        assert tally.as_dict() == {
            "lower_bound": 1,
            "unknown": 1,
            "low_coverage": 1,
            "scope_changed": 1,
            "maintenance": 1,
            "version_mismatch": 1,
            "known_anomaly": 1,
        }
        built = cells[(NOW.isoweekday(), NOW.hour)]
        assert built.sample_count == 2
        assert built.is_reliable is False
        assert built.median == 100.0


class TestHistoryVerification:
    @staticmethod
    def _window() -> tuple[datetime, datetime, list[PwBucketFact]]:
        start = NOW - timedelta(minutes=15)
        rows = [bucket(start + timedelta(minutes=5 * index)) for index in range(3)]
        return start, NOW, rows

    def verify(self, rows: list[PwBucketFact]) -> HistoryFacts:
        start, end, _ = self._window()
        return verify_value_history(
            rows,
            history_start=start,
            history_end=end,
            bucket_seconds=300,
            watch_version=1,
        )

    def test_complete_history(self) -> None:
        _, _, rows = self._window()
        assert self.verify(rows).complete

    @pytest.mark.parametrize(
        ("mutate", "reason"),
        [
            (lambda rows: rows[1:], "never collected"),
            (
                lambda rows: [
                    replace(
                        rows[0],
                        values_measurement=PwBucketMeasurement.LOWER_BOUND,
                        untracked_events=None,
                    ),
                    *rows[1:],
                ],
                "truncated value discovery",
            ),
            (
                lambda rows: [replace(rows[0], admission_truncated=True), *rows[1:]],
                "truncated value admission",
            ),
            (
                lambda rows: [replace(rows[0], watch_version=2), *rows[1:]],
                "another watch version",
            ),
            (
                lambda rows: [
                    replace(
                        rows[0],
                        values_measurement=PwBucketMeasurement.NOT_COLLECTED,
                        totals_complete=False,
                        total_events=None,
                        parsed_events=None,
                        untracked_events=None,
                    ),
                    *rows[1:],
                ],
                "NOT_COLLECTED",
            ),
        ],
    )
    def test_each_history_failure(self, mutate: object, reason: str) -> None:
        _, _, rows = self._window()
        changed = mutate(rows)  # type: ignore[operator]
        result = self.verify(changed)
        assert not result.complete
        assert reason in (result.reason or "")


class TestSpike:
    @pytest.mark.parametrize(
        ("ctx", "reason"),
        [
            (context(status=PwWatchStatus.DISABLED), "DISABLED"),
            (
                PwDetectionContext(PwWatchStatus.OK, None, reading(None, None), cell()),
                "bucket is missing",
            ),
            (
                context(
                    fact=bucket(measurement=PwBucketMeasurement.NOT_COLLECTED),
                    value=reading(None, None, kind=PwReadingKind.UNKNOWN),
                ),
                "NOT_COLLECTED",
            ),
            (context(fact=bucket(scope_changed=True)), "membership changed"),
            (replace(context(), baseline=None), "no value-events baseline"),
            (context(baseline=cell(samples=2, reliable=False)), "below the minimum"),
            (context(value=reading(None, None, kind=PwReadingKind.UNKNOWN)), "unknown"),
            (context(value=reading(10)), "below the 20 minimum"),
            (context(value=reading(150)), "ratio"),
            (
                context(
                    value=reading(210),
                    thresholds=replace(PwThresholds(), min_absolute_delta_events=150),
                ),
                "minimum absolute delta",
            ),
            (
                context(
                    value=reading(210),
                    baseline=cell(mad=50),
                ),
                "robust z",
            ),
        ],
    )
    def test_exits_in_guard_order(self, ctx: PwDetectionContext, reason: str) -> None:
        verdict = detect_value_spike(ctx)
        assert verdict.signal is not DetectorSignal.ANOMALOUS
        assert reason in verdict.evidence.reason

    def test_anomalous_exact_and_lower_bound(self) -> None:
        assert detect_value_spike(context()).signal is DetectorSignal.ANOMALOUS
        lower = context(
            fact=bucket(measurement=PwBucketMeasurement.LOWER_BOUND),
            value=reading(250, WafMeasurement.LOWER_BOUND),
        )
        assert detect_value_spike(lower).signal is DetectorSignal.ANOMALOUS

    def test_monotonicity_property(self) -> None:
        rng = random.Random(1701)
        for first in (rng.randint(250, 2_000) for _ in range(100)):
            later = first + rng.randint(0, 2_000)
            for count in (first, later):
                ctx = context(
                    fact=bucket(measurement=PwBucketMeasurement.LOWER_BOUND),
                    value=reading(count, WafMeasurement.LOWER_BOUND),
                )
                assert detect_value_spike(ctx).signal is DetectorSignal.ANOMALOUS


@pytest.mark.parametrize(
    "detector",
    [detect_value_spike, detect_value_drop, detect_value_disappeared],
)
@pytest.mark.parametrize(
    ("ctx", "reason"),
    [
        (context(status=PwWatchStatus.SCOPE_EMPTY), "SCOPE_EMPTY"),
        (
            PwDetectionContext(PwWatchStatus.OK, None, reading(None, None), cell()),
            "bucket is missing",
        ),
        (
            context(
                fact=bucket(measurement=PwBucketMeasurement.NOT_COLLECTED),
                value=reading(None, None, kind=PwReadingKind.UNKNOWN),
            ),
            "NOT_COLLECTED",
        ),
        (context(fact=bucket(scope_changed=True)), "membership changed"),
        (replace(context(), baseline=None), "no value-events baseline"),
        (context(baseline=cell(samples=2, reliable=False)), "below the minimum"),
        (context(value=reading(None, None, kind=PwReadingKind.UNKNOWN)), "unknown"),
    ],
)
def test_each_baseline_detector_has_the_ordered_fail_closed_exits(
    detector: object, ctx: PwDetectionContext, reason: str
) -> None:
    verdict = detector(ctx)  # type: ignore[operator]
    assert verdict.signal is DetectorSignal.UNKNOWN
    assert reason in verdict.evidence.reason


class TestDrop:
    @pytest.mark.parametrize(
        ("ctx", "reason"),
        [
            (context(status=PwWatchStatus.SCOPE_STALE), "SCOPE_STALE"),
            (context(fact=bucket(scope_changed=True)), "membership changed"),
            (context(baseline=cell(samples=2, reliable=False)), "below the minimum"),
            (
                context(
                    fact=bucket(measurement=PwBucketMeasurement.LOWER_BOUND),
                    value=reading(0, WafMeasurement.LOWER_BOUND),
                ),
                "needs exact counts",
            ),
            (context(fact=bucket(parsed=50, total=100), value=reading(0)), "coverage"),
            (context(baseline=cell(median=10), value=reading(0)), "traffic floor"),
            (context(value=reading(75)), "drop line"),
            (
                context(
                    value=reading(50),
                    thresholds=replace(PwThresholds(), min_absolute_delta_events=60),
                ),
                "minimum absolute delta",
            ),
            (context(value=reading(50), baseline=cell(mad=30)), "robust z"),
        ],
    )
    def test_exits_and_guards(self, ctx: PwDetectionContext, reason: str) -> None:
        verdict = detect_value_drop(ctx)
        assert verdict.signal is not DetectorSignal.ANOMALOUS
        assert reason in verdict.evidence.reason

    def test_exact_drop_fires_and_lower_bound_never_does(self) -> None:
        exact = context(value=reading(0), baseline=cell(mad=0, p05=80))
        assert detect_value_drop(exact).signal is DetectorSignal.ANOMALOUS
        lower = context(
            fact=bucket(measurement=PwBucketMeasurement.LOWER_BOUND),
            value=reading(0, WafMeasurement.LOWER_BOUND),
        )
        assert detect_value_drop(lower).signal is DetectorSignal.UNKNOWN


class TestDisappearance:
    def _ctx(self, current: PwReading | None = None) -> PwDetectionContext:
        points = tuple(
            PwReadingPoint(bucket(NOW - timedelta(minutes=5 * index)), measured_zero())
            for index in range(6)
        )
        return replace(
            context(value=current or measured_zero()),
            last_seen_at=NOW - timedelta(hours=1),
            recent=points,
        )

    @pytest.mark.parametrize(
        ("mutate", "reason"),
        [
            (lambda ctx: replace(ctx, last_seen_at=NOW - timedelta(days=8)), "not observed"),
            (lambda ctx: replace(ctx, baseline=cell(median=2)), "disappearance floor"),
            (lambda ctx: replace(ctx, reading=reading(1)), "carried 1 exact events"),
            (
                lambda ctx: replace(
                    ctx,
                    recent=(
                        *ctx.recent[:3],
                        PwReadingPoint(bucket(NOW - timedelta(minutes=15)), reading(5)),
                    ),
                ),
                "3 consecutive",
            ),
            (
                lambda ctx: replace(ctx, bucket=bucket(parsed=50, total=100)),
                "coverage",
            ),
        ],
    )
    def test_own_guard_exits(self, mutate: object, reason: str) -> None:
        ctx = mutate(self._ctx())  # type: ignore[operator]
        verdict = detect_value_disappeared(ctx)
        assert verdict.signal is not DetectorSignal.ANOMALOUS
        assert reason in verdict.evidence.reason

    def test_measured_zero_fires_but_unknown_zero_and_lower_bound_do_not(self) -> None:
        assert detect_value_disappeared(self._ctx()).signal is DetectorSignal.ANOMALOUS
        unknown = self._ctx(reading(None, WafMeasurement.LOWER_BOUND, kind=PwReadingKind.UNKNOWN))
        assert detect_value_disappeared(unknown).signal is DetectorSignal.UNKNOWN
        lower = self._ctx(reading(0, WafMeasurement.LOWER_BOUND))
        assert detect_value_disappeared(lower).signal is DetectorSignal.UNKNOWN


class TestNewValue:
    @pytest.mark.parametrize(
        ("facts", "signal", "reason"),
        [
            (None, DetectorSignal.UNKNOWN, "first appearance"),
            (
                novelty(first_seen=NOW - timedelta(hours=2)),
                DetectorSignal.HEALTHY,
                "established value",
            ),
            (
                novelty(history=HistoryFacts(False, "one bucket was never collected")),
                DetectorSignal.UNKNOWN,
                "never collected",
            ),
            (
                novelty(appearance=bucket(NOW - timedelta(minutes=5), rejected=1)),
                DetectorSignal.UNKNOWN,
                "flood",
            ),
            (novelty(events=10), DetectorSignal.HEALTHY, "has not reached"),
            (
                novelty(events=10, materiality=WafMeasurement.LOWER_BOUND),
                DetectorSignal.UNKNOWN,
                "lower bounds",
            ),
            (novelty(pinned=True), DetectorSignal.HEALTHY, "pinned values"),
            (
                novelty(first_collected=NOW - timedelta(days=1)),
                DetectorSignal.UNKNOWN,
                "learning: day",
            ),
            (
                novelty(membership_changed=True),
                DetectorSignal.UNKNOWN,
                "membership changed",
            ),
        ],
    )
    def test_every_exit(
        self, facts: PwNoveltyFacts | None, signal: DetectorSignal, reason: str
    ) -> None:
        verdict = detect_new_value(novelty_context(facts))
        assert verdict.signal is signal
        assert reason in verdict.evidence.reason

    def test_material_new_value_fires(self) -> None:
        assert detect_new_value(novelty_context(novelty())).signal is DetectorSignal.ANOMALOUS


class TestNewCombination:
    def known(
        self, measurement: WafMeasurement = WafMeasurement.EXACT
    ) -> tuple[PwComponentFact, ...]:
        return (
            PwComponentFact("alice", True, measurement),
            PwComponentFact("DENY", True, measurement),
        )

    def test_exclusive_classification(self) -> None:
        facts = novelty(property_count=2, components=self.known())
        ctx = novelty_context(facts)
        assert detect_new_combination(ctx).signal is DetectorSignal.ANOMALOUS
        value = detect_new_value(ctx)
        assert value.signal is DetectorSignal.HEALTHY
        assert "new combination" in value.evidence.reason

    def test_components_known_through_lower_bound_can_prove_combination(self) -> None:
        facts = novelty(
            property_count=2,
            components=self.known(WafMeasurement.LOWER_BOUND),
        )
        assert detect_new_combination(novelty_context(facts)).signal is DetectorSignal.ANOMALOUS

    def test_component_seen_only_below_admission_falls_to_new_value(self) -> None:
        components = (
            PwComponentFact("alice", True, WafMeasurement.EXACT),
            PwComponentFact("DENY", False, WafMeasurement.EXACT, seen_below_admission=True),
        )
        ctx = novelty_context(novelty(property_count=2, components=components))
        combination = detect_new_combination(ctx)
        assert combination.signal is DetectorSignal.HEALTHY
        assert "below the admission threshold" in combination.evidence.reason
        assert detect_new_value(ctx).signal is DetectorSignal.ANOMALOUS

    def test_one_property_watch_is_insufficient(self) -> None:
        verdict = detect_new_combination(novelty_context(novelty()))
        assert verdict.signal is DetectorSignal.UNKNOWN
        assert "one-property" in verdict.evidence.reason


class TestReviewedGaps:
    """Exits the inherited tests did not pin (review of the PW-3a hand-over)."""

    @pytest.mark.parametrize(
        "detector", [detect_value_spike, detect_value_drop, detect_value_disappeared]
    )
    def test_property_parsed_on_nothing_is_not_applicable(self, detector: object) -> None:
        ctx = context(fact=bucket(parsed=0, total=100))
        verdict = detector(ctx)  # type: ignore[operator]
        assert verdict.signal is DetectorSignal.UNKNOWN
        assert "NOT_APPLICABLE" in verdict.evidence.reason

    def test_maintenance_is_not_a_verdict_exit(self) -> None:
        # Design §G.8.2 lists no maintenance exit; suppression belongs to alerting.
        ctx = context(fact=bucket(maintenance=True))
        assert detect_value_spike(ctx).signal is DetectorSignal.ANOMALOUS

    def test_cell_under_another_watch_version_is_not_compared(self) -> None:
        verdict = detect_value_spike(context(baseline=cell(version=2)))
        assert verdict.signal is DetectorSignal.UNKNOWN
        assert "watch version" in verdict.evidence.reason

    def test_cell_below_min_samples_never_drives_a_verdict_even_if_flagged_reliable(self) -> None:
        verdict = detect_value_spike(context(baseline=cell(samples=3, reliable=True)))
        assert verdict.signal is DetectorSignal.UNKNOWN
        assert "below the minimum" in verdict.evidence.reason

    def test_spike_monotonic_in_a_lower_bound_count(self) -> None:
        rng = random.Random(4242)

        def fires(count: int) -> bool:
            ctx = context(
                fact=bucket(measurement=PwBucketMeasurement.LOWER_BOUND),
                value=reading(count, WafMeasurement.LOWER_BOUND),
            )
            return detect_value_spike(ctx).signal is DetectorSignal.ANOMALOUS

        assert not fires(30) and fires(400)
        for _ in range(200):
            low = rng.randint(0, 600)
            high = low + rng.randint(0, 600)
            if fires(low):
                assert fires(high)

    def test_lower_bound_never_opens_drop_or_disappearance(self) -> None:
        rng = random.Random(99)
        for _ in range(100):
            count = rng.randint(0, 400)
            lower = context(
                fact=bucket(measurement=PwBucketMeasurement.LOWER_BOUND),
                value=reading(count, WafMeasurement.LOWER_BOUND),
            )
            assert detect_value_drop(lower).signal is not DetectorSignal.ANOMALOUS
            points = tuple(
                PwReadingPoint(
                    bucket(
                        NOW - timedelta(minutes=5 * i),
                        measurement=PwBucketMeasurement.LOWER_BOUND,
                    ),
                    reading(0, WafMeasurement.LOWER_BOUND),
                )
                for i in range(6)
            )
            gone = replace(lower, last_seen_at=NOW - timedelta(hours=1), recent=points)
            assert detect_value_disappeared(gone).signal is not DetectorSignal.ANOMALOUS

    def _gone(self) -> PwDetectionContext:
        points = tuple(
            PwReadingPoint(bucket(NOW - timedelta(minutes=5 * i)), measured_zero())
            for i in range(6)
        )
        return replace(
            context(value=measured_zero()),
            last_seen_at=NOW - timedelta(hours=1),
            recent=points,
        )

    def test_disappearance_fails_closed_on_every_bad_window_member(self) -> None:
        base = self._gone()
        recent = list(base.recent)
        cases = {
            "was not supplied": replace(base, recent=tuple(recent[:3] + recent[4:])),
            "unknown, not zero": replace(
                base,
                recent=(
                    *recent[:2],
                    PwReadingPoint(
                        recent[2].bucket, reading(None, None, kind=PwReadingKind.UNKNOWN)
                    ),
                    *recent[3:],
                ),
            ),
            "unknown, not zero ": replace(
                base,
                recent=(
                    *recent[:2],
                    PwReadingPoint(recent[2].bucket, reading(0, WafMeasurement.LOWER_BOUND)),
                    *recent[3:],
                ),
            ),
            "below the floor": replace(
                base,
                recent=(
                    *recent[:2],
                    PwReadingPoint(
                        bucket(recent[2].bucket.bucket_start, parsed=10), measured_zero()
                    ),
                    *recent[3:],
                ),
            ),
            "membership changed": replace(
                base,
                recent=(
                    *recent[:2],
                    PwReadingPoint(
                        bucket(recent[2].bucket.bucket_start, scope_changed=True), measured_zero()
                    ),
                    *recent[3:],
                ),
            ),
            "not known": replace(base, last_seen_at=None),
        }
        for reason, ctx in cases.items():
            verdict = detect_value_disappeared(ctx)
            assert verdict.signal is DetectorSignal.UNKNOWN, reason
            assert reason.strip() in verdict.evidence.reason

    def test_baseline_skips_untracked_values_without_a_tally_entry(self) -> None:
        item = PwBaselineReading(
            bucket(), PwReading(PwReadingKind.NOT_AN_OBSERVATION, None, None)
        )
        cells, tally = build_value_baseline(
            [item], watch_version=1, min_coverage=0.9, baseline_min_samples=3
        )
        assert cells == {} and tally.total == 0

    def test_measured_zero_trains_as_a_sample(self) -> None:
        item = PwBaselineReading(bucket(), measured_zero())
        cells, tally = build_value_baseline(
            [item], watch_version=1, min_coverage=0.9, baseline_min_samples=3
        )
        assert tally.total == 0
        assert cells[(NOW.isoweekday(), NOW.hour)].median == 0.0

    def test_history_rejects_duplicates_and_width_mismatch(self) -> None:
        start = NOW - timedelta(minutes=15)
        rows = [bucket(start + timedelta(minutes=5 * i)) for i in range(3)]
        kwargs = {
            "history_start": start,
            "history_end": NOW,
            "bucket_seconds": 300,
            "watch_version": 1,
        }
        dup = verify_value_history([*rows, rows[0]], **kwargs)  # type: ignore[arg-type]
        assert not dup.complete and "bucket records" in (dup.reason or "")
        wide = verify_value_history(rows, **{**kwargs, "bucket_seconds": 900})  # type: ignore[arg-type]
        assert not wide.complete
        odd = verify_value_history(rows, **{**kwargs, "bucket_seconds": 400})  # type: ignore[arg-type]
        assert not odd.complete and "divisible" in (odd.reason or "")

    def test_new_combination_shares_the_novelty_exits(self) -> None:
        comps = (
            PwComponentFact("alice", True, WafMeasurement.EXACT),
            PwComponentFact("DENY", True, WafMeasurement.EXACT),
        )

        def verdict(**kw: object):  # type: ignore[no-untyped-def]
            facts = novelty(property_count=2, components=comps, **kw)  # type: ignore[arg-type]
            return detect_new_combination(novelty_context(facts))

        cases = [
            (verdict(pinned=True), DetectorSignal.HEALTHY, "pinned"),
            (verdict(first_seen=NOW - timedelta(hours=2)), DetectorSignal.HEALTHY, "established"),
            (verdict(first_collected=NOW - timedelta(days=1)), DetectorSignal.UNKNOWN, "learning"),
            (verdict(membership_changed=True), DetectorSignal.UNKNOWN, "membership"),
            (
                verdict(history=HistoryFacts(False, "bucket gone")),
                DetectorSignal.UNKNOWN,
                "bucket gone",
            ),
            (
                verdict(appearance=bucket(NOW - timedelta(minutes=5), admission_truncated=True)),
                DetectorSignal.UNKNOWN,
                "flood",
            ),
            (verdict(events=5), DetectorSignal.HEALTHY, "has not reached"),
            (
                verdict(events=5, materiality=WafMeasurement.LOWER_BOUND),
                DetectorSignal.UNKNOWN,
                "lower bounds",
            ),
        ]
        for result, signal, reason in cases:
            assert result.signal is signal, reason
            assert reason in result.evidence.reason

    def test_new_value_exit_order_established_before_history(self) -> None:
        facts = novelty(
            first_seen=NOW - timedelta(hours=2), history=HistoryFacts(False, "bucket gone")
        )
        assert detect_new_value(novelty_context(facts)).signal is DetectorSignal.HEALTHY
