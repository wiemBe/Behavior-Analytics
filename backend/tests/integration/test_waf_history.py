"""HB-6: WAF history volume, enrichment, lifecycle, and transaction guarantees."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import event, func, insert, select

from app.collectors.waf_endpoint_collector import (
    WafCollectionReport,
    _BreakdownResult,
    watermark_collector,
)
from app.custom_rules.loaders import load_waf_endpoints
from app.custom_rules.preview import _load as preview_load
from app.history import HistoryFamily, HistoryState, encode_progress
from app.history.planner import ScopeProgress
from app.history.waf import HistoryVolumeBucket
from app.models.enums import (
    CustomRuleSubjectKind,
    DetectorSignal,
    SearchFamily,
    WafAnomalyType,
    WafBreakdown,
    WafMeasurement,
    WafTelemetryStatus,
)
from app.models.history_backfill import HistoryBackfillScope
from app.models.monitoring import CollectionWatermark
from app.models.waf import WafEndpoint, WafEndpointMetric, WafSourceBucket
from app.providers.dto import WafBreakdownBatch, WafBreakdownGroup
from app.repositories.history_backfill import HistoryBackfillRepository
from app.services.locks import collector_lock_key
from app.services.search_cadence import SearchCadenceService
from app.services.waf_pipeline import (
    WafPipelineReport,
    WafSourcePipeline,
    evaluation_collector,
    run_waf_source,
)
from app.waf import engine as engine_module
from app.waf.behavior import detect_new_endpoint_access
from tests.integration.factories import make_log_source
from tests.integration.test_history_backfill_api import READER, Client
from tests.integration.test_waf_ariel_budget import holder
from tests.integration.test_waf_breakdown_collection import OUTCOMES_ON
from tests.integration.test_waf_keyed_collection import ScriptedWaf, make_source
from tests.integration.test_waf_new_endpoint import (
    metric_row,
    novelty_settings,
    source_bucket,
)
from tests.integration.test_waf_productionization import STEP, T0, batch, cfg

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@contextmanager
def recorded_statements(session):  # type: ignore[no-untyped-def]
    statements: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def add_active_endpoints(session, instance_id, source_id, count: int) -> None:  # type: ignore[no-untyped-def]
    session.add_all(
        [
            WafEndpoint(
                instance_id=instance_id,
                log_source_id=source_id,
                virtual_host=None,
                normalized_endpoint=f"/fit/{index}",
                http_method="GET",
                normalizer_version=1,
                first_seen_at=T0 - timedelta(days=30),
                last_seen_at=T0,
                is_active=True,
                tracked_since=T0 - timedelta(days=30),
            )
            for index in range(count)
        ]
    )
    await session.flush()


async def test_history_volume_and_breakdown_are_bulk_and_history_only(db_session) -> None:  # type: ignore[no-untyped-def]
    settings = cfg(
        **OUTCOMES_ON,
        history_backfill_max_lower_bound_share=1.0,
        history_backfill_searches_per_tick=10,
    )
    provider = ScriptedWaf({T0: [("/portal/login", "POST", 100)]})
    provider.derive_breakdowns()
    _profile, source, _instance, collector_factory = await make_source(
        db_session, provider, settings
    )
    collector = collector_factory(T0 + STEP)
    keyed = await provider.get_waf_observations_bucketed(
        qradar_log_source_id=source.qradar_id,
        window_start=T0,
        window_end=T0 + STEP,
        bucket_seconds=int(STEP.total_seconds()),
        url_property="URL",
        method_property="HTTP_Method",
        virtual_host_property=None,
        max_groups=settings.waf_max_groups_per_bucket,
        max_rows=settings.ariel_multi_bucket_max_rows,
    )
    values = [
        HistoryVolumeBucket(batch=item, collected_at=T0 + STEP)
        for item in keyed.buckets.values()
    ]
    with recorded_statements(db_session) as volume_sql:
        folded = await collector.persist_history_volume(source, values, floor=T0)
        await db_session.flush()
    assert folded.endpoints_created == 1
    assert sum("INSERT INTO waf_endpoint " in sql for sql in volume_sql) == 1
    assert sum('INSERT INTO "waf_source_bucket" ' in sql for sql in volume_sql) == 1
    assert sum('INSERT INTO "waf_endpoint_metric" ' in sql for sql in volume_sql) == 1

    live = WafSourceBucket(
        log_source_id=source.id,
        bucket_start=T0 + STEP,
        bucket_seconds=int(STEP.total_seconds()),
        origin="LIVE",
        profile_version=1,
        metric_semantics=collector.metric_semantics,
        completeness="COMPLETE",
        endpoint_coverage_ratio=1.0,
        total_events=0,
        parsed_events=0,
        group_count=0,
        groups_truncated=False,
        unusable_groups=0,
        observed_endpoints=0,
        tracked_endpoints_observed=0,
        admitted_endpoints=0,
        below_admission_threshold=0,
        rejected_over_quota=0,
        untracked_endpoints=0,
        untracked_requests=0,
        admission_truncated=False,
        zero_filled_endpoints=0,
        request_count_measurement=WafMeasurement.EXACT.value,
        sparse_endpoint_rows=True,
        in_maintenance=False,
        action_measurement=WafTelemetryStatus.NOT_COLLECTED.value,
        response_code_measurement=WafTelemetryStatus.NOT_COLLECTED.value,
        client_measurement=WafTelemetryStatus.NOT_COLLECTED.value,
        collection_source="test",
        query_provenance={},
        collection_duration_ms=0,
        collected_at=T0 + STEP,
    )
    db_session.add(live)
    await db_session.flush()

    progress = ScopeProgress(
        target_start=T0,
        started_at=T0,
        floor=T0,
        scan_cursor=None,
    )
    with recorded_statements(db_session) as breakdown_sql:
        outcome = await collector.history_breakdown_step(
            source,
            progress,
            boundary=T0 + STEP,
            search_budget=3,
            seconds_left=30,
            cadence_seconds=int(STEP.total_seconds()),
        )
        await db_session.flush()
    assert outcome.buckets_updated == 1
    assert outcome.metrics_updated == 1
    assert sum(sql.startswith("UPDATE waf_source_bucket") for sql in breakdown_sql) == 1
    assert sum(sql.startswith("UPDATE waf_endpoint_metric") for sql in breakdown_sql) == 1

    history_bucket = await db_session.get(WafSourceBucket, (source.id, T0))
    assert history_bucket is not None
    assert history_bucket.action_measurement == WafTelemetryStatus.EXACT.value
    assert history_bucket.response_code_measurement == WafTelemetryStatus.EXACT.value
    assert history_bucket.client_measurement == WafTelemetryStatus.EXACT.value
    assert live.action_measurement == WafTelemetryStatus.NOT_COLLECTED.value
    metric = await db_session.scalar(select(WafEndpointMetric))
    assert metric is not None
    assert metric.blocked_count == 10
    endpoint = await db_session.scalar(select(WafEndpoint))
    assert endpoint is not None
    assert endpoint.first_seen_at == T0


async def test_pipeline_applies_yes_and_live_ineligibility_before_collection(db_session) -> None:  # type: ignore[no-untyped-def]
    settings = cfg(history_backfill_days=7, history_backfill_max_lower_bound_share=1.0)
    provider = ScriptedWaf({T0: [("/history", "GET", 40)]})
    profile, source, instance, _ = await make_source(db_session, provider, settings)
    repository = HistoryBackfillRepository(db_session)
    await repository.record_decision(
        instance.id,
        answer="YES",
        days=7,
        source="UI",
        decided_by="operator",
        at=T0,
    )
    await db_session.commit()

    first = await run_waf_source(
        db_session,
        profile.id,
        provider_factory=lambda _instance: provider,
        settings=settings,
        clock=lambda: T0 + STEP + timedelta(seconds=5),
    )
    assert first["status"] == "history_reading"
    assert await db_session.get(WafSourceBucket, (source.id, T0)) is None
    scope = await db_session.get(
        HistoryBackfillScope,
        (instance.id, HistoryFamily.WAF.value, str(source.qradar_id)),
    )
    assert scope is not None
    assert scope.state == HistoryState.READING.value
    assert "fitted with no tracked endpoint yet" in (scope.reason or "")

    # A separate source that already has a LIVE row is classified before its next
    # collection attempt, so history cannot be placed in front of that row.
    provider_2 = ScriptedWaf({T0: [("/live", "GET", 50)]})
    profile_2, source_2, _instance, _ = await make_source(
        db_session, provider_2, settings, qradar_id=313, instance=instance
    )
    await run_waf_source(
        db_session,
        profile_2.id,
        provider_factory=lambda _instance: provider_2,
        settings=cfg(history_backfill_enabled=False),
        clock=lambda: T0 + STEP + timedelta(seconds=5),
    )
    assert await db_session.get(WafSourceBucket, (source_2.id, T0)) is not None
    ineligible = await run_waf_source(
        db_session,
        profile_2.id,
        provider_factory=lambda _instance: provider_2,
        settings=settings,
        clock=lambda: T0 + 2 * STEP + timedelta(seconds=5),
    )
    assert ineligible["history_state"] == HistoryState.NOT_ELIGIBLE.value
    scope_2 = await db_session.get(
        HistoryBackfillScope,
        (instance.id, HistoryFamily.WAF.value, str(source_2.qradar_id)),
    )
    assert scope_2 is not None
    assert scope_2.state == HistoryState.NOT_ELIGIBLE.value
    assert "already collected live data" in (scope_2.reason or "")


async def test_waf_fits_once_and_the_estimate_route_matches_the_adapter(
    db_session, db_schema,  # type: ignore[no-untyped-def]
) -> None:
    settings = cfg(
        history_backfill_days=28,
        history_backfill_max_hours=24,
        history_backfill_searches_per_tick=1,
        history_backfill_max_lower_bound_share=1.0,
    )
    boundary = T0 + STEP
    target = boundary - timedelta(days=15)
    provider = ScriptedWaf({target - timedelta(hours=24): [("/floor", "GET", 40)]})
    profile, source, instance, _ = await make_source(db_session, provider, settings)
    await add_active_endpoints(db_session, instance.id, source.id, 50)
    await SearchCadenceService(db_session, settings).set_cadence(
        SearchFamily.WAF_COLLECTION, 300, actor="test", source_ip=None
    )
    await HistoryBackfillRepository(db_session).record_decision(
        instance.id,
        answer="YES",
        days=28,
        source="UI",
        decided_by="operator",
        at=T0,
    )
    await db_session.commit()

    await run_waf_source(
        db_session,
        profile.id,
        provider_factory=lambda _instance: provider,
        settings=settings,
        clock=lambda: boundary + timedelta(seconds=5),
    )
    scope = await db_session.get(
        HistoryBackfillScope,
        (instance.id, HistoryFamily.WAF.value, str(source.qradar_id)),
    )
    assert scope is not None
    assert scope.days_target == 15 and scope.target_start == target
    assert "window reduced from 28 to 15 days" in (scope.reason or "")
    assert provider.calls[0][2] == target - timedelta(hours=24)
    starts = (
        await db_session.scalars(
            select(WafSourceBucket.bucket_start).where(
                WafSourceBucket.log_source_id == source.id,
                WafSourceBucket.origin == "HISTORY",
            )
        )
    ).all()
    assert not starts or min(starts) >= target

    route = Client(
        db_schema,
        READER,
        settings_update={
            "waf_collection_enabled": True,
            "history_backfill_days": 28,
            "history_backfill_max_hours": 24,
            "history_backfill_searches_per_tick": 1,
        },
    )
    estimates = {
        item["family"]: item
        for item in route.get("/api/v1/admin/history-backfill/estimate").json()[0]["families"]
    }
    assert estimates["WAF"]["days_fitted"] == scope.days_target

    # A resumed scope keeps its stored fit even when a new fit would now refuse it.
    changed = settings.model_copy(
        update={
            "history_backfill_max_hours": 1,
            "history_backfill_searches_per_tick": 64,
        }
    )
    calls = len(provider.calls)
    for _ in range(5):
        await run_waf_source(
            db_session,
            profile.id,
            provider_factory=lambda _instance: provider,
            settings=changed,
            clock=lambda: boundary + timedelta(seconds=5),
        )
        await db_session.refresh(scope)
        if scope.state != HistoryState.READING.value:
            break
    assert scope.days_target == 15 and scope.target_start == target
    assert "window reduced from 28 to 15 days" in (scope.reason or "")
    assert len(provider.calls) > calls
    assert scope.state == HistoryState.COMPLETE.value


async def test_waf_refuses_below_seven_days_without_an_ariel_search(db_session) -> None:  # type: ignore[no-untyped-def]
    settings = cfg(
        history_backfill_days=28,
        history_backfill_max_hours=1,
        history_backfill_searches_per_tick=1,
    )
    provider = ScriptedWaf({T0: [("/live", "GET", 40)]})
    profile, source, instance, _ = await make_source(db_session, provider, settings)
    await add_active_endpoints(db_session, instance.id, source.id, 50)
    await SearchCadenceService(db_session, settings).set_cadence(
        SearchFamily.WAF_COLLECTION, 300, actor="test", source_ip=None
    )
    await HistoryBackfillRepository(db_session).record_decision(
        instance.id,
        answer="YES",
        days=28,
        source="UI",
        decided_by="operator",
        at=T0,
    )
    await db_session.commit()

    result = await run_waf_source(
        db_session,
        profile.id,
        provider_factory=lambda _instance: provider,
        settings=settings,
        clock=lambda: T0 + STEP + timedelta(seconds=5),
    )
    scope = await db_session.get(
        HistoryBackfillScope,
        (instance.id, HistoryFamily.WAF.value, str(source.qradar_id)),
    )
    assert scope is not None and scope.state == HistoryState.UNAVAILABLE.value
    assert "even 7 days" in (scope.reason or "")
    assert result["history_searches"] == 0 and provider.calls == []

    live = await run_waf_source(
        db_session,
        profile.id,
        provider_factory=lambda _instance: provider,
        settings=settings,
        clock=lambda: T0 + STEP + timedelta(seconds=5),
    )
    assert live["intervals_collected"] == 1


async def test_waf_first_step_adds_one_count_statement_and_later_steps_add_none(
    db_session,  # type: ignore[no-untyped-def]
) -> None:
    settings = cfg(
        history_backfill_days=7,
        history_backfill_searches_per_tick=1,
        history_backfill_max_lower_bound_share=1.0,
    )
    boundary = T0 + STEP
    target = boundary - timedelta(days=7)
    provider = ScriptedWaf(
        {
            target - timedelta(hours=24): [("/floor", "GET", 40)],
            target: [("/history", "GET", 40)],
        }
    )
    profile, source, instance, _ = await make_source(db_session, provider, settings)
    await add_active_endpoints(db_session, instance.id, source.id, 1)
    await HistoryBackfillRepository(db_session).record_decision(
        instance.id,
        answer="YES",
        days=7,
        source="UI",
        decided_by="operator",
        at=T0,
    )
    await db_session.commit()

    with recorded_statements(db_session) as first:
        await run_waf_source(
            db_session,
            profile.id,
            provider_factory=lambda _instance: provider,
            settings=settings,
            clock=lambda: boundary + timedelta(seconds=5),
        )
    with recorded_statements(db_session) as later:
        await run_waf_source(
            db_session,
            profile.id,
            provider_factory=lambda _instance: provider,
            settings=settings,
            clock=lambda: boundary + timedelta(seconds=5),
        )

    count_sql = [sql for sql in first if "count(waf_endpoint.id)" in sql.lower()]
    assert len(count_sql) == 1
    assert not [sql for sql in later if "count(waf_endpoint.id)" in sql.lower()]
    assert (len(first), len(later)) == (16, 16)


async def test_scope_upsert_refreshes_loaded_rows_once_and_skips_unloaded_rows(
    db_session,  # type: ignore[no-untyped-def]
) -> None:
    settings = cfg()
    provider = ScriptedWaf({})
    _profile, _source, instance, _ = await make_source(db_session, provider, settings)
    repository = HistoryBackfillRepository(db_session)

    def values(key: str, state: str = "READING") -> dict[str, object]:
        return {
            "instance_id": instance.id,
            "family": HistoryFamily.WAF.value,
            "scope_key": key,
            "state": state,
            "days_requested": 7,
            "days_target": 7,
            "target_start": T0,
            "floor": T0,
            "through": T0,
            "anchor": T0 + STEP,
            "started_at": T0,
            "finished_at": T0 + STEP if state == "PARTIAL" else None,
            "reason": "stopped by operator" if state == "PARTIAL" else None,
            "last_error_class": None,
            "last_error_at": None,
            "progress": {},
        }

    await repository.write_scopes([values(str(key)) for key in range(1, 11)])
    loaded = await repository.scopes(
        instance.id, HistoryFamily.WAF.value, [str(key) for key in range(1, 11)]
    )

    with recorded_statements(db_session) as loaded_sql:
        await repository.write_scopes(
            [values(str(key), HistoryState.PARTIAL.value) for key in range(1, 11)]
        )

    scope_sql = [sql for sql in loaded_sql if "history_backfill_scope" in sql]
    assert len(scope_sql) == 1  # one statement for ten rows: RETURNING refreshes every loaded scope
    assert "RETURNING" in scope_sql[0]
    assert {row.state for row in loaded.values()} == {HistoryState.PARTIAL.value}

    with recorded_statements(db_session) as unloaded_sql:
        await repository.write_scopes([values(str(key)) for key in range(11, 21)])

    scope_sql = [sql for sql in unloaded_sql if "history_backfill_scope" in sql]
    assert len(scope_sql) == 1
    assert "RETURNING" not in scope_sql[0]  # no loaded identity, so no refresh work


async def test_waf_stop_before_an_unstepped_reading_keeps_existing_evidence(
    db_session,  # type: ignore[no-untyped-def]
) -> None:
    settings = cfg(history_backfill_days=7)
    provider = ScriptedWaf({})
    profile, source, instance, _ = await make_source(db_session, provider, settings)
    repository = HistoryBackfillRepository(db_session)
    await repository.record_decision(
        instance.id,
        answer="YES",
        days=7,
        source="UI",
        decided_by="operator",
        at=T0,
    )
    target = T0 - timedelta(days=7)
    floor = target - timedelta(hours=1)
    through = T0 - STEP
    await repository.write_scopes(
        [
            {
                "instance_id": instance.id,
                "family": HistoryFamily.WAF.value,
                "scope_key": str(source.qradar_id),
                "state": HistoryState.READING.value,
                "days_requested": 7,
                "days_target": 7,
                "target_start": target,
                "floor": floor,
                "through": through,
                "anchor": T0,
                "started_at": T0,
                "finished_at": None,
                "reason": "read history now: topping up",
                "last_error_class": None,
                "last_error_at": None,
                "progress": {},
            }
        ]
    )
    await repository.request_stop(instance.id, by="operator", at=T0 + timedelta(seconds=1))
    await db_session.commit()

    await run_waf_source(
        db_session,
        profile.id,
        provider_factory=lambda _instance: provider,
        settings=settings,
        clock=lambda: T0 + STEP + timedelta(seconds=5),
    )

    stopped = await db_session.get(
        HistoryBackfillScope,
        (instance.id, HistoryFamily.WAF.value, str(source.qradar_id)),
    )
    assert stopped is not None and stopped.state == HistoryState.PARTIAL.value
    assert stopped.reason == "stopped by operator"
    assert stopped.progress == {}
    assert (stopped.days_target, stopped.target_start, stopped.floor, stopped.through) == (
        7,
        target,
        floor,
        through,
    )


async def test_history_rows_are_not_pending_or_f1_but_preview_can_read_them(db_session) -> None:  # type: ignore[no-untyped-def]
    settings = cfg()
    provider = ScriptedWaf(
        {
            T0: [("/history-only", "GET", 40)],
            T0 + STEP: [("/live-only", "GET", 50)],
        }
    )
    profile, source, instance, collector_factory = await make_source(
        db_session, provider, settings
    )
    collector = collector_factory(T0 + STEP)
    keyed = await collector._observations_keyed(
        source, T0, T0 + STEP, int(STEP.total_seconds())
    )
    await collector.persist_history_volume(
        source,
        [HistoryVolumeBucket(batch=keyed.buckets[T0], collected_at=T0 + STEP)],
        floor=T0,
    )
    endpoint = await db_session.scalar(select(WafEndpoint))
    assert endpoint is not None

    after = T0 - STEP
    assert await load_waf_endpoints(
        db_session, [endpoint.id], after=after, through=T0
    ) == {}
    preview = await preview_load(
        db_session,
        CustomRuleSubjectKind.WAF_ENDPOINT,
        str(endpoint.id),
        after=after,
        through=T0,
    )
    assert preview[str(endpoint.id)][T0].request_count == 40

    live_report = WafCollectionReport(instance.id)
    live_fetched = await collector._fetch_per_bucket_control(
        source, T0 + STEP, T0 + 2 * STEP, live_report
    )
    await collector._persist_interval(
        source,
        T0 + STEP,
        T0 + 2 * STEP,
        live_fetched,
        watermark_at=T0 + STEP,
        report=live_report,
    )

    pipeline = WafSourcePipeline(
        db_session,
        provider,
        settings=settings,
        clock=lambda: T0 + STEP,
    )
    collection = CollectionWatermark(
        instance_id=instance.id,
        collector="history-classification-collection",
    )
    evaluation = CollectionWatermark(
        instance_id=instance.id,
        collector="history-classification-evaluation",
        intervals_collected=0,
    )
    # Guard 1: no collection watermark means nothing is pending, even with a LIVE row.
    report = WafPipelineReport(profile.id, source.id)
    await pipeline._evaluate(source, collection, evaluation, report)
    assert report.evaluated_buckets == []

    # Guard 2: a LIVE row at or before the evaluation watermark is not pending again.
    collection.watermark_at = T0 + STEP
    evaluation.watermark_at = T0 + STEP
    report = WafPipelineReport(profile.id, source.id)
    await pipeline._evaluate(source, collection, evaluation, report)
    assert report.evaluated_buckets == []
    assert evaluation.watermark_at == T0 + STEP

    # Guard 3: HISTORY is not pending even with both watermarks otherwise admitting it.
    collection.watermark_at = T0
    for initial in (None, T0 - STEP):
        evaluation.watermark_at = initial
        report = WafPipelineReport(profile.id, source.id)
        await pipeline._evaluate(source, collection, evaluation, report)
        assert report.evaluated_buckets == []
        assert evaluation.watermark_at == initial


async def test_history_rows_match_live_rows_before_and_after_enrichment(db_session) -> None:  # type: ignore[no-untyped-def]
    settings = cfg(
        **OUTCOMES_ON,
        history_backfill_max_lower_bound_share=1.0,
        waf_endpoint_active_days=30,
    )
    volume = {
        T0 + index * STEP: [
            ("/portal/login", "POST", 100 + index),
            ("/api/status", "GET", 50 + index),
        ]
        for index in range(3)
    }
    provider = ScriptedWaf(volume)
    provider.derive_breakdowns()
    _profile, live_source, instance, live_factory = await make_source(
        db_session, provider, settings, qradar_id=312
    )
    _profile, history_source, _instance, history_factory = await make_source(
        db_session, provider, settings, qradar_id=313, instance=instance
    )
    live = live_factory(T0 + 3 * STEP)
    history = history_factory(T0 + 3 * STEP)

    report = WafCollectionReport(instance.id)
    for start in sorted(volume):
        fetched = await live._fetch_per_bucket_control(
            live_source, start, start + STEP, report
        )
        await live._persist_interval(
            live_source,
            start,
            start + STEP,
            fetched,
            watermark_at=start,
            report=report,
        )

    keyed = await history._observations_keyed(
        history_source, T0, T0 + 3 * STEP, int(STEP.total_seconds())
    )
    await history.persist_history_volume(
        history_source,
        [
            HistoryVolumeBucket(batch=item, collected_at=T0 + 3 * STEP)
            for _, item in sorted(keyed.buckets.items())
        ],
        floor=T0,
    )
    progress = ScopeProgress(
        target_start=T0,
        started_at=T0,
        floor=T0,
        scan_cursor=None,
    )
    enriched = await history.history_breakdown_step(
        history_source,
        progress,
        boundary=T0 + 3 * STEP,
        search_budget=3,
        seconds_left=30,
        cadence_seconds=int(STEP.total_seconds()),
    )
    assert enriched.buckets_updated == 3

    endpoint_columns = (
        "virtual_host",
        "normalized_endpoint",
        "http_method",
        "normalizer_version",
        "first_seen_at",
        "last_seen_at",
        "is_active",
        "retired_at",
        "tracked_since",
        "tracked_until",
        "baseline_rebuilt_at",
    )
    bucket_columns = (
        "bucket_start",
        "bucket_seconds",
        "profile_version",
        "metric_semantics",
        "completeness",
        "endpoint_coverage_ratio",
        "total_events",
        "parsed_events",
        "group_count",
        "groups_truncated",
        "unusable_groups",
        "observed_endpoints",
        "tracked_endpoints_observed",
        "admitted_endpoints",
        "below_admission_threshold",
        "rejected_over_quota",
        "untracked_endpoints",
        "untracked_requests",
        "admission_truncated",
        "zero_filled_endpoints",
        "request_count_measurement",
        "sparse_endpoint_rows",
        "in_maintenance",
        "action_measurement",
        "action_semantics",
        "action_events",
        "response_code_measurement",
        "response_code_semantics",
        "response_code_events",
        "client_measurement",
        "client_semantics",
        "client_events",
    )
    metric_columns = tuple(
        column.name
        for column in WafEndpointMetric.__table__.columns
        if column.name not in {
            "waf_endpoint_id",
            "collection_source",
            "query_provenance",
            "collection_duration_ms",
            "collected_at",
            "watermark_at",
            "metric_semantics",
        }
    )

    async def rows(source, model, columns):  # type: ignore[no-untyped-def]
        if model is WafEndpoint:
            records = (
                await db_session.scalars(
                    select(model)
                    .where(model.log_source_id == source.id)
                    .order_by(model.normalized_endpoint, model.http_method)
                )
            ).all()
        elif model is WafSourceBucket:
            records = (
                await db_session.scalars(
                    select(model)
                    .where(model.log_source_id == source.id)
                    .order_by(model.bucket_start)
                )
            ).all()
        else:
            records = (
                await db_session.scalars(
                    select(model)
                    .join(WafEndpoint, WafEndpoint.id == model.waf_endpoint_id)
                    .where(WafEndpoint.log_source_id == source.id)
                    .order_by(model.bucket_start, WafEndpoint.normalized_endpoint)
                )
            ).all()
        return [tuple(getattr(record, name) for name in columns) for record in records]

    assert await rows(live_source, WafEndpoint, endpoint_columns) == await rows(
        history_source, WafEndpoint, endpoint_columns
    )
    assert await rows(live_source, WafSourceBucket, bucket_columns) == await rows(
        history_source, WafSourceBucket, bucket_columns
    )
    assert await rows(live_source, WafEndpointMetric, metric_columns) == await rows(
        history_source, WafEndpointMetric, metric_columns
    )


async def test_breakdown_failure_and_untracked_endpoint(
    db_session,  # type: ignore[no-untyped-def]
) -> None:
    settings = cfg(
        **OUTCOMES_ON,
        waf_max_new_endpoints_per_bucket=1,
        history_backfill_max_lower_bound_share=1.0,
    )
    provider = ScriptedWaf(
        {T0: [("/tracked", "GET", 100), ("/over-quota", "GET", 20)]}
    )
    provider.derive_breakdowns()
    _profile, source, _instance, collector_factory = await make_source(
        db_session, provider, settings
    )
    collector = collector_factory(T0 + STEP)
    keyed = await collector._observations_keyed(
        source, T0, T0 + STEP, int(STEP.total_seconds())
    )
    await collector.persist_history_volume(
        source,
        [HistoryVolumeBucket(batch=keyed.buckets[T0], collected_at=T0 + STEP)],
        floor=T0,
    )
    bucket = await db_session.get(WafSourceBucket, (source.id, T0))
    assert bucket is not None
    assert bucket.rejected_over_quota == 1
    assert bucket.admission_truncated is True
    assert (
        await db_session.scalar(
            select(WafEndpoint).where(WafEndpoint.normalized_endpoint == "/over-quota")
        )
        is None
    )

    provider.fail_keyed_breakdown.add("ACTION")
    progress = ScopeProgress(
        target_start=T0,
        started_at=T0,
        floor=T0,
        scan_cursor=None,
    )
    failed = await collector.history_breakdown_step(
        source,
        progress,
        boundary=T0 + STEP,
        search_budget=3,
        seconds_left=30,
        cadence_seconds=int(STEP.total_seconds()),
    )
    assert failed.buckets_updated == 0
    await db_session.refresh(bucket)
    assert bucket.action_measurement == WafTelemetryStatus.NOT_COLLECTED.value
    assert bucket.response_code_measurement == WafTelemetryStatus.NOT_COLLECTED.value
    assert bucket.client_measurement == WafTelemetryStatus.NOT_COLLECTED.value

    provider.fail_keyed_breakdown.clear()
    complete = await collector.history_breakdown_step(
        source,
        progress,
        boundary=T0 + STEP,
        search_budget=3,
        seconds_left=30,
        cadence_seconds=int(STEP.total_seconds()),
    )
    assert complete.trigger is not None
    assert complete.metrics_updated == 1
    calls = len(provider.calls)
    already_done = await collector.history_breakdown_step(
        source,
        complete.progress,
        boundary=T0 + STEP,
        search_budget=3,
        seconds_left=30,
        cadence_seconds=int(STEP.total_seconds()),
    )
    assert already_done.trigger is not None
    assert len(provider.calls) == calls


async def test_worker_failure_rolls_back_rows_and_progress_then_resumes(
    db_session, monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    settings = cfg(
        history_backfill_days=7,
        history_backfill_max_lower_bound_share=1.0,
    )
    provider = ScriptedWaf(
        {
            T0: [("/resume", "GET", 80)],
            T0 + STEP: [("/after-hand-off", "GET", 90)],
        }
    )
    profile, source, instance, _ = await make_source(db_session, provider, settings)
    profile_id = profile.id
    source_id = source.id
    instance_id = instance.id
    scope_key = str(source.qradar_id)
    collection_key = watermark_collector(source)
    evaluation_key = evaluation_collector(source)
    repository = HistoryBackfillRepository(db_session)
    await repository.record_decision(
        instance.id,
        answer="YES",
        days=7,
        source="UI",
        decided_by="operator",
        at=T0,
    )
    progress = ScopeProgress(
        target_start=T0,
        started_at=T0,
        floor=T0,
        scan_cursor=None,
    )
    await repository.write_scopes(
        [
            {
                "instance_id": instance.id,
                "family": HistoryFamily.WAF.value,
                "scope_key": str(source.qradar_id),
                "state": HistoryState.READING.value,
                "days_requested": 7,
                "days_target": 7,
                "target_start": T0,
                "floor": T0,
                "through": None,
                "anchor": None,
                "started_at": T0,
                "finished_at": None,
                "reason": "test starts at a proved floor",
                "last_error_class": None,
                "last_error_at": None,
                "progress": encode_progress(progress),
            }
        ]
    )
    await db_session.commit()

    original = HistoryBackfillRepository.write_scopes

    async def crash_after_rows(self, rows):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated worker loss before scope progress")

    monkeypatch.setattr(HistoryBackfillRepository, "write_scopes", crash_after_rows)
    failed = await run_waf_source(
        db_session,
        profile_id,
        provider_factory=lambda _instance: provider,
        settings=settings,
        clock=lambda: T0 + STEP + timedelta(seconds=5),
    )
    assert failed["status"] == "failed"
    assert await db_session.get(WafSourceBucket, (source_id, T0)) is None
    scope = await db_session.get(
        HistoryBackfillScope,
        (instance_id, HistoryFamily.WAF.value, scope_key),
    )
    assert scope is not None
    assert scope.through is None

    monkeypatch.setattr(HistoryBackfillRepository, "write_scopes", original)
    resumed = await run_waf_source(
        db_session,
        profile_id,
        provider_factory=lambda _instance: provider,
        settings=settings,
        clock=lambda: T0 + STEP + timedelta(seconds=5),
    )
    assert resumed["status"] == "history_handoff"
    bucket = await db_session.get(WafSourceBucket, (source_id, T0))
    assert bucket is not None
    await db_session.refresh(scope)
    assert scope.through == T0
    assert scope.state == HistoryState.COMPLETE.value
    assert scope.anchor == T0 + STEP
    collection_wm = await db_session.scalar(
        select(CollectionWatermark).where(
            CollectionWatermark.instance_id == instance_id,
            CollectionWatermark.collector == collection_key,
        )
    )
    evaluation_wm = await db_session.scalar(
        select(CollectionWatermark).where(
            CollectionWatermark.instance_id == instance_id,
            CollectionWatermark.collector == evaluation_key,
        )
    )
    assert collection_wm is not None and collection_wm.watermark_at == T0
    assert evaluation_wm is not None and evaluation_wm.watermark_at == T0

    live = await run_waf_source(
        db_session,
        profile_id,
        provider_factory=lambda _instance: provider,
        settings=settings,
        clock=lambda: T0 + 2 * STEP + timedelta(seconds=5),
    )
    assert live["intervals_collected"] == 1
    assert (await db_session.get(WafSourceBucket, (source_id, T0))).origin == "HISTORY"  # type: ignore[union-attr]
    first_live = await db_session.get(WafSourceBucket, (source_id, T0 + STEP))
    assert first_live is not None and first_live.origin == "LIVE"


async def test_history_writer_statement_count_is_constant_at_scale(db_session) -> None:  # type: ignore[no-untyped-def]
    settings = cfg(
        waf_max_active_endpoints_per_source=5000,
    )
    provider = ScriptedWaf({})
    _profile, first_source, instance, collector_factory = await make_source(
        db_session, provider, settings
    )
    sources = [first_source]
    for index in range(1, 10):
        sources.append(
            await make_log_source(
                db_session,
                instance,
                qradar_id=1000 + index,
                name=f"scale-{index}",
            )
        )
    await db_session.commit()

    async def persist_count(source, values):  # type: ignore[no-untyped-def]
        collector = collector_factory(T0 + timedelta(days=8))
        with recorded_statements(db_session) as statements:
            await collector.persist_history_volume(source, values, floor=T0)
            await db_session.flush()
        return len(statements)

    one_bucket = [
        HistoryVolumeBucket(
            batch=batch(T0, [("/scale", "GET", 10)]),
            collected_at=T0 + STEP,
        )
    ]
    counts = {"one_source": await persist_count(sources[0], one_bucket)}
    # The tenth source runs with nine other WAF sources already present; all reads and
    # writes remain source-scoped and retain the identical four-statement shape.
    counts["ten_sources"] = await persist_count(sources[9], one_bucket)

    for label, endpoint_count, source in (
        ("60_endpoints", 60, sources[1]),
        ("1200_endpoints", 1200, sources[2]),
    ):
        await db_session.execute(
            insert(WafEndpoint),
            [
                {
                    "id": uuid.uuid4(),
                    "instance_id": instance.id,
                    "log_source_id": source.id,
                    "virtual_host": None,
                    "normalized_endpoint": f"/existing/{index}",
                    "http_method": "GET",
                    "normalizer_version": 1,
                    "first_seen_at": T0,
                    "last_seen_at": T0,
                    "is_active": True,
                    "tracked_since": T0,
                }
                for index in range(endpoint_count)
            ],
        )
        await db_session.flush()
        counts[label] = await persist_count(source, one_bucket)

    for label, bucket_count, source in (
        ("12_buckets", 12, sources[3]),
        ("2016_buckets", 2016, sources[4]),
    ):
        values = [
            HistoryVolumeBucket(
                batch=batch(T0 + index * STEP, [("/span", "GET", 10)]),
                collected_at=T0 + timedelta(days=8),
            )
            for index in range(bucket_count)
        ]
        counts[label] = await persist_count(source, values)

    assert counts == {
        "one_source": 4,
        "ten_sources": 4,
        "60_endpoints": 4,
        "1200_endpoints": 4,
        "12_buckets": 4,
        "2016_buckets": 4,
    }


async def test_history_writer_upserts_more_endpoints_than_one_statement_can_bind(
    db_session,
) -> None:  # type: ignore[no-untyped-def]
    settings = cfg(
        waf_max_new_endpoints_per_bucket=1000,
        waf_max_active_endpoints_per_source=5000,
        waf_min_admission_requests=1,
    )
    provider = ScriptedWaf({})
    _profile, source, _instance, collector_factory = await make_source(
        db_session, provider, settings
    )
    # The two timestamp columns have server defaults and are not bound by this writer.
    columns = len(WafEndpoint.__table__.columns) - 2
    row_count = 32_767 // columns + 1
    values = []
    remaining = row_count
    bucket_index = 0
    endpoint_index = 0

    def endpoint_path(index: int) -> str:
        letters = ""
        value = index
        while True:
            value, remainder = divmod(value, 26)
            letters = chr(ord("a") + remainder) + letters
            if value == 0:
                return f"/bulk/item{letters}"

    while remaining:
        in_bucket = min(1000, remaining)
        start = T0 + bucket_index * STEP
        values.append(
            HistoryVolumeBucket(
                batch=batch(
                    start,
                    [
                        (endpoint_path(index), "GET", 10)
                        for index in range(endpoint_index, endpoint_index + in_bucket)
                    ],
                ),
                collected_at=T0 + timedelta(days=8),
            )
        )
        endpoint_index += in_bucket
        remaining -= in_bucket
        bucket_index += 1

    with recorded_statements(db_session) as statements:
        folded = await collector_factory(T0 + timedelta(days=8)).persist_history_volume(
            source, values, floor=T0
        )
        await db_session.flush()

    assert row_count * columns > 32_767
    assert folded.endpoints_created == row_count
    assert await db_session.scalar(select(func.count()).select_from(WafEndpoint)) == row_count
    assert sum("INSERT INTO waf_endpoint " in sql for sql in statements) == 1


async def test_empty_history_handoff_keeps_null_watermarks_then_live_seeds(db_session) -> None:  # type: ignore[no-untyped-def]
    settings = cfg(history_backfill_days=7)
    provider = ScriptedWaf({T0 + STEP: [("/first-live", "GET", 60)]})
    profile, source, instance, _ = await make_source(db_session, provider, settings)
    repository = HistoryBackfillRepository(db_session)
    await repository.record_decision(
        instance.id,
        answer="YES",
        days=7,
        source="UI",
        decided_by="operator",
        at=T0,
    )
    boundary = T0 + STEP
    progress = ScopeProgress(
        target_start=boundary,
        started_at=T0,
        floor=boundary,
        scan_cursor=None,
    )
    await repository.write_scopes(
        [
            {
                "instance_id": instance.id,
                "family": HistoryFamily.WAF.value,
                "scope_key": str(source.qradar_id),
                "state": HistoryState.READING.value,
                "days_requested": 7,
                "days_target": 7,
                "target_start": boundary,
                "floor": boundary,
                "through": None,
                "anchor": None,
                "started_at": T0,
                "finished_at": None,
                "reason": "nothing settled",
                "last_error_class": None,
                "last_error_at": None,
                "progress": encode_progress(progress),
            }
        ]
    )
    await db_session.commit()
    handoff = await run_waf_source(
        db_session,
        profile.id,
        provider_factory=lambda _instance: provider,
        settings=settings,
        clock=lambda: boundary + timedelta(seconds=5),
    )
    assert handoff["status"] == "history_handoff"
    watermarks = (
        await db_session.scalars(
            select(CollectionWatermark).where(
                CollectionWatermark.instance_id == instance.id,
                CollectionWatermark.collector.in_(
                    [watermark_collector(source), evaluation_collector(source)]
                ),
            )
        )
    ).all()
    assert len(watermarks) == 2
    assert all(row.watermark_at is None for row in watermarks)
    assert await db_session.get(WafSourceBucket, (source.id, boundary)) is None

    live = await run_waf_source(
        db_session,
        profile.id,
        provider_factory=lambda _instance: provider,
        settings=settings,
        clock=lambda: boundary + STEP + timedelta(seconds=5),
    )
    assert live["intervals_collected"] == 1
    first = await db_session.get(WafSourceBucket, (source.id, boundary))
    assert first is not None and first.origin == "LIVE"


async def test_no_no_and_stopped_operator_paths_never_create_a_reading_scope(db_session) -> None:  # type: ignore[no-untyped-def]
    settings = cfg(history_backfill_days=7)

    undecided_provider = ScriptedWaf({T0: [("/undecided", "GET", 30)]})
    undecided, undecided_source, undecided_instance, _ = await make_source(
        db_session, undecided_provider, settings
    )
    undecided_run = await run_waf_source(
        db_session,
        undecided.id,
        provider_factory=lambda _instance: undecided_provider,
        settings=settings,
        clock=lambda: T0 + STEP + timedelta(seconds=5),
    )
    assert undecided_run["intervals_collected"] == 1
    assert (
        await db_session.get(
            HistoryBackfillScope,
            (
                undecided_instance.id,
                HistoryFamily.WAF.value,
                str(undecided_source.qradar_id),
            ),
        )
        is None
    )

    no_provider = ScriptedWaf({T0: [("/no", "GET", 30)]})
    no_profile, no_source, no_instance, _ = await make_source(
        db_session, no_provider, settings
    )
    await HistoryBackfillRepository(db_session).record_decision(
        no_instance.id,
        answer="NO",
        days=None,
        source="UI",
        decided_by="operator",
        at=T0,
    )
    await db_session.commit()
    no_run = await run_waf_source(
        db_session,
        no_profile.id,
        provider_factory=lambda _instance: no_provider,
        settings=settings,
        clock=lambda: T0 + STEP + timedelta(seconds=5),
    )
    assert no_run["intervals_collected"] == 1
    assert (
        await db_session.get(
            HistoryBackfillScope,
            (no_instance.id, HistoryFamily.WAF.value, str(no_source.qradar_id)),
        )
        is None
    )

    stopped_provider = ScriptedWaf(
        {
            T0: [("/stopped", "GET", 30)],
            T0 + STEP: [("/started-late", "GET", 30)],
        }
    )
    stopped_profile, stopped_source, stopped_instance, _ = await make_source(
        db_session, stopped_provider, settings
    )
    stopped_repository = HistoryBackfillRepository(db_session)
    await stopped_repository.record_decision(
        stopped_instance.id,
        answer="YES",
        days=7,
        source="UI",
        decided_by="operator",
        at=T0,
    )
    await stopped_repository.request_stop(
        stopped_instance.id, by="operator", at=T0 + timedelta(seconds=1)
    )
    await db_session.commit()
    stopped_run = await run_waf_source(
        db_session,
        stopped_profile.id,
        provider_factory=lambda _instance: stopped_provider,
        settings=settings,
        clock=lambda: T0 + STEP + timedelta(seconds=5),
    )
    assert stopped_run["intervals_collected"] == 1
    scope_key = (
        stopped_instance.id,
        HistoryFamily.WAF.value,
        str(stopped_source.qradar_id),
    )
    assert await db_session.get(HistoryBackfillScope, scope_key) is None

    await stopped_repository.request_start(
        stopped_instance.id, by="operator", at=T0 + timedelta(seconds=2)
    )
    await db_session.commit()
    started_late = await run_waf_source(
        db_session,
        stopped_profile.id,
        provider_factory=lambda _instance: stopped_provider,
        settings=settings,
        clock=lambda: T0 + 2 * STEP + timedelta(seconds=5),
    )
    assert started_late["history_state"] == HistoryState.NOT_ELIGIBLE.value
    scope = await db_session.get(HistoryBackfillScope, scope_key)
    assert scope is not None and scope.state == HistoryState.NOT_ELIGIBLE.value


async def reading_scope(  # type: ignore[no-untyped-def]
    session,
    *,
    instance_id,
    scope_key,
    target_start,
    floor,
    through=None,
    state=HistoryState.READING,
    anchor=None,
    progress=None,
):
    """Persist a scope as the collector's previous tick would have left it."""
    repository = HistoryBackfillRepository(session)
    decision, _created = await repository.record_decision(
        instance_id,
        answer="YES",
        days=7,
        source="UI",
        decided_by="operator",
        at=target_start,
    )
    assert decision.answer == "YES"
    body = progress or ScopeProgress(
        target_start=target_start,
        started_at=target_start,
        floor=floor,
        through=through,
        scan_cursor=None,
    )
    await repository.write_scopes(
        [
            {
                "instance_id": instance_id,
                "family": HistoryFamily.WAF.value,
                "scope_key": scope_key,
                "state": state.value,
                "days_requested": 7,
                "days_target": 7,
                "target_start": body.target_start,
                "floor": body.floor,
                "through": body.through,
                "anchor": anchor,
                "started_at": body.started_at,
                "finished_at": None,
                "reason": "test starts at a proved floor",
                "last_error_class": None,
                "last_error_at": None,
                "progress": encode_progress(body),
            }
        ]
    )
    await session.commit()


