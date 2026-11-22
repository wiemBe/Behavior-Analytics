"""D-99: collection keeps its measurements while unreadable policy refuses training."""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.collectors.metric_collector import COLLECTOR_NAME, MetricCollector
from app.history import phase_a
from app.history.progress_codec import decode_progress, encode_progress
from app.models.feature_switch import FeatureSwitch
from app.models.log_source import LogSourceBaseline, LogSourceMetric
from app.models.monitoring import CollectionWatermark
from app.services.feature_switches import UNREADABLE_REASON
from app.workers import tasks
from tests.integration.history_phase_a_support import ANCHOR, FIRST_TICK, STEP, scope_of
from tests.integration.test_history_rebuild_source_pauses import _pending, _record

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("policy", [None, False, True], ids=["unreadable", "off", "on"])
async def test_collect_metrics_pending_training_policy(
    db_session, monkeypatch, caplog, policy,
):
    console, provider, settings = await _pending(db_session, pause=False, count=3)
    console.sources[2].enabled = False
    # Start the actual task with only pending work, no cells from fixture preparation.
    await db_session.execute(delete(LogSourceBaseline))
    scope = await scope_of(db_session, console.instance)
    before_progress = copy.deepcopy(scope.progress)
    before_scope = {
        column.name: copy.deepcopy(getattr(scope, column.name))
        for column in type(scope).__table__.columns
    }
    if policy is None:
        await db_session.execute(text("DROP TABLE feature_switch"))
    else:
        db_session.add(FeatureSwitch(
            key="qradar_source_pause_enabled", enabled=policy,
            updated_at=FIRST_TICK, updated_by="operator",
        ))
    await db_session.commit()
    instance_id = console.instance.id
    source_ids = {qid: source.id for qid, source in console.sources.items()}
    maker = async_sessionmaker(db_session.bind, expire_on_commit=False)
    # Readable rows beat the opposite environment value; unreadable beats env-on.
    settings = settings.model_copy(update={
        "qradar_source_pause_enabled": policy is not True,
        "history_backfill_baseline_sources_per_tick": 3,
    })
    at = FIRST_TICK + 2 * STEP
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "build_provider_for_instance", lambda *a, **k: provider)
    monkeypatch.setattr("app.services.search_cadence.slot_start", lambda *a, **k: at)
    reports, reasons = [], []

    class Recorder(MetricCollector):
        def __init__(self, session, provider, *, settings):
            super().__init__(session, provider, settings=settings, clock=lambda: at)

        async def collect(self, instance):
            reasons.append(self.training_disabled_reason)
            report = await super().collect(instance)
            reports.append(report)
            return report

    monkeypatch.setattr(tasks, "MetricCollector", Recorder)
    provider.calls.clear()
    with _record(db_session) as operations, caplog.at_level("WARNING", logger="app.tasks"):
        result = await tasks.collect_metrics()

    [run] = result["results"]
    [report] = reports
    reason = UNREADABLE_REASON if policy is None else None
    assert run["status"] == "ok", result
    assert run["history_rebuild_skipped_reason"] == reason
    assert reasons == [reason]
    assert report.history.error is None
    assert report.history.rebuild_skipped_reason == reason
    assert report.history.rebuilt == (0 if policy is None else 1 if policy else 2)
    assert run["history_state"] == "COMPLETE" and run["history_searches"] == 0
    assert run["buckets_collected"] == 1 and run["searches_sent"] == 1
    expected_qids = {1, 3} if policy else {1, 2, 3}
    assert run["samples"] == len(expected_qids)
    assert provider.calls == [(ANCHOR + 2 * STEP, ANCHOR + 3 * STEP, 1)]
    assert sum("FROM feature_switch" in sql for sql in operations) == 1
    assert not any("FOR NO KEY UPDATE" in sql or "FOR UPDATE" in sql for sql in operations)
    if policy is None:
        assert not any("log_source_baseline" in sql for sql in operations)
        assert not any("baseline_training_exclusion" in sql for sql in operations)
    assert [r.reason for r in caplog.records if r.name == "app.tasks"] == (
        [UNREADABLE_REASON] if policy is None else []
    )

    # Read committed effects through a fresh session, as the next worker would.
    async with maker() as session:
        scope = await scope_of(session, console.instance)
        after_progress = copy.deepcopy(scope.progress)
        pending = decode_progress(after_progress).adapter[phase_a.REBUILD_PENDING_KEY]
        if policy is None:
            assert after_progress == before_progress
            assert {
                column.name: getattr(scope, column.name)
                for column in type(scope).__table__.columns
            } == before_scope  # no history watermark, adapter or audit write
        else:
            assert pending == []
            expected_progress = copy.deepcopy(before_progress)
            # Only the pending list changes; the history reading has already finished.
            decoded = decode_progress(expected_progress)
            assert after_progress == encode_progress(
                decoded.progress, {**decoded.adapter, phase_a.REBUILD_PENDING_KEY: []},
            )
        cells = (await session.scalars(select(LogSourceBaseline))).all()
        expected_trained = set() if policy is None else {3} if policy else {2, 3}
        assert {cell.log_source_id for cell in cells} == {
            source_ids[qid] for qid in expected_trained
        }
        mark = await session.scalar(select(CollectionWatermark).where(
            CollectionWatermark.instance_id == instance_id,
            CollectionWatermark.collector == COLLECTOR_NAME,
        ))
        assert mark.watermark_at == ANCHOR + 2 * STEP
        rows = (await session.scalars(select(LogSourceMetric).where(
            LogSourceMetric.bucket_start == ANCHOR + 2 * STEP,
        ))).all()
        assert {row.log_source_id for row in rows} == {source_ids[q] for q in expected_qids}
        for row in rows:
            qid = next(q for q, source_id in source_ids.items() if source_id == row.log_source_id)
            expected = provider.sample(qid, row.bucket_start, 300)
            assert row.event_count == expected.event_count
            assert row.average_eps == expected.average_eps
            assert row.collection_source == "mock"
