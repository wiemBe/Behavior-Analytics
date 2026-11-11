"""SOC-14a handover snapshot boundaries, registration and safe query shapes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.main import create_app
from app.models.enums import Severity
from app.schemas.handover import ActedOnSource, HandoverWatchIn
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
from app.workers import tasks
from app.workers.celery_app import celery_app

NOW = datetime(2026, 11, 11, 6, tzinfo=UTC)


def _row(number: int = 1, *, kind: QueueKind = QueueKind.ALERT) -> QueueRow:
    return QueueRow(
        kind=kind,
        family=None,
        id=str(uuid.UUID(int=number)),
        title="x" * 250,
        title_code=None,
        title_params=None,
        subject="must not leave the queue fold",
        detector="must not leave the queue fold",
        severity=Severity.HIGH,
        magnitude=None,
        occurrence_count=3,
        state="OPEN",
        triage=None,
        owner="analyst",
        owner_kind="app",
        waiting_since=NOW - timedelta(hours=2),
        age_seconds=7200,
        new_since_last_visit=True,
        change="NEW",
        alert=None,
        link=f"/alerts/{number}",
    )


def _queue(items: list[QueueRow], *, as_of: datetime = NOW) -> QueueOut:
    return QueueOut(
        as_of=as_of,
        marker=QueueMarker(
            compare_from=as_of - timedelta(hours=24),
            kind=CompareKind.OVERRIDE,
            marked_at=None,
        ),
        sources=[
            SourceStatus(
                kind=QueueKind.ALERT,
                state=SourceState.OK,
                count=len(items),
                new_count=len(items),
                permission=None,
                as_of=None,
                reason_code=None,
            )
        ],
        total=len(items),
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


def _ok_source(kind: str) -> ActedOnSource:
    return ActedOnSource(
        kind=kind,
        state=SourceState.OK,
        count=0,
        permission=None,
        reason_code=None,
    )


@pytest.mark.asyncio
async def test_build_uses_the_queue_once_with_the_frozen_window(monkeypatch) -> None:
    build = AsyncMock(return_value=_queue([_row()]))
    monkeypatch.setattr(handover, "build_queue", build)
    monkeypatch.setattr(handover, "latest", AsyncMock(return_value=None))
    guarded = AsyncMock(side_effect=[(_ok_source("triage"), []), (_ok_source("alert"), [])])
    monkeypatch.setattr(handover, "_guarded_actions", guarded)

    body, previous = await handover.build_body(
        object(),  # type: ignore[arg-type]
        Settings(),
        Principal("ada", permissions=frozenset({"read:*"})),
        now=NOW,
    )

    assert previous is None
    assert body.window_started_at == NOW - timedelta(hours=24)
    build.assert_awaited_once()
    call = build.await_args.kwargs
    assert call["scope"] is QueueScope.ALL
    assert call["since"] == body.window_started_at
    assert call["limit"] == 50 and call["offset"] == 0
    assert call["read_marker"] is False


def test_snapshot_has_only_the_contract_fields_and_cuts_titles() -> None:
    item = handover._snapshot_items([_row()], Settings(), set())[0]
    assert set(item.model_dump()) == {
        "kind",
        "family",
        "id",
        "link",
        "state",
        "owner",
        "owner_kind",
        "severity",
        "waiting_since",
        "age_seconds",
        "change",
        "watch",
        "over_sla",
        "title",
        "title_code",
        "title_params",
    }
    assert len(item.title) == 200
    assert item.over_sla is None


def test_only_offenses_have_an_sla_flag() -> None:
    row = _row(kind=QueueKind.OFFENSE)
    row.age_seconds = 24 * 3600
    assert handover._snapshot_items([row], Settings(), set())[0].over_sla is True
    row.age_seconds -= 1
    assert handover._snapshot_items([row], Settings(), set())[0].over_sla is False


@pytest.mark.asyncio
async def test_forbidden_acted_source_names_permission_and_runs_no_statement() -> None:
    work = AsyncMock(side_effect=AssertionError("forbidden source must not run"))
    source, items = await handover._guarded_actions(
        object(),  # type: ignore[arg-type]
        "triage",
        "read:anomalies",
        Principal("viewer"),
        work,
    )
    assert source.model_dump(mode="json") == {
        "kind": "triage",
        "state": "FORBIDDEN",
        "count": None,
        "permission": "read:anomalies",
        "reason_code": None,
    }
    assert items == []
    work.assert_not_awaited()


@pytest.mark.asyncio
async def test_triage_action_statement_never_selects_comment_text() -> None:
    class Result:
        def all(self) -> list[object]:
            return []

    class Session:
        statement: object | None = None

        async def execute(self, statement):  # type: ignore[no-untyped-def]
            self.statement = statement
            return Result()

    session = Session()
    assert await handover._triage_actions(session, "ada", NOW - timedelta(hours=1), NOW) == []  # type: ignore[arg-type]
    selected = str(session.statement).split("FROM", 1)[0].lower()
    assert "comment" not in selected


@pytest.mark.asyncio
async def test_oversized_frozen_body_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(handover, "MAX_BODY_BYTES", 1)
    monkeypatch.setattr(handover, "build_queue", AsyncMock(return_value=_queue([_row()])))
    monkeypatch.setattr(handover, "latest", AsyncMock(return_value=None))
    guarded = AsyncMock(side_effect=[(_ok_source("triage"), []), (_ok_source("alert"), [])])
    monkeypatch.setattr(handover, "_guarded_actions", guarded)
    with pytest.raises(ValueError, match="256 KiB"):
        await handover.build_body(
            object(),  # type: ignore[arg-type]
            Settings(),
            Principal("ada", permissions=frozenset({"read:*"})),
            now=NOW,
        )


def test_watch_and_body_limits_are_bounded() -> None:
    assert handover.MAX_ITEMS == 50
    assert handover.MAX_ACTIONS == 50
    assert handover.MAX_BODY_BYTES == 256 * 1024
    assert HandoverWatchIn(kind="alert", id="a").model_dump() == {
        "kind": QueueKind.ALERT,
        "id": "a",
    }


def test_retention_defaults_task_and_route_inventory() -> None:
    settings = Settings()
    assert (
        settings.handover_retention_days,
        settings.handover_retention_batch_size,
        settings.handover_retention_max_batches,
    ) == (90, 500, 20)
    assert celery_app.conf.beat_schedule["apply-handover-retention"] == {
        "task": "apply_handover_retention",
        "schedule": 86400.0,
    }
    assert "apply_handover_retention" in celery_app.tasks
    assert tasks.apply_handover_retention_task.name == "apply_handover_retention"

    methods = {
        (method.upper(), path)
        for path, operations in create_app().openapi()["paths"].items()
        for method in operations
        if path.startswith("/api/v1/handover")
    }
    assert ("GET", "/api/v1/handover/latest") in methods
    assert ("GET", "/api/v1/handover/preview") in methods
    assert ("POST", "/api/v1/handover") in methods
    assert not ({"PATCH", "DELETE"} & {method for method, _path in methods})
