"""Custom-rule pages on stored buckets (design §F.6, D-17).

Shadow mode writes the breach and no alert. With alerting on, one breach is
one alert: sustain opens it, a later breach refreshes it, recovery resolves
it, and a second tick of the same buckets does not page again. Statement
counts stay flat from 10 rules to 100.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import delete, select

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.fingerprint import custom_rule_breach_fingerprint
from app.alerts.routing import RoutingPolicy, RoutingRule
from app.custom_rules.alerts import (
    EVALUATOR_ACTOR,
    REASON_DELETED,
    REASON_DISABLED,
    REASON_RECOVERED,
    resolve_deleted_rule_alert,
)
from app.custom_rules.engine import CustomRuleEngine
from app.models.alert import Alert, AlertNotification
from app.models.custom_rule import CustomRule, CustomRuleBreach, CustomRuleWatermark
from app.models.enums import (
    AlertStatus,
    AnomalyState,
    ClassificationReviewStatus,
    Criticality,
    CustomRuleMetric,
    CustomRuleSubjectKind,
    NotificationChannel,
    SearchCadence,
    SearchFamily,
    Severity,
    SourceClass,
)
from app.models.fleet import LogSourceClassification
from app.models.log_source import LogSource, LogSourceMetric
from app.models.search_cadence import SearchCadenceSetting
from app.models.waf import WafEndpoint
from tests.integration.factories import add_metric, make_instance, make_log_source, utc
from tests.integration.test_custom_rules import _alert_count, _breach_count, _rule, _settings
from tests.integration.test_waf_scale import counting

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _paging(**overrides: object):
    values: dict[str, object] = {
        "custom_rules_alerting_enabled": True,
        "notify_send_recovery": True,
    }
    values.update(overrides)
    return _settings(**values)


def _policy() -> RoutingPolicy:
    return RoutingPolicy(
        rules=[
            RoutingRule(
                channel=NotificationChannel.SLACK,
                target="custom-rules",
                alert_types=frozenset({"custom_rule_breach"}),
            ),
            RoutingRule(
                channel=NotificationChannel.SLACK,
                target="anomalies-only",
                alert_types=frozenset({"anomaly"}),
            ),
        ]
    )


class _CountingEnqueuer:
    """Counts enqueue calls, then writes the rows. A swallowed duplicate still counts."""

    def __init__(self, session, settings) -> None:  # type: ignore[no-untyped-def]
        self.calls: list[str] = []
        self._inner = NotificationDispatcher(
            session, notifiers={}, policy=_policy(), settings=settings
        )

    async def enqueue(self, alert, transition):  # type: ignore[no-untyped-def]
        self.calls.append(str(transition))
        return await self._inner.enqueue(alert, transition)


class _RecordingEnqueuer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def enqueue(self, alert, transition):  # type: ignore[no-untyped-def]
        self.calls.append(str(transition))
        return []


async def _tick(session, settings, when: datetime, enqueuer=None):  # type: ignore[no-untyped-def]
    return await CustomRuleEngine(
        session,
        settings,
        enqueuer=enqueuer,
        clock=lambda when=when: when,
    ).run()


async def _alerts(session) -> list[Alert]:  # type: ignore[no-untyped-def]
    return list((await session.scalars(select(Alert).order_by(Alert.opened_at))).all())


async def _notes(session) -> list[AlertNotification]:  # type: ignore[no-untyped-def]
    return list((await session.scalars(select(AlertNotification))).all())


def _open_alert(rule: CustomRule, when: datetime) -> Alert:
    fingerprint = custom_rule_breach_fingerprint(rule.id)
    return Alert(
        dedup_key=fingerprint,
        fingerprint=fingerprint,
        title=f"{rule.name} crossed its threshold",
        severity=rule.severity,
        status=AlertStatus.OPEN,
        source_type="custom_rule",
        source_id=rule.id,
        context={"rule": rule.name},
        opened_at=when,
        first_seen_at=when,
        occurrence_count=1,
        evidence_snapshot={},
        source_anomaly_ids=[],
    )


async def test_a_sustained_breach_opens_one_alert_and_recovery_resolves_it(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=91, name="edge-fw")
    for minute, events in ((0, 400), (5, 400), (10, 450), (15, 1), (20, 1)):
        await add_metric(db_session, source, utc(h=10, mi=minute), event_count=events)
    rule = _rule(
        "sustained-volume",
        source,
        sustain_buckets=2,
        recovery_buckets=2,
        threshold_value=100.0,
    )
    db_session.add(rule)
    await db_session.flush()
    settings = _paging()
    enqueuer = _CountingEnqueuer(db_session, settings)

    seeded = await _tick(db_session, settings, utc(h=10), enqueuer)
    assert seeded.status == "seeded"
    assert seeded.alerting_mode == "alerting"
    assert await _alert_count(db_session) == 0
    assert enqueuer.calls == []

    opened = await _tick(db_session, settings, utc(h=10, mi=11), enqueuer)
    assert opened.opened == 1
    assert opened.alerting_mode == "alerting"
    alerts = await _alerts(db_session)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.status == AlertStatus.OPEN
    assert alert.occurrence_count == 1
    assert alert.severity == Severity.HIGH
    assert alert.source_type == "custom_rule"
    assert alert.source_id == rule.id
    assert alert.fingerprint == custom_rule_breach_fingerprint(rule.id)
    assert alert.fingerprint.startswith("custom_rule_breach:")
    assert alert.title == "sustained-volume crossed its threshold"
    assert alert.opened_at == utc(h=10, mi=11)
    assert alert.description == (
        "sustained-volume observed 400 events/bucket (GT 100 events/bucket)"
    )
    assert set(alert.evidence_snapshot) == {
        "observed_value",
        "measurement",
        "threshold",
        "operator",
        "first_bucket_start",
        "last_bucket_start",
        "unmeasured_buckets",
    }
    assert alert.evidence_snapshot["observed_value"] == 400.0
    assert alert.evidence_snapshot["measurement"] == "EXACT"
    assert alert.evidence_snapshot["first_bucket_start"] == utc(h=10, mi=5).isoformat()
    assert alert.context == {
        "rule": "sustained-volume",
        "subject_kind": "LOG_SOURCE",
        "subject_label": "edge-fw",
        "metric": "event_count (events/bucket)",
    }
    assert enqueuer.calls == ["OPENED"]
    notes = await _notes(db_session)
    assert [str(note.transition) for note in notes] == ["OPENED"]
    assert {note.target for note in notes} == {"custom-rules"}

    again = await _tick(db_session, settings, utc(h=10, mi=11), enqueuer)
    assert again.opened == 0
    assert again.judged_buckets == 0
    assert await _alert_count(db_session) == 1
    assert alert.occurrence_count == 1
    assert enqueuer.calls == ["OPENED"]
    assert len(await _notes(db_session)) == 1

    refreshed = await _tick(db_session, settings, utc(h=10, mi=16), enqueuer)
    assert refreshed.refreshed == 1
    assert refreshed.opened == 0
    assert alert.status == AlertStatus.OPEN
    assert alert.occurrence_count == 2
    assert alert.evidence_snapshot["observed_value"] == 450.0
    assert alert.evidence_snapshot["last_bucket_start"] == utc(h=10, mi=10).isoformat()
    assert enqueuer.calls == ["OPENED"]
    assert len(await _notes(db_session)) == 1

    resolved = await _tick(db_session, settings, utc(h=10, mi=26), enqueuer)
    assert resolved.resolved == 1
    assert alert.status == AlertStatus.RESOLVED
    assert alert.resolved_by == EVALUATOR_ACTOR
    assert alert.resolution_reason == REASON_RECOVERED
    assert alert.resolved_at == utc(h=10, mi=26)
    assert enqueuer.calls == ["OPENED", "RESOLVED"]
    notes = await _notes(db_session)
    assert {str(note.transition) for note in notes} == {"OPENED", "RESOLVED"}
    assert {note.target for note in notes} == {"custom-rules"}
    breach = (await db_session.execute(select(CustomRuleBreach))).scalar_one()
    assert breach.state == AnomalyState.RESOLVED
    assert await _alert_count(db_session) == 1


async def test_open_and_resolve_in_one_run_pages_both_transitions(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=92, name="edge-fw")
    await add_metric(db_session, source, utc(h=10), event_count=400)
    await add_metric(db_session, source, utc(h=10, mi=5), event_count=1)
    rule = _rule("spike-then-quiet", source, sustain_buckets=1, recovery_buckets=1)
    db_session.add(rule)
    await db_session.flush()
    settings = _paging()
    enqueuer = _CountingEnqueuer(db_session, settings)

    await _tick(db_session, settings, utc(h=10), enqueuer)
    report = await _tick(db_session, settings, utc(h=10, mi=11), enqueuer)

    assert report.opened == 1
    assert report.resolved == 1
    alerts = await _alerts(db_session)
    assert len(alerts) == 1
    assert alerts[0].status == AlertStatus.RESOLVED
    assert alerts[0].resolved_by == EVALUATOR_ACTOR
    assert alerts[0].resolution_reason == REASON_RECOVERED
    assert enqueuer.calls == ["OPENED", "RESOLVED"]
    assert {str(note.transition) for note in await _notes(db_session)} == {"OPENED", "RESOLVED"}


async def test_shadow_mode_writes_the_breach_and_no_alert(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=93, name="edge-fw")
    await add_metric(db_session, source, utc(h=10), event_count=400)
    rule = _rule("shadow-volume", source, sustain_buckets=1, recovery_buckets=2)
    db_session.add(rule)
    await db_session.flush()
    settings = _settings(custom_rules_alerting_enabled=False)
    enqueuer = _RecordingEnqueuer()

    await _tick(db_session, settings, utc(h=10), enqueuer)
    opened = await _tick(db_session, settings, utc(h=10, mi=6), enqueuer)

    assert opened.opened == 1
    assert opened.alerting_mode == "shadow"
    assert await _breach_count(db_session) == 1
    assert await _alert_count(db_session) == 0
    assert enqueuer.calls == []
    breach = (await db_session.execute(select(CustomRuleBreach))).scalar_one()
    assert breach.state == AnomalyState.OPEN


async def test_maintenance_does_not_open_refresh_or_resolve_a_live_page(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=94, name="paused")
    await add_metric(db_session, source, utc(h=10), event_count=400)
    rule = _rule("paused-volume", source, sustain_buckets=1, recovery_buckets=1)
    db_session.add(rule)
    await db_session.flush()
    settings = _paging()
    enqueuer = _CountingEnqueuer(db_session, settings)

    await _tick(db_session, settings, utc(h=10), enqueuer)
    await _tick(db_session, settings, utc(h=10, mi=6), enqueuer)
    alert = (await _alerts(db_session))[0]
    assert alert.status == AlertStatus.OPEN
    assert alert.occurrence_count == 1
    breach = (await db_session.execute(select(CustomRuleBreach))).scalar_one()
    breaching = rule.consecutive_breaching

    source.maintenance_mode = True
    await add_metric(db_session, source, utc(h=10, mi=5), event_count=1)
    await add_metric(db_session, source, utc(h=10, mi=10), event_count=900)
    held = await _tick(db_session, settings, utc(h=10, mi=16), enqueuer)

    assert held.opened == 0
    assert held.resolved == 0
    assert held.refreshed == 0
    assert alert.status == AlertStatus.OPEN
    assert alert.occurrence_count == 1
    assert alert.resolved_at is None
    assert breach.state == AnomalyState.OPEN
    assert rule.consecutive_breaching == breaching
    assert rule.consecutive_under == 0
    assert enqueuer.calls == ["OPENED"]


async def test_disabling_a_rule_resolves_the_page_and_keeps_the_breach(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=95, name="edge-fw")
    await add_metric(db_session, source, utc(h=10), event_count=400)
    await add_metric(db_session, source, utc(h=10, mi=5), event_count=1)
    rule = _rule("switched-off", source, sustain_buckets=1, recovery_buckets=1)
    db_session.add(rule)
    await db_session.flush()
    settings = _paging()
    enqueuer = _CountingEnqueuer(db_session, settings)

    await _tick(db_session, settings, utc(h=10), enqueuer)
    await _tick(db_session, settings, utc(h=10, mi=6), enqueuer)
    rule.enabled = False
    await db_session.flush()
    stopped = await _tick(db_session, settings, utc(h=10, mi=16), enqueuer)

    alert = (await _alerts(db_session))[0]
    breach = (await db_session.execute(select(CustomRuleBreach))).scalar_one()
    assert stopped.resolved == 0
    assert alert.status == AlertStatus.RESOLVED
    assert alert.resolution_reason == REASON_DISABLED
    assert alert.resolved_by == EVALUATOR_ACTOR
    assert breach.state == AnomalyState.OPEN
    assert rule.active_breach_id == breach.id
    assert rule.last_status == "DISABLED"
    assert rule.consecutive_breaching == 1
    assert enqueuer.calls == ["OPENED", "RESOLVED"]

    await _tick(db_session, settings, utc(h=10, mi=21), enqueuer)
    assert enqueuer.calls == ["OPENED", "RESOLVED"]
    assert len(await _notes(db_session)) == 2


async def test_shadow_mode_does_not_resolve_a_disabled_rules_page(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=96, name="edge-fw")
    await add_metric(db_session, source, utc(h=10), event_count=400)
    rule = _rule("later-shadow", source, sustain_buckets=1, recovery_buckets=1)
    db_session.add(rule)
    await db_session.flush()
    paging = _paging()
    enqueuer = _CountingEnqueuer(db_session, paging)

    await _tick(db_session, paging, utc(h=10), enqueuer)
    await _tick(db_session, paging, utc(h=10, mi=6), enqueuer)
    rule.enabled = False
    await db_session.flush()
    shadow = _settings(custom_rules_alerting_enabled=False)
    await _tick(db_session, shadow, utc(h=10, mi=16), _RecordingEnqueuer())

    alert = (await _alerts(db_session))[0]
    assert alert.status == AlertStatus.OPEN
    assert alert.resolved_at is None
    assert enqueuer.calls == ["OPENED"]
    assert rule.last_status == "DISABLED"
    breach = (await db_session.execute(select(CustomRuleBreach))).scalar_one()
    assert breach.state == AnomalyState.OPEN


async def test_a_retired_subject_resolves_the_page_with_its_reason(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=97, name="waf")
    retired_at = utc(h=9)
    endpoint = WafEndpoint(
        instance_id=instance.id,
        log_source_id=source.id,
        virtual_host=None,
        normalized_endpoint="/secret-path",
        http_method="GET",
        normalizer_version=1,
        first_seen_at=utc(h=8),
        last_seen_at=retired_at,
        tracked_since=utc(h=8),
        is_active=False,
        retired_at=retired_at,
    )
    db_session.add(endpoint)
    await db_session.flush()
    rule = CustomRule(
        name="retired-endpoint",
        subject_kind=CustomRuleSubjectKind.WAF_ENDPOINT,
        waf_endpoint_id=endpoint.id,
        metric=CustomRuleMetric.REQUEST_COUNT.value,
        operator="GT",
        threshold_value=1.0,
        sustain_buckets=1,
        recovery_buckets=1,
        severity=Severity.MEDIUM,
        enabled=True,
        consecutive_breaching=2,
        consecutive_under=0,
    )
    db_session.add(rule)
    await db_session.flush()
    db_session.add(_open_alert(rule, utc(h=9)))
    await db_session.flush()
    settings = _paging()
    enqueuer = _CountingEnqueuer(db_session, settings)

    await _tick(db_session, settings, utc(h=10, mi=16), enqueuer)

    alert = (await _alerts(db_session))[0]
    assert rule.last_status == "SUBJECT_RETIRED"
    assert alert.status == AlertStatus.RESOLVED
    assert alert.resolved_by == EVALUATOR_ACTOR
    assert alert.resolution_reason == rule.last_status_reason
    assert alert.resolution_reason is not None
    assert retired_at.isoformat() in alert.resolution_reason
    assert "/secret-path" not in alert.resolution_reason
    assert alert.resolution_reason != REASON_RECOVERED
    assert await _alert_count(db_session) == 1
    assert enqueuer.calls == ["RESOLVED"]

    await _tick(db_session, settings, utc(h=10, mi=21), enqueuer)
    assert enqueuer.calls == ["RESOLVED"]


async def test_a_deleted_rule_is_closed_by_the_orphan_sweep(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=98, name="gone")
    rule = _rule("deleted-volume", source, sustain_buckets=1, recovery_buckets=1)
    db_session.add(rule)
    await db_session.flush()
    when = utc(h=10)
    db_session.add(_open_alert(rule, when))
    other = Alert(
        dedup_key="anomaly:keep-this-page",
        fingerprint="anomaly:keep-this-page",
        title="a phase A page",
        severity=Severity.HIGH,
        status=AlertStatus.OPEN,
        source_type="log_source",
        source_id=source.id,
        context={},
        opened_at=when,
        first_seen_at=when,
        occurrence_count=1,
        evidence_snapshot={},
        source_anomaly_ids=[],
    )
    db_session.add(other)
    await db_session.flush()
    await db_session.delete(rule)
    await db_session.flush()
    settings = _paging()
    enqueuer = _CountingEnqueuer(db_session, settings)

    await _tick(db_session, settings, utc(h=10, mi=16), enqueuer)

    alerts = {row.fingerprint: row for row in await _alerts(db_session)}
    closed = alerts[custom_rule_breach_fingerprint(rule.id)]
    assert closed.status == AlertStatus.RESOLVED
    assert closed.resolution_reason == REASON_DELETED
    assert closed.resolved_by == EVALUATOR_ACTOR
    assert alerts["anomaly:keep-this-page"].status == AlertStatus.OPEN
    assert enqueuer.calls == ["RESOLVED"]


async def test_deleting_through_the_helper_uses_the_principal(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=99, name="kept")
    rule = _rule("removed-by-ada", source, sustain_buckets=1, recovery_buckets=1)
    db_session.add(rule)
    await db_session.flush()
    db_session.add(_open_alert(rule, utc(h=10)))
    await db_session.flush()
    settings = _paging()
    enqueuer = _CountingEnqueuer(db_session, settings)

    result = await resolve_deleted_rule_alert(
        db_session,
        rule.id,
        actor="ada",
        enqueuer=enqueuer,
        clock=lambda: utc(h=10, mi=30),
    )
    assert result is not None
    assert result.transition is not None
    alert = (await _alerts(db_session))[0]
    assert alert.status == AlertStatus.RESOLVED
    assert alert.resolved_by == "ada"
    assert alert.resolution_reason == REASON_DELETED
    assert alert.resolved_at == utc(h=10, mi=30)
    assert enqueuer.calls == ["RESOLVED"]

    again = await resolve_deleted_rule_alert(
        db_session, rule.id, actor="ada", enqueuer=enqueuer
    )
    assert again is None
    assert enqueuer.calls == ["RESOLVED"]

    await _tick(db_session, settings, utc(h=10, mi=16), enqueuer)
    assert enqueuer.calls == ["RESOLVED"]
    assert len(await _notes(db_session)) == 1


async def test_a_lower_bound_opens_and_a_later_one_below_the_line_does_not_resolve(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    hot = await make_log_source(db_session, instance, qradar_id=101, name="hot")
    cold = await make_log_source(db_session, instance, qradar_id=102, name="cold")
    when = utc()
    for source in (hot, cold):
        db_session.add(
            LogSourceClassification(
                log_source_id=source.id,
                source_class=SourceClass.LINUX.value,
                basis="NAME_REGEX",
                conflicts=[],
                classified_at=when,
                review_status=ClassificationReviewStatus.REVIEWED.value,
            )
        )
    await add_metric(
        db_session, hot, utc(h=10, mi=5), event_count=300, bucket_seconds=300, average_eps=1.0
    )
    above = CustomRule(
        name="linux-eps-above",
        subject_kind=CustomRuleSubjectKind.FLEET_CLASS,
        source_class=SourceClass.LINUX.value,
        metric=CustomRuleMetric.EPS.value,
        operator="GT",
        threshold_value=0.5,
        sustain_buckets=1,
        recovery_buckets=1,
        severity=Severity.MEDIUM,
        enabled=True,
        consecutive_breaching=0,
        consecutive_under=0,
    )
    db_session.add(above)
    await db_session.flush()
    settings = _paging()
    enqueuer = _CountingEnqueuer(db_session, settings)

    await _tick(db_session, settings, utc(h=10, mi=7), enqueuer)
    opened = await _tick(db_session, settings, utc(h=10, mi=12), enqueuer)
    alert = (await _alerts(db_session))[0]
    assert opened.opened == 1
    assert alert.status == AlertStatus.OPEN
    assert alert.evidence_snapshot["measurement"] == "LOWER_BOUND"
    assert alert.description is not None
    assert "at least" in alert.description
    assert "events/s" in alert.description
    assert enqueuer.calls == ["OPENED"]

    await add_metric(
        db_session, hot, utc(h=10, mi=10), event_count=30, bucket_seconds=300, average_eps=0.1
    )
    held = await _tick(db_session, settings, utc(h=10, mi=16), enqueuer)
    assert held.resolved == 0
    assert alert.status == AlertStatus.OPEN
    assert alert.resolved_at is None
    assert enqueuer.calls == ["OPENED"]
    breach = (await db_session.execute(select(CustomRuleBreach))).scalar_one()
    assert breach.state == AnomalyState.OPEN
    assert breach.worst_measurement == "LOWER_BOUND"


async def test_a_stopped_collection_keeps_the_open_page(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=103, name="silent")
    db_session.add(
        SearchCadenceSetting(
            family=SearchFamily.PHASE_A_VOLUME.value,
            cadence_seconds=int(SearchCadence.FIVE_MINUTES),
            updated_at=utc(h=9),
        )
    )
    await add_metric(db_session, source, utc(h=10), event_count=400)
    rule = _rule("stale-volume", source, sustain_buckets=1, recovery_buckets=2)
    db_session.add(rule)
    await db_session.flush()
    settings = _paging(search_cadence_settle_seconds=0)
    enqueuer = _CountingEnqueuer(db_session, settings)

    await _tick(db_session, settings, utc(h=10), enqueuer)
    await _tick(db_session, settings, utc(h=10, mi=6), enqueuer)
    stale = await _tick(db_session, settings, utc(h=10, mi=21), enqueuer)

    alert = (await _alerts(db_session))[0]
    assert stale.resolved == 0
    assert rule.last_status == "STALE"
    assert alert.status == AlertStatus.OPEN
    assert alert.resolved_at is None
    assert enqueuer.calls == ["OPENED"]
    breach = (await db_session.execute(select(CustomRuleBreach))).scalar_one()
    assert breach.state == AnomalyState.OPEN


def _alert_statements(statements: list[str]) -> int:
    return sum(
        "alert" in statement.lower() and "custom_rule" not in statement.lower()
        for statement in statements
    )


async def test_alerting_statement_count_is_the_same_for_ten_and_a_hundred_rules(
    db_session,
) -> None:
    """No breach opens. The reconcile lookup is one statement at both sizes."""
    instance = await make_instance(db_session)

    async def plant(count: int) -> list[str]:
        await db_session.execute(delete(AlertNotification))
        await db_session.execute(delete(Alert))
        await db_session.execute(delete(CustomRule))
        await db_session.execute(delete(CustomRuleWatermark))
        await db_session.flush()
        sources = [
            LogSource(
                instance_id=instance.id,
                qradar_id=40_000 + count * 1000 + index,
                name=f"quiet-{count}-{index}",
                criticality=Criticality.MEDIUM,
            )
            for index in range(count)
        ]
        db_session.add_all(sources)
        await db_session.flush()
        start = utc(h=10, mi=5)
        db_session.add_all(
            [
                LogSourceMetric(
                    log_source_id=source.id,
                    bucket_start=start,
                    event_count=10,
                    average_eps=0.03,
                )
                for source in sources
            ]
        )
        db_session.add_all(
            [
                _rule(
                    f"quiet-rule-{count}-{index}",
                    source,
                    threshold_value=1_000_000.0,
                    sustain_buckets=1,
                    recovery_buckets=1,
                )
                for index, source in enumerate(sources)
            ]
        )
        db_session.add(
            CustomRuleWatermark(
                subject_kind=CustomRuleSubjectKind.LOG_SOURCE,
                evaluated_through=utc(h=10),
                updated_at=utc(h=10),
            )
        )
        await db_session.flush()
        with counting(db_session) as statements:
            report = await _tick(db_session, _paging(), utc(h=10, mi=12))
        assert report.opened == 0
        assert report.resolved == 0
        assert report.judged_buckets == 1
        assert report.alerting_mode == "alerting"
        assert await _alert_count(db_session) == 0
        return list(statements)

    small = await plant(10)
    large = await plant(100)
    assert len(small) == len(large)
    assert sum("log_source_metric" in statement for statement in small) == 1
    assert sum("log_source_metric" in statement for statement in large) == 1
    assert _alert_statements(small) == _alert_statements(large)
    assert _alert_statements(large) <= 4


async def test_refreshing_open_alerts_is_constant_in_the_rule_count(db_session) -> None:
    """The counted tick only refreshes. New-alert inserts stay outside that window."""
    instance = await make_instance(db_session)

    async def plant(count: int) -> tuple[list[str], int]:
        await db_session.execute(delete(AlertNotification))
        await db_session.execute(delete(Alert))
        await db_session.execute(delete(CustomRule))
        await db_session.execute(delete(CustomRuleWatermark))
        await db_session.flush()
        sources = [
            LogSource(
                instance_id=instance.id,
                qradar_id=60_000 + count * 1000 + index,
                name=f"hot-{count}-{index}",
                criticality=Criticality.MEDIUM,
            )
            for index in range(count)
        ]
        db_session.add_all(sources)
        await db_session.flush()
        db_session.add_all(
            [
                LogSourceMetric(
                    log_source_id=source.id,
                    bucket_start=bucket,
                    event_count=400,
                    average_eps=1.0,
                )
                for source in sources
                for bucket in (utc(h=10, mi=5), utc(h=10, mi=10))
            ]
        )
        rules = [
            _rule(
                f"hot-rule-{count}-{index}",
                source,
                threshold_value=100.0,
                sustain_buckets=1,
                recovery_buckets=2,
            )
            for index, source in enumerate(sources)
        ]
        db_session.add_all(rules)
        db_session.add(
            CustomRuleWatermark(
                subject_kind=CustomRuleSubjectKind.LOG_SOURCE,
                evaluated_through=utc(h=10),
                updated_at=utc(h=10),
            )
        )
        await db_session.flush()
        settings = _paging()
        opened = await _tick(db_session, settings, utc(h=10, mi=12))
        assert opened.opened == count
        assert await _alert_count(db_session) == count
        with counting(db_session) as statements:
            refreshed = await _tick(db_session, settings, utc(h=10, mi=16))
        assert refreshed.opened == 0
        assert refreshed.refreshed == count
        assert refreshed.resolved == 0
        rows = await _alerts(db_session)
        assert len(rows) == count
        assert {row.occurrence_count for row in rows} == {2}
        assert {row.status for row in rows} == {AlertStatus.OPEN}
        return list(statements), _alert_statements(statements)

    small, small_alerts = await plant(10)
    large, large_alerts = await plant(100)
    assert len(small) == len(large)
    assert small_alerts == large_alerts
    assert large_alerts <= 8
