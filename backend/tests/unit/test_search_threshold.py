"""Threshold reachability rule: the shared pure helper and create validation.

T-047: a threshold that can never be breached (GT at/above max_result_rows + 1,
GE above max_result_rows + 1) is refused at create time, so a search never
silently fails to alert. The executor's exact +1 lower bound is pinned in the
integration suite; these tests need no database.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.search import ScheduledSearchCreate, unreachable_threshold_reason


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "thr",
        "aql_query": "SELECT sourceip FROM events LAST 1 HOURS",
        "schedule_cron": "*/5 * * * *",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- pure helper
def test_gt_at_max_is_reachable() -> None:
    # A whole run can count exactly max_result_rows, which breaches GT at max.
    assert unreachable_threshold_reason("GT", 100, 100) is None


def test_gt_between_max_and_cap_is_reachable() -> None:
    assert unreachable_threshold_reason("GT", 100.5, 100) is None


def test_gt_at_cap_plus_one_is_unreachable() -> None:
    # A run counts at most 100 rows; a truncated run proves only >= 101. A GT
    # threshold of 101 can therefore never be observed as breached.
    assert unreachable_threshold_reason("GT", 101, 100) is not None


def test_ge_at_cap_plus_one_is_reachable() -> None:
    # A truncated run proves >= 101, which is exactly GE 101.
    assert unreachable_threshold_reason("GE", 101, 100) is None


def test_ge_above_cap_plus_one_is_unreachable() -> None:
    assert unreachable_threshold_reason("GE", 101.5, 100) is not None


def test_none_threshold_is_always_reachable() -> None:
    assert unreachable_threshold_reason("GT", None, 100) is None
    assert unreachable_threshold_reason("GE", None, 1) is None
    assert unreachable_threshold_reason("LT", None, 100) is None


def test_lt_le_eq_are_never_refused() -> None:
    assert unreachable_threshold_reason("LT", 1_000_000, 100) is None
    assert unreachable_threshold_reason("LE", 1_000_000, 100) is None
    assert unreachable_threshold_reason("EQ", 1_000_000, 100) is None


def test_unreachable_message_says_what_to_change() -> None:
    reason = unreachable_threshold_reason("GT", 20000, 10000)
    assert reason == (
        "threshold GT 20000 can never be reached: a run counts at most "
        "max_result_rows (10000) rows; raise max_result_rows above the "
        "threshold or lower the threshold"
    )


# --------------------------------------------------------------- create schema
@pytest.mark.parametrize(
    ("overrides", "valid"),
    [
        ({"threshold_value": 100, "max_result_rows": 100}, True),  # GT at max
        ({"threshold_value": 100.5, "max_result_rows": 100}, True),  # GT below cap+1
        ({"threshold_value": 101, "max_result_rows": 100}, False),  # GT at cap+1
        ({"threshold_value": 101, "threshold_operator": "GE", "max_result_rows": 100}, True),
        ({"threshold_value": 101.5, "threshold_operator": "GE", "max_result_rows": 100}, False),
        ({"threshold_value": None, "max_result_rows": 1}, True),  # none, any max
        ({"threshold_value": 10_001}, False),  # default max_result_rows 10_000
        ({"threshold_value": 1_000_000, "threshold_operator": "LT"}, True),
        ({"threshold_value": 1_000_000, "threshold_operator": "EQ"}, True),
    ],
)
def test_create_refuses_never_breachable_thresholds(overrides: dict, valid: bool) -> None:
    payload = _payload(**overrides)
    if valid:
        ScheduledSearchCreate(**payload)
        return
    with pytest.raises(ValidationError) as excinfo:
        ScheduledSearchCreate(**payload)
    assert "can never be reached" in str(excinfo.value)