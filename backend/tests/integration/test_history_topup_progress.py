"""History topup chunk progress through collect(), persistence and shared fleet readers."""

import copy
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import event, insert

from app.custom_rules.loaders import load_log_sources
from app.custom_rules.preview import replay
from app.fleet.series import build_fleet_eps_series
from app.fleet.summary import build_fleet_summary
from app.history.phase_a import TOPUP_SOURCES_KEY
from app.history.planner import ScopeProgress
from app.history.progress_codec import decode_progress, encode_progress
from app.history.source_floors import SourceFloors
from app.history.topup import CHUNKS_KEY, CUTOFFS_KEY
from app.models.enums import CustomRuleMetric, CustomRuleSubjectKind
from app.models.fleet import LogSourceClassification
from app.models.history_backfill import HistoryBackfillScope
from app.models.log_source import LogSource
from tests.integration.factories import add_metric, make_instance, make_log_source
from tests.integration.history_phase_a_support import (
    ANCHOR,
    FIRST_TICK,
    STEP,
    ScriptedMockProvider,
    history_rows,
    say_yes,
    scope_of,
    settings_for,
    tick,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def seed(session, count):
    instance = await make_instance(session)
    ids = {q: uuid.uuid4() for q in range(1, count + 1)}
    await session.execute(insert(LogSource), [
        {"id": sid, "instance_id": instance.id, "qradar_id": q, "name": f"source-{q}"}
        for q, sid in ids.items()
    ])
    # A fully read class must also stay hidden while another chunk is unread.
    await session.execute(insert(LogSourceClassification), [
        {"log_source_id": sid, "source_class": "WAF" if q <= 250 else "IPS",
         "basis": "MANUAL", "review_status": "CONFIRMED", "classified_at": FIRST_TICK}
        for q, sid in ids.items()
    ])
    await say_yes(session, instance)
    provider = ScriptedMockProvider(ids)
    settings = settings_for(history_backfill_baseline_sources_per_tick=1)
    await tick(session, instance, provider, settings, FIRST_TICK)
    scope = await scope_of(session, instance)
    start = ANCHOR - 2 * STEP
    scope.target_start = start
    scope.progress = encode_progress(
        ScopeProgress(target_start=start, started_at=scope.started_at, floor=start,
                      chunk_buckets=1, density_checked=True),
        {**SourceFloors({str(q): start - timedelta(hours=1) for q in ids}).encode(),
         TOPUP_SOURCES_KEY: list(map(str, ids))},
    )
    await session.flush()
    return instance, ids, provider, settings


async def fleet(session):
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        summary = await build_fleet_summary(session, window="1h", now=ANCHOR, bucket_seconds=300)
        assert len(statements) == 5
        series = await build_fleet_eps_series(session, window="1h", group_by="class",
                                              now=ANCHOR, bucket_seconds=300)
        assert len(statements) == 8
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return summary, series


async def class_preview(session):
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        result = await replay(
            session, metric=CustomRuleMetric.EPS, operator="GT", threshold_value=0,
            sustain_buckets=1, recovery_buckets=1, severity="HIGH",
            subject_kind=CustomRuleSubjectKind.FLEET_CLASS, source_class="WAF",
            log_source_id=None, waf_endpoint_id=None, ips_entity_id=None,
            hours=1, interval_seconds=300, now=ANCHOR,
        )
        assert len(statements) == 1
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return result


async def test_8000_topup_collect_finishes_in_seven_ticks_with_guarded_fleet(db_session):
    instance, ids, provider, settings = await seed(db_session, 8000)
    counts = []
    for index in range(7):
        report = await tick(
            db_session, instance, provider, settings, FIRST_TICK + (index + 1) * STEP,
        )
        assert report.history is not None and report.history.error is None
        counts.append(report.history.searches)
        scope = await scope_of(db_session, instance)
        decoded = decode_progress(scope.progress)
        assert decoded.progress.searches == sum(counts)
        assert scope.state == ("COMPLETE" if index == 6 else "READING")
        if index in (0, 3, 6):
            summary, series = await fleet(db_session)
            preview = await class_preview(db_session)
            waf = next(c for c in summary.classes if c.source_class == "WAF")
            if index == 0:
                assert waf.events is None and waf.mean_eps is None
                assert waf.buckets.complete == waf.buckets.complete_from_history == 0
                assert all(p.cells_complete == p.cells_from_history == 0
                           and p.eps is None for s in series.series for p in s.points)
                assert preview.measured_count == preview.fired_count == 0
                assert all(b.value is None and b.completeness is None for b in preview.buckets)
                evidence = await load_log_sources(
                    db_session, [ids[1]], after=ANCHOR - timedelta(hours=1),
                    through=ANCHOR - STEP, include_history=True,
                )
                assert len(evidence[str(ids[1])]) == 1
            else:
                assert waf.events is not None and waf.buckets.complete > 0
                assert preview.measured_count == (1 if index == 3 else 2)
                measured = [b for b in preview.buckets if b.value is not None]
                assert all(b.measurement == "EXACT" and b.completeness == "COMPLETE"
                           for b in measured)
                assert all(b.value == sum(provider.sample(q, b.bucket_start, 300).event_count
                                          for q in range(1, 251)) / (250 * 300)
                           for b in measured)
    assert counts == [10] * 6 + [4]
    rows = await history_rows(db_session, instance)
    assert len(rows) == len({(r.log_source_id, r.bucket_start) for r in rows}) == 16000
    reverse = {sid: q for q, sid in ids.items()}
    assert all(r.event_count == provider.sample(reverse[r.log_source_id], r.bucket_start, 300
                                                ).event_count for r in rows)
    assert all(r.query_provenance["topup_reading"] for r in rows)
    assert all(c["read"] == 2 for c in decoded.adapter[CHUNKS_KEY])
    assert decoded.progress.buckets_read == 2
    print(f"measured history searches/tick={counts}; history rows={len(rows)}")


@pytest.mark.parametrize("failure", ["provider", "permit"])
async def test_failure_keeps_other_chunks_and_retry_clears_marker(db_session, failure):
    instance, _, provider, settings = await seed(db_session, 2750)
    marked = len(provider.calls)
    provider.fail = lambda start, end, count: end <= ANCHOR and len(provider.calls) == marked + 4
    if failure == "permit":
        from app.services.ariel_permit import ArielPermitUnavailable
        provider.failure_type = lambda _: ArielPermitUnavailable(1, 3, 6)
    report = await tick(db_session, instance, provider, settings, FIRST_TICK + STEP)
    scope = await scope_of(db_session, instance)
    assert report.history.error is None
    assert scope.state == "READING"
    decoded = decode_progress(scope.progress)
    if failure == "provider":
        assert report.history.searches == decoded.progress.searches == 10
        assert scope.last_error_class == "ProviderError"
        assert decoded.adapter[CHUNKS_KEY][2]["error"] == "ProviderError"
        assert len(await history_rows(db_session, instance)) == 2250
    else:
        assert report.history.searches == decoded.progress.searches == 2
        assert scope.last_error_class is None
        assert len(await history_rows(db_session, instance)) == 500
    provider.fail = None
    report = await tick(db_session, instance, provider, settings, FIRST_TICK + 2 * STEP)
    assert report.history.searches == 10
    scope = await scope_of(db_session, instance)
    assert scope.last_error_class is None


async def test_old_partial_cutoff_survives_restart_and_missing_scope_fails_closed(db_session):
    instance, _, provider, settings = await seed(db_session, 2750)
    await tick(db_session, instance, provider, settings, FIRST_TICK + STEP)
    scope = await scope_of(db_session, instance)
    before = copy.deepcopy(decode_progress(scope.progress).adapter[CUTOFFS_KEY])
    # Finish by time limit, then use the operator restart request. All carried
    # floors cause some previously read sources to be skipped in the next reading.
    await tick(db_session, instance, provider, settings, FIRST_TICK + timedelta(hours=73))
    from app.repositories.history_backfill import HistoryBackfillRepository
    await HistoryBackfillRepository(db_session).request_start(
        instance.id, by="test", at=FIRST_TICK + timedelta(hours=74),
    )
    await tick(db_session, instance, provider, settings, FIRST_TICK + timedelta(hours=74))
    await tick(db_session, instance, provider, settings, FIRST_TICK + timedelta(hours=74) + STEP)
    scope = await scope_of(db_session, instance)
    assert decode_progress(scope.progress).adapter[CUTOFFS_KEY].items() >= before.items()
    summary, _ = await fleet(db_session)
    waf = next(c for c in summary.classes if c.source_class == "WAF")
    assert waf.events is None
    # Losing progress cannot make the tagged subset a fleet measurement.
    scope.progress = {"version": 3, "adapter": {}, "progress": {}}
    await db_session.flush()
    summary, _ = await fleet(db_session)
    assert next(c for c in summary.classes if c.source_class == "WAF").events is None
    assert (await class_preview(db_session)).measured_count == 0


@pytest.mark.parametrize("kind", [
    "legacy_history", "live", "missing", "instance", "family", "token", "matching", "boundary",
])
async def test_all_chunk_guard_binds_instance_family_and_reading_without_hiding_legacy(
    db_session, kind,
):
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance)
    db_session.add(LogSourceClassification(
        log_source_id=source.id, source_class="WAF", basis="MANUAL",
        review_status="CONFIRMED", classified_at=FIRST_TICK,
    ))
    tagged = kind not in ("legacy_history", "live")
    await add_metric(
        db_session, source, ANCHOR - STEP,
        collection_source="ariel_aggregate" if kind == "live" else "ariel_history",
        query_provenance={"topup_reading": "reading-token"} if tagged else {},
    )
    if kind in ("instance", "family", "token", "matching", "boundary"):
        owner = await make_instance(db_session) if kind == "instance" else instance
        db_session.add(HistoryBackfillScope(
            instance_id=owner.id, family="RULE_MATCH" if kind == "family" else "PHASE_A",
            scope_key="", state="READING",
            progress={"adapter": {CUTOFFS_KEY: {
                "other" if kind == "token" else "reading-token": (
                    ANCHOR - STEP if kind == "boundary" else ANCHOR
                ).isoformat(),
            }}},
        ))
    await db_session.flush()
    summary, series = await fleet(db_session)
    preview = await class_preview(db_session)
    measured = kind in ("legacy_history", "live", "matching")
    waf = next(c for c in summary.classes if c.source_class == "WAF")
    assert waf.events == (15000 if measured else None)
    assert waf.buckets.complete == int(measured)
    assert preview.measured_count == int(measured)
    assert any(p.eps is not None for s in series.series for p in s.points) == measured
    if measured:
        assert next(b for b in preview.buckets if b.value is not None).value == 50
