"""SOC-6c / SOC-6f: the rule match engine's cost, lifecycle, alerts and suppression.

DB-gated. Statement counts are taken around ``evaluate`` only, after the fixture
has been flushed, so the 100-rule run and the 5,000-rule run issue the same
statements. A rule here is COMMON when the test wants every detector to stop
at ``not_exact`` (a state row, no incident) and EVENT when it wants a verdict.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from app.alerts.fingerprint import rule_anomaly_fingerprint
from app.core.config import Settings
from app.models.alert import Alert
from app.models.alert_policy import AlertPolicy
from app.models.alert_suppression import AlertSuppression, AlertSuppressionHit
from app.models.enums import AlertPolicyMode, AlertPolicyScope, AnomalyFamily
from app.models.monitoring import CollectionWatermark
from app.models.rule import AnalyticsRule, RuleMatchMetric, RuleMatchWindow
from app.models.rule_anomaly import RuleAnomaly, RuleAnomalyTransition, RuleDetectorState
from app.rules.engine import ACTOR, RuleMatchEngine
from tests.integration.factories import make_instance
from tests.integration.test_waf_scale import counting

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

WIDTH = 3600
COLLECTOR = "rule_inventory"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "encryption_key": "x" * 44,
        "rule_match_collection_enabled": True,
        "rule_alerting_enabled": False,
        "rule_match_settle_lag_seconds": 0,
        "rule_match_window_seconds": WIDTH,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _marked(statements: list[str], marker: str) -> int:
    return sum(marker in statement for statement in statements)


def _savepoints(statements: list[str]) -> tuple[int, int]:
    saves = releases = 0
    for statement in statements:
        upper = statement.upper()
        if "SAVEPOINT" not in upper:
            continue
        if "RELEASE" in upper:
            releases += 1
        else:
            saves += 1
    return saves, releases


async def _rule(
    session,
    instance,
    *,
    qradar_id: int,
    name: str,
    rule_type: str = "EVENT",
    enabled: bool = True,
    born: datetime | None = None,
) -> AnalyticsRule:
    row = AnalyticsRule(
        instance_id=instance.id,
        qradar_id=qradar_id,
        name=name,
        rule_type=rule_type,
        enabled=enabled,
        first_seen_at=born,
        qradar_created_at=born,
    )
    session.add(row)
    await session.flush()
    return row


def _window(instance, start: datetime, *, status: str = "COMPLETE") -> RuleMatchWindow:
    return RuleMatchWindow(
        instance_id=instance.id,
        window_start=start,
        window_seconds=WIDTH,
        origin="LIVE",
        status=status,
        rows_returned=0,
        row_cap=5000,
        unknown_ids=0,
        query_version="v1",
        collected_at=start,
        attempts=1,
    )


def _metric(rule: AnalyticsRule, start: datetime, events: int) -> RuleMatchMetric:
    return RuleMatchMetric(
        rule_id=rule.id,
        bucket_start=start,
        bucket_seconds=WIDTH,
        match_records=events,
        matched_events=events,
    )


async def _watermark(session, instance, at: datetime) -> None:
    session.add(
        CollectionWatermark(
            instance_id=instance.id,
            collector=COLLECTOR,
            watermark_at=at,
        )
    )
    await session.flush()


async def _count(session, model) -> int:  # type: ignore[no-untyped-def]
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


def _engine(session, clock: datetime, **overrides: object) -> RuleMatchEngine:
    return RuleMatchEngine(session, settings=_settings(**overrides), clock=lambda: clock)


async def _silence_fixture(session):  # type: ignore[no-untyped-def]
    """168 hourly tens, then three proven zeros. The third zero opens silence."""
    instance = await make_instance(session)
    judged = datetime(2026, 6, 8, 12, tzinfo=UTC)
    born = judged - timedelta(days=30)
    rule = await _rule(session, instance, qradar_id=7, name="nightly-auth", born=born)
    baseline = judged - timedelta(days=7)
    windows = [_window(instance, baseline + timedelta(hours=hour)) for hour in range(168)]
    metrics = [
        _metric(rule, baseline + timedelta(hours=hour), 10) for hour in range(168)
    ]
    starts = [judged + timedelta(hours=step) for step in range(3)]
    windows.extend(_window(instance, start) for start in starts)
    session.add_all([*windows, *metrics])
    clock = judged + timedelta(hours=4)
    await _watermark(session, instance, clock)
    await session.flush()
    return instance, rule, starts, clock


async def _open_incident(
    session,
    rule: AnalyticsRule,
    detector: str,
    *,
    start: datetime,
) -> RuleAnomaly:
    incident = RuleAnomaly(
        rule_id=rule.id,
        qradar_id=rule.qradar_id,
        detector_type=detector,
        fingerprint=f"stored:{detector}",
        severity="MEDIUM",
        state="OPEN",
        detected_at=start,
        opened_at=start,
        anomaly_start=start,
        consecutive_buckets=3,
    )
    session.add(incident)
    await session.flush()
    session.add(
        RuleDetectorState(
            rule_id=rule.id,
            detector_type=detector,
            state="OPEN",
            consecutive_anomalous=3,
            consecutive_healthy=0,
            last_interval_start=start,
            active_anomaly_id=incident.id,
            open_anomaly_id=incident.id,
        )
    )
    await session.flush()
    return incident


def _policy(instance, rule: AnalyticsRule, detector: str, mode: str) -> AlertPolicy:
    return AlertPolicy(
        instance_id=instance.id,
        scope_kind=AlertPolicyScope.SOURCE.value,
        scope_value=str(rule.id),
        family=AnomalyFamily.RULE.value,
        detector_type=detector,
        mode=mode,
        updated_by="test",
    )


def _suppression(rule: AnalyticsRule, detector: str, clock: datetime) -> AlertSuppression:
    return AlertSuppression(
        family=AnomalyFamily.RULE.value,
        subject_id=rule.id,
        detector_type=detector,
        kind="ANALYST",
        reason="hold this rule",
        owner="shift-lead",
        starts_at=clock - timedelta(hours=1),
        ends_at=clock + timedelta(days=1),
        created_by="shift-lead",
    )


async def test_collection_off_and_an_empty_probe_issue_nothing(db_session) -> None:
    instance = await make_instance(db_session)
    clock = datetime(2026, 6, 8, 16, tzinfo=UTC)
    off = RuleMatchEngine(
        db_session,
        settings=_settings(rule_match_collection_enabled=False),
        clock=lambda: clock,
    )
    with counting(db_session) as statements:
        report = await off.evaluate(instance, window_starts=[clock])
    assert report.skipped == "collection_disabled"
    assert statements == []

    empty = _engine(db_session, clock)
    with counting(db_session) as probed:
        missed = await empty.evaluate(
            instance, written_since=clock + timedelta(days=1)
        )
    assert missed.skipped == "no_window"
    assert _marked(probed, "-- rule-engine probe") == 1
    assert _marked(probed, "-- rule-engine silence") == 0


async def test_statement_count_matches_at_100_and_5000_rules(db_session) -> None:
    """Six windows are six verdicts against one loaded series, at either size.

    COMMON rules stop at ``not_exact``, so both runs write states and no incident.
    Alerting is off, so neither run touches the alert, policy or suppression tables.
    """
    base = datetime(2026, 6, 8, 0, tzinfo=UTC)
    starts = [base + timedelta(hours=hour) for hour in range(6)]
    clock = base + timedelta(hours=8)

    async def prepare(count: int) -> tuple[object, list[str]]:
        instance = await make_instance(db_session)
        await db_session.execute(
            text(
                """
                INSERT INTO analytics_rule (
                    id, instance_id, qradar_id, name, rule_type, enabled,
                    is_building_block, categories, response_actions, mitre_techniques,
                    health_monitoring_enabled, event_contribution_count,
                    offense_contribution_count, health_status
                )
                SELECT
                    gen_random_uuid(), :instance_id, g, 'common-' || g, 'COMMON',
                    true, false, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                    true, 0, 0, 'UNKNOWN'
                FROM generate_series(1, :count) AS g
                """
            ),
            {"instance_id": instance.id, "count": count},
        )
        db_session.add_all(_window(instance, start) for start in starts)
        await db_session.flush()
        db_session.expire_all()
        await db_session.refresh(instance)
        engine = _engine(db_session, clock)
        with counting(db_session) as statements:
            report = await engine.evaluate(instance, window_starts=starts)
        assert report.windows_judged == 6
        assert report.rules_seen == count
        assert report.incidents_opened == 0
        assert _marked(statements, "-- rule-engine probe") == 0
        assert _marked(statements, "-- rule-engine silence") == 1
        assert _marked(statements, "-- rule-engine storm") == 1
        # SOC-13h: storm exclusions sit inside the existing storm statement.
        # Silence exclusions are one extra SELECT for every rule, so both sizes
        # gain the same one read.
        storm_sql = next(sql for sql in statements if "-- rule-engine storm" in sql)
        assert "baseline_training_exclusion" in storm_sql
        silence_reads = [
            sql
            for sql in statements
            if "baseline_training_exclusion" in sql and "-- rule-engine storm" not in sql
        ]
        assert len(silence_reads) == 1
        assert _marked(statements, "-- rule-engine state-upsert") == 1
        assert _marked(statements, "-- rule-engine anomaly-upsert") == 0
        blob = "\n".join(statements).lower()
        assert "alert_policy" not in blob
        assert "alert_suppression" not in blob
        assert "insert into alert" not in blob
        return instance, statements

    _, small = await prepare(100)
    _, large = await prepare(5000)
    assert len(small) == len(large)
    assert await _count(db_session, RuleAnomaly) == 0


async def test_switch_off_records_the_silence_and_opens_no_alert(db_session) -> None:
    instance, _rule_row, starts, clock = await _silence_fixture(db_session)
    engine = _engine(db_session, clock, rule_alerting_enabled=False)
    with counting(db_session) as statements:
        report = await engine.evaluate(instance, window_starts=starts)
    assert report.incidents_opened == 1
    incident = await db_session.scalar(select(RuleAnomaly))
    assert incident is not None
    assert incident.state == "OPEN"
    assert incident.detector_type == "RULE_SILENCE"
    assert incident.suppressed is False
    assert await _count(db_session, Alert) == 0
    blob = "\n".join(statements).lower()
    assert "alert_policy" not in blob
    assert "alert_suppression" not in blob
    assert "insert into alert" not in blob


async def test_record_only_opens_no_alert_and_writes_no_hit(db_session) -> None:
    instance, rule, starts, clock = await _silence_fixture(db_session)
    db_session.add(_policy(instance, rule, "RULE_SILENCE", AlertPolicyMode.RECORD_ONLY.value))
    db_session.add(_suppression(rule, "RULE_SILENCE", clock))
    await db_session.flush()
    report = await _engine(
        db_session, clock, rule_alerting_enabled=True
    ).evaluate(instance, window_starts=starts)
    assert report.incidents_opened == 1
    assert await _count(db_session, Alert) == 0
    assert await _count(db_session, AlertSuppressionHit) == 0
    incident = await db_session.scalar(select(RuleAnomaly))
    assert incident is not None and incident.state == "OPEN"


async def test_an_alert_uses_the_rule_fingerprint_and_one_savepoint(db_session) -> None:
    instance, rule, starts, clock = await _silence_fixture(db_session)
    instance_id, rule_id = instance.id, rule.id
    engine = _engine(db_session, clock, rule_alerting_enabled=True)
    with counting(db_session) as statements:
        await engine.evaluate(instance, window_starts=starts)
    alerts = list((await db_session.scalars(select(Alert))).all())
    assert len(alerts) == 1
    alert = alerts[0]
    expected = rule_anomaly_fingerprint(instance_id, rule_id, "RULE_SILENCE")
    assert alert.fingerprint == expected
    assert alert.dedup_key == expected
    assert alert.source_type == "analytics_rule"
    assert alert.source_id == rule_id
    assert "nightly-auth" in alert.title
    assert _savepoints(statements) == (1, 1)
    assert await _count(db_session, AlertSuppressionHit) == 0


async def test_a_silence_suppression_holds_for_the_window(db_session) -> None:
    instance, rule, starts, clock = await _silence_fixture(db_session)
    db_session.add(_suppression(rule, "RULE_SILENCE", clock))
    await db_session.flush()
    report = await _engine(
        db_session, clock, rule_alerting_enabled=True
    ).evaluate(instance, window_starts=starts)
    assert report.incidents_opened == 1
    assert await _count(db_session, Alert) == 0
    assert await _count(db_session, AlertSuppressionHit) == 1
    incident = await db_session.scalar(select(RuleAnomaly))
    assert incident is not None
    assert incident.state == "OPEN"
    assert incident.suppressed is False


async def test_insufficient_data_does_not_resolve_an_open_incident(db_session) -> None:
    instance = await make_instance(db_session)
    start = datetime(2026, 6, 8, 12, tzinfo=UTC)
    born = start - timedelta(days=30)
    rule = await _rule(session=db_session, instance=instance, qradar_id=8, name="held", born=born)
    previous = start - timedelta(hours=1)
    incident = await _open_incident(db_session, rule, "RULE_SILENCE", start=previous)
    db_session.add(_window(instance, start, status="FAILED"))
    clock = start + timedelta(hours=2)
    await _watermark(db_session, instance, clock)
    await db_session.flush()
    report = await _engine(db_session, clock).evaluate(instance, window_starts=[start])
    assert report.incidents_resolved == 0
    await db_session.refresh(incident)
    assert incident.state == "OPEN"
    assert incident.resolved_at is None
    resolved = await _count_resolved(db_session)
    assert resolved == 0


async def _count_resolved(session) -> int:  # type: ignore[no-untyped-def]
    return int(
        await session.scalar(
            select(func.count())
            .select_from(RuleAnomalyTransition)
            .where(RuleAnomalyTransition.to_state == "RESOLVED")
        )
        or 0
    )


async def test_a_disabled_rule_resolves_both_detectors_in_one_write(db_session) -> None:
    instance = await make_instance(db_session)
    stamp = datetime(2026, 11, 1, 15, 30, tzinfo=UTC)
    start = datetime(2026, 6, 8, 12, tzinfo=UTC)
    rule = await _rule(
        db_session, instance, qradar_id=9, name="retired", enabled=False, born=start
    )
    await _open_incident(db_session, rule, "RULE_SILENCE", start=start)
    await _open_incident(db_session, rule, "RULE_STORM", start=start)
    db_session.add(_window(instance, start))
    await _watermark(db_session, instance, stamp)
    clock = start + timedelta(hours=2)
    with counting(db_session) as statements:
        report = await _engine(db_session, clock).evaluate(instance, window_starts=[start])
    assert report.incidents_resolved == 2
    assert _marked(statements, "-- rule-engine anomaly-upsert") == 1
    assert _marked(statements, "-- rule-engine transition-insert") == 1
    incidents = list((await db_session.scalars(select(RuleAnomaly))).all())
    assert len(incidents) == 2
    assert {row.state for row in incidents} == {"RESOLVED"}
    transitions = list((await db_session.scalars(select(RuleAnomalyTransition))).all())
    assert len(transitions) == 2
    assert {row.actor for row in transitions} == {ACTOR}
    assert all("2026-11-01 15:30" in (row.reason or "") for row in transitions)


def _monday(moment: datetime) -> datetime:
    moment = moment.replace(minute=0, second=0, microsecond=0)
    return moment - timedelta(days=moment.weekday())


async def _storm_fixture(session, *, observed: int, baseline: int | None):  # type: ignore[no-untyped-def]
    """Two same-slot Mondays to judge, and eight earlier Mondays as the cell.

    ``baseline`` None stores no metric on the sample windows, so the cell median
    is the proven zero and the ratio stays null.
    """
    instance = await make_instance(session)
    newer = _monday(datetime(2026, 9, 1, 10, tzinfo=UTC))
    older = newer - timedelta(days=7)
    samples = [newer - timedelta(days=7 * week) for week in range(2, 10)]
    born = newer - timedelta(days=200)
    rule = await _rule(session, instance, qradar_id=11, name="burst-rule", born=born)
    starts = [*samples, older, newer]
    session.add_all(_window(instance, start) for start in starts)
    metrics = [_metric(rule, older, observed), _metric(rule, newer, observed)]
    if baseline is not None:
        metrics.extend(_metric(rule, start, baseline) for start in samples)
    session.add_all(metrics)
    clock = newer + timedelta(hours=2)
    await _watermark(session, instance, clock)
    await session.flush()
    return instance, rule, [older, newer], clock


@pytest.mark.parametrize(
    ("observed", "baseline", "alerts", "hits"),
    [
        (100, 10, 1, 0),
        (99, 10, 0, 1),
        (100, None, 0, 1),
    ],
)
async def test_a_storm_suppression_breaks_through_only_at_ten(
    db_session, observed: int, baseline: int | None, alerts: int, hits: int
) -> None:
    instance, rule, starts, clock = await _storm_fixture(
        db_session, observed=observed, baseline=baseline
    )
    db_session.add(_suppression(rule, "RULE_STORM", clock))
    await db_session.flush()
    report = await _engine(
        db_session, clock, rule_alerting_enabled=True
    ).evaluate(instance, window_starts=starts)
    assert report.incidents_opened == 1
    assert await _count(db_session, Alert) == alerts
    assert await _count(db_session, AlertSuppressionHit) == hits
    incident = await db_session.scalar(
        select(RuleAnomaly).where(RuleAnomaly.detector_type == "RULE_STORM")
    )
    assert incident is not None
    assert incident.state == "OPEN"
    assert incident.suppressed is False


async def test_collect_rule_matches_runs_the_engine_without_changing_its_result(
    db_session, monkeypatch
) -> None:
    """The operator path: the task collects, then the real engine judges that write."""
    from app.workers import tasks

    instance = await make_instance(db_session)
    instance_name = instance.name
    settings = _settings(rule_match_collection_enabled=True, rule_alerting_enabled=False)

    @asynccontextmanager
    async def _same():
        yield db_session

    class _Provider:
        async def aclose(self) -> None:
            return None

    class _Collector:
        def __init__(self, session, provider) -> None:  # type: ignore[no-untyped-def]
            self.session = session

        async def collect(self, collected):  # type: ignore[no-untyped-def]
            start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
            start = start - timedelta(hours=3)
            rule = AnalyticsRule(
                instance_id=collected.id,
                qradar_id=70,
                name="task-rule",
                rule_type="EVENT",
                enabled=True,
                first_seen_at=start - timedelta(days=30),
                qradar_created_at=start - timedelta(days=30),
            )
            self.session.add(rule)
            self.session.add(
                RuleMatchWindow(
                    instance_id=collected.id,
                    window_start=start,
                    window_seconds=WIDTH,
                    origin="LIVE",
                    status="COMPLETE",
                    rows_returned=0,
                    row_cap=5000,
                    unknown_ids=0,
                    query_version="v1",
                    collected_at=datetime.now(UTC) + timedelta(seconds=5),
                    attempts=1,
                )
            )
            await self.session.flush()
            return SimpleNamespace(
                skipped_locked=False,
                unsupported=False,
                partial_failure=False,
                windows_attempted=1,
                windows_complete=1,
                windows_truncated=0,
                windows_failed=0,
                searches_sent=1,
                splits=0,
                rows_written=0,
                unknown_ids=0,
                duration_ms=1,
                error=None,
            )

    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: (lambda: _same()))
    monkeypatch.setattr(tasks, "build_provider_for_instance", lambda *a, **k: _Provider())
    monkeypatch.setattr(tasks, "RuleMatchCollector", _Collector)

    result = await tasks.collect_rule_matches()
    assert result["instances"] == 1
    item = result["results"][0]
    assert item["instance"] == instance_name
    assert item["status"] == "ok"
    assert item["windows_complete"] == 1
    assert set(item) == {
        "instance",
        "status",
        "windows_attempted",
        "windows_complete",
        "windows_truncated",
        "windows_failed",
        "searches_sent",
        "splits",
        "rows_written",
        "unknown_ids",
        "duration_ms",
        "error",
    }
    assert await _count(db_session, RuleDetectorState) >= 1
