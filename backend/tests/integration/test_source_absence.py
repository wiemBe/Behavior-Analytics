"""X1: dormant ABSENT readers, switch-off SQL parity and additive schema evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.anomaly.baseline import BaselineBuilder
from app.api.routes import custom_rules as custom_routes
from app.api.routes import fleet as fleet_routes
from app.api.routes.outage_policy import _governed_counts
from app.collectors.metric_collector import MetricCollector, monitored_source_map
from app.core.config import get_settings
from app.custom_rules.loaders import load_fleet_classes
from app.fleet.series import build_fleet_eps_series
from app.fleet.summary import build_fleet_summary
from app.log_source_policy import not_absent
from app.models.custom_rule import CustomRule, CustomRuleWatermark
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    CustomRuleSubjectKind,
    OutagePolicyMode,
    OutagePolicyScope,
)
from app.models.feature_switch import FeatureSwitch
from app.models.log_source import LogSource
from app.models.log_source_pause import REASON_ABSENT, REASON_QRADAR_DISABLED, LogSourcePause
from app.models.monitoring import AnomalyEvaluationWatermark
from app.models.outage import OutageAlertPolicy
from app.repositories.anomaly import AnomalyRepository
from app.repositories.log_source import LogSourceRepository
from app.services.history_backfill import HistoryBackfillService
from app.services.source_pause_spans import close_pause, open_pause
from app.workers import tasks
from tests.integration.factories import add_metric, make_instance, make_log_source
from tests.integration.history_phase_a_support import ScriptedMockProvider, make_console, tick
from tests.integration.schema_reset import reset_public_schema
from tests.integration.test_anomaly_histogram import phase_a
from tests.integration.test_baseline_source_pauses import (
    BASE,
    BUCKET,
    NOW,
    WEEKS,
    _settings,
    _weekly,
)
from tests.integration.test_fleet_api import ADMIN, Client
from tests.integration.test_inventory_source_pauses import _sync
from tests.integration.test_metric_source_pauses import NOW as COLLECT_NOW
from tests.integration.test_metric_source_pauses import _rows_by_qid
from tests.integration.test_metric_source_pauses import _settings as collection_settings
from tests.integration.test_source_pause_consistency import _record
from tests.integration.test_source_pause_spans import (
    _alembic_config,
    _insert,
    _seed_source,
    _sync_url,
)

pytestmark = pytest.mark.integration

LEGACY_TASK_SQL = {
    "evaluation": [
        "570d6e28a48b5a681e485a02e0e9229c09a71cdf74431aca73036db1cadc1d06"
    ],
    "baseline": [
        "570d6e28a48b5a681e485a02e0e9229c09a71cdf74431aca73036db1cadc1d06"
    ],
    "monitor": [
        "38c4df779fb8ae49d0aad56b0d7c96209464b7c82d508bab8040a31ca79e3999"
    ]
}

# Filled from the unmodified f153fdf5 app in a scratch copy, using _reads below.
LEGACY_SQL = {
    "target": [
        "a0c54bc5d8f6ad072a0bb00cebdf65b0ba0c60ee8bd6a9f00a16bf663ad1c769"
    ],
    "inventory": [
        "6cbb3e8e164db12abce917438e9fe251cd9af734d73e59eec147e39db7d2fcc5"
    ],
    "zero": [
        "dc9c9a5e116c4bf9e5d3f153b9445bf46ace2acdef116ce2e4ebec278cb245ec"
    ],
    "estimate": [
        "3ded21d45fb597b06f8d1368deb033412e0adbf804b2afd8d06bb88732a2d4bd",
        "41efa902c1976a635a09c379f693b25525a2a5413e2b183d4dd924aacdd59cef",
        "d41c24c1e94d97241e04473a9ff3eb924ec932fe3ec7312ba28a96d1ead28283",
        "68cf0a37472ec0355270a75484bd55d8703d5b9716e87f31cefd05ff8ea3349b",
        "9e3b1265159c8bb39f49137d52601c8f52fd373d243ab52142e9846a8065576c",
        "94e1973fb3e131aa60fd2496c399bd1f9dc909afcf8e3778217394dfe1265c2a"
    ],
    "counts": [
        "b90c33721b8e26a19bef2d462231afb8cd23255b9af06dd89764eac73c8cd53c"
    ],
    "page_counts": [
        "5168bf22ebf7d04474a5f98ccc1fc276ed549d3ff7c72c123b4ee83788b68edb"
    ],
    "open": [
        "f0163723b7c45d899bbbda7e3527faa620f8509dd9777e10ae2ededfdad82d92"
    ],
    "anomalous": [
        "f45ca11a97f7fda1b1c6c4215e1d0322c10d6de4f86ffd79fb566fdd7b19d8aa"
    ],
    "unified": [
        "f3d43510ee03c41e0b81f8b190b3b75dd8b54481cd469f5df4e0182b105203d3",
        "824990fc2fa4c26dca800423adc3cd93e744e9cb5b1514629895d9b928db0c0f"
    ],
    "states": [
        "fe827d470a464acfadb5a07f28048819d352d7b74de7ce1830b46315b7a3390e"
    ],
    "families": [
        "f9ca0d3e1de2d1facb439648f1d18a22da2662ab1b4b2dcb2da38ed264a5a46b"
    ],
    "highest": [
        "47171d71800891d60f2e980fe56ca76cc19c7f0ed4eb0c547e26b3b549113707"
    ],
    "fleet": [
        "aaf5bc4790bd7532f47c1098cb9fa8455f9bfde304736d82e54e12660845a700",
        "2435f13b7ff06686c6e2963817caf43f5aad37f4a45aedb255e478d262193281",
        "0933978fee43b7116ffd984a686d548d31c47774dd84fbb2fdc90072229dd4d7",
        "dd8c60b331577d9fdd10da15b521cf346b648d2a2dc2f78bf493a5a0fbd42fde",
        "a74e371a369997eddd5832e3de3e0078120e7fe0dd9938bc3ca09a4b0e9cba5d"
    ],
    "series": [
        "6c40f9fa1f99537fa1fd259accb0bd5da31b73fbf4704c83743af82d85c76b1c",
        "953c487e7e95ef8957a269ebcb532e7fc908db5d4c9714313e5bd3e710aef762",
        "7f9b26a0d334cb3b1106c276f443d04245ffe1e51f8038a7653a53fe8500bd05"
    ],
    "custom": [
        "32e260a56eff207864aadb176af473caf104c26ab229246bc16049aade8a8564"
    ],
    "outage": [
        "cd3fdcbf4ebaba5d327a3656b76f87a3bd60676c5696f10804b067a7a7282873"
    ]
}


async def _pause(session, source, *, reason=REASON_ABSENT, start=COLLECT_NOW - timedelta(days=1),
                 end=None):
    session.add(LogSourcePause(
        log_source_id=source.id, reason=reason, starts_at=start, ends_at=end,
    ))
    await session.flush()


async def _world(session):
    console = await make_console(session, [1, 2, 3])
    for source in console.sources.values():
        session.add(phase_a(
            source.id, COLLECT_NOW - timedelta(hours=1), state=AnomalyState.OPEN,
            anomaly_type=AnomalyType.NO_EVENTS,
        ))
    await session.flush()
    return console


async def _reads(session, console, *, pause):
    """Every changed query family; listener removal survives failures."""
    # The legacy pins below were captured with the rule-match family off; since D-105 it is
    # on by default and its estimate adds one bulk analytics_rule count (FIX-105), so pin it.
    settings = collection_settings(pause=pause, rule_match_collection_enabled=False)
    ls = LogSourceRepository(session)
    anomalies = AnomalyRepository(session)
    now = COLLECT_NOW
    policy = OutageAlertPolicy(
        scope_kind=OutagePolicyScope.CLASS, scope_value="UNCLASSIFIED",
        mode=OutagePolicyMode.ALERT,
    )
    calls = {
        'target': lambda: monitored_source_map(session, [console.instance.id], settings),
        'inventory': lambda: MetricCollector(session, ScriptedMockProvider([1, 2, 3]),
                                            settings=settings)._log_source_inventory(
                                                console.instance.id),
        'zero': lambda: MetricCollector(session, ScriptedMockProvider([1, 2, 3]),
                                        settings=settings)._zero_filled_rows(
            console.instance, now-timedelta(minutes=5), now, reported=set(),
            id_map={q: source.id for q, source in console.sources.items()},
            provenance={}, duration_ms=0, collected_at=now, watermark_at=None,
        ),
        'estimate': lambda: HistoryBackfillService(session, settings).estimates(),
        'counts': lambda: ls.open_anomaly_counts(source_pause=pause),
        'page_counts': lambda: ls.open_anomaly_counts([console.sources[q].id for q in (1, 2)],
                                                     source_pause=pause),
        'open': lambda: ls.open_anomalies_for(console.sources[1].id, source_pause=pause),
        'anomalous': lambda: ls.anomalous_source_count(source_pause=pause),
        'unified': lambda: anomalies.list_unified(limit=50, offset=0, active_only=True,
                                                since=None, until=now, source_pause=pause),
        'states': lambda: anomalies.state_counts(source_pause=pause),
        'families': lambda: anomalies.active_family_counts(since=None, until=now,
                                                         source_pause=pause),
        'highest': lambda: anomalies.highest_deviation(limit=5, source_pause=pause),
        'fleet': lambda: build_fleet_summary(session, window='1h', now=now, bucket_seconds=300,
                                            source_pause=pause),
        'series': lambda: build_fleet_eps_series(session, window='1h', group_by='none', now=now,
                                               bucket_seconds=300, source_pause=pause),
        'custom': lambda: load_fleet_classes(session, ['UNCLASSIFIED'],
                                            after=now-timedelta(hours=1),
                                            through=now, source_pause=pause),
        'outage': lambda: _governed_counts(session, [policy], OutagePolicyMode.ALERT,
                                           source_pause=pause),
    }
    results, sql = {}, {}
    for name, call in calls.items():
        with _record(session) as statements:
            results[name] = await call()
        sql[name] = list(statements)
    return results, sql


@pytest.mark.asyncio
async def test_absent_reason_suppresses_evaluation_zero_fill_history_counts(
    db_session, db_schema, monkeypatch,
):
    console = await _world(db_session)
    # Both reported positives and zero filling must be suppressed; one source stays present.
    for q in (1, 2):
        await _pause(db_session, console.sources[q])
    db_session.add(FeatureSwitch(key='qradar_source_pause_enabled', enabled=True,
                                updated_at=COLLECT_NOW, updated_by='test'))
    await db_session.commit()
    engine = create_async_engine(db_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, 'get_sessionmaker', lambda: maker)
    monkeypatch.setattr(tasks, 'get_settings', lambda: collection_settings(pause=False))
    original = MetricCollector

    class FixedCollector(original):
        def __init__(self, session, provider, *, settings=None):
            assert settings.qradar_source_pause_enabled is True
            super().__init__(session, provider, settings=settings, clock=lambda: COLLECT_NOW)

    async def one_instance(run, instance_name=None, *, provider_settings=None):
        assert provider_settings.qradar_source_pause_enabled is True
        async with maker() as session:
            instance = await session.get(type(console.instance), console.instance.id)
            result = await run(session, instance, ScriptedMockProvider([1, 2, 3], silent=[1]))
            await session.commit()
            return {'results': [result]}

    monkeypatch.setattr(tasks, 'MetricCollector', FixedCollector)
    monkeypatch.setattr(tasks, '_for_each_instance', one_instance)
    try:
        collected = await tasks.collect_metrics()
        assert collected['results'][0]['status'] in {'ok', 'partial'}, collected
    finally:
        await engine.dispose()
    rows = await _rows_by_qid(db_session, console)
    assert rows[1] == rows[2] == []
    assert len(rows[3]) == 1 and rows[3][0].event_count > 0
    results, _ = await _reads(db_session, console, pause=True)
    kept = console.sources[3].id
    assert results['target'] == {console.instance.id: {3: kept}}
    assert results['inventory'][1] == frozenset(console.sources[q].id for q in (1, 2))
    family = next(f for f in results['estimate'][0].families if f.family == 'PHASE_A')
    assert '1 monitored sources' in ' '.join(family.assumptions)
    assert {row['log_source_id'] for row in results['zero']} == {kept}
    assert results['counts'] == {kept: 1}
    assert results['page_counts'] == {} and results['open'] == []
    assert results['anomalous'] == results['unified'][1] == 1
    assert sum(results['states'].values()) == sum(results['families'].values()) == 1
    assert len(results['highest']) == 1
    cls = next(c for c in results['fleet'].classes if c.source_class == 'UNCLASSIFIED')
    assert cls.monitored_sources == 1 and cls.buckets.expected == 12
    assert results['outage'] == {'UNCLASSIFIED': 1}
    assert results['custom']['UNCLASSIFIED']
    assert all(f.expected_sources == 1 for f in results['custom']['UNCLASSIFIED'].values())

    # Real task entry, DB switch over the environment-off base, no locks/watermarks for ABSENT.
    engine = create_async_engine(db_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, 'get_sessionmaker', lambda: maker)
    try:
        report = await tasks.evaluate_anomalies()
        assert report['sources'] == 1, report
        for q in (1, 2):
            assert await db_session.get(AnomalyEvaluationWatermark, console.sources[q].id) is None
        assert (await tasks.rebuild_baselines())['sources'] == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_all_span_reasons_excluded_by_baseline(db_session):
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance)
    starts = await _weekly(db_session, source)
    for index, reason in enumerate((REASON_QRADAR_DISABLED, REASON_ABSENT)):
        await _pause(db_session, source, reason=reason, start=starts[index],
                     end=starts[index+1]+BUCKET)
    builder = BaselineBuilder(db_session, settings=_settings(), source_pause_policy=True)
    observations, tally, _ = await builder._load_clean_observations(
        source, NOW-timedelta(days=120), NOW,
    )
    assert len(observations) == WEEKS-3
    assert tally.qradar_paused == tally.total == 3


@pytest.mark.asyncio
async def test_disabled_absent_overlaps_independent(db_session):
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, enabled=False, qradar_id=1)
    await _pause(db_session, source)
    # X3b: a sync that sees the source present closes ABSENT (state-based, whole-bucket end)
    # and, independently, reconciles QRADAR_DISABLED; the mirrored enabled flag is untouched.
    # The reconcile must not mistake the ABSENT span for an already-open disabled span.
    await _sync(db_session, instance.id, [(1, False)])
    spans = list(await db_session.scalars(select(LogSourcePause)))
    assert {s.reason for s in spans if s.ends_at is None} == {REASON_QRADAR_DISABLED}
    absent = [s for s in spans if s.reason == REASON_ABSENT]
    assert len(absent) == 1 and absent[0].ends_at is not None
    await db_session.refresh(source)
    assert source.enabled is False
    assert await db_session.scalar(select(LogSource.id).where(not_absent())) == source.id
    assert await close_pause(db_session, [source.id], reason=REASON_ABSENT,
                             observed_at=COLLECT_NOW, bucket_seconds=300) == 0

    # Overlap independence of the readers, set up directly: a sync would now close ABSENT.
    other = await make_log_source(db_session, instance, enabled=False, qradar_id=2)
    await _pause(db_session, other)
    await _pause(db_session, other, reason=REASON_QRADAR_DISABLED)
    spans = list(await db_session.scalars(
        select(LogSourcePause).where(LogSourcePause.log_source_id == other.id)))
    assert {s.reason for s in spans if s.ends_at is None} == {
        REASON_ABSENT, REASON_QRADAR_DISABLED,
    }
    assert await close_pause(db_session, [other.id], reason=REASON_QRADAR_DISABLED,
                             observed_at=COLLECT_NOW, bucket_seconds=300) == 1
    assert await db_session.scalar(
        select(LogSource.id).where(not_absent(), LogSource.id == other.id)) is None
    assert await close_pause(db_session, [other.id], reason=REASON_ABSENT,
                             observed_at=COLLECT_NOW, bucket_seconds=300) == 1
    # Closed with a future end still means present immediately.
    assert await db_session.scalar(
        select(LogSource.id).where(not_absent(), LogSource.id == other.id)) == other.id
    await open_pause(db_session, [other.id], reason=REASON_QRADAR_DISABLED,
                     starts_at=COLLECT_NOW+timedelta(hours=1), now=COLLECT_NOW+timedelta(hours=2))
    assert await db_session.scalar(
        select(LogSource.id).where(not_absent(), LogSource.id == other.id)) == other.id


@pytest.mark.asyncio
async def test_no_absent_rows_existing_behavior_unchanged(db_session):
    console = await _world(db_session)
    # A stale disabled span must not suppress an enabled source (ABSENT-only).
    await _pause(db_session, console.sources[1], reason=REASON_QRADAR_DISABLED)
    off, off_sql = await _reads(db_session, console, pause=False)
    on, on_sql = await _reads(db_session, console, pause=True)
    for name in off_sql:
        assert len(on_sql[name]) == len(off_sql[name]), name
    assert on == off
    assert on['inventory'][1] == frozenset()


@pytest.mark.asyncio
async def test_switch_off_unchanged(db_session):
    console = await _world(db_session)
    before, before_sql = await _reads(db_session, console, pause=False)
    for q in (1, 2):
        await _pause(db_session, console.sources[q])
    after, after_sql = await _reads(db_session, console, pause=False)
    assert before_sql == after_sql
    assert {name: [sha256(s.encode()).hexdigest() for s in sql]
            for name, sql in after_sql.items()} == LEGACY_SQL
    assert before == after
    assert after['inventory'][1] == frozenset()
    await tick(db_session, console.instance, ScriptedMockProvider([1, 2, 3], silent=[1]),
               collection_settings(pause=False), COLLECT_NOW)
    rows = await _rows_by_qid(db_session, console)
    assert len(rows[1]) == len(rows[2]) == len(rows[3]) == 1
    assert rows[1][0].event_count == 0 and rows[2][0].event_count > 0


@pytest.mark.asyncio
async def test_open_absence_uses_partial_unique_index(db_session):
    console = await make_console(db_session, range(1, 201))
    for q, source in console.sources.items():
        await _pause(db_session, source, end=None if q % 2 else COLLECT_NOW)
    await db_session.execute(text('ANALYZE log_source_pause'))
    statement = select(LogSource.id).where(not_absent()).compile(
        compile_kwargs={'literal_binds': True},
    )
    # Pin the correlated lookup itself: force nested loops, not sequential hash anti-join.
    # SET LOCAL is transaction-scoped and cannot leak to another test/session.
    await db_session.execute(text('SET LOCAL enable_hashjoin = off'))
    await db_session.execute(text('SET LOCAL enable_mergejoin = off'))
    plan = await db_session.scalar(text('EXPLAIN (FORMAT JSON) '+str(statement)))
    encoded = json.dumps(plan)
    print("ABSENT_EXPLAIN: " + encoded)
    assert 'uq_log_source_pause_open' in encoded, encoded
    assert '"Node Type": "Seq Scan", "Parent Relationship": "Inner"' not in encoded
    assert not any(node.get('Node Type') == 'Seq Scan' and
                   node.get('Relation Name') == 'log_source_pause'
                   for node in _plan_nodes(plan[0]['Plan'])), encoded


def _plan_nodes(node):
    yield node
    for child in node.get('Plans', []):
        yield from _plan_nodes(child)


def test_migration_absent_downgrade_deletes_only_absent_no_drift():
    url = _sync_url()
    reset_public_schema(url)
    cfg = _alembic_config(url)  # Config(), never alembic.ini/fileConfig.
    engine = create_engine(url)
    try:
        command.upgrade(cfg, 'head')
        with engine.begin() as conn:
            source = _seed_source(conn)
            _insert(conn, source, REASON_ABSENT, BASE, None)
            _insert(conn, source, REASON_QRADAR_DISABLED, BASE, None)
        with pytest.raises(IntegrityError, match='reason_known'), engine.begin() as conn:
            _insert(conn, source, 'UNKNOWN', BASE, None)
        command.downgrade(cfg, '0076')
        with engine.connect() as conn:
            assert conn.execute(text('SELECT reason FROM log_source_pause')).scalars().all() == [
                REASON_QRADAR_DISABLED,
            ]
        command.upgrade(cfg, 'head')
        command.check(cfg)
        with engine.begin() as conn:
            _insert(conn, source, REASON_ABSENT, BASE, None)
    finally:
        engine.dispose()
        reset_public_schema(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("pause", [True, False])
async def test_resolved_switch_reaches_real_tier_three_entries(db_session, db_schema, monkeypatch,
                                                             pause):
    console = await _world(db_session)
    now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)
    bucket = now.replace(minute=55, hour=9, second=0)
    for q, source in console.sources.items():
        await add_metric(db_session, source, bucket, event_count=300 if q == 3 else 600)
    for q in (1, 2):
        await _pause(db_session, console.sources[q])
    db_session.add(FeatureSwitch(key="qradar_source_pause_enabled", enabled=pause,
                                updated_at=now, updated_by="test"))
    db_session.add(OutageAlertPolicy(scope_kind="CLASS", scope_value="UNCLASSIFIED", mode="ALERT"))
    rule = CustomRule(name="absence-fleet", subject_kind=CustomRuleSubjectKind.FLEET_CLASS,
                      source_class="UNCLASSIFIED", metric="eps", operator="GT",
                      threshold_value=0.5, sustain_buckets=1, recovery_buckets=1,
                      severity="MEDIUM", enabled=True)
    db_session.add(rule)
    db_session.add(CustomRuleWatermark(subject_kind=CustomRuleSubjectKind.FLEET_CLASS,
                                      evaluated_through=bucket-timedelta(minutes=5),
                                      updated_at=now))
    await db_session.commit()
    monkeypatch.setattr(fleet_routes, "_utcnow", lambda: now)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(custom_routes, "datetime", Clock)
    client = Client(ADMIN)
    base = collection_settings(pause=not pause)
    client.http.app.dependency_overrides[get_settings] = lambda: base
    statements = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(client.engine.sync_engine, "before_cursor_execute", record)
    try:
        expected = 1 if pause else 3
        def get(url):
            response = client.get("/api/v1/"+url)
            assert response.status_code == 200, response.text
            return response.json()

        summary = get("fleet/summary?window=1h")
        cls = next(c for c in summary['classes'] if c['source_class'] == 'UNCLASSIFIED')
        assert cls['monitored_sources'] == expected
        series = get("fleet/eps-series?window=1h")
        assert all(p['sources_expected'] == expected for p in series['series'][0]['points'])
        assert get("outage-policy")[0]['governed_monitored_sources'] == expected
        draft = {"subject_kind": "FLEET_CLASS", "source_class": "UNCLASSIFIED", "metric": "eps",
                 "operator": "GT", "threshold_value": 0.5, "sustain_buckets": 1,
                 "recovery_buckets": 1, "severity": "MEDIUM"}
        response = client.post("/api/v1/custom-rules/preview?hours=1", json=draft)
        assert response.status_code == 200, response.text
        previews = [response.json(), get(f"custom-rules/{rule.id}/preview?hours=1")]
        for preview in previews:
            measured = [b for b in preview['buckets'] if b['value'] is not None]
            assert len(measured) == 1, preview
            assert measured[0]['value'] == pytest.approx(1.0 if pause else 5 / 3)
            assert measured[0]['measurement'] == 'EXACT'
        assert sum("FROM feature_switch" in s for s in statements) == 5
        assert any("FROM log_source_pause" in s for s in statements) is pause
    finally:
        event.remove(client.engine.sync_engine, "before_cursor_execute", record)
        client.http.close()
        await client.engine.dispose()

    # The actual worker entry resolves the opposite environment setting from the DB.
    engine = create_async_engine(db_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", lambda: collection_settings(pause=not pause))
    original = tasks.CustomRuleEngine
    seen = []

    class FixedEngine(original):
        def __init__(self, session, settings, **kwargs):
            assert settings.qradar_source_pause_enabled is pause
            super().__init__(session, settings, clock=lambda: now, **kwargs)

        async def _load_metrics(self, kind, keys, *, after, through):
            result = await super()._load_metrics(kind, keys, after=after, through=through)
            seen.extend(f.expected_sources for grid in result.values() for f in grid.values())
            return result

    monkeypatch.setattr(tasks, "CustomRuleEngine", FixedEngine)
    try:
        await tasks.evaluate_custom_rules()
        assert seen and set(seen) == {expected}
    finally:
        await engine.dispose()


async def _worker_reads(maker, console, monkeypatch, *, base_pause):
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", lambda: collection_settings(pause=base_pause))

    async def one_instance(run, instance_name=None, *, needs_provider=True):
        assert needs_provider is False
        async with maker() as session:
            instance = await session.get(type(console.instance), console.instance.id)
            result = await run(session, instance, None)
            await session.commit()
            return {"results": [result]}

    monkeypatch.setattr(tasks, "_for_each_instance", one_instance)
    results, sql = {}, {}
    async with maker() as recording_session:
        for name, call in (("evaluation", tasks.evaluate_anomalies),
                           ("baseline", tasks.rebuild_baselines),
                           ("monitor", tasks.detect_stale_log_source_collection)):
            with _record(recording_session) as statements:
                results[name] = await call()
            sql[name] = [s for s in statements if s.startswith("SELECT log_source.")
                         and "\nFROM log_source \nWHERE" in s
                         and "log_source.monitoring_enabled IS true" in s]
    return results, sql


@pytest.mark.asyncio
@pytest.mark.parametrize("pause", [True, False])
async def test_task_targets_and_switch_off_sql(db_session, db_schema, monkeypatch, pause):
    console = await _world(db_session)
    for source in console.sources.values():
        await _pause(db_session, source)
    db_session.add(FeatureSwitch(key="qradar_source_pause_enabled", enabled=pause,
                                updated_at=COLLECT_NOW, updated_by="test"))
    await db_session.commit()
    engine = create_async_engine(db_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        results, sql = await _worker_reads(maker, console, monkeypatch, base_pause=not pause)
        assert results['evaluation']['sources'] == (0 if pause else 3)
        assert results['baseline']['sources'] == (0 if pause else 3)
        monitor = results['monitor']['results'][0]
        assert monitor['reason'] == ('all_sources_disabled' if pause else 'awaiting_first_run')
        assert monitor['judged'] is False
        assert all(len(statements) == (2 if pause and name == 'monitor' else 1)
                   for name, statements in sql.items()), sql
        if not pause:
            assert {name: [sha256(s.encode()).hexdigest() for s in statements]
                    for name, statements in sql.items()} == LEGACY_TASK_SQL
    finally:
        await engine.dispose()
