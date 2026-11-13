"""SOC-13b: baseline training exclusions over HTTP (create, list, revoke)."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from threading import Thread

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.anomaly import training_exclusions as te
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    EvidenceStatus,
    Severity,
    TriageEventKind,
)
from app.models.identity import AuditLog
from app.models.log_source import LogSourceAnomaly, LogSourceMetric
from app.models.training_exclusion import BaselineTrainingExclusion
from app.models.triage import AnomalyTriage, AnomalyTriageEvent
from app.security.rbac import Principal
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_fleet_api import Client, _maker, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the training exclusion API tests",
    ),
]

H = timedelta(hours=1)
BASE = (datetime.now(UTC) - timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
START, END = BASE, BASE + 2 * H
SECRET = "reason-text-that-must-stay-out-of-audit"

TRIAGER = Principal("ada", permissions=frozenset({"read:anomalies", "anomaly:triage"}))
READER = Principal("rita", permissions=frozenset({"read:*"}))
TRIAGE_ONLY = Principal("t", permissions=frozenset({"anomaly:triage"}))
NOBODY = Principal("nobody", permissions=frozenset())


def _iso(value: datetime) -> str:
    return value.isoformat()


def _body(start: datetime = START, end: datetime = END, reason: str = SECRET) -> dict:
    return {"starts_at": _iso(start), "ends_at": _iso(end), "reason": reason}


def _url(anomaly_id: uuid.UUID, family: str = "log_source") -> str:
    return f"/api/v1/anomalies/{family}/{anomaly_id}/training-exclusions"


async def _seed_one(session, *, state=AnomalyState.RESOLVED, metric=True):  # type: ignore[no-untyped-def]
    inst = await make_instance(session)
    source = await make_log_source(
        session, inst, qradar_id=7001, name=f"tx-{uuid.uuid4().hex[:6]}"
    )
    anomaly = LogSourceAnomaly(
        log_source_id=source.id,
        anomaly_type=AnomalyType.VOLUME_SPIKE,
        severity=Severity.HIGH,
        state=state,
        detected_at=START,
        resolved_at=END if state == AnomalyState.RESOLVED else None,
        evidence_status=EvidenceStatus.NOT_REQUESTED,
        details={},
    )
    session.add(anomaly)
    if metric:
        session.add(
            LogSourceMetric(log_source_id=source.id, bucket_start=START, bucket_seconds=300)
        )
    await session.flush()
    return anomaly.id, source.id


def _seed(**kw):  # type: ignore[no-untyped-def]
    async def work(session):  # type: ignore[no-untyped-def]
        return await _seed_one(session, **kw)

    return run(work)


def _counts() -> tuple[int, int, int]:
    async def work(session):  # type: ignore[no-untyped-def]
        return (
            await session.scalar(select(func.count()).select_from(BaselineTrainingExclusion)),
            await session.scalar(select(func.count()).select_from(AnomalyTriageEvent)),
            await session.scalar(select(func.count()).select_from(AuditLog)),
        )

    return run(work)


def _code(response) -> str:  # type: ignore[no-untyped-def]
    return response.json()["detail"]["code"] if isinstance(
        response.json()["detail"], dict
    ) else response.json()["detail_code"]


# --- authorization -----------------------------------------------------------------


@pytest.mark.parametrize("principal", [READER, TRIAGE_ONLY, NOBODY])
def test_create_without_both_permissions_is_refused_and_writes_nothing(
    db_schema, principal
) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, _ = _seed()
    response = Client(principal).post(_url(anomaly_id), json=_body())
    assert response.status_code == 403
    assert _counts() == (0, 0, 0)


def test_read_star_alone_cannot_create_but_can_list(db_schema) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, _ = _seed()
    assert Client(READER).post(_url(anomaly_id), json=_body()).status_code == 403
    assert Client(READER).get("/api/v1/behavior/training-exclusions").status_code == 200


def test_list_needs_read_anomalies_and_revoke_needs_triage(db_schema) -> None:  # type: ignore[no-untyped-def]
    assert Client(NOBODY).get("/api/v1/behavior/training-exclusions").status_code == 403
    assert Client(TRIAGE_ONLY).get("/api/v1/behavior/training-exclusions").status_code == 403
    url = f"/api/v1/behavior/training-exclusions/{uuid.uuid4()}/revoke"
    assert Client(READER).post(url, json={"reason": "x"}).status_code == 403
    assert Client(TRIAGE_ONLY).post(url, json={"reason": "x"}).status_code == 403


# --- create guards -------------------------------------------------------------------


def test_active_anomaly_is_409(db_schema) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, _ = _seed(state=AnomalyState.OPEN)
    response = Client(TRIAGER).post(_url(anomaly_id), json=_body())
    assert response.status_code == 409
    assert response.json()["detail_code"] == "baseline_exclusion.anomaly_active"
    assert _counts() == (0, 0, 0)


@pytest.mark.parametrize("family", ["waf", "ips", "property_watch", "rule", "nonsense"])
def test_other_family_is_422_unsupported(db_schema, family) -> None:  # type: ignore[no-untyped-def]
    response = Client(TRIAGER).post(_url(uuid.uuid4(), family), json=_body())
    assert response.status_code == 422
    assert response.json()["detail_code"] == "baseline_exclusion.family_unsupported"


def test_unknown_anomaly_is_404(db_schema) -> None:  # type: ignore[no-untyped-def]
    response = Client(TRIAGER).post(_url(uuid.uuid4()), json=_body())
    assert response.status_code == 404
    assert response.json()["detail_code"] == "baseline_exclusion.not_found"


def test_missing_bucket_width_fails_closed(db_schema) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, _ = _seed(metric=False)
    response = Client(TRIAGER).post(_url(anomaly_id), json=_body())
    assert response.status_code == 422
    assert response.json()["detail_code"] == "baseline_exclusion.interval_invalid"


@pytest.mark.parametrize(
    ("start", "end", "code"),
    [
        (START + timedelta(seconds=60), END, "interval_invalid"),
        (START, END + timedelta(seconds=60), "interval_invalid"),
        (END, START, "interval_invalid"),
        (START, START, "interval_invalid"),
        (START + timedelta(minutes=5), END, "interval_not_widened"),
        (START, END - timedelta(minutes=5), "interval_not_widened"),
        (START, START + timedelta(days=7, minutes=5), "interval_too_long"),
    ],
)
def test_bad_intervals_are_422_with_their_code(db_schema, start, end, code) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, _ = _seed()
    response = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, end))
    assert response.status_code == 422
    assert response.json()["detail_code"] == f"baseline_exclusion.{code}"
    assert _counts() == (0, 0, 0)


def test_naive_timestamp_is_invalid(db_schema) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, _ = _seed()
    body = _body()
    body["starts_at"] = START.replace(tzinfo=None).isoformat()
    response = Client(TRIAGER).post(_url(anomaly_id), json=body)
    assert response.status_code == 422
    assert response.json()["detail_code"] == "baseline_exclusion.interval_invalid"


@pytest.mark.parametrize("reason", ["", "   ", "x" * 1001])
def test_blank_or_long_reason_is_422(db_schema, reason) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, _ = _seed()
    response = Client(TRIAGER).post(_url(anomaly_id), json=_body(reason=reason))
    assert response.status_code == 422
    assert response.json()["detail_code"] == "baseline_exclusion.reason_invalid"
    assert _counts() == (0, 0, 0)


def test_extra_field_is_422(db_schema) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, _ = _seed()
    body = {**_body(), "subject_id": str(uuid.uuid4())}
    assert Client(TRIAGER).post(_url(anomaly_id), json=body).status_code == 422


# --- create, duplicate, overlap, cap -----------------------------------------------------


def test_create_widened_then_duplicate_is_200_and_writes_once(db_schema) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, source_id = _seed()
    client = Client(TRIAGER)
    body = _body(START - H, END + H)
    first = client.post(_url(anomaly_id), json=body)
    assert first.status_code == 201
    data = first.json()
    assert data["state"] == "PENDING_REBUILD"
    assert data["subject_kind"] == "log_source"
    assert data["subject_id"] == str(source_id)
    assert data["scope_key"] == "log_source:volume"
    assert data["origin_anomaly_id"] == str(anomaly_id)
    assert data["created_by"] == "ada"
    second = Client(TRIAGER).post(_url(anomaly_id), json=body)
    assert second.status_code == 200
    assert second.json()["id"] == data["id"]
    assert _counts() == (1, 1, 1)


def test_overlap_is_409_and_adjacent_is_allowed(db_schema) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, _ = _seed()
    assert Client(TRIAGER).post(_url(anomaly_id), json=_body()).status_code == 201
    overlap = Client(TRIAGER).post(_url(anomaly_id), json=_body(START - H, END + H))
    assert overlap.status_code == 409
    assert overlap.json()["detail_code"] == "baseline_exclusion.overlap"
    assert _counts()[0] == 1


def test_adjacent_interval_of_a_second_incident_is_allowed(db_schema) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, source_id = _seed()
    assert Client(TRIAGER).post(_url(anomaly_id), json=_body()).status_code == 201

    async def second(session):  # type: ignore[no-untyped-def]
        other = LogSourceAnomaly(
            log_source_id=source_id,
            anomaly_type=AnomalyType.VOLUME_SPIKE,
            severity=Severity.HIGH,
            state=AnomalyState.RESOLVED,
            detected_at=END,
            resolved_at=END + H,
            evidence_status=EvidenceStatus.NOT_REQUESTED,
            details={},
        )
        session.add(other)
        session.add(LogSourceMetric(log_source_id=source_id, bucket_start=END, bucket_seconds=300))
        await session.flush()
        return other.id

    other_id = run(second)
    assert (
        Client(TRIAGER).post(_url(other_id), json=_body(END, END + H)).status_code == 201
    )


def test_fifty_first_is_409(db_schema) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, source_id = _seed()

    async def fill(session):  # type: ignore[no-untyped-def]
        for i in range(te.MAX_RELEVANT_PER_SCOPE):
            start = BASE - timedelta(hours=4 * (i + 1))
            session.add(
                BaselineTrainingExclusion(
                    origin_family="log_source",
                    origin_anomaly_id=uuid.uuid4(),
                    subject_kind="log_source",
                    subject_id=source_id,
                    scope_key=te.LOG_SOURCE_VOLUME_SCOPE,
                    starts_at=start,
                    ends_at=start + H,
                    reason="seed",
                    created_by="seed",
                )
            )

    run(fill)
    response = Client(TRIAGER).post(_url(anomaly_id), json=_body())
    assert response.status_code == 409
    assert response.json()["detail_code"] == "baseline_exclusion.limit_reached"
    assert _counts()[0] == te.MAX_RELEVANT_PER_SCOPE


def test_lost_race_on_the_unique_index_rereads_and_returns_200(db_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, source_id = _seed()
    real = te.create

    async def racing(session, **kw):  # type: ignore[no-untyped-def]
        engine, maker = _maker()
        try:
            async with maker() as other:
                other.add(
                    BaselineTrainingExclusion(
                        origin_family="log_source",
                        origin_anomaly_id=anomaly_id,
                        subject_kind="log_source",
                        subject_id=source_id,
                        scope_key=te.LOG_SOURCE_VOLUME_SCOPE,
                        starts_at=START,
                        ends_at=END,
                        reason="winner",
                        created_by="other",
                    )
                )
                await other.commit()
        finally:
            await engine.dispose()
        raise IntegrityError("insert", {}, Exception("duplicate"))

    monkeypatch.setattr(te, "create", racing)
    response = Client(TRIAGER).post(_url(anomaly_id), json=_body())
    monkeypatch.setattr(te, "create", real)
    assert response.status_code == 200
    assert response.json()["created_by"] == "other"
    # The loser records no activity: the winner owns it.
    assert _counts() == (1, 0, 0)


def test_truly_concurrent_identical_creates_leave_one_row(db_schema) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, _ = _seed()
    results: list[int] = []

    def post() -> None:
        results.append(Client(TRIAGER).post(_url(anomaly_id), json=_body()).status_code)

    threads = [Thread(target=post) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(code in (200, 201) for code in results)
    assert _counts()[0] == 1


# --- activity and audit --------------------------------------------------------------------


def test_create_and_revoke_write_events_and_audit_without_the_reason(db_schema) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, source_id = _seed()
    created = Client(TRIAGER).post(_url(anomaly_id), json=_body()).json()
    revoke_url = f"/api/v1/behavior/training-exclusions/{created['id']}/revoke"
    assert Client(TRIAGER).post(revoke_url, json={"reason": "revoke-secret"}).status_code == 200

    async def read(session):  # type: ignore[no-untyped-def]
        events = (
            await session.scalars(
                select(AnomalyTriageEvent).order_by(AnomalyTriageEvent.created_at)
            )
        ).all()
        triage = (await session.scalars(select(AnomalyTriage))).one()
        audits = (await session.scalars(select(AuditLog).order_by(AuditLog.created_at))).all()
        return events, triage, audits

    events, triage, audits = run(read)
    assert triage.anomaly_id == anomaly_id and triage.version == 1
    assert [e.kind for e in events] == [
        TriageEventKind.BASELINE_EXCLUSION_CREATED,
        TriageEventKind.BASELINE_EXCLUSION_REVOKED,
    ]
    assert [e.to_value for e in events] == [created["id"], created["id"]]
    assert [e.comment for e in events] == [SECRET, "revoke-secret"]
    assert all(e.triage_version == 1 and e.actor == "ada" for e in events)
    assert [a.action for a in audits] == [
        "baseline_exclusion.create",
        "baseline_exclusion.revoke",
    ]
    for audit in audits:
        text = str(audit.detail)
        assert SECRET not in text and "revoke-secret" not in text
        assert audit.detail["subject"] == f"log_source:{source_id}"
        assert audit.detail["anomaly"] == f"log_source:{anomaly_id}"
        assert audit.object_id == created["id"]
        assert audit.detail["scope_key"] == "log_source:volume"
        assert audit.detail["actor"] == "ada"
        assert "starts_at" in audit.detail and "ends_at" in audit.detail


def test_event_joins_the_existing_triage_row_at_its_version(db_schema) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, _ = _seed()
    assert (
        Client(TRIAGER)
        .post(f"/api/v1/anomalies/triage/log_source/{anomaly_id}/comments", json={"text": "hi"})
        .status_code
        == 201
    )
    assert Client(TRIAGER).post(_url(anomaly_id), json=_body()).status_code == 201

    async def read(session):  # type: ignore[no-untyped-def]
        return (await session.scalars(select(AnomalyTriage))).all(), (
            await session.scalar(select(func.count()).select_from(AnomalyTriageEvent))
        )

    rows, events = run(read)
    assert len(rows) == 1 and events == 2


# --- revoke ----------------------------------------------------------------------------------


def test_revoke_twice_is_200_and_second_writes_nothing(db_schema) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, _ = _seed()
    created = Client(TRIAGER).post(_url(anomaly_id), json=_body()).json()
    url = f"/api/v1/behavior/training-exclusions/{created['id']}/revoke"
    first = Client(TRIAGER).post(url, json={"reason": "mistake"})
    assert first.status_code == 200
    assert first.json()["state"] == "REVOKED"
    after_first = _counts()
    second = Client(TRIAGER).post(url, json={"reason": "again"})
    assert second.status_code == 200
    assert second.json()["revocation_reason"] == "mistake"
    assert _counts() == after_first


def test_revoke_unknown_is_404_and_blank_reason_is_422(db_schema) -> None:  # type: ignore[no-untyped-def]
    unknown = Client(TRIAGER).post(
        f"/api/v1/behavior/training-exclusions/{uuid.uuid4()}/revoke", json={"reason": "x"}
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail_code"] == "baseline_exclusion.not_found"
    blank = Client(TRIAGER).post(
        f"/api/v1/behavior/training-exclusions/{uuid.uuid4()}/revoke", json={"reason": " "}
    )
    assert blank.status_code == 422
    assert blank.json()["detail_code"] == "baseline_exclusion.reason_invalid"


# --- list ------------------------------------------------------------------------------------


def test_list_is_bounded_newest_first_filtered_with_states(db_schema) -> None:  # type: ignore[no-untyped-def]
    anomaly_id, source_id = _seed()
    created = Client(TRIAGER).post(_url(anomaly_id), json=_body()).json()

    async def extra(session):  # type: ignore[no-untyped-def]
        now = datetime.now(UTC)
        old = now - timedelta(days=60)
        for index, (start, revoked) in enumerate([(old, False), (BASE - 10 * H, True)]):
            session.add(
                BaselineTrainingExclusion(
                    origin_family="log_source",
                    origin_anomaly_id=uuid.uuid4(),
                    subject_kind="log_source",
                    subject_id=source_id,
                    scope_key=te.LOG_SOURCE_VOLUME_SCOPE,
                    starts_at=start,
                    ends_at=start + H,
                    reason=f"seed{index}",
                    created_by="seed",
                    created_at=now + timedelta(minutes=index + 1),
                    revoked_by="seed" if revoked else None,
                    revoked_at=now if revoked else None,
                    revocation_reason="r" if revoked else None,
                )
            )

    run(extra)
    client = Client(READER)
    base = "/api/v1/behavior/training-exclusions"
    everything = client.get(base, params={"subject_id": str(source_id)}).json()
    assert everything["limit"] == 50
    assert [item["state"] for item in everything["items"]] == [
        "REVOKED",
        "OUTSIDE_LOOKBACK",
        "PENDING_REBUILD",
    ]
    assert everything["items"][2]["id"] == created["id"]
    for state, expected in [
        ("PENDING_REBUILD", 1),
        ("OUTSIDE_LOOKBACK", 1),
        ("REVOKED", 1),
        ("APPLIED", 0),
    ]:
        got = client.get(base, params={"state": state}).json()["items"]
        assert len(got) == expected, state
    assert len(client.get(base, params={"limit": 1}).json()["items"]) == 1
    assert client.get(base, params={"limit": 101}).status_code == 422
    assert client.get(base, params={"limit": 0}).status_code == 422
    assert client.get(base, params={"state": "NOPE"}).status_code == 422
    assert client.get(base, params={"subject_id": str(uuid.uuid4())}).json()["items"] == []
    assert client.get(base, params={"scope_key": "other"}).json()["items"] == []
    assert client.get(base, params={"subject_kind": "endpoint"}).json()["items"] == []
