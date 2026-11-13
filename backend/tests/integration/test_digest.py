"""SOC-17c: the daily digest's content, states, idempotency and cost.

The digest lists only incidents whose final two-layer mode is DIGEST, grouped by
source then detector. It never invents a number: a missing e-mail configuration,
an empty recipient list and a delivery failure are recorded states. The run row's
unique (instance, date) makes a second beat tick a no-op. DB-gated.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.models.alert_policy import AlertDigestRun, AlertPolicy
from app.models.email import EmailRecipient, EmailTemplate
from app.models.enums import (
    AlertDigestState,
    AlertPolicyMode,
    AlertPolicyScope,
    AnomalyFamily,
    OutagePolicyMode,
    OutagePolicyScope,
    Severity,
)
from app.models.fleet import LogSourceClassification
from app.models.ips import IpsAnomaly
from app.models.log_source import LogSourceAnomaly
from app.models.outage import OutageAlertPolicy
from app.models.property_watch import PropertyWatch, PwAnomaly
from app.models.rule import AnalyticsRule
from app.models.rule_anomaly import RuleAnomaly
from app.models.waf import WafAnomaly, WafEndpoint
from app.services.digest import DigestService, run_due_digests
from app.services.email_delivery import EmailDeliveryError
from tests.integration.factories import make_instance, make_log_source, utc
from tests.integration.test_waf_scale import counting

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 11, 6, 7, 5, tzinfo=UTC)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "encryption_key": "x" * 44,
        "smtp_host": "relay.example.internal",
        "smtp_from": "observability@example.internal",
        "smtp_security": "none",
        "notify_send_recovery": True,
        "waf_alerting_enabled": True,
        "ips_alerting_enabled": True,
        "pw_alerting_enabled": True,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class Capture:
    """Stands in for the SMTP transport: keeps every built message."""

    def __init__(self, *, fail: str | None = None) -> None:
        self.messages: list[object] = []
        self._fail = fail

    def __call__(self, _settings: Settings, message: object) -> None:
        self.messages.append(message)
        if self._fail is not None:
            raise EmailDeliveryError(self._fail)


def _html(message: object) -> str:
    return message.get_body(preferencelist=("html",)).get_content()  # type: ignore[attr-defined]


def _text(message: object) -> str:
    return message.get_body(preferencelist=("plain",)).get_content()  # type: ignore[attr-defined]


async def _phase_a(
    session,
    instance,
    source,
    *,
    detector: str = "VOLUME_SPIKE",
    count: int = 1,
    severity: Severity = Severity.HIGH,
    ratio: float = 2.5,
    at: datetime | None = None,
) -> None:
    start = at or (NOW - timedelta(hours=20))
    session.add_all(
        [
            LogSourceAnomaly(
                log_source_id=source.id,
                anomaly_type=detector,
                severity=severity.value,
                state="OPEN",
                detected_at=start + timedelta(minutes=5 * index),
                anomaly_start=start + timedelta(minutes=5 * index),
                anomaly_end=start + timedelta(minutes=5 * index + 5),
                deviation_ratio=ratio + index,
            )
            for index in range(count)
        ]
    )
    await session.flush()


async def _policy(
    session,
    instance,
    *,
    family: AnomalyFamily,
    source_id: uuid.UUID,
    detector: str,
    mode: AlertPolicyMode,
    scope: AlertPolicyScope = AlertPolicyScope.SOURCE,
    value: str | None = None,
) -> AlertPolicy:
    row = AlertPolicy(
        instance_id=instance.id,
        scope_kind=scope.value,
        scope_value=value if value is not None else str(source_id),
        family=family.value,
        detector_type=detector,
        mode=mode.value,
        updated_by="test",
    )
    session.add(row)
    await session.flush()
    return row


async def _recipient(session, address: str = "soc@example.org", **kwargs) -> EmailRecipient:  # type: ignore[no-untyped-def]
    row = EmailRecipient(
        address=address, min_severity=kwargs.pop("min_severity", "LOW"), **kwargs
    )
    session.add(row)
    await session.flush()
    return row


# ------------------------------------------------------------------ content
async def test_content_groups_by_source_and_detector(db_session, monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "ops@example.org")
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1, name="edge-fw")
    await _phase_a(
        db_session,
        instance,
        source,
        count=3,
        severity=Severity.MEDIUM,
        ratio=2.0,
        at=datetime(2026, 11, 5, 8, 0, tzinfo=UTC),
    )
    # The worst reading is the third one; set it explicitly over the generated ratios.
    incidents = list(
        (
            await db_session.scalars(
                select(LogSourceAnomaly).order_by(LogSourceAnomaly.detected_at)
            )
        ).all()
    )
    incidents[1].severity = Severity.HIGH.value
    incidents[2].severity = Severity.CRITICAL.value
    incidents[2].deviation_ratio = 4.5
    incidents[2].anomaly_end = datetime(2026, 11, 5, 10, 10, tzinfo=UTC)
    await _policy(
        db_session,
        instance,
        family=AnomalyFamily.LOG_SOURCE,
        source_id=source.id,
        detector="VOLUME_SPIKE",
        mode=AlertPolicyMode.DIGEST,
    )
    await db_session.flush()

    capture = Capture()
    results = await run_due_digests(
        db_session, now=NOW, settings=_settings(), deliver=capture
    )
    [result] = results
    assert result.state is AlertDigestState.SUCCESS
    assert result.item_count == 1 and result.recipient_count == 1
    [message] = capture.messages
    assert message["To"] == "ops@example.org"  # type: ignore[index]
    assert instance.name in message["Subject"]  # type: ignore[index]
    body = _html(message)
    assert "edge-fw" in body and "VOLUME_SPIKE" in body
    assert ">3</td>" in body
    assert "CRITICAL" in body
    assert "4.50x" in body
    assert "2026-11-05 08:00 UTC" in body
    assert "2026-11-05 10:10 UTC" in body
    assert "live" not in _text(message).lower()
    assert "real-time" not in _text(message).lower()
    assert "/anomalies?" in _text(message)


async def test_waf_ips_and_pw_are_grouped_by_their_own_source(
    db_session, monkeypatch
) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "ops@example.org")
    instance = await make_instance(db_session)
    waf_source = await make_log_source(db_session, instance, qradar_id=1, name="waf-src")
    ips_source = await make_log_source(db_session, instance, qradar_id=2, name="ips-src")
    endpoint = WafEndpoint(
        instance_id=instance.id,
        log_source_id=waf_source.id,
        normalized_endpoint="/login",
        http_method="POST",
        normalizer_version=1,
        first_seen_at=NOW - timedelta(days=30),
        last_seen_at=NOW,
        is_active=True,
    )
    db_session.add(endpoint)
    await db_session.flush()
    db_session.add_all(
        [
            WafAnomaly(
                waf_endpoint_id=endpoint.id,
                detector_type="WAF_REQUEST_RATE_SPIKE",
                severity="HIGH",
                state="OPEN",
                detected_at=NOW - timedelta(hours=2),
                anomaly_start=NOW - timedelta(hours=2),
                anomaly_end=NOW - timedelta(hours=2) + timedelta(minutes=5),
                deviation_ratio=7.0,
            ),
            IpsAnomaly(
                subject_kind="IPS_SOURCE",
                log_source_id=ips_source.id,
                detector_type="IPS_PORT_FANOUT_SPIKE",
                fingerprint="ips-test",
                severity="MEDIUM",
                state="OPEN",
                detected_at=NOW - timedelta(hours=1),
                anomaly_start=NOW - timedelta(hours=1),
                anomaly_end=NOW - timedelta(hours=1) + timedelta(minutes=5),
                deviation_ratio=3.0,
                profile_version=1,
                normalizer_version=1,
            ),
        ]
    )
    watch = PropertyWatch(
        instance_id=instance.id,
        name="auth-watch",
        scope_kind="LOG_SOURCE",
        log_source_id=ips_source.id,
        properties=[{"kind": "NORMALIZED", "name": "username"}],
        detectors=["PW_VALUE_SPIKE"],
        enabled=True,
    )
    db_session.add(watch)
    await db_session.flush()
    db_session.add(
        PwAnomaly(
            watch_id=watch.id,
            value_key="alice",
            detector_type="PW_VALUE_SPIKE",
            fingerprint="pw-test",
            severity="CRITICAL",
            state="OPEN",
            detected_at=NOW - timedelta(hours=3),
            anomaly_start=NOW - timedelta(hours=3),
            anomaly_end=NOW - timedelta(hours=3) + timedelta(minutes=5),
            deviation_ratio=9.0,
            watch_version=watch.watch_version,
        )
    )
    await db_session.flush()
    for family, source_id, detector in (
        (AnomalyFamily.WAF, waf_source.id, "WAF_REQUEST_RATE_SPIKE"),
        (AnomalyFamily.IPS, ips_source.id, "IPS_PORT_FANOUT_SPIKE"),
        (AnomalyFamily.PROPERTY_WATCH, watch.id, "PW_VALUE_SPIKE"),
    ):
        await _policy(
            db_session,
            instance,
            family=family,
            source_id=source_id,
            detector=detector,
            mode=AlertPolicyMode.DIGEST,
        )

    capture = Capture()
    [result] = await run_due_digests(
        db_session, now=NOW, settings=_settings(), deliver=capture
    )
    assert result.state is AlertDigestState.SUCCESS and result.item_count == 3
    body = _html(capture.messages[0])
    for expected in ("waf-src", "ips-src", "auth-watch", "WAF_REQUEST_RATE_SPIKE",
                     "IPS_PORT_FANOUT_SPIKE", "PW_VALUE_SPIKE"):
        assert expected in body


# ------------------------------------------------------- only final DIGEST
async def test_only_the_final_digest_mode_is_listed(db_session, monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "ops@example.org")
    instance = await make_instance(db_session)
    digest_source = await make_log_source(db_session, instance, qradar_id=1, name="digest-src")
    record_source = await make_log_source(db_session, instance, qradar_id=2, name="record-src")
    outage_record = await make_log_source(
        db_session, instance, qradar_id=3, name="outage-record"
    )
    outage_alert = await make_log_source(db_session, instance, qradar_id=4, name="outage-alert")
    for source in (digest_source, record_source, outage_record, outage_alert):
        await _phase_a(db_session, instance, source)
    await _phase_a(
        db_session, instance, outage_record, detector="NO_EVENTS", count=1,
        at=datetime(2026, 11, 5, 9, 0, tzinfo=UTC),
    )
    await _phase_a(
        db_session, instance, outage_alert, detector="NO_EVENTS", count=1,
        at=datetime(2026, 11, 5, 9, 0, tzinfo=UTC),
    )
    await _policy(
        db_session, instance, family=AnomalyFamily.LOG_SOURCE,
        source_id=digest_source.id, detector="VOLUME_SPIKE", mode=AlertPolicyMode.DIGEST,
    )
    await _policy(
        db_session, instance, family=AnomalyFamily.LOG_SOURCE,
        source_id=record_source.id, detector="VOLUME_SPIKE", mode=AlertPolicyMode.RECORD_ONLY,
    )
    await _policy(
        db_session, instance, family=AnomalyFamily.LOG_SOURCE,
        source_id=outage_record.id, detector="NO_EVENTS", mode=AlertPolicyMode.DIGEST,
    )
    await _policy(
        db_session, instance, family=AnomalyFamily.LOG_SOURCE,
        source_id=outage_alert.id, detector="NO_EVENTS", mode=AlertPolicyMode.DIGEST,
    )
    db_session.add(
        OutageAlertPolicy(
            scope_kind=OutagePolicyScope.SOURCE.value,
            scope_value=str(outage_record.id),
            mode=OutagePolicyMode.RECORD_ONLY.value,
        )
    )
    await db_session.flush()

    capture = Capture()
    [result] = await run_due_digests(
        db_session, now=NOW, settings=_settings(), deliver=capture
    )
    listed = {(item.source_name, item.detector) for item in result.items}
    assert listed == {
        ("digest-src", "VOLUME_SPIKE"),
        ("outage-alert", "NO_EVENTS"),
    }


async def test_the_window_is_fixed_to_the_digest_hour_not_the_tick(
    db_session, monkeypatch
) -> None:
    """A 09:30 tick covers exactly D-1 07:00 to D 07:00, and the edges are half-open."""
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "ops@example.org")
    instance = await make_instance(db_session)
    edges = {
        "before-start": datetime(2026, 11, 5, 6, 59, tzinfo=UTC),
        "at-start": datetime(2026, 11, 5, 7, 0, tzinfo=UTC),
        "before-end": datetime(2026, 11, 6, 6, 59, tzinfo=UTC),
        "at-end": datetime(2026, 11, 6, 7, 0, tzinfo=UTC),
    }
    for index, (name, at) in enumerate(edges.items()):
        source = await make_log_source(
            db_session, instance, qradar_id=index + 1, name=name
        )
        await _phase_a(db_session, instance, source, at=at)
    await _policy(
        db_session,
        instance,
        family=AnomalyFamily.LOG_SOURCE,
        source_id=uuid.uuid4(),
        detector="VOLUME_SPIKE",
        mode=AlertPolicyMode.DIGEST,
        scope=AlertPolicyScope.CLASS,
        value="UNCLASSIFIED",
    )

    capture = Capture()
    [result] = await run_due_digests(
        db_session,
        now=datetime(2026, 11, 6, 9, 30, tzinfo=UTC),
        settings=_settings(digest_hour_utc=7),
        deliver=capture,
    )
    assert result.digest_date == date(2026, 11, 6)
    assert {item.source_name for item in result.items} == {"at-start", "before-end"}


async def test_maintenance_suppressed_incidents_are_not_listed(
    db_session, monkeypatch
) -> None:
    """A suppressed incident would not have alerted, so it must not be digested."""
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "ops@example.org")
    instance = await make_instance(db_session)
    muted = await make_log_source(db_session, instance, qradar_id=1, name="muted")
    mixed = await make_log_source(db_session, instance, qradar_id=2, name="mixed")
    await _phase_a(
        db_session, instance, muted, at=datetime(2026, 11, 5, 8, 0, tzinfo=UTC)
    )
    await _phase_a(
        db_session,
        instance,
        mixed,
        count=2,
        at=datetime(2026, 11, 5, 9, 0, tzinfo=UTC),
    )
    muted_rows = list(
        (
            await db_session.scalars(
                select(LogSourceAnomaly).where(
                    LogSourceAnomaly.log_source_id == muted.id
                )
            )
        ).all()
    )
    muted_rows[0].suppressed = True
    mixed_rows = list(
        (
            await db_session.scalars(
                select(LogSourceAnomaly)
                .where(LogSourceAnomaly.log_source_id == mixed.id)
                .order_by(LogSourceAnomaly.detected_at)
            )
        ).all()
    )
    mixed_rows[0].suppressed = True
    await db_session.flush()
    await _policy(
        db_session,
        instance,
        family=AnomalyFamily.LOG_SOURCE,
        source_id=uuid.uuid4(),
        detector="VOLUME_SPIKE",
        mode=AlertPolicyMode.DIGEST,
        scope=AlertPolicyScope.CLASS,
        value="UNCLASSIFIED",
    )

    capture = Capture()
    [result] = await run_due_digests(
        db_session, now=NOW, settings=_settings(), deliver=capture
    )
    assert {item.source_name: item.count for item in result.items} == {"mixed": 1}


async def test_a_family_switch_off_keeps_its_incidents_out(db_session, monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "ops@example.org")
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1, name="waf-off")
    endpoint = WafEndpoint(
        instance_id=instance.id,
        log_source_id=source.id,
        normalized_endpoint="/login",
        http_method="POST",
        normalizer_version=1,
        first_seen_at=NOW - timedelta(days=30),
        last_seen_at=NOW,
        is_active=True,
    )
    db_session.add(endpoint)
    await db_session.flush()
    db_session.add(
        WafAnomaly(
            waf_endpoint_id=endpoint.id,
            detector_type="WAF_REQUEST_RATE_SPIKE",
            severity="HIGH",
            state="OPEN",
            detected_at=NOW - timedelta(hours=1),
            anomaly_start=NOW - timedelta(hours=1),
            anomaly_end=NOW - timedelta(hours=1) + timedelta(minutes=5),
            deviation_ratio=7.0,
        )
    )
    await _policy(
        db_session, instance, family=AnomalyFamily.WAF,
        source_id=source.id, detector="WAF_REQUEST_RATE_SPIKE",
        mode=AlertPolicyMode.DIGEST,
    )
    await db_session.flush()

    capture = Capture()
    [result] = await run_due_digests(
        db_session,
        now=NOW,
        settings=_settings(waf_alerting_enabled=False),
        deliver=capture,
    )
    assert result.state is AlertDigestState.EMPTY and result.items == ()
    assert capture.messages == []


# --------------------------------------------------------------- top rows
async def test_top_fifty_rows_then_the_number_of_more_sources(
    db_session, monkeypatch
) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "ops@example.org")
    instance = await make_instance(db_session)
    for index in range(52):
        source = await make_log_source(
            db_session, instance, qradar_id=100 + index, name=f"src-{index:02d}"
        )
        db_session.add(
            LogSourceClassification(
                log_source_id=source.id,
                source_class="FIREWALL",
                basis="NAME_REGEX",
                conflicts=[],
                classified_at=utc(),
                review_status="REVIEWED",
            )
        )
        await _phase_a(
            db_session,
            instance,
            source,
            count=index + 1,
            at=datetime(2026, 11, 5, 8, 0, tzinfo=UTC),
        )
    await db_session.flush()
    await _policy(
        db_session,
        instance,
        family=AnomalyFamily.LOG_SOURCE,
        source_id=uuid.uuid4(),
        detector="VOLUME_SPIKE",
        mode=AlertPolicyMode.DIGEST,
        scope=AlertPolicyScope.CLASS,
        value="FIREWALL",
    )

    capture = Capture()
    [result] = await run_due_digests(
        db_session, now=NOW, settings=_settings(), deliver=capture
    )
    assert result.item_count == 52 and len(result.items) == 52
    body = _html(capture.messages[0])
    assert "and 2 more sources are not listed." in body
    assert body.count("src-") == 50


# ------------------------------------------------------------------ states
async def test_not_configured_records_the_state_and_sends_nothing(
    db_session, monkeypatch
) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "ops@example.org")
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1, name="src")
    await _phase_a(db_session, instance, source)
    await _policy(
        db_session, instance, family=AnomalyFamily.LOG_SOURCE,
        source_id=source.id, detector="VOLUME_SPIKE", mode=AlertPolicyMode.DIGEST,
    )
    capture = Capture()
    [result] = await run_due_digests(
        db_session, now=NOW, settings=_settings(smtp_host=""), deliver=capture
    )
    assert result.state is AlertDigestState.NOT_CONFIGURED
    assert result.item_count == 1 and result.recipient_count == 1
    assert capture.messages == []
    row = await db_session.scalar(select(AlertDigestRun))
    assert row is not None and row.state == AlertDigestState.NOT_CONFIGURED.value


async def test_no_recipients_records_the_state(db_session, monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "")
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1, name="src")
    await _phase_a(db_session, instance, source)
    await _policy(
        db_session, instance, family=AnomalyFamily.LOG_SOURCE,
        source_id=source.id, detector="VOLUME_SPIKE", mode=AlertPolicyMode.DIGEST,
    )
    capture = Capture()
    [result] = await run_due_digests(
        db_session, now=NOW, settings=_settings(), deliver=capture
    )
    assert result.state is AlertDigestState.NO_RECIPIENTS and result.recipient_count == 0
    assert capture.messages == []


async def test_empty_records_the_state_without_a_mail(db_session, monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "ops@example.org")
    await make_instance(db_session)
    capture = Capture()
    [result] = await run_due_digests(
        db_session, now=NOW, settings=_settings(), deliver=capture
    )
    assert result.state is AlertDigestState.EMPTY and result.item_count == 0
    assert capture.messages == []


async def test_failed_records_only_the_delivery_code(db_session, monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "ops@example.org")
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1, name="src")
    await _phase_a(db_session, instance, source)
    await _policy(
        db_session, instance, family=AnomalyFamily.LOG_SOURCE,
        source_id=source.id, detector="VOLUME_SPIKE", mode=AlertPolicyMode.DIGEST,
    )
    capture = Capture(fail="recipient_refused")
    [result] = await run_due_digests(
        db_session, now=NOW, settings=_settings(), deliver=capture
    )
    assert result.state is AlertDigestState.FAILED
    assert result.error_code == "recipient_refused"
    row = await db_session.scalar(select(AlertDigestRun))
    assert row is not None and row.error_code == "recipient_refused"


# ------------------------------------------------------------- idempotency
async def test_a_second_tick_on_the_same_day_sends_nothing(db_session, monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "ops@example.org")
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1, name="src")
    await _phase_a(db_session, instance, source)
    await _policy(
        db_session, instance, family=AnomalyFamily.LOG_SOURCE,
        source_id=source.id, detector="VOLUME_SPIKE", mode=AlertPolicyMode.DIGEST,
    )
    capture = Capture()
    first = await run_due_digests(
        db_session, now=NOW, settings=_settings(), deliver=capture
    )
    second = await run_due_digests(
        db_session, now=NOW + timedelta(minutes=5), settings=_settings(), deliver=capture
    )
    assert len(first) == 1 and second == [] and len(capture.messages) == 1
    assert await db_session.scalar(
        select(func.count()).select_from(AlertDigestRun)
    ) == 1


async def test_two_overlapping_ticks_send_one_digest(db_schema, monkeypatch) -> None:
    """Two concurrent ticks: the lock holder sends once, the other sends nothing."""
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "ops@example.org")
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as setup:
            instance = await make_instance(setup)
            source = await make_log_source(setup, instance, qradar_id=1, name="src")
            await _phase_a(
                setup, instance, source, at=datetime(2026, 11, 5, 8, 0, tzinfo=UTC)
            )
            await _policy(
                setup,
                instance,
                family=AnomalyFamily.LOG_SOURCE,
                source_id=source.id,
                detector="VOLUME_SPIKE",
                mode=AlertPolicyMode.DIGEST,
            )
            await setup.commit()

        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        release = threading.Event()
        messages: list[object] = []

        def blocking_deliver(_settings: Settings, message: object) -> None:
            messages.append(message)
            loop.call_soon_threadsafe(entered.set)
            release.wait(10)

        async def tick(deliver) -> list:  # type: ignore[no-untyped-def]
            async with maker() as session:
                return await run_due_digests(
                    session, now=NOW, settings=_settings(), deliver=deliver
                )

        first = asyncio.create_task(tick(blocking_deliver))
        await asyncio.wait_for(entered.wait(), 10)
        # The first tick is inside delivery and holds the instance's lock.
        second = await asyncio.wait_for(tick(Capture()), 10)
        assert second == []
        release.set()
        first_results = await asyncio.wait_for(first, 10)
        assert len(first_results) == 1
        assert len(messages) == 1

        async with maker() as session:
            assert (
                await session.scalar(select(func.count()).select_from(AlertDigestRun))
                == 1
            )
    finally:
        await engine.dispose()


async def test_not_due_before_the_hour(db_session, monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "ops@example.org")
    await make_instance(db_session)
    capture = Capture()
    before = await run_due_digests(
        db_session,
        now=datetime(2026, 11, 6, 6, 59, tzinfo=UTC),
        settings=_settings(digest_hour_utc=7),
        deliver=capture,
    )
    assert before == [] and capture.messages == []
    assert await db_session.scalar(
        select(func.count()).select_from(AlertDigestRun)
    ) == 0

    due = await run_due_digests(
        db_session,
        now=datetime(2026, 11, 6, 0, 30, tzinfo=UTC),
        settings=_settings(digest_hour_utc=0),
        deliver=capture,
    )
    assert len(due) == 1 and due[0].state is AlertDigestState.EMPTY


# -------------------------------------------------------------------- cost
async def test_one_instance_run_costs_twelve_statements_at_two_fleet_sizes(
    db_session, monkeypatch
) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "")
    await _recipient(db_session, "soc@example.org")

    async def statements_for(fleet_size: int) -> int:
        instance = await make_instance(db_session)
        target = await make_log_source(
            db_session, instance, qradar_id=5000 + fleet_size, name=f"target-{fleet_size}"
        )
        await _phase_a(db_session, instance, target)
        await _policy(
            db_session, instance, family=AnomalyFamily.LOG_SOURCE,
            source_id=target.id, detector="VOLUME_SPIKE", mode=AlertPolicyMode.DIGEST,
        )
        for extra in range(fleet_size - 1):
            await make_log_source(
                db_session, instance, qradar_id=5100 + extra, name=f"extra-{extra}"
            )
        service = DigestService(
            db_session, settings=_settings(), clock=lambda: NOW, deliver=lambda *_: None
        )
        with counting(db_session) as statements:
            await service.run_for_instance(instance)
        return len(statements)

    # The thirteenth statement is the rule-family grouped read (SOC-6c, design
    # §5.7). The fourteenth is the identity-family grouped read (SOC-10c). Both
    # run at every fleet size, including while that family's alerting is off.
    assert await statements_for(1) == await statements_for(4) == 14


# ------------------------------------------------------- recipient union
async def test_recipients_are_the_env_and_enabled_stored_union(
    db_session, monkeypatch
) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "ops@example.org, SOC@example.org")
    instance = await make_instance(db_session)
    await _recipient(db_session, "stored@example.org", alert_types=["waf_endpoint"])
    await _recipient(db_session, "disabled@example.org", enabled=False)
    source = await make_log_source(db_session, instance, qradar_id=1, name="src")
    await _phase_a(db_session, instance, source)
    await _policy(
        db_session, instance, family=AnomalyFamily.LOG_SOURCE,
        source_id=source.id, detector="VOLUME_SPIKE", mode=AlertPolicyMode.DIGEST,
    )

    capture = Capture()
    [result] = await run_due_digests(
        db_session, now=NOW, settings=_settings(), deliver=capture
    )
    assert result.recipient_count == 3
    assert capture.messages[0]["To"] == (  # type: ignore[index]
        "ops@example.org, soc@example.org, stored@example.org"
    )


# ---------------------------------------------------------------- template
async def test_the_stored_digest_template_is_used_not_the_alert_default(
    db_session, monkeypatch
) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "ops@example.org")
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1, name="src")
    await _phase_a(db_session, instance, source)
    await _policy(
        db_session, instance, family=AnomalyFamily.LOG_SOURCE,
        source_id=source.id, detector="VOLUME_SPIKE", mode=AlertPolicyMode.DIGEST,
    )
    db_session.add_all(
        [
            EmailTemplate(
                alert_type=None,
                subject="[Alert] must not be used",
                html_body="<p>alert default</p>",
                enabled=True,
            ),
            EmailTemplate(
                alert_type="digest",
                subject="Digest for {{ instance.name }}",
                html_body="<p>custom digest preamble</p>",
                text_body="custom digest preamble",
                enabled=True,
            ),
        ]
    )
    await db_session.flush()

    capture = Capture()
    await run_due_digests(db_session, now=NOW, settings=_settings(), deliver=capture)
    subject = capture.messages[0]["Subject"]  # type: ignore[index]
    body = _html(capture.messages[0])
    assert subject.startswith("Digest for ") and "[Alert]" not in subject
    assert "custom digest preamble" in body and "alert default" not in body


async def test_a_disabled_digest_template_falls_back_to_the_shipped_one(
    db_session, monkeypatch
) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "ops@example.org")
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1, name="src")
    await _phase_a(db_session, instance, source)
    await _policy(
        db_session, instance, family=AnomalyFamily.LOG_SOURCE,
        source_id=source.id, detector="VOLUME_SPIKE", mode=AlertPolicyMode.DIGEST,
    )
    db_session.add(
        EmailTemplate(
            alert_type="digest",
            subject="disabled digest",
            html_body="<p>disabled</p>",
            enabled=False,
        )
    )
    await db_session.flush()

    capture = Capture()
    await run_due_digests(db_session, now=NOW, settings=_settings(), deliver=capture)
    subject = capture.messages[0]["Subject"]  # type: ignore[index]
    assert subject == f"Daily digest: {instance.name}"


# ------------------------------------------------------- task registration
async def test_the_digest_task_and_its_beat_entry_are_registered() -> None:
    """The task is registered and the beat entry is the 300 s interval-gated one."""
    from app.workers import tasks  # noqa: F401 - importing registers the tasks
    from app.workers.celery_app import celery_app

    assert "run_alert_digest" in celery_app.tasks
    entry = celery_app.conf.beat_schedule["run-alert-digest"]
    assert entry["task"] == "run_alert_digest" and entry["schedule"] == 300.0


async def _rule_digest_incident(session, instance, name: str) -> AnalyticsRule:
    """One open silence inside the digest window, listed only under a DIGEST policy."""
    rule = AnalyticsRule(
        instance_id=instance.id,
        qradar_id=60,
        name=name,
        rule_type="EVENT",
        enabled=True,
    )
    session.add(rule)
    await session.flush()
    session.add(
        RuleAnomaly(
            rule_id=rule.id,
            qradar_id=rule.qradar_id,
            detector_type="RULE_SILENCE",
            fingerprint=f"rule_anomaly:{rule.id.hex}",
            severity=Severity.MEDIUM.value,
            state="OPEN",
            detected_at=datetime(2026, 11, 5, 12, 0, tzinfo=UTC),
            anomaly_start=datetime(2026, 11, 5, 9, 0, tzinfo=UTC),
            anomaly_end=datetime(2026, 11, 5, 12, 0, tzinfo=UTC),
            deviation_ratio=0.0,
        )
    )
    await _policy(
        session,
        instance,
        family=AnomalyFamily.RULE,
        source_id=rule.id,
        detector="RULE_SILENCE",
        mode=AlertPolicyMode.DIGEST,
    )
    return rule


async def test_rule_rows_are_dropped_unless_the_family_switch_is_on(db_session) -> None:
    """The grouped read runs either way. The row is listed only when the switch is on."""
    await _recipient(db_session, "soc@example.org")
    quiet = await make_instance(db_session)
    paging = await make_instance(db_session)
    await _rule_digest_incident(db_session, quiet, "quiet-rule")
    listed = await _rule_digest_incident(db_session, paging, "paging-rule")

    off = await DigestService(
        db_session, settings=_settings(), clock=lambda: NOW, deliver=lambda *_: None
    ).run_for_instance(quiet)
    assert off is not None and off.item_count == 0
    assert all(item.family is not AnomalyFamily.RULE for item in off.items)

    on = await DigestService(
        db_session,
        settings=_settings(rule_alerting_enabled=True),
        clock=lambda: NOW,
        deliver=lambda *_: None,
    ).run_for_instance(paging)
    assert on is not None and on.item_count == 1
    item = on.items[0]
    assert item.family is AnomalyFamily.RULE
    assert item.detector == "RULE_SILENCE"
    assert item.source_name == "paging-rule"
    assert item.source_class is None
    link = item.link("https://soc.example")
    assert "subject_kind=RULE" in link and f"rule_id={listed.id}" in link
