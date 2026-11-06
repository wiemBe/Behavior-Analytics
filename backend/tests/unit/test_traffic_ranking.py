"""TOP-1a traffic ranking: the pure window, order and verdict rules.

The HTTP routes are exercised against Postgres in
`tests/integration/test_traffic_ranking.py`; here every rule that decides a
number or a state is pinned without a database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.api.routes.anomalies import (
    _csv_field,
    _csv_filename,
    _csv_time,
    _export_header,
    _series_shape,
    _series_values,
    _traffic_bounds,
    _traffic_order,
    _traffic_reason,
    _traffic_verdict,
    _TrafficSpan,
    _waf_export_body,
)
from app.repositories.anomaly import WafTrafficRow
from app.schemas.anomaly import TrafficSortOrder, TrafficState, TrafficWindow

#: The newest complete bucket in the examples.
T0 = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("window", "expected", "start_offset"),
    [
        (TrafficWindow.FIVE_MINUTES, 1, 0),
        (TrafficWindow.ONE_HOUR, 12, 11 * 300),
        (TrafficWindow.ONE_DAY, 288, 287 * 300),
        (TrafficWindow.ONE_WEEK, 2016, 2015 * 300),
        (TrafficWindow.FOUR_WEEKS, 8064, 8063 * 300),
    ],
)
def test_each_window_is_whole_buckets_ending_at_the_newest_bucket(
    window: TrafficWindow, expected: int, start_offset: int
) -> None:
    start, end, buckets = _traffic_bounds(window, T0, 300)
    assert buckets == expected
    assert end == T0 + timedelta(seconds=300)
    assert start == end - timedelta(seconds=expected * 300)
    assert start == T0 - timedelta(seconds=start_offset)


def test_five_minutes_is_the_newest_bucket_whatever_its_width() -> None:
    # A source collecting hourly buckets: "5m" is still exactly one bucket,
    # not one twelfth of an hour.
    start, end, buckets = _traffic_bounds(TrafficWindow.FIVE_MINUTES, T0, 3600)
    assert buckets == 1
    assert (start, end) == (T0, T0 + timedelta(hours=1))


def test_a_window_that_does_not_divide_evenly_still_holds_whole_buckets() -> None:
    start, end, buckets = _traffic_bounds(TrafficWindow.ONE_HOUR, T0, 450)
    assert buckets == 8
    assert (end - start).total_seconds() == 8 * 450


@pytest.mark.parametrize(
    ("sort", "default"),
    [
        ("rate", TrafficSortOrder.DESC),
        ("peak", TrafficSortOrder.DESC),
        ("host", TrafficSortOrder.ASC),
        ("endpoint", TrafficSortOrder.ASC),
        ("ip", TrafficSortOrder.ASC),
    ],
)
def test_order_defaults_descending_for_counts_and_ascending_for_text(
    sort: str, default: TrafficSortOrder
) -> None:
    assert _traffic_order(sort, None) is default


@pytest.mark.parametrize("order", [TrafficSortOrder.ASC, TrafficSortOrder.DESC])
def test_an_explicit_order_overrides_the_default(order: TrafficSortOrder) -> None:
    assert _traffic_order("rate", order) is order
    assert _traffic_order("host", order) is order


def test_reason_codes_use_the_detail_reason_namespace() -> None:
    assert _traffic_reason("waf", "truncated") == "waf_traffic_detail.reason.truncated"
    assert _traffic_reason("ips", "truncated") == "ips_traffic_detail.reason.truncated"


def test_not_collected_wins_over_everything() -> None:
    assert _traffic_verdict(
        "waf",
        not_collected=True,
        has_window=False,
        buckets_measured=0,
        buckets_expected=12,
        partial_buckets=0,
        truncated=True,
    ) == (TrafficState.NOT_COLLECTED, "waf_traffic_detail.reason.not_collected")


def test_no_complete_bucket_is_insufficient_data() -> None:
    assert _traffic_verdict(
        "waf",
        not_collected=False,
        has_window=False,
        buckets_measured=0,
        buckets_expected=0,
        partial_buckets=0,
        truncated=False,
    ) == (TrafficState.INSUFFICIENT_DATA, "waf_traffic_detail.reason.no_complete_bucket")


def test_a_window_with_no_usable_bucket_is_insufficient_data() -> None:
    assert _traffic_verdict(
        "ips",
        not_collected=False,
        has_window=True,
        buckets_measured=0,
        buckets_expected=12,
        partial_buckets=0,
        truncated=False,
    ) == (TrafficState.INSUFFICIENT_DATA, "ips_traffic_detail.reason.no_usable_bucket")


def test_truncation_outranks_a_missing_or_partial_bucket() -> None:
    assert _traffic_verdict(
        "waf",
        not_collected=False,
        has_window=True,
        buckets_measured=5,
        buckets_expected=12,
        partial_buckets=5,
        truncated=True,
    ) == (TrafficState.TRUNCATED, "waf_traffic_detail.reason.truncated")


def test_fewer_measured_than_expected_is_partial() -> None:
    assert _traffic_verdict(
        "waf",
        not_collected=False,
        has_window=True,
        buckets_measured=11,
        buckets_expected=12,
        partial_buckets=0,
        truncated=False,
    ) == (TrafficState.PARTIAL, "waf_traffic_detail.reason.buckets_missing")


def test_a_partial_measured_bucket_is_partial_even_when_all_are_present() -> None:
    assert _traffic_verdict(
        "ips",
        not_collected=False,
        has_window=True,
        buckets_measured=12,
        buckets_expected=12,
        partial_buckets=1,
        truncated=False,
    ) == (TrafficState.PARTIAL, "ips_traffic_detail.reason.bucket_partial")


def test_a_whole_window_with_whole_buckets_is_ok_with_no_reason() -> None:
    assert _traffic_verdict(
        "ips",
        not_collected=False,
        has_window=True,
        buckets_measured=12,
        buckets_expected=12,
        partial_buckets=0,
        truncated=False,
    ) == (TrafficState.OK, None)


# ------------------------------------------------- series grid (Amendment 1)
@pytest.mark.parametrize(
    ("window", "points", "step"),
    [
        (TrafficWindow.FIVE_MINUTES, 1, 300),
        (TrafficWindow.ONE_HOUR, 12, 300),
        (TrafficWindow.ONE_DAY, 48, 1800),
        (TrafficWindow.ONE_WEEK, 56, 10800),
        (TrafficWindow.FOUR_WEEKS, 56, 43200),
    ],
)
def test_the_series_grid_is_the_contract_table_for_300s_buckets(
    window: TrafficWindow, points: int, step: int
) -> None:
    _start, _end, buckets = _traffic_bounds(window, T0, 300)
    assert _series_shape(window, 300, buckets) == (points, step)
    # The grid always covers the window exactly.
    assert points * step == buckets * 300


def test_a_wider_source_bucket_gets_a_whole_bucket_grid() -> None:
    # An hourly source in a 1 h window has one bucket, so one point of 3600 s:
    # a narrower step would repeat the same measurement twelve times.
    assert _series_shape(TrafficWindow.ONE_HOUR, 3600, 1) == (1, 3600)
    assert _series_shape(TrafficWindow.FIVE_MINUTES, 3600, 1) == (1, 3600)


def test_a_window_without_an_anchor_has_no_points() -> None:
    assert _series_shape(TrafficWindow.ONE_HOUR, 300, 0) == (0, 300)


def test_series_values_are_none_for_an_unmeasured_step_and_zero_for_a_measured_one() -> None:
    entity = uuid.uuid4()
    measured = {0: 2, 2: 1}
    events = {(0, entity): 600, (2, entity): 0}
    assert _series_values(3, measured, events, entity, 300) == [1.0, None, 0.0]


def test_series_values_divide_by_the_measured_buckets_of_the_step_not_the_grid() -> None:
    """A half-measured step still reads as a rate over what was measured."""
    entity = uuid.uuid4()
    measured = {0: 1}
    events = {(0, entity): 900}
    assert _series_values(1, measured, events, entity, 300) == [3.0]


# ------------------------------------------------- CSV export (Amendment 2)
def test_a_csv_field_is_empty_for_none_and_plain_for_numbers() -> None:
    assert _csv_field(None) == ""
    assert _csv_field(5) == "5"
    assert _csv_field(0.516666) == "0.517"
    assert _csv_field(True) == "true"
    assert _csv_field(False) == "false"


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_a_csv_field_guards_every_formula_character(prefix: str) -> None:
    guarded = _csv_field(f"{prefix}danger")
    assert guarded.lstrip('"').startswith("'")
    # A CR is a line break, so the guarded field is also quoted; the parsed
    # value is still the guard plus the original text.
    assert guarded.strip('"') == f"'{prefix}danger"


def test_a_csv_field_quotes_rfc4180_and_doubles_the_quotes() -> None:
    assert _csv_field('a"b,c') == '"a""b,c"'
    assert _csv_field("plain/path") == "plain/path"
    assert _csv_field("a,b") == '"a,b"'


def test_a_csv_time_is_iso_utc_with_z_and_none_stays_empty() -> None:
    assert _csv_time(T0) == "2026-11-05T18:00:00Z"
    assert _csv_time(None) is None


def test_the_csv_filename_slugs_the_source_and_stamps_the_range() -> None:
    assert _csv_filename(
        "waf", "WAF North / Prod", TrafficWindow.ONE_HOUR, T0 + timedelta(minutes=5)
    ) == "traffic-waf-waf-north-prod-1h-20261105T1805Z.csv"
    assert _csv_filename("ips", "", TrafficWindow.ONE_HOUR, None) == (
        "traffic-ips-source-1h-no-range.csv"
    )


def test_the_csv_header_is_the_contract_column_order() -> None:
    assert _export_header("waf").rstrip("\r\n").split(",") == [
        "rank", "host", "method", "path", "avg_events_per_s", "peak_events_per_s",
        "peak_bucket_start_utc", "events", "buckets_with_data", "partial_buckets",
        "active", "window", "range_start_utc", "range_end_utc", "buckets_measured",
        "buckets_expected", "state", "source", "truncated",
    ]
    assert _export_header("ips").rstrip("\r\n").split(",")[:3] == [
        "rank", "destination_ip", "display_name",
    ]


def test_a_waf_export_row_carries_the_context_and_a_null_host_is_empty() -> None:
    hostless = WafTrafficRow(
        id=uuid.uuid4(),
        events=20,
        buckets_with_data=12,
        partial_buckets=1,
        peak_rate_per_second=0.1,
        peak_bucket_start=T0,
        virtual_host=None,
        http_method="GET",
        normalized_endpoint="/health",
        active=False,
    )
    span = _TrafficSpan(
        start=T0 - timedelta(minutes=55),
        end=T0 + timedelta(minutes=5),
        bucket_seconds=300,
        buckets_expected=12,
        buckets_measured=12,
        source_total=5000,
        unattributed=50,
    )
    body = _waf_export_body(
        [hostless],
        window=TrafficWindow.ONE_HOUR,
        span=span,
        state=TrafficState.PARTIAL,
        source_name="waf-1",
        measured_seconds=3600,
        truncated=False,
    )
    header, row, tail = body.split("\r\n")
    assert header.startswith("rank,host,method,path,")
    assert row == (
        "1,,GET,/health,0.006,0.100,2026-11-05T18:00:00Z,20,12,1,false,1h,"
        "2026-11-05T17:05:00Z,2026-11-05T18:05:00Z,12,12,PARTIAL,waf-1,false"
    )
    assert tail == ""
