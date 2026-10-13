"""The outage alert policy's engine hook: what RECORD_ONLY does and does not change.

The policy changes exactly one thing: whether a source's NO_EVENTS opens an
alert. The anomaly row, its lifecycle, its transitions, the fingerprint and
every volume detector are untouched (design C.7). DB-gated.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.alerts.fingerprint import anomaly_fingerprint
from app.anomaly.engine import AnomalyEngine
from app.core.config import Settings
from app.models.alert import Alert
from app.models.enums import (
    AlertStatus,
    AnomalyState,
    AnomalyType,
    ClassificationReviewStatus,
    Criticality,
    OutagePolicyMode,
    OutagePolicyScope,
    SourceClass,
)
from app.models.fleet import LogSourceClassification
from app.models.log_source import (
    AnomalyStateTransition,
    LogSourceAnomaly,
    LogSourceBaseline,
)
from app.models.outage import OutageAlertPolicy
from tests.integration.factories import add_metric, make_instance, make_log_source, utc
from tests.integration.test_waf_scale import counting

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _settings() -> Settings:
    return Settings(encryption_key="x" * 44, anomaly_deviation_threshold=3.5)


class RecordingEnqueuer:
    """Stands in for the dispatcher: records notification intents only."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[str, object]] = []

    async def enqueue(self, alert: Alert, transition: object) -> list[object]:
        self.enqueued.append((alert.fingerprint, transition))
        return []


async def _classified(session, instance, *, cls=SourceClass.FIREWALL, **kwargs):  # type: ignore[no-untyped-def]
    log_source = await make_log_source(session, instance, **kwargs)
    session.add(
        LogSourceClassification(
            log_source_id=log_source.id,
            source_class=cls.value,
            basis="NAME_REGEX",
            conflicts=[],
            classified_at=utc(),
            review_status=ClassificationReviewStatus.REVIEWED.value,
        )
    )
    await session.flush()
    return log_source


async def _event_count_baseline(session, log_source, start) -> None:  # type: ignore[no-untyped-def]
    session.add(
        LogSourceBaseline(
            log_source_id=log_source.id,
            metric_name="event_count",
            weekday=start.isoweekday(),
            hour=start.hour,
            median=15_000.0,
            mad=500.0,
            p05=14_000.0,
            p95=16_000.0,
            sample_count=30,
            is_reliable=True,
            baseline_version=1,
            observations=[15_000.0],
        )
    )
    await session.flush()


async def _eps_baseline(session, log_source, start, median: float = 50.0) -> None:  # type: ignore[no-untyped-def]
    session.add(
        LogSourceBaseline(
            log_source_id=log_source.id,
            metric_name="average_eps",
            weekday=start.isoweekday(),
            hour=start.hour,
            median=median,
            mad=2.0,
            p05=median * 0.9,
            p95=median * 1.1,
            sample_count=30,
            is_reliable=True,
            baseline_version=1,
            observations=[median],
        )
    )
    await session.flush()


async def _bucket(session, log_source, start, index, **kwargs):  # type: ignore[no-untyped-def]
    defaults = dict(average_eps=0.0, event_count=0, stored_event_count=0,
                    last_event_at=None, event_delay_seconds=None, collection_source="mock")
    defaults.update(kwargs)
    return await add_metric(session, log_source, start + timedelta(minutes=5 * index), **defaults)


async def _class_policy(session, mode: OutagePolicyMode, *, cls=SourceClass.FIREWALL) -> None:  # type: ignore[no-untyped-def]
    session.add(
        OutageAlertPolicy(
            scope_kind=OutagePolicyScope.CLASS.value,
            scope_value=cls.value,
            mode=mode.value,
        )
    )
    await session.flush()


async def _open_alert(session, log_source, atype: AnomalyType) -> Alert | None:
    return await session.scalar(
        select(Alert).where(
            Alert.dedup_key == anomaly_fingerprint(log_source.id, atype.value),
            Alert.status != AlertStatus.RESOLVED,
        )
    )


async def _no_events_anomaly(session, log_source) -> LogSourceAnomaly | None:
    return await session.scalar(
        select(LogSourceAnomaly).where(
            LogSourceAnomaly.log_source_id == log_source.id,
            LogSourceAnomaly.anomaly_type == AnomalyType.NO_EVENTS,
        )
    )


