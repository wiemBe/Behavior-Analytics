"""SOC-6g: every rule-detector sentence is coded, registered, and byte-identical.

The expected strings below are the uncoded English the detectors wrote before the catalog.
Rendering the registry reference must reproduce each one exactly; the reference must also
reach the wire through `AnomalyEvidence.to_dict()`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.anomaly import statistics as rstats
from app.i18n.messages import REGISTRY, MessageRef
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
from app.rules.matches import floor_to_window

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


def fact(
    start: datetime,
    events: int | None,
    *,
    width: int = W,
    status: S | str = S.COMPLETE,
    origin: str = "LIVE",
) -> WindowFact:
    return WindowFact(start, width, status, events, origin)


class Silence:
    """A 7-day baseline of `base`, then `zeros` proven-zero windows (as in SOC-6b's tests)."""

    def __init__(
        self,
        *,
        width: int = W,
        base: int | list[int] = 5,
        zeros: int = 3,
        open_: bool = False,
        **sub: object,
    ) -> None:
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


def cell_of(values: list[float], weeks: int = 8, **kw: object) -> RuleCell:
    judged = anchor()
    key = slot_of(judged, W)
    assert key is not None
    return summarize_rule_cell(key[0], key[1], values, weeks, **kw)  # type: ignore[arg-type]


STEADY = [100.0, 102.0, 98.0, 101.0, 99.0, 100.0, 103.0, 97.0]


class Storm:
    def __init__(
        self,
        observed: int | None = 400,
        *,
        cell: RuleCell | None = None,
        status: S | str = S.COMPLETE,
        width: int = W,
        **sub: object,
    ) -> None:
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
                subject=self.sub,
                window=self.window,
                window_seconds=self.width,
                judged_start=self.judged,
                settled_until=self.settled,
                inventory_watermark=self.watermark,
                cell=self.cell,
                thresholds=self.th,
            )
        )


REGISTERED = {spec.code for spec in REGISTRY}


def assert_coded(ev: object, expected: str) -> None:
    """The sentence is the old English, its code is registered, and both reach the wire."""
    assert ev.reason == expected  # type: ignore[attr-defined]
    message = ev.reason_message  # type: ignore[attr-defined]
    assert isinstance(message, MessageRef)
    assert message.code in REGISTERED
    wire = ev.to_dict()  # type: ignore[attr-defined]
    assert wire["reason_code"] == message.code
    assert wire["reason_params"] == message.to_wire()["params"]


