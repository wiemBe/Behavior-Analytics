"""SOC-6b: rule silence and storm are pure, and every guard can only refuse."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.anomaly.capabilities import (
    RULE_DETECTORS,
    BehaviorDomain,
    DetectorStatus,
    detector_status,
    rule_verdict_scope,
)
from app.core.config import Settings
from app.models.enums import DetectorSignal, RuleAnomalyType, Severity
from app.models.rule import RuleMatchWindowStatus as S
from app.rules.anomalies import (
    RuleCell,
    RuleSubject,
    RuleThresholds,
    SilenceContext,
    StormContext,
    WindowFact,
    detect_silence,
    detect_storm,
    slot_of,
    summarize_rule_cell,
)
from app.rules.matches import MatchEvidence, floor_to_window
from app.waf.measurement import WafVerdictScope

W = 3600
DAY = 86_400
WEEK = 7 * DAY
STEP = timedelta(seconds=W)
OLD = datetime(2020, 1, 1, tzinfo=UTC)


def anchor(width: int = W) -> datetime:
    return floor_to_window(datetime(2026, 11, 4, 12, 0, tzinfo=UTC), width)


def subject(**kw: object) -> RuleSubject:
    base: dict[str, object] = {
        "qradar_id": 7,
        "rule_type": "EVENT",
        "enabled": True,
        "qradar_created_at": OLD,
        "first_seen_at": OLD,
    }
    base.update(kw)
    return RuleSubject(**base)  # type: ignore[arg-type]


def fact(start: datetime, events: int | None, *, width: int = W, status: S | str = S.COMPLETE,
         origin: str = "LIVE") -> WindowFact:
    return WindowFact(start, width, status, events, origin)


class Silence:
    """A silence scenario: a 7-day baseline of `base`, then `zeros` proven-zero windows."""

    def __init__(self, *, width: int = W, base: int | list[int] = 5, zeros: int = 3,
                 open_: bool = False, **sub: object) -> None:
        self.width = width
        self.step = timedelta(seconds=width)
        self.zeros = zeros
        self.judged = anchor(width)
        self.streak_start = self.judged - (zeros - 1) * self.step
        n = WEEK // width
        vals = base if isinstance(base, list) else [base] * n
        assert len(vals) == n
        self.windows: dict[datetime, WindowFact] = {}
        for i, v in enumerate(vals):
            s = self.streak_start - (n - i) * self.step
            self.windows[s] = fact(s, v or None, width=width)
        for i in range(zeros):
            s = self.streak_start + i * self.step
            self.windows[s] = fact(s, None, width=width)
        self.sub = subject(**sub)
        self.open_ = open_
        self.th = RuleThresholds()
        self.watermark: datetime | None = self.judged + self.step
        self.settled = self.judged + self.step

    def run(self) -> object:
        return detect_silence(
            SilenceContext(
                subject=self.sub,
                windows=list(self.windows.values()),
                window_seconds=self.width,
                judged_start=self.judged,
                settled_until=self.settled,
                inventory_watermark=self.watermark,
                incident_open=self.open_,
                thresholds=self.th,
            )
        )


def skip(ev: object) -> str | None:
    assert ev.signal is DetectorSignal.UNKNOWN  # type: ignore[attr-defined]
    assert ev.insufficient_data  # type: ignore[attr-defined]
    return ev.extra.get("skip")  # type: ignore[attr-defined]


class TestSilenceFires:
    def test_stable_rate_then_three_zeros_is_anomalous(self) -> None:
        sc = Silence()
        ev = sc.run()
        assert ev.is_anomalous and ev.severity is Severity.MEDIUM
        assert ev.observed_value == 0 and ev.expected_value == 5
        assert (ev.baseline_low, ev.baseline_high) == (5, 5)
        assert ev.threshold == 3 and ev.sample_count == 168
        assert ev.interval_start == sc.streak_start and ev.interval_end == sc.judged + STEP
        assert ev.extra["consecutive_buckets"] == 3
        assert ev.extra["unit"] == "matched_events" and ev.extra["match_evidence"] == "EXACT"
        assert ev.extra["ratio_to_baseline"] == 0.0
        assert ev.extra["is_building_block"] is False and ev.extra["qradar_id"] == 7

    def test_the_streak_is_not_in_its_own_baseline(self) -> None:
        sc = Silence(base=[2, 8] * 84)
        ev = sc.run()
        assert ev.expected_value == 5 and ev.sample_count == 168

    def test_two_zeros_report_a_streak_of_two_against_n_of_three(self) -> None:
        ev = Silence(zeros=2).run()
        assert ev.is_anomalous
        assert ev.extra["consecutive_buckets"] == 2 and ev.threshold == 3

    def test_n_is_the_threshold_it_reports(self) -> None:
        sc = Silence(zeros=2)
        sc.th = RuleThresholds(silence_windows=2)
        ev = sc.run()
        assert ev.threshold == 2 and ev.extra["consecutive_buckets"] == 2

    def test_building_block_is_marked(self) -> None:
        ev = Silence(is_building_block=True).run()
        assert ev.is_anomalous and ev.extra["is_building_block"] is True

    def test_history_windows_are_counted(self) -> None:
        sc = Silence()
        for s in list(sc.windows)[:10]:
            sc.windows[s] = fact(s, 5, origin="HISTORY")
        assert sc.run().extra["history_windows"] == 10

    def test_a_dependency_list_that_is_empty_is_allowed(self) -> None:
        ev = Silence(dependency_count=0).run()
        assert ev.is_anomalous and ev.extra["dependencies"] == 0

    def test_unknown_ids_of_the_window_ride_along(self) -> None:
        sc = Silence()
        sc.windows[sc.judged] = WindowFact(sc.judged, W, S.COMPLETE, None, "LIVE", 4)
        assert sc.run().extra["unknown_ids"] == 4


class TestSilenceNotJudged:
    @pytest.mark.parametrize("rule_type", ["COMMON", "FLOW", "OFFENSE", None, "", "weird"])
    def test_only_event_rules_are_judged(self, rule_type: str | None) -> None:
        ev = Silence(rule_type=rule_type).run()
        assert skip(ev) == "not_exact" and ev.extra["applicable"] is False

    def test_a_disabled_rule_is_not_a_subject(self) -> None:
        ev = Silence(enabled=False).run()
        assert skip(ev) == "disabled" and ev.extra["applicable"] is False

    def test_monitoring_off_skips(self) -> None:
        assert skip(Silence(health_monitoring_enabled=False).run()) == "monitoring_off"

    def test_a_disabled_dependency_is_not_silence(self) -> None:
        ev = Silence(disabled_dependencies=(9,), dependency_count=1).run()
        assert skip(ev) == "dependency_disabled"

    def test_an_unobserved_dependency_is_insufficient_not_a_skip(self) -> None:
        ev = Silence(unobserved_dependencies=(9,), dependency_count=1).run()
        assert skip(ev) == "dependency_unobserved" and ev.extra["applicable"] is True

    def test_stale_inventory(self) -> None:
        sc = Silence()
        sc.watermark = sc.judged + STEP - timedelta(seconds=1)
        assert skip(sc.run()) == "stale_inventory"
        sc.watermark = None
        assert skip(sc.run()) == "stale_inventory"

    def test_watermark_exactly_at_the_window_end_passes(self) -> None:
        sc = Silence()
        sc.watermark = sc.judged + STEP
        assert sc.run().is_anomalous

    def test_stale_inventory_holds_an_open_incident(self) -> None:
        sc = Silence(open_=True)
        sc.watermark = sc.judged
        assert skip(sc.run()) == "stale_inventory"

    def test_a_window_that_has_not_settled(self) -> None:
        sc = Silence()
        sc.settled = sc.judged + STEP - timedelta(seconds=1)
        assert skip(sc.run()) == "not_settled"

    def test_rule_held_less_than_seven_days(self) -> None:
        sc = Silence(qradar_created_at=None, first_seen_at=sc_start() - timedelta(days=3))
        assert skip(sc.run()) == "rule_too_new"

    def test_no_creation_facts_at_all(self) -> None:
        assert skip(Silence(qradar_created_at=None, first_seen_at=None).run()) == "rule_too_new"

    def test_either_creation_fact_before_the_span_is_enough(self) -> None:
        assert Silence(first_seen_at=None).run().is_anomalous

    def test_a_flip_inside_the_baseline(self) -> None:
        sc = Silence()
        sc.sub = subject(transitions=(sc.streak_start - timedelta(days=2),))
        assert skip(sc.run()) == "state_transition"

    def test_a_flip_inside_the_streak(self) -> None:
        sc = Silence()
        sc.sub = subject(transitions=(sc.streak_start + timedelta(minutes=30),))
        assert skip(sc.run()) == "state_transition"

    def test_a_flip_before_the_span_is_fine(self) -> None:
        sc = Silence()
        sc.sub = subject(transitions=(sc.streak_start - timedelta(days=8),))
        assert sc.run().is_anomalous

    def test_modified_inside_the_baseline(self) -> None:
        sc = Silence()
        sc.sub = subject(qradar_modified_at=sc.streak_start - timedelta(days=1))
        ev = sc.run()
        assert skip(ev) == "modified" and "modified at" in ev.reason

    def test_modified_exactly_at_the_span_start_refuses_and_before_it_passes(self) -> None:
        sc = Silence()
        span_start = sc.streak_start - timedelta(days=7)
        sc.sub = subject(qradar_modified_at=span_start)
        assert skip(sc.run()) == "modified"
        sc.sub = subject(qradar_modified_at=span_start - timedelta(seconds=1))
        assert sc.run().is_anomalous


def sc_start() -> datetime:
    return Silence().streak_start


class TestSilenceBaseline:
    def _hole(self, **change: object) -> Silence:
        sc = Silence()
        victim = sc.streak_start - 10 * STEP
        if change.get("drop"):
            del sc.windows[victim]
        else:
            sc.windows[victim] = fact(victim, None, **change)  # type: ignore[arg-type]
        return sc

    def test_a_missing_baseline_window(self) -> None:
        assert skip(self._hole(drop=True).run()) == "baseline_incomplete"

    def test_a_truncated_baseline_window(self) -> None:
        sc = self._hole(status=S.TRUNCATED)
        ev = sc.run()
        assert skip(ev) == "baseline_incomplete" and ev.extra["baseline_tally"]["truncated"] == 1

    def test_a_truncated_baseline_window_with_a_row_is_still_not_complete(self) -> None:
        sc = Silence()
        victim = sc.streak_start - 10 * STEP
        sc.windows[victim] = fact(victim, 9, status=S.TRUNCATED)
        assert skip(sc.run()) == "baseline_incomplete"

    def test_a_failed_baseline_window(self) -> None:
        sc = self._hole(status=S.FAILED)
        assert skip(sc.run()) == "baseline_incomplete"

    def test_an_unknown_status_fails_closed(self) -> None:
        assert skip(self._hole(status="MYSTERY").run()) == "baseline_incomplete"

    def test_a_wrong_width_baseline_window(self) -> None:
        ev = self._hole(width=1800).run()
        assert skip(ev) == "baseline_incomplete"
        assert ev.extra["baseline_tally"]["wrong_width"] == 1

    def test_an_empty_baseline(self) -> None:
        sc = Silence()
        sc.windows = {s: f for s, f in sc.windows.items() if s >= sc.streak_start}
        assert skip(sc.run()) == "baseline_incomplete"

    def test_presence_079_refuses_and_080_passes(self) -> None:
        for nonzero, expect_fire in ((79, False), (80, True)):
            sc = Silence(width=6048, base=[3] * nonzero + [0] * (100 - nonzero))
            ev = sc.run()
            if expect_fire:
                assert ev.is_anomalous and ev.extra["nonzero_fraction"] == 0.8
            else:
                assert skip(ev) == "presence_below_gate"

    def test_median_floor(self) -> None:
        sc = Silence(base=3)
        sc.th = RuleThresholds(silence_min_median=5)
        assert skip(sc.run()) == "median_below_floor"
        sc.th = RuleThresholds(silence_min_median=3)
        assert sc.run().is_anomalous

    def test_a_baseline_of_zeros_is_not_stable_and_not_healthy(self) -> None:
        ev = Silence(base=0).run()
        assert skip(ev) == "median_below_floor"
        assert ev.signal is not DetectorSignal.HEALTHY


class TestSilenceWindowUnderJudgement:
    def test_a_positive_count_is_healthy(self) -> None:
        sc = Silence()
        sc.windows[sc.judged] = fact(sc.judged, 4)
        ev = sc.run()
        assert ev.signal is DetectorSignal.HEALTHY and ev.observed_value == 4

    @pytest.mark.parametrize(
        ("status", "code"),
        [(S.FAILED, "window_failed"), ("MYSTERY", "window_failed")],
    )
    def test_failed_window(self, status: S | str, code: str) -> None:
        sc = Silence()
        sc.windows[sc.judged] = fact(sc.judged, None, status=status)
        assert skip(sc.run()) == code

    def test_missing_window(self) -> None:
        sc = Silence()
        del sc.windows[sc.judged]
        assert skip(sc.run()) == "window_missing"

    def test_wrong_width_window(self) -> None:
        sc = Silence()
        sc.windows[sc.judged] = fact(sc.judged, None, width=1800)
        assert skip(sc.run()) == "window_wrong_width"

    def test_truncated_window_without_a_row(self) -> None:
        sc = Silence()
        sc.windows[sc.judged] = fact(sc.judged, None, status=S.TRUNCATED)
        assert skip(sc.run()) == "window_truncated"

    def test_truncated_window_with_a_row_is_not_read_by_silence(self) -> None:
        sc = Silence()
        sc.windows[sc.judged] = fact(sc.judged, 4, status=S.TRUNCATED)
        ev = sc.run()
        assert skip(ev) == "window_truncated" and ev.signal is not DetectorSignal.HEALTHY

    def test_a_failed_earlier_window_cannot_extend_the_streak_and_taints_the_baseline(self) -> None:
        sc = Silence()
        bad = sc.judged - STEP
        sc.windows[bad] = fact(bad, None, status=S.FAILED)
        assert skip(sc.run()) == "baseline_incomplete"

    def test_a_count_before_the_judged_zero_makes_a_streak_of_one(self) -> None:
        sc = Silence()
        prev = sc.judged - STEP
        sc.windows[prev] = fact(prev, 5)
        ev = sc.run()
        assert ev.is_anomalous and ev.extra["consecutive_buckets"] == 1


class TestSilenceOpenIncident:
    def test_stability_is_not_retested(self) -> None:
        sc = Silence(base=0, open_=True, qradar_created_at=None, first_seen_at=None,
                     qradar_modified_at=OLD + timedelta(days=9999))
        ev = sc.run()
        assert ev.is_anomalous and ev.extra["stability_retested"] is False

    def test_it_holds_through_an_incomplete_baseline(self) -> None:
        sc = Silence(open_=True)
        del sc.windows[sc.streak_start - 10 * STEP]
        ev = sc.run()
        assert ev.is_anomalous and ev.expected_value is None

    def test_a_proven_count_is_healthy(self) -> None:
        sc = Silence(open_=True)
        sc.windows[sc.judged] = fact(sc.judged, 2)
        assert sc.run().signal is DetectorSignal.HEALTHY

    def test_an_unproven_window_holds(self) -> None:
        sc = Silence(open_=True)
        sc.windows[sc.judged] = fact(sc.judged, None, status=S.FAILED)
        assert skip(sc.run()) == "window_failed"

    def test_a_disabled_rule_is_not_applicable_so_the_engine_can_resolve(self) -> None:
        ev = Silence(open_=True, enabled=False).run()
        assert skip(ev) == "disabled" and ev.extra["applicable"] is False

    def test_the_baseline_keeps_the_incident_anchor(self) -> None:
        sc = Silence(zeros=3, open_=True)
        later = sc.judged + 2 * STEP
        for i in (1, 2):
            s = sc.judged + i * STEP
            sc.windows[s] = fact(s, None)
        ev = detect_silence(
            SilenceContext(
                subject=sc.sub, windows=list(sc.windows.values()), window_seconds=W,
                judged_start=later, settled_until=later + STEP,
                inventory_watermark=later + STEP, incident_open=True,
                incident_streak_start=sc.streak_start,
            )
        )
        assert ev.is_anomalous and ev.expected_value == 5
        assert ev.interval_start == sc.streak_start


# --- storm ---------------------------------------------------------------------------------


def cell_of(values: list[float], weeks: int = 8, **kw: object) -> RuleCell:
    judged = anchor()
    key = slot_of(judged, W)
    assert key is not None
    return summarize_rule_cell(key[0], key[1], values, weeks, **kw)  # type: ignore[arg-type]


STEADY = [100.0, 102.0, 98.0, 101.0, 99.0, 100.0, 103.0, 97.0]


class Storm:
    def __init__(self, observed: int | None = 400, *, cell: RuleCell | None = None,
                 status: S | str = S.COMPLETE, width: int = W, **sub: object) -> None:
        self.width = width
        self.judged = anchor(width)
        self.window: WindowFact | None = fact(self.judged, observed, width=width, status=status)
        self.sub = subject(**sub)
        key = slot_of(self.judged, width)
        self.cell = cell if cell is not None else (
            summarize_rule_cell(key[0], key[1], STEADY, 8) if key else None
        )
        self.th = RuleThresholds()
        self.watermark: datetime | None = self.judged + timedelta(seconds=width)
        self.settled = self.judged + timedelta(seconds=width)

    def run(self) -> object:
        return detect_storm(
            StormContext(
                subject=self.sub, window=self.window, window_seconds=self.width,
                judged_start=self.judged, settled_until=self.settled,
                inventory_watermark=self.watermark, cell=self.cell, thresholds=self.th,
            )
        )


class TestStormFires:
    def test_a_large_rise_fires_with_evidence(self) -> None:
        ev = Storm(400).run()
        assert ev.is_anomalous and ev.anomaly_type is RuleAnomalyType.RULE_STORM
        assert ev.observed_value == 400 and ev.expected_value == 100.0
        assert ev.baseline_low is not None and ev.baseline_high is not None
        assert ev.baseline_low <= 100.0 <= ev.baseline_high
        assert ev.threshold == 3.5 and ev.sample_count == 8
        assert ev.extra["ratio"] == 4.0 and ev.severity is Severity.HIGH
        assert ev.extra["visits"] == 8 and ev.extra["weekday"] == anchor().isoweekday()
        assert ev.extra["hour"] == 12 and ev.extra["absolute_delta"] == 300.0

    def test_a_smaller_rise_is_medium(self) -> None:
        ev = Storm(300).run()
        assert ev.is_anomalous and ev.severity is Severity.MEDIUM

    def test_zero_median_fires_through_the_floors_with_a_null_ratio(self) -> None:
        cell = cell_of([0.0] * 8)
        ev = Storm(80, cell=cell).run()
        assert ev.is_anomalous and ev.extra["deviation_ratio"] is None
        assert "ratio" not in ev.extra and ev.severity is Severity.MEDIUM
        assert "usual count" in ev.reason and "is 0" in ev.reason

    def test_zero_median_one_event_does_not_fire(self) -> None:
        assert Storm(1, cell=cell_of([0.0] * 8)).run().signal is DetectorSignal.HEALTHY

    def test_zero_median_below_the_delta_floor(self) -> None:
        ev = Storm(49, cell=cell_of([0.0] * 8)).run()
        assert ev.signal is DetectorSignal.HEALTHY

    def test_a_truncated_window_with_a_row_is_judged_and_says_so(self) -> None:
        ev = Storm(400, status=S.TRUNCATED).run()
        assert ev.is_anomalous and ev.extra["truncated_window"] is True

    def test_building_block_is_marked(self) -> None:
        assert Storm(400, is_building_block=True).run().extra["is_building_block"] is True


class TestStormGuards:
    def test_window_events_floor(self) -> None:
        cell = cell_of([1.0, 1, 1, 1, 1, 1, 1, 1])
        th = RuleThresholds(storm_min_absolute_delta=0)  # isolate the events floor
        low = Storm(19, cell=cell)
        low.th = th
        assert low.run().signal is DetectorSignal.HEALTHY
        at = Storm(20, cell=cell)
        at.th = th
        assert at.run().is_anomalous

    def test_ratio_199_refuses_and_200_passes(self) -> None:
        cell = cell_of([100.0] * 4 + [100.0, 100.0, 100.0, 100.0])
        th = RuleThresholds(storm_min_absolute_delta=0)
        s = Storm(199, cell=cell)
        s.th = th
        assert s.run().signal is DetectorSignal.HEALTHY
        s = Storm(200, cell=cell)
        s.th = th
        assert s.run().is_anomalous

    def test_absolute_delta_floor(self) -> None:
        cell = cell_of([30.0] * 8)
        assert Storm(79, cell=cell).run().signal is DetectorSignal.HEALTHY
        assert Storm(80, cell=cell).run().is_anomalous

    def test_robust_z_must_clear_the_threshold(self) -> None:
        cell = cell_of([10.0, 100, 200, 300, 400, 500, 600, 700])
        ev = Storm(700, cell=cell).run()
        assert ev.signal is DetectorSignal.HEALTHY and "z" in ev.reason

    def test_degenerate_mad_falls_back_to_p95(self) -> None:
        cell = cell_of([100.0] * 8)
        assert Storm(250, cell=cell).run().is_anomalous
        s = Storm(200, cell=cell_of([100.0] * 7 + [300.0]))  # p95 is 230
        s.th = RuleThresholds(storm_ratio=1.0, storm_min_absolute_delta=0)
        assert s.run().signal is DetectorSignal.HEALTHY

    def test_in_band_is_healthy_not_unknown(self) -> None:
        ev = Storm(101).run()
        assert ev.signal is DetectorSignal.HEALTHY and not ev.insufficient_data

    def test_a_proven_zero_cannot_storm_and_is_healthy(self) -> None:
        s = Storm(None)
        ev = s.run()
        assert ev.signal is DetectorSignal.HEALTHY and ev.observed_value == 0

    def test_a_truncated_window_without_a_row_is_insufficient(self) -> None:
        assert skip(Storm(None, status=S.TRUNCATED).run()) == "window_truncated"

    @pytest.mark.parametrize("status", [S.FAILED, "MYSTERY"])
    def test_failed_window(self, status: S | str) -> None:
        assert skip(Storm(400, status=status).run()) == "window_failed"

    def test_missing_window(self) -> None:
        s = Storm()
        s.window = None
        assert skip(s.run()) == "window_missing"

    def test_wrong_width_window(self) -> None:
        s = Storm()
        s.window = fact(s.judged, 400, width=1800)
        assert skip(s.run()) == "window_wrong_width"

    def test_not_settled(self) -> None:
        s = Storm()
        s.settled = s.judged
        assert skip(s.run()) == "not_settled"

    @pytest.mark.parametrize("rule_type", ["COMMON", "FLOW", None])
    def test_not_an_event_rule(self, rule_type: str | None) -> None:
        assert skip(Storm(rule_type=rule_type).run()) == "not_exact"

    def test_disabled_and_monitoring_and_dependency(self) -> None:
        assert skip(Storm(enabled=False).run()) == "disabled"
        assert skip(Storm(health_monitoring_enabled=False).run()) == "monitoring_off"
        assert skip(Storm(disabled_dependencies=(1,)).run()) == "dependency_disabled"
        assert skip(Storm(unobserved_dependencies=(1,)).run()) == "dependency_unobserved"

    def test_stale_inventory(self) -> None:
        s = Storm()
        s.watermark = s.judged
        assert skip(s.run()) == "stale_inventory"


class TestStormCell:
    def test_thin_by_samples(self) -> None:
        ev = Storm(400, cell=cell_of([100.0] * 7, weeks=7)).run()
        assert skip(ev) == "cell_thin" and ev.sample_count == 7

    def test_thin_by_visits(self) -> None:
        cell = cell_of([100.0] * 12, weeks=3)
        assert not cell.is_reliable
        assert skip(Storm(400, cell=cell).run()) == "cell_thin"

    def test_exactly_the_floor_is_reliable(self) -> None:
        assert cell_of([100.0] * 8, weeks=4).is_reliable

    def test_no_cell_and_wrong_cell(self) -> None:
        s = Storm()
        s.cell = None
        assert skip(s.run()) == "cell_missing"
        key = slot_of(s.judged, W)
        assert key is not None
        s.cell = summarize_rule_cell(key[0], (key[1] + 1) % 24, STEADY, 8)
        assert skip(s.run()) == "cell_missing"
        s.cell = summarize_rule_cell(0, key[1], [], 0)
        assert skip(s.run()) == "cell_missing"

    def test_empty_samples_are_not_reliable(self) -> None:
        cell = summarize_rule_cell(1, 1, [], 0)
        assert cell.summary is None and not cell.is_reliable

    def test_history_samples_are_carried(self) -> None:
        cell = cell_of(STEADY, history_samples=3)
        assert Storm(400, cell=cell).run().extra["history_samples"] == 3

    def test_width_that_does_not_tile_a_day(self) -> None:
        s = Storm(400, width=7000, cell=RuleCell(1, 0, None, 0, False))
        assert skip(s.run()) == "width_does_not_tile"

    def test_slot_of(self) -> None:
        t = datetime(2026, 11, 4, 12, 30, tzinfo=UTC)  # a Wednesday
        assert slot_of(t, 3600) == (3, 12)
        assert slot_of(t, 1800) == (3, 25)
        assert slot_of(t, 7000) is None
        assert slot_of(t, 0) is None
        assert slot_of(t, 86_400) == (3, 0)

    def test_slot_of_normalises_to_utc(self) -> None:
        from datetime import timezone

        t = datetime(2026, 11, 4, 15, 0, tzinfo=timezone(timedelta(hours=3)))
        assert slot_of(t, 3600) == (3, 12)

    def test_a_half_hour_width_keys_by_index(self) -> None:
        s = Storm(400, width=1800)
        assert s.run().extra["slot"] == slot_of(s.judged, 1800)[1]  # type: ignore[index]
        assert s.run().extra["hour"] is None


class TestCapabilities:
    def test_both_detectors_are_declared_exact_on_the_rule_domain(self) -> None:
        assert set(RULE_DETECTORS) == set(RuleAnomalyType)
        for spec in RULE_DETECTORS.values():
            assert spec.domain is BehaviorDomain.RULE
            assert spec.measurement is not None and spec.measurement.value == "EXACT"
            assert spec.enabling_setting == "rule_match_collection_enabled"
            assert spec.scheduled_task == "collect_rule_matches"

    @pytest.mark.parametrize("det", list(RuleAnomalyType))
    def test_scope_comes_from_the_declaration(self, det: RuleAnomalyType) -> None:
        assert rule_verdict_scope(det, MatchEvidence.EXACT).scope is WafVerdictScope.FULL
        assert rule_verdict_scope(det, MatchEvidence.LOWER_BOUND).scope is WafVerdictScope.NONE
        assert rule_verdict_scope(det, MatchEvidence.NOT_MEASURED).scope is WafVerdictScope.NONE
        assert rule_verdict_scope(det, None).scope is WafVerdictScope.NONE

    def test_status_follows_the_collection_switch(self) -> None:
        for spec in RULE_DETECTORS.values():
            assert detector_status(spec) is DetectorStatus.OPERATIONAL
            off = Settings(_env_file=None, rule_match_collection_enabled=False)
            on = Settings(_env_file=None, rule_match_collection_enabled=True)
            assert detector_status(spec, off) is DetectorStatus.NOT_ENABLED
            assert detector_status(spec, on) is DetectorStatus.OPERATIONAL
            assert detector_status(replace(spec, scheduled_task=None)) is (
                DetectorStatus.NOT_SCHEDULED
            )
            assert detector_status(replace(spec, implemented=False)) is DetectorStatus.UNSUPPORTED

    def test_the_phase_a_enum_is_not_widened(self) -> None:
        from app.models.enums import AnomalyType

        assert not {t.value for t in RuleAnomalyType} & {t.value for t in AnomalyType}
