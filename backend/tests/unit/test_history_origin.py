"""How a row is marked as history, per family (HISTORY-BACKFILL §B.4)."""

from __future__ import annotations

import pytest
from sqlalchemy import Column, String, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import declarative_base

from app.history.origin import (
    PHASE_A_HISTORY_SOURCE,
    HistoryOrigin,
    is_history_clause,
    is_live_clause,
    marker_column,
    marker_values,
    origin_of,
)
from app.history.state import HistoryFamily

Base = declarative_base()


class WithOrigin(Base):  # type: ignore[misc,valid-type]
    __tablename__ = "t_origin"
    id = Column(String, primary_key=True)
    origin = Column(String(8), nullable=False)


class WithSource(Base):  # type: ignore[misc,valid-type]
    __tablename__ = "t_source"
    id = Column(String, primary_key=True)
    collection_source = Column(String(32), nullable=True)


class WithNeither(Base):  # type: ignore[misc,valid-type]
    __tablename__ = "t_neither"
    id = Column(String, primary_key=True)


def _sql(clause: object) -> str:
    stmt = select(1).where(clause)  # type: ignore[arg-type]
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


def test_phase_a_marks_collection_source_and_leaves_live_alone() -> None:
    assert marker_column(HistoryFamily.PHASE_A) == "collection_source"
    assert marker_values(HistoryFamily.PHASE_A, HistoryOrigin.HISTORY) == {
        "collection_source": PHASE_A_HISTORY_SOURCE
    }
    assert marker_values(HistoryFamily.PHASE_A, HistoryOrigin.HISTORY, mock=True) == {
        "collection_source": "mock_history"
    }
    assert marker_values(HistoryFamily.PHASE_A, HistoryOrigin.LIVE) == {}


@pytest.mark.parametrize(
    "family",
    [
        HistoryFamily.WAF,
        HistoryFamily.IPS,
        HistoryFamily.CATCHALL,
        HistoryFamily.RULE_MATCH,
        HistoryFamily.PROPERTY_WATCH,
    ],
)
def test_other_families_use_the_origin_column(family: HistoryFamily) -> None:
    assert marker_column(family) == "origin"
    assert marker_values(family, HistoryOrigin.HISTORY) == {"origin": "HISTORY"}
    assert marker_values(family, HistoryOrigin.LIVE) == {"origin": "LIVE"}


def test_origin_of_reads_both_spellings_back() -> None:
    assert origin_of(HistoryFamily.PHASE_A, "ariel_history") is HistoryOrigin.HISTORY
    assert origin_of(HistoryFamily.PHASE_A, "mock_history") is HistoryOrigin.HISTORY
    assert origin_of(HistoryFamily.PHASE_A, "ariel_aggregate") is HistoryOrigin.LIVE
    assert origin_of(HistoryFamily.PHASE_A, None) is HistoryOrigin.LIVE
    assert origin_of(HistoryFamily.WAF, "HISTORY") is HistoryOrigin.HISTORY
    assert origin_of(HistoryFamily.WAF, "LIVE") is HistoryOrigin.LIVE


def test_origin_column_is_never_defaulted_to_live() -> None:
    with pytest.raises(ValueError, match="NOT NULL"):
        origin_of(HistoryFamily.IPS, None)
    with pytest.raises(ValueError, match="unknown origin"):
        origin_of(HistoryFamily.IPS, "history")


def test_clauses_for_an_origin_table() -> None:
    assert "origin = 'HISTORY'" in _sql(is_history_clause(WithOrigin))
    assert "origin = 'LIVE'" in _sql(is_live_clause(WithOrigin))


def test_clauses_for_phase_a_keep_null_live() -> None:
    history = _sql(is_history_clause(WithSource))
    assert "'ariel_history'" in history
    assert "'mock_history'" in history
    live = _sql(is_live_clause(WithSource))
    assert "IS NULL" in live
    assert "NOT IN ('ariel_history', 'mock_history')" in live


def test_a_table_without_a_marker_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot hold history"):
        is_history_clause(WithNeither)
    with pytest.raises(ValueError, match="cannot hold history"):
        is_live_clause(WithNeither)
