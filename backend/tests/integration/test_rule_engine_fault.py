"""SOC-6i: a rule engine failure keeps the collected windows and never re-searches them.

The task path runs for real: the real `RuleMatchCollector` against a counting
provider, then an engine that does its work and raises. Ticks are driven by a
shared clock so the collector's `collected_at` and the engine's judged-through
marker live on one timeline.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.collectors.rule_match_collector import RuleMatchCollector
from app.core.config import Settings
from app.models.alert import Alert
from app.models.monitoring import CollectionWatermark
from app.models.rule import AnalyticsRule, RuleMatchWindow
from app.models.rule_anomaly import RuleDetectorState
from app.rules.engine import RuleMatchEngine
from app.rules.engine_tick import EVALUATION_COLLECTOR, RuleEngineTick
from app.services.search_cadence import SearchCadenceService
from tests.integration.factories import make_instance
from tests.integration.test_rule_match_collection import ScriptedProvider
from tests.integration.test_rule_match_engine import _settings

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

HOUR = timedelta(hours=1)
SECRET = "payload-value-9f3c"


class _Rig:
    """One shared clock, one counting provider, one engine that can be told to fail."""

    def __init__(self, db_session, monkeypatch, settings: Settings) -> None:
        from contextlib import asynccontextmanager

        from app.workers import tasks

        self.session = db_session
        self.tasks = tasks
        self.settings = settings
        self.now = datetime.now(UTC).replace(minute=10, second=0, microsecond=0)
        self.provider = ScriptedProvider({70: (3, 9)})
        self.fail = False
        self.collector_runs = 0
        self.probes: list[list[datetime]] = []
        rig = self

        class _Engine(RuleMatchEngine):
            async def _probe(self, instance_id, written_since):  # type: ignore[no-untyped-def]
                found = await super()._probe(instance_id, written_since)
                rig.probes.append(list(found))
                return found

            async def evaluate(self, instance, **kwargs):  # type: ignore[no-untyped-def]
                report = await super().evaluate(instance, **kwargs)
                if rig.fail:
                    raise RuntimeError(f"engine bug {SECRET}")
                return report

        class _Collector(RuleMatchCollector):
            def __init__(self, session, provider) -> None:  # type: ignore[no-untyped-def]
                rig.collector_runs += 1
                super().__init__(
                    session,
                    rig.provider,
                    settings=settings,
                    clock=lambda: rig.now,
                )

        def _tick(session, engine, tick_settings):  # type: ignore[no-untyped-def]
            # After the collector's own clock, as real time is after a write.
            return RuleEngineTick(
                session,
                engine,
                tick_settings,
                clock=lambda: rig.now + timedelta(seconds=1),
            )

        @asynccontextmanager
        async def _same():
            yield db_session

        class _Provider:
            async def aclose(self) -> None:
                return None

        async def _claim(self, family):  # type: ignore[no-untyped-def]
            return object()

        monkeypatch.setattr(tasks, "get_settings", lambda: settings)
        monkeypatch.setattr(tasks, "get_sessionmaker", lambda: (lambda: _same()))
        monkeypatch.setattr(tasks, "build_provider_for_instance", lambda *a, **k: _Provider())
        monkeypatch.setattr(tasks, "RuleMatchCollector", _Collector)
        monkeypatch.setattr(tasks, "RuleMatchEngine", _Engine)
        monkeypatch.setattr(tasks, "RuleEngineTick", _tick)
        monkeypatch.setattr(SearchCadenceService, "claim_slot", _claim)

    async def tick(self) -> dict:
        self.now += HOUR
        result = await self.tasks.collect_rule_matches()
        (item,) = result["results"]
        return item

    async def marker(self) -> CollectionWatermark:
        return await self.session.scalar(
            select(CollectionWatermark)
            .where(CollectionWatermark.collector == EVALUATION_COLLECTOR)
            .execution_options(populate_existing=True)
        )

    async def count(self, model) -> int:
        return int(await self.session.scalar(select(func.count()).select_from(model)))


async def _rig(db_session, monkeypatch, **overrides) -> _Rig:
    instance = await make_instance(db_session)
    settings = _settings(
        rule_match_max_windows_per_run=1,
        rule_match_repair_max_windows_per_run=1,
        rule_match_max_rows=100,
        rule_match_max_window_attempts=2,
        **overrides,
    )
    rig = _Rig(db_session, monkeypatch, settings)
    session = db_session
    session.add(
        AnalyticsRule(
            instance_id=instance.id,
            qradar_id=70,
            name="fault-rule",
            rule_type="EVENT",
            enabled=True,
            first_seen_at=rig.now - timedelta(days=30),
            qradar_created_at=rig.now - timedelta(days=30),
        )
    )
    await session.flush()
    return rig


async def test_a_failing_engine_commits_the_windows_and_is_not_researched(
    db_session, monkeypatch, caplog
) -> None:
    rig = await _rig(db_session, monkeypatch)
    rig.fail = True
    with caplog.at_level(logging.WARNING, logger="app.rules.engine_tick"):
        first = await rig.tick()
    assert first["status"] == "ok"  # collection succeeded; the engine is what failed
    assert first["engine_error_class"] == "RuntimeError"
    assert SECRET not in str(first)
    assert SECRET not in caplog.text
    (record,) = [r for r in caplog.records if r.name == "app.rules.engine_tick"]
    assert record.error_class == "RuntimeError"  # type: ignore[attr-defined]
    searched = list(rig.provider.calls)
    assert len(searched) == 1
    # Windows committed, the engine's own writes (judgement, alerts) rolled back.
    assert await rig.count(RuleMatchWindow) == 1
    assert await rig.count(RuleDetectorState) == 0
    assert await rig.count(Alert) == 0
    marker = await rig.marker()
    assert (marker.consecutive_failures, marker.last_error) == (1, "RuntimeError")
    assert marker.collection_metadata["batch_attempts"] == 1

    # The next tick searches only the new window, never the committed one again.
    second = await rig.tick()
    assert second["engine_error_class"] == "RuntimeError"
    assert rig.provider.calls[: len(searched)] == searched
    assert len(rig.provider.calls) == 2
    assert len(set(rig.provider.calls)) == 2
    assert await rig.count(RuleMatchWindow) == 2


async def test_the_next_successful_run_judges_what_the_failed_run_missed(
    db_session, monkeypatch
) -> None:
    rig = await _rig(db_session, monkeypatch)
    rig.fail = True
    await rig.tick()
    missed = rig.probes[-1]
    assert len(missed) == 1

    rig.fail = False
    ok = await rig.tick()
    assert "engine_error_class" not in ok
    # The probe saw the window the failed run wrote as well as the new one.
    assert set(missed) <= set(rig.probes[-1])
    assert len(rig.probes[-1]) == 2
    assert await rig.count(RuleDetectorState) >= 1
    marker = await rig.marker()
    assert (marker.consecutive_failures, marker.last_error) == (0, None)
    assert marker.collection_metadata["batch_attempts"] == 0

    # Judged windows are not judged again.
    await rig.tick()
    assert len(rig.probes[-1]) == 1


async def test_a_permanent_failure_gives_up_after_the_limit_and_says_so(
    db_session, monkeypatch
) -> None:
    rig = await _rig(db_session, monkeypatch)
    rig.fail = True
    await rig.tick()
    await rig.tick()  # attempt 2 of 2: the batch is given up
    marker = await rig.marker()
    assert marker.consecutive_failures == 2
    assert marker.last_error == "RuntimeError"
    assert marker.collection_metadata["given_up_batches"] == 1
    assert marker.collection_metadata["batch_attempts"] == 0
    assert "last_given_up_at" in marker.collection_metadata

    # The given-up windows are never retried: the next batch holds only its own.
    rig.fail = False
    await rig.tick()
    assert len(rig.probes[-1]) == 1
    marker = await rig.marker()
    assert marker.consecutive_failures == 0
    assert marker.collection_metadata["given_up_batches"] == 1
    # The given-up windows stay stored, unjudged, never read as healthy.
    assert await rig.count(RuleMatchWindow) == 3


async def test_a_tick_that_could_not_lock_does_not_judge_or_mark(
    db_session, monkeypatch
) -> None:
    rig = await _rig(db_session, monkeypatch)
    await rig.tick()
    before = (await rig.marker()).watermark_at
    probes = len(rig.probes)

    class _Locked(RuleMatchCollector):
        async def collect(self, instance):  # type: ignore[no-untyped-def]
            report = await super().collect(instance)
            report.skipped_locked = True
            return report

    monkeypatch.setattr(
        rig.tasks,
        "RuleMatchCollector",
        lambda session, provider: _Locked(
            session, rig.provider, settings=rig.settings, clock=lambda: rig.now
        ),
    )
    item = await rig.tick()
    assert item["status"] == "skipped_locked"
    assert len(rig.probes) == probes
    assert (await rig.marker()).watermark_at == before
