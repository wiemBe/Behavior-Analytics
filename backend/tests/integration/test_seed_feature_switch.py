"""FIX-98 (D-99): the seed resolves the feature switch table before it syncs.

`seed()` is the operator path installed by INSTALL.md, so the tests run the
function itself against a real PostgreSQL schema. The mock provider never
reports an unknown (`None`) `enabled` flag — it always derives a bool from its
source shape — so the unknown-flag cases use a small `MockQRadarProvider` stub
through the same `seed.build_provider` seam the real factory occupies. The stub
records the settings the seed handed it, which is what makes a provider strict.

No process-wide state is left changed: settings are a local `Settings` object,
the sessionmaker is monkeypatched to this test's engine, and statements are
captured with a listener that is removed in a `finally`.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import ProviderKind, Settings
from app.log_source_policy import source_pause_enabled
from app.models import Base
from app.models.feature_switch import FeatureSwitch
from app.models.identity import Role
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.log_source_pause import LogSourcePause
from app.providers.dto import LogSourceDTO, LogSourceWalk
from app.providers.mock import MockQRadarProvider
from app.services import seed
from app.services.inventory_sync import InventorySyncService
from tests.integration.factories import make_instance, make_log_source

pytestmark = pytest.mark.integration

QID = 4242
ROLE_NAMES = {"admin", "analyst", "viewer", "detection_engineer"}


def _statement_signature(statement: str) -> tuple[str, ...]:
    """Reduce SQL to its (verb, first table) shape, ignoring binds and savepoint names.

    Adding, removing or reordering a statement still fails the parity pin; a new
    column, a different bind style or a renamed savepoint does not.
    """
    s = " ".join(statement.split())
    low = s.lower()
    if low.startswith("select "):
        return ("SELECT", low.split(" from ", 1)[1].split()[0])
    if low.startswith("insert into "):
        return ("INSERT", low.split("insert into ", 1)[1].split()[0])
    if low.startswith("update "):
        return ("UPDATE", low.split("update ", 1)[1].split()[0])
    if low.startswith("delete from "):
        return ("DELETE", low.split("delete from ", 1)[1].split()[0])
    if low.startswith("savepoint "):
        return ("SAVEPOINT",)
    if low.startswith("release "):
        return ("RELEASE SAVEPOINT",)
    return (s,)


# Captured with a `before_cursor_execute` recorder over the pre-change seed()
# (`git show HEAD:backend/app/services/seed.py`) in a scratch copy, on the same
# seeded state. The pre-change stream was the 20 entries below *without* the first
# line; the new seed adds exactly one switch read, in its own short session, at
# position 0. build_provider() issued no SQL in either run.
_EXPECTED_LEGACY_STATEMENTS = [
    ("SELECT", "feature_switch"),  # the one allowed extra read
    ("SELECT", "role"),
    ("INSERT", "role"),
    ("SELECT", "role"),
    ("INSERT", "role"),
    ("SELECT", "role"),
    ("INSERT", "role"),
    ("SELECT", "role"),
    ("INSERT", "role"),
    ("SELECT", "qradar_instance"),
    ("UPDATE", "qradar_instance"),
    ("SELECT", "log_source"),
    ("UPDATE", "log_source"),
    ("SAVEPOINT",),
    ("DELETE", "entity_mention"),
    ("INSERT", "entity_mention"),
    ("RELEASE SAVEPOINT",),
    ("INSERT", "classification_rule"),
    ("SELECT", "classification_rule"),
    ("SELECT", "log_source_classification"),
    ("INSERT", "log_source_classification"),
]


def _column_values(instance: QRadarInstance) -> dict[str, object]:
    """Every mapped column except the id and the two audit timestamps."""
    return {
        attr.key: getattr(instance, attr.key)
        for attr in inspect(instance).mapper.column_attrs
        if attr.key not in {"id", "created_at", "updated_at"}
    }


class UnknownFlagProvider(MockQRadarProvider):
    """Reports one source with a caller-chosen `enabled` (including `None`)."""

    def __init__(self, qid: int, enabled: bool | None) -> None:
        super().__init__()
        self.qid = qid
        self.flag = enabled
        self.walk_calls = 0

    async def list_log_source_types(self):  # type: ignore[no-untyped-def]
        return []

    async def list_log_source_groups(self):  # type: ignore[no-untyped-def]
        return []

    async def walk_log_sources(self, **kwargs):  # type: ignore[no-untyped-def]
        self.walk_calls += 1
        return LogSourceWalk(
            sources=[LogSourceDTO(qradar_id=self.qid, name="observed", enabled=self.flag)],
            complete=True,
            pages_done=1,
        )


@pytest_asyncio.fixture
async def rig(db_schema, monkeypatch):  # type: ignore[no-untyped-def]
    """A private engine/maker on the test schema, pointed at by the seed module."""
    engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    base = Settings(
        _env_file=None,
        qradar_provider=ProviderKind.MOCK,
        qradar_source_pause_enabled=False,  # env off in every case
        feature_switches_db=True,  # the table is read
        inventory_new_source_alerts_enabled=False,
    )
    monkeypatch.setattr(seed, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(seed, "get_settings", lambda: base)
    try:
        yield engine, maker, base
    finally:
        await engine.dispose()


@contextmanager
def _capture(engine):  # type: ignore[no-untyped-def]
    seen: list[tuple[str, Any]] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        seen.append((statement, parameters))

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


def _install_stub(monkeypatch, flag):  # type: ignore[no-untyped-def]
    built_settings: list[Any] = []
    providers: list[UnknownFlagProvider] = []

    def build(settings):  # type: ignore[no-untyped-def]
        built_settings.append(settings)
        provider = UnknownFlagProvider(QID, flag)
        providers.append(provider)
        return provider

    monkeypatch.setattr(seed, "build_provider", build)
    return built_settings, providers


async def _stored_source(maker):  # type: ignore[no-untyped-def]
    async with maker() as session:
        instance = await make_instance(session)
        source = await make_log_source(
            session, instance, qradar_id=QID, name="stored", enabled=False
        )
        await session.commit()
        return instance.id, source.id


async def _switch(maker, enabled: bool) -> None:
    async with maker() as session:
        session.add(
            FeatureSwitch(
                key="qradar_source_pause_enabled",
                enabled=enabled,
                updated_at=datetime.now(UTC),
                updated_by="test",
            )
        )
        await session.commit()


async def _enabled(maker, source_id) -> bool:  # type: ignore[no-untyped-def]
    async with maker() as session:
        value = await session.scalar(select(LogSource.enabled).where(LogSource.id == source_id))
    assert value is not None
    return bool(value)


async def _snapshot(maker, source_id):  # type: ignore[no-untyped-def]
    async with maker() as session:
        return (
            len((await session.scalars(select(QRadarInstance.id))).all()),
            len((await session.scalars(select(LogSource.id))).all()),
            len((await session.scalars(select(Role.name))).all()),
            await session.scalar(select(LogSource.enabled).where(LogSource.id == source_id)),
            len((await session.scalars(select(LogSourcePause.log_source_id))).all()),
        )


async def _assert_bootstrap_committed_and_no_sync(maker, instance_id, source_id, capsys):  # type: ignore[no-untyped-def]
    async with maker() as session:
        role_names = set((await session.scalars(select(Role.name))).all())
        instances = (await session.scalars(select(QRadarInstance.id))).all()
    assert role_names >= ROLE_NAMES
    assert instance_id in instances
    assert await _enabled(maker, source_id) is False
    out = capsys.readouterr().out
    assert "feature_switch_unreadable" in out
    assert "skipped" in out


async def test_db_switch_on_env_off_keeps_stored_false_and_builds_strict_provider(
    rig, monkeypatch, capsys  # type: ignore[no-untyped-def]
) -> None:
    """D-52 activation: the DB row wins, the provider is strict, a stored false stays."""
    _engine, maker, _base = rig
    _instance_id, source_id = await _stored_source(maker)
    await _switch(maker, True)
    built_settings, providers = _install_stub(monkeypatch, None)

    await seed.seed()

    assert await _enabled(maker, source_id) is False
    assert len(built_settings) == 1
    assert source_pause_enabled(built_settings[0]) is True
    assert providers[0].walk_calls == 1
    assert "seed complete" in capsys.readouterr().out


async def test_no_switch_row_env_off_is_legacy_parity(
    rig, monkeypatch, capsys  # type: ignore[no-untyped-def]
) -> None:
    """No row and env off: one switch read; every other statement is the legacy stream."""
    engine, maker, _base = rig
    _instance_id, source_id = await _stored_source(maker)
    built_settings, providers = _install_stub(monkeypatch, None)

    with _capture(engine) as seen:
        await seed.seed()

    assert await _enabled(maker, source_id) is True
    assert len(built_settings) == 1
    assert source_pause_enabled(built_settings[0]) is False
    assert providers[0].walk_calls == 1
    switch_reads = [statement for statement, _ in seen if "feature_switch" in statement.lower()]
    assert len(switch_reads) == 1
    # Pin the whole stream, not just the switch read: the pre-change sequence is
    # unchanged apart from that one read at position 0.
    assert [_statement_signature(statement) for statement, _ in seen] == _EXPECTED_LEGACY_STATEMENTS
    assert "seed complete" in capsys.readouterr().out


async def test_resolver_raising_is_fail_closed(
    rig, monkeypatch, capsys  # type: ignore[no-untyped-def]
) -> None:
    """A resolver fault commits the bootstrap rows but never walks a provider."""
    _engine, maker, _base = rig
    instance_id, source_id = await _stored_source(maker)

    def boom(session: AsyncSession, base: Settings):  # type: ignore[no-untyped-def]
        raise RuntimeError("switch store down")

    monkeypatch.setattr(seed, "resolve", boom)
    built_settings, providers = _install_stub(monkeypatch, None)

    await seed.seed()

    assert built_settings == []
    assert providers == []
    await _assert_bootstrap_committed_and_no_sync(maker, instance_id, source_id, capsys)


async def test_dropped_switch_table_is_fail_closed(
    rig, monkeypatch, capsys  # type: ignore[no-untyped-def]
) -> None:
    """A real DB error on the switch read does not abort the seed's own commit."""
    engine, maker, _base = rig
    instance_id, source_id = await _stored_source(maker)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE feature_switch"))
    built_settings, providers = _install_stub(monkeypatch, None)

    try:
        await seed.seed()
    finally:
        # Restore the table so the db_schema teardown's drop_all finds it.
        table = Base.metadata.tables["feature_switch"]
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: table.create(sync_conn, checkfirst=True))

    assert built_settings == []
    assert providers == []
    await _assert_bootstrap_committed_and_no_sync(maker, instance_id, source_id, capsys)


