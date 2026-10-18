"""Property-watch incidents drive the generic alert lifecycle, behind `pw_alerting_enabled`.

PW-4 (design §G.10.1). The shape follows `test_ips_alerting.py`: shadow mode writes nothing,
an open raises one alert on the incident's fixed fingerprint, a still-anomalous bucket
refreshes it, a recovery resolves it. The timeline is the engine tests': collection runs for
real through the mock provider, one bucket per tick, with trained cells written directly.

Spike timeline of the rig (open after 2, resolve after 3): a spike on ticks 1 to 3 makes the
incident a CANDIDATE at tick 1, OPEN at tick 2 (the alert opens) and refreshes it at tick 3;
healthy ticks follow: RECOVERING at 4, RESOLVED at 6.
"""

from __future__ import annotations

import logging
import re
import uuid

import pytest
from sqlalchemy import select, update

from app.alerts.fingerprint import pw_anomaly_fingerprint
from app.models.alert import Alert
from app.models.enums import AlertStatus, PwAnomalyType
from app.models.property_watch import PropertyWatch, PwAnomaly, PwBucket, PwDetectorState
from app.property_watch.engine import PwAnomalyEngine
from app.property_watch.training import PwTrainingReport
from app.services.pw_pipeline import PwInstancePipeline
from tests.integration.pw_engine_support import (
    STEP,
    T0,
    Rig,
    add_cells,
    make_rig,
    now_of,
    plan_range,
)
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SPIKE = PwAnomalyType.PW_VALUE_SPIKE
ALERT_TABLES = re.compile(r"\balert(_notification)?\b")


