"""Custom-rule alerting: fingerprint, shadow mode, and the alert batch (F-2).

The fold still decides when a breach opens or recovers. These tests pin what
that decision becomes once paging is on, and that shadow mode compiles nothing.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.fingerprint import (
    anomaly_fingerprint,
    catchall_sender_fingerprint,
    custom_rule_breach_fingerprint,
    inventory_fingerprint,
    ips_anomaly_fingerprint,
    search_failure_fingerprint,
    search_threshold_fingerprint,
    waf_anomaly_fingerprint,
)
from app.alerts.service import AlertResult
from app.core.config import Settings, get_settings
from app.custom_rules.alerts import (
    ALERTING_MODE_ALERTING,
    ALERTING_MODE_FIELD,
    ALERTING_MODE_SHADOW,
    EVALUATOR_ACTOR,
    REASON_DISABLED,
    REASON_RECOVERED,
    REASON_RETIRED,
    SOURCE_TYPE,
    AlertingCustomRuleSink,
    NoOpCustomRuleAlertSink,
    alerting_mode,
    compile_batch,
    extra_resolve_reason,
    resolve_deleted_rule_alert,
)
from app.custom_rules.engine import CustomRuleEngine, EvaluationReport
from app.models.alert import Alert
from app.models.enums import AlertStatus, AlertTransition, CustomRuleMetric, Severity
from app.workers import tasks
from tests.unit.test_custom_rule_evaluate import _at, _fold, _live, _reading

_PINNED_RULE = uuid.UUID("00000000-0000-0000-0000-0000000000f2")
_PINNED_FINGERPRINT = "custom_rule_breach:b77528a0664e5d8f55bd631f47eab7df"
_EVIDENCE_KEYS = {
    "observed_value",
    "measurement",
    "threshold",
    "operator",
    "first_bucket_start",
    "last_bucket_start",
    "unmeasured_buckets",
}
_NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def test_custom_rule_fingerprint_does_not_collide_with_existing_kinds() -> None:
    """One id, every fingerprint family. A shared prefix would dedup into the wrong page."""
    rule_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    others = {
        anomaly_fingerprint(rule_id, "VOLUME_DROP"),
        inventory_fingerprint(instance_id, 7),
        catchall_sender_fingerprint(instance_id, rule_id, "sender-a"),
        waf_anomaly_fingerprint(
            instance_id=instance_id,
            log_source_id=rule_id,
            virtual_host=None,
            normalized_endpoint="/login",
            http_method="GET",
            normalizer_version=1,
            detector_type="WAF_VOLUME_SPIKE",
        ),
        ips_anomaly_fingerprint(
            instance_id=instance_id,
            log_source_id=rule_id,
            detector_type="IPS_EVENT_VOLUME_SPIKE",
        ),
        ips_anomaly_fingerprint(
            instance_id=instance_id,
            log_source_id=rule_id,
            detector_type="IPS_ENTITY_VOLUME_SPIKE",
            dimension="destination_port",
            value_key="3389/tcp",
        ),
        search_threshold_fingerprint(rule_id),
        search_failure_fingerprint(rule_id),
    }
    breach = custom_rule_breach_fingerprint(rule_id)
    assert breach.startswith("custom_rule_breach:")
    assert not breach.startswith("custom_rule:")
    assert breach not in others
    assert len(others) == 8
    assert all(not item.startswith("custom_rule_breach:") for item in others)


def test_custom_rule_fingerprint_is_pinned_for_string_and_uuid() -> None:
    assert custom_rule_breach_fingerprint(_PINNED_RULE) == _PINNED_FINGERPRINT
    assert custom_rule_breach_fingerprint(str(_PINNED_RULE)) == _PINNED_FINGERPRINT
    assert custom_rule_breach_fingerprint(_PINNED_RULE) != custom_rule_breach_fingerprint(
        uuid.uuid4()
    )


def test_alerting_mode_is_a_computed_field_and_defaults_to_shadow() -> None:
    assert ALERTING_MODE_FIELD == "alerting_mode"
    assert alerting_mode(False) == ALERTING_MODE_SHADOW
    assert alerting_mode(True) == ALERTING_MODE_ALERTING
    assert Settings.model_fields["custom_rules_alerting_enabled"].default is False


def test_shadow_keeps_the_breach_and_compiles_no_alert() -> None:
    opened = _fold(readings=(_reading(0, 150),))
    assert opened.breach is not None
    assert opened.breach.state == "OPEN"
    assert [event.action for event in opened.events] == ["open"]
    assert compile_batch(opened.events, alerting_enabled=False) is None
    assert (
        extra_resolve_reason(
            "DISABLED",
            "the rule is disabled",
            breach_open=True,
            alerting_enabled=False,
        )
        is None
    )
    assert (
        extra_resolve_reason("OK", None, breach_open=False, alerting_enabled=False) is None
    )


def test_one_reading_does_not_open_before_sustain_and_two_do() -> None:
    waiting = _fold(sustain_buckets=2, readings=(_reading(0, 150),))
    assert waiting.events == ()
    waiting_batch = compile_batch(waiting.events, alerting_enabled=True)
    assert waiting_batch is not None
    assert waiting_batch.opens == ()

    opened = _fold(sustain_buckets=2, readings=(_reading(0, 150), _reading(5, 160)))
    batch = compile_batch(opened.events, alerting_enabled=True)
    assert batch is not None
    assert [event.action for event in opened.events] == ["open"]
    assert len(batch.opens) == 1
    assert batch.resolves == ()
    item = batch.opens[0]
    assert item.source_type == SOURCE_TYPE
    assert item.source_id == opened.events[0].rule_id
    assert item.severity is Severity.HIGH
    assert item.title == "high eps crossed its threshold"
    assert item.fingerprint == custom_rule_breach_fingerprint(opened.events[0].rule_id)
    assert set(item.evidence or {}) == _EVIDENCE_KEYS
    assert "breach_id" not in (item.evidence or {})
    assert item.evidence is not None
    assert item.evidence["observed_value"] == 160.0
    assert item.evidence["measurement"] == "EXACT"
    assert item.evidence["threshold"] == 100.0
    assert item.evidence["operator"] == "GT"
    assert item.evidence["first_bucket_start"] == _at(5).isoformat()
    assert item.evidence["last_bucket_start"] == _at(5).isoformat()
    assert item.evidence["unmeasured_buckets"] == 0
    assert item.context == {
        "rule": "high eps",
        "subject_kind": "LOG_SOURCE",
        "subject_label": "edge-proxy",
        "metric": "event_count (events/bucket)",
    }
    assert item.description == (
        "high eps observed 160 events/bucket (GT 100 events/bucket)"
    )


def test_refresh_bumps_without_a_resolve_and_recovery_names_the_condition() -> None:
    opened = _fold(sustain_buckets=1, readings=(_reading(0, 150),))
    refreshed = _fold(
        breach=opened.breach,
        consecutive_breaching=opened.consecutive_breaching,
        last_evaluated_bucket_start=opened.last_evaluated_bucket_start,
        readings=(_reading(5, 170),),
    )
    assert [event.action for event in refreshed.events] == ["refresh"]
    refresh_batch = compile_batch(refreshed.events, alerting_enabled=True)
    assert refresh_batch is not None
    assert len(refresh_batch.opens) == 1
    assert refresh_batch.resolves == ()

    recovered = _fold(
        recovery_buckets=2,
        breach=opened.breach,
        consecutive_breaching=opened.consecutive_breaching,
        last_evaluated_bucket_start=opened.last_evaluated_bucket_start,
        readings=(_reading(5, 1), _reading(10, 1)),
    )
    assert [event.action for event in recovered.events] == ["resolve"]
    assert recovered.events[0].reason == REASON_RECOVERED
    batch = compile_batch(recovered.events, alerting_enabled=True)
    assert batch is not None
    assert batch.opens == ()
    assert len(batch.resolves) == 1
    assert batch.resolves[0].actor == EVALUATOR_ACTOR
    assert batch.resolves[0].reason == REASON_RECOVERED
    assert batch.resolves[0].fingerprint == custom_rule_breach_fingerprint(
        recovered.events[0].rule_id
    )


def test_open_and_resolve_in_one_batch_keep_both() -> None:
    both = _fold(
        sustain_buckets=1,
        recovery_buckets=1,
        readings=(_reading(0, 150), _reading(5, 10)),
    )
    assert [event.action for event in both.events] == ["open", "resolve"]
    batch = compile_batch(both.events, alerting_enabled=True)
    assert batch is not None
    assert len(batch.opens) == 1
    assert len(batch.resolves) == 1
    assert batch.opens[0].fingerprint == batch.resolves[0].fingerprint


def test_an_unknown_action_is_not_an_alert() -> None:
    opened = _fold(readings=(_reading(0, 150),))
    batch = compile_batch(
        (replace(opened.events[0], action="explode"),),
        alerting_enabled=True,
    )
    assert batch is not None
    assert batch.opens == ()
    assert batch.resolves == ()


def test_lower_bound_above_a_gt_line_pages_and_one_below_does_not_resolve() -> None:
    opened = _fold(
        metric=CustomRuleMetric.REQUEST_COUNT,
        readings=(_reading(0, 150, "LOWER_BOUND"),),
    )
    batch = compile_batch(opened.events, alerting_enabled=True)
    assert batch is not None
    assert len(batch.opens) == 1
    assert batch.opens[0].evidence is not None
    assert batch.opens[0].evidence["measurement"] == "LOWER_BOUND"
    assert batch.opens[0].description == (
        "high eps observed at least 150 requests/bucket (GT 100 requests/bucket)"
    )
    assert "at least" in (batch.opens[0].description or "")

    held = _fold(
        metric=CustomRuleMetric.REQUEST_COUNT,
        breach=opened.breach,
        consecutive_breaching=opened.consecutive_breaching,
        last_evaluated_bucket_start=opened.last_evaluated_bucket_start,
        readings=(_reading(5, 1, "LOWER_BOUND"),),
    )
    assert held.breach is not None
    assert held.breach.state == "OPEN"
    assert held.events == ()
    assert (
        extra_resolve_reason(
            held.status,
            held.status_reason,
            breach_open=True,
            alerting_enabled=True,
        )
        is None
    )
    held_batch = compile_batch(held.events, alerting_enabled=True)
    assert held_batch is not None
    assert held_batch.resolves == ()


def test_maintenance_opens_nothing_and_a_live_breach_is_kept() -> None:
    quiet = _fold(readings=(_reading(0, 10_000, maintenance=True),))
    assert quiet.breach is None
    assert quiet.events == ()
    quiet_batch = compile_batch(quiet.events, alerting_enabled=True)
    assert quiet_batch is not None
    assert quiet_batch.opens == ()

    live = _live()
    held = _fold(
        breach=live,
        consecutive_breaching=2,
        last_evaluated_bucket_start=_at(0),
        readings=(_reading(5, 0, maintenance=True), _reading(10, 10_000, maintenance=True)),
    )
    assert held.breach is not None
    assert held.breach.state == "OPEN"
    assert held.events == ()
    assert (
        extra_resolve_reason(
            held.status,
            held.status_reason,
            breach_open=True,
            alerting_enabled=True,
        )
        is None
    )


def test_a_disabled_rule_resolves_its_page_and_keeps_the_breach() -> None:
    live = _live()
    result = _fold(
        enabled=False,
        breach=live,
        consecutive_breaching=4,
        consecutive_under=1,
        last_evaluated_bucket_start=_at(0),
        readings=(_reading(5, 0),),
    )
    assert result.status == "DISABLED"
    assert result.breach is not None
    assert result.breach.state == "OPEN"
    assert result.events == ()
    assert (
        extra_resolve_reason(
            result.status,
            result.status_reason,
            breach_open=True,
            alerting_enabled=True,
        )
        == REASON_DISABLED
    )
    # The disabled reason wins if a recovered resolve is also in the tick.
    event = replace(
        _fold(readings=(_reading(0, 150),)).events[0],
        action="resolve",
        reason=REASON_DISABLED,
    )
    later = replace(event, reason=REASON_RECOVERED)
    batch = compile_batch((event, later), alerting_enabled=True)
    assert batch is not None
    assert len(batch.resolves) == 1
    assert batch.resolves[0].reason == REASON_DISABLED
    assert batch.resolves[0].actor == EVALUATOR_ACTOR


def test_a_retired_subject_uses_the_fold_reason() -> None:
    live = _live()
    reason = "the endpoint was retired at 2026-07-01T00:00:00+00:00"
    result = _fold(
        retired=True,
        retired_reason=reason,
        breach=live,
        consecutive_breaching=2,
        readings=(_reading(0, 0),),
    )
    assert result.status == "SUBJECT_RETIRED"
    assert result.breach is not None
    assert result.breach.state == "OPEN"
    assert result.events == ()
    assert (
        extra_resolve_reason(
            result.status,
            result.status_reason,
            breach_open=True,
            alerting_enabled=True,
        )
        == reason
    )
    assert (
        extra_resolve_reason(
            "SUBJECT_RETIRED",
            None,
            breach_open=True,
            alerting_enabled=True,
        )
        == REASON_RETIRED
    )
    assert reason != REASON_RECOVERED


def test_stale_and_not_collected_keep_a_live_page() -> None:
    live = _live()
    stale = _fold(
        breach=live,
        consecutive_breaching=2,
        last_evaluated_bucket_start=_at(0),
        collection_stale_reason="no bucket collected for this kind since 2026-07-15 10:00 UTC",
        readings=(_reading(5, 0),),
    )
    assert stale.status == "STALE"
    assert stale.breach is not None
    assert stale.breach.state == "OPEN"
    assert stale.events == ()
    assert stale.consecutive_breaching == 2
    assert (
        extra_resolve_reason(
            stale.status,
            stale.status_reason,
            breach_open=True,
            alerting_enabled=True,
        )
        is None
    )

    missing = _fold(
        family_collected=False,
        family_reason="waf_collection_enabled is off",
        breach=live,
        consecutive_breaching=2,
        readings=(_reading(5, 0),),
    )
    assert missing.status == "NOT_COLLECTED"
    assert missing.breach is not None
    assert missing.events == ()
    assert (
        extra_resolve_reason(
            missing.status,
            missing.status_reason,
            breach_open=True,
            alerting_enabled=True,
        )
        is None
    )


def test_a_rule_with_no_live_breach_closes_a_page_left_open_in_shadow() -> None:
    assert (
        extra_resolve_reason("OK", None, breach_open=False, alerting_enabled=True)
        == REASON_RECOVERED
    )
    assert (
        extra_resolve_reason("NO_VERDICT", None, breach_open=False, alerting_enabled=True)
        == REASON_RECOVERED
    )


class _Enqueuer:
    def __init__(self) -> None:
        self.calls: list[AlertTransition] = []

    async def enqueue(self, alert: Alert, transition: AlertTransition) -> list[object]:
        self.calls.append(transition)
        return []


class _FakeAlerts:
    def __init__(self) -> None:
        self.by_fingerprint: dict[str, Alert] = {}

    async def apply_bulk(self, open_or_updates, resolutions):  # type: ignore[no-untyped-def]
        opened: list[AlertResult] = []
        for data in open_or_updates:
            existing = self.by_fingerprint.get(data.fingerprint)
            if existing is None:
                alert = Alert(
                    id=uuid.uuid4(),
                    dedup_key=data.fingerprint,
                    fingerprint=data.fingerprint,
                    title=data.title,
                    severity=data.severity,
                    status=AlertStatus.OPEN,
                    source_type=data.source_type,
                    source_id=data.source_id,
                    context=data.context or {},
                    opened_at=_NOW,
                    first_seen_at=_NOW,
                    last_seen_at=_NOW,
                    occurrence_count=1,
                    evidence_snapshot=data.evidence or {},
                    source_anomaly_ids=[],
                    created_at=_NOW,
                    updated_at=_NOW,
                )
                self.by_fingerprint[data.fingerprint] = alert
                opened.append(
                    AlertResult(
                        alert=alert,
                        opened=True,
                        updated=False,
                        transition=AlertTransition.OPENED,
                    )
                )
            else:
                existing.occurrence_count += 1
                opened.append(
                    AlertResult(
                        alert=existing, opened=False, updated=True, transition=None
                    )
                )
        resolved: list[AlertResult | None] = []
        for item in resolutions:
            current = self.by_fingerprint.pop(item.fingerprint, None)
            if current is None:
                resolved.append(None)
                continue
            current.status = AlertStatus.RESOLVED
            resolved.append(
                AlertResult(
                    alert=current,
                    opened=False,
                    updated=True,
                    transition=AlertTransition.RESOLVED,
                )
            )
        return opened, resolved


def _sink(fake: _FakeAlerts, enqueuer: _Enqueuer) -> AlertingCustomRuleSink:
    return AlertingCustomRuleSink(
        object(),  # type: ignore[arg-type]
        enqueuer=enqueuer,
        alerts=fake,
        scan_deleted=False,
    )


async def test_only_a_real_transition_is_enqueued() -> None:
    opened = _fold(readings=(_reading(0, 150),))
    event = opened.events[0]
    fake = _FakeAlerts()
    enqueuer = _Enqueuer()
    sink = _sink(fake, enqueuer)

    fingerprint = custom_rule_breach_fingerprint(event.rule_id)
    await sink.open_or_update(event)
    await sink.flush()
    assert enqueuer.calls == [AlertTransition.OPENED]
    assert fake.by_fingerprint[fingerprint].occurrence_count == 1

    await sink.open_or_update(replace(event, action="refresh"))
    await sink.flush()
    assert enqueuer.calls == [AlertTransition.OPENED]
    assert fake.by_fingerprint[fingerprint].occurrence_count == 2

    await sink.resolve(replace(event, action="resolve", reason=REASON_RECOVERED))
    await sink.flush()
    assert enqueuer.calls == [AlertTransition.OPENED, AlertTransition.RESOLVED]
    assert fingerprint not in fake.by_fingerprint


async def test_one_flush_opens_before_it_resolves() -> None:
    """Same-tick open and resolve must page both. Resolving first would drop the open."""
    both = _fold(
        sustain_buckets=1,
        recovery_buckets=1,
        readings=(_reading(0, 150), _reading(5, 10)),
    )
    fake = _FakeAlerts()
    enqueuer = _Enqueuer()
    sink = _sink(fake, enqueuer)
    for event in both.events:
        if event.action == "resolve":
            await sink.resolve(event)
        else:
            await sink.open_or_update(event)
    await sink.flush()
    assert enqueuer.calls == [AlertTransition.OPENED, AlertTransition.RESOLVED]
    assert fake.by_fingerprint == {}


async def test_a_blank_actor_is_rejected_before_the_session_is_used() -> None:
    class _Session:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(name)

    with pytest.raises(ValueError, match="actor is required"):
        await resolve_deleted_rule_alert(_Session(), uuid.uuid4(), actor="   ")  # type: ignore[arg-type]


def test_the_engine_uses_the_noop_sink_until_alerting_is_on() -> None:
    shadow = get_settings().model_copy(
        update={"custom_rules_enabled": True, "custom_rules_alerting_enabled": False}
    )
    engine = CustomRuleEngine(object(), shadow)  # type: ignore[arg-type]
    assert isinstance(engine.sink, NoOpCustomRuleAlertSink)

    paging = get_settings().model_copy(
        update={"custom_rules_enabled": True, "custom_rules_alerting_enabled": True}
    )
    alerting = CustomRuleEngine(object(), paging)  # type: ignore[arg-type]
    assert isinstance(alerting.sink, AlertingCustomRuleSink)

    injected = NoOpCustomRuleAlertSink()
    kept = CustomRuleEngine(object(), paging, sink=injected)  # type: ignore[arg-type]
    assert kept.sink is injected


async def test_a_disabled_engine_reports_the_mode_without_a_query() -> None:
    settings = get_settings().model_copy(
        update={"custom_rules_enabled": False, "custom_rules_alerting_enabled": True}
    )
    report = await CustomRuleEngine(object(), settings).run()  # type: ignore[arg-type]
    assert report.status == "disabled"
    assert report.alerting_mode == ALERTING_MODE_ALERTING
    body = report.as_dict()
    assert body["alerting_mode"] == ALERTING_MODE_ALERTING
    assert body["opened"] == 0


async def test_the_beat_passes_a_dispatcher_when_rules_are_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def commit(self) -> None:
            return None

    captured: dict[str, object] = {}

    class _Engine:
        def __init__(self, session: object, settings: Settings, **kwargs: object) -> None:
            captured["enqueuer"] = kwargs.get("enqueuer")
            captured["settings"] = settings

        async def run(self) -> EvaluationReport:
            return EvaluationReport(status="ok", alerting_mode=ALERTING_MODE_SHADOW)

    settings = get_settings().model_copy(update={"custom_rules_enabled": True})
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: (lambda: _Session()))
    monkeypatch.setattr(tasks, "CustomRuleEngine", _Engine)

    body = await tasks.evaluate_custom_rules()
    assert isinstance(captured["enqueuer"], NotificationDispatcher)
    assert body["status"] == "ok"
    assert body["alerting_mode"] == ALERTING_MODE_SHADOW
