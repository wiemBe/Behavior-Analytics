"""Real PostgreSQL/Timescale logical-backup round trip for the operator sequence."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app.models.enums import BucketCompleteness, Criticality, InstanceStatus
from app.models.identity import Role
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource, LogSourceMetric

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        TEST_DATABASE_URL is None,
        reason="set TEST_DATABASE_URL to a PostgreSQL+TimescaleDB instance",
    ),
]


def _sync_url(url: URL, database: str) -> URL:
    return url.set(drivername="postgresql+psycopg", database=database)


def _scratch_names(url: URL) -> tuple[str, str]:
    assert url.database is not None
    prefix = re.sub(r"[^A-Za-z0-9_]", "_", url.database)[:40]
    source = f"{prefix}_ops5_backup_source"
    restored = f"{prefix}_ops5_backup_restored"
    assert source != url.database and restored != url.database
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", source)
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", restored)
    return source, restored


def _admin_sql(
    admin_url: URL, statement: str, parameters: tuple[object, ...] | None = None
) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(statement, parameters or ())
    finally:
        engine.dispose()


def _drop_database(admin_url: URL, database: str, protected: str) -> None:
    assert database != protected
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", database)
    terminate = (
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid();"
    )
    _admin_sql(admin_url, terminate, (database,))
    _admin_sql(admin_url, f'DROP DATABASE IF EXISTS "{database}";')


def _create_database(admin_url: URL, database: str) -> None:
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", database)
    _admin_sql(admin_url, f'CREATE DATABASE "{database}";')


def _prepare_extensions(url: URL) -> None:
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS timescaledb;")
            connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    finally:
        engine.dispose()


def _migrate(url: URL) -> None:
    # No ini file: env.py would call fileConfig() and disable every existing app.* logger.
    config = Config()
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    command.upgrade(config, "head")


def _insert_fixture(url: URL) -> None:
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            instance = QRadarInstance(
                name="ops5-roundtrip-instance",
                console_host="fixture.invalid",
                api_version="20.0",
                verify_ssl=True,
                provider_kind="mock",
                enabled=True,
                status=InstanceStatus.UNKNOWN,
                consecutive_failures=0,
            )
            source = LogSource(
                instance=instance,
                qradar_id=5005,
                name="ops5-roundtrip-source",
                enabled=True,
                criticality=Criticality.MEDIUM,
                monitoring_enabled=True,
                maintenance_mode=False,
                business_hours_only=False,
                business_hours_start=8,
                business_hours_end=18,
                business_days=[1, 2, 3, 4, 5],
                timezone_name="UTC",
                custom_thresholds={},
            )
            metric = LogSourceMetric(
                log_source=source,
                bucket_start=datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
                bucket_seconds=300,
                event_count=4242,
                average_eps=14.14,
                peak_eps=21.0,
                unknown_event_count=0,
                stored_event_count=4242,
                distinct_qid_count=7,
                distinct_username_count=3,
                distinct_source_ip_count=5,
                collection_error_count=0,
                completeness=BucketCompleteness.COMPLETE,
                collection_source="ops5-roundtrip",
                query_provenance={},
            )
            session.add_all(
                [
                    Role(
                        id=uuid.uuid4(),
                        name="ops5-roundtrip-role",
                        description="backup round-trip marker",
                        permissions=["read:test"],
                    ),
                    metric,
                ]
            )
            session.commit()
    finally:
        engine.dispose()


def _container_for_port(port: int) -> str | None:
    docker = shutil.which("docker")
    if docker is None:
        return None
    result = subprocess.run(  # noqa: S603
        [docker, "ps", "--format", "{{.Names}}\t{{.Ports}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        return None
    marker = f":{port}->5432/tcp"
    matches = [
        line.split("\t", 1)[0]
        for line in result.stdout.splitlines()
        if "\t" in line and marker in line.split("\t", 1)[1]
    ]
    if len(matches) != 1:
        return None
    check = subprocess.run(  # noqa: S603
        [docker, "exec", matches[0], "sh", "-c", "command -v pg_dump && command -v pg_restore"],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    return matches[0] if check.returncode == 0 else None


def _dump_and_restore(source_url: URL, restored_url: URL, dump: Path) -> None:
    pg_dump = shutil.which("pg_dump")
    pg_restore = shutil.which("pg_restore")
    environment = os.environ.copy()
    if source_url.password is not None:
        environment["PGPASSWORD"] = source_url.password
    if pg_dump is not None and pg_restore is not None:
        def connection_args(url: URL) -> list[str]:
            assert url.host is not None and url.username is not None and url.database is not None
            return [
                "-h",
                url.host,
                "-p",
                str(url.port or 5432),
                "-U",
                url.username,
                "-d",
                url.database,
            ]

        with dump.open("wb") as output:
            subprocess.run(  # noqa: S603
                [pg_dump, "-Fc", "--no-owner", *connection_args(source_url)],
                stdout=output,
                stderr=subprocess.PIPE,
                env=environment,
                check=True,
                timeout=120,
            )
        with dump.open("rb") as input_file:
            subprocess.run(  # noqa: S603
                [pg_restore, "--no-owner", *connection_args(restored_url)],
                stdin=input_file,
                stderr=subprocess.PIPE,
                env=environment,
                check=True,
                timeout=120,
            )
        return

    port = source_url.port or 5432
    container = _container_for_port(port)
    if container is None:
        pytest.skip("neither host pg_dump/pg_restore nor a matching test-db container is available")
    assert source_url.username is not None
    docker = shutil.which("docker")
    assert docker is not None
    with dump.open("wb") as output:
        subprocess.run(  # noqa: S603
            [
                docker,
                "exec",
                container,
                "pg_dump",
                "-Fc",
                "--no-owner",
                "-U",
                source_url.username,
                "-d",
                source_url.database or "",
            ],
            stdout=output,
            stderr=subprocess.PIPE,
            check=True,
            timeout=120,
        )
    with dump.open("rb") as input_file:
        subprocess.run(  # noqa: S603
            [
                docker,
                "exec",
                "-i",
                container,
                "pg_restore",
                "--no-owner",
                "-U",
                restored_url.username or "",
                "-d",
                restored_url.database or "",
            ],
            stdin=input_file,
            stderr=subprocess.PIPE,
            check=True,
            timeout=120,
        )


def _scalar(url: URL, query: str) -> object:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return connection.execute(text(query)).scalar_one()
    finally:
        engine.dispose()


def test_backup_restore_roundtrip_preserves_schema_row_and_hypertable(tmp_path: Path) -> None:
    assert TEST_DATABASE_URL is not None
    assigned = make_url(TEST_DATABASE_URL)
    assert assigned.database is not None
    source_name, restored_name = _scratch_names(assigned)
    admin_url = _sync_url(assigned, "postgres")
    source_url = _sync_url(assigned, source_name)
    restored_url = _sync_url(assigned, restored_name)
    dump = tmp_path / "database.dump"

    for database in (restored_name, source_name):
        _drop_database(admin_url, database, assigned.database)
    try:
        _create_database(admin_url, source_name)
        _prepare_extensions(source_url)
        _migrate(source_url)
        _insert_fixture(source_url)

        _create_database(admin_url, restored_name)
        _prepare_extensions(restored_url)
        restored_engine = create_engine(restored_url, isolation_level="AUTOCOMMIT")
        try:
            with restored_engine.connect() as connection:
                connection.exec_driver_sql("SELECT timescaledb_pre_restore();")
        finally:
            restored_engine.dispose()

        _dump_and_restore(source_url, restored_url, dump)

        restored_engine = create_engine(restored_url, isolation_level="AUTOCOMMIT")
        try:
            with restored_engine.connect() as connection:
                connection.exec_driver_sql("SELECT timescaledb_post_restore();")
        finally:
            restored_engine.dispose()

        assert _scalar(source_url, "SELECT version_num FROM alembic_version") == _scalar(
            restored_url, "SELECT version_num FROM alembic_version"
        )
        assert _scalar(
            restored_url, "SELECT count(*) FROM role WHERE name = 'ops5-roundtrip-role'"
        ) == 1
        assert _scalar(
            restored_url,
            "SELECT event_count FROM log_source_metric WHERE event_count = 4242",
        ) == 4242
        assert _scalar(
            restored_url,
            "SELECT EXISTS (SELECT 1 FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'log_source_metric')",
        ) is True
    finally:
        for database in (restored_name, source_name):
            _drop_database(admin_url, database, assigned.database)