class TestSilenceSentences:
    def test_not_exact(self) -> None:
        assert_coded(
            Silence(rule_type="COMMON").run(),
            "Not judged: this rule's type (COMMON) does not give exact match counts, so a "
            "window cannot prove how many times it matched.",
        )

    def test_disabled_with_sync(self) -> None:
        sc = Silence(enabled=False)
        assert_coded(
            sc.run(),
            f"Not judged: the inventory sync at {sc.watermark:%Y-%m-%d %H:%M} UTC shows this "
            "rule disabled.",
        )

    def test_disabled_without_sync(self) -> None:
        sc = Silence(enabled=False)
        sc.watermark = None
        assert_coded(sc.run(), "Not judged: the inventory sync shows this rule disabled.")

    def test_monitoring_off(self) -> None:
        assert_coded(
            Silence(health_monitoring_enabled=False).run(),
            "Not judged: health monitoring is switched off for this rule.",
        )

    def test_dependency_disabled(self) -> None:
        assert_coded(
            Silence(disabled_dependencies=(9, 10), dependency_count=2).run(),
            "Not judged: a stored building-block dependency of this rule is disabled "
            "(QRadar ids 9, 10).",
        )

    def test_dependency_unobserved(self) -> None:
        assert_coded(
            Silence(unobserved_dependencies=(9,), dependency_count=1).run(),
            "No verdict: a stored dependency of this rule is not in the inventory "
            "(QRadar ids 9).",
        )

    def test_stale_inventory(self) -> None:
        sc = Silence()
        sc.watermark = sc.judged + STEP - timedelta(seconds=1)
        assert_coded(
            sc.run(),
            "No verdict: the inventory was last synced before this window ended, so the "
            "enabled flag may be from an earlier sync.",
        )

    def test_not_settled(self) -> None:
        sc = Silence()
        sc.settled = sc.judged + STEP - timedelta(seconds=1)
        assert_coded(sc.run(), "No verdict: this window has not settled yet.")

    def test_window_missing(self) -> None:
        sc = Silence()
        del sc.windows[sc.judged]
        assert_coded(sc.run(), "No verdict: no collection record exists for this window.")

    def test_window_wrong_width(self) -> None:
        sc = Silence()
        sc.windows[sc.judged] = fact(sc.judged, None, width=1800)
        assert_coded(
            sc.run(),
            "No verdict: it was collected at another window width than the current 3600 s.",
        )

    def test_window_failed(self) -> None:
        sc = Silence()
        sc.windows[sc.judged] = fact(sc.judged, None, status=S.FAILED)
        assert_coded(sc.run(), "No verdict: its collection failed or was skipped.")

    def test_silence_truncated(self) -> None:
        sc = Silence()
        sc.windows[sc.judged] = fact(sc.judged, None, status=S.TRUNCATED)
        assert_coded(
            sc.run(),
            "No verdict: this window was truncated, so an absent rule is not a measured zero.",
        )

    def test_silence_truncated_with_row(self) -> None:
        sc = Silence()
        sc.windows[sc.judged] = fact(sc.judged, 4, status=S.TRUNCATED)
        assert_coded(sc.run(), "No verdict: this window was truncated.")

    def test_matched_count(self) -> None:
        sc = Silence()
        sc.windows[sc.judged] = fact(sc.judged, 4)
        assert_coded(sc.run(), "The rule matched 4 events in this window.")

    def test_rule_too_new(self) -> None:
        sc = Silence(qradar_created_at=None, first_seen_at=anchor() - timedelta(days=3))
        assert_coded(sc.run(), "No verdict: the inventory has not held this rule for 7 days.")

    def test_state_transition(self) -> None:
        sc = Silence()
        sc.sub = subject(transitions=(sc.streak_start - timedelta(days=2),))
        assert_coded(
            sc.run(),
            "No verdict: this rule was enabled or disabled inside the baseline or the streak "
            "(the platform noticed the flip at the recorded sync time).",
        )

    def test_modified(self) -> None:
        sc = Silence()
        modified = sc.streak_start - timedelta(days=1)
        sc.sub = subject(qradar_modified_at=modified)
        assert_coded(
            sc.run(),
            f"QRadar reports this rule modified at {modified:%Y-%m-%d %H:%M} UTC. "
            "Match anomalies are not judged across that time.",
        )

    def test_baseline_incomplete(self) -> None:
        sc = Silence()
        del sc.windows[sc.streak_start - 10 * STEP]
        assert_coded(
            sc.run(),
            "No verdict: the 7 days before the streak are not fully measured "
            "(1 missing, 0 truncated, 0 failed, 0 of another width, of 168 windows).",
        )

    def test_median_below_floor(self) -> None:
        sc = Silence(base=3)
        sc.th = RuleThresholds(silence_min_median=5)
        assert_coded(
            sc.run(),
            "No verdict: the median is 3 matched events per window, below the minimum of 5; "
            "not a stable match rate.",
        )

    def test_presence_below_gate(self) -> None:
        sc = Silence(width=6048, base=[3] * 79 + [0] * 21)
        assert_coded(
            sc.run(),
            "No verdict: the rule matched in 79% of windows, below 80%; "
            "not a stable match rate.",
        )

    def test_proven_zero(self) -> None:
        assert_coded(
            Silence().run(),
            "Proven zero matches in 3 consecutive window(s) of 3600 s; the preceding 7 days "
            "had a median of 5 matched events per window, matching in 100% of windows.",
        )

    def test_proven_zero_with_the_incident_open(self) -> None:
        sc = Silence(open_=True)
        del sc.windows[sc.streak_start - 10 * STEP]
        ev = sc.run()
        assert isinstance(ev.extra, dict) and ev.extra["stability_retested"] is False
        assert_coded(
            ev,
            "Proven zero matches in 3 consecutive window(s) of 3600 s; the incident is open "
            "and its stability is not re-tested.",
        )