# ------------------------------------------------------- Phase 2: the guards at scale


async def test_history_volume_never_overwrites_a_live_row(db_session) -> None:  # type: ignore[no-untyped-def]
    settings = cfg(waf_max_new_endpoints_per_bucket=10)
    provider = ScriptedWaf({T0: [("/live", "GET", 100)]})
    _profile, source, _instance, collector_factory = await make_source(
        db_session, provider, settings
    )
    collector = collector_factory(T0 + STEP)
    db_session.add(source_bucket(source.id, T0, origin="LIVE"))
    await db_session.flush()
    endpoint = WafEndpoint(
        instance_id=source.instance_id,
        log_source_id=source.id,
        virtual_host=None,
        normalized_endpoint="/live",
        http_method="GET",
        normalizer_version=1,
        first_seen_at=T0,
        last_seen_at=T0,
        tracked_since=T0,
        is_active=True,
    )
    db_session.add(endpoint)
    await db_session.flush()
    db_session.add(metric_row(endpoint, T0, 100))
    await db_session.flush()

    keyed = await collector._observations_keyed(source, T0, T0 + STEP, int(STEP.total_seconds()))
    await collector.persist_history_volume(
        source,
        [HistoryVolumeBucket(batch=keyed.buckets[T0], collected_at=T0 + STEP)],
        floor=T0,
    )
    await db_session.flush()

    bucket = await db_session.get(WafSourceBucket, (source.id, T0))
    assert bucket is not None
    assert bucket.origin == "LIVE" and bucket.query_provenance == {}
    metric = await db_session.scalar(
        select(WafEndpointMetric).where(
            WafEndpointMetric.waf_endpoint_id == endpoint.id,
            WafEndpointMetric.bucket_start == T0,
        )
    )
    assert metric is not None
    assert metric.request_count == 100 and metric.query_provenance == {}