async def test_record_only_class_records_the_outage_without_an_alert(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await _classified(db_session, inst, qradar_id=1, name="fw-1",
                           criticality=Criticality.CRITICAL)
    start = utc(h=10)
    await _event_count_baseline(db_session, ls, start)
    await _class_policy(db_session, OutagePolicyMode.RECORD_ONLY)

    dispatcher = RecordingEnqueuer()
    engine = AnomalyEngine(db_session, settings=_settings(), enqueuer=dispatcher)
    await engine.evaluate_interval(ls, await _bucket(db_session, ls, start, 0))
    report = await engine.evaluate_interval(ls, await _bucket(db_session, ls, start, 1))

    # The anomaly is recorded and its lifecycle runs exactly as before.
    assert AnomalyType.NO_EVENTS.value in report.opened
    anomaly = await _no_events_anomaly(db_session, ls)
    assert anomaly is not None
    assert anomaly.state == AnomalyState.OPEN
    assert anomaly.opened_at is not None
    transitions = await db_session.scalar(
        select(func.count()).select_from(AnomalyStateTransition).where(
            AnomalyStateTransition.anomaly_id == anomaly.id
        )
    )
    assert transitions >= 1  # the lifecycle still records its transitions
    # But nothing pages: no alert, no notification intent.
    assert await _open_alert(db_session, ls, AnomalyType.NO_EVENTS) is None
    assert dispatcher.enqueued == []


async def test_default_alert_still_opens_an_outage_alert(db_session) -> None:
    # No policy rows: the default is ALERT, so behaviour is Phase A's exactly.
    inst = await make_instance(db_session)
    ls = await _classified(db_session, inst, qradar_id=1, name="fw-1",
                           criticality=Criticality.CRITICAL)
    start = utc(h=10)
    await _event_count_baseline(db_session, ls, start)

    dispatcher = RecordingEnqueuer()
    engine = AnomalyEngine(db_session, settings=_settings(), enqueuer=dispatcher)
    await engine.evaluate_interval(ls, await _bucket(db_session, ls, start, 0))
    report = await engine.evaluate_interval(ls, await _bucket(db_session, ls, start, 1))

    assert AnomalyType.NO_EVENTS.value in report.opened
    alert = await _open_alert(db_session, ls, AnomalyType.NO_EVENTS)
    assert alert is not None and alert.status == AlertStatus.OPEN
    # The policy never enters the fingerprint: identity is the class-free one.
    assert alert.dedup_key == anomaly_fingerprint(ls.id, AnomalyType.NO_EVENTS.value)
    assert dispatcher.enqueued and dispatcher.enqueued[0][0] == alert.dedup_key


async def test_alert_source_override_beats_a_record_only_class(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await _classified(db_session, inst, qradar_id=1, name="fw-1",
                           criticality=Criticality.CRITICAL)
    start = utc(h=10)
    await _event_count_baseline(db_session, ls, start)
    await _class_policy(db_session, OutagePolicyMode.RECORD_ONLY)
    db_session.add(
        OutageAlertPolicy(
            scope_kind=OutagePolicyScope.SOURCE.value,
            scope_value=str(ls.id),
            mode=OutagePolicyMode.ALERT.value,
        )
    )
    await db_session.flush()

    dispatcher = RecordingEnqueuer()
    engine = AnomalyEngine(db_session, settings=_settings(), enqueuer=dispatcher)
    await engine.evaluate_interval(ls, await _bucket(db_session, ls, start, 0))
    report = await engine.evaluate_interval(ls, await _bucket(db_session, ls, start, 1))

    assert AnomalyType.NO_EVENTS.value in report.opened
    assert await _open_alert(db_session, ls, AnomalyType.NO_EVENTS) is not None
    assert len(dispatcher.enqueued) == 1


async def test_switching_to_record_only_then_recovery_still_resolves_the_alert(
    db_session,
) -> None:
    inst = await make_instance(db_session)
    ls = await _classified(db_session, inst, qradar_id=1, name="fw-1",
                           criticality=Criticality.CRITICAL)
    start = utc(h=10)
    await _event_count_baseline(db_session, ls, start)

    # Open under ALERT (the default).
    first = AnomalyEngine(db_session, settings=_settings())
    await first.evaluate_interval(ls, await _bucket(db_session, ls, start, 0))
    await first.evaluate_interval(ls, await _bucket(db_session, ls, start, 1))
    assert await _open_alert(db_session, ls, AnomalyType.NO_EVENTS) is not None

    # The operator switches the class to RECORD_ONLY; a fresh engine sees it.
    await _class_policy(db_session, OutagePolicyMode.RECORD_ONLY)
    second = AnomalyEngine(db_session, settings=_settings())
    await second.evaluate_interval(
        ls, await _bucket(db_session, ls, start, 2, average_eps=50.0, event_count=15_000,
                          stored_event_count=15_000, last_event_at=start, event_delay_seconds=5.0)
    )
    await second.evaluate_interval(
        ls, await _bucket(db_session, ls, start, 3, average_eps=50.0, event_count=15_000,
                          stored_event_count=15_000, last_event_at=start, event_delay_seconds=5.0)
    )

    # The alert opened before the switch closes normally; the policy never
    # mass-resolves and never blocks resolution.
    alert = await _open_alert(db_session, ls, AnomalyType.NO_EVENTS)
    assert alert is None
    resolved = await db_session.scalar(
        select(Alert).where(
            Alert.dedup_key == anomaly_fingerprint(ls.id, AnomalyType.NO_EVENTS.value)
        )
    )
    assert resolved is not None and resolved.status == AlertStatus.RESOLVED


async def test_grace_override_changes_when_no_events_fires(db_session) -> None:
    inst = await make_instance(db_session)
    start = utc(h=10)
    overridden = await _classified(db_session, inst, qradar_id=1, name="fw-grace-0",
                                   criticality=Criticality.CRITICAL)
    defaulted = await _classified(db_session, inst, qradar_id=2, name="fw-grace-default",
                                  criticality=Criticality.CRITICAL)
    await _event_count_baseline(db_session, overridden, start)
    await _event_count_baseline(db_session, defaulted, start)
    db_session.add(
        OutageAlertPolicy(
            scope_kind=OutagePolicyScope.SOURCE.value,
            scope_value=str(overridden.id),
            mode=OutagePolicyMode.ALERT.value,
            silence_grace_buckets=0,
        )
    )
    await db_session.flush()

    engine = AnomalyEngine(db_session, settings=_settings())
    await engine.evaluate_interval(overridden, await _bucket(db_session, overridden, start, 0))
    await engine.evaluate_interval(defaulted, await _bucket(db_session, defaulted, start, 0))

    # grace 0 fires NO_EVENTS and opens on the very first empty bucket ...
    assert await _open_alert(db_session, overridden, AnomalyType.NO_EVENTS) is not None
    # ... while the default grace 1 tolerates it.
    assert await _open_alert(db_session, defaulted, AnomalyType.NO_EVENTS) is None
    assert await _no_events_anomaly(db_session, defaulted) is None


async def test_volume_spike_in_a_record_only_class_still_alerts(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await _classified(db_session, inst, qradar_id=1, name="fw-1")
    start = utc(h=10)
    await _eps_baseline(db_session, ls, start, median=50.0)
    await _class_policy(db_session, OutagePolicyMode.RECORD_ONLY)

    engine = AnomalyEngine(db_session, settings=_settings())
    for index in range(2):
        await engine.evaluate_interval(
            ls,
            await _bucket(db_session, ls, start, index, average_eps=500.0, event_count=150_000,
                          stored_event_count=150_000, last_event_at=start, event_delay_seconds=5.0,
                          payload_signature=f"spike-{index}"),
        )

    # "Outage" means NO_EVENTS only: a volume spike is not an outage.
    assert await _open_alert(db_session, ls, AnomalyType.VOLUME_SPIKE) is not None
    assert await _open_alert(db_session, ls, AnomalyType.NO_EVENTS) is None


async def test_policy_is_loaded_once_per_engine_run_whatever_the_fleet(db_session) -> None:
    """One statement for the whole run, whatever the number of policy rows.

    A per-source load would show one policy statement per source; the batch
    shows exactly one (design C.7, AGENTS.md rule 9).
    """

    async def policy_statements(size: int) -> int:
        inst = await make_instance(db_session)
        start = utc(h=10)
        sources = []
        for i in range(size):
            ls = await _classified(db_session, inst, qradar_id=i + 1, name=f"scale-{size}-{i}")
            sources.append(ls)
        db_session.add(
            OutageAlertPolicy(
                instance_id=inst.id,
                scope_kind=OutagePolicyScope.CLASS.value,
                scope_value=SourceClass.FIREWALL.value,
                mode=OutagePolicyMode.ALERT.value,
            )
        )
        db_session.add_all(
            OutageAlertPolicy(
                instance_id=inst.id,
                scope_kind=OutagePolicyScope.SOURCE.value,
                scope_value=str(ls.id),
                mode=OutagePolicyMode.ALERT.value,
            )
            for ls in sources
        )
        await db_session.flush()
        metrics = [
            (ls, await add_metric(db_session, ls, start, average_eps=50.0, event_count=15_000,
                                  collection_source="mock"))
            for ls in sources
        ]
        engine = AnomalyEngine(db_session, settings=_settings())
        with counting(db_session) as statements:
            for ls, metric in metrics:
                await engine.evaluate_interval(ls, metric)
        return sum(1 for statement in statements if "outage_alert_policy" in statement)

    small = await policy_statements(5)
    large = await policy_statements(40)
    assert small == large == 1, (small, large)


async def _evaluate_fleet(session, size: int, *, policy: str):  # type: ignore[no-untyped-def]
    """Evaluate `size` sources once each and report the policy/class lookups.

    `policy` is "class", "source" or "none": what shape of policy row, if any,
    the fleet runs against. Every source is classified, so the graceful decode
    of the returned counts cannot hide a missing lookup.
    """
    inst = await make_instance(session)
    start = utc(h=10)
    sources = []
    for i in range(size):
        sources.append(await _classified(session, inst, qradar_id=i + 1, name=f"fleet-{size}-{i}"))
    if policy == "class":
        session.add(
            OutageAlertPolicy(
                instance_id=inst.id,
                scope_kind=OutagePolicyScope.CLASS.value,
                scope_value=SourceClass.FIREWALL.value,
                mode=OutagePolicyMode.ALERT.value,
            )
        )
    elif policy == "source":
        session.add_all(
            OutageAlertPolicy(
                instance_id=inst.id,
                scope_kind=OutagePolicyScope.SOURCE.value,
                scope_value=str(ls.id),
                mode=OutagePolicyMode.ALERT.value,
            )
            for ls in sources
        )
    await session.flush()
    metrics = [
        (ls, await add_metric(session, ls, start, average_eps=50.0, event_count=15_000,
                              collection_source="mock"))
        for ls in sources
    ]
    engine = AnomalyEngine(session, settings=_settings())
    with counting(session) as statements:
        for ls, metric in metrics:
            await engine.evaluate_interval(ls, metric)
    policy_loads = sum(1 for s in statements if "outage_alert_policy" in s)
    class_loads = sum(1 for s in statements if "log_source_classification" in s)
    return policy_loads, class_loads


async def test_class_lookup_is_one_statement_whatever_the_fleet(db_session) -> None:
    """A CLASS policy costs one policy load and one class-map load, not one per source.

    The grace override in `_build_context` reads the outage verdict for every
    evaluated source, and a CLASS row makes that verdict depend on the source's
    class. Resolving it must not add a query per source: one policy statement
    and one class-map statement at any fleet size (design C.7, rule 9).
    """
    small = await _evaluate_fleet(db_session, 2, policy="class")
    large = await _evaluate_fleet(db_session, 10, policy="class")
    assert small == large == (1, 1), (small, large)


async def test_source_only_policy_skips_the_class_lookup(db_session) -> None:
    """With no CLASS row the class is irrelevant, so it is never read.

    The class map exists only to resolve a CLASS policy row; a fleet governed
    by SOURCE rows alone must not pay for it (rule 9).
    """
    small = await _evaluate_fleet(db_session, 2, policy="source")
    large = await _evaluate_fleet(db_session, 10, policy="source")
    assert small == large == (1, 0), (small, large)


async def test_no_policy_rows_skips_the_class_lookup(db_session) -> None:
    """No policy rows at all: the default applies and no class query runs.

    The default is ALERT, so evaluation is Phase A's exactly, and the engine
    reads no classification while resolving it (rule 9).
    """
    policy_loads, class_loads = await _evaluate_fleet(db_session, 3, policy="none")
    assert policy_loads == 1
    assert class_loads == 0
