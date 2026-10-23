"""How a stored row says it came from history (HISTORY-BACKFILL §B.4, §B.11).

Two spellings, one owner. Tables a family may change carry an `origin` column
(`'LIVE'`/`'HISTORY'`). Phase A's `log_source_metric` is not changed (repository rule 6):
it marks history in `collection_source`. A reader never writes either by hand, so a guard
test can find every use (§B.4.7).

Pure: the clause builders take the mapped class and read its columns; no model import.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, or_

from app.history.state import HistoryFamily


class HistoryOrigin(StrEnum):
    LIVE = "LIVE"
    HISTORY = "HISTORY"


ORIGIN_COLUMN = "origin"
COLLECTION_SOURCE_COLUMN = "collection_source"

# Phase A: the real provider and the mock one (§B.4.1).
PHASE_A_HISTORY_SOURCE = "ariel_history"
PHASE_A_MOCK_HISTORY_SOURCE = "mock_history"
PHASE_A_HISTORY_SOURCES: tuple[str, ...] = (PHASE_A_HISTORY_SOURCE, PHASE_A_MOCK_HISTORY_SOURCE)


def marker_column(family: HistoryFamily) -> str:
    """Test-only: the column that carries the marker. Writers use ``marker_values``."""
    return COLLECTION_SOURCE_COLUMN if family is HistoryFamily.PHASE_A else ORIGIN_COLUMN


def marker_values(
    family: HistoryFamily, origin: HistoryOrigin, *, mock: bool = False
) -> dict[str, str]:
    """The column values that mark a row written with `origin`.

    A live Phase A row sets nothing here: the live writer keeps its own
    `collection_source` (`ariel_aggregate`, `mock`), which this feature does not touch.
    """
    if family is HistoryFamily.PHASE_A:
        if origin is HistoryOrigin.HISTORY:
            source = PHASE_A_MOCK_HISTORY_SOURCE if mock else PHASE_A_HISTORY_SOURCE
            return {COLLECTION_SOURCE_COLUMN: source}
        return {}
    return {ORIGIN_COLUMN: origin.value}


def origin_of(family: HistoryFamily, row_value: str | None) -> HistoryOrigin:
    """Read a stored marker back: Phase A's `collection_source`, the others' `origin`."""
    if family is HistoryFamily.PHASE_A:
        if row_value in PHASE_A_HISTORY_SOURCES:
            return HistoryOrigin.HISTORY
        return HistoryOrigin.LIVE
    if row_value is None:
        raise ValueError("an origin column is NOT NULL; a missing value is not LIVE")
    try:
        return HistoryOrigin(row_value)
    except ValueError as exc:
        raise ValueError(f"unknown origin {row_value!r}") from exc


def _spelling(model: Any) -> tuple[Any, bool]:
    origin = getattr(model, ORIGIN_COLUMN, None)
    if origin is not None:
        return origin, True
    source = getattr(model, COLLECTION_SOURCE_COLUMN, None)
    if source is not None:
        return source, False
    raise ValueError(
        f"{getattr(model, '__name__', model)!r} has neither an origin nor a "
        "collection_source column, so it cannot hold history"
    )


def is_history_clause(model: Any) -> ColumnElement[bool]:
    """`WHERE` clause that selects the history rows of a table."""
    column, has_origin = _spelling(model)
    if has_origin:
        return column == HistoryOrigin.HISTORY.value
    return column.in_(PHASE_A_HISTORY_SOURCES)


def is_live_clause(model: Any) -> ColumnElement[bool]:
    """`WHERE` clause for a LIVE-only reader (pending reads, streaks, F.1 loaders).

    Phase A's `collection_source` is nullable, so a NULL is live: `NOT IN` alone would
    drop it.
    """
    column, has_origin = _spelling(model)
    if has_origin:
        return column == HistoryOrigin.LIVE.value
    return or_(column.is_(None), column.not_in(PHASE_A_HISTORY_SOURCES))