async def test_breakdown_updates_only_history_rows_and_tracked_endpoints(
    db_session,  # type: ignore[no-untyped-def]
) -> None:
    settings = cfg(**OUTCOMES_ON, history_backfill_max_lower_bound_share=1.0)
    provider = ScriptedWaf({T0: [("/portal/login", "POST", 100)]})
    _profile, source, _instance, collector_factory = await make_source(
        db_session, provider, settings
    )
    collector = collector_factory(T0 + 2 * STEP)

    def endpoint(name: str, *, tracked_since, tracked_until=None):  # type: ignore[no-untyped-def]
        return WafEndpoint(
            instance_id=source.instance_id,
            log_source_id=source.id,
            virtual_host=None,
            normalized_endpoint=name,
            http_method="POST",
            normalizer_version=1,
            first_seen_at=T0,
            last_seen_at=T0,
            tracked_since=tracked_since,
            tracked_until=tracked_until,
            is_active=tracked_until is None,
        )

    history_endpoint = endpoint("/portal/login", tracked_since=T0)
    untracked = endpoint("/not-yet", tracked_since=T0 + STEP)
    db_session.add_all([history_endpoint, untracked])
    await db_session.flush()
    db_session.add(
        source_bucket(source.id, T0, origin="HISTORY", action_measurement="NOT_COLLECTED")
    )
    db_session.add(source_bucket(source.id, T0 + STEP, origin="LIVE"))
    await db_session.flush()
    db_session.add_all(
        [
            metric_row(history_endpoint, T0, 100),
            metric_row(untracked, T0, 20),
            metric_row(history_endpoint, T0 + STEP, 50),
        ]
    )
    await db_session.flush()

    def measured(at):  # type: ignore[no-untyped-def]
        return {
            WafBreakdown.ACTION: _BreakdownResult(
                WafTelemetryStatus.EXACT,
                batch=WafBreakdownBatch(
                    breakdown="ACTION",
                    qradar_log_source_id=source.qradar_id,
                    window_start=at,
                    window_end=at + STEP,
                    groups=[
                        WafBreakdownGroup(
                            raw_url="/portal/login",
                            raw_method="POST",
                            raw_value="DENY",
                            request_count=30,
                        )
                    ],
                ),
            )
        }

    counts = await collector.persist_history_breakdowns(
        source, {T0: measured(T0), T0 + STEP: measured(T0 + STEP)}
    )
    await db_session.flush()
    assert counts == (2, 1)  # both starts are planned; only the HISTORY metric is a target

    history_bucket = await db_session.get(WafSourceBucket, (source.id, T0))
    live_bucket = await db_session.get(WafSourceBucket, (source.id, T0 + STEP))
    assert history_bucket is not None and live_bucket is not None
    assert history_bucket.action_measurement == WafTelemetryStatus.EXACT.value
    assert live_bucket.action_measurement == WafTelemetryStatus.NOT_COLLECTED.value

    tracked_metric = await db_session.scalar(
        select(WafEndpointMetric).where(
            WafEndpointMetric.waf_endpoint_id == history_endpoint.id,
            WafEndpointMetric.bucket_start == T0,
        )
    )
    untracked_metric = await db_session.scalar(
        select(WafEndpointMetric).where(
            WafEndpointMetric.waf_endpoint_id == untracked.id,
            WafEndpointMetric.bucket_start == T0,
        )
    )
    live_metric = await db_session.scalar(
        select(WafEndpointMetric).where(
            WafEndpointMetric.waf_endpoint_id == history_endpoint.id,
            WafEndpointMetric.bucket_start == T0 + STEP,
        )
    )
    assert tracked_metric is not None and tracked_metric.blocked_count == 30
    assert untracked_metric is not None and untracked_metric.blocked_count is None
    assert live_metric is not None and live_metric.blocked_count is None


