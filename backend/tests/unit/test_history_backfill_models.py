"""The history-backfill tables: every CHECK literal is pinned to its enum or constant."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.history.origin import PHASE_A_HISTORY_SOURCES, HistoryOrigin
from app.history.state import SCOPE_TABLE_FAMILIES, HistoryFamily, HistoryState
from app.models import Base
from app.models.catchall import CatchallBucket
from app.models.enums import HistoryDecisionAnswer, HistoryDecisionSource
from app.models.history_backfill import (
    HistoryBackfillDecision,
    HistoryBackfillScope,
)
from app.models.ips import IpsSourceBucket
from app.models.rule import RuleMatchWindow
from app.models.waf import WafSourceBucket

MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0030_history_backfill.py"
)


def _check(model: type, name: str) -> str:
    return str(
        next(item.sqltext for item in model.__table__.constraints if item.name.endswith(name))  # type: ignore[attr-defined]
    )


def _in(values: list[str]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def _migration():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("migration_0030", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_enums_are_the_documented_vocabularies() -> None:
    assert [m.value for m in HistoryDecisionAnswer] == ["YES", "NO"]
    assert [m.value for m in HistoryDecisionSource] == ["INSTALLER", "UI"]
    assert [m.value for m in HistoryOrigin] == ["LIVE", "HISTORY"]


def test_decision_check_literals_stay_in_parity() -> None:
    assert _check(HistoryBackfillDecision, "answer_known") == (
        f"answer IN {_in([m.value for m in HistoryDecisionAnswer])}"
    )
    assert _check(HistoryBackfillDecision, "source_known") == (
        f"source IN {_in([m.value for m in HistoryDecisionSource])}"
    )
    assert _check(HistoryBackfillDecision, "days_iff_yes") == (
        "(answer = 'YES') = (days IS NOT NULL)"
    )
    assert _check(HistoryBackfillDecision, "days_minimum") == "days IS NULL OR days >= 7"


def test_scope_check_literals_stay_in_parity() -> None:
    families = [f.value for f in HistoryFamily if f in SCOPE_TABLE_FAMILIES]
    states = [s.value for s in HistoryState if s is not HistoryState.NOT_REQUESTED]
    assert HistoryFamily.PROPERTY_WATCH.value not in families
    assert "NOT_REQUESTED" not in states
    assert _check(HistoryBackfillScope, "family_known") == f"family IN {_in(families)}"
    assert _check(HistoryBackfillScope, "state_known") == f"state IN {_in(states)}"
    assert _check(HistoryBackfillScope, "scope_key_shape") == (
        "(family IN ('WAF', 'IPS')) = (scope_key <> '')"
    )


def test_every_origin_check_matches_the_origin_enum() -> None:
    expected = f"origin IN {_in([m.value for m in HistoryOrigin])}"
    for model in (WafSourceBucket, IpsSourceBucket, CatchallBucket, RuleMatchWindow):
        assert _check(model, "origin_known") == expected, model.__name__


def test_origin_has_no_python_or_server_default() -> None:
    for model in (WafSourceBucket, IpsSourceBucket, CatchallBucket, RuleMatchWindow):
        column = model.__table__.c.origin  # type: ignore[attr-defined]
        assert column.nullable is False
        assert column.default is None and column.server_default is None


def test_phase_a_table_keeps_its_columns() -> None:
    assert "origin" not in Base.metadata.tables["log_source_metric"].c


def test_the_migration_literals_match_the_models_and_constants() -> None:
    migration = _migration()
    assert migration.PHASE_A_HISTORY_SOURCES == PHASE_A_HISTORY_SOURCES
    assert _check(WafSourceBucket, "origin_known") == migration._ORIGIN_CHECK
    assert set(migration.ORIGIN_TABLES) == {
        WafSourceBucket.__tablename__,
        IpsSourceBucket.__tablename__,
        CatchallBucket.__tablename__,
        RuleMatchWindow.__tablename__,
    }
    families = [f.value for f in HistoryFamily if f in SCOPE_TABLE_FAMILIES]
    assert ", ".join(f"'{f}'" for f in families) == migration._FAMILIES
    states = [s.value for s in HistoryState if s is not HistoryState.NOT_REQUESTED]
    assert ", ".join(f"'{s}'" for s in states) == migration._STATES
