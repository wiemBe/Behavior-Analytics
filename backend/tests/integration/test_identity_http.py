"""Identity HTTP: permissions, profile lifecycle, honesty, incidents, and the queue."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.identity.collector import IdentityCollector
from app.models.enums import (
    AnomalyState,
    EvidenceStatus,
    IdentityAnomalyType,
    IdentityMeasurement,
    Severity,
)
from app.models.identity import AuditLog
from app.models.identity_anomaly import IdentityAnomaly
from app.models.identity_detection import (
    IdentityBucket,
    IdentityEventCount,
    IdentitySourceProfile,
    IdentityTopUsername,
)
from app.models.instance import QRadarInstance
from app.models.monitoring import CollectionWatermark
from app.providers.mock import MockQRadarProvider
from app.security.rbac import Principal
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_fleet_api import Client, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="set TEST_DATABASE_URL"),
]

ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
IDENTITY_READER = Principal(subject="id-reader", permissions=frozenset({"read:identity"}))
IDENTITY_ADMIN = Principal(subject="id-admin", permissions=frozenset({"admin:identity"}))
WAF_ADMIN = Principal(subject="waf-admin", permissions=frozenset({"admin:waf"}))
WAF_READER = Principal(
    subject="waf-reader", permissions=frozenset({"read:identity", "admin:waf"})
)
NOBODY = Principal(subject="nobody", permissions=frozenset())

BUCKET = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
EXACT = IdentityMeasurement.EXACT.value
TRUNCATED = IdentityMeasurement.TRUNCATED.value
MISSING = IdentityMeasurement.NOT_COLLECTED.value
BODY = {
    "event_id_property": "EventID",
    "username_property": "username",
    "enabled_event_ids": [4625],
    "product_label": "Windows",
}


def _sources() -> str:
    return "/api/v1/identity/sources"


def _source(log_source_id: uuid.UUID) -> str:
    return f"{_sources()}/{log_source_id}"


def _profile(log_source_id: uuid.UUID) -> str:
    return f"{_source(log_source_id)}/profile"


def test_reads_and_writes_follow_read_identity_and_admin_identity(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        source = await make_log_source(session, instance, qradar_id=1003, name="dc01")
        return source.id

    source_id = run(seed)
    missing = "00000000-0000-0000-0000-000000000099"

    assert Client(NOBODY).get(_sources()).status_code == 403
    assert Client(WAF_ADMIN).get(_sources()).status_code == 403
    assert Client(IDENTITY_ADMIN).get(_sources()).status_code == 403
    assert Client(READER).get(_sources()).status_code == 200
    assert Client(IDENTITY_READER).get(_sources()).status_code == 200

    # read:identity does not grant the write. admin:waf does not either, even
    # when the caller can already read.
    assert Client(WAF_READER).get(_sources()).status_code == 200
    for principal in (READER, IDENTITY_READER, WAF_ADMIN, WAF_READER, NOBODY):
        client = Client(principal)
        assert client.request("PUT", _profile(source_id), json=BODY).status_code == 403
        assert client.post(f"{_source(source_id)}/validate").status_code == 403
        assert client.post(f"{_source(source_id)}/enable").status_code == 403
        assert client.post(f"{_source(source_id)}/disable").status_code == 403

    # The admin check runs before the lookup, so a missing source is 403, not 404.
    refused = Client(READER).request("PUT", _profile(uuid.UUID(missing)), json=BODY)
    assert refused.status_code == 403

    created = Client(ADMIN).request("PUT", _profile(source_id), json=BODY)
    assert created.status_code == 200, created.text
    assert created.json()["validation_status"] == "UNVALIDATED"
    assert created.json()["enabled"] is False

    absent = Client(ADMIN).request("PUT", _profile(uuid.UUID(missing)), json=BODY)
    assert absent.status_code == 404
    assert absent.json()["detail_code"] == "identity.source.not_found"
    no_profile = Client(ADMIN).post(f"/api/v1/identity/sources/{missing}/enable")
    assert no_profile.status_code == 404
    assert no_profile.json()["detail_code"] == "identity.source.not_found"


def test_a_refused_property_is_not_echoed_and_audit_omits_the_username(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        source = await make_log_source(session, instance, qradar_id=1003, name="dc01")
        session.add(
            IdentityTopUsername(
                log_source_id=source.id,
                event_id=4625,
                bucket_start=BUCKET,
                rank=1,
                username="secret-user",
                truncated=False,
                event_count=5,
            )
        )
        return source.id

    source_id = run(seed)
    client = Client(ADMIN)
    refused = client.request(
        "PUT",
        _profile(source_id),
        json={**BODY, "username_property": "sourceip"},
    )
    assert refused.status_code == 422
    assert refused.json()["detail_code"] == "identity.profile.invalid"
    assert "sourceip" not in refused.text
    assert "secret-user" not in refused.text

    saved = client.request("PUT", _profile(source_id), json=BODY)
    assert saved.status_code == 200, saved.text
    validated = client.post(f"{_source(source_id)}/validate")
    assert validated.status_code == 200, validated.text
    assert validated.json()["validation_status"] == "VALID"
    assert "secret-user" not in validated.text
    enabled = client.post(f"{_source(source_id)}/enable")
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] is True

    async def audits(session):  # type: ignore[no-untyped-def]
        rows = (await session.scalars(select(AuditLog))).all()
        return [{"action": row.action, "detail": row.detail, "actor": row.actor} for row in rows]

    blob = json.dumps(run(audits))
    assert "secret-user" not in blob
    assert "identity.profile.created" in blob
    assert "identity.profile.validated" in blob
    assert "identity.profile.enabled" in blob


def test_enable_disable_and_a_version_mismatch_never_collects(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        source = await make_log_source(session, instance, qradar_id=1003, name="dc01")
        return instance.id, source.id

    instance_id, source_id = run(seed)
    client = Client(ADMIN)
    assert client.request("PUT", _profile(source_id), json=BODY).status_code == 200
    early = client.post(f"{_source(source_id)}/enable")
    assert early.status_code == 409
    assert early.json()["detail_code"] == "identity.profile.enable_refused"
    assert "UNVALIDATED" in early.json()["detail"]

    assert client.post(f"{_source(source_id)}/validate").json()["validation_status"] == "VALID"
    assert client.post(f"{_source(source_id)}/enable").json()["enabled"] is True

    changed = client.request(
        "PUT",
        _profile(source_id),
        json={**BODY, "enabled_event_ids": [4625, 4740]},
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["enabled"] is True
    assert body["profile_version"] == 2
    assert body["validation_status"] == "UNVALIDATED"
    again = client.post(f"{_source(source_id)}/enable")
    assert again.status_code == 409
    assert again.json()["detail_params"]["version"] == "2"

    async def collect(session):  # type: ignore[no-untyped-def]
        instance = await session.get(QRadarInstance, instance_id)
        report = await IdentityCollector(
            session,
            MockQRadarProvider(seed=1),
            settings=_collector_settings(),
            clock=lambda: datetime(2026, 11, 13, 12, 0, tzinfo=UTC),
        ).collect(instance)
        count = await session.scalar(select(func.count()).select_from(IdentityBucket))
        return report.status, int(count or 0)

    assert run(collect) == ("idle", 0)

    disabled = client.post(f"{_source(source_id)}/disable")
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    again_disabled = client.post(f"{_source(source_id)}/disable")
    assert again_disabled.json()["enabled"] is False

    async def disabled_audits(session):  # type: ignore[no-untyped-def]
        rows = (
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "identity.profile.disabled")
            )
        ).all()
        return [row.detail for row in rows]

    details = run(disabled_audits)
    assert details == [{"profile_version": 2}]


def test_an_unsupported_provider_is_a_coded_refusal(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        instance.provider_kind = "mcp"
        instance.sec_token = "probe-token"
        source = await make_log_source(session, instance, qradar_id=1003, name="dc01")
        return source.id

    source_id = run(seed)
    client = Client(ADMIN)
    assert client.request("PUT", _profile(source_id), json=BODY).status_code == 200
    refused = client.post(f"{_source(source_id)}/validate")
    assert refused.status_code == 422
    assert refused.json()["detail_code"] == "identity.provider.unsupported"
    assert "probe-token" not in refused.text


def test_the_source_list_reports_nulls_and_a_constant_statement_count(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed(session, count: int):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        for index in range(count):
            source = await make_log_source(
                session, instance, qradar_id=2000 + index, name=f"dc-{index:02d}"
            )
            session.add(
                IdentitySourceProfile(
                    instance_id=instance.id,
                    log_source_id=source.id,
                    enabled=False,
                    profile_version=1,
                    event_id_property="EventID",
                    username_property="username",
                    enabled_event_ids=[4625],
                    validation_status="UNVALIDATED",
                    validation_report={},
                )
            )
            session.add(
                IdentityBucket(
                    log_source_id=source.id,
                    bucket_start=BUCKET,
                    bucket_seconds=300,
                    measurement=MISSING,
                    population=None,
                    admission_truncated=False,
                    query_provenance={},
                    collected_at=BUCKET,
                )
            )
        return instance.id

    instance_id = run(lambda session: seed(session, 1))
    client = Client(READER)
    one = client.get(_sources())
    assert one.status_code == 200, one.text
    one_count = client.statements
    row = one.json()[0]
    assert row["collection"]["consecutive_failures"] is None
    assert row["last_bucket"]["population"] is None
    assert row["last_bucket"]["measurement"] == "NOT_COLLECTED"

    async def mark(session):  # type: ignore[no-untyped-def]
        session.add(
            CollectionWatermark(
                instance_id=instance_id,
                collector="identity_collection",
                watermark_at=BUCKET,
                consecutive_failures=2,
            )
        )

    run(mark)
    marked = Client(READER).get(_sources()).json()[0]
    assert marked["collection"]["consecutive_failures"] == 2

    run(lambda session: seed(session, 4))
    many_client = Client(READER)
    many = many_client.get(_sources())
    assert len(many.json()) == 5
    assert many_client.statements == one_count


def test_source_detail_keeps_every_honesty_state_and_a_constant_read(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        source = await make_log_source(session, instance, qradar_id=1003, name="dc01")
        session.add(
            IdentitySourceProfile(
                instance_id=instance.id,
                log_source_id=source.id,
                enabled=False,
                profile_version=1,
                event_id_property="EventID",
                username_property="username",
                enabled_event_ids=[4625, 4740, 1102],
                validation_status="UNVALIDATED",
                validation_report={},
            )
        )
        session.add_all(
            [
                _count(source.id, 4625, BUCKET, None, MISSING, MISSING, False),
                _count(source.id, 4740, BUCKET, 3, TRUNCATED, TRUNCATED, True),
                _count(source.id, 1102, BUCKET, 1, EXACT, EXACT, False),
                IdentityTopUsername(
                    log_source_id=source.id,
                    event_id=4740,
                    bucket_start=BUCKET,
                    rank=1,
                    username="carol",
                    truncated=False,
                    event_count=3,
                ),
                IdentityTopUsername(
                    log_source_id=source.id,
                    event_id=1102,
                    bucket_start=BUCKET,
                    rank=1,
                    username="auditor",
                    truncated=False,
                    event_count=1,
                ),
            ]
        )
        return source.id

    source_id = run(seed)
    params = {"start": "2026-11-12T00:00:00", "end": "2026-11-13T00:00:00"}
    client = Client(READER)
    body = client.get(_source(source_id), params=params)
    assert body.status_code == 200, body.text
    one_count = client.statements
    by_event = {row["event_id"]: row for row in body.json()["series"]}
    assert by_event[4625]["event_count"] is None
    assert by_event[4625]["measurement"] == "NOT_COLLECTED"
    assert by_event[4625]["usernames"] == []
    assert by_event[4740]["measurement"] == "TRUNCATED"
    assert by_event[4740]["admission_truncated"] is True
    assert [item["username"] for item in by_event[4740]["usernames"]] == ["carol"]
    assert by_event[1102]["event_count"] == 1
    assert by_event[1102]["measurement"] == "EXACT"
    assert "bob" not in body.text

    wide = client.get(
        _source(source_id),
        params={"start": "2026-11-01T00:00:00Z", "end": "2026-11-12T00:00:00Z"},
    )
    assert wide.status_code == 422
    assert wide.json()["detail_code"] == "identity.window.max"
    unordered = client.get(
        _source(source_id),
        params={"start": "2026-11-13T00:00:00Z", "end": "2026-11-12T00:00:00Z"},
    )
    assert unordered.status_code == 422
    assert unordered.json()["detail_code"] == "identity.window.order"

    async def more_names(session):  # type: ignore[no-untyped-def]
        for event_id in (4720, 4726, 4728, 4732, 4756):
            session.add(_count(source_id, event_id, BUCKET, 1, EXACT, EXACT, False))
            for rank in range(1, 6):
                session.add(
                    IdentityTopUsername(
                        log_source_id=source_id,
                        event_id=event_id,
                        bucket_start=BUCKET,
                        rank=rank,
                        username=f"user-{event_id}-{rank}",
                        truncated=False,
                        event_count=1,
                    )
                )

    run(more_names)
    many_client = Client(READER)
    many = many_client.get(_source(source_id), params=params)
    assert many.status_code == 200, many.text
    assert len(many.json()["series"]) == 8
    assert many_client.statements == one_count


def test_identity_incidents_list_detail_triage_and_queue(db_schema) -> None:  # type: ignore[no-untyped-def]
    detected = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)

    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        source = await make_log_source(session, instance, qradar_id=1003, name="dc01")
        incident = IdentityAnomaly(
            log_source_id=source.id,
            event_id=4726,
            detector_type=IdentityAnomalyType.IDENTITY_ACCOUNT_DELETED,
            fingerprint=f"fp-{uuid.uuid4().hex[:12]}",
            severity=Severity.HIGH,
            state=AnomalyState.OPEN,
            detected_at=detected,
            consecutive_buckets=1,
            policy_version=1,
            profile_version=1,
            catalog_version=1,
            details={
                "event_id": 4726,
                "measurement": "EXACT",
                "usernames": [{"rank": 1, "username": "frank", "count": 1}],
                "raw_event": "RAW-SECRET",
                "ratio": 4.0,
                "robust_z": 5.0,
                "score_status": "OK",
            },
            evidence_status=EvidenceStatus.COMPLETE,
            observed_measurement=IdentityMeasurement.EXACT,
            explanation=None,
        )
        session.add(incident)
        await session.flush()
        return incident.id

    incident_id = run(seed)
    params = {"since": "2026-11-12T00:00:00Z", "until": "2026-11-13T00:00:00Z"}
    client = Client(READER)
    listed = client.get("/api/v1/anomalies", params={**params, "limit": 50})
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    item = listed.json()["items"][0]
    assert item["subject_kind"] == "IDENTITY_SOURCE"
    assert item["identity"]["event_id"] == 4726
    assert item["identity"]["usernames"] == []
    assert "RAW-SECRET" not in listed.text

    kind = client.get(
        "/api/v1/anomalies",
        params={**params, "subject_kind": "IDENTITY_SOURCE"},
    )
    assert kind.json()["total"] == 1
    other = client.get(
        "/api/v1/anomalies", params={**params, "subject_kind": "LOG_SOURCE"}
    )
    assert other.json()["total"] == 0
    histogram = client.get("/api/v1/anomalies/histogram", params=params)
    assert histogram.status_code == 200, histogram.text
    assert histogram.json()["total"] == listed.json()["total"]
    assert "IDENTITY_SOURCE" in histogram.json()["families"]

    detail = client.get(f"/api/v1/anomalies/{incident_id}")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["identity"]["usernames"] == [{"rank": 1, "username": "frank", "count": 1}]
    assert payload["explanation_package"] is None
    assert payload["detection"]["event_id"] == 4726
    assert "RAW-SECRET" not in detail.text
    assert "raw_event" not in detail.text

    triage = client.get(f"/api/v1/anomalies/triage/identity/{incident_id}")
    assert triage.status_code == 200, triage.text
    assert triage.json()["status"] == "NEW"
    assert triage.json()["version"] == 0
    patched = Client(ADMIN).patch(
        f"/api/v1/anomalies/triage/identity/{incident_id}",
        json={"expected_version": 0, "status": "IN_PROGRESS"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "IN_PROGRESS"

    queue = client.get("/api/v1/queue", params={"family": "identity"})
    assert queue.status_code == 200, queue.text
    titles = [row["title"] for row in queue.json()["items"]]
    assert any("Account deleted" in title for title in titles)
    assert all("IDENTITY_ACCOUNT_DELETED" not in title for title in titles)


def _count(source_id, event_id, bucket, count, measurement, names, truncated):  # type: ignore[no-untyped-def]
    return IdentityEventCount(
        log_source_id=source_id,
        event_id=event_id,
        bucket_start=bucket,
        event_count=count,
        measurement=measurement,
        names_measurement=names,
        admission_truncated=truncated,
    )


def _collector_settings():  # type: ignore[no-untyped-def]
    from app.core.config import Settings

    return Settings(
        search_cadence_settle_seconds=0,
        collection_interval_seconds=300,
        collection_max_backfill_intervals=0,
        identity_max_sources_per_search=50,
        ariel_multi_bucket_max_rows=5000,
        search_cadence_max_searches_per_tick=16,
    )
