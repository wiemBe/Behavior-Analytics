"""Schema bounds for a log source's business-hours fields.

The route judges a PATCH against the stored row. These tests judge one payload.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.log_source import LogSourceUpdate, business_days_usable


def _locs(exc: ValidationError) -> set[str]:
    found: set[str] = set()
    for error in exc.errors():
        found.update(part for part in error["loc"] if isinstance(part, str))
    return found


def test_timezone_name_must_be_a_real_zone() -> None:
    with pytest.raises(ValidationError) as exc:
        LogSourceUpdate(timezone_name="Not/AZone")
    assert "timezone_name" in _locs(exc.value)
    assert "IANA" in str(exc.value)
    accepted = LogSourceUpdate(timezone_name="Europe/Istanbul")
    assert accepted.timezone_name == "Europe/Istanbul"


def test_business_days_items_are_iso_weekdays() -> None:
    for bad in ([0], [8], [1, 8]):
        with pytest.raises(ValidationError) as exc:
            LogSourceUpdate(business_days=bad)
        assert "business_days" in _locs(exc.value)


def test_empty_days_are_rejected_only_when_business_hours_are_on() -> None:
    with pytest.raises(ValidationError) as exc:
        LogSourceUpdate(business_hours_only=True, business_days=[])
    assert "business_days" in _locs(exc.value)
    off = LogSourceUpdate(business_hours_only=False, business_days=[])
    assert off.business_days == []
    # The schema cannot see the stored row. Omitting the flag leaves [] alone.
    omitted = LogSourceUpdate(business_days=[])
    assert omitted.business_days == []


def test_wrap_and_equal_bounds_are_accepted() -> None:
    wrap = LogSourceUpdate(business_hours_start=22, business_hours_end=6)
    assert wrap.business_hours_start == 22
    assert wrap.business_hours_end == 6
    same = LogSourceUpdate(business_hours_start=8, business_hours_end=8)
    assert same.business_hours_start == same.business_hours_end == 8


def test_hours_outside_0_to_23_are_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        LogSourceUpdate(business_hours_start=24)
    assert "business_hours_start" in _locs(exc.value)
    with pytest.raises(ValidationError) as exc:
        LogSourceUpdate(business_hours_end=-1)
    assert "business_hours_end" in _locs(exc.value)


def test_business_days_usable_rejects_empty_and_out_of_range() -> None:
    assert business_days_usable([1, 2, 3, 4, 5]) is True
    assert business_days_usable([]) is False
    assert business_days_usable([0]) is False
    assert business_days_usable([1, 0]) is False
    assert business_days_usable([True]) is False
    assert business_days_usable(None) is False
