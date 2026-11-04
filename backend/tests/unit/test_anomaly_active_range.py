"""The anomaly list's range resolver: an active-only read has no lower bound.

"Active" is a state, not a time window. Without a caller-supplied `since` the
list must reach every active incident up to its end, whatever its age, while an
explicit range keeps the ceiling that keeps an unbounded hypertable scan off the
database. The pure resolver is pinned here; the routes' behaviour is pinned by
the integration tests in `tests/integration/test_phase_a_api.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.api.routes.anomalies import _active_only_range, _validate_range

NOW = datetime.now(UTC)


class TestActiveOnlyRange:
    def test_without_since_there_is_no_lower_bound(self) -> None:
        start, end = _active_only_range(None, None, 365)
        assert start is None
        assert end.tzinfo is not None
        assert abs((end - datetime.now(UTC)).total_seconds()) < 5

    def test_without_since_until_still_ends_the_population(self) -> None:
        until = NOW - timedelta(days=400)
        start, end = _active_only_range(None, until, 365)
        assert start is None
        assert end == until

    def test_a_naive_until_is_read_as_utc(self) -> None:
        until = datetime(2026, 1, 1, 12, 0)
        _, end = _active_only_range(None, until, 365)
        assert end == until.replace(tzinfo=UTC)

    def test_without_since_the_ceiling_does_not_apply(self) -> None:
        """A 400-day-wide unbounded read is the point: the active set is not a window."""
        start, end = _active_only_range(None, NOW - timedelta(days=400), 365)
        assert start is None
        assert end == NOW - timedelta(days=400)

    def test_an_explicit_since_keeps_the_ceiling(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            _active_only_range(NOW - timedelta(days=400), NOW, 365)
        assert excinfo.value.status_code == 422

    def test_an_explicit_since_is_still_honored_inside_the_ceiling(self) -> None:
        since = NOW - timedelta(days=10)
        start, end = _active_only_range(since, NOW, 365)
        assert (start, end) == (since, NOW)

    def test_an_inverted_explicit_range_is_refused(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            _active_only_range(NOW, NOW - timedelta(hours=1), 365)
        assert excinfo.value.status_code == 422


class TestValidateRange:
    def test_without_since_the_default_is_one_day(self) -> None:
        start, end = _validate_range(None, NOW, 365)
        assert end == NOW
        assert end - start == timedelta(days=1)

    def test_an_until_in_the_past_anchors_the_default_day(self) -> None:
        until = NOW - timedelta(days=3)
        start, end = _validate_range(None, until, 365)
        assert end == until
        assert end - start == timedelta(days=1)

    def test_the_ceiling_is_enforced(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            _validate_range(NOW - timedelta(days=400), NOW, 365)
        assert excinfo.value.status_code == 422