async def test_seed_idempotent_with_switch_on(
    rig, monkeypatch, capsys  # type: ignore[no-untyped-def]
) -> None:
    """A second run with the switch on changes no row."""
    _engine, maker, _base = rig
    _instance_id, source_id = await _stored_source(maker)
    await _switch(maker, True)
    _built_settings, providers = _install_stub(monkeypatch, None)

    await seed.seed()
    first = await _snapshot(maker, source_id)
    await seed.seed()
    second = await _snapshot(maker, source_id)

    assert first == second
    assert first[0] == 1 and first[1] == 1 and first[4] == 0
    assert len(providers) == 2
    assert all(provider.walk_calls == 1 for provider in providers)
    assert capsys.readouterr().out.count("seed complete") == 2


async def test_default_instance_helpers_create_identical_rows(rig) -> None:  # type: ignore[no-untyped-def]
    """The seed's bootstrap row matches the service helper, column for column.

    Each helper runs in its own transaction against an empty instance table; both
    rows are compared on every mapped column except id and the audit timestamps.
    """
    _engine, maker, base = rig
    async with maker() as session:  # uncommitted: closed/rolled back on exit
        seed_values = _column_values(await seed._ensure_default_instance(session))
    async with maker() as session:
        svc = InventorySyncService(session, MockQRadarProvider(), settings=base)
        svc_values = _column_values(await svc.ensure_default_instance())

    assert seed_values == svc_values
    assert seed_values["name"] == "default"
    assert seed_values["console_host"] == "mock"
    assert seed_values["provider_kind"] == "mock"


async def test_default_instance_helpers_return_existing_row(rig) -> None:  # type: ignore[no-untyped-def]
    """With a stored instance, both helpers return that first row and create nothing."""
    _engine, maker, base = rig
    instance_id, _source_id = await _stored_source(maker)
    async with maker() as session:
        assert (await seed._ensure_default_instance(session)).id == instance_id
    async with maker() as session:
        svc = InventorySyncService(session, MockQRadarProvider(), settings=base)
        assert (await svc.ensure_default_instance()).id == instance_id
    async with maker() as session:
        ids = (await session.scalars(select(QRadarInstance.id))).all()
    assert ids == [instance_id]
