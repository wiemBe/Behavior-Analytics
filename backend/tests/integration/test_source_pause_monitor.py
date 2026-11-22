"""D-52 stale-collection monitoring uses stored flags and the database rollout switch."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.collectors.log_source_collector import COLLECTOR_NAME as INVENTORY_COLLECTOR
from app.collectors.metric_collector import COLLECTOR_NAME
from app.core.config import get_settings
from app.i18n.artifact import ARTIFACT_PATH, ENGLISH_CATALOG_PATH
from app.i18n.messages import MessageRef, render
from app.models.alert import Alert, AlertNotification
from app.models.enums import AlertStatus, AlertTransition
from app.models.feature_switch import FeatureSwitch
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.workers import tasks
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

KEY = "qradar_source_pause_enabled"
DISABLED_REASON = "alert.log_source_collection.not_judged_all_sources_disabled"
NO_SOURCES_REASON = "alert.log_source_collection.not_judged_no_monitored_sources"
DISABLED_TEXT = (
    "Closed without a verdict: every monitored log source of this console is disabled "
    "in QRadar, so collection freshness is not judged."
)


class Rig:
    def __init__(self, url: str, monkeypatch: pytest.MonkeyPatch) -> None:
        self.engine = create_async_engine(url)
        self.maker = async_sessionmaker(self.engine, expire_on_commit=False)
        self.statements: list[str] = []
        self.patch = monkeypatch
        monkeypatch.setattr(tasks, "get_sessionmaker", lambda: self.maker)
        event.listen(self.engine.sync_engine, "before_cursor_execute", self.record)

    def record(self, conn, cursor, statement, *rest) -> None:  # type: ignore[no-untyped-def]
        self.statements.append(statement)

    def env_policy(self, enabled: bool) -> None:
        self.patch.setenv("QRADAR_SOURCE_PAUSE_ENABLED", str(enabled).lower())
        get_settings.cache_clear()

    async def switch(self, enabled: bool) -> None:
        async with self.maker() as session:
            row = await session.get(FeatureSwitch, KEY)
            if row is None:
                session.add(FeatureSwitch(
                    key=KEY, enabled=enabled, updated_at=datetime.now(UTC), updated_by="test"
                ))
            else:
                row.enabled = enabled
            await session.commit()

    async def console(self, flags: list[bool], *, monitored: bool = True, stale: bool = True):
        async with self.maker() as session:
            instance = await make_instance(session)
            instance.created_at = datetime.now(UTC) - timedelta(days=60)
            for index, flag in enumerate(flags):
                await make_log_source(
                    session, instance, qradar_id=1000 + index,
                    enabled=flag, monitoring_enabled=monitored,
                )
            session.add_all([
                CollectionWatermark(
                    instance_id=instance.id, collector=COLLECTOR_NAME,
                    last_run_at=datetime.now(UTC) - timedelta(days=1 if stale else 0),
                ),
                CollectionWatermark(
                    instance_id=instance.id, collector=INVENTORY_COLLECTOR,
                    last_run_at=datetime.now(UTC) - timedelta(days=30),
                    watermark_at=datetime.now(UTC) - timedelta(days=30),
                ),
            ])
            await session.commit()
            return instance

    async def run(self, instance) -> dict:  # type: ignore[no-untyped-def]
        self.statements.clear()
        result = await tasks.detect_stale_log_source_collection(instance.name)
        assert result["instances"] == 1, result
        assert result["results"][0]["status"] == "ok", result
        return result["results"][0]

    def probes(self) -> list[str]:
        return [s for s in self.statements if "FROM log_source \n" in s]

    async def rows(self) -> tuple[list[Alert], list[AlertNotification]]:
        async with self.maker() as session:
            return (
                list(await session.scalars(select(Alert))),
                list(await session.scalars(
                    select(AlertNotification).order_by(AlertNotification.created_at)
                )),
            )


@pytest.fixture
async def rig(db_schema: str, monkeypatch: pytest.MonkeyPatch):
    # Use real env settings and real DB overrides; restore both env and settings cache.
    try:
        with monkeypatch.context() as patch:
            patch.setenv("QRADAR_SOURCE_PAUSE_ENABLED", "false")
            patch.setenv("NOTIFY_GENERIC_WEBHOOK_URL", "https://notify.invalid/collector")
            patch.setenv("NOTIFY_SEND_RECOVERY", "true")
            get_settings.cache_clear()
            rig = Rig(db_schema, patch)
            try:
                yield rig
            finally:
                event.remove(rig.engine.sync_engine, "before_cursor_execute", rig.record)
                await rig.engine.dispose()
    finally:
        get_settings.cache_clear()


async def _disable(rig: Rig, instance) -> None:  # type: ignore[no-untyped-def]
    async with rig.maker() as session:
        await session.execute(
            update(LogSource).where(LogSource.instance_id == instance.id).values(enabled=False)
        )
        await session.commit()


async def _close_disabled(rig: Rig):
    await rig.switch(True)
    instance = await rig.console([True, False])
    opened = await rig.run(instance)
    assert opened["stale"] is True
    alerts, notifications = await rig.rows()
    assert len(alerts) == 1 and alerts[0].status == AlertStatus.OPEN
    assert [n.transition for n in notifications] == [AlertTransition.OPENED]
    await _disable(rig, instance)
    outcome = await rig.run(instance)
    probes = rig.probes()
    alerts, notifications = await rig.rows()
    return instance, outcome, probes, alerts, notifications


async def test_all_disabled_closes_not_judged(rig: Rig) -> None:
    instance, outcome, probes, alerts, notifications = await _close_disabled(rig)
    assert outcome["status"] == "ok"
    assert outcome["stale"] is False
    assert outcome["judged"] is False
    assert outcome["reason"] == "all_sources_disabled"
    assert len(probes) == 2
    assert "log_source.enabled IS true" in probes[0]
    assert "log_source.enabled" not in probes[1]
    assert all("LIMIT" in probe for probe in probes)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.source_id == instance.id
    assert alert.status == AlertStatus.RESOLVED
    assert alert.resolved_by == "system:log-source-collection-monitor"
    assert alert.resolution_reason_code == DISABLED_REASON
    assert alert.resolution_reason == DISABLED_TEXT
    assert [n.transition for n in notifications] == [
        AlertTransition.OPENED, AlertTransition.RESOLVED
    ]
    assert all(n.alert_id == alert.id for n in notifications)
    assert notifications[-1].payload["resolution_reason_code"] == DISABLED_REASON
    # Repeating an administrative close neither reopens nor notifies twice.
    assert (await rig.run(instance))["judged"] is False
    assert len((await rig.rows())[1]) == 2


async def test_one_enabled_continues_with_stale_inventory(rig: Rig) -> None:
    await rig.switch(True)
    instance = await rig.console([False, True])
    for _ in range(2):
        outcome = await rig.run(instance)
        assert outcome["stale"] is True
        assert outcome["judged"] is True
        assert outcome["reason"] is None
        assert len(rig.probes()) == 1
        assert sum("FROM feature_switch" in s for s in rig.statements) == 1
        # No inventory watermark is consulted to decide whether to judge freshness.
        assert len([s for s in rig.statements if "FROM collection_watermark" in s]) == 1
    alerts, notifications = await rig.rows()
    assert len(alerts) == 1 and alerts[0].status == AlertStatus.OPEN
    assert alerts[0].occurrence_count == 2
    assert [n.transition for n in notifications] == [AlertTransition.OPENED]


async def test_no_monitor_recovery_wording(rig: Rig) -> None:
    _, outcome, _, alerts, notifications = await _close_disabled(rig)
    assert outcome["judged"] is False
    rendered = render(MessageRef(DISABLED_REASON))
    assert rendered == DISABLED_TEXT
    for value in (rendered, alerts[0].resolution_reason, notifications[-1].payload[
        "resolution_reason_code"
    ]):
        assert "recover" not in value.lower()
        assert "healthy" not in value.lower()
    catalog = json.loads(ENGLISH_CATALOG_PATH.read_text())
    assert catalog["alert"]["log_source_collection"]["not_judged_all_sources_disabled"] == rendered
    codes = json.loads(ARTIFACT_PATH.read_text())
    assert codes[DISABLED_REASON]["template"] == rendered
    assert codes[DISABLED_REASON]["sample_english"] == rendered
    assert codes[DISABLED_REASON]["params"] == {}


async def test_switch_off_legacy_sql_and_stale_verdict(rig: Rig) -> None:
    # The DB row must override even an env-on policy.
    rig.env_policy(True)
    await rig.switch(False)
    instance = await rig.console([False, False])
    outcome = await rig.run(instance)
    assert outcome["stale"] is True and outcome["judged"] is True
    assert outcome["reason"] is None
    legacy_probe = select(LogSource.id).where(
        LogSource.instance_id == instance.id,
        LogSource.monitoring_enabled.is_(True),
    ).limit(1)
    # Exact cursor text from the legacy statement, including LIMIT and bind syntax.
    assert rig.probes() == [str(legacy_probe.compile(dialect=rig.engine.sync_engine.dialect))]
    alerts, notifications = await rig.rows()
    assert len(alerts) == 1 and alerts[0].status == AlertStatus.OPEN
    assert [n.transition for n in notifications] == [AlertTransition.OPENED]


async def test_unreadable_switch_table_keeps_legacy_monitor(rig: Rig) -> None:
    rig.env_policy(True)
    instance = await rig.console([False])
    async with rig.engine.begin() as connection:
        await connection.execute(text("DROP TABLE feature_switch"))
    outcome = await rig.run(instance)
    assert outcome["stale"] is True and outcome["judged"] is True
    assert outcome["reason"] is None
    assert len(rig.probes()) == 1
    assert "log_source.enabled" not in rig.probes()[0]
    alerts, notifications = await rig.rows()
    assert len(alerts) == 1 and alerts[0].status == AlertStatus.OPEN
    assert [n.transition for n in notifications] == [AlertTransition.OPENED]


@pytest.mark.parametrize("flags", [[], [True, False]], ids=["empty", "unmonitored"])
async def test_no_monitored_sources_retains_reason_and_notification(rig: Rig, flags: list[bool]):
    await rig.switch(True)
    instance = await rig.console([True])
    await rig.run(instance)
    async with rig.maker() as session:
        await session.execute(
            update(LogSource).where(LogSource.instance_id == instance.id).values(
                monitoring_enabled=False
            )
        )
        for index, flag in enumerate(flags):
            await make_log_source(
                session, instance, qradar_id=2000 + index, enabled=flag, monitoring_enabled=False
            )
        if not flags:
            # Keep the existing source in another console: probe scope must stay local.
            other = await make_instance(session)
            await session.execute(
                update(LogSource).where(LogSource.instance_id == instance.id).values(
                    instance_id=other.id, monitoring_enabled=True
                )
            )
        await session.commit()
    outcome = await rig.run(instance)
    assert outcome["stale"] is False and outcome["judged"] is False
    assert outcome["reason"] == "no_monitored_sources"
    assert len(rig.probes()) == 2
    alerts, notifications = await rig.rows()
    assert len(alerts) == 1 and alerts[0].status == AlertStatus.RESOLVED
    assert alerts[0].resolution_reason_code == NO_SOURCES_REASON
    assert [n.transition for n in notifications] == [
        AlertTransition.OPENED, AlertTransition.RESOLVED
    ]
    assert notifications[-1].payload["resolution_reason_code"] == NO_SOURCES_REASON


@pytest.mark.parametrize("stale", [True, False])
@pytest.mark.parametrize("source_count", [1, 20])
async def test_common_path_stays_one_probe(rig: Rig, stale: bool, source_count: int) -> None:
    # Same downstream SQL/budget; the probe adds only enabled and ABSENT terms.
    legacy = await rig.console([True] * source_count, stale=stale)
    enabled = await rig.console([False] * (source_count - 1) + [True], stale=stale)
    await rig.switch(False)
    off = await rig.run(legacy)
    off_statements = list(rig.statements)
    assert len(rig.probes()) == 1
    await rig.switch(True)
    on = await rig.run(enabled)
    assert len(rig.probes()) == 1
    assert "log_source.enabled IS true" in rig.probes()[0]
    added = (
        " AND log_source.enabled IS true AND NOT (EXISTS (SELECT 1 \n"
        "FROM log_source_pause \nWHERE log_source_pause.log_source_id = log_source.id "
        "AND log_source_pause.reason = $2::VARCHAR AND log_source_pause.ends_at IS NULL))"
    )
    assert added in rig.probes()[0]
    assert [
        s.replace(added, "").replace("LIMIT $3::INTEGER", "LIMIT $2::INTEGER")
        if added in s else s for s in rig.statements
    ] == off_statements
    assert sum("FROM feature_switch" in s for s in rig.statements) == 1
    for key in ("status", "stale", "judged", "reason", "transition", "consecutive_failures"):
        assert on[key] == off[key]
    alerts, notifications = await rig.rows()
    assert len(alerts) == (2 if stale else 0)
    assert len(notifications) == (2 if stale else 0)


async def test_null_stored_flag_is_not_enabled(rig: Rig) -> None:
    await rig.switch(True)
    instance = await rig.console([False])
    # The production column is NOT NULL; emulate a legacy nullable schema only in this DB.
    async with rig.engine.begin() as connection:
        await connection.execute(text("ALTER TABLE log_source ALTER COLUMN enabled DROP NOT NULL"))
        await connection.execute(
            text("UPDATE log_source SET enabled = NULL WHERE instance_id = :id"),
            {"id": instance.id},
        )
    outcome = await rig.run(instance)
    assert outcome["stale"] is False and outcome["judged"] is False
    assert outcome["reason"] == "all_sources_disabled"
    assert len(rig.probes()) == 2
    assert await rig.rows() == ([], [])
