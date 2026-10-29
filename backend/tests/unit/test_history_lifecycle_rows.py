"""The lifecycle's scope-row builders: `scope_view`, `scope_row`, `step_row` (T-168).

The five family adapters stopped hand-building the 16-column mapping: a step that
leaves the state alone starts from `scope_row` (the row as it stands) and names what
it changes through `step_row`. These pins are the ones the family-local builders
carried before T-168 (`rule_match.step_scope_row`, `catchall.scope_row`), moved to
the one module that now owns the rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.history.lifecycle import (
    MAX_REASON_LENGTH,
    SCOPE_COLUMNS,
    LifecycleError,
    ScopeRowView,
    scope_row,
    scope_view,
    step_row,
)
from app.history.state import HistoryFamily, HistoryState

F = HistoryFamily
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
INSTANCE = uuid.uuid4()
PROGRESS = {"version": 3, "progress": {}, "adapter": {}}


def stored_row(**overrides: object) -> SimpleNamespace:
    """A row-like object holding every scope column, as a stored row reads."""
    values: dict[str, object] = {
        "instance_id": INSTANCE,
        "family": F.PHASE_A.value,
        "scope_key": "",
        "state": HistoryState.READING.value,
        "days_requested": 28,
        "days_target": 28,
        "target_start": NOW,
        "floor": NOW - HOUR,
        "through": NOW - HOUR,
        "anchor": NOW,
        "started_at": NOW - HOUR,
        "finished_at": None,
        "reason": "the scope reads its next step",
        "last_error_class": "ProviderError",
        "last_error_at": NOW,
        "progress": dict(PROGRESS),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def base_of(row: SimpleNamespace | None = None, *, family: F = F.PHASE_A) -> dict[str, object]:
    row = row or stored_row()
    key = "17" if family in (F.WAF, F.IPS) else ""
    return scope_row(
        scope_view(row),  # type: ignore[arg-type]
        instance_id=row.instance_id,
        family=family,
        scope_key=key,
    )


class TestScopeView:
    def test_none_reads_as_none(self) -> None:
        assert scope_view(None) is None

    def test_a_raw_state_string_is_read_as_a_state(self) -> None:
        view = scope_view(stored_row(state="PARTIAL"))

        assert view is not None
        assert view.state is HistoryState.PARTIAL

    def test_a_row_like_object_round_trips_to_a_full_row(self) -> None:
        row = stored_row()

        assert base_of(row) == {
            "instance_id": INSTANCE,
            "family": "PHASE_A",
            "scope_key": "",
            "state": "READING",
            "days_requested": 28,
            "days_target": 28,
            "target_start": NOW,
            "floor": NOW - HOUR,
            "through": NOW - HOUR,
            "anchor": NOW,
            "started_at": NOW - HOUR,
            "finished_at": None,
            "reason": "the scope reads its next step",
            "reason_i18n": None,
            "last_error_class": None,
            "last_error_at": None,
            "progress": PROGRESS,
        }


class TestScopeRow:
    def test_the_row_holds_exactly_the_scope_columns(self) -> None:
        assert set(base_of()) == set(SCOPE_COLUMNS)

    def test_a_view_reads_the_same_columns(self) -> None:
        view = scope_view(stored_row(days_target=7, through=NOW, reason="reading"))

        assert view is not None
        assert view.days_target == 7
        assert view.through == NOW
        assert view.reason == "reading"

    def test_progress_is_copied_never_aliased(self) -> None:
        row = stored_row()

        base = base_of(row)

        assert base["progress"] is not row.progress
        assert base["progress"] == row.progress

    def test_the_error_marker_is_cleared(self) -> None:
        base = base_of()

        assert base["last_error_class"] is None
        assert base["last_error_at"] is None

    def test_the_key_must_match_the_family(self) -> None:
        with pytest.raises(LifecycleError, match="source key"):
            scope_row(
                scope_view(stored_row()),
                instance_id=INSTANCE,
                family=F.PHASE_A,
                scope_key="17",
            )


class TestStepRow:
    def test_only_the_named_columns_change(self) -> None:
        base = base_of()

        row = step_row(base, through=NOW + HOUR, reason="read 12 bucket(s)")

        assert set(row) == set(SCOPE_COLUMNS)
        assert row["through"] == NOW + HOUR
        assert row["reason"] == "read 12 bucket(s)"
        changed = {"through", "reason", "last_error_class", "last_error_at"}
        assert {name: row[name] for name in SCOPE_COLUMNS if name not in changed} == {
            name: base[name] for name in SCOPE_COLUMNS if name not in changed
        }
        assert row["last_error_class"] is None and row["last_error_at"] is None

    def test_state_accepts_a_history_state(self) -> None:
        row = step_row(base_of(), state=HistoryState.COMPLETE)

        assert row["state"] == "COMPLETE"

    def test_a_long_reason_is_bounded(self) -> None:
        row = step_row(base_of(), reason="x" * 400)

        assert len(row["reason"]) == MAX_REASON_LENGTH
        assert row["reason"].endswith("…")

    def test_progress_is_copied_never_aliased(self) -> None:
        row = step_row(base_of(), progress=PROGRESS)

        assert row["progress"] is not PROGRESS
        assert row["progress"] == PROGRESS

    def test_an_unknown_column_is_refused(self) -> None:
        with pytest.raises(LifecycleError, match="unknown scope columns"):
            step_row(base_of(), not_a_scope_column=1)

    def test_an_incomplete_base_is_refused(self) -> None:
        base = base_of()
        del base["floor"]

        with pytest.raises(LifecycleError, match=r"missing \['floor'\]"):
            step_row(base, reason="read 1 bucket(s)")

    def test_the_marker_is_cleared_even_when_the_base_carries_one(self) -> None:
        base = base_of()
        base["last_error_class"] = "RuntimeError"

        row = step_row(base, reason="read 1 bucket(s)")

        assert row["last_error_class"] is None
        assert row["last_error_at"] is None


def test_the_view_type_is_what_the_builders_read() -> None:
    """`scope_row` takes the `ScopeRowView` the lifecycle's other builders take."""
    view = scope_view(stored_row())

    assert isinstance(view, ScopeRowView)
    assert set(base_of()) == set(SCOPE_COLUMNS)
