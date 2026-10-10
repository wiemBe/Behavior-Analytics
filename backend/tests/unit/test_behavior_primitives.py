"""Shared behavior-engine primitives: seasonal baseline rules, hysteresis
counters, and domain-generic evidence. Every behavior domain builds on these, so
they are pinned independently of any domain's persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.anomaly import statistics as rstats
from app.anomaly.evidence import healthy, insufficient
from app.anomaly.lifecycle import advance_counters
from app.anomaly.seasonal import (
    MAX_STORED_OBSERVATIONS,
    anomaly_exclusion_span,
    in_any_span,
    retained_fraction,
    seasonal_key,
    summarize_cell,
)
from app.models.enums import AnomalyType, DetectorSignal, WafAnomalyType

T0 = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)


class TestSummarizeCell:
    def test_uses_the_robust_statistics(self) -> None:
        values = [10.0, 12.0, 11.0, 50.0, 9.0, 11.0]
        cell = summarize_cell(values, min_samples=4)
        med = rstats.median(values)
        assert cell.median == med
        assert cell.mad == rstats.mad(values, med)
        assert cell.p05 == rstats.percentile(values, 5)
        assert cell.p95 == rstats.percentile(values, 95)
        assert cell.sample_count == 6
        assert cell.is_reliable is True

    def test_below_the_minimum_the_cell_is_not_reliable(self) -> None:
        assert summarize_cell([1.0, 2.0, 3.0], min_samples=4).is_reliable is False

    def test_the_minimum_is_inclusive(self) -> None:
        assert summarize_cell([1.0, 2.0, 3.0, 4.0], min_samples=4).is_reliable is True

    def test_stored_observations_are_bounded_and_keep_the_newest(self) -> None:
        values = [i + 0.123456 for i in range(250)]
        cell = summarize_cell(values, min_samples=1)
        assert len(cell.observations) == MAX_STORED_OBSERVATIONS == 200
        assert cell.observations[0] == round(50.123456, 4)
        assert cell.observations[-1] == round(249.123456, 4)
        # Statistics cover every value, not only the stored sample.
        assert cell.sample_count == 250


def test_retained_fraction() -> None:
    assert retained_fraction(0, 0) == 0.0
    assert retained_fraction(3, 4) == 0.75


def test_seasonal_key_is_iso_weekday_and_utc_hour() -> None:
    # 2026-08-03 is a Monday.
    assert seasonal_key(datetime(2026, 8, 3, 23, 55, tzinfo=UTC)) == (1, 23)


class TestExclusionSpan:
    detected = T0
    ended = T0 + timedelta(minutes=10)
    resolved = T0 + timedelta(minutes=25)
    window_end = T0 + timedelta(days=1)

    def test_a_resolved_incident_excludes_through_its_resolution(self) -> None:
        span = anomaly_exclusion_span(self.detected, self.ended, self.resolved, self.window_end)
        assert span == (self.detected, self.resolved)

    def test_an_unresolved_incident_ends_where_its_abnormal_telemetry_ended(self) -> None:
        """The 4892f8e guard: an OPEN or RECOVERING incident, or a CANDIDATE
        that returned to normal and so is never resolved, must not exclude to
        the end of the lookback."""
        span = anomaly_exclusion_span(self.detected, self.ended, None, self.window_end)
        assert span == (self.detected, self.ended)

    def test_only_an_incident_still_in_progress_excludes_to_the_window_end(self) -> None:
        span = anomaly_exclusion_span(self.detected, None, None, self.window_end)
        assert span == (self.detected, self.window_end)


def test_in_any_span_is_inclusive_at_both_ends() -> None:
    spans = [(T0, T0 + timedelta(minutes=5))]
    assert in_any_span(T0, spans)
    assert in_any_span(T0 + timedelta(minutes=5), spans)
    assert not in_any_span(T0 + timedelta(minutes=6), spans)
    assert not in_any_span(T0, [])


class TestAdvanceCounters:
    def test_anomalous_increments_and_resets_healthy(self) -> None:
        assert advance_counters(DetectorSignal.ANOMALOUS, 2, 5) == (3, 0)

    def test_healthy_increments_and_resets_anomalous(self) -> None:
        assert advance_counters(DetectorSignal.HEALTHY, 2, 5) == (0, 6)

    def test_unknown_advances_nothing(self) -> None:
        assert advance_counters(DetectorSignal.UNKNOWN, 2, 5) == (2, 5)


class TestEvidenceDetectorType:
    def test_evidence_records_the_detector_type_it_was_given(self) -> None:
        ev = healthy(
            WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
            interval_start=T0,
            interval_end=T0 + timedelta(minutes=5),
        )
        assert ev.anomaly_type is WafAnomalyType.WAF_REQUEST_RATE_SPIKE
        assert ev.to_dict()["anomaly_type"] == "WAF_REQUEST_RATE_SPIKE"

    def test_phase_a_evidence_is_unchanged(self) -> None:
        ev = insufficient(
            AnomalyType.VOLUME_SPIKE,
            interval_start=T0,
            interval_end=T0 + timedelta(minutes=5),
            reason="no baseline",
            sample_count=3,
        )
        d = ev.to_dict()
        assert d["anomaly_type"] == "VOLUME_SPIKE"
        assert d["signal"] == "UNKNOWN"
        assert d["insufficient_data"] is True
        assert d["sample_count"] == 3