async def test_history_step_waits_on_a_zero_search_or_time_budget(db_session) -> None:  # type: ignore[no-untyped-def]
    settings = cfg()
    provider = ScriptedWaf({T0: [("/budget", "GET", 40)]})
    _profile, source, _instance, collector_factory = await make_source(
        db_session, provider, settings
    )
    collector = collector_factory(T0 + STEP)
    provider.calls.clear()
    progress = ScopeProgress(target_start=T0, started_at=T0, floor=T0, scan_cursor=None)

    no_searches = await collector.history_volume_step(
        source,
        progress,
        boundary=T0 + STEP,
        search_budget=0,
        seconds_left=30,
        cadence_seconds=int(STEP.total_seconds()),
    )
    assert no_searches.searches == 0 and no_searches.rows_written == 0
    assert no_searches.reason == "waiting for search budget"
    assert provider.calls == []

    no_time = await collector.history_volume_step(
        source,
        progress,
        boundary=T0 + STEP,
        search_budget=10,
        seconds_left=1e-9,
        cadence_seconds=int(STEP.total_seconds()),
    )
    assert no_time.searches == 0 and no_time.rows_written == 0
    assert no_time.reason == "waiting for the history time budget"
    assert no_time.progress.through is None
    assert provider.calls == []


async def test_a_truncated_history_search_writes_nothing(db_session) -> None:  # type: ignore[no-untyped-def]
    settings = cfg(ariel_multi_bucket_max_rows=1)
    provider = ScriptedWaf(
        {
            T0: [("/one", "GET", 10), ("/two", "GET", 10)],
            T0 + STEP: [("/one", "GET", 10), ("/two", "GET", 10)],
            T0 + 2 * STEP: [("/one", "GET", 10), ("/two", "GET", 10)],
        }
    )
    _profile, source, _instance, collector_factory = await make_source(
        db_session, provider, settings
    )
    collector = collector_factory(T0 + 3 * STEP)
    progress = ScopeProgress(target_start=T0, started_at=T0, floor=T0, scan_cursor=None)

    step = await collector.history_volume_step(
        source,
        progress,
        boundary=T0 + 3 * STEP,
        search_budget=10,
        seconds_left=30,
        cadence_seconds=int(STEP.total_seconds()),
    )

    assert step.searches == 1
    assert step.progress.through is None
    assert await db_session.scalar(select(WafSourceBucket)) is None


