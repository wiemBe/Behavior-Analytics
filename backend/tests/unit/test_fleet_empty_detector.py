"""FIX-45c (D-62): the pure fleet-empty branch of `detect_no_events` and its row reader."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.anomaly.detectors import detect_no_events
from app.anomaly.engine import fleet_empty_bucket
from app.models.enums import BucketCompleteness, DetectorSignal
from tests.unit.test_volume_detectors import _cell, _ctx, _point


def _fleet_ctx(*, count_cell, expected_active=True, preceding_empty=5):
    point = replace(
        _point(eps=0.0, completeness=BucketCompleteness.PARTIAL), fleet_empty=True
    )
    return _ctx(
        point, count_cell=count_cell, expected_active=expected_active,
        preceding_empty=preceding_empty,
    )


def test_an_expecting_source_is_insufficient_never_anomalous_whatever_the_streak() -> None:
    ev = detect_no_events(_fleet_ctx(count_cell=_cell(median=15000.0), preceding_empty=50))
    assert ev.signal is DetectorSignal.UNKNOWN
    assert ev.extra == {"fleet_empty": True}
    assert ev.reason_message is not None
    assert ev.reason_message.code == "alert.fleet_empty.detector_reason"


def test_off_schedule_is_healthy_and_does_not_expect_traffic() -> None:
    ev = detect_no_events(_fleet_ctx(count_cell=_cell(median=15000.0), expected_active=False))
    assert ev.signal is DetectorSignal.HEALTHY
    assert "fleet_empty" not in ev.extra


def test_a_zero_median_cell_is_healthy_and_does_not_expect_traffic() -> None:
    ev = detect_no_events(_fleet_ctx(count_cell=_cell(median=0.0)))
    assert ev.signal is DetectorSignal.HEALTHY
    assert "fleet_empty" not in ev.extra


@pytest.mark.parametrize("cell", [None, _cell(median=15000.0, reliable=False, samples=3)])
def test_no_reliable_baseline_is_insufficient_without_the_fleet_mark(cell) -> None:
    ev = detect_no_events(_fleet_ctx(count_cell=cell))
    assert ev.signal is DetectorSignal.UNKNOWN
    assert "fleet_empty" not in ev.extra


def test_the_flag_never_hides_events() -> None:
    point = replace(_point(eps=10.0), fleet_empty=True)
    ev = detect_no_events(_ctx(point, count_cell=_cell(median=15000.0)))
    assert ev.signal is DetectorSignal.HEALTHY


def test_without_the_flag_a_partial_bucket_is_still_the_generic_insufficient() -> None:
    point = _point(eps=0.0, completeness=BucketCompleteness.PARTIAL)
    ev = detect_no_events(_ctx(point, count_cell=_cell(median=15000.0)))
    assert ev.signal is DetectorSignal.UNKNOWN
    assert ev.extra == {}


@pytest.mark.parametrize(
    ("completeness", "provenance", "expected"),
    [
        (BucketCompleteness.PARTIAL, {"fleet_empty": True}, True),
        (BucketCompleteness.COMPLETE, {"fleet_empty": True}, False),
        (BucketCompleteness.PARTIAL, {"zero_filled": True}, False),
        (BucketCompleteness.PARTIAL, {"fleet_empty": "yes"}, False),
        (BucketCompleteness.PARTIAL, None, False),
    ],
)
def test_the_row_reader_needs_a_partial_row_with_the_provenance_flag(
    completeness, provenance, expected
) -> None:
    row = SimpleNamespace(completeness=completeness, query_provenance=provenance)
    assert fleet_empty_bucket(row) is expected  # type: ignore[arg-type]


def test_a_fleet_empty_bucket_demotes_the_detectors_that_read_the_zero_row() -> None:
    """Parsing reads the zero row's ratios, not its completeness: only the flag stops N alerts."""
    from app.anomaly.engine import run_detectors
    from app.models.enums import AnomalyType
    from app.providers.base import LogSourceTelemetry

    ctx = _fleet_ctx(count_cell=_cell(median=15000.0))
    ctx.point = replace(ctx.point, parsed_username_ratio=0.0, parsed_source_ip_ratio=0.0)
    # Control: without the flag the same row makes the parsing detector fire.
    bare = replace(ctx, point=replace(ctx.point, fleet_empty=False))
    control, _ = run_detectors(bare, frozenset(LogSourceTelemetry))
    assert any(e.anomaly_type is AnomalyType.PARSING_DEGRADATION and e.is_anomalous
               for e in control)

    results, _ = run_detectors(ctx, frozenset(LogSourceTelemetry))

    assert not any(e.is_anomalous for e in results)
    parsing = next(e for e in results if e.anomaly_type is AnomalyType.PARSING_DEGRADATION)
    assert parsing.signal is DetectorSignal.UNKNOWN