class RecordingEnqueuer:
    """The enqueuer test double: records every (fingerprint, transition) pair."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def enqueue(self, alert, transition):  # type: ignore[no-untyped-def]
        self.calls.append((alert.fingerprint, str(transition)))
        return []


async def alerting_rig(
    session,  # type: ignore[no-untyped-def]
    *,
    values: tuple[str, ...] = ("alice",),
    alerting: bool = True,
    **overrides: object,
) -> Rig:
    rig = await make_rig(session, values=values, pw_alerting_enabled=alerting, **overrides)
    for text in values:
        await add_cells(session, await rig.value(text))
    return rig


async def step(
    rig: Rig,
    k: int,
    *,
    enqueuer: RecordingEnqueuer | None = None,
    maintenance: bool = False,
    **settings: object,
):  # type: ignore[no-untyped-def]
    """Collect bucket k, optionally mark it in maintenance, then evaluate it."""
    await rig.collect(k)
    if maintenance:
        await rig.session.execute(  # type: ignore[attr-defined]
            update(PwBucket)
            .where(PwBucket.watch_id == rig.watch.id, PwBucket.bucket_start == T0 + STEP * k)
            .values(in_maintenance=True)
        )
        await rig.session.commit()  # type: ignore[attr-defined]
    engine_settings = rig.settings.model_copy(update=settings) if settings else rig.settings
    report = await PwAnomalyEngine(
        rig.session, settings=engine_settings, clock=lambda: now_of(k), enqueuer=enqueuer
    ).evaluate(rig.instance)
    await rig.session.commit()  # type: ignore[attr-defined]
    rig.last_report = report
    return report


async def alerts(rig: Rig) -> list[Alert]:
    return list(
        (
            await rig.session.scalars(  # type: ignore[attr-defined]
                select(Alert)
                .where(Alert.source_type == "pw_value")
                .order_by(Alert.opened_at, Alert.id)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


def fingerprint_of(rig: Rig, text: str = "alice", detector: PwAnomalyType = SPIKE) -> str:
    return pw_anomaly_fingerprint(rig.instance.id, rig.watch.id, detector.value, text)


def spike(rig: Rig, start: int = 1, stop: int = 4, who: str = "alice") -> None:
    plan_range(rig.plan, start, stop, {who: 500, "bob": 400})


# ------------------------------------------------------------------------ shadow mode
async def test_nothing_opens_while_alerting_is_off_and_no_statement_touches_an_alert(
    db_session,
) -> None:
    rig = await alerting_rig(db_session, alerting=False)
    spike(rig)
    enqueuer = RecordingEnqueuer()
    with recorded_statements(db_session) as statements:
        for k in range(0, 7):
            await step(rig, k, enqueuer=enqueuer)
            if k == 2:
                assert await rig.state_name("alice", SPIKE) == "OPEN"
        # a version bump resolves the incident too, still without touching an alert
        await db_session.execute(
            update(PropertyWatch).where(PropertyWatch.id == rig.watch.id).values(watch_version=2)
        )
        await db_session.commit()
        await step(rig, 7, enqueuer=enqueuer)
    [anomaly] = await rig.anomalies(SPIKE)
    assert anomaly.state == "RESOLVED", "the incident is recorded and its lifecycle ran"
    assert await alerts(rig) == []
    assert enqueuer.calls == []
    assert not [s for s in statements if ALERT_TABLES.search(s)], "shadow mode adds no statement"
    # the same timeline with alerting on does touch the alert tables (the control)
    other = await alerting_rig(db_session)
    spike(other)
    with recorded_statements(db_session) as statements:
        await step(other, 0)
        await step(other, 1)
        await step(other, 2)
    assert [s for s in statements if ALERT_TABLES.search(s)]


async def test_nothing_opens_while_the_family_switch_is_off(db_session) -> None:
    rig = await alerting_rig(db_session)  # alerting on
    spike(rig)
    enqueuer = RecordingEnqueuer()
    for k in range(0, 4):
        await step(rig, k, enqueuer=enqueuer, pw_collection_enabled=False)
    assert await rig.state_name("alice", SPIKE) == "OPEN"
    assert await alerts(rig) == []
    assert enqueuer.calls == []


# ------------------------------------------------------- open, refresh and resolve
async def test_open_refresh_and_resolve_carry_the_fingerprint(db_session) -> None:
    rig = await alerting_rig(db_session)
    spike(rig)
    enqueuer = RecordingEnqueuer()
    await step(rig, 0, enqueuer=enqueuer)
    await step(rig, 1, enqueuer=enqueuer)
    assert await alerts(rig) == [], "a candidate raises nothing"
    await step(rig, 2, enqueuer=enqueuer)

    [alert] = await alerts(rig)
    fingerprint = fingerprint_of(rig)
    assert alert.fingerprint == alert.dedup_key == fingerprint
    assert alert.status == AlertStatus.OPEN and alert.occurrence_count == 1
    [anomaly] = await rig.anomalies(SPIKE)
    assert anomaly.fingerprint == fingerprint, "the incident carries the alert's identity"
    assert alert.source_anomaly_ids == [str(anomaly.id)]
    assert alert.severity == rig.watch.severity
    assert enqueuer.calls == [(fingerprint, "OPENED")]

    await step(rig, 3, enqueuer=enqueuer)
    [alert] = await alerts(rig)
    assert alert.occurrence_count == 2, "a still-anomalous bucket refreshes the one alert"
    assert enqueuer.calls == [(fingerprint, "OPENED")], "a refresh notifies nobody"

    await step(rig, 4, enqueuer=enqueuer)
    await step(rig, 5, enqueuer=enqueuer)
    [alert] = await alerts(rig)
    assert alert.status == AlertStatus.OPEN, "a recovering incident keeps its alert"
    await step(rig, 6, enqueuer=enqueuer)
    [alert] = await alerts(rig)
    assert alert.status == AlertStatus.RESOLVED
    assert alert.resolved_by == "pw-engine" and alert.resolution_reason == "condition recovered"
    assert enqueuer.calls == [(fingerprint, "OPENED"), (fingerprint, "RESOLVED")]

    # a recurrence is a new incident and a new alert on the same fingerprint
    spike(rig, 7, 10)
    for k in range(7, 10):
        await step(rig, k, enqueuer=enqueuer)
    again = await alerts(rig)
    assert len(again) == 2 and {a.fingerprint for a in again} == {fingerprint}
    assert [a.status for a in again] == [AlertStatus.RESOLVED, AlertStatus.OPEN]


async def test_the_alert_context_names_the_watch_the_value_and_the_band(db_session) -> None:
    rig = await alerting_rig(db_session)
    spike(rig)
    for k in range(0, 3):
        await step(rig, k)
    [alert] = await alerts(rig)
    context = alert.context
    watch = await rig.reload_watch()
    assert context["watch"] == watch.name and context["watch_id"] == str(watch.id)
    assert context["scope"].startswith("log source ")
    assert context["detector_type"] == "PW_VALUE_SPIKE" and context["severity"] == "MEDIUM"
    assert context["value"] == "alice" and context["instance"] == rig.instance.name
    assert context["observed"] == 500 and context["expected"] == 100
    assert context["band_low"] is not None and context["band_high"] is not None
    assert context["measurement"] == "EXACT"
    assert context["bucket_start"] == (T0 + STEP * 2).isoformat()
    assert context["bucket_end"] == (T0 + STEP * 3).isoformat()
    assert alert.title == f"PW_VALUE_SPIKE on {watch.name}: alice"
    assert alert.evidence_snapshot["anomaly_type"] == "PW_VALUE_SPIKE"


async def test_a_tuple_value_is_shown_with_its_parts_apart(db_session) -> None:
    rig = await alerting_rig(
        db_session,
        values=("alice|login",),
        properties=[
            {"kind": "NORMALIZED", "name": "username"},
            {"kind": "CUSTOM_PROPERTY", "name": "WAF_Action"},
        ],
    )
    plan_range(rig.plan, 0, 4, {"alice|login": 100})
    plan_range(rig.plan, 1, 4, {"alice|login": 500})
    for k in range(0, 3):
        await step(rig, k)
    [alert] = await alerts(rig)
    assert alert.context["value"] == "alice | login"
    assert "\x1f" not in alert.title
    assert alert.fingerprint == fingerprint_of(rig, "alice\x1flogin")


async def test_two_incidents_of_one_value_deduplicate_into_one_alert(db_session) -> None:
    rig = await alerting_rig(db_session)
    spike(rig, 1, 8)
    for k in range(0, 4):
        await step(rig, k)
    [first_incident] = await rig.anomalies(SPIKE)
    # the detector state is lost (a restore, a manual repair): the next bucket starts a new
    # incident for the same (watch, detector, value) while the first is still open
    await db_session.execute(
        update(PwDetectorState)
        .where(PwDetectorState.watch_id == rig.watch.id)
        .values(
            state="NORMAL",
            consecutive_anomalous=0,
            consecutive_healthy=0,
            active_anomaly_id=None,
            open_anomaly_id=None,
        )
    )
    await db_session.commit()
    for k in range(4, 7):
        await step(rig, k)
    incidents = await rig.anomalies(SPIKE)
    assert len(incidents) == 2 and incidents[0].id == first_incident.id
    assert incidents[0].fingerprint == incidents[1].fingerprint
    [alert] = await alerts(rig)
    assert alert.status == AlertStatus.OPEN
    assert set(alert.source_anomaly_ids) == {str(row.id) for row in incidents}


async def test_a_value_that_opens_and_resolves_in_one_run_does_both_in_order(db_session) -> None:
    """A run that catches up several buckets (a stalled worker) still opens, then resolves."""
    rig = await alerting_rig(db_session)
    spike(rig, 1, 4)
    for k in range(0, 8):
        await rig.collect(k)  # collection only: the engine has not run
    enqueuer = RecordingEnqueuer()
    engine = PwAnomalyEngine(
        rig.session, settings=rig.settings, clock=lambda: now_of(8), enqueuer=enqueuer
    )
    await engine.evaluate(rig.instance)
    await db_session.commit()
    [anomaly] = await rig.anomalies(SPIKE)
    assert anomaly.state == "RESOLVED"
    [alert] = await alerts(rig)
    assert alert.status == AlertStatus.RESOLVED
    assert [call[1] for call in enqueuer.calls] == ["OPENED", "RESOLVED"]


async def test_a_bucket_that_proves_nothing_does_not_refresh_the_alert(db_session) -> None:
    rig = await alerting_rig(db_session)
    spike(rig, 1, 6)
    rig.plan[3] = {"bob": 400}
    rig.truncate = {3}  # alice is unknown in a truncated bucket: the incident holds
    for k in range(0, 3):
        await step(rig, k)
    [alert] = await alerts(rig)
    assert alert.occurrence_count == 1
    await step(rig, 3)
    assert await rig.state_name("alice", SPIKE) == "OPEN"
    [alert] = await alerts(rig)
    assert alert.occurrence_count == 1, "an unknown reading is not a recurrence"
    await step(rig, 4)
    [alert] = await alerts(rig)
    assert alert.occurrence_count == 2


async def test_a_recurrence_inside_one_catch_up_run_opens_a_second_alert(db_session) -> None:
    """Open, resolve and open again within one run: the resolve must reach the first alert
    before the second open, or the second incident would refresh and then close the first."""
    rig = await alerting_rig(db_session)
    spike(rig, 1, 4)
    spike(rig, 8, 11)
    for k in range(0, 12):
        await rig.collect(k)
    enqueuer = RecordingEnqueuer()
    await PwAnomalyEngine(
        rig.session, settings=rig.settings, clock=lambda: now_of(12), enqueuer=enqueuer
    ).evaluate(rig.instance)
    await db_session.commit()
    # both opened in the same run, so by clock tie: tell them apart by status
    assert sorted(str(a.status) for a in await alerts(rig)) == ["OPEN", "RESOLVED"]
    assert [call[1] for call in enqueuer.calls] == ["OPENED", "RESOLVED", "OPENED"]


# ------------------------------------------------------------------------ maintenance
async def test_a_bucket_in_maintenance_opens_and_refreshes_nothing(db_session) -> None:
    rig = await alerting_rig(db_session)
    spike(rig, 1, 6)
    enqueuer = RecordingEnqueuer()
    await step(rig, 0, enqueuer=enqueuer)
    await step(rig, 1, enqueuer=enqueuer)
    await step(rig, 2, enqueuer=enqueuer, maintenance=True)
    await step(rig, 3, enqueuer=enqueuer, maintenance=True)
    assert await rig.state_name("alice", SPIKE) == "OPEN", "the incident is still recorded"
    assert await alerts(rig) == [] and enqueuer.calls == []
    # the first bucket outside maintenance that is still anomalous raises it
    await step(rig, 4, enqueuer=enqueuer)
    [alert] = await alerts(rig)
    assert alert.status == AlertStatus.OPEN and alert.occurrence_count == 1
    # and a later maintenance bucket does not refresh it
    await step(rig, 5, enqueuer=enqueuer, maintenance=True)
    [alert] = await alerts(rig)
    assert alert.occurrence_count == 1


async def test_a_bucket_in_maintenance_does_not_resolve_an_alert(db_session) -> None:
    rig = await alerting_rig(db_session)
    spike(rig, 1, 4)
    enqueuer = RecordingEnqueuer()
    for k in range(0, 6):
        await step(rig, k, enqueuer=enqueuer)
    assert await rig.state_name("alice", SPIKE) == "RECOVERING"
    # the third healthy bucket would resolve; in maintenance it must not
    await step(rig, 6, enqueuer=enqueuer, maintenance=True)
    await step(rig, 7, enqueuer=enqueuer, maintenance=True)
    assert await rig.state_name("alice", SPIKE) == "RECOVERING"
    [anomaly] = await rig.anomalies(SPIKE)
    assert anomaly.state == "RECOVERING" and anomaly.resolved_at is None
    [alert] = await alerts(rig)
    assert alert.status == AlertStatus.OPEN
    assert [call[1] for call in enqueuer.calls] == ["OPENED"]
    # outside maintenance the next healthy bucket resolves both, together
    await step(rig, 8, enqueuer=enqueuer)
    [anomaly] = await rig.anomalies(SPIKE)
    [alert] = await alerts(rig)
    assert anomaly.state == "RESOLVED" and alert.status == AlertStatus.RESOLVED
    assert alert.resolved_by == "pw-engine"


# ----------------------------------------------------------------------- version bump
async def test_a_version_bump_resolves_the_open_alert(db_session) -> None:
    rig = await alerting_rig(db_session)
    spike(rig, 1, 6)
    enqueuer = RecordingEnqueuer()
    for k in range(0, 4):
        await step(rig, k, enqueuer=enqueuer)
    [alert] = await alerts(rig)
    assert alert.status == AlertStatus.OPEN
    await db_session.execute(
        update(PropertyWatch).where(PropertyWatch.id == rig.watch.id).values(watch_version=2)
    )
    await db_session.commit()
    await step(rig, 4, enqueuer=enqueuer)
    [alert] = await alerts(rig)
    assert alert.status == AlertStatus.RESOLVED
    assert alert.resolved_by == "pw-engine"
    assert alert.resolution_reason == "watch definition changed"
    assert [call[1] for call in enqueuer.calls] == ["OPENED", "RESOLVED"]
    [anomaly] = await rig.anomalies(SPIKE)
    transitions = await rig.transitions(anomaly)
    assert str(transitions[-1].to_state) == "RESOLVED"
    assert transitions[-1].reason == "watch definition changed"


async def test_a_version_bump_in_maintenance_still_resolves_the_alert(db_session) -> None:
    rig = await alerting_rig(db_session)
    spike(rig, 1, 6)
    for k in range(0, 4):
        await step(rig, k)
    await db_session.execute(
        update(PropertyWatch).where(PropertyWatch.id == rig.watch.id).values(watch_version=2)
    )
    await db_session.commit()
    await step(rig, 4, maintenance=True)
    [alert] = await alerts(rig)
    assert alert.status == AlertStatus.RESOLVED


# --------------------------------------------------------------------- statement counts
async def _cost(db_session, count: int, tick: int) -> int:  # type: ignore[no-untyped-def]
    names = tuple(f"v{i}" for i in range(count))
    rig = await alerting_rig(db_session, values=names)
    rig.default = dict.fromkeys(names, 100)
    plan_range(rig.plan, 1, 6, dict.fromkeys(names, 500))
    for k in range(0, tick):
        await step(rig, k)
    await rig.collect(tick)
    with recorded_statements(db_session) as statements:
        await PwAnomalyEngine(
            rig.session, settings=rig.settings, clock=lambda: now_of(tick)
        ).evaluate(rig.instance)
    await db_session.commit()
    return len(statements)


async def test_opening_alerts_costs_a_fixed_number_of_statements_per_new_alert(db_session) -> None:
    """`apply_bulk` inserts each new alert in its own savepoint (the partial unique index's
    protection): savepoint, insert, release. Nothing else scales with the alerts."""
    small = await _cost(db_session, 3, 2)
    large = await _cost(db_session, 9, 2)
    assert large - small == 3 * (9 - 3), (small, large)


async def test_refreshing_alerts_costs_the_same_at_any_size(db_session) -> None:
    assert await _cost(db_session, 3, 3) == await _cost(db_session, 9, 3)


# ---------------------------------------------------------------------------- logging
async def test_no_log_line_holds_the_value_text(db_session, caplog) -> None:
    secret = "zq-unique-value-77"
    rig = await alerting_rig(db_session, values=(secret,))
    rig.default = {secret: 100, "bob": 400}
    plan_range(rig.plan, 1, 4, {secret: 500, "bob": 400})
    enqueuer = RecordingEnqueuer()
    caplog.set_level(logging.DEBUG)
    for k in range(0, 8):
        await step(rig, k, enqueuer=enqueuer)
    [alert] = await alerts(rig)
    assert alert.status == AlertStatus.RESOLVED
    assert alert.context["value"] == secret, "the alert context is where the value belongs"
    assert secret not in alert.fingerprint
    assert secret not in repr(rig.last_report.as_dict()) if rig.last_report else True
    assert caplog.records, "the capture ran"
    for record in caplog.records:
        assert secret not in record.getMessage(), record.name
        assert secret not in str(record.__dict__.get("args", "")), record.name
    assert all(secret not in repr(call) for call in enqueuer.calls)


# ------------------------------------------------------------------------- the pipeline
async def test_the_pipeline_hands_the_engine_an_enqueuer_only_while_alerting(db_session) -> None:
    seen: list[dict] = []

    def factory(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(kwargs)
        return PwAnomalyEngine(*args, **kwargs)

    for alerting in (False, True):
        rig = await alerting_rig(db_session, alerting=alerting)
        await rig.collect(0)
        await PwInstancePipeline(
            rig.session,
            rig.provider,
            settings=rig.settings,
            clock=lambda: now_of(1),
            engine_factory=factory,
        ).run(rig.instance)
        await db_session.commit()
    assert "enqueuer" not in seen[0]
    assert type(seen[1]["enqueuer"]).__name__ == "NotificationDispatcher"


class NoTraining:
    """Training would replace the hand-written cells with the few samples of this timeline."""

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        pass

    async def rebuild_due(self, instance, *, now):  # type: ignore[no-untyped-def]
        return PwTrainingReport()


async def test_the_scheduled_pipeline_opens_and_resolves_an_alert(db_session) -> None:
    rig = await alerting_rig(db_session)
    spike(rig, 1, 4)
    for k in range(0, 8):
        report = await PwInstancePipeline(
            rig.session,
            rig.provider,
            settings=rig.settings,
            clock=lambda k=k: now_of(k),
            trainer_factory=NoTraining,
        ).run(rig.instance)
        await db_session.commit()
        assert report.evaluation_error is None, report.evaluation_error
        if k == 2:
            [alert] = await alerts(rig)
            assert alert.status == AlertStatus.OPEN
    [alert] = await alerts(rig)
    assert alert.status == AlertStatus.RESOLVED and alert.fingerprint == fingerprint_of(rig)


async def test_old_rows_keep_their_stored_fingerprint_but_alert_on_the_new_one(
    db_session,
) -> None:
    """A row written by PW-3b's own fingerprint (never released) is not rewritten, and the
    alert still carries the contract fingerprint, derived from the row's fields."""
    rig = await alerting_rig(db_session)
    spike(rig, 1, 5)
    for k in range(0, 2):
        await step(rig, k)
    await db_session.execute(
        update(PwAnomaly)
        .where(PwAnomaly.watch_id == rig.watch.id)
        .values(fingerprint="pw_anomaly:old")
    )
    await db_session.commit()
    for k in range(2, 4):
        await step(rig, k)
    [anomaly] = await rig.anomalies(SPIKE)
    assert anomaly.fingerprint == "pw_anomaly:old"
    [alert] = await alerts(rig)
    assert alert.fingerprint == fingerprint_of(rig) != anomaly.fingerprint
    assert uuid.UUID(str(alert.source_anomaly_ids[0])) == anomaly.id


