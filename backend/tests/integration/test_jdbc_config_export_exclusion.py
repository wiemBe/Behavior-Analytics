"""JDBC-1 J1q1: the real config-export command never carries JDBC mirror data.

The exporter reads each console's log sources and writes only the SOC-owned fields
(``_SOC_FIELDS``); it has no JDBC mirror section. This test seeds JDBC configuration and
sync rows whose every settable text column holds a unique tripwire, then runs the actual
``python -m app.cli.config export`` command the way the wrapper does: once on the clean
success path and once with a stored dashboard that forces a warning. The bundle file and
both output streams are searched for every tripwire, and the exported source rows are
compared with the exporter's own field list rather than a copied one.

The Alembic-free schema fixture (``db_schema``) builds the tables from model metadata;
no migration is run and no process-wide state is changed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config_transfer.export_bundle import _SOC_FIELDS
from app.jdbc.dto import ConfigState
from app.models.dashboard import Dashboard
from app.models.instance import QRadarInstance
from app.models.jdbc import JdbcSourceConfig, JdbcSyncState
from app.models.log_source import LogSource

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
CONSOLE = "jdbc-export-probe"
# Each value is unique and short enough for the tightest column it is written to
# (``start_time_local`` is String(16)). None is ever put in a LogSource column: the
# bundle legitimately exports a source's name and type, which are not JDBC mirror data.
TRIPWIRES: dict[str, str] = {
    "database_type": "JDBCTRIP-Type-9f2c1a",
    "database_host": "JDBCTRIP-Host-9f2c1a",
    "tls_hostname": "JDBCTRIP-Tls-9f2c1a",
    "database_name": "JDBCTRIP-Db-9f2c1a",
    "schema_name": "JDBCTRIP-Schema-9f2",
    "table_name": "JDBCTRIP-Table-9f2c",
    "select_list": "JDBCTRIP-Select-9f2c1a",
    "compare_field": "JDBCTRIP-Compare-9f2",
    "start_time_local": "JDBCTRIP-Start",
    "predefined_query": "JDBCTRIP-Query-9f2c",
    "source_label": "JDBCTRIP-Label-9f2c1a",
}
SYNC_TRIPWIRE = "jdbctripfailurecode9f2c"
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


async def _seed_jdbc(db_session: AsyncSession) -> QRadarInstance:
    """One console, a JDBC source with every text column trapped, and a sync state."""
    console = QRadarInstance(name=CONSOLE, console_host="https://qradar.lab.internal",
                             sec_token="S3CRET-TOKEN-h4rdc0ded")
    db_session.add(console)
    await db_session.flush()
    source = LogSource(instance_id=console.id, qradar_id=1, protocol_type_id=8,
                       name="Probe JDBC source", type_name="IBM QRadar JDBC",
                       criticality="LOW", owner="soc")
    db_session.add(source)
    await db_session.flush()
    db_session.add(JdbcSourceConfig(
        log_source_id=source.id, protocol_type_id=8,
        config_state=ConfigState.COMPLETE.value, allowlist_version=1,
        observed_at=datetime.now(UTC), field_states={},
        failure_code=SYNC_TRIPWIRE, **TRIPWIRES,
    ))
    db_session.add(JdbcSyncState(
        instance_id=console.id, state="COMPLETE", failure_code=SYNC_TRIPWIRE,
        pages=1, rows=1, failures=0, elapsed_ms=1,
    ))
    await db_session.commit()
    return console


def _assert_no_tripwires(result: subprocess.CompletedProcess[str], bundle: str) -> None:
    """Nothing may carry a mirrored JDBC value, in the file or on either output stream."""
    all_tripwires = [*TRIPWIRES.values(), SYNC_TRIPWIRE]
    for haystack in (bundle, result.stdout, result.stderr):
        for tripwire in all_tripwires:
            assert tripwire not in haystack, f"JDBC tripwire leaked: {tripwire}"


def _assert_shape(document: dict[str, object]) -> None:
    """No JDBC section or key, and source rows hold exactly the exporter's SOC fields."""
    sections = document["sections"]
    assert isinstance(sections, dict)
    for name in sections:
        assert "jdbc" not in name.lower(), f"unexpected export section: {name}"
    for rows in sections.values():
        assert isinstance(rows, list)
        for row in rows:
            assert isinstance(row, dict)
            for key in row:
                assert "jdbc" not in key.lower(), f"unexpected export key: {key}"
                assert "protocol_parameters" not in key.lower()
    source_rows = sections["log_source_settings"]
    assert len(source_rows) == 1
    assert set(source_rows[0]) == EXPECTED_ROW_KEYS


async def test_actual_export_success_and_warning_exclude_jdbc(
    db_schema: str, db_session: AsyncSession, tmp_path: Path
) -> None:
    await _seed_jdbc(db_session)

    # Success path: a clean console holds JDBC rows, but the export has no warning and no leak.
    success = _run(db_schema, BACKEND)
    assert (success.returncode, success.stderr) == (0, "")
    success_bundle = _save_and_read(success, tmp_path / "success.json")
    _assert_no_tripwires(success, success_bundle)
    success_doc = json.loads(success_bundle)
    _assert_shape(success_doc)

    # Warning path: one stored dashboard with an unsupported layout makes the real command
    # emit its fixed stderr warning and skip that dashboard. The JDBC exclusion still holds.
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