async def test_the_floor_is_the_first_history_bucket_and_a_cut_floor_ends_partial(
    db_session,  # type: ignore[no-untyped-def]
) -> None:
    settings = cfg(history_backfill_days=7, history_backfill_max_lower_bound_share=1.0)
    # The first event is three days old, so the 24 h margin puts the floor a day
    # before the boundary and well after the asked start (seven days back).
    provider = ScriptedWaf({T0 - timedelta(hours=72): [("/old", "GET", 40)]})
    profile, source, instance, _ = await make_source(db_session, provider, settings)
    await HistoryBackfillRepository(db_session).record_decision(
        instance.id,
        answer="YES",
        days=7,
        source="UI",
        decided_by="operator",
        at=T0,
    )
    await db_session.commit()

    await run_waf_source(
        db_session,
        profile.id,
        provider_factory=lambda _instance: provider,
        settings=settings,
        clock=lambda: T0 + STEP + timedelta(seconds=5),
    )
    scope = await db_session.get(
        HistoryBackfillScope,
        (instance.id, HistoryFamily.WAF.value, str(source.qradar_id)),
    )
    assert scope is not None and scope.floor is not None
    assert scope.state == HistoryState.READING.value
    starts = (
        await db_session.scalars(
            select(WafSourceBucket.bucket_start).where(
                WafSourceBucket.log_source_id == source.id
            )
        )
    ).all()
    assert starts and min(starts) >= scope.floor
    assert scope.floor > (scope.target_start or scope.floor) + STEP

    finished = await run_waf_source(
        db_session,
        profile.id,
        provider_factory=lambda _instance: provider,
        settings=settings,
        clock=lambda: T0 + STEP + timedelta(seconds=5),
    )
    assert finished["status"] == "history_handoff"
    assert finished["history_state"] == HistoryState.PARTIAL.value
    await db_session.refresh(scope)
    assert scope.state == HistoryState.PARTIAL.value
    assert "less than asked" in (scope.reason or "")
    assert scope.anchor == T0 + STEP


