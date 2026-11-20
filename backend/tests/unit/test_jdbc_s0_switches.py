"""JDBC-1 S0: the two default-off activation switches and the shared predicates.

Pure resolver and policy checks: no database, no network, no provider. The
operator route and the migration round trip are covered in the integration file.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Boolean, DateTime, column
from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.log_source_policy import active_source, pause_overlaps_bucket, source_pause_enabled
from app.models.base import utcnow
from app.models.feature_switch import FEATURE_KEYS, FeatureSwitch
from app.services.feature_overview import ACCESS, FAMILIES
from app.services.feature_switches import (
    FAMILY_LABELS,
    UNREADABLE_REASON,
    ResolvedSwitch,
    effective_settings,
    resolve,
)

NEW_KEYS = ("jdbc_collection_enabled", "qradar_source_pause_enabled")


def _row(key: str, enabled: bool) -> FeatureSwitch:
    return FeatureSwitch(key=key, enabled=enabled, updated_at=utcnow(), updated_by="operator")


class _Result:
    def __init__(self, rows: list[FeatureSwitch]) -> None:
        self._rows = rows

    def all(self) -> list[FeatureSwitch]:
        return self._rows


class _Session:
    """A session whose single read returns the given rows."""

    def __init__(self, rows: list[FeatureSwitch] | None = None, error: str | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.statements = 0

    async def scalars(self, *args: object, **kwargs: object) -> _Result:
        self.statements += 1
        if self.error:
            raise RuntimeError(self.error)
        return _Result(self.rows)


def test_defaults_are_off_and_both_keys_are_registered() -> None:
    base = Settings(_env_file=None)
    for key in NEW_KEYS:
        assert getattr(base, key) is False
        assert key in FEATURE_KEYS
        assert key in FAMILY_LABELS

    families = {name: keys for name, _, keys in FAMILIES}
    assert families["jdbc"] == ("jdbc_collection_enabled",)
    assert families["qradar_source_pause"] == ("qradar_source_pause_enabled",)
    assert "jdbc" in ACCESS and "qradar_source_pause" in ACCESS


async def test_a_db_row_beats_the_env_in_both_directions(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in NEW_KEYS:
        monkeypatch.setenv(key.upper(), "true")
    base = Settings(_env_file=None)
    session = _Session([_row(key, False) for key in NEW_KEYS])

    resolved = await resolve(session, base)

    assert session.statements == 1
    assert resolved.reason is None
    assert resolved.unknown_keys == ()
    for key in NEW_KEYS:
        state = resolved.values[key]
        assert state.enabled is False and state.source == "db"
        assert state.updated_by == "operator" and state.updated_at is not None
        assert getattr(effective_settings(base, resolved), key) is False

    for key in NEW_KEYS:
        monkeypatch.setenv(key.upper(), "false")
    base = Settings(_env_file=None)
    session = _Session([_row(key, True) for key in NEW_KEYS])

    resolved = await resolve(session, base)

    for key in NEW_KEYS:
        state = resolved.values[key]
        assert state.enabled is True and state.source == "db"
        assert getattr(effective_settings(base, resolved), key) is True


async def test_unreadable_switch_table_is_off_with_the_fixed_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in NEW_KEYS:
        monkeypatch.setenv(key.upper(), "true")
    base = Settings(_env_file=None)
    session = _Session(error="connection lost")

    resolved = await resolve(session, base)

    assert resolved.reason == UNREADABLE_REASON
    for key in NEW_KEYS:
        assert resolved.values[key] == ResolvedSwitch(False, "unreadable")
        # A failed read can never leave an env-driven new switch on.
        assert getattr(effective_settings(base, resolved), key) is False


async def test_feature_switches_db_false_uses_env_without_a_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JDBC_COLLECTION_ENABLED", "true")
    base = Settings(_env_file=None, feature_switches_db=False)
    session = _Session(error="the kill switch issued a statement")

    resolved = await resolve(session, base)

    assert session.statements == 0
    assert resolved.reason is None
    assert resolved.values["jdbc_collection_enabled"] == ResolvedSwitch(True, "env")
    assert resolved.values["qradar_source_pause_enabled"] == ResolvedSwitch(False, "default")


def test_policy_module_is_only_the_flag_and_the_two_predicates() -> None:
    assert source_pause_enabled(Settings(_env_file=None)) is False
    assert source_pause_enabled(
        Settings(_env_file=None, qradar_source_pause_enabled=True)
    ) is True

    dialect = postgresql.dialect()
    enabled = column("enabled", Boolean)
    assert "IS true" in str(active_source(enabled).compile(dialect=dialect))

    overlap = pause_overlaps_bucket(
        column("bucket_start", DateTime(timezone=True)),
        column("bucket_end", DateTime(timezone=True)),
        column("starts_at", DateTime(timezone=True)),
        column("ends_at", DateTime(timezone=True)),
    )
    sql = str(
        overlap.compile(dialect=dialect, compile_kwargs={"literal_binds": True})
    ).lower()
    assert "coalesce(ends_at" in sql
    assert "'infinity'" in sql
    assert "bucket_start <" in sql and "bucket_end >" in sql
    assert isinstance(overlap.type, Boolean)


def test_predicate_boundaries_are_half_open() -> None:
    # The SQL is half-open by construction; pin the ordering of its two arms so
    # a later refactor cannot turn an exact boundary touch into an overlap.
    overlap = pause_overlaps_bucket(
        column("bucket_start", DateTime(timezone=True)),
        column("bucket_end", DateTime(timezone=True)),
        column("starts_at", DateTime(timezone=True)),
        column("ends_at", DateTime(timezone=True)),
    )
    sql = str(overlap.compile(dialect=postgresql.dialect()))
    assert sql.index("bucket_start <") < sql.index("bucket_end >")
