"""SOC-14a handover API, immutable snapshot, audit and bounded retention."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.models.alert import Alert
from app.models.enums import (
    AlertStatus,
    AnomalyFamily,
    Severity,
    TriageEventKind,
    TriageStatus,
)
from app.models.handover import ShiftHandover
from app.models.identity import AuditLog
from app.models.queue_marker import UserQueueMarker
from app.models.triage import AnomalyTriage, AnomalyTriageEvent
from app.schemas.queue import (
    CompareKind,
    QueueFiltersApplied,
    QueueKind,
    QueueMarker,
    QueueOut,
    QueueRow,
    QueueScope,
    SourceState,
    SourceStatus,
)
from app.security.rbac import Principal
from app.services import handover
from app.services.handover_retention import HandoverRetention
from tests.integration.test_fleet_api import Client, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run handover API tests",
    ),
]

URL = "/api/v1/handover"
ANALYST = Principal(
    "ada",
    permissions=frozenset(
        {"anomaly:triage", "read:anomalies", "read:alerts", "read:fleet"}
    ),
)
VIEWER = Principal("viewer", permissions=frozenset({"read:*"}))


def _queue(count: int, *, as_of: datetime) -> QueueOut:
    items = [
        QueueRow(
            kind=QueueKind.ALERT,
            family=None,
            id=str(uuid.UUID(int=index + 1)),
            title=f"Alert {index}",
            title_code=None,
            title_params=None,
            subject=None,
            detector=None,
            severity="HIGH",
            magnitude=None,
            occurrence_count=1,
            state="OPEN",
            triage=None,
            owner=None,
            owner_kind=None,
            waiting_since=as_of - timedelta(hours=1),
            age_seconds=3600,
            new_since_last_visit=True,
            change="NEW",
            alert=None,
            link=f"/alerts/{index + 1}",
        )
        for index in range(count)
    ]
    sources = [
        SourceStatus(
            kind=kind,
            state=(SourceState.FORBIDDEN if kind is QueueKind.OFFENSE else SourceState.OK),
            count=(
                None
                if kind is QueueKind.OFFENSE
                else (count if kind is QueueKind.ALERT else 0)
            ),
            new_count=(None if kind is QueueKind.OFFENSE else 0),
            permission=("read:offenses" if kind is QueueKind.OFFENSE else None),
            as_of=None,
            reason_code=None,
        )
        for kind in QueueKind
    ]
    return QueueOut(
        as_of=as_of,
        marker=QueueMarker(
            compare_from=as_of - timedelta(hours=24),
            kind=CompareKind.OVERRIDE,
            marked_at=None,
        ),
        sources=sources,
        total=count,
        total_exact=True,
        limit=50,
        offset=0,
        filters_applied=QueueFiltersApplied(
            scope=QueueScope.ALL,
            kind=list(QueueKind),
            family=[],
            severity=[],
            new_only=False,
            since=as_of - timedelta(hours=24),
        ),
        items=items,
    )


def _queue_builder(count: int) -> AsyncMock:
    async def build(*_args, **kwargs):  # type: ignore[no-untyped-def]
        return _queue(count, as_of=kwargs["now"])

    return AsyncMock(side_effect=build)


def test_viewer_post_is_403_and_writes_nothing(db_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    build = _queue_builder(1)
    monkeypatch.setattr(handover, "build_queue", build)
    response = Client(VIEWER).post(URL, json={"note": "not allowed", "watch": []})
    assert response.status_code == 403
    assert response.json()["detail_code"] == "auth.permission.missing"
    build.assert_not_awaited()

    async def counts(session):  # type: ignore[no-untyped-def]
        return (
            await session.scalar(select(func.count()).select_from(ShiftHandover)),
            await session.scalar(select(func.count()).select_from(AuditLog)),
        )

    assert run(counts) == (0, 0)


def test_create_freezes_safe_shape_audit_actions_and_windows(db_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    build = _queue_builder(1)
    monkeypatch.setattr(handover, "build_queue", build)
    provider = Mock(side_effect=AssertionError("handover must not build a provider"))
    monkeypatch.setattr("app.providers.factory.build_provider", provider)
    monkeypatch.setattr("app.providers.factory.build_provider_for_instance", provider)
    secret = "comment text must never leave anomaly_triage_event"
    marker_at = datetime.now(UTC) - timedelta(days=5)
    anomaly_id = uuid.uuid4()

    async def seed(session):  # type: ignore[no-untyped-def]
        triage = AnomalyTriage(
            family=AnomalyFamily.LOG_SOURCE,
            anomaly_id=anomaly_id,
            owner="ada",
            status=TriageStatus.IN_PROGRESS,
            updated_by="ada",
            version=1,
        )
        alert = Alert(
            dedup_key=f"handover-{uuid.uuid4()}",
            fingerprint=f"handover-{uuid.uuid4()}",
            title="acted alert",
            severity=Severity.HIGH,
            status=AlertStatus.RESOLVED,
            source_type="custom_rule",
            opened_at=datetime.now(UTC) - timedelta(hours=2),
            first_seen_at=datetime.now(UTC) - timedelta(hours=2),
            acknowledged_at=datetime.now(UTC) - timedelta(minutes=3),
            acknowledged_by="ada",
            resolved_at=datetime.now(UTC) - timedelta(minutes=2),
            resolved_by="ada",
            evidence_snapshot={},
            source_anomaly_ids=[],
        )
        session.add_all(
            [triage, alert, UserQueueMarker(owner_subject="ada", marked_at=marker_at)]
        )
        await session.flush()
        session.add(
            AnomalyTriageEvent(
                triage_id=triage.id,
                kind=TriageEventKind.COMMENT,
                actor="ada",
                created_at=datetime.now(UTC) - timedelta(minutes=1),
                from_value=None,
                to_value=None,
                comment=secret,
                triage_version=1,
            )
        )

    run(seed)
    watched = str(uuid.UUID(int=1))
    client = Client(ANALYST)
    response = client.post(
        URL,
        json={
            "note": "Night shift note",
            "watch": [
                {"kind": "alert", "id": watched},
                {"kind": "alert", "id": "missing"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert set(data) == {
        "id",
        "author",
        "written_at",
        "window_started_at",
        "previous_id",
        "note",
        "body",
    }
    assert set(data["body"]) == {
        "as_of",
        "window_started_at",
        "sources",
        "total",
        "total_exact",
        "truncated",
        "items",
        "acted_on",
        "acted_on_sources",
        "watch_dropped",
    }
    assert data["body"]["items"][0]["watch"] is True
    assert data["body"]["watch_dropped"] == [{"kind": "alert", "id": "missing"}]
    offense = next(s for s in data["body"]["sources"] if s["kind"] == "offense")
    assert offense["state"] == "FORBIDDEN"
    assert offense["permission"] == "read:offenses" and offense["count"] is None
    action = next(item for item in data["body"]["acted_on"] if item["event"] == "COMMENT")
    assert set(action) == {
        "kind",
        "id",
        "related_id",
        "link",
        "event",
        "at",
        "from_value",
        "to_value",
    }
    alert_actions = [item for item in data["body"]["acted_on"] if item["kind"] == "alert"]
    assert [(item["event"], item["from_value"], item["to_value"]) for item in alert_actions] == [
        ("RESOLVED", "ACKNOWLEDGED", "RESOLVED"),
        ("ACKNOWLEDGED", "OPEN", "ACKNOWLEDGED"),
    ]
    assert secret not in response.text
    build.assert_awaited_once()

    first_written = datetime.fromisoformat(data["written_at"])
    assert datetime.fromisoformat(data["window_started_at"]) == first_written - timedelta(
        hours=24
    )

    second = client.post(URL, json={"note": "Replacement note", "watch": []})
    assert second.status_code == 201
    assert datetime.fromisoformat(second.json()["window_started_at"]) == first_written

    latest = client.get(f"{URL}/latest")
    assert latest.status_code == 200
    assert set(latest.json()) == {"handover"}
    assert latest.json()["handover"]["id"] == second.json()["id"]
    listed = client.get(URL)
    assert listed.status_code == 200
    assert set(listed.json()) == {"items", "total", "limit", "offset"}
    assert set(listed.json()["items"][0]) == {
        "id",
        "author",
        "written_at",
        "window_started_at",
        "previous_id",
        "note_length",
        "note_excerpt",
    }
    detail = client.get(f"{URL}/{data['id']}")
    assert detail.status_code == 200 and detail.json()["note"] == "Night shift note"

    async def stored(session):  # type: ignore[no-untyped-def]
        marker = await session.get(UserQueueMarker, "ada")
        audits = (
            await session.scalars(
                select(AuditLog)
                .where(AuditLog.action == "handover.create")
                .order_by(AuditLog.created_at)
            )
        ).all()
        return marker.marked_at, [entry.detail for entry in audits]

    stored_marker, audits = run(stored)
    assert stored_marker == marker_at
    assert len(audits) == 2
    assert audits[0]["note_length"] == len("Night shift note")
    assert audits[0]["item_count"] == 1 and audits[0]["watch_count"] == 1
    assert "note" not in audits[0]
    assert secret not in str(audits)
    assert "Alert 0" not in str(audits)
    provider.assert_not_called()


def test_empty_latest_validation_not_found_and_failed_source(db_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = Client(ANALYST)
    assert client.get(f"{URL}/latest").json() == {"handover": None}
    missing = client.get(f"{URL}/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["detail_code"] == "handover.not_found"

    for payload, code in (
        ({"watch": []}, "handover.note_required"),
        ({"note": "", "watch": []}, "handover.note_required"),
        ({"note": "x" * 4001, "watch": []}, "handover.note_too_long"),
    ):
        response = client.post(URL, json=payload)
        assert response.status_code == 422
        assert response.json()["detail_code"] == code
    too_many = client.post(
        URL,
        json={"note": "n", "watch": [{"kind": "alert", "id": str(i)} for i in range(21)]},
    )
    assert too_many.status_code == 422
    assert too_many.json()["detail_code"] == "handover.watch_too_many"

    async def audit_count(session):  # type: ignore[no-untyped-def]
        return await session.scalar(select(func.count()).select_from(AuditLog))

    assert run(audit_count) == 0

    monkeypatch.setattr(handover, "build_queue", _queue_builder(1))
    monkeypatch.setattr(
        handover, "_triage_actions", AsyncMock(side_effect=RuntimeError("deliberate"))
    )
    created = client.post(URL, json={"note": "partial", "watch": []})
    assert created.status_code == 201
    source = next(
        item for item in created.json()["body"]["acted_on_sources"] if item["kind"] == "triage"
    )
    assert source["state"] == "FAILED" and source["count"] is None


@pytest.mark.parametrize("path", [URL, f"{URL}/preview"])
def test_statement_count_is_constant_for_one_and_fifty_items(
    db_schema, monkeypatch, path  # type: ignore[no-untyped-def]
) -> None:
    counts: list[int] = []
    for size in (1, 50):
        monkeypatch.setattr(handover, "build_queue", _queue_builder(size))
        client = Client(ANALYST)
        response = (
            client.post(path, json={"note": f"note {size}", "watch": []})
            if path == URL
            else client.get(path)
        )
        assert response.status_code in (200, 201), response.text
        counts.append(client.statements)
    assert counts[0] == counts[1]


def test_retention_deletes_old_parent_and_nulls_the_next_link(db_schema) -> None:  # type: ignore[no-untyped-def]
    old_id = uuid.uuid4()
    new_id = uuid.uuid4()
    now = datetime.now(UTC)
    body = {
        "as_of": now.isoformat(),
        "window_started_at": (now - timedelta(hours=24)).isoformat(),
        "sources": [],
        "total": 0,
        "total_exact": True,
        "truncated": False,
        "items": [],
        "acted_on": [],
        "acted_on_sources": [],
        "watch_dropped": [],
    }

    async def exercise(session):  # type: ignore[no-untyped-def]
        session.add(
            ShiftHandover(
                id=old_id,
                author_subject="old",
                written_at=now - timedelta(days=91),
                window_started_at=now - timedelta(days=92),
                note="old",
                body=body,
            )
        )
        await session.flush()
        session.add(
            ShiftHandover(
                id=new_id,
                author_subject="new",
                written_at=now,
                window_started_at=now - timedelta(days=91),
                previous_id=old_id,
                note="new",
                body=body,
            )
        )
        await session.commit()
        report = await HandoverRetention(
            session,
            Settings(
                handover_retention_days=90,
                handover_retention_batch_size=1,
                handover_retention_max_batches=2,
            ),
        ).run(now=now)
        await session.refresh(await session.get(ShiftHandover, new_id))
        child = await session.get(ShiftHandover, new_id)
        return report.as_dict(), await session.get(ShiftHandover, old_id), child.previous_id

    assert run(exercise) == ({"deleted": 1, "incomplete": False}, None, None)