# ------------------------------------------------------------------------------- delete
async def _open_alert(session, watch_id, value: str = "alice") -> Alert:  # type: ignore[no-untyped-def]
    """An open PW alert as the engine writes it (fingerprint, source type, watch id)."""
    from app.alerts.service import AlertInput, AlertService
    from app.models.enums import Severity

    result = await AlertService(session).open_or_update(
        AlertInput(
            fingerprint=pw_anomaly_fingerprint(uuid.uuid4(), watch_id, "PW_VALUE_SPIKE", value),
            title=f"PW_VALUE_SPIKE on w: {value}",
            severity=Severity.MEDIUM,
            source_type="pw_value",
            source_id=None,
            context={"watch_id": str(watch_id), "value": value},
        )
    )
    await session.commit()
    return result.alert


async def _status(session, alert_id) -> Alert:  # type: ignore[no-untyped-def]
    return await session.scalar(
        select(Alert).where(Alert.id == alert_id).execution_options(populate_existing=True)
    )


async def test_deleting_a_watch_resolves_its_open_alerts_as_the_deleting_principal(
    db_session, db_schema: str
) -> None:
    from tests.integration.test_property_watches_api import (
        PREFIX,
        _client,
        _create,
        _principal,
        _seed,
    )

    await _seed(db_session)
    writer = _client(db_schema, _principal("detection_engineer"))
    doomed = _create(writer, "doomed")
    keep = _create(writer, "survivor")
    first = await _open_alert(db_session, uuid.UUID(doomed["id"]), "alice")
    second = await _open_alert(db_session, uuid.UUID(doomed["id"]), "bob")
    other = await _open_alert(db_session, uuid.UUID(keep["id"]), "alice")

    assert writer.delete(f"{PREFIX}/{doomed['id']}").status_code == 204

    for alert in (first, second):
        row = await _status(db_session, alert.id)
        assert row.status == AlertStatus.RESOLVED
        assert row.resolved_by == "detection_engineer"
        assert row.resolution_reason == "the watch was deleted"
    assert (await _status(db_session, other.id)).status == AlertStatus.OPEN
    assert writer.get(f"{PREFIX}/{doomed['id']}").status_code == 404


