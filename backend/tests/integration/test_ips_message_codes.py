"""MSG-6b: the IPS engine stores each sentence with its message reference.

The detector unit table pins every English shape. These database tests pin the operator path:
incident creation, an OPEN incident's worse-reading refresh, the evidence stored in ``details``,
and the engine-only history hand-off facts.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.i18n.messages import MessageRef, render
from app.ips.behavior import detect_new_destination_port
from app.ips.engine import IpsAnomalyEngine
from app.models.enums import AnomalyState
from app.models.ips import IpsAnomaly
from tests.integration.test_ips_baseline import add_bucket, add_source
from tests.integration.test_ips_engine import (
    DAY,
    NEW_PORT,
    PORT,
    START,
    add_entity,
    add_entity_cell,
    add_metric,
    anomalies_for,
    engine_settings,
    evaluate,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _port_spike(db_session, readings: tuple[int, ...]) -> IpsAnomaly:
    instance, source, _profile = await add_source(db_session)
    entity = await add_entity(db_session, instance, source)
    for offset, events in enumerate(readings):
        at = START + DAY * offset
        await add_entity_cell(db_session, entity, at)
        await add_bucket(db_session, source.id, at)
        await add_metric(db_session, entity, at, events)
        await evaluate(db_session, source, at)
    (anomaly,) = await anomalies_for(db_session, PORT)
    return anomaly


async def test_a_candidate_stores_the_explanation_and_caveat_codes(db_session) -> None:
    anomaly = await _port_spike(db_session, (600,))

    assert anomaly.state == AnomalyState.CANDIDATE
    assert anomaly.explanation_code == "ips.spike.fired"
    assert anomaly.explanation_i18n is not None
    assert anomaly.explanation_params is not None
    assert anomaly.explanation_params["observed"] == 600.0
    assert anomaly.details["reason"] == anomaly.explanation
    assert anomaly.details["reason_code"] == "ips.spike.fired"
    assert anomaly.details["reason_params"] == anomaly.explanation_i18n["params"]
    assert anomaly.details["extra"]["caveat_code"] == "ips.caveat.port_rate"
    assert anomaly.details["extra"]["caveat_params"] == {}
    assert anomaly.suppression_reason is None
    assert anomaly.suppression_reason_i18n is None


async def test_a_worse_reading_overwrites_the_text_and_code_together(db_session) -> None:
    anomaly = await _port_spike(db_session, (600, 700, 900))

    assert anomaly.state == AnomalyState.OPEN
    assert anomaly.explanation is not None and anomaly.explanation.startswith("900 events")
    assert anomaly.explanation_code == "ips.spike.fired"
    assert anomaly.explanation_params is not None
    assert anomaly.explanation_params["observed"] == 900.0
    assert anomaly.details["reason"] == anomaly.explanation
    assert anomaly.details["reason_params"] == anomaly.explanation_i18n["params"]  # type: ignore[index]


@pytest.mark.parametrize(
    ("history_end", "history_code", "verdict_code"),
    (
        (None, "ips.history_check.not_loaded", "ips.novelty.established_outside_window"),
        (
            START + timedelta(hours=1),
            "ips.history_check.before_hand_off",
            "ips.novelty.established_hand_off",
        ),
    ),
    ids=("not-loaded", "before-hand-off"),
)
async def test_the_engine_carries_its_history_fact_as_a_nested_reference(
    db_session, history_end, history_code: str, verdict_code: str
) -> None:
    instance, source, _profile = await add_source(db_session)
    entity = await add_entity(db_session, instance, source, first_seen_at=START)
    judged = START + timedelta(hours=14)
    bucket = await add_bucket(db_session, source.id, judged)
    await add_metric(db_session, entity, judged, 30)
    seen = []

    def spy(ctx):  # type: ignore[no-untyped-def]
        assert ctx.novelty is not None
        seen.append((ctx.novelty, detect_new_destination_port(ctx)))
        return seen[-1][1]

    engine = IpsAnomalyEngine(
        db_session,
        settings=engine_settings(ips_new_port_history_days=1),
        clock=lambda: judged + timedelta(hours=1),
        detectors={NEW_PORT: spy},
    )
    engine.history_end = history_end
    await engine.evaluate_bucket(source, bucket)

    novelty, verdict = seen[0]
    message = getattr(novelty.history, "message", None)
    assert message == MessageRef(
        history_code,
        {"hand_off": history_end} if history_end is not None else {},
    )
    assert novelty.history.reason == render(message)
    # The hand-off case replaces the healthy detector sentence after the detector returns. The
    # spy holds the same verdict object, so this checks that real engine path too.
    assert verdict.evidence.reason_message is not None
    assert verdict.evidence.reason_message.code == verdict_code
    assert render(verdict.evidence.reason_message) == verdict.evidence.reason


async def test_a_custom_plain_sentence_stores_no_reference(db_session) -> None:
    from app.anomaly.evidence import AnomalyEvidence
    from app.ips.detector import IpsVerdict
    from app.models.enums import DetectorSignal, Severity

    instance, source, _profile = await add_source(db_session)
    entity = await add_entity(db_session, instance, source)
    await add_entity_cell(db_session, entity, START)
    bucket = await add_bucket(db_session, source.id, START)
    await add_metric(db_session, entity, START, 600)

    def plain(ctx):  # type: ignore[no-untyped-def]
        return IpsVerdict(
            detector_type=PORT,
            evidence=AnomalyEvidence(
                anomaly_type=PORT,
                signal=DetectorSignal.ANOMALOUS,
                severity=Severity.HIGH,
                observed_value=600.0,
                expected_value=100.0,
                baseline_low=80.0,
                baseline_high=120.0,
                deviation_score=9.0,
                threshold=3.5,
                sample_count=10,
                interval_start=ctx.observation.bucket.bucket_start,
                interval_end=ctx.observation.bucket.bucket_end,
                confidence=1.0,
                reason="written by hand",
            ),
        )

    engine = IpsAnomalyEngine(
        db_session,
        settings=engine_settings(anomaly_open_after_intervals=1),
        detectors={PORT: plain},
    )
    await engine.evaluate_bucket(source, bucket)
    anomaly = await db_session.scalar(select(IpsAnomaly))

    assert anomaly is not None and anomaly.explanation == "written by hand"
    assert anomaly.explanation_i18n is None
    assert anomaly.explanation_code is None
    assert "reason_code" not in anomaly.details
