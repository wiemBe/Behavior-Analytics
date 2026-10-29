"""MSG-6: the stored WAF sentences are written with their message code.

An incident's explanation and suppression reason keep their English text column and gain a
`{code, params}` beside it. The two are one fact: a writer that changes the text changes the
code, and an incident that took its sentence from a detector with no code stores NULL. The
detector's reason and caveat travel inside `details`, where the API projection reads them.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.i18n.messages import MessageRef, render
from app.models.enums import (
    AnomalyState,
    WafAnomalyType,
    WafMeasurement,
    WafTelemetryStatus,
)
from app.models.waf import WafAnomaly
from app.schemas.messages import MessageValue
from app.waf.engine import WafAnomalyEngine
from tests.integration.test_waf_b2_engine import (
    ACTION_SEMANTICS,
    RATIO_CASES,
    STEP,
    T0,
    RatioCase,
    add_cell,
    add_observation,
    behavior_settings,
    seed_endpoint,
)
from tests.integration.test_waf_new_endpoint import NEW, Scenario, novelty_settings

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

CAVEATS = {
    WafAnomalyType.WAF_BLOCK_RATIO_SPIKE: "waf.caveat.block_ratio",
    WafAnomalyType.WAF_ERROR_RATIO_SPIKE: "waf.caveat.server_error_ratio",
    WafAnomalyType.WAF_CLIENT_ERROR_RATIO_SPIKE: "waf.caveat.client_error_ratio",
}


def raw(params: dict[str, Any]) -> dict[str, Any]:
    """Wire parameters back to the raw values the sentence was rendered from."""
    out: dict[str, Any] = {}
    for name, value in params.items():
        if isinstance(value, dict) and value.get("kind") == "percent":
            out[name] = value["ratio"]
        elif isinstance(value, dict) and value.get("kind") == "points":
            out[name] = value["points"] / 100
        elif isinstance(value, dict) and "code" in value:
            out[name] = MessageRef(value["code"], raw(value["params"]))
        else:
            out[name] = value
    return out


def assert_pair(text: str | None, pair: dict[str, Any] | None) -> None:
    """The stored text is the English of the stored code: they cannot disagree."""
    assert pair is not None and text is not None
    assert text == render(MessageRef(pair["code"], raw(pair["params"])))


async def open_ratio(db_session, case: RatioCase, numerators: tuple[int, ...]):  # type: ignore[no-untyped-def]
    settings = behavior_settings()
    _source, endpoint = await seed_endpoint(db_session)
    add_cell(db_session, endpoint, case.metric, case.semantics)
    engine = WafAnomalyEngine(db_session, settings=settings)
    for index, numerator in enumerate(numerators):
        kwargs = {
            case.status_field: WafTelemetryStatus.EXACT,
            case.semantics_field: case.semantics,
            case.count_field: numerator,
            case.denominator_field: 100,
        }
        bucket, metric = await add_observation(db_session, endpoint, T0 + STEP * index, **kwargs)
        await engine.evaluate_interval(endpoint, metric, source_bucket=bucket)
    return await db_session.scalar(
        select(WafAnomaly).where(WafAnomaly.detector_type == case.detector_type)
    )


@pytest.mark.parametrize("case", RATIO_CASES, ids=lambda case: case.metric)
async def test_an_incident_stores_its_explanation_code_beside_the_text(
    db_session, case: RatioCase
) -> None:
    anomaly = await open_ratio(db_session, case, (60, 80))
    assert anomaly is not None and anomaly.state == AnomalyState.OPEN
    assert anomaly.explanation_i18n is not None
    assert anomaly.explanation_i18n["code"] == "waf.ratio.fired"
    assert_pair(anomaly.explanation, anomaly.explanation_i18n)
    assert anomaly.explanation_code == "waf.ratio.fired"
    assert anomaly.explanation_params is not None
    assert anomaly.explanation_params["count"] == 80
    # The raw event values the detector folded never reach the stored reference: the endpoint
    # is the entity the row already names, and no source address is in it.
    assert "192.0.2.10" not in str(anomaly.explanation_i18n)
    assert anomaly.suppression_reason is None and anomaly.suppression_reason_i18n is None


@pytest.mark.parametrize("case", RATIO_CASES, ids=lambda case: case.metric)
async def test_details_carry_the_reason_and_the_caveat_codes(
    db_session, case: RatioCase
) -> None:
    anomaly = await open_ratio(db_session, case, (60, 80))
    assert anomaly is not None
    details = anomaly.details
    assert details["reason"] == anomaly.explanation
    assert details["reason_code"] == "waf.ratio.fired"
    assert details["reason_params"] == anomaly.explanation_i18n["params"]  # type: ignore[index]
    extra = details["extra"]
    assert extra["caveat_code"] == CAVEATS[case.detector_type]
    assert extra["caveat_params"] == {}
    assert extra["caveat"] == render(MessageRef(extra["caveat_code"]))


async def test_a_candidate_stores_its_code_when_the_incident_is_created(db_session) -> None:
    """The first abnormal bucket creates the row, before any later reading can overwrite it."""
    anomaly = await open_ratio(db_session, RATIO_CASES[0], (60,))
    assert anomaly is not None and anomaly.state == AnomalyState.CANDIDATE
    assert anomaly.explanation_code == "waf.ratio.fired"
    assert anomaly.explanation_params is not None and anomaly.explanation_params["count"] == 60
    assert_pair(anomaly.explanation, anomaly.explanation_i18n)
    assert anomaly.details["reason_code"] == "waf.ratio.fired"


async def test_a_worse_reading_overwrites_the_text_and_the_code_together(db_session) -> None:
    case = RATIO_CASES[0]
    anomaly = await open_ratio(db_session, case, (60, 80, 95))
    assert anomaly is not None
    assert "95 of 100" in (anomaly.explanation or "")
    assert anomaly.explanation_params is not None and anomaly.explanation_params["count"] == 95
    assert_pair(anomaly.explanation, anomaly.explanation_i18n)
    assert anomaly.details["reason"] == anomaly.explanation
    assert anomaly.details["reason_params"]["count"] == 95


async def test_a_lower_bound_numerator_stores_the_lower_bound_variant(db_session) -> None:
    settings = behavior_settings(anomaly_open_after_intervals=1)
    _source, endpoint = await seed_endpoint(db_session)
    case = RATIO_CASES[0]
    add_cell(db_session, endpoint, case.metric, ACTION_SEMANTICS)
    engine = WafAnomalyEngine(db_session, settings=settings)
    bucket, metric = await add_observation(
        db_session, endpoint, T0,
        action_status=WafTelemetryStatus.EXACT, action_semantics=ACTION_SEMANTICS,
        blocked=80, action_events=100, unmapped_actions=1,
    )
    await engine.evaluate_interval(endpoint, metric, source_bucket=bucket)
    anomaly = await db_session.scalar(select(WafAnomaly))
    assert anomaly is not None
    assert anomaly.observed_measurement == WafMeasurement.LOWER_BOUND
    assert anomaly.explanation_code == "waf.ratio.fired_lower"
    assert_pair(anomaly.explanation, anomaly.explanation_i18n)
    assert (anomaly.explanation or "").startswith("at least 80 of 100")


async def test_an_incident_from_a_detector_with_no_code_stores_null(db_session) -> None:
    """A detector that supplies only text (a custom one) leaves the reference NULL."""
    from app.anomaly.evidence import AnomalyEvidence
    from app.models.enums import DetectorSignal, Severity
    from app.waf.detector import WafSpikeVerdict

    settings = behavior_settings(anomaly_open_after_intervals=1)
    _source, endpoint = await seed_endpoint(db_session)
    add_cell(db_session, endpoint, RATIO_CASES[0].metric, ACTION_SEMANTICS)

    def plain(ctx):  # type: ignore[no-untyped-def]
        return WafSpikeVerdict(
            detector_type=WafAnomalyType.WAF_BLOCK_RATIO_SPIKE,
            evidence=AnomalyEvidence(
                anomaly_type=WafAnomalyType.WAF_BLOCK_RATIO_SPIKE,
                signal=DetectorSignal.ANOMALOUS, severity=Severity.HIGH,
                observed_value=0.8, expected_value=0.1, baseline_low=None, baseline_high=None,
                deviation_score=9.0, threshold=3.5, sample_count=12,
                interval_start=ctx.observation.bucket_start,
                interval_end=ctx.observation.bucket_end, confidence=0.9,
                reason="written by hand", extra={"observed_measurement": "EXACT"},
            ),
        )

    engine = WafAnomalyEngine(
        db_session, settings=settings, detectors={WafAnomalyType.WAF_BLOCK_RATIO_SPIKE: plain}
    )
    bucket, metric = await add_observation(
        db_session, endpoint, T0,
        action_status=WafTelemetryStatus.EXACT, action_semantics=ACTION_SEMANTICS,
        blocked=80, action_events=100,
    )
    await engine.evaluate_interval(endpoint, metric, source_bucket=bucket)
    anomaly = await db_session.scalar(select(WafAnomaly))
    assert anomaly is not None
    assert anomaly.explanation == "written by hand"
    assert anomaly.explanation_i18n is None and anomaly.explanation_code is None
    assert "reason_code" not in anomaly.details


# --------------------------------------------------------------- the engine's own reasons
async def test_the_engine_hands_the_detector_the_semantics_reason_as_a_code(db_session) -> None:
    """A breakdown identity that changed: the verdict's sentence names both identities, coded."""
    from app.waf.baseline import BLOCK_RATIO_METRIC
    from app.waf.behavior import detect_block_ratio_spike

    verdicts: list = []

    def spy(ctx):  # type: ignore[no-untyped-def]
        verdict = detect_block_ratio_spike(ctx)
        verdicts.append(verdict)
        return verdict

    settings = behavior_settings()
    _source, endpoint = await seed_endpoint(db_session)
    add_cell(db_session, endpoint, BLOCK_RATIO_METRIC, ACTION_SEMANTICS)
    bucket, metric = await add_observation(
        db_session, endpoint, T0,
        action_status=WafTelemetryStatus.EXACT, action_semantics=f"{ACTION_SEMANTICS};changed",
        blocked=80, action_events=100, unmapped_actions=0,
    )
    engine = WafAnomalyEngine(
        db_session, settings=settings, detectors={WafAnomalyType.WAF_BLOCK_RATIO_SPIKE: spy}
    )
    await engine.evaluate_interval(endpoint, metric, source_bucket=bucket)
    (verdict,) = verdicts
    message = verdict.evidence.reason_message
    assert message is not None and message.code == "waf.semantics.mismatch"
    assert message.params == {
        "baseline": ACTION_SEMANTICS, "bucket": f"{ACTION_SEMANTICS};changed",
    }
    assert render(message) == verdict.evidence.reason
    assert verdict.evidence.insufficient_data is True


