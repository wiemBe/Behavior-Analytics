"""MSG-5: Phase A detector sentences and lifecycle reasons are stored and served with their codes.

The engine writes the text and its reference together (explanation, transition reason,
suppression reason), a later abnormal bucket overwrites both, the API serves the pair, and a row
stored before codes stays uncoded. WAF and IPS transitions carry the lifecycle's reason code too.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.anomaly.engine import AnomalyEngine, EvaluationReport
from app.anomaly.evidence import unknown
from app.anomaly.lifecycle import LifecycleInput, next_state
from app.core.config import Settings
from app.i18n.messages import MessageRef, render
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    DetectorSignal,
    EvidenceStatus,
    Severity,
    WafTelemetryStatus,
)
from app.models.ips import IpsAnomalyTransition
from app.models.log_source import (
    AnomalyStateTransition,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceDetectorState,
    LogSourceMetric,
)
from app.models.waf import WafAnomalyTransition
from app.waf.engine import WafAnomalyEngine
from tests.integration.factories import add_metric, make_instance, make_log_source
from tests.integration.test_ips_engine import (
    DAY,
    PORT,
    add_bucket,
    add_entity,
    add_entity_cell,
    add_source,
    anomalies_for,
    evaluate,
)
from tests.integration.test_ips_engine import START as IPS_START
from tests.integration.test_ips_engine import add_metric as add_ips_metric
from tests.integration.test_phase3_api import READER, client_for
from tests.integration.test_waf_b2_engine import (
    ACTION_SEMANTICS,
    add_cell,
    add_observation,
    behavior_settings,
    seed_endpoint,
)
from tests.integration.test_waf_productionization import STEP, T0

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# Three hours back, on the hour: the buckets stay in one weekday/hour cell and inside the API's
# default 24-hour window.
START = (datetime.now(UTC) - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
BUCKET = 300
BASELINE_EPS = 2.0
SPIKE_EPS = 6.0
LC = "anomaly.lifecycle."
DET = "anomaly.detector."


def _settings(**kwargs: Any) -> Settings:
    defaults: dict[str, Any] = dict(
        encryption_key="x" * 44,
        anomaly_open_after_intervals=2,
        anomaly_resolve_after_intervals=2,
        anomaly_min_absolute_delta_events=200.0,
        anomaly_min_bucket_events=100.0,
    )
    defaults.update(kwargs)
    return Settings(**defaults)


async def _baseline(session, ls, *, median: float = BASELINE_EPS) -> None:
    session.add(
        LogSourceBaseline(
            log_source_id=ls.id,
            metric_name="average_eps",
            weekday=START.isoweekday(),
            hour=START.hour,
            median=median,
            mad=0.15,
            p05=median * 0.85,
            p95=median * 1.15,
            sample_count=30,
            is_reliable=True,
            baseline_version=2,
            observations=[median],
            completeness=0.95,
        )
    )
    await session.flush()


async def _feed(engine, session, ls, *, index: int, count: int, eps: float) -> None:
    for i in range(index, index + count):
        metric = await add_metric(
            session,
            ls,
            START + timedelta(seconds=BUCKET * i),
            average_eps=eps,
            event_count=int(eps * BUCKET),
            bucket_seconds=BUCKET,
            completeness=BucketCompleteness.COMPLETE,
            payload_signature=f"sig-{i}",
        )
        await engine.evaluate_interval(ls, metric)


async def _the_anomaly(session, ls) -> LogSourceAnomaly:
    row = await session.scalar(
        select(LogSourceAnomaly)
        .where(LogSourceAnomaly.log_source_id == ls.id)
        .order_by(LogSourceAnomaly.detected_at.desc())
        .limit(1)
    )
    assert row is not None
    return row


async def _transitions(session, anomaly) -> list[AnomalyStateTransition]:
    return list(
        (
            await session.scalars(
                select(AnomalyStateTransition)
                .where(AnomalyStateTransition.anomaly_id == anomaly.id)
                .order_by(AnomalyStateTransition.occurred_at, AnomalyStateTransition.created_at)
            )
        ).all()
    )


def _ref(wire: dict[str, Any]) -> MessageRef:
    """A flat stored reference as a `MessageRef` (the lifecycle reasons have no nested params)."""
    return MessageRef(wire["code"], wire["params"])


class TestPhaseAEngineStoresTheCode:
    async def test_the_explanation_and_the_details_carry_the_reference(self, db_session) -> None:
        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst)
        await _baseline(db_session, ls)
        engine = AnomalyEngine(db_session, settings=_settings())

        await _feed(engine, db_session, ls, index=0, count=2, eps=BASELINE_EPS)
        await _feed(engine, db_session, ls, index=2, count=1, eps=SPIKE_EPS)

        anomaly = await _the_anomaly(db_session, ls)
        assert anomaly.state == AnomalyState.CANDIDATE
        assert anomaly.explanation == (
            "average EPS 6.00 is above the baseline median 2.00 (3.00x expected, "
            "1200 events/bucket); robust z=17.99 (threshold ±3.5)"
        )
        stored = anomaly.explanation_i18n
        assert stored is not None and stored["code"] == DET + "volume_above"
        assert stored["params"]["observed"] == 6.0
        assert stored["params"]["basis"]["code"] == DET + "basis_z"
        assert anomaly.explanation_code == DET + "volume_above"
        assert anomaly.details["reason"] == anomaly.explanation
        assert anomaly.details["reason_code"] == stored["code"]
        assert anomaly.details["reason_params"] == stored["params"]
        json.dumps(anomaly.details)

    async def test_a_worse_bucket_overwrites_the_text_and_the_reference_together(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst)
        await _baseline(db_session, ls)
        engine = AnomalyEngine(db_session, settings=_settings())
        await _feed(engine, db_session, ls, index=0, count=1, eps=BASELINE_EPS)
        await _feed(engine, db_session, ls, index=1, count=2, eps=SPIKE_EPS)
        first = (await _the_anomaly(db_session, ls)).explanation

        await _feed(engine, db_session, ls, index=3, count=1, eps=9.0)

        anomaly = await _the_anomaly(db_session, ls)
        assert anomaly.state == AnomalyState.OPEN
        assert anomaly.explanation != first
        assert anomaly.explanation is not None and "average EPS 9.00" in anomaly.explanation
        assert anomaly.explanation_i18n is not None
        assert anomaly.explanation_i18n["params"]["observed"] == 9.0
        assert anomaly.details["reason_params"] == anomaly.explanation_i18n["params"]
        assert anomaly.details["reason"] == anomaly.explanation

    async def test_every_transition_stores_its_reason_code_beside_the_text(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst)
        await _baseline(db_session, ls)
        engine = AnomalyEngine(db_session, settings=_settings())
        await _feed(engine, db_session, ls, index=0, count=1, eps=BASELINE_EPS)
        await _feed(engine, db_session, ls, index=1, count=2, eps=SPIKE_EPS)
        await _feed(engine, db_session, ls, index=3, count=3, eps=BASELINE_EPS)

        anomaly = await _the_anomaly(db_session, ls)
        rows = await _transitions(db_session, anomaly)
        assert [str(t.to_state) for t in rows] == [
            AnomalyState.CANDIDATE,
            AnomalyState.OPEN,
            AnomalyState.RECOVERING,
            AnomalyState.RESOLVED,
        ]
        assert [t.reason_i18n for t in rows] == [
            {"code": LC + "first_abnormal", "params": {}},
            {"code": LC + "confirmed", "params": {"count": 2, "threshold": 2}},
            {"code": LC + "normal_observed", "params": {}},
            {"code": LC + "recovered", "params": {"count": 3, "threshold": 3}},
        ]
        for row in rows:
            assert row.reason_i18n is not None
            assert row.reason == render(_ref(row.reason_i18n))
            assert row.reason_code == row.reason_i18n["code"]
        assert rows[1].reason == (
            "2 consecutive abnormal bucket(s) reached the confirmation threshold of 2"
        )
        assert rows[3].reason == (
            "3 consecutive normal bucket(s) reached the recovery threshold of 3"
        )

    async def test_an_incident_suppressed_for_maintenance_stores_the_suppression_code(
        self, db_session
    ) -> None:
        """Both writers of the suppression reason, driven directly.

        The engine's own path never reaches either today (an abnormal bucket in maintenance is
        demoted to UNKNOWN before hysteresis, and a suppressed state has no live incident), so
        the writers are called as the engine would call them: the pair must still agree.
        """
        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst)
        await _baseline(db_session, ls)
        engine = AnomalyEngine(db_session, settings=_settings())
        await _feed(engine, db_session, ls, index=0, count=1, eps=BASELINE_EPS)
        await _feed(engine, db_session, ls, index=1, count=1, eps=SPIKE_EPS)
        candidate = await _the_anomaly(db_session, ls)
        assert candidate.state == AnomalyState.CANDIDATE
        assert candidate.suppression_reason is None
        assert candidate.suppression_reason_i18n is None

        state = await db_session.scalar(
            select(LogSourceDetectorState).where(
                LogSourceDetectorState.log_source_id == ls.id,
                LogSourceDetectorState.anomaly_type == candidate.anomaly_type,
            )
        )
        assert state is not None
        metric = await db_session.scalar(
            select(LogSourceMetric).order_by(LogSourceMetric.bucket_start.desc()).limit(1)
        )
        assert metric is not None
        evidence = unknown(
            AnomalyType(candidate.anomaly_type),
            interval_start=metric.bucket_start,
            interval_end=metric.bucket_start + timedelta(seconds=BUCKET),
            reason_message=MessageRef(DET + "within_range"),
        )
        await engine._apply_transition(
            ls,
            metric,
            evidence,
            state,
            next_state(
                LifecycleInput(
                    state=AnomalyState.CANDIDATE,
                    signal=DetectorSignal.UNKNOWN,
                    consecutive_anomalous=1,
                    consecutive_healthy=0,
                    open_after=2,
                    resolve_after=3,
                    suppressed=True,
                )
            ),
            True,
            EvaluationReport(ls.id, metric.bucket_start),
        )

        anomaly = await _the_anomaly(db_session, ls)
        assert anomaly.state == AnomalyState.SUPPRESSED
        assert anomaly.suppression_reason == "maintenance window"
        assert anomaly.suppression_reason_i18n == {
            "code": "anomaly.suppression.maintenance",
            "params": {},
        }
        last = (await _transitions(db_session, anomaly))[-1]
        assert last.reason == "suppressed"
        assert last.reason_i18n == {"code": LC + "suppressed", "params": {}}

        created = await engine._create_incident(ls, metric, evidence, True)
        assert created.suppression_reason == "maintenance window"
        assert created.suppression_reason_i18n == anomaly.suppression_reason_i18n


async def _seed_old_and_new(session) -> tuple[Any, Any]:
    """One coded incident (from the engine) and one stored before codes."""
    inst = await make_instance(session)
    coded_ls = await make_log_source(session, inst, qradar_id=1, name="coded")
    old_ls = await make_log_source(session, inst, qradar_id=2, name="before-codes")
    await _baseline(session, coded_ls)
    engine = AnomalyEngine(session, settings=_settings())
    await _feed(engine, session, coded_ls, index=0, count=1, eps=BASELINE_EPS)
    await _feed(engine, session, coded_ls, index=1, count=2, eps=SPIKE_EPS)
    coded = await _the_anomaly(session, coded_ls)

    old = LogSourceAnomaly(
        log_source_id=old_ls.id,
        anomaly_type=AnomalyType.COLLECTION_ERROR,
        severity=Severity.HIGH,
        detected_at=START + timedelta(minutes=10),
        state=AnomalyState.OPEN,
        explanation="3 collection error(s) in the interval",
        details={"reason": "3 collection error(s) in the interval", "extra": {}},
        evidence_status=EvidenceStatus.NOT_REQUESTED,
    )
    session.add(old)
    await session.flush()
    session.add(
        AnomalyStateTransition(
            anomaly_id=old.id,
            from_state=AnomalyState.CANDIDATE,
            to_state=AnomalyState.OPEN,
            occurred_at=START + timedelta(minutes=10),
            reason="2 consecutive abnormal bucket(s) reached the confirmation threshold of 2",
            actor="anomaly-engine",
        )
    )
    await session.commit()
    return coded, old


class TestPhaseAApiServesTheCode:
    async def test_the_detail_serves_the_explanation_the_detection_and_the_transitions(
        self, db_session
    ) -> None:
        coded, _old = await _seed_old_and_new(db_session)

        body = client_for(READER).get(f"/api/v1/anomalies/{coded.id}").json()

        assert body["explanation_code"] == DET + "volume_above"
        assert body["explanation_params"]["observed"] == 6.0
        assert body["detection"]["reason"] == body["explanation"]
        assert body["detection"]["reason_code"] == DET + "volume_above"
        assert body["detection"]["reason_params"] == body["explanation_params"]
        assert [t["reason_code"] for t in body["transitions"]] == [
            LC + "first_abnormal",
            LC + "confirmed",
        ]
        assert body["transitions"][1]["reason_params"] == {"count": 2, "threshold": 2}
        assert body["transitions"][1]["reason"] == (
            "2 consecutive abnormal bucket(s) reached the confirmation threshold of 2"
        )

    async def test_the_unified_list_serves_the_explanation_code(self, db_session) -> None:
        coded, old = await _seed_old_and_new(db_session)

        body = client_for(READER).get("/api/v1/anomalies?limit=50").json()

        by_id = {item["id"]: item for item in body["items"]}
        assert by_id[str(coded.id)]["explanation_code"] == DET + "volume_above"
        assert by_id[str(coded.id)]["explanation_params"]["delta"] == 1200.0
        assert by_id[str(old.id)]["explanation"] == "3 collection error(s) in the interval"
        assert by_id[str(old.id)]["explanation_code"] is None
        assert by_id[str(old.id)]["explanation_params"] is None

    async def test_a_row_stored_before_codes_serves_no_code_anywhere(self, db_session) -> None:
        _coded, old = await _seed_old_and_new(db_session)

        body = client_for(READER).get(f"/api/v1/anomalies/{old.id}").json()

        assert body["explanation"] == "3 collection error(s) in the interval"
        assert body["explanation_code"] is None
        assert body["detection"]["reason"] == "3 collection error(s) in the interval"
        assert body["detection"]["reason_code"] is None
        assert body["detection"]["reason_params"] is None
        [transition] = body["transitions"]
        assert transition["reason"].startswith("2 consecutive abnormal")
        assert transition["reason_code"] is None
        assert transition["reason_params"] is None

    async def test_a_malformed_stored_reference_serves_no_code(self, db_session) -> None:
        _coded, old = await _seed_old_and_new(db_session)
        old.explanation_i18n = {"code": 7, "params": []}
        old.details = {"reason": "x", "reason_code": ["no"], "reason_params": "no"}
        await db_session.commit()

        body = client_for(READER).get(f"/api/v1/anomalies/{old.id}").json()

        assert body["explanation_code"] is None and body["explanation_params"] is None
        assert body["detection"]["reason_code"] is None
        assert body["detection"]["reason_params"] is None

    async def test_the_source_summary_serves_the_explanation_code(self, db_session) -> None:
        coded, _old = await _seed_old_and_new(db_session)

        rows = client_for(READER).get(f"/api/v1/log-sources/{coded.log_source_id}/anomalies").json()

        assert [r["explanation_code"] for r in rows] == [DET + "volume_above"]
        assert rows[0]["explanation_params"]["expected"] == 2.0


class TestOtherEnginesRecordTheLifecycleReasonCode:
    async def test_a_waf_transition_stores_the_reason_code(self, db_session) -> None:
        from tests.integration.test_waf_b2_engine import RATIO_CASES

        case = RATIO_CASES[0]
        _source, endpoint = await seed_endpoint(db_session)
        add_cell(db_session, endpoint, case.metric, ACTION_SEMANTICS)
        engine = WafAnomalyEngine(db_session, settings=behavior_settings())
        for index, numerator in enumerate((60, 80)):
            bucket, metric = await add_observation(
                db_session,
                endpoint,
                T0 + STEP * index,
                action_status=WafTelemetryStatus.EXACT,
                action_semantics=ACTION_SEMANTICS,
                blocked=numerator,
                action_events=100,
            )
            await engine.evaluate_interval(endpoint, metric, source_bucket=bucket)

        rows = list(
            (
                await db_session.scalars(
                    select(WafAnomalyTransition).order_by(
                        WafAnomalyTransition.occurred_at, WafAnomalyTransition.created_at
                    )
                )
            ).all()
        )
        assert [str(r.to_state) for r in rows] == [AnomalyState.CANDIDATE, AnomalyState.OPEN]
        assert [r.reason_i18n for r in rows] == [
            {"code": LC + "first_abnormal", "params": {}},
            {"code": LC + "confirmed", "params": {"count": 2, "threshold": 2}},
        ]
        for row in rows:
            assert row.reason == render(_ref(row.reason_i18n or {}))
            assert row.reason_code is not None

    async def test_an_ips_transition_stores_the_reason_code(self, db_session) -> None:
        instance, source, _profile = await add_source(db_session)
        entity = await add_entity(db_session, instance, source)
        for offset in range(6):
            await add_entity_cell(db_session, entity, IPS_START + DAY * offset)
        for offset, events in ((0, 600), (1, 600), (2, 100), (3, 100), (4, 100)):
            at = IPS_START + DAY * offset
            await add_bucket(db_session, source.id, at)
            await add_ips_metric(db_session, entity, at, events)
            await evaluate(db_session, source, at)

        [incident] = await anomalies_for(db_session, PORT)
        rows = list(
            (
                await db_session.scalars(
                    select(IpsAnomalyTransition)
                    .where(IpsAnomalyTransition.ips_anomaly_id == incident.id)
                    .order_by(IpsAnomalyTransition.occurred_at, IpsAnomalyTransition.created_at)
                )
            ).all()
        )
        assert [r.reason_code for r in rows] == [
            LC + "first_abnormal",
            LC + "confirmed",
            LC + "normal_observed",
            LC + "recovered",
        ]
        for row in rows:
            assert row.reason_i18n is not None
            assert row.reason == render(_ref(row.reason_i18n))
        assert rows[-1].reason_params == {"count": 3, "threshold": 3}

