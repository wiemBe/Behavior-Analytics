"""SOC-6d2: `GET /rules/{id}/match-anomalies` (design §7.3).

One state per call, taken from the detector skips, plus the open incidents and
the newest closed one. Rows are inserted directly. The statement count is taken
around the handler, after the fixture has been flushed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api.deps import get_settings
from app.api.routes.rules import rule_match_section
from app.collectors.rule_collector import COLLECTOR_NAME
from app.core.config import Settings
from app.models.enums import AnomalyState
from app.models.monitoring import CollectionWatermark
from app.models.rule import AnalyticsRule, RuleMatchMetric, RuleMatchWindow
from app.models.rule_anomaly import RuleAnomaly
from app.rules.matches import floor_to_window
from tests.integration.factories import make_instance
from tests.integration.test_phase3_api import NOBODY, READER, client_for
from tests.integration.test_rule_anomaly_read import incident
from tests.integration.test_rule_match_collection import recorded_statements
from tests.integration.test_rule_match_engine import _engine, _settings, _silence_fixture

pytestmark = pytest.mark.integration

WIDTH = 3600
URL = "/api/v1/rules/{rule_id}/match-anomalies"


def cfg(**extra: object) -> Settings:
    return get_settings().model_copy(update={"rule_match_collection_enabled": True, **extra})


def fetch(rule_id: uuid.UUID, *, principal=READER, settings: Settings | None = None):
    client = client_for(principal)
    if settings is not None:
        client.app.dependency_overrides[get_settings] = lambda: settings
    try:
        return client.get(URL.format(rule_id=rule_id))
    finally:
        client.app.dependency_overrides.pop(get_settings, None)


def ok_body(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()


def instant(value: object) -> datetime:
    assert isinstance(value, str)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def assert_code(body: dict, state: str, code: str) -> None:
    assert body["state"] == state
    assert body["reason_code"] == code
    assert body["reason"]
    assert "observed" not in body
    assert "match_rate" not in body


def settled_start(now: datetime) -> datetime:
    return floor_to_window(now - timedelta(seconds=WIDTH * 2), WIDTH)


async def add_rule(session, inst, qradar_id: int, *, born: datetime, **kw) -> AnalyticsRule:
    rule = AnalyticsRule(
        instance_id=inst.id,
        qradar_id=qradar_id,
        name=kw.pop("name", f"rule-{qradar_id}"),
        rule_type=kw.pop("rule_type", "EVENT"),
        enabled=kw.pop("enabled", True),
        is_building_block=False,
        health_monitoring_enabled=kw.pop("monitoring", True),
        first_seen_at=born,
        qradar_created_at=born,
        qradar_modified_at=kw.pop("modified", None),
        origin="USER",
    )
    session.add(rule)
    await session.flush()
    return rule


def window(inst, start: datetime, *, status: str = "COMPLETE") -> RuleMatchWindow:
    return RuleMatchWindow(
        instance_id=inst.id,
        window_start=start,
        window_seconds=WIDTH,
        origin="LIVE",
        status=status,
        rows_returned=0,
        row_cap=5000,
        unknown_ids=0,
        query_version="v1",
        collected_at=start,
        attempts=1,
    )


def metric(rule: AnalyticsRule, start: datetime, events: int) -> RuleMatchMetric:
    return RuleMatchMetric(
        rule_id=rule.id,
        bucket_start=start,
        bucket_seconds=WIDTH,
        match_records=events,
        matched_events=events,
    )


async def watermark(session, inst, at: datetime) -> None:
    session.add(
        CollectionWatermark(
            instance_id=inst.id, collector=COLLECTOR_NAME, watermark_at=at
        )
    )
    await session.flush()


class TestStates:
    @pytest.mark.asyncio
    async def test_collection_off_is_not_collected_and_keeps_incidents(self, db_session) -> None:
        inst = await make_instance(db_session)
        now = datetime.now(UTC)
        start = settled_start(now)
        rule = await add_rule(
            db_session, inst, 1, born=start - timedelta(days=30), enabled=False
        )
        db_session.add(window(inst, start))
        opened = incident(rule, detector="RULE_SILENCE", observed_value=0.0, expected_value=4.0)
        db_session.add(opened)
        await watermark(db_session, inst, now)
        await db_session.commit()

        body = ok_body(fetch(rule.id, settings=cfg(rule_match_collection_enabled=False)))

        assert_code(body, "NOT_COLLECTED", "rule.match_anomaly.collection_off")
        assert instant(body["judged_through"]) == start + timedelta(seconds=WIDTH)
        assert [item["anomaly_id"] for item in body["incidents"]] == [str(opened.id)]
        assert body["counts"] is None

    @pytest.mark.asyncio
    async def test_no_window_is_not_collected_with_nothing_judged(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 1, born=datetime.now(UTC) - timedelta(days=30))
        opened = incident(rule)
        db_session.add(opened)
        await db_session.commit()

        body = ok_body(fetch(rule.id, settings=cfg()))

        assert_code(body, "NOT_COLLECTED", "rule.match_anomaly.no_window")
        assert body["judged_through"] is None
        assert [item["anomaly_id"] for item in body["incidents"]] == [str(opened.id)]

    @pytest.mark.asyncio
    async def test_not_event_names_lower_bound_or_not_measured(self, db_session) -> None:
        inst = await make_instance(db_session)
        now = datetime.now(UTC)
        start = settled_start(now)
        born = start - timedelta(days=30)
        common = await add_rule(db_session, inst, 1, born=born, rule_type="COMMON", enabled=False)
        flow = await add_rule(db_session, inst, 2, born=born, rule_type="FLOW")
        unknown = await add_rule(db_session, inst, 3, born=born, rule_type=None)
        db_session.add(window(inst, start))
        await db_session.commit()

        lower = ok_body(fetch(common.id, settings=cfg()))
        measured = ok_body(fetch(flow.id, settings=cfg()))
        missing = ok_body(fetch(unknown.id, settings=cfg()))

        assert_code(lower, "NOT_EVENT", "rule.match_anomaly.not_event")
        assert lower["match_evidence"] == "LOWER_BOUND"
        assert lower["reason_params"]["evidence"] == "LOWER_BOUND"
        assert_code(measured, "NOT_EVENT", "rule.match_anomaly.not_event")
        assert measured["match_evidence"] == "NOT_MEASURED"
        assert_code(missing, "NOT_EVENT", "rule.match_anomaly.not_event")
        assert missing["match_evidence"] == "NOT_MEASURED"

    @pytest.mark.asyncio
    async def test_disabled_names_the_inventory_sync_time(self, db_session) -> None:
        inst = await make_instance(db_session)
        now = datetime.now(UTC)
        start = settled_start(now)
        synced = start + timedelta(hours=2)
        rule = await add_rule(
            db_session, inst, 1, born=start - timedelta(days=30), enabled=False
        )
        db_session.add(window(inst, start))
        await watermark(db_session, inst, synced)
        await db_session.commit()

        body = ok_body(fetch(rule.id, settings=cfg()))

        assert_code(body, "DISABLED", "rule.match_anomaly.disabled")
        assert instant(body["inventory_synced_at"]) == synced
        assert instant(body["reason_params"]["synced_at"]) == synced

    @pytest.mark.asyncio
    async def test_monitoring_off_names_the_flag(self, db_session) -> None:
        inst = await make_instance(db_session)
        now = datetime.now(UTC)
        start = settled_start(now)
        rule = await add_rule(
            db_session, inst, 1, born=start - timedelta(days=30), monitoring=False
        )
        db_session.add(window(inst, start))
        await watermark(db_session, inst, now)
        await db_session.commit()

        body = ok_body(fetch(rule.id, settings=cfg()))

        assert_code(body, "MONITORING_OFF", "rule.match_anomaly.monitoring_off")
        assert "health_monitoring_enabled" in body["reason"]
        assert "`" not in body["reason"]

    @pytest.mark.asyncio
    async def test_modified_names_qradar_modified_time(self, db_session) -> None:
        inst = await make_instance(db_session)
        now = datetime.now(UTC)
        start = settled_start(now)
        modified = start - timedelta(days=1)
        rule = await add_rule(
            db_session, inst, 1, born=start - timedelta(days=30), modified=modified
        )
        db_session.add(window(inst, start))
        await watermark(db_session, inst, now)
        await db_session.commit()

        body = ok_body(fetch(rule.id, settings=cfg()))

        assert_code(body, "MODIFIED", "rule.match_anomaly.modified")
        assert instant(body["modified_at"]) == modified
        assert instant(body["reason_params"]["modified_at"]) == modified
        assert instant(body["window_start"]) == start
        assert instant(body["window_end"]) == start + timedelta(seconds=WIDTH)

    @pytest.mark.asyncio
    async def test_truncated_latest_window_is_not_judged(self, db_session) -> None:
        inst = await make_instance(db_session)
        now = datetime.now(UTC)
        start = settled_start(now)
        rule = await add_rule(db_session, inst, 1, born=start - timedelta(days=30))
        db_session.add(window(inst, start, status="TRUNCATED"))
        await watermark(db_session, inst, now)
        await db_session.commit()

        body = ok_body(fetch(rule.id, settings=cfg()))

        assert_code(body, "TRUNCATED", "rule.match_anomaly.truncated")
        assert body["counts"] is None
        assert instant(body["window_start"]) == start

    @pytest.mark.asyncio
    async def test_span_incomplete_names_the_missing_windows(self, db_session) -> None:
        inst = await make_instance(db_session)
        now = datetime.now(UTC)
        start = settled_start(now)
        rule = await add_rule(db_session, inst, 1, born=start - timedelta(days=30))
        db_session.add(window(inst, start))
        await watermark(db_session, inst, now)
        await db_session.commit()

        body = ok_body(fetch(rule.id, settings=cfg()))

        assert_code(body, "INSUFFICIENT_DATA", "rule.match_anomaly.span_incomplete")
        counts = body["counts"]
        assert counts["expected_windows"] == 168
        assert counts["missing_windows"] == 168
        assert counts["complete_windows"] == 0
        assert counts["truncated_windows"] == 0
        assert counts["failed_windows"] == 0
        assert counts["wrong_width_windows"] == 0
        assert counts["baseline_days"] == 7
        assert counts["presence"] is None and counts["median"] is None
        assert body["reason_params"]["missing"] == 168
        assert body["reason_params"]["expected"] == 168
        assert instant(body["window_start"]) == start
        assert instant(body["window_end"]) == start + timedelta(seconds=WIDTH)

    @pytest.mark.asyncio
    async def test_a_rule_younger_than_seven_days_is_not_judged(self, db_session) -> None:
        inst = await make_instance(db_session)
        now = datetime.now(UTC)
        start = settled_start(now)
        rule = await add_rule(db_session, inst, 1, born=start - timedelta(days=1))
        db_session.add(window(inst, start))
        await watermark(db_session, inst, now)
        await db_session.commit()

        body = ok_body(fetch(rule.id, settings=cfg()))

        assert_code(body, "INSUFFICIENT_DATA", "rule.match_anomaly.rule_too_new")
        assert body["counts"]["baseline_days"] == 7
        assert body["reason_params"]["days"] == 7
        assert instant(body["window_start"]) == start
        assert instant(body["window_end"]) == start + timedelta(seconds=WIDTH)

    @pytest.mark.asyncio
    async def test_stale_inventory_names_the_sync_and_the_window(self, db_session) -> None:
        inst = await make_instance(db_session)
        now = datetime.now(UTC)
        start = settled_start(now)
        synced = start
        rule = await add_rule(db_session, inst, 1, born=start - timedelta(days=30))
        db_session.add(window(inst, start))
        db_session.add(metric(rule, start, 10))
        await watermark(db_session, inst, synced)
        await db_session.commit()

        body = ok_body(fetch(rule.id, settings=cfg()))

        assert_code(body, "INSUFFICIENT_DATA", "rule.match_anomaly.stale_inventory")
        assert instant(body["inventory_synced_at"]) == synced
        assert instant(body["window_end"]) == start + timedelta(seconds=WIDTH)
        assert instant(body["reason_params"]["synced_at"]) == synced
        assert body["counts"] is None

    @pytest.mark.asyncio
    async def test_presence_below_the_gate_names_the_fraction_not_a_zero_rate(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        now = datetime.now(UTC)
        start = settled_start(now)
        rule = await add_rule(db_session, inst, 1, born=start - timedelta(days=30))
        rows = [window(inst, start)]
        # The hour before the judged zero has events, so the streak stays one window.
        # 100 of the 168 baseline hours match; 68 are proven zeros. Median clears the
        # floor, presence does not.
        for hour in range(1, 169):
            slot = start - timedelta(hours=hour)
            rows.append(window(inst, slot))
            if hour <= 100:
                rows.append(metric(rule, slot, 10))
        db_session.add_all(rows)
        await watermark(db_session, inst, now)
        await db_session.commit()

        body = ok_body(fetch(rule.id, settings=cfg()))

        assert_code(body, "INSUFFICIENT_DATA", "rule.match_anomaly.presence_below")
        counts = body["counts"]
        assert counts["presence"] == pytest.approx(100 / 168)
        assert counts["presence_minimum"] == cfg().rule_silence_min_presence
        assert counts["baseline_days"] == 7
        assert counts["median"] is None
        assert body["reason_params"]["presence"]["ratio"] == pytest.approx(100 / 168)
        assert instant(body["window_start"]) == start
        assert instant(body["window_end"]) == start + timedelta(seconds=WIDTH)

    @pytest.mark.asyncio
    async def test_a_storm_cell_still_learning_names_samples_and_weeks(self, db_session) -> None:
        inst = await make_instance(db_session)
        now = datetime.now(UTC)
        start = settled_start(now)
        rule = await add_rule(db_session, inst, 1, born=start - timedelta(days=90))
        rows = [window(inst, start), metric(rule, start, 5)]
        for week in range(1, 4):
            slot = start - timedelta(days=7 * week)
            rows.append(window(inst, slot))
            rows.append(metric(rule, slot, 5))
        db_session.add_all(rows)
        await watermark(db_session, inst, now)
        await db_session.commit()

        body = ok_body(fetch(rule.id, settings=cfg()))
        used = cfg()

        assert_code(body, "INSUFFICIENT_DATA", "rule.match_anomaly.cell_learning")
        counts = body["counts"]
        assert counts["sample_count"] == 3
        assert counts["visits"] == 3
        assert counts["minimum_samples"] == used.rule_storm_min_samples
        assert counts["minimum_visits"] == used.rule_storm_min_visits
        assert counts["presence"] is None and counts["median"] is None
        assert instant(body["window_start"]) == start
        assert instant(body["window_end"]) == start + timedelta(seconds=WIDTH)

    @pytest.mark.asyncio
    async def test_empty_ok_has_no_incidents_and_a_settled_window(self, db_session) -> None:
        inst = await make_instance(db_session)
        now = datetime.now(UTC)
        start = settled_start(now)
        rule = await add_rule(db_session, inst, 1, born=start - timedelta(days=90))
        rows = [window(inst, start), metric(rule, start, 5)]
        for week in range(1, 9):
            slot = start - timedelta(days=7 * week)
            rows.append(window(inst, slot))
            rows.append(metric(rule, slot, 5))
        db_session.add_all(rows)
        await watermark(db_session, inst, now)
        await db_session.commit()

        body = ok_body(fetch(rule.id, settings=cfg()))

        assert_code(body, "OK", "rule.match_anomaly.judged_clear")
        assert body["incidents"] == []
        assert body["counts"] is None
        assert instant(body["judged_through"]) == start + timedelta(seconds=WIDTH)
        assert instant(body["reason_params"]["judged_through"]) == start + timedelta(seconds=WIDTH)


class TestPrecedenceAndIncidents:
    @pytest.mark.asyncio
    async def test_disabled_wins_over_a_truncated_window(self, db_session) -> None:
        inst = await make_instance(db_session)
        now = datetime.now(UTC)
        start = settled_start(now)
        synced = now
        rule = await add_rule(
            db_session, inst, 1, born=start - timedelta(days=30), enabled=False
        )
        db_session.add(window(inst, start, status="TRUNCATED"))
        await watermark(db_session, inst, synced)
        await db_session.commit()

        body = ok_body(fetch(rule.id, settings=cfg()))

        assert_code(body, "DISABLED", "rule.match_anomaly.disabled")
        assert instant(body["inventory_synced_at"]) == synced

    @pytest.mark.asyncio
    async def test_an_open_incident_is_returned_with_insufficient_data(self, db_session) -> None:
        inst = await make_instance(db_session)
        now = datetime.now(UTC)
        start = settled_start(now)
        rule = await add_rule(db_session, inst, 1, born=start - timedelta(days=30))
        db_session.add(window(inst, start))
        db_session.add(metric(rule, start, 10))
        await watermark(db_session, inst, start)
        opened_at = now - timedelta(hours=2)
        recovering_at = now - timedelta(hours=1)
        opened = incident(
            rule,
            detector="RULE_SILENCE",
            state=AnomalyState.OPEN,
            observed_value=0.0,
            expected_value=11.0,
            consecutive_buckets=4,
            anomaly_start=opened_at,
            detected_at=opened_at,
            details={
                "baseline_low": 2.0,
                "baseline_high": 30.0,
                "extra": {"ratio_to_baseline": 0.5},
            },
        )
        recovering = incident(
            rule,
            state=AnomalyState.RECOVERING,
            detected_at=recovering_at,
            anomaly_start=recovering_at,
            observed_value=40.0,
            expected_value=8.0,
            deviation_ratio=5.0,
            consecutive_buckets=2,
        )
        newer = incident(
            rule,
            state=AnomalyState.RESOLVED,
            detected_at=now - timedelta(days=2),
            resolved_at=now - timedelta(days=1),
            observed_value=1.0,
            expected_value=9.0,
        )
        older = incident(
            rule,
            state=AnomalyState.RESOLVED,
            detected_at=now - timedelta(days=11),
            resolved_at=now - timedelta(days=10),
        )
        candidate = incident(rule, state=AnomalyState.CANDIDATE, detected_at=now)
        db_session.add_all([opened, recovering, newer, older, candidate])
        await db_session.commit()

        body = ok_body(fetch(rule.id, settings=cfg()))

        assert_code(body, "INSUFFICIENT_DATA", "rule.match_anomaly.stale_inventory")
        assert [item["anomaly_id"] for item in body["incidents"]] == [
            str(recovering.id),
            str(opened.id),
            str(newer.id),
        ]
        live = body["incidents"][1]
        assert live["detector"] == "RULE_SILENCE"
        assert live["observed"] == 0.0 and live["expected"] == 11.0
        assert live["baseline_low"] == 2.0 and live["baseline_high"] == 30.0
        assert live["consecutive_buckets"] == 4
        assert live["ratio"] == 0.5
        assert instant(live["started"]) == opened_at
        assert live["ended"] is None and live["state"] == "OPEN"
        closed = body["incidents"][2]
        assert closed["state"] == "RESOLVED"
        assert instant(closed["ended"]) == now - timedelta(days=1)

    @pytest.mark.asyncio
    async def test_payload_agrees_with_an_engine_opened_silence(self, db_session) -> None:
        instance, rule, starts, clock = await _silence_fixture(db_session)
        rule_id = rule.id
        await _engine(db_session, clock).evaluate(instance, window_starts=starts)
        await db_session.commit()
        stored = await db_session.scalar(select(RuleAnomaly).where(RuleAnomaly.rule_id == rule_id))

        body = ok_body(fetch(rule_id, settings=_settings()))

        assert stored is not None
        assert_code(body, "OK", "rule.match_anomaly.judged")
        assert len(body["incidents"]) == 1
        item = body["incidents"][0]
        assert item["anomaly_id"] == str(stored.id)
        assert item["detector"] == "RULE_SILENCE"
        assert item["observed"] == 0.0
        assert item["expected"] == stored.expected_value
        assert stored.expected_value == 10.0
        assert instant(body["judged_through"]) == starts[-1] + timedelta(seconds=WIDTH)


class TestCostAndAccess:
    @pytest.mark.asyncio
    async def test_statement_count_is_constant_at_10_and_500_windows(self, db_session) -> None:
        inst = await make_instance(db_session)
        now = datetime.now(UTC)
        start = settled_start(now)
        rule = await add_rule(db_session, inst, 1, born=start - timedelta(days=30))
        db_session.add_all(window(inst, start - timedelta(hours=hour)) for hour in range(10))
        await watermark(db_session, inst, now)
        await db_session.flush()
        settings = cfg()
        counts: dict[int, int] = {}
        with recorded_statements(db_session) as first:
            await rule_match_section(rule.id, db_session, settings)
        counts[10] = len(first)
        assert sum("rule-section windows" in statement for statement in first) == 1
        db_session.add_all(
            window(inst, start - timedelta(hours=hour)) for hour in range(10, 500)
        )
        await db_session.flush()
        with recorded_statements(db_session) as second:
            await rule_match_section(rule.id, db_session, settings)
        counts[500] = len(second)
        assert sum("rule-section windows" in statement for statement in second) == 1

        # SOC-13h adds one SELECT on baseline_training_exclusion (scope
        # rule:silence) so this page holds out the same windows the engine
        # does. Storm's predicate stays inside the existing storm statement.
        # 10 windows and 500 windows both cost 8; the equality stays exact.
        assert counts[10] == counts[500] == 8, counts

    def test_unknown_rule_is_404(self, db_schema) -> None:
        response = fetch(uuid.uuid4(), settings=cfg())
        assert response.status_code == 404
        assert response.json()["detail"] == "rule not found"

    def test_without_read_permission_is_403(self, db_schema) -> None:
        response = fetch(uuid.uuid4(), principal=NOBODY, settings=cfg())
        assert response.status_code == 403
