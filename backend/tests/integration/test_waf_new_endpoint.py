"""WAF_NEW_ENDPOINT_ACCESS through the persisted engine path (task T-006b).

Every scenario builds a source with a day of history before the endpoint's first
appearance, then judges the buckets after it with the bulk path.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.enums import (
    AnomalyState,
    BucketCompleteness,
    WafAnomalyType,
    WafMeasurement,
    WafTelemetryStatus,
)
from app.models.waf import (
    WafAnomaly,
    WafDetectorState,
    WafEndpoint,
    WafEndpointMetric,
    WafSourceBucket,
)
from app.waf import engine as engine_module
from app.waf.behavior import detect_new_endpoint_access
from app.waf.engine import WafAnomalyEngine
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from app.waf.semantics import request_count_semantics
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_waf_productionization import STEP, T0, cfg
from tests.integration.test_waf_scale import counting

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NEW = WafAnomalyType.WAF_NEW_ENDPOINT_ACCESS
SEMANTICS = request_count_semantics(
    aggregation="SUM(eventcount)",
    normalizer_version=ENDPOINT_NORMALIZER_VERSION,
    profile_version=1,
)
#: One day of history: 288 five-minute buckets before `T0`.
HISTORY = 288


def novelty_settings(**overrides):  # type: ignore[no-untyped-def]
    values = {
        "anomaly_open_after_intervals": 2,
        "waf_new_endpoint_history_days": 1,
        "waf_new_endpoint_window_buckets": 12,
        "waf_new_endpoint_min_requests": 20,
        "waf_new_endpoint_min_buckets": 2,
    }
    values.update(overrides)
    return cfg(**values)


def source_bucket(
    source_id: uuid.UUID, at: datetime, **overrides  # type: ignore[no-untyped-def]
) -> WafSourceBucket:
    values = {
        "log_source_id": source_id,
        "bucket_start": at,
        "bucket_seconds": 300,
        "profile_version": 1,
        "metric_semantics": SEMANTICS,
        "completeness": BucketCompleteness.COMPLETE,
        "endpoint_coverage_ratio": 1.0,
        "total_events": 100,
        "parsed_events": 100,
        "group_count": 1,
        "groups_truncated": False,
        "unusable_groups": 0,
        "observed_endpoints": 1,
        "tracked_endpoints_observed": 1,
        "admitted_endpoints": 0,
        "below_admission_threshold": 0,
        "rejected_over_quota": 0,
        "untracked_endpoints": 0,
        "untracked_requests": 0,
        "admission_truncated": False,
        "zero_filled_endpoints": 0,
        "request_count_measurement": WafMeasurement.EXACT,
        "sparse_endpoint_rows": True,
        "in_maintenance": False,
        "action_measurement": WafTelemetryStatus.NOT_COLLECTED,
        "response_code_measurement": WafTelemetryStatus.NOT_COLLECTED,
        "client_measurement": WafTelemetryStatus.NOT_COLLECTED,
        "collection_source": "TEST",
        "query_provenance": {},
    }
    values.setdefault("origin", "LIVE")
    values.update(overrides)
    return WafSourceBucket(**values)


def metric_row(endpoint: WafEndpoint, at: datetime, requests: int) -> WafEndpointMetric:
    return WafEndpointMetric(
        waf_endpoint_id=endpoint.id,
        bucket_start=at,
        bucket_seconds=300,
        request_count=requests,
        distinct_client_count=None,
        endpoint_coverage_ratio=1.0,
        completeness=BucketCompleteness.COMPLETE,
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
    )


class Scenario:
    """A source whose endpoint first appeared at `T0`, with a day of history before it."""

    def __init__(self, session, settings) -> None:  # type: ignore[no-untyped-def]
        self.session = session
        self.settings = settings
        self.verdicts: list = []

    async def build(  # type: ignore[no-untyped-def]
        self,
        *,
        first_seen: datetime = T0,
        history_overrides: dict[int, dict] | None = None,
        skip_history: frozenset[int] = frozenset(),
        appearance: dict | None = None,
        later: dict[int, dict] | None = None,
        traffic: dict[int, int] | None = None,
        reactivated: bool = False,
    ):
        instance = await make_instance(self.session)
        source = await make_log_source(self.session, instance, qradar_id=312, name="waf-new")
        endpoint = WafEndpoint(
            instance_id=instance.id,
            log_source_id=source.id,
            virtual_host="portal.example",
            normalized_endpoint="/admin",
            http_method="GET",
            normalizer_version=ENDPOINT_NORMALIZER_VERSION,
            first_seen_at=first_seen,
            last_seen_at=T0,
            tracked_since=T0 if reactivated else first_seen,
            is_active=True,
        )
        self.session.add(endpoint)
        await self.session.flush()
        self.source, self.endpoint = source, endpoint
        overrides = history_overrides or {}
        self.session.add_all(
            source_bucket(source.id, first_seen - STEP * (HISTORY - i), **overrides.get(i, {}))
            for i in range(HISTORY)
            if i not in skip_history
        )
        self.buckets: dict[int, WafSourceBucket] = {}
        for k, requests in sorted((traffic or {}).items()):
            extra = (later or {}).get(k, {}) if k else (appearance or {})
            at = T0 + STEP * k
            bucket = source_bucket(source.id, at, **extra)
            self.buckets[k] = bucket
            self.session.add(bucket)
            self.session.add(metric_row(endpoint, at, requests))
        await self.session.flush()
        return self

    def engine(self) -> WafAnomalyEngine:
        def spy(ctx):  # type: ignore[no-untyped-def]
            verdict = detect_new_endpoint_access(ctx)
            self.verdicts.append(verdict)
            return verdict

        return WafAnomalyEngine(self.session, settings=self.settings, detectors={NEW: spy})

    async def judge(self, k: int):  # type: ignore[no-untyped-def]
        result = await self.engine().evaluate_bucket(self.source, self.buckets[k])
        (report,) = result.endpoint_reports
        return report

    @property
    def evidence(self):  # type: ignore[no-untyped-def]
        return self.verdicts[-1].evidence


async def test_verified_history_then_material_traffic_opens_an_incident(db_session) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(
        traffic={0: 10, 1: 30, 2: 30}
    )
    assert (await scenario.judge(0)).states[NEW.value] == AnomalyState.NORMAL.value
    first = await scenario.judge(1)
    assert first.states[NEW.value] == AnomalyState.CANDIDATE.value
    report = await scenario.judge(2)
    assert report.states[NEW.value] == AnomalyState.OPEN.value
    assert report.opened == [NEW.value]

    anomaly = await db_session.scalar(select(WafAnomaly).where(WafAnomaly.detector_type == NEW))
    assert anomaly is not None
    assert anomaly.state == AnomalyState.OPEN
    extra = anomaly.details["extra"]
    assert extra["observed"]["requests"] == 70
    assert extra["observed"]["buckets_with_traffic"] == 3
    assert extra["observed"]["measurement"]["requests"] == "EXACT"
    assert extra["expected"]["history_buckets_verified"] == HISTORY
    assert extra["measurement_completeness"]["history"] == "COMPLETE"
    assert anomaly.observed_value == 70.0


async def test_one_missing_history_bucket_is_insufficient_and_names_the_gap(db_session) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(
        skip_history=frozenset({100}), traffic={0: 10, 1: 30}
    )
    report = await scenario.judge(1)
    assert report.states[NEW.value] == AnomalyState.INSUFFICIENT_DATA.value
    assert f"1 of {HISTORY} history buckets were never collected" in scenario.evidence.reason
    assert await db_session.scalar(select(WafAnomaly)) is None


@pytest.mark.parametrize(
    ("override", "fragment"),
    (
        ({"groups_truncated": True}, "truncated endpoint discovery"),
        ({"admission_truncated": True}, "truncated endpoint admission"),
        ({"profile_version": 2}, "used profile version 2"),
    ),
    ids=("discovery", "admission", "profile"),
)
async def test_an_unsound_history_bucket_is_insufficient(
    db_session, override: dict, fragment: str
) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(
        history_overrides={50: override}, traffic={0: 10, 1: 30, 2: 30}
    )
    for k in (1, 2):
        report = await scenario.judge(k)
        assert report.states[NEW.value] == AnomalyState.INSUFFICIENT_DATA.value
    assert fragment in scenario.evidence.reason
    assert await db_session.scalar(select(WafAnomaly)) is None


async def test_a_history_under_another_normalizer_is_insufficient(db_session) -> None:
    other = request_count_semantics(
        aggregation="SUM(eventcount)",
        normalizer_version=ENDPOINT_NORMALIZER_VERSION + 1,
        profile_version=1,
    )
    scenario = await Scenario(db_session, novelty_settings()).build(
        history_overrides={9: {"metric_semantics": other}}, traffic={0: 10, 1: 30}
    )
    report = await scenario.judge(1)
    assert report.states[NEW.value] == AnomalyState.INSUFFICIENT_DATA.value
    assert "normalizer" in scenario.evidence.reason


@pytest.mark.parametrize(
    ("override", "fragment"),
    (
        ({"groups_truncated": True}, "discovery was truncated"),
        ({"admission_truncated": True}, "admission was truncated"),
    ),
    ids=("discovery", "admission"),
)
async def test_a_flood_at_first_appearance_is_insufficient(
    db_session, override: dict, fragment: str
) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(
        appearance=override, traffic={0: 10, 1: 30, 2: 30}
    )
    for k in (1, 2):
        report = await scenario.judge(k)
        assert report.states[NEW.value] == AnomalyState.INSUFFICIENT_DATA.value
    assert fragment in scenario.evidence.reason
    assert await db_session.scalar(select(WafAnomaly)) is None


async def test_an_endpoint_outside_its_window_is_established_and_costs_no_history_query(
    db_session,
) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(
        traffic={0: 10, 12: 500}
    )
    with counting(db_session) as statements:
        report = await scenario.judge(12)
    assert report.states[NEW.value] == AnomalyState.NORMAL.value
    assert "established endpoint" in scenario.evidence.reason
    assert not any("waf_source_bucket" in statement for statement in statements)
    assert not any("judged_at" in statement for statement in statements)


async def test_the_last_bucket_of_the_window_is_still_new(db_session) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(
        traffic={0: 10, 11: 500}
    )
    await scenario.judge(11)
    assert "established" not in scenario.evidence.reason


async def test_a_reactivated_endpoint_with_an_old_first_seen_is_established(db_session) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(
        first_seen=T0 - timedelta(days=30), reactivated=True, traffic={0: 500, 1: 500}
    )
    report = await scenario.judge(1)
    assert report.states[NEW.value] == AnomalyState.NORMAL.value
    assert "established endpoint" in scenario.evidence.reason
    assert await db_session.scalar(select(WafAnomaly)) is None


async def test_immaterial_traffic_is_healthy_with_exact_counts(db_session) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(traffic={0: 4, 1: 5})
    report = await scenario.judge(1)
    assert report.states[NEW.value] == AnomalyState.NORMAL.value
    evidence = scenario.evidence
    assert evidence.observed_value == 9.0
    assert "9 request(s) in 2 bucket(s)" in evidence.reason


async def test_immaterial_traffic_with_lower_bound_counts_is_insufficient(db_session) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(
        traffic={0: 4, 1: 5},
        appearance={"request_count_measurement": WafMeasurement.LOWER_BOUND},
    )
    report = await scenario.judge(1)
    assert report.states[NEW.value] == AnomalyState.INSUFFICIENT_DATA.value
    assert "lower bounds" in scenario.evidence.reason


async def test_a_bucket_never_collected_after_first_appearance_makes_counts_lower_bounds(
    db_session,
) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(traffic={0: 4, 2: 5})
    report = await scenario.judge(2)
    assert report.states[NEW.value] == AnomalyState.INSUFFICIENT_DATA.value
    assert "lower bounds" in scenario.evidence.reason


async def test_no_novelty_without_a_collected_first_appearance_bucket(db_session) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(traffic={1: 30})
    report = await scenario.judge(1)
    assert report.states[NEW.value] == AnomalyState.INSUFFICIENT_DATA.value
    assert "first-appearance bucket was never collected" in scenario.evidence.reason


async def test_the_detector_has_no_enabling_setting_and_is_registered() -> None:
    from app.anomaly.capabilities import WAF_DETECTORS

    assert engine_module.WAF_DETECTOR_FUNCTIONS[NEW] is detect_new_endpoint_access
    assert WAF_DETECTORS[NEW].enabling_setting is None


# ----------------------------------------------------- state creation lost to a conflict
async def test_a_state_lost_to_a_conflict_is_reloaded(db_session, monkeypatch) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(traffic={0: 10, 1: 30})
    original = WafAnomalyEngine._ensure_states
    raced: list[WafDetectorState] = []

    async def lose_the_race(self, ids, types, states):  # type: ignore[no-untyped-def]
        winner = WafDetectorState(
            waf_endpoint_id=scenario.endpoint.id,
            detector_type=NEW,
            consecutive_anomalous=0,
            consecutive_healthy=0,
            state=AnomalyState.NORMAL,
        )
        db_session.add(winner)
        await db_session.flush()
        raced.append(winner)
        await original(self, ids, types, states)
        assert not states  # the insert returned nothing: it lost

    monkeypatch.setattr(WafAnomalyEngine, "_ensure_states", lose_the_race)
    report = await scenario.judge(1)
    assert report.states[NEW.value] == AnomalyState.CANDIDATE.value
    states = (await db_session.scalars(select(WafDetectorState))).all()
    assert len(states) == 1 and states[0] is raced[0]


async def test_a_state_missing_after_reload_raises_naming_endpoint_and_detector(
    db_session, monkeypatch
) -> None:
    scenario = await Scenario(db_session, novelty_settings()).build(traffic={0: 10, 1: 30})

    async def create_nothing(self, ids, types, states):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(WafAnomalyEngine, "_ensure_states", create_nothing)
    with pytest.raises(RuntimeError) as raised:
        await scenario.judge(1)
    assert str(scenario.endpoint.id) in str(raised.value)
    assert NEW.value in str(raised.value)
