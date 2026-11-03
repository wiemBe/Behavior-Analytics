"""The notification roll-up over a range (HLT-3): summed groups, the cut, the honest empty."""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.qradar_health_detail import (
    NOTIFICATION_ROLLUP_LIMIT,
    rollup_notifications,
)

START = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
END = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
COVERAGE = (96, START, END)
LAST = datetime(2026, 11, 3, 11, 59, tzinfo=UTC)


def group(host: str, qid: str, events: int, severity: int | None = 4, seen=LAST):  # type: ignore[no-untyped-def]
    return ("GROUP", host, qid, events, 1, severity, seen)


def remainder(events: int, groups: int):  # type: ignore[no-untyped-def]
    return ("REMAINDER", "", "", events, groups, None, None)


def test_a_range_with_no_measured_window_is_no_list_never_an_empty_one() -> None:
    assert rollup_notifications([], None, 24) is None
    assert rollup_notifications([group("a", "Warning Message", 3)], (0, None, None), 24) is None  # type: ignore[arg-type]


def test_a_measured_range_with_no_group_is_an_empty_list_that_says_how_much_it_covers() -> None:
    value = rollup_notifications([], COVERAGE, 24)
    assert value is not None
    assert value.entries == [] and value.remainder is None
    assert (value.hours, value.windows) == (24, 96)
    assert (value.covered_from, value.covered_until) == (START, END)


def test_groups_are_listed_by_events_then_host_then_name_with_their_own_severity_and_time() -> None:
    value = rollup_notifications(
        [
            group("b", "Warning Message", 5, 3),
            group("a", "Error Message", 40, 8),
            group("a", "Warning Message", 5, None, None),
        ],
        COVERAGE,
        24,
    )
    assert value is not None
    assert [(e.host, e.qid_name, e.events) for e in value.entries] == [
        ("a", "Error Message", 40),
        ("a", "Warning Message", 5),
        ("b", "Warning Message", 5),
    ]
    assert value.entries[0].max_severity == 8 and value.entries[0].last_seen == LAST
    assert value.entries[1].max_severity is None and value.entries[1].last_seen is None


def test_stored_remainders_are_summed_and_the_events_still_add_up() -> None:
    rows = [group("a", "x", 10), remainder(7, 2)]
    value = rollup_notifications(rows, COVERAGE, 24)
    assert value is not None and value.remainder is not None
    assert (value.remainder.events, value.remainder.groups) == (7, 2)
    total = sum(e.events for e in value.entries) + value.remainder.events
    assert total == 17


def test_groups_beyond_the_limit_join_the_remainder() -> None:
    rows = [group("h", f"q{n:03d}", 1000 - n) for n in range(NOTIFICATION_ROLLUP_LIMIT + 3)]
    rows.append(remainder(5, 1))
    value = rollup_notifications(rows, COVERAGE, 168)
    assert value is not None and value.remainder is not None
    assert len(value.entries) == NOTIFICATION_ROLLUP_LIMIT
    assert value.entries[0].events == 1000
    dropped = [1000 - n for n in range(NOTIFICATION_ROLLUP_LIMIT, NOTIFICATION_ROLLUP_LIMIT + 3)]
    assert value.remainder.events == sum(dropped) + 5
    assert value.remainder.groups == 3 + 1
    assert value.hours == 168
