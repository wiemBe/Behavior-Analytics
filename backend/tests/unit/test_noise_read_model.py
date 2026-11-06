"""Pure rules of the noisy-sources read model (SOC-17a, design §2.4).

The database-backed routes are pinned in ``tests/integration/test_noise_api.py``.
These tests pin the arithmetic and the state rules without a database, so a
boundary (four closed items versus five, seven covered days versus twenty-eight)
fails here before it can hide behind a fixture.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.models.enums import AnomalyType, Criticality, SourceClass
from app.noise.read_model import (
    MIN_NOISE_SHARE_DENOMINATOR,
    REASON_EMPTY_WINDOW,
    REASON_NO_RELIABLE_BASELINE,
    REASON_NOT_MONITORED,
    REASON_PARTIAL_RETENTION,
    AlertGroup,
    CoverageState,
    IncidentGroup,
    Judgement,
    NoiseOrder,
    SourceSnapshot,
    SourceSummary,
    coverage_for,
    judgement_for,
    noise_share,
    order_rows,
    reason,
    summarise_source,
    window_start,
)

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def snapshot(
    *,
    name: str = "source",
    monitoring: bool = True,
    reliable: bool | None = True,
    source_id: uuid.UUID | None = None,
) -> SourceSnapshot:
    return SourceSnapshot(
        log_source_id=source_id or uuid.uuid4(),
        name=name,
        instance_id=uuid.uuid4(),
        instance_name="console",
        criticality=Criticality.MEDIUM,
        source_class=SourceClass.FIREWALL,
        monitoring_enabled=monitoring,
        custom_thresholds={},
        maintenance_mode=False,
        maintenance_until=None,
        business_hours_only=False,
        expected_interval_seconds=None,
        baseline_reliable=reliable,
        baseline_computed_at=NOW if reliable else None,
        evaluated_through=NOW,
    )


def incident(
    kind: AnomalyType,
    *,
    incidents_7d: int = 0,
    incidents_28d: int = 0,
    opened_7d: int = 0,
    opened_28d: int = 0,
    noise_closed: int = 0,
    denominator: int = 0,
    worst_ratio: float | None = None,
    worst_z: float | None = None,
) -> IncidentGroup:
    return IncidentGroup(
        log_source_id=uuid.uuid4(),
        anomaly_type=kind,
        incidents_7d=incidents_7d,
        incidents_28d=incidents_28d,
        opened_7d=opened_7d,
        opened_28d=opened_28d,
        noise_closed=noise_closed,
        noise_denominator=denominator,
        worst_ratio=worst_ratio,
        worst_z=worst_z,
    )


def alert(kind: AnomalyType, *, alerts_7d: int = 0, alerts_28d: int = 0) -> AlertGroup:
    return AlertGroup(
        log_source_id=uuid.uuid4(),
        anomaly_type=kind,
        alerts_7d=alerts_7d,
        alerts_28d=alerts_28d,
    )


def row(
    *,
    name: str,
    incidents_7d: int = 0,
    incidents_28d: int = 0,
    alerts_7d: int = 0,
    alerts_28d: int = 0,
    noise_share: float | None = None,
) -> SourceSummary:
    source = summarise_source(
        snapshot(name=name),
        [
            incident(
                AnomalyType.VOLUME_SPIKE,
                incidents_7d=incidents_7d,
                incidents_28d=incidents_28d,
            )
        ],
        [
            alert(
                AnomalyType.VOLUME_SPIKE,
                alerts_7d=alerts_7d,
                alerts_28d=alerts_28d,
            )
        ],
    )
    # Rebuild with the share the ordering test wants to sort on.
    return replace(source, noise_share=noise_share)


class TestWindowArithmetic:
    def test_window_start_is_now_minus_the_window(self) -> None:
        assert window_start(NOW, 7) == datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
        assert window_start(NOW, 28) == datetime(2026, 10, 9, 12, 0, tzinfo=UTC)

    @pytest.mark.parametrize("days", [7, 28])
    def test_coverage_is_complete_when_retention_reaches_the_window(self, days: int) -> None:
        result = coverage_for(days, 365)
        assert result.state is CoverageState.COMPLETE
        assert result.covered_days == days
        assert result.reason is None

    def test_coverage_is_partial_when_retention_is_shorter(self) -> None:
        result = coverage_for(28, 7)
        assert result.state is CoverageState.PARTIAL
        assert result.covered_days == 7
        assert result.reason is not None
        assert result.reason.code == REASON_PARTIAL_RETENTION
        assert result.reason.text == (
            "Counts cover 7 of 28 days: incident retention is shorter than the "
            "requested window."
        )

    def test_a_seven_day_window_inside_a_seven_day_retention_is_complete(self) -> None:
        assert coverage_for(7, 7).state is CoverageState.COMPLETE


class TestNoiseShareBoundary:
    def test_four_closed_items_are_not_a_share(self) -> None:
        assert MIN_NOISE_SHARE_DENOMINATOR == 5
        assert noise_share(3, 4) is None

    def test_five_closed_items_are_a_share(self) -> None:
        assert noise_share(2, 5) == pytest.approx(0.4)

    def test_zero_noise_out_of_five_is_a_measured_zero(self) -> None:
        assert noise_share(0, 5) == 0.0

    def test_no_closed_items_is_null_not_zero(self) -> None:
        assert noise_share(0, 0) is None


class TestStateRules:
    def test_a_not_monitored_source_is_not_collected_and_counts_are_null(self) -> None:
        summary = summarise_source(
            snapshot(monitoring=False, reliable=None),
            [incident(AnomalyType.VOLUME_SPIKE, incidents_28d=9, denominator=9)],
            [alert(AnomalyType.VOLUME_SPIKE, alerts_28d=3)],
        )
        assert summary.judgement is Judgement.NOT_COLLECTED
        assert summary.judged is False
        assert summary.reason is not None
        assert summary.reason.code == REASON_NOT_MONITORED
        assert summary.incidents_7d is None
        assert summary.incidents_28d is None
        assert summary.alerts_28d is None
        assert summary.noise_share is None
        assert summary.detectors == ()

    def test_a_monitored_source_without_a_reliable_cell_is_not_judged(self) -> None:
        summary = summarise_source(snapshot(reliable=None), [], [])
        assert summary.judgement is Judgement.INSUFFICIENT_DATA
        assert summary.judged is False
        assert summary.reason is not None
        assert summary.reason.code == REASON_NO_RELIABLE_BASELINE

    def test_an_unreliable_cell_is_not_judged(self) -> None:
        summary = summarise_source(snapshot(reliable=False), [], [])
        assert summary.judgement is Judgement.INSUFFICIENT_DATA

    def test_a_reliable_cell_is_judged_even_with_zero_incidents(self) -> None:
        summary = summarise_source(snapshot(reliable=True), [], [])
        assert summary.judgement is Judgement.JUDGED
        assert summary.judged is True
        assert summary.reason is None
        # A measured zero, not a missing measurement.
        assert summary.incidents_28d == 0

    def test_judgement_for_covers_every_combination(self) -> None:
        assert judgement_for(False, True) is Judgement.NOT_COLLECTED
        assert judgement_for(True, True) is Judgement.JUDGED
        assert judgement_for(True, False) is Judgement.INSUFFICIENT_DATA
        assert judgement_for(True, None) is Judgement.INSUFFICIENT_DATA

    def test_empty_window_reason_is_registered(self) -> None:
        rendered = reason(REASON_EMPTY_WINDOW, window=7)
        assert rendered.code == REASON_EMPTY_WINDOW
        assert rendered.text == "No source raised an anomaly in the last 7 days of stored data."


class TestSourceFold:
    def test_totals_sum_detectors_and_keep_the_worst_magnitude(self) -> None:
        summary = summarise_source(
            snapshot(),
            [
                incident(
                    AnomalyType.VOLUME_SPIKE,
                    incidents_7d=2,
                    incidents_28d=5,
                    opened_7d=1,
                    opened_28d=4,
                    noise_closed=2,
                    denominator=5,
                    worst_ratio=8.0,
                    worst_z=4.2,
                ),
                incident(
                    AnomalyType.NO_EVENTS,
                    incidents_7d=1,
                    incidents_28d=1,
                    opened_28d=1,
                    noise_closed=1,
                    denominator=1,
                    worst_ratio=0.2,
                    worst_z=6.1,
                ),
            ],
            [
                alert(AnomalyType.VOLUME_SPIKE, alerts_7d=1, alerts_28d=3),
                alert(AnomalyType.NO_EVENTS, alerts_28d=1),
            ],
        )
        assert summary.incidents_7d == 3
        assert summary.incidents_28d == 6
        assert summary.opened_28d == 5
        assert summary.alerts_28d == 4
        assert summary.noise_closed == 3
        assert summary.noise_closed_total == 6
        assert summary.noise_share == pytest.approx(0.5)
        assert summary.worst_ratio == 8.0
        assert summary.worst_z == pytest.approx(6.1)

    def test_top_detector_is_the_most_frequent_and_ties_follow_the_taxonomy(self) -> None:
        summary = summarise_source(
            snapshot(),
            [
                incident(AnomalyType.VOLUME_SPIKE, incidents_28d=4),
                incident(AnomalyType.VOLUME_DROP, incidents_28d=4),
            ],
            [],
        )
        # VOLUME_DROP precedes VOLUME_SPIKE in AnomalyType, so the tie keeps it.
        assert summary.top_detector is AnomalyType.VOLUME_DROP

    def test_a_detector_with_alerts_but_no_incident_in_the_window_keeps_its_alert(self) -> None:
        summary = summarise_source(
            snapshot(),
            [],
            [alert(AnomalyType.VOLUME_SPIKE, alerts_7d=1, alerts_28d=1)],
        )
        assert summary.incidents_28d == 0
        assert summary.alerts_28d == 1
        assert summary.top_detector is None
        assert summary.detectors[0].alerts_28d == 1

    def test_per_detector_share_uses_the_same_boundary(self) -> None:
        summary = summarise_source(
            snapshot(),
            [
                incident(
                    AnomalyType.VOLUME_SPIKE,
                    incidents_28d=4,
                    noise_closed=4,
                    denominator=4,
                )
            ],
            [],
        )
        assert summary.detectors[0].noise_share is None
        assert summary.noise_share is None


class TestOrdering:
    def test_incidents_order_then_alerts(self) -> None:
        rows = [
            row(name="a", incidents_28d=2, alerts_28d=9),
            row(name="b", incidents_28d=5, alerts_28d=1),
            row(name="c", incidents_28d=5, alerts_28d=3),
        ]
        ordered = order_rows(rows, NoiseOrder.INCIDENTS, 28)
        assert [item.name for item in ordered] == ["c", "b", "a"]

    def test_equal_counts_tie_break_on_name_then_id(self) -> None:
        rows = [
            row(name="zeta", incidents_28d=1),
            row(name="Alpha", incidents_28d=1),
            row(name="beta", incidents_28d=1),
        ]
        ordered = order_rows(rows, NoiseOrder.INCIDENTS, 7)
        assert [item.name for item in ordered] == ["Alpha", "beta", "zeta"]

    def test_alerts_order_then_incidents(self) -> None:
        rows = [
            row(name="a", incidents_28d=9, alerts_28d=1),
            row(name="b", incidents_28d=1, alerts_28d=4),
        ]
        ordered = order_rows(rows, NoiseOrder.ALERTS, 28)
        assert [item.name for item in ordered] == ["b", "a"]

    def test_noise_share_orders_null_last(self) -> None:
        rows = [
            row(name="null", incidents_28d=9, noise_share=None),
            row(name="low", incidents_28d=1, noise_share=0.1),
            row(name="high", incidents_28d=1, noise_share=0.9),
        ]
        ordered = order_rows(rows, NoiseOrder.NOISE_SHARE, 28)
        assert [item.name for item in ordered] == ["high", "low", "null"]

    def test_the_selected_window_drives_the_incidents_order(self) -> None:
        rows = [
            row(name="week", incidents_7d=5, incidents_28d=5),
            row(name="month", incidents_7d=0, incidents_28d=20),
        ]
        assert [item.name for item in order_rows(rows, NoiseOrder.INCIDENTS, 7)] == [
            "week",
            "month",
        ]
        assert [item.name for item in order_rows(rows, NoiseOrder.INCIDENTS, 28)] == [
            "month",
            "week",
        ]
