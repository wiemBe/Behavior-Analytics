"""The worker tasks decide from the effective switches: row over env over default.

OPS-FLAGS S3a. Real Postgres, the real `feature_switch` table, the real task bodies.
Only the QRadar provider is a recorder that fails the test if a disabled family builds it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.models.base import utcnow
from app.models.feature_switch import FeatureSwitch
from app.services.feature_switches import UNREADABLE_REASON
from app.workers import tasks
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SLOT = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)

#: task name, the switch that gates it, and how to call it.
GATED: list[tuple[str, str]] = [
    ("collect_rule_matches", "rule_match_collection_enabled"),
    ("collect_catchall_senders", "catchall_collection_enabled"),
    ("collect_geo_countries", "geo_collection_enabled"),
    ("collect_qradar_health", "qradar_health_collection_enabled"),
    ("collect_anomaly_explanations", "explanation_enabled"),
    ("dispatch_waf_collection", "waf_collection_enabled"),
    ("evaluate_custom_rules", "custom_rules_enabled"),
    ("dispatch_ips_collection", "ips_collection_enabled"),
    ("dispatch_identity_collection", "identity_collection_enabled"),
    ("dispatch_property_watch_collection", "pw_collection_enabled"),
]
#: Tasks that are not gated by a switch but build an engine that reads one.
ENGINE_BUILDERS = ["evaluate_anomalies", "run_alert_digest", "sync_log_sources"]


class Rig:
    """A real engine and sessionmaker, with the tasks pointed at them."""

    def __init__(self, url: str, monkeypatch: pytest.MonkeyPatch) -> None:
        self.engine = create_async_engine(url)
        self.maker = async_sessionmaker(self.engine, expire_on_commit=False)
        self.monkeypatch = monkeypatch
        self.provider_builds = 0
        self.statements: list[str] = []
        monkeypatch.setattr(tasks, "get_sessionmaker", lambda: self.maker)
        monkeypatch.setattr(tasks, "build_provider_for_instance", self._build)

        @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
        def record(conn, cursor, statement, *rest):  # type: ignore[no-untyped-def]
            self.statements.append(statement)

    def _build(self, *args: object, **kwargs: object) -> object:
        self.provider_builds += 1
        raise AssertionError("a disabled family built a QRadar provider")

    def base(self, **env: bool) -> None:
        settings = Settings(_env_file=None, **env)
        self.monkeypatch.setattr(tasks, "get_settings", lambda: settings)

    async def row(self, key: str, enabled: bool) -> None:
        async with self.maker() as session:
            session.add(
                FeatureSwitch(key=key, enabled=enabled, updated_at=utcnow(), updated_by="alice")
            )
            await session.commit()

    async def run(self, name: str) -> dict:
        call: Callable[..., Any] = getattr(tasks, name)
        if name == "dispatch_waf_collection":
            return await call(enqueue=lambda _id: None)
        return await call()

    def switch_selects(self) -> int:
        return sum(1 for s in self.statements if "FROM feature_switch" in s)


@pytest.fixture
async def rig(db_schema: str, monkeypatch: pytest.MonkeyPatch):
    rig = Rig(db_schema, monkeypatch)
    try:
        yield rig
    finally:
        await rig.engine.dispose()


def _is_disabled(result: dict) -> bool:
    return result.get("status") == "disabled"


@pytest.mark.parametrize(("name", "key"), GATED)
@pytest.mark.parametrize(
    ("env", "row", "runs"),
    [
        (False, None, False),  # only env: off
        (True, None, True),  # only env: on
        (True, False, False),  # the row turns an env-on family off
        (False, True, True),  # the row turns an env-off family on
    ],
)
async def test_the_row_over_env_decides_whether_the_task_runs(
    rig: Rig, name: str, key: str, env: bool, row: bool | None, runs: bool
) -> None:
    rig.base(**{key: env})
    if row is not None:
        await rig.row(key, row)

    result = await rig.run(name)

    assert _is_disabled(result) is (not runs), result
    assert "reason" not in result
    assert rig.provider_builds == 0
    assert rig.switch_selects() == 1


@pytest.mark.parametrize(("name", "key"), GATED)
async def test_only_a_database_row_enables_a_family_the_env_leaves_off(
    rig: Rig, name: str, key: str
) -> None:
    """Nothing in the environment names the family: the row alone is enough."""
    rig.base(**{key: False})
    assert _is_disabled(await rig.run(name))
    await rig.row(key, True)
    assert not _is_disabled(await rig.run(name))


@pytest.mark.parametrize(("name", "key"), [*GATED, *[(n, "") for n in ENGINE_BUILDERS]])
async def test_an_unreadable_table_runs_nothing_and_says_why(
    rig: Rig, name: str, key: str, caplog: pytest.LogCaptureFixture
) -> None:
    rig.base(**({key: True} if key else {}))
    async with rig.engine.begin() as conn:
        await conn.execute(text("DROP TABLE feature_switch"))

    with caplog.at_level("WARNING", logger="app.tasks"):
        result = await rig.run(name)

    assert result["status"] == "disabled"
    assert result["reason"] == UNREADABLE_REASON
    assert rig.provider_builds == 0
    assert [r.task for r in caplog.records if r.name == "app.tasks"] == [name]  # type: ignore[attr-defined]


async def test_the_per_source_task_stops_when_the_row_turns_the_family_off_mid_fleet(
    rig: Rig,
) -> None:
    rig.base(waf_collection_enabled=True)
    ran: list[str] = []

    async def run_source(session: object, profile_id: uuid.UUID, **kwargs: Any) -> dict:
        assert kwargs["settings"].waf_collection_enabled is True
        ran.append(str(profile_id))
        return {"status": "ok"}

    rig.monkeypatch.setattr(tasks, "run_waf_source", run_source)
    first, second = str(uuid.uuid4()), str(uuid.uuid4())

    assert await tasks.collect_waf_source(first) == {"status": "ok"}
    await rig.row("waf_collection_enabled", False)
    assert await tasks.collect_waf_source(second) == {
        "status": "disabled",
        "profile_id": second,
    }
    assert ran == [first]


async def test_the_per_source_task_with_an_unreadable_table_does_not_run(rig: Rig) -> None:
    rig.base(waf_collection_enabled=True)
    async with rig.engine.begin() as conn:
        await conn.execute(text("DROP TABLE feature_switch"))

    async def run_source(*args: object, **kwargs: object) -> dict:
        raise AssertionError("an unreadable table must not keep the env value")

    rig.monkeypatch.setattr(tasks, "run_waf_source", run_source)
    pid = str(uuid.uuid4())
    assert await tasks.collect_waf_source(pid) == {
        "status": "disabled",
        "profile_id": pid,
        "reason": UNREADABLE_REASON,
    }


async def test_engines_are_built_from_the_effective_settings(rig: Rig) -> None:
    rig.base(
        explanation_enabled=True,
        waf_alerting_enabled=True,
        inventory_new_source_alerts_enabled=True,
    )
    for key in (
        "explanation_enabled", "waf_alerting_enabled", "inventory_new_source_alerts_enabled"
    ):
        await rig.row(key, False)
    seen: dict[str, Any] = {}

    class Engine:
        def __init__(self, session: object, *, settings: Settings, **kwargs: object) -> None:
            seen["engine"] = settings

        async def evaluate_pending(self, source: object) -> object:
            raise AssertionError("no source exists")

    async def digests(session: object, *, settings: Settings, **kwargs: object) -> list:
        seen["digest"] = settings
        return []

    class Collector:
        def __init__(self, session: object, provider: object, *, settings: Settings) -> None:
            seen["sync"] = settings

    rig.monkeypatch.setattr(tasks, "AnomalyEngine", Engine)
    rig.monkeypatch.setattr(tasks, "run_due_digests", digests)
    rig.monkeypatch.setattr(tasks, "LogSourceCollector", Collector)

    await tasks.evaluate_anomalies()
    await tasks.run_alert_digest()
    async with rig.maker() as session:
        await make_instance(session)
        await session.commit()
    rig.monkeypatch.setattr(tasks, "build_provider_for_instance", lambda *a, **k: _Closeable())
    await tasks.sync_log_sources()

    assert seen["engine"].explanation_enabled is False
    assert seen["digest"].waf_alerting_enabled is False
    assert seen["sync"].inventory_new_source_alerts_enabled is False


class _Closeable:
    async def aclose(self) -> None:
        return None


async def test_the_statements_of_a_run_do_not_grow_with_the_fleet(rig: Rig) -> None:
    rig.base(waf_collection_enabled=True, ips_collection_enabled=True)
    slots = iter(SLOT + timedelta(hours=n) for n in range(100))
    rig.monkeypatch.setattr("app.services.search_cadence.slot_start", lambda *a, **k: next(slots))
    async with rig.maker() as session:
        instance = await make_instance(session)
        await session.commit()

    async def add_sources(first: int, last: int) -> None:
        async with rig.maker() as session:
            for qradar_id in range(first, last + 1):
                await make_log_source(
                    session, instance, qradar_id=qradar_id,
                    enabled=True, monitoring_enabled=True,
                )
            await session.commit()

    # The first run of a family also restores its missing cadence seed row; not a per-source cost.
    await tasks.dispatch_waf_collection(enqueue=lambda _id: None)
    counts: list[tuple[int, int]] = []
    for first, last in ((1, 1), (2, 200)):
        await add_sources(first, last)
        rig.statements.clear()
        result = await tasks.dispatch_waf_collection(enqueue=lambda _id: None)
        assert result["status"] == "ok", result
        counts.append((rig.switch_selects(), len(rig.statements)))

    assert counts[0] == counts[1]
    assert counts[0][0] == 1