async def test_switched_off_and_stopped_readings_end_partial_before_live(db_session) -> None:  # type: ignore[no-untyped-def]
    settings = cfg(history_backfill_days=7)
    provider = ScriptedWaf({T0 + 3 * STEP: [("/live-again", "GET", 40)]})
    profile, source, instance, _ = await make_source(db_session, provider, settings)
    await reading_scope(
        db_session,
        instance_id=instance.id,
        scope_key=str(source.qradar_id),
        target_start=T0,
        floor=T0,
        through=T0 + STEP,
    )
    await HistoryBackfillRepository(db_session).record_decision(
        instance.id,
        answer="YES",
        days=7,
        source="UI",
        decided_by="operator",
        at=T0,
    )
    await db_session.commit()
    off = await run_waf_source(
        db_session,
        profile.id,
        provider_factory=lambda _instance: provider,
        settings=cfg(history_backfill_days=7, history_backfill_enabled=False),
        clock=lambda: T0 + 3 * STEP + timedelta(seconds=5),
    )
    assert off["status"] == "ok" and off["intervals_collected"] == 0
    scope = await db_session.get(
        HistoryBackfillScope,
        (instance.id, HistoryFamily.WAF.value, str(source.qradar_id)),
    )
    assert scope is not None and scope.state == HistoryState.PARTIAL.value
    assert "switched off" in (scope.reason or "")
    assert scope.anchor == T0 + 3 * STEP
    watermarks = (
        await db_session.scalars(
            select(CollectionWatermark).where(
                CollectionWatermark.instance_id == instance.id,
                CollectionWatermark.collector.in_(
                    [watermark_collector(source), evaluation_collector(source)]
                ),
            )
        )
    ).all()
    assert {row.watermark_at for row in watermarks} == {T0 + 2 * STEP}
    resumed = await run_waf_source(
        db_session,
        profile.id,
        provider_factory=lambda _instance: provider,
        settings=cfg(history_backfill_days=7, history_backfill_enabled=False),
        clock=lambda: T0 + 4 * STEP + timedelta(seconds=5),
    )
    assert resumed["intervals_collected"] == 1
    first_live = await db_session.get(WafSourceBucket, (source.id, T0 + 3 * STEP))
    assert first_live is not None and first_live.origin == "LIVE"

    stopped_provider = ScriptedWaf({T0 + 2 * STEP: [("/stopped-live", "GET", 40)]})
    stopped_profile, stopped_source, stopped_instance, _ = await make_source(
        db_session, stopped_provider, settings
    )
    await reading_scope(
        db_session,
        instance_id=stopped_instance.id,
        scope_key=str(stopped_source.qradar_id),
        target_start=T0,
        floor=T0,
        through=T0 + STEP,
    )
    repository = HistoryBackfillRepository(db_session)
    await repository.record_decision(
        stopped_instance.id,
        answer="YES",
        days=7,
        source="UI",
        decided_by="operator",
        at=T0,
    )
    await repository.request_stop(
        stopped_instance.id, by="operator", at=T0 + timedelta(seconds=1)
    )
    await db_session.commit()
    stopped = await run_waf_source(
        db_session,
        stopped_profile.id,
        provider_factory=lambda _instance: stopped_provider,
        settings=settings,
        clock=lambda: T0 + 3 * STEP + timedelta(seconds=5),
    )
    assert stopped["status"] == "ok" and stopped["intervals_collected"] == 0
    stopped_scope = await db_session.get(
        HistoryBackfillScope,
        (stopped_instance.id, HistoryFamily.WAF.value, str(stopped_source.qradar_id)),
    )
    assert stopped_scope is not None
    assert stopped_scope.state == HistoryState.PARTIAL.value
    assert "stopped by operator" in (stopped_scope.reason or "")