# ------------------------------------------------------------------- new endpoint
async def test_a_new_endpoint_incident_stores_its_timestamps_as_iso_text(db_session) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(traffic={0: 10, 1: 30, 2: 30})
    for k in (0, 1, 2):
        await scenario.judge(k)
    anomaly = await db_session.scalar(select(WafAnomaly).where(WafAnomaly.detector_type == NEW))
    assert anomaly is not None
    assert anomaly.explanation_code == "waf.novelty.fired"
    assert_pair(anomaly.explanation, anomaly.explanation_i18n)
    params = anomaly.explanation_params
    assert params is not None
    assert params["first_seen"] == scenario.endpoint.first_seen_at.isoformat()
    assert params["requests"] == 70 and params["buckets"] == 3
    subject = params["subject"]
    assert isinstance(subject, MessageValue) and subject.code == "waf.subject.named"
    assert subject.params == {"method": "GET", "endpoint": "/admin"}
    assert anomaly.details["extra"]["caveat_code"] == "waf.caveat.new_endpoint"


async def test_a_missing_history_bucket_is_coded_inside_the_cannot_establish_sentence(
    db_session,
) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(
        skip_history=frozenset({100}), traffic={0: 10, 1: 30}
    )
    await scenario.judge(1)
    message = scenario.evidence.reason_message
    assert message is not None and message.code == "waf.novelty.cannot_establish"
    cause = message.params["cause"]
    assert isinstance(cause, MessageRef)
    assert cause.code == "waf.history_check.never_collected"
    assert cause.params == {"missing": 1, "expected": 288}
    assert render(message) == scenario.evidence.reason