async def test_a_refused_delete_leaves_the_alerts_open(db_session, db_schema: str) -> None:
    from tests.integration.test_custom_rules_pw_value import PREFIX as RULES
    from tests.integration.test_custom_rules_pw_value import WATCH_PREFIX, _api_world, _rule_body

    client, watch, pinned, _automatic, _instance = await _api_world(db_session, db_schema)
    assert client.post(RULES, json=_rule_body(pinned.id)).status_code == 201
    alert = await _open_alert(db_session, uuid.UUID(watch["id"]))

    assert client.delete(f"{WATCH_PREFIX}/{watch['id']}").status_code == 409
    assert (await _status(db_session, alert.id)).status == AlertStatus.OPEN


async def test_deleting_a_watch_with_no_open_alert_changes_no_alert(
    db_session, db_schema: str
) -> None:
    from tests.integration.test_property_watches_api import (
        PREFIX,
        _client,
        _create,
        _principal,
        _seed,
    )

    await _seed(db_session)
    writer = _client(db_schema, _principal("detection_engineer"))
    made = _create(writer, "quiet")
    resolved = await _open_alert(db_session, uuid.UUID(made["id"]))
    resolved.status = AlertStatus.RESOLVED
    resolved.resolved_by = "someone"
    await db_session.commit()
    assert writer.delete(f"{PREFIX}/{made['id']}").status_code == 204
    row = await _status(db_session, resolved.id)
    assert row.resolved_by == "someone", "an alert already resolved is left as it was"
