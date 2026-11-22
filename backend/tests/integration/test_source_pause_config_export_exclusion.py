"""JDBC-1 P12a: the real config-export command never carries source-pause history.

The exporter writes only the SOC-owned log-source fields (``_SOC_FIELDS``); it has no
feature-switch section and no ``log_source_pause`` section. The pause model has no free-text
column and its ``reason`` is CHECK-constrained to QRADAR_DISABLED, so its own identifying data
is its ``starts_at``/``ends_at``. This test uses the two paused sources' UUID ids *and* the pause
rows' distinctive timestamps as tripwires in both spellings — the raw ``isoformat()`` value and
the exporter's own ``format_timestamp`` value — and checks that none of them reaches the bundle
or either output stream. It runs the actual ``python -m app.cli.config export`` command the way
the wrapper does: once on the clean success path and once with a stored dashboard that forces a
warning.

The Alembic-free schema fixture (``db_schema``) builds the tables from model metadata;
no migration is run and no process-wide state is changed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config_transfer.document import format_timestamp
from app.config_transfer.export_bundle import _SOC_FIELDS
from app.models.dashboard import Dashboard
from app.models.feature_switch import FeatureSwitch
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.log_source_pause import LogSourcePause

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
CONSOLE = "source-pause-export-probe"
# The pause model has no id column: its key is (log_source_id, reason, starts_at) and
# ``reason`` is CHECK-constrained to QRADAR_DISABLED. The two source ids below are the
# pause rows' ``log_source_id`` (the source-id tripwires to keep); the pause rows' own data
# is their starts_at/ends_at, set to fixed odd seconds+microseconds below. Neither source
# id is in ``_SOC_FIELDS``, so exporting the two parent sources never prints them. Each boundary
# is checked both as raw ISO text and through the exporter's own ``format_timestamp`` spelling,
# so a leak that reuses that function (no microseconds, trailing Z) is caught too.
TRIPWIRE_OPEN_ID = uuid.UUID("11111111-2222-4333-8444-555555555501")
TRIPWIRE_CLOSED_ID = uuid.UUID("11111111-2222-4333-8444-555555555502")
OPEN_STARTS_AT = datetime(2026, 1, 2, 3, 4, 7, 123457, tzinfo=UTC)
CLOSED_STARTS_AT = datetime(2026, 2, 3, 4, 5, 7, 234569, tzinfo=UTC)
CLOSED_ENDS_AT = datetime(2026, 2, 3, 6, 7, 7, 345691, tzinfo=UTC)
PAUSE_TRIPWIRES = (
    str(TRIPWIRE_OPEN_ID),
    str(TRIPWIRE_CLOSED_ID),
    OPEN_STARTS_AT.isoformat(),
    CLOSED_STARTS_AT.isoformat(),
    CLOSED_ENDS_AT.isoformat(),
    format_timestamp(OPEN_STARTS_AT),
    format_timestamp(CLOSED_STARTS_AT),
    format_timestamp(CLOSED_ENDS_AT),
)
SWITCH_KEY = "qradar_source_pause_enabled"
EXPECTED_ROW_KEYS = {"log_source", *_SOC_FIELDS}


def _run(url: str, cwd_backend: Path) -> subprocess.CompletedProcess[str]:
    """Run the CLI exactly as the wrapper does, against the fixture's database.

    The URL is parsed, never copied into the environment as a whole: only its parts are
    passed, the same way the other config-export integration tests drive the command.
    """
    parsed = make_url(url)
    env = dict(os.environ) | {
        "POSTGRES_USER": parsed.username or "",
        "POSTGRES_PASSWORD": parsed.password or "",
        "POSTGRES_DB": parsed.database or "",
        "POSTGRES_HOST": parsed.host or "localhost",
        "POSTGRES_PORT": str(parsed.port or 5432),
        "PYTHONPATH": str(cwd_backend),
    }
    return subprocess.run(
        [sys.executable, "-m", "app.cli.config", "export"], cwd=cwd_backend, env=env,
        capture_output=True, text=True, timeout=120, check=False,
    )


def _save_and_read(result: subprocess.CompletedProcess[str], path: Path) -> str:
    """Capture the emitted bundle as a file (the operator's artifact) and read it back."""
    path.write_text(result.stdout, encoding="utf-8")
    return path.read_text(encoding="utf-8")


async def _seed_source_pauses(db_session: AsyncSession) -> None:
    """One console, two pauses with distinctive timestamps, and the rollout switch."""
    now = datetime.now(UTC)
    console = QRadarInstance(name=CONSOLE, console_host="https://qradar.lab.internal",
                             sec_token="S3CRET-TOKEN-h4rdc0ded")
    db_session.add(console)
    await db_session.flush()
    db_session.add_all([
        LogSource(id=TRIPWIRE_OPEN_ID, instance_id=console.id, qradar_id=1,
                  name="Paused probe source", type_name="IBM QRadar",
                  enabled=False, criticality="LOW", owner="soc"),
        LogSource(id=TRIPWIRE_CLOSED_ID, instance_id=console.id, qradar_id=2,
                  name="Resumed probe source", type_name="IBM QRadar",
                  enabled=True, criticality="LOW", owner="soc"),
    ])
    await db_session.flush()
    db_session.add_all([
        LogSourcePause(log_source_id=TRIPWIRE_OPEN_ID, reason="QRADAR_DISABLED",
                       starts_at=OPEN_STARTS_AT),
        LogSourcePause(log_source_id=TRIPWIRE_CLOSED_ID, reason="QRADAR_DISABLED",
                       starts_at=CLOSED_STARTS_AT, ends_at=CLOSED_ENDS_AT),
    ])
    db_session.add(FeatureSwitch(key=SWITCH_KEY, enabled=True, updated_at=now,
                                 updated_by="cli"))
    await db_session.commit()


def _assert_no_tripwires(result: subprocess.CompletedProcess[str], bundle: str) -> None:
    """No pause data, source id or switch row may reach the file or either output stream."""
    all_tripwires = [*PAUSE_TRIPWIRES, SWITCH_KEY]
    for label, haystack in (("bundle", bundle), ("stdout", result.stdout),
                            ("stderr", result.stderr)):
        for tripwire in all_tripwires:
            assert tripwire not in haystack, f"pause data leaked into {label}: {tripwire}"


def _assert_shape(document: dict[str, object]) -> None:
    """No pause or feature section/key, and source rows hold only the exporter's SOC fields."""
    sections = document["sections"]
    assert isinstance(sections, dict)
    for name in sections:
        lower = name.lower()
        assert "pause" not in lower, f"unexpected export section: {name}"
        assert "feature" not in lower, f"unexpected export section: {name}"
    for rows in sections.values():
        assert isinstance(rows, list)
        for row in rows:
            assert isinstance(row, dict)
            for key in row:
                lower = key.lower()
                assert "pause" not in lower, f"unexpected export key: {key}"
                assert "feature" not in lower, f"unexpected export key: {key}"
    source_rows = sections["log_source_settings"]
    assert len(source_rows) == 2
    for row in source_rows:
        assert set(row) == EXPECTED_ROW_KEYS, (
            f"unexpected keys in source row: {sorted(set(row) - EXPECTED_ROW_KEYS)}"
        )
    assert [row["log_source"]["name"] for row in source_rows] == [
        "Paused probe source", "Resumed probe source",
    ]


async def test_actual_export_success_and_warning_exclude_pause_history(
    db_schema: str, db_session: AsyncSession, tmp_path: Path
) -> None:
    await _seed_source_pauses(db_session)

    # Success path: the same database holds pause rows and the switch row, but the export
    # has no warning and carries neither.
    success = _run(db_schema, BACKEND)
    assert (success.returncode, success.stderr) == (0, "")
    success_bundle = _save_and_read(success, tmp_path / "success.json")
    _assert_no_tripwires(success, success_bundle)
    success_doc = json.loads(success_bundle)
    _assert_shape(success_doc)

    # Warning path: one stored dashboard with an unsupported layout makes the real command
    # emit its fixed stderr warning and skip that dashboard. The exclusion still holds.
    db_session.add(Dashboard(owner_subject="soc", name="Tripwire legacy view",
                             visibility="PRIVATE", schema_version=1,
                             layout={"v": 2, "widgets": []}))
    await db_session.commit()
    warning = _run(db_schema, BACKEND)
    assert warning.returncode == 0, warning.stderr
    assert warning.stderr.splitlines() == [
        "warning: dashboard 'Tripwire legacy view' was not exported: "
        "layout version is not supported"
    ], "the exporter's real warning path did not run"
    warning_bundle = _save_and_read(warning, tmp_path / "warning.json")
    _assert_no_tripwires(warning, warning_bundle)
    warning_doc = json.loads(warning_bundle)
    _assert_shape(warning_doc)
    assert [row["name"] for row in warning_doc["sections"]["dashboards"]] == []
