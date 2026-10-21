"""`HistoryBackfillRepository` against PostgreSQL: one statement per write, the guards."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.history.lifecycle import MAX_REASON_LENGTH, SCOPE_COLUMNS, bounded_reason
from app.models.history_backfill import HistoryBackfillDecision, HistoryBackfillScope
from app.providers.base import ProviderError
from app.repositories.history_backfill import HistoryBackfillRepository
from tests.integration.factories import make_instance
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)


def scope_row(instance_id: uuid.UUID, family: str = "WAF", key: str = "1", **extra: object):  # type: ignore[no-untyped-def]
    values: dict[str, object] = {
        "instance_id": instance_id, "family": family, "scope_key": key, "state": "READING",
        "days_requested": 28, "days_target": None, "target_start": None, "floor": None,
        "through": None, "anchor": None, "started_at": NOW, "finished_at": None,
        "reason": None, "last_error_class": None, "last_error_at": None, "progress": {},
    }
    values.update(extra)
    return values


async def stored(session, instance_id) -> dict[tuple[str, str], HistoryBackfillScope]:  # type: ignore[no-untyped-def]
    rows = await session.scalars(
        select(HistoryBackfillScope)
        .where(HistoryBackfillScope.instance_id == instance_id)
        .execution_options(populate_existing=True)
    )
    return {(r.family, r.scope_key): r for r in rows}


# --------------------------------------------------------------------------- write_scopes
@pytest.mark.parametrize("count", [1, 50])
async def test_write_scopes_is_one_statement(db_session, count) -> None:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    repo = HistoryBackfillRepository(db_session)
    rows = [scope_row(instance.id, key=str(i + 1)) for i in range(count)]
    with recorded_statements(db_session, only="history_backfill_scope") as statements:
        await repo.write_scopes(rows)
    assert len(statements) == 1
    assert "ON CONFLICT" in statements[0]
    assert len(await stored(db_session, instance.id)) == count


async def test_write_scopes_inserts_then_updates_on_the_key_and_keeps_others(db_session) -> None:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    repo = HistoryBackfillRepository(db_session)
    await repo.write_scopes([scope_row(instance.id, key="1"), scope_row(instance.id, key="2")])
    await repo.write_scopes(
        [
            scope_row(instance.id, key="1", state="PARTIAL", finished_at=LATER,
                      reason="stopped by ayse", progress={"a": 1}),
            scope_row(instance.id, family="PHASE_A", key=""),
        ]
    )
    rows = await stored(db_session, instance.id)
    assert set(rows) == {("WAF", "1"), ("WAF", "2"), ("PHASE_A", "")}
    updated = rows[("WAF", "1")]
    assert (updated.state, updated.finished_at, updated.reason, updated.progress) == (
        "PARTIAL", LATER, "stopped by ayse", {"a": 1})
    assert rows[("WAF", "2")].state == "READING"  # untouched


async def test_write_scopes_overwrites_every_non_key_column(db_session) -> None:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    repo = HistoryBackfillRepository(db_session)
    await repo.write_scopes(
        [scope_row(instance.id, anchor=NOW, target_start=NOW, reason="x", progress={"a": 1})]
    )
    await repo.write_scopes([scope_row(instance.id)])
    row = (await stored(db_session, instance.id))[("WAF", "1")]
    assert (row.anchor, row.target_start, row.reason, row.progress) == (None, None, None, {})


async def test_write_scopes_refuses_a_row_missing_a_column_and_writes_nothing(db_session) -> None:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    repo = HistoryBackfillRepository(db_session)
    good = scope_row(instance.id, key="1")
    for name in SCOPE_COLUMNS:
        bad = scope_row(instance.id, key="2")
        del bad[name]
        with pytest.raises(ValueError, match=name):
            await repo.write_scopes([good, bad])
    with pytest.raises(ValueError, match="unknown"):
        await repo.write_scopes([good, {**scope_row(instance.id, key="3"), "extra": 1}])
    assert await stored(db_session, instance.id) == {}


async def test_write_scopes_refuses_a_reason_over_255_and_takes_a_bounded_one(db_session) -> None:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    repo = HistoryBackfillRepository(db_session)
    long = "x" * (MAX_REASON_LENGTH + 1)
    with pytest.raises(ValueError, match="reason"):
        await repo.write_scopes([scope_row(instance.id, reason=long)])
    await repo.write_scopes([scope_row(instance.id, reason=bounded_reason(long))])
    row = (await stored(db_session, instance.id))[("WAF", "1")]
    assert row.reason is not None and len(row.reason) == MAX_REASON_LENGTH
    assert row.reason.endswith("…")


async def test_write_scopes_with_no_rows_sends_nothing(db_session) -> None:  # type: ignore[no-untyped-def]
    repo = HistoryBackfillRepository(db_session)
    with recorded_statements(db_session) as statements:
        await repo.write_scopes([])
    assert statements == []


async def test_step_errors_are_one_statement_class_only_and_the_next_write_clears(  # type: ignore[no-untyped-def]
    db_session,
) -> None:
    instance = await make_instance(db_session)
    repo = HistoryBackfillRepository(db_session)
    await repo.write_scopes([scope_row(instance.id, family="IPS", key=str(i)) for i in range(3)])
    message = "SELECT sensitive_property FROM events"

    with recorded_statements(db_session, only="history_backfill_scope") as statements:
        await repo.write_step_errors(
            instance.id,
            "IPS",
            {str(i): ProviderError(message) for i in range(3)},
            at=LATER,
        )

    assert len(statements) == 1
    rows = await stored(db_session, instance.id)
    assert {
        (rows[("IPS", str(i))].last_error_class, rows[("IPS", str(i))].last_error_at)
        for i in range(3)
    } == {("ProviderError", LATER)}
    assert message not in " ".join(row.last_error_class or "" for row in rows.values())

    await repo.write_scopes([scope_row(instance.id, family="IPS", key=str(i)) for i in range(3)])
    cleared = await stored(db_session, instance.id)
    assert all(
        row.last_error_class is None and row.last_error_at is None for row in cleared.values()
    )


async def test_step_error_class_is_bounded_to_64_characters(db_session) -> None:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    repo = HistoryBackfillRepository(db_session)
    await repo.write_scopes([scope_row(instance.id)])
    long_error_class = type("X" * 80, (Exception,), {})

    await repo.write_step_errors(instance.id, "WAF", {"1": long_error_class()}, at=LATER)

    row = (await stored(db_session, instance.id))[("WAF", "1")]
    assert row.last_error_class == "X" * 64
    assert row.last_error_at == LATER


# --------------------------------------------------------------------------- reads
async def test_scopes_and_decisions_are_one_statement_each(db_session) -> None:  # type: ignore[no-untyped-def]
    one, other = await make_instance(db_session), await make_instance(db_session)
    repo = HistoryBackfillRepository(db_session)
    await repo.write_scopes(
        [scope_row(one.id, key=str(i)) for i in range(1, 6)]
        + [scope_row(one.id, family="IPS", key="1"), scope_row(other.id)]
    )
    await repo.record_decision(one.id, answer="NO", days=None, source="UI", decided_by="a", at=NOW)
    await repo.record_decision(other.id, answer="YES", days=28, source="UI", decided_by="a",
                               at=NOW)
    with recorded_statements(db_session) as statements:
        everything = await repo.scopes(one.id, "WAF")
        some = await repo.scopes(one.id, "WAF", ["2", "3", "99"])
        decisions = await repo.decisions([one.id, other.id, uuid.uuid4()])
    assert len(statements) == 3
    assert set(everything) == {"1", "2", "3", "4", "5"}
    assert set(some) == {"2", "3"}
    assert set(decisions) == {one.id, other.id}
    assert await repo.scopes(one.id, "WAF", []) == {}
    assert await repo.decisions([]) == {}


# --------------------------------------------------------------------------- decisions
async def test_record_decision_inserts_then_reports_the_conflict(db_session) -> None:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    repo = HistoryBackfillRepository(db_session)
    first, inserted = await repo.record_decision(
        instance.id, answer="YES", days=28, source="INSTALLER", decided_by="installer", at=NOW)
    assert inserted is True
    assert (first.answer, first.days, first.decided_by) == ("YES", 28, "installer")
    again, inserted = await repo.record_decision(
        instance.id, answer="NO", days=None, source="UI", decided_by="other", at=LATER)
    assert inserted is False
    assert (again.answer, again.days, again.decided_by, again.decided_at) == (
        "YES", 28, "installer", NOW)


async def test_record_decision_never_overwrites_a_row_inserted_in_between(  # type: ignore[no-untyped-def]
    db_session, db_schema
) -> None:
    instance = await make_instance(db_session)
    await db_session.commit()
    repo = HistoryBackfillRepository(db_session)
    assert await repo.decisions([instance.id]) == {}  # the caller's read: nothing yet
    engine = create_async_engine(db_schema)
    try:
        async with async_sessionmaker(engine)() as other, other.begin():
            await other.execute(
                text(
                    "INSERT INTO history_backfill_decision (instance_id, answer, days, "
                    "decided_at, decided_by, source) VALUES (:i, 'NO', NULL, now(), 'rival', 'UI')"
                ),
                {"i": instance.id},
            )
    finally:
        await engine.dispose()
    row, inserted = await repo.record_decision(
        instance.id, answer="YES", days=28, source="INSTALLER", decided_by="installer", at=NOW)
    assert inserted is False
    assert (row.answer, row.decided_by) == ("NO", "rival")


async def test_upgrade_to_yes_changes_only_a_no(db_session) -> None:  # type: ignore[no-untyped-def]
    no_console, yes_console = await make_instance(db_session), await make_instance(db_session)
    repo = HistoryBackfillRepository(db_session)
    await repo.record_decision(no_console.id, answer="NO", days=None, source="INSTALLER",
                               decided_by="installer", at=NOW)
    await repo.record_decision(yes_console.id, answer="YES", days=28, source="INSTALLER",
                               decided_by="installer", at=NOW)
    assert await repo.upgrade_to_yes(no_console.id, days=14, by="ayse", at=LATER) is True
    assert await repo.upgrade_to_yes(yes_console.id, days=14, by="ayse", at=LATER) is False
    assert await repo.upgrade_to_yes(uuid.uuid4(), days=14, by="ayse", at=LATER) is False
    got = await repo.decisions([no_console.id, yes_console.id])
    upgraded = got[no_console.id]
    assert (upgraded.answer, upgraded.days, upgraded.updated_at, upgraded.updated_by) == (
        "YES", 14, LATER, "ayse")
    assert (upgraded.decided_by, upgraded.decided_at) == ("installer", NOW)  # the first answer
    untouched = got[yes_console.id]
    assert (untouched.days, untouched.updated_at, untouched.updated_by) == (28, None, None)


async def test_there_is_no_way_to_turn_a_yes_into_a_no() -> None:
    names = {n for n in dir(HistoryBackfillRepository) if not n.startswith("_")}
    assert names == {
        "decisions", "scopes", "write_scopes", "write_step_errors", "record_decision",
        "upgrade_to_yes", "request_start", "request_stop",
    }


@pytest.mark.parametrize("kind", ["start", "stop"])
async def test_a_request_is_recorded_on_a_yes_and_a_later_one_overwrites(  # type: ignore[no-untyped-def]
    db_session, kind
) -> None:
    instance = await make_instance(db_session)
    repo = HistoryBackfillRepository(db_session)
    await repo.record_decision(instance.id, answer="YES", days=28, source="INSTALLER",
                               decided_by="installer", at=NOW)
    request = repo.request_start if kind == "start" else repo.request_stop
    other = "stop" if kind == "start" else "start"
    assert await request(instance.id, by="ayse", at=NOW) is True
    assert await request(instance.id, by="can", at=LATER) is True
    row = (await repo.decisions([instance.id]))[instance.id]
    assert getattr(row, f"{kind}_requested_at") == LATER
    assert getattr(row, f"{kind}_requested_by") == "can"
    assert getattr(row, f"{other}_requested_at") is None
    assert getattr(row, f"{other}_requested_by") is None


@pytest.mark.parametrize("kind", ["start", "stop"])
async def test_a_request_changes_nothing_on_a_no_or_a_missing_console(db_session, kind) -> None:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    repo = HistoryBackfillRepository(db_session)
    await repo.record_decision(instance.id, answer="NO", days=None, source="INSTALLER",
                               decided_by="installer", at=NOW)
    request = repo.request_start if kind == "start" else repo.request_stop
    assert await request(instance.id, by="ayse", at=NOW) is False
    assert await request(uuid.uuid4(), by="ayse", at=NOW) is False
    row = (await repo.decisions([instance.id]))[instance.id]
    assert row.start_requested_at is None and row.stop_requested_at is None


async def test_each_decision_method_is_one_statement(db_session) -> None:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    repo = HistoryBackfillRepository(db_session)
    with recorded_statements(db_session, only="history_backfill_decision") as statements:
        await repo.record_decision(instance.id, answer="YES", days=28, source="INSTALLER",
                                   decided_by="installer", at=NOW)
    assert len(statements) == 2  # the insert and the read of the stored row
    for call in (repo.request_start, repo.request_stop):
        with recorded_statements(db_session) as statements:
            await call(instance.id, by="a", at=NOW)
        assert len(statements) == 1
    with recorded_statements(db_session) as statements:
        await repo.upgrade_to_yes(instance.id, days=14, by="a", at=NOW)
    assert len(statements) == 1


async def test_the_model_refuses_a_request_on_a_no(db_session) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import IntegrityError

    instance = await make_instance(db_session)
    db_session.add(HistoryBackfillDecision(
        instance_id=instance.id, answer="NO", days=None, decided_at=NOW, decided_by="a",
        source="UI", stop_requested_at=NOW, stop_requested_by="a"))
    with pytest.raises(IntegrityError, match="request_only_on_yes"):
        await db_session.flush()
