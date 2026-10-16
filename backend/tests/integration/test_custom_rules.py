"""Custom-rule evaluation against stored buckets (design §F.4, §F.5, §F.10, §F.14).

The beat task's disabled no-op is pinned in the unit suite: it must return
before a session exists, and this fixture always opens one. Here the engine
runs on a real schema.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

from app.core.config import Settings, get_settings
from app.custom_rules.alerts import CustomRuleAlertEvent
from app.custom_rules.engine import CustomRuleEngine
from app.custom_rules.retention import CustomRuleRetention
from app.models.alert import Alert
from app.models.custom_rule import CustomRule, CustomRuleBreach, CustomRuleWatermark
from app.models.enums import (
    AnomalyState,
    ClassificationReviewStatus,
    Criticality,
    CustomRuleMetric,
    CustomRuleSubjectKind,
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
from tests.integration.test_waf_scale import counting

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _RecordingSink:
    """Stands in for the F-2 alert adapter and records the events it is handed."""

    def __init__(self) -> None:
        self.events: list[CustomRuleAlertEvent] = []

    async def open_or_update(self, event: CustomRuleAlertEvent) -> None:
        self.events.append(event)

    async def resolve(self, event: CustomRuleAlertEvent) -> None:
        self.events.append(event)


def _settings(**overrides: object) -> Settings:
    values = get_settings().model_dump()
    values.update(
        custom_rules_enabled=True,
        collection_interval_seconds=300,
        custom_rules_max_buckets_per_run=288,
    )
    values.update(overrides)
    return Settings(**values)


def _rule(name: str, source: LogSource, **overrides: object) -> CustomRule:
    values: dict[str, object] = {
        "name": name,
        "subject_kind": CustomRuleSubjectKind.LOG_SOURCE,
        "log_source_id": source.id,
        "metric": CustomRuleMetric.EVENT_COUNT.value,
        "operator": "GT",
        "threshold_value": 100.0,
        "sustain_buckets": 3,
        "recovery_buckets": 2,
        "severity": Severity.HIGH,
        "enabled": True,
        "consecutive_breaching": 0,
        "consecutive_under": 0,
    }
    values.update(overrides)
    return CustomRule(**values)  # type: ignore[arg-type]


async def _breach_count(session) -> int:  # type: ignore[no-untyped-def]
    count = await session.scalar(select(func.count()).select_from(CustomRuleBreach))
    return int(count or 0)


async def _alert_count(session) -> int:  # type: ignore[no-untyped-def]
    count = await session.scalar(select(func.count()).select_from(Alert))
    return int(count or 0)


async def test_first_run_seeds_and_a_later_run_opens_then_resolves(db_session) -> None:
    """The seed bucket is never judged. Sustain opens on the third judged bucket.

    Recovery then resolves, the sink hears open then resolve, and no alert row
    is written. A repeat of the recovery tick does not open a second breach.
    """
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=41, name="edge-fw")
    for minute, events in (
        (55, 400),
        (0, 400),
        (5, 400),
        (10, 400),
        (15, 1),
        (20, 1),
    ):
        hour = 9 if minute == 55 else 10
        await add_metric(
            db_session, source, utc(h=hour, mi=minute), event_count=events, average_eps=1.0
        )
    rule = _rule("sustained-volume", source)
    db_session.add(rule)
    await db_session.flush()
    sink = _RecordingSink()
    settings = _settings()

    seeded = await CustomRuleEngine(
        db_session, settings, sink=sink, clock=lambda: utc(h=10, mi=0)
    ).run()
    assert seeded.status == "seeded"
    assert seeded.judged_buckets == 0
    assert seeded.opened == 0
    assert rule.last_status is None
    assert rule.consecutive_breaching == 0
    mark = (
        await db_session.execute(select(CustomRuleWatermark))
    ).scalar_one()
    assert mark.subject_kind == CustomRuleSubjectKind.LOG_SOURCE
    assert mark.evaluated_through == utc(h=9, mi=55)
    assert await _breach_count(db_session) == 0

    opened = await CustomRuleEngine(
        db_session, settings, sink=sink, clock=lambda: utc(h=10, mi=16)
    ).run()
    assert opened.status == "ok"
    assert opened.opened == 1
    assert opened.resolved == 0
    assert [event.action for event in sink.events] == ["open"]
    assert sink.events[0].observed_value == 400.0
    assert sink.events[0].measurement == "EXACT"
    assert sink.events[0].metric == "event_count"
    breach = (await db_session.execute(select(CustomRuleBreach))).scalar_one()
    assert breach.state == AnomalyState.OPEN
    assert breach.first_bucket_start == utc(h=10, mi=10)
    assert breach.last_bucket_start == utc(h=10, mi=10)
    assert breach.worst_value == 400.0
    assert breach.worst_measurement == "EXACT"
    assert breach.subject_label == "edge-fw"
    assert breach.details["metric"] == "event_count"
    assert breach.details["subject_label"] == "edge-fw"
    assert rule.active_breach_id == breach.id
    assert rule.consecutive_breaching == 3
    assert rule.last_status == AnomalyState.OPEN.value
    assert mark.evaluated_through == utc(h=10, mi=10)
    assert await _alert_count(db_session) == 0

    resolved = await CustomRuleEngine(
        db_session, settings, sink=sink, clock=lambda: utc(h=10, mi=26)
    ).run()
    assert resolved.resolved == 1
    assert resolved.opened == 0
    assert [event.action for event in sink.events] == ["open", "resolve"]
    assert sink.events[1].reason == "condition recovered"
    assert breach.state == AnomalyState.RESOLVED
    assert breach.resolved_at == utc(h=10, mi=26)
    assert rule.active_breach_id is None
    assert rule.consecutive_breaching == 0
    assert rule.consecutive_under == 0
    assert rule.last_status == "OK"
    assert mark.evaluated_through == utc(h=10, mi=20)
    assert await _alert_count(db_session) == 0

    again = await CustomRuleEngine(
        db_session, settings, sink=sink, clock=lambda: utc(h=10, mi=26)
    ).run()
    assert again.opened == 0
    assert again.resolved == 0
    assert again.judged_buckets == 0
    assert await _breach_count(db_session) == 1
    assert [event.action for event in sink.events] == ["open", "resolve"]


async def test_a_stopped_collection_is_stale_until_the_backlog_is_judged(db_session) -> None:
    """No new row past the family's cadence is STALE, and the breach stays open.

    The watermark and ``last_evaluated_at`` stay on the last judged bucket.
    When rows return, those buckets are judged and recovery can resolve.
    """
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=81, name="edge-fw")
    db_session.add(
        SearchCadenceSetting(
            family=SearchFamily.PHASE_A_VOLUME.value,
            cadence_seconds=int(SearchCadence.FIVE_MINUTES),
            updated_at=utc(h=9),
        )
    )
    await add_metric(db_session, source, utc(h=10), event_count=400)
    rule = _rule("sustained-then-silent", source, sustain_buckets=1, recovery_buckets=2)
    db_session.add(rule)
    await db_session.flush()
    sink = _RecordingSink()
    # Bound is 300 + 300 + 0 + 60 = 660 s. The watermark after the open is 10:00.
    settings = _settings(search_cadence_settle_seconds=0)

    await CustomRuleEngine(db_session, settings, sink=sink, clock=lambda: utc(h=10)).run()
    opened = await CustomRuleEngine(
        db_session, settings, sink=sink, clock=lambda: utc(h=10, mi=6)
    ).run()
    mark = (await db_session.execute(select(CustomRuleWatermark))).scalar_one()
    assert opened.opened == 1
    assert rule.last_status == "OPEN"
    assert rule.last_evaluated_at == utc(h=10, mi=6)
    assert mark.evaluated_through == utc(h=10)
    breach = (await db_session.execute(select(CustomRuleBreach))).scalar_one()
    assert breach.state == AnomalyState.OPEN
    assert breach.unmeasured_buckets == 0

    # 10:16 closes 10:10. Lag is 600 s, inside the 660 s bound.
    held = await CustomRuleEngine(
        db_session, settings, sink=sink, clock=lambda: utc(h=10, mi=16)
    ).run()
    assert held.judged_buckets == 0
    assert rule.last_status == "OPEN"
    assert rule.last_evaluated_at == utc(h=10, mi=6)
    assert mark.evaluated_through == utc(h=10)
    assert [event.action for event in sink.events] == ["open"]

    # 10:21 closes 10:15. Lag is 900 s, past the bound.
    stale = await CustomRuleEngine(
        db_session, settings, sink=sink, clock=lambda: utc(h=10, mi=21)
    ).run()
    assert stale.judged_buckets == 0
    assert stale.resolved == 0
    assert rule.last_status == "STALE"
    assert rule.last_status_reason == (
        "no bucket collected for this kind since 2026-07-15 10:00 UTC (21 min)"
    )
    assert rule.last_evaluated_at == utc(h=10, mi=6)
    assert rule.last_evaluated_bucket_start == utc(h=10)
    assert rule.consecutive_breaching == 1
    assert mark.evaluated_through == utc(h=10)
    assert breach.state == AnomalyState.OPEN
    assert breach.unmeasured_buckets == 0
    assert breach.resolved_at is None
    assert [event.action for event in sink.events] == ["open"]
    assert await _alert_count(db_session) == 0

    await add_metric(db_session, source, utc(h=10, mi=5), event_count=1)
    await add_metric(db_session, source, utc(h=10, mi=10), event_count=1)
    recovered = await CustomRuleEngine(
        db_session, settings, sink=sink, clock=lambda: utc(h=10, mi=21)
    ).run()
    assert recovered.judged_buckets == 2
    assert recovered.resolved == 1
    assert rule.last_status == "OK"
    assert rule.last_evaluated_at == utc(h=10, mi=21)
    assert rule.active_breach_id is None
    assert mark.evaluated_through == utc(h=10, mi=10)
    assert breach.state == AnomalyState.RESOLVED
    assert [event.action for event in sink.events] == ["open", "resolve"]
    assert sink.events[1].reason == "condition recovered"


async def test_a_source_with_no_row_is_unmeasured_while_its_kind_has_buckets(
    db_session,
) -> None:
    """One silent source is unmeasured per grid point. The kind is not stale.

    The sibling's row is what the kind walked, so the quiet rule is judged
    and comes back ``NO_VERDICT`` rather than a zero that reads as under.
    """
    instance = await make_instance(db_session)
    loud = await make_log_source(db_session, instance, qradar_id=82, name="loud")
    quiet = await make_log_source(db_session, instance, qradar_id=83, name="quiet")
    await add_metric(db_session, loud, utc(h=10, mi=5), event_count=10)
    loud_rule = _rule("loud-source", loud, threshold_value=100.0, sustain_buckets=1)
    quiet_rule = _rule("quiet-source", quiet, threshold_value=100.0, sustain_buckets=1)
    db_session.add_all([loud_rule, quiet_rule])
    await db_session.flush()
    settings = _settings()

    await CustomRuleEngine(db_session, settings, clock=lambda: utc(h=10)).run()
    await CustomRuleEngine(db_session, settings, clock=lambda: utc(h=10, mi=12)).run()
    mark = (await db_session.execute(select(CustomRuleWatermark))).scalar_one()

    assert loud_rule.last_status == "OK"
    assert loud_rule.last_evaluated_at == utc(h=10, mi=12)
    assert quiet_rule.last_status == "NO_VERDICT"
    assert quiet_rule.last_status_reason == "no row for this subject in the bucket"
    assert quiet_rule.last_evaluated_at == utc(h=10, mi=12)
    assert quiet_rule.last_evaluated_bucket_start == utc(h=10, mi=5)
    assert quiet_rule.consecutive_breaching == 0
    assert quiet_rule.consecutive_under == 0
    assert quiet_rule.active_breach_id is None
    assert mark.evaluated_through == utc(h=10, mi=5)
    assert await _breach_count(db_session) == 0


async def test_a_source_in_maintenance_is_not_opened_or_resolved(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(
        db_session, instance, qradar_id=42, name="paused", maintenance_mode=True
    )
    for minute in (0, 5, 10):
        await add_metric(db_session, source, utc(h=10, mi=minute), event_count=900)
    rule = _rule("paused-volume", source, sustain_buckets=1, recovery_buckets=1)
    db_session.add(rule)
    await db_session.flush()
    settings = _settings()

    await CustomRuleEngine(db_session, settings, clock=lambda: utc(h=10, mi=0)).run()
    judged = await CustomRuleEngine(
        db_session, settings, clock=lambda: utc(h=10, mi=16)
    ).run()
    mark = (await db_session.execute(select(CustomRuleWatermark))).scalar_one()

    assert judged.opened == 0
    assert judged.resolved == 0
    assert judged.refreshed == 0
    assert await _breach_count(db_session) == 0
    assert rule.consecutive_breaching == 0
    assert rule.consecutive_under == 0
    assert rule.last_status == "NO_VERDICT"
    assert rule.last_status_reason is not None
    assert "maintenance" in rule.last_status_reason
    assert rule.last_evaluated_bucket_start == utc(h=10, mi=10)
    assert mark.evaluated_through == utc(h=10, mi=10)
    assert await _alert_count(db_session) == 0


async def test_disabled_and_uncollected_rules_do_not_seed_or_judge(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=43, name="quiet")
    await add_metric(db_session, source, utc(h=10, mi=5), event_count=900)
    held = utc(h=9, mi=50)
    disabled = _rule(
        "switched-off",
        source,
        enabled=False,
        sustain_buckets=1,
        consecutive_breaching=4,
        consecutive_under=2,
        last_evaluated_bucket_start=held,
        last_status="OK",
    )
    endpoint = WafEndpoint(
        instance_id=instance.id,
        log_source_id=source.id,
        virtual_host=None,
        normalized_endpoint="/login",
        http_method="GET",
        normalizer_version=1,
        first_seen_at=utc(h=8),
        last_seen_at=utc(h=9),
        tracked_since=utc(h=8),
        is_active=True,
    )
    db_session.add(endpoint)
    await db_session.flush()
    waf_rule = CustomRule(
        name="waf-off",
        subject_kind=CustomRuleSubjectKind.WAF_ENDPOINT,
        waf_endpoint_id=endpoint.id,
        metric=CustomRuleMetric.REQUEST_COUNT.value,
        operator="GT",
        threshold_value=1.0,
        sustain_buckets=1,
        recovery_buckets=1,
        severity=Severity.MEDIUM,
        enabled=True,
        consecutive_breaching=0,
        consecutive_under=0,
    )
    ips_rule = _rule(
        "ips-off",
        source,
        subject_kind=CustomRuleSubjectKind.IPS_SOURCE,
        metric=CustomRuleMetric.TOTAL_EVENTS.value,
        sustain_buckets=1,
    )
    db_session.add_all([disabled, waf_rule, ips_rule])
    await db_session.flush()

    # WAF and IPS collection stay off. A disabled log-source rule is not a
    # reason to seed the log-source watermark either.
    report = await CustomRuleEngine(
        db_session, _settings(), clock=lambda: utc(h=10, mi=16)
    ).run()

    assert report.judged_buckets == 0
    assert report.seeded == []
    assert disabled.last_status == "DISABLED"
    assert disabled.consecutive_breaching == 4
    assert disabled.consecutive_under == 2
    assert disabled.last_evaluated_bucket_start == held
    assert waf_rule.last_status == "NOT_COLLECTED"
    assert waf_rule.last_status_reason == "waf_collection_enabled is off"
    assert ips_rule.last_status == "NOT_COLLECTED"
    assert ips_rule.last_status_reason == "ips_collection_enabled is off"
    assert (await db_session.execute(select(CustomRuleWatermark))).scalars().all() == []
    assert await _breach_count(db_session) == 0


async def test_a_retired_endpoint_is_not_judged_and_the_reason_names_when(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=44, name="waf")
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

    await CustomRuleEngine(
        db_session,
        _settings(waf_collection_enabled=True),
        clock=lambda: utc(h=10, mi=16),
    ).run()

    assert rule.last_status == "SUBJECT_RETIRED"
    assert rule.last_status_reason is not None
    assert retired_at.isoformat() in rule.last_status_reason
    assert "/secret-path" not in rule.last_status_reason
    assert rule.consecutive_breaching == 2
    assert rule.last_evaluated_bucket_start is None
    assert (await db_session.execute(select(CustomRuleWatermark))).scalars().all() == []


async def test_fleet_eps_excludes_disabled_sources_and_stays_a_lower_bound(db_session) -> None:
    """One COMPLETE source of two monitored sources is a lower bound, not a zero.

    A disabled sibling with a large count must not inflate the rate. A lower
    bound can open a GT rule and cannot open an LT rule.
    """
    instance = await make_instance(db_session)
    hot = await make_log_source(db_session, instance, qradar_id=51, name="hot")
    cold = await make_log_source(db_session, instance, qradar_id=52, name="cold")
    quiet = await make_log_source(
        db_session, instance, qradar_id=53, name="quiet", enabled=False
    )
    when = utc()
    for source in (hot, cold, quiet):
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
        db_session,
        hot,
        utc(h=10, mi=5),
        event_count=300,
        bucket_seconds=300,
        average_eps=1.0,
    )
    await add_metric(
        db_session,
        quiet,
        utc(h=10, mi=5),
        event_count=300_000,
        bucket_seconds=300,
        average_eps=1000.0,
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
    below = CustomRule(
        name="linux-eps-below",
        subject_kind=CustomRuleSubjectKind.FLEET_CLASS,
        source_class=SourceClass.LINUX.value,
        metric=CustomRuleMetric.EPS.value,
        operator="LT",
        threshold_value=10.0,
        sustain_buckets=1,
        recovery_buckets=1,
        severity=Severity.LOW,
        enabled=True,
        consecutive_breaching=0,
        consecutive_under=0,
    )
    db_session.add_all([above, below])
    await db_session.flush()
    settings = _settings()
    await CustomRuleEngine(db_session, settings, clock=lambda: utc(h=10, mi=7)).run()
    await CustomRuleEngine(db_session, settings, clock=lambda: utc(h=10, mi=12)).run()

    assert above.last_status == AnomalyState.OPEN.value
    breach = (await db_session.execute(select(CustomRuleBreach))).scalar_one()
    assert breach.rule_id == above.id
    assert breach.worst_value == 1.0
    assert breach.worst_measurement == "LOWER_BOUND"
    assert below.last_status == "NO_VERDICT"
    assert below.active_breach_id is None
    assert await _breach_count(db_session) == 1


async def test_statement_count_is_the_same_for_ten_and_a_hundred_rules(db_session) -> None:
    """One metric statement per kind, however many rules share it.

    The threshold cannot open a breach, so the two sizes flush the same shape
    of update. An insert that paged would make the counts diverge.
    """
    instance = await make_instance(db_session)

    async def plant(count: int) -> list[str]:
        await db_session.execute(delete(CustomRule))
        await db_session.execute(delete(CustomRuleWatermark))
        await db_session.flush()
        sources = [
            LogSource(
                instance_id=instance.id,
                qradar_id=20_000 + count * 1000 + index,
                name=f"bulk-{count}-{index}",
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
                    f"bulk-rule-{count}-{index}",
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
                evaluated_through=utc(h=10, mi=0),
                updated_at=utc(h=10, mi=0),
            )
        )
        await db_session.flush()
        with counting(db_session) as statements:
            report = await CustomRuleEngine(
                db_session, _settings(), clock=lambda: utc(h=10, mi=12)
            ).run()
        assert report.opened == 0
        assert report.judged_buckets == 1
        return list(statements)

    small = await plant(10)
    large = await plant(100)
    assert len(small) == len(large)
    assert sum("log_source_metric" in statement for statement in small) == 1
    assert sum("log_source_metric" in statement for statement in large) == 1


async def test_the_engine_issues_no_sql_while_custom_rules_are_disabled(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=60, name="ignored")
    db_session.add(_rule("ignored", source, sustain_buckets=1))
    await db_session.flush()
    with counting(db_session) as statements:
        report = await CustomRuleEngine(
            db_session, _settings(custom_rules_enabled=False), clock=lambda: utc(h=10, mi=16)
        ).run()
    assert report.status == "disabled"
    assert statements == []
    assert await _breach_count(db_session) == 0


async def test_retention_drops_old_resolved_breaches_in_bounded_batches(db_session) -> None:
    """Live rows, a null resolved_at, a recent resolve and a still-referenced row stay.

    One full batch of 100 against 101 deletable rows is incomplete. A later run
    finishes the remainder and still leaves the protected rows.
    """
    now = datetime(2026, 10, 16, tzinfo=UTC)
    old = now - timedelta(days=181)
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=70, name="kept")
    other = await make_log_source(db_session, instance, qradar_id=71, name="other")
    rule = _rule("retained", source, sustain_buckets=1, recovery_buckets=1)
    other_rule = _rule("referenced", other, sustain_buckets=1, recovery_buckets=1)
    db_session.add_all([rule, other_rule])
    await db_session.flush()

    def resolved(when: datetime | None, owner: CustomRule) -> CustomRuleBreach:
        return CustomRuleBreach(
            id=uuid.uuid4(),
            rule_id=owner.id,
            state=AnomalyState.RESOLVED,
            first_bucket_start=old,
            last_bucket_start=old,
            opened_at=old,
            resolved_at=when,
            worst_value=10.0,
            worst_measurement="EXACT",
            last_value=1.0,
            last_measurement="EXACT",
            unmeasured_buckets=0,
            subject_label=owner.name,
            details={"metric": "event_count"},
        )

    unprotected = [resolved(old, rule) for _ in range(101)]
    protected = resolved(old, other_rule)
    recent = resolved(now - timedelta(days=1), rule)
    missing_time = resolved(None, rule)
    live = CustomRuleBreach(
        id=uuid.uuid4(),
        rule_id=rule.id,
        state=AnomalyState.OPEN,
        first_bucket_start=now,
        last_bucket_start=now,
        opened_at=now,
        worst_value=50.0,
        worst_measurement="EXACT",
        last_value=50.0,
        last_measurement="EXACT",
        unmeasured_buckets=0,
        subject_label="kept",
        details={"metric": "event_count"},
    )
    db_session.add_all([*unprotected, protected, recent, missing_time, live])
    rule.active_breach_id = live.id
    other_rule.active_breach_id = protected.id
    await db_session.flush()

    limited = _settings(
        custom_rule_breach_retention_days=180,
        custom_rules_retention_batch_size=100,
        custom_rules_retention_max_batches=1,
    )
    first = await CustomRuleRetention(db_session, settings=limited, clock=lambda: now).run()
    assert first.deleted == {"custom_rule_breach": 100}
    assert first.incomplete == ["custom_rule_breach"]

    async def still_there(row_id: uuid.UUID) -> bool:
        # Retention commits, and the session does not expire. Read the table,
        # so an identity-map object left behind by the DELETE does not count.
        found = await db_session.scalar(
            select(CustomRuleBreach.id).where(CustomRuleBreach.id == row_id)
        )
        return found is not None

    survivors = [row for row in unprotected if await still_there(row.id)]
    assert len(survivors) == 1
    assert await still_there(protected.id)
    assert await still_there(recent.id)
    assert await still_there(missing_time.id)
    assert await still_there(live.id)

    finished = _settings(
        custom_rule_breach_retention_days=180,
        custom_rules_retention_batch_size=100,
        custom_rules_retention_max_batches=5,
    )
    second = await CustomRuleRetention(db_session, settings=finished, clock=lambda: now).run()
    assert second.deleted == {"custom_rule_breach": 1}
    assert second.incomplete == []
    assert not await still_there(survivors[0].id)
    assert await still_there(protected.id)
    assert await still_there(recent.id)
    assert await still_there(missing_time.id)
    assert await still_there(live.id)
    remaining_rules = await db_session.scalar(select(func.count()).select_from(CustomRule))
    assert remaining_rules == 2