class TestStormSentences:
    def test_width_does_not_tile(self) -> None:
        s = Storm(400, width=7000, cell=RuleCell(1, 0, None, 0, False))
        assert_coded(
            s.run(),
            "No verdict: a window of 7000 s does not tile a UTC day, so there is no weekday "
            "and slot to compare against.",
        )

    def test_storm_truncated(self) -> None:
        assert_coded(
            Storm(None, status=S.TRUNCATED).run(),
            "No verdict: this window was truncated and holds no row for this rule, so its "
            "count is neither zero nor small.",
        )

    def test_storm_zero(self) -> None:
        assert_coded(
            Storm(None).run(),
            "The rule had a measured zero in this window; a zero is not a storm.",
        )

    def test_cell_missing(self) -> None:
        s = Storm()
        s.cell = None
        assert_coded(
            s.run(),
            "No verdict: there is no baseline cell for this weekday and slot.",
        )

    def test_cell_thin(self) -> None:
        assert_coded(
            Storm(400, cell=cell_of([100.0] * 7, weeks=7)).run(),
            "Still learning: this weekday and slot has 7 sample(s) over 7 week(s); "
            "8 samples over 4 weeks are needed.",
        )

    def test_below_event_floor(self) -> None:
        s = Storm(19, cell=cell_of([1.0] * 8))
        s.th = RuleThresholds(storm_min_absolute_delta=0)
        assert_coded(
            s.run(),
            "19 matched events is below the 20-event floor for a storm.",
        )

    def test_ratio_below(self) -> None:
        s = Storm(199, cell=cell_of([100.0] * 8))
        s.th = RuleThresholds(storm_min_absolute_delta=0)
        assert_coded(s.run(), "1.99x the usual count is below the 2x line.")

    def test_rise_below_delta(self) -> None:
        assert_coded(
            Storm(79, cell=cell_of([30.0] * 8)).run(),
            "A rise of 49 events is below the 50-event line.",
        )

    def test_z_below(self) -> None:
        cell = cell_of([10.0, 100, 200, 300, 400, 500, 600, 700])
        summary = cell.summary
        assert summary is not None
        score, _ = rstats.robust_z_with_status(
            700, summary.median, summary.mad, mad_floor_ratio=0.1
        )
        assert_coded(
            Storm(700, cell=cell).run(),
            f"Robust z {score:.2f} is below the threshold 3.5.",
        )

    def test_mad_within_p95(self) -> None:
        cell = cell_of([100.0] * 7 + [300.0])
        summary = cell.summary
        assert summary is not None
        upper = max(summary.p95, summary.median)
        assert_coded(
            Storm(200, cell=cell).run(),
            f"The count is within the usual 95th percentile ({upper:g}) and the spread is "
            "too small for a z-score.",
        )

    def test_storm_fired_on_a_z_score(self) -> None:
        cell = cell_of(STEADY)
        summary = cell.summary
        assert summary is not None
        score, _ = rstats.robust_z_with_status(
            400, summary.median, summary.mad, mad_floor_ratio=0.1
        )
        assert_coded(
            Storm(400, cell=cell).run(),
            f"400 matched events against a usual {summary.median:g} for this weekday and "
            f"slot (4.0x, +300); robust z {score:.2f} against a threshold of 3.5.",
        )

    def test_storm_fired_through_the_degenerate_mad(self) -> None:
        cell = cell_of([100.0] * 8)
        summary = cell.summary
        assert summary is not None
        upper = max(summary.p95, summary.median)
        assert_coded(
            Storm(250, cell=cell).run(),
            f"250 matched events against a usual {summary.median:g} for this weekday and "
            f"slot (2.5x, +150); above the usual 95th percentile of {upper:g} "
            "(spread too small for a z-score).",
        )

    def test_storm_fired_from_a_zero_median(self) -> None:
        assert_coded(
            Storm(80, cell=cell_of([0.0] * 8)).run(),
            "80 matched events where the usual count for this weekday and slot is 0; "
            "above the usual 95th percentile of 0 (spread too small for a z-score).",
        )
