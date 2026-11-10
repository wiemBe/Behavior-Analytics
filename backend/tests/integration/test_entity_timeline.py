"""SOC-16b entity timeline over HTTP and a real PostgreSQL schema."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.config import get_settings
from app.core.database import get_session
from app.main import create_app
from app.models.entity import EntityMention
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    DimensionAvailability,
    EvidenceStatus,
    IpsDimension,
    Severity,
    WafMeasurement,
    WafTelemetryStatus,
)
from app.models.explanation import AnomalyExplanation, AnomalyExplanationDimension
from app.models.ips import IpsEntity, IpsSourceBucket
from app.models.log_source import LogSourceAnomaly
from app.models.monitoring import CollectionWatermark
from app.models.offense import OffenseSnapshot
from app.models.waf import WafSourceBucket
from app.security.rbac import Principal
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_fleet_api import Client, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run entity timeline integration tests",
    ),
]

T0 = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
START = T0 - timedelta(hours=1)
END = T0 + timedelta(hours=6)
IP = "192.0.2.44"

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
ANOMALY_READER = Principal(
    subject="anomaly-reader", permissions=frozenset({"read:anomalies"})
)
NOBODY = Principal(subject="nobody", permissions=frozenset())


class TimelineClient(Client):
    """The shared integration client plus SQL text and settings overrides."""

    def __init__(
        self, principal: Principal, *, settings_update: dict[str, Any] | None = None
    ) -> None:
        super().__init__(principal)
        settings = get_settings().model_copy(update=settings_update or {})
        self.http.app.dependency_overrides[get_settings] = lambda: settings
        self.sql: list[str] = []

        @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
        def _capture(
            _conn: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            self.sql.append(statement)

    def request(self, method: str, url: str, **kwargs):  # type: ignore[no-untyped-def]
        self.sql = []
        return super().request(method, url, **kwargs)


def _url(instance_id: uuid.UUID, **params: Any) -> str:
    query: dict[str, Any] = {
        "instance_id": str(instance_id),
        "value": IP,
        "start": START.isoformat(),
        "end": END.isoformat(),
    }
    query.update(params)
    from urllib.parse import urlencode

    return f"/api/v1/entities/timeline?{urlencode(query)}"


def _mention(
    instance_id: uuid.UUID,
    *,
    source: str,
    ref_table: str,
    ref_id: str,
    occurred_at: datetime,
    kind: str = "IP",
    value_norm: str = IP,
    role: str = "SOURCE_IP",
) -> EntityMention:
    return EntityMention(
        instance_id=instance_id,
        kind=kind,
        value_norm=value_norm,
        role=role,
        source=source,
        ref_table=ref_table,
        ref_id=ref_id,
        occurred_at=occurred_at,
    )


def _snapshot(instance_id: uuid.UUID, offense_id: int) -> OffenseSnapshot:
    return OffenseSnapshot(
        instance_id=instance_id,
        qradar_offense_id=offense_id,
        captured_at=T0 + timedelta(hours=5),
        description=f"offense {offense_id}",
        status="OPEN",
        severity=7,
        is_assigned=False,
        start_time=T0,
        last_updated_time=T0 + timedelta(minutes=5),
        categories=[],
        source_addresses=[],
        local_destination_addresses=[],
        usernames=[],
        log_source_ids=[],
        rule_ids=[],
    )


def _watermark(instance_id: uuid.UUID) -> CollectionWatermark:
    now = datetime.now(UTC)
    return CollectionWatermark(
        instance_id=instance_id,
        collector="offense_snapshot",
        watermark_at=now,
        last_run_at=now,
        intervals_collected=1,
        consecutive_failures=0,
        collection_metadata={},
    )


def _ips_bucket(source_id: uuid.UUID) -> IpsSourceBucket:
    return IpsSourceBucket(
        log_source_id=source_id,
        bucket_start=T0,
        bucket_seconds=300,
        origin="LIVE",
        total_events=100,
        total_records=100,
        destination_port_measurement="EXACT",
        destination_port_coverage_ratio=1.0,
        destination_port_coverage_lower_bound=False,
        destination_ip_measurement="EXACT",
        destination_ip_coverage_ratio=1.0,
        destination_ip_coverage_lower_bound=False,
        signature_measurement="EXACT",
        signature_coverage_ratio=1.0,
        signature_coverage_lower_bound=False,
        source_ip_measurement="EXACT",
        source_ip_coverage_ratio=1.0,
        source_ip_coverage_lower_bound=False,
        port_fanout_measurement="EXACT",
        action_measurement="NOT_COLLECTED",
        action_coverage_ratio=None,
        action_coverage_lower_bound=None,
        contributors={},
        admitted={},
        below_admission_threshold={},
        rejected_over_quota={},
        untracked_entities={},
        untracked_events={},
        admission_truncated={},
        profile_version=1,
        normalizer_version=1,
        query_provenance={},
        in_maintenance=False,
        collected_at=T0 + timedelta(minutes=5),
    )


def _waf_bucket(source_id: uuid.UUID) -> WafSourceBucket:
    return WafSourceBucket(
        log_source_id=source_id,
        bucket_start=T0,
        bucket_seconds=300,
        origin="LIVE",
        profile_version=1,
        metric_semantics="test-v1",
        completeness=BucketCompleteness.COMPLETE,
        endpoint_coverage_ratio=1.0,
        total_events=100,
        parsed_events=100,
        group_count=0,
        groups_truncated=False,
        unusable_groups=0,
        observed_endpoints=0,
        tracked_endpoints_observed=0,
        admitted_endpoints=0,
        below_admission_threshold=0,
        rejected_over_quota=0,
        untracked_endpoints=0,
        untracked_requests=0,
        admission_truncated=False,
        zero_filled_endpoints=0,
        request_count_measurement=WafMeasurement.EXACT,
        sparse_endpoint_rows=True,
        in_maintenance=False,
        action_measurement=WafTelemetryStatus.NOT_COLLECTED,
        response_code_measurement=WafTelemetryStatus.NOT_COLLECTED,
        client_measurement=WafTelemetryStatus.EXACT,
        client_events=0,
        query_provenance={},
        collected_at=T0 + timedelta(minutes=5),
    )


async def _add_explanation(session: Any, source_id: uuid.UUID) -> None:
    anomaly = LogSourceAnomaly(
        log_source_id=source_id,
        anomaly_type=AnomalyType.VOLUME_SPIKE,
        severity=Severity.HIGH,
        detected_at=T0,
        anomaly_start=T0,
        anomaly_end=T0 + timedelta(minutes=5),
        state=AnomalyState.OPEN,
        evidence_status=EvidenceStatus.COMPLETE,
        details={},
    )
    session.add(anomaly)
    await session.flush()
    package = AnomalyExplanation(
        anomaly_id=anomaly.id,
        status=EvidenceStatus.COMPLETE,
        anomaly_window_start=T0,
        anomaly_window_end=T0 + timedelta(minutes=5),
        baseline_window_start=T0 - timedelta(hours=1),
        baseline_window_end=T0,
        comparison_strategy="test",
        anomaly_total_events=100,
        baseline_total_events=100,
        query_provenance={},
    )
    session.add(package)
    await session.flush()
    session.add_all(
        AnomalyExplanationDimension(
            explanation_id=package.id,
            dimension=dimension,
            availability=DimensionAvailability.AVAILABLE,
            truncated=False,
        )
        for dimension in ("source_ip", "destination_ip")
    )


def test_ip_mentions_from_three_lanes_are_merged_in_stable_time_order(db_schema) -> None:
    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        source = await make_log_source(
            session, instance, qradar_id=101, name="edge-fw", identifier=IP
        )
        entity = IpsEntity(
            instance_id=instance.id,
            log_source_id=source.id,
            dimension=IpsDimension.DESTINATION_IP,
            value_key=IP,
            display_name=IP,
            first_seen_at=T0,
            last_seen_at=T0,
            active=True,
        )
        session.add_all([entity, _snapshot(instance.id, 7001), _watermark(instance.id)])
        await session.flush()
        session.add_all(
            [
                _mention(
                    instance.id,
                    source="LOG_SOURCE",
                    ref_table="log_source",
                    ref_id=str(source.id),
                    occurred_at=T0 + timedelta(minutes=3),
                    role="HOST",
                ),
                _mention(
                    instance.id,
                    source="OFFENSE",
                    ref_table="offense_snapshot",
                    ref_id="7001",
                    occurred_at=T0 + timedelta(minutes=2),
                ),
                _mention(
                    instance.id,
                    source="IPS_ENTITY",
                    ref_table="ips_entity",
                    ref_id=str(entity.id),
                    occurred_at=T0 + timedelta(minutes=1),
                    role="DESTINATION_IP",
                ),
            ]
        )
        return instance.id

    instance_id = run(seed)
    response = TimelineClient(READER).get(_url(instance_id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["source"] for item in body["items"]] == [
        "LOG_SOURCE",
        "OFFENSE",
        "IPS_ENTITY",
    ]
    assert body["entity"] == {"kind": "IP", "value": IP, "value_norm": IP}
    assert body["items"][1]["severity"] == 7


def test_user_query_matches_its_bare_account_form(db_schema) -> None:
    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        session.add_all([_snapshot(instance.id, 7101), _watermark(instance.id)])
        await session.flush()
        session.add(
            _mention(
                instance.id,
                source="OFFENSE",
                ref_table="offense_snapshot",
                ref_id="7101",
                occurred_at=T0,
                kind="USER",
                value_norm="alice",
                role="USER",
            )
        )
        return instance.id

    instance_id = run(seed)
    response = TimelineClient(READER).get(
        _url(instance_id, kind="user", value="DOMAIN\\Alice")
    )
    assert response.status_code == 200, response.text
    assert [item["title"] for item in response.json()["items"]] == ["offense 7101"]


@pytest.mark.parametrize("mention_count", [5, 150])
def test_statement_count_is_four_for_one_parent_table_at_any_page_size(
    db_schema, mention_count: int
) -> None:
    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        session.add(_watermark(instance.id))
        for index in range(mention_count):
            offense_id = 8000 + index
            session.add(_snapshot(instance.id, offense_id))
            session.add(
                _mention(
                    instance.id,
                    source="OFFENSE",
                    ref_table="offense_snapshot",
                    ref_id=str(offense_id),
                    occurred_at=T0 + timedelta(seconds=index),
                )
            )
        return instance.id

    instance_id = run(seed)
    client = TimelineClient(READER)
    response = client.get(_url(instance_id, limit=200))
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == mention_count
    assert client.statements == 4


def test_truncated_cursor_pages_return_every_row_once(db_schema) -> None:
    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        session.add(_watermark(instance.id))
        for index in range(8):
            offense_id = 9000 + index
            session.add(_snapshot(instance.id, offense_id))
            session.add(
                _mention(
                    instance.id,
                    source="OFFENSE",
                    ref_table="offense_snapshot",
                    ref_id=str(offense_id),
                    occurred_at=T0,
                )
            )
        return instance.id

    instance_id = run(seed)
    client = TimelineClient(READER)
    titles: list[str] = []
    cursor: str | None = None
    states: list[str] = []
    while True:
        params: dict[str, Any] = {"limit": 3}
        if cursor is not None:
            params["cursor"] = cursor
        response = client.get(_url(instance_id, **params))
        assert response.status_code == 200, response.text
        body = response.json()
        states.append(body["state"])
        titles.extend(item["title"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert states == ["TRUNCATED", "TRUNCATED", "PARTIAL"]
    assert len(titles) == len(set(titles)) == 8
    assert set(titles) == {f"offense {9000 + index}" for index in range(8)}


def test_window_over_seven_days_and_tampered_cursor_are_coded_422s(db_schema) -> None:
    async def seed(session):  # type: ignore[no-untyped-def]
        return (await make_instance(session)).id

    instance_id = run(seed)
    client = TimelineClient(READER)
    too_long = client.get(
        _url(instance_id, start=(END - timedelta(days=7, microseconds=1)).isoformat())
    )
    assert too_long.status_code == 422
    assert too_long.json()["detail_code"] == "entity_timeline.input.window_max"

    invalid = client.get(_url(instance_id, cursor="tampered"))
    assert invalid.status_code == 422
    assert invalid.json()["detail_code"] == "entity_timeline.input.cursor_invalid"


def test_every_first_slice_lane_can_report_ok_from_stored_evidence(db_schema) -> None:
    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        source = await make_log_source(
            session, instance, qradar_id=301, name="coverage-source", identifier="other"
        )
        await _add_explanation(session, source.id)
        entity = IpsEntity(
            instance_id=instance.id,
            log_source_id=source.id,
            dimension=IpsDimension.DESTINATION_IP,
            value_key=IP,
            display_name=IP,
            first_seen_at=T0,
            last_seen_at=T0,
            active=True,
        )
        session.add_all(
            [_waf_bucket(source.id), _ips_bucket(source.id), entity, _watermark(instance.id)]
        )
        await session.flush()
        session.add_all(
            [
                _mention(
                    instance.id,
                    source="IPS_TOP",
                    ref_table="ips_source_bucket",
                    ref_id=str(source.id),
                    occurred_at=T0,
                ),
                _mention(
                    instance.id,
                    source="IPS_ENTITY",
                    ref_table="ips_entity",
                    ref_id=str(entity.id),
                    occurred_at=T0 + timedelta(minutes=1),
                    role="DESTINATION_IP",
                ),
            ]
        )
        return instance.id

    instance_id = run(seed)
    client = TimelineClient(
        READER,
        settings_update={"waf_collection_enabled": True, "ips_collection_enabled": True},
    )
    response = client.get(_url(instance_id))
    assert response.status_code == 200, response.text
    lanes = {lane["source"]: lane for lane in response.json()["lanes"]}
    assert set(lanes) == {
        "EXPLANATION",
        "WAF_TOP",
        "IPS_TOP",
        "IPS_ENTITY",
        "OFFENSE",
        "LOG_SOURCE",
    }
    assert {lane["state"] for lane in lanes.values()} == {"OK"}
    assert response.json()["state"] == "OK"


def test_disabled_and_forbidden_lanes_are_not_collected_without_zeroes_or_sql(
    db_schema,
) -> None:
    async def seed(session):  # type: ignore[no-untyped-def]
        return (await make_instance(session)).id

    instance_id = run(seed)
    client = TimelineClient(
        ANOMALY_READER,
        settings_update={
            "explanation_enabled": False,
            "waf_collection_enabled": False,
            "ips_collection_enabled": False,
        },
    )
    response = client.get(_url(instance_id))
    assert response.status_code == 200, response.text
    lanes = {lane["source"]: lane for lane in response.json()["lanes"]}
    for source in ("EXPLANATION", "WAF_TOP", "IPS_TOP", "IPS_ENTITY"):
        assert lanes[source]["state"] == "NOT_COLLECTED"
        assert lanes[source]["reason_code"] == "entity_timeline.reason.family_off"
        assert lanes[source]["total"] is None
    for source, permission in (
        ("OFFENSE", "read:offenses"),
        ("LOG_SOURCE", "read:fleet"),
    ):
        assert lanes[source]["state"] == "NOT_COLLECTED"
        assert lanes[source]["reason_code"] == "entity_timeline.reason.permission"
        assert lanes[source]["permission"] == permission
        assert lanes[source]["total"] is None
    sql = "\n".join(client.sql).lower()
    assert "offense_snapshot" not in sql
    assert "collection_watermark" not in sql
    assert client.statements == 2


def test_dangling_parent_is_dropped_counted_and_marks_its_lane_partial(db_schema) -> None:
    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        session.add(_watermark(instance.id))
        session.add(
            _mention(
                instance.id,
                source="OFFENSE",
                ref_table="offense_snapshot",
                ref_id="999999",
                occurred_at=T0,
            )
        )
        return instance.id

    instance_id = run(seed)
    response = TimelineClient(READER).get(_url(instance_id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["dangling"] == 1
    offense = next(lane for lane in body["lanes"] if lane["source"] == "OFFENSE")
    assert offense["state"] == "PARTIAL"
    assert offense["reason_code"] == "entity_timeline.reason.index_incomplete"


def test_console_scope_prevents_another_instances_mentions_from_leaking(db_schema) -> None:
    async def seed(session):  # type: ignore[no-untyped-def]
        first = await make_instance(session)
        second = await make_instance(session)
        first_source = await make_log_source(
            session, first, qradar_id=401, name="first-source", identifier=IP
        )
        second_source = await make_log_source(
            session, second, qradar_id=402, name="second-source", identifier=IP
        )
        session.add_all(
            [
                _mention(
                    first.id,
                    source="LOG_SOURCE",
                    ref_table="log_source",
                    ref_id=str(first_source.id),
                    occurred_at=T0,
                    role="HOST",
                ),
                _mention(
                    second.id,
                    source="LOG_SOURCE",
                    ref_table="log_source",
                    ref_id=str(second_source.id),
                    occurred_at=T0 + timedelta(minutes=1),
                    role="HOST",
                ),
                # A corrupted first-console mention must not hydrate a parent owned by
                # another console; it is a dangling row, never cross-console evidence.
                _mention(
                    first.id,
                    source="LOG_SOURCE",
                    ref_table="log_source",
                    ref_id=str(second_source.id),
                    occurred_at=T0 + timedelta(minutes=2),
                    role="HOST",
                ),
            ]
        )
        return first.id

    first_id = run(seed)
    response = TimelineClient(READER).get(_url(first_id))
    assert response.status_code == 200, response.text
    assert [item["title"] for item in response.json()["items"]] == [
        "Log source first-source"
    ]
    assert response.json()["dangling"] == 1


def test_route_requires_authentication_and_read_anomalies(db_schema) -> None:
    async def seed(session):  # type: ignore[no-untyped-def]
        return (await make_instance(session)).id

    instance_id = run(seed)
    forbidden = TimelineClient(NOBODY).get(_url(instance_id))
    assert forbidden.status_code == 403

    async def no_session():  # type: ignore[no-untyped-def]
        yield None

    app = create_app()
    oidc = get_settings().model_copy(update={"auth_provider": "oidc"})
    app.dependency_overrides[get_settings] = lambda: oidc
    app.dependency_overrides[get_session] = no_session
    unauthenticated = TestClient(app, raise_server_exceptions=False).get(_url(instance_id))
    assert unauthenticated.status_code == 401