@pytest.mark.parametrize(
    ("override", "code"),
    (
        ({"groups_truncated": True}, "waf.history_check.discovery_truncated"),
        ({"admission_truncated": True}, "waf.history_check.admission_truncated"),
        ({"profile_version": 2}, "waf.history_check.profile_version"),
    ),
    ids=("discovery", "admission", "profile"),
)
async def test_an_unsound_history_bucket_names_its_check_and_its_timestamp(
    db_session, override: dict, code: str
) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(
        history_overrides={50: override}, traffic={0: 10, 1: 30}
    )
    await scenario.judge(1)
    message = scenario.evidence.reason_message
    assert message is not None and message.code == "waf.novelty.cannot_establish"
    cause = message.params["cause"]
    assert isinstance(cause, MessageRef) and cause.code == code
    wire = scenario.evidence.to_dict()["reason_params"]["cause"]
    assert wire["code"] == code
    assert wire["params"]["bucket"].endswith("+00:00")


@pytest.mark.parametrize(
    ("appearance", "code"),
    (
        ({"groups_truncated": True}, "waf.novelty.flood_discovery"),
        ({"admission_truncated": True}, "waf.novelty.flood_admission"),
    ),
    ids=("discovery", "admission"),
)
async def test_a_flood_at_first_appearance_is_the_coded_verdict(
    db_session, appearance: dict, code: str
) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(
        appearance=appearance, traffic={0: 10, 1: 30}
    )
    await scenario.judge(1)
    assert scenario.evidence.to_dict()["reason_code"] == code
    assert scenario.evidence.reason == render(MessageRef(code))


async def test_an_established_endpoint_is_healthy_with_its_coded_sentence(db_session) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(traffic={0: 10, 12: 500})
    await scenario.judge(12)
    evidence = scenario.evidence
    assert evidence.to_dict()["reason_code"] == "waf.novelty.established_outside_window"
    assert evidence.to_dict()["reason_params"]["buckets"] == 12