async def test_a_provider_failure_mid_step_writes_nothing_and_retries_narrower(
    db_session,  # type: ignore[no-untyped-def]
) -> None:
    settings = cfg(history_backfill_days=7, history_backfill_max_lower_bound_share=1.0)
    provider = ScriptedWaf(
        {
            T0: [("/resume-one", "GET", 40)],
            T0 + STEP: [("/resume-two", "GET", 40)],
        }
    )
    profile, source, instance, _ = await make_source(db_session, provider, settings)
    await reading_scope(
        db_session,
        instance_id=instance.id,
        scope_key=str(source.qradar_id),
        target_start=T0,
        floor=T0,
    )
    provider.fail_keyed_volume_at = {T0}

    failed = await run_waf_source(
        db_session,
        profile.id,
        provider_factory=lambda _instance: provider,
        settings=settings,
        clock=lambda: T0 + 2 * STEP + timedelta(seconds=5),
    )
    assert failed["status"] == "history_reading"
    assert "failed" in (failed["history_reason"] or "")
    scope = await db_session.get(
        HistoryBackfillScope,
        (instance.id, HistoryFamily.WAF.value, str(source.qradar_id)),
    )
    assert scope is not None and scope.through is None
    assert scope.state == HistoryState.READING.value
    assert scope.last_error_class == "ProviderUnavailableError"
    assert scope.last_error_at == T0 + 2 * STEP + timedelta(seconds=5)
    assert await db_session.scalar(select(WafSourceBucket)) is None

    provider.fail_keyed_volume_at.clear()
    resumed = await run_waf_source(
        db_session,
        profile.id,
        provider_factory=lambda _instance: provider,
        settings=settings,
        clock=lambda: T0 + 2 * STEP + timedelta(seconds=5),
    )
    assert resumed["status"] == "history_handoff"
    assert resumed["history_state"] == HistoryState.COMPLETE.value
    await db_session.refresh(scope)
    assert scope.last_error_class is None and scope.last_error_at is None
    starts = (
        await db_session.scalars(
            select(WafSourceBucket.bucket_start).order_by(WafSourceBucket.bucket_start)
        )
    ).all()
    assert list(starts) == [T0, T0 + STEP]


