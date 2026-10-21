"""Alert lifecycle service: open, dedup, acknowledge, resolve, reopen. DB-gated."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select

from app.alerts.service import AlertInput, AlertService
from app.models.alert import Alert
from app.models.enums import AlertStatus, AlertTransition, Severity

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
OPENED_AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _input(fp: str = "anomaly:abc") -> AlertInput:
    return AlertInput(
        fingerprint=fp, title="t", severity=Severity.HIGH,
        source_type="log_source", source_id=uuid.uuid4(),
        evidence={"observed_value": 3.0}, source_anomaly_ids=[str(uuid.uuid4())],
    )


async def test_open_then_update_dedups(db_session) -> None:
    svc = AlertService(db_session)
    first = await svc.open_or_update(_input())
    assert first.opened and first.transition is AlertTransition.OPENED

    second = await svc.open_or_update(_input())
    assert second.updated and not second.opened
    assert second.transition is None  # dedup: no notification on update
    assert second.alert.id == first.alert.id
    assert second.alert.occurrence_count == 2


async def test_acknowledge_transition(db_session) -> None:
    svc = AlertService(db_session)
    r = await svc.open_or_update(_input("anomaly:ack"))
    ack = await svc.acknowledge(r.alert.id, actor="analyst")
    assert ack.alert.status == AlertStatus.ACKNOWLEDGED
    assert ack.alert.acknowledged_by == "analyst"
    assert ack.transition is AlertTransition.ACKNOWLEDGED
    # Idempotent: acknowledging again is a no-op transition.
    again = await svc.acknowledge(r.alert.id, actor="analyst")
    assert again.transition is None


async def test_resolve_then_reopen_allows_new_alert(db_session) -> None:
    svc = AlertService(db_session)
    r1 = await svc.open_or_update(_input("anomaly:cycle"))
    await svc.resolve(r1.alert.id, actor="analyst", reason="fixed")
    # After resolution the same condition may open a brand-new alert.
    r2 = await svc.open_or_update(_input("anomaly:cycle"))
    assert r2.opened
    assert r2.alert.id != r1.alert.id


async def test_resolve_by_fingerprint(db_session) -> None:
    svc = AlertService(db_session)
    await svc.open_or_update(_input("anomaly:byfp"))
    result = await svc.resolve_by_fingerprint("anomaly:byfp", actor="engine", reason="recovered")
    assert result is not None
    assert result.alert.status == AlertStatus.RESOLVED
    # Nothing active left to resolve.
    assert await svc.resolve_by_fingerprint("anomaly:byfp", actor="engine") is None


@contextmanager
def _counting(session):  # type: ignore[no-untyped-def]
    """Record every statement the engine sends while the block runs."""
    statements: list[str] = []
    engine = session.bind.sync_engine

    def record(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def _seed_open(session, *fingerprints: str) -> dict[str, Alert]:
    alerts = [
        Alert(
            dedup_key=fp,
            fingerprint=fp,
            title=f"seed {fp}",
            severity=Severity.HIGH,
            status=AlertStatus.OPEN,
            source_type="log_source",
            source_id=uuid.uuid4(),
            opened_at=OPENED_AT,
            first_seen_at=OPENED_AT,
            last_seen_at=OPENED_AT,
            occurrence_count=1,
            evidence_snapshot={"observed_value": 1.0},
            source_anomaly_ids=["seed-anomaly"],
        )
        for fp in fingerprints
    ]
    session.add_all(alerts)
    await session.flush()
    return {alert.dedup_key: alert for alert in alerts}


class TestApplyBulkBucketShape:
    """`apply_bulk` keeps the bucket's batching promise.

    Refreshes of alerts that were open before the call must leave as one
    batched UPDATE even when the bucket interleaves them with new conditions,
    while every new alert still inserts inside its own savepoint.
    """

    async def test_interleaved_bucket_issues_one_update_and_one_insert_per_new_alert(
        self, db_session
    ) -> None:
        seeded = await _seed_open(db_session, *(f"anomaly:open-{i}" for i in range(5)))
        # refresh, new, refresh, new, refresh, refresh, new, refresh
        inputs = [
            _input("anomaly:open-0"),
            _input("anomaly:new-1"),
            _input("anomaly:open-1"),
            _input("anomaly:new-2"),
            _input("anomaly:open-2"),
            _input("anomaly:open-3"),
            _input("anomaly:new-3"),
            _input("anomaly:open-4"),
        ]
        svc = AlertService(db_session, clock=lambda: NOW)
        with _counting(db_session) as statements:
            opened, _resolved = await svc.apply_bulk(inputs, [])

        updates = [s for s in statements if s.startswith("UPDATE alert")]
        inserts = [s for s in statements if s.startswith("INSERT INTO alert")]
        assert len(updates) == 1
        assert len(inserts) == 3

        # Results come back in input order: updated for the seeded alerts,
        # opened for the new fingerprints.
        assert [(r.opened, r.updated) for r in opened] == [
            (False, True),
            (True, False),
            (False, True),
            (True, False),
            (False, True),
            (False, True),
            (True, False),
            (False, True),
        ]
        assert [r.transition for r in opened] == [
            None,
            AlertTransition.OPENED,
            None,
            AlertTransition.OPENED,
            None,
            None,
            AlertTransition.OPENED,
            None,
        ]
        for index in (0, 2, 4, 5, 7):
            assert opened[index].alert.id == seeded[inputs[index].fingerprint].id
        assert {opened[index].alert.id for index in (1, 3, 6)}.isdisjoint(
            {alert.id for alert in seeded.values()}
        )

        rows = (await db_session.scalars(select(Alert).order_by(Alert.dedup_key))).all()
        assert len(rows) == 8
        by_fingerprint = {row.dedup_key: row for row in rows}
        for i in range(5):
            refreshed = by_fingerprint[f"anomaly:open-{i}"]
            assert refreshed.occurrence_count == 2
            assert refreshed.last_seen_at == NOW
        for fp in ("anomaly:new-1", "anomaly:new-2", "anomaly:new-3"):
            created = by_fingerprint[fp]
            assert created.occurrence_count == 1
            assert created.opened_at == NOW
            assert created.first_seen_at == NOW
            assert created.last_seen_at == NOW

    async def test_the_same_new_fingerprint_twice_opens_once_then_updates(
        self, db_session
    ) -> None:
        svc = AlertService(db_session, clock=lambda: NOW)
        with _counting(db_session) as statements:
            opened, _resolved = await svc.apply_bulk(
                [_input("anomaly:dup"), _input("anomaly:dup")], []
            )

        # The second input must refresh the alert the first one created, not
        # attempt a second insert.
        inserts = [s for s in statements if s.startswith("INSERT INTO alert")]
        assert len(inserts) == 1
        assert opened[0].opened and opened[0].transition is AlertTransition.OPENED
        assert opened[1].updated and not opened[1].opened
        assert opened[1].alert.id == opened[0].alert.id

        rows = (await db_session.scalars(select(Alert))).all()
        assert len(rows) == 1
        assert rows[0].occurrence_count == 2
        assert rows[0].last_seen_at == NOW