async def test_a_locked_source_runs_no_history_step(db_session, db_schema) -> None:  # type: ignore[no-untyped-def]
    settings = cfg(history_backfill_days=7)
    provider = ScriptedWaf({T0: [("/locked", "GET", 40)]})
    profile, source, instance, _ = await make_source(db_session, provider, settings)
    await reading_scope(
        db_session,
        instance_id=instance.id,
        scope_key=str(source.qradar_id),
        target_start=T0,
        floor=T0,
    )
    provider.calls.clear()
    key = collector_lock_key(instance.id, f"waf_pipeline:{source.qradar_id}")
    async with holder(db_schema, settings.collection_advisory_lock_namespace, key):
        result = await run_waf_source(
            db_session,
            profile.id,
            provider_factory=lambda _instance: provider,
            settings=settings,
            clock=lambda: T0 + STEP + timedelta(seconds=5),
        )
    assert result["status"] == "locked"
    assert provider.calls == []
    scope = await db_session.get(
        HistoryBackfillScope,
        (instance.id, HistoryFamily.WAF.value, str(source.qradar_id)),
    )
    assert scope is not None and scope.state == HistoryState.READING.value


async def test_the_pipeline_hands_off_the_scope_anchor_as_the_novelty_end(
    db_session, monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    settings = novelty_settings(waf_new_endpoint_min_buckets=1)
    provider = ScriptedWaf(
        {T0: [("/pre-hand-off", "GET", 500), ("/after-hand-off", "GET", 500)]}
    )
    profile, source, instance, _ = await make_source(db_session, provider, settings)
    await reading_scope(
        db_session,
        instance_id=instance.id,
        scope_key=str(source.qradar_id),
        target_start=T0 - timedelta(days=1),
        floor=T0 - timedelta(days=1),
        through=T0 - STEP,
        state=HistoryState.COMPLETE,
        anchor=T0,
    )
    watermarks = (
        CollectionWatermark(
            instance_id=instance.id,
            collector=watermark_collector(source),
            watermark_at=T0 - STEP,
        ),
        CollectionWatermark(
            instance_id=instance.id,
            collector=evaluation_collector(source),
            watermark_at=T0 - STEP,
        ),
    )
    db_session.add_all(watermarks)
    pre = WafEndpoint(
        instance_id=instance.id,
        log_source_id=source.id,
        virtual_host=None,
        normalized_endpoint="/pre-hand-off",
        http_method="GET",
        normalizer_version=1,
        first_seen_at=T0 - STEP,
        last_seen_at=T0,
        tracked_since=T0 - STEP,
        is_active=True,
    )
    after = WafEndpoint(
        instance_id=instance.id,
        log_source_id=source.id,
        virtual_host=None,
        normalized_endpoint="/after-hand-off",
        http_method="GET",
        normalizer_version=1,
        first_seen_at=T0,
        last_seen_at=T0,
        tracked_since=T0,
        is_active=True,
    )
    db_session.add_all([pre, after])
    await db_session.flush()
    for index in range(288):
        db_session.add(
            source_bucket(source.id, T0 - STEP * (288 - index), origin="HISTORY")
        )
    await db_session.flush()
    db_session.add_all([metric_row(pre, T0 - STEP, 0), metric_row(pre, T0, 500)])
    db_session.add(metric_row(after, T0, 500))
    await db_session.commit()

    verdicts: list[tuple[str, object]] = []

    def spy(ctx):  # type: ignore[no-untyped-def]
        verdict = detect_new_endpoint_access(ctx)
        verdicts.append((ctx.extra["entity"]["normalized_endpoint"], verdict))
        return verdict

    monkeypatch.setitem(
        engine_module.WAF_DETECTOR_FUNCTIONS, WafAnomalyType.WAF_NEW_ENDPOINT_ACCESS, spy
    )
    run = await run_waf_source(
        db_session,
        profile.id,
        provider_factory=lambda _instance: provider,
        settings=settings,
        clock=lambda: T0 + STEP + timedelta(seconds=5),
    )
    assert run["intervals_collected"] == 1

    by_endpoint = dict(verdicts)
    established = by_endpoint["/pre-hand-off"]
    fresh = by_endpoint["/after-hand-off"]
    assert established.signal is DetectorSignal.HEALTHY
    assert "before the history hand-off" in established.evidence.reason
    assert (T0 - STEP).isoformat() in established.evidence.reason
    assert T0.isoformat() in established.evidence.reason
    assert fresh.signal is DetectorSignal.ANOMALOUS
    assert "established endpoint" not in fresh.evidence.reason
