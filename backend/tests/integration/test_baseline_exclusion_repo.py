"""SOC-13a: create / revoke / load_active against a real database."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, select

from app.anomaly import training_exclusions as te
from app.models.training_exclusion import BaselineTrainingExclusion

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

T0 = datetime(2026, 11, 4, 8, 0, tzinfo=UTC)
H = timedelta(hours=1)
LOOKBACK = T0 - timedelta(days=28)
SCOPE = te.LOG_SOURCE_VOLUME_SCOPE


async def _create(session, subject, start, end, **extra):  # type: ignore[no-untyped-def]
    args = {
        "origin_family": "log_source",
        "origin_anomaly_id": uuid.uuid4(),
        "subject_kind": "log_source",
        "subject_id": subject,
        "scope_key": SCOPE,
        "starts_at": start,
        "ends_at": end,
        "reason": "outage",
        "created_by": "alice",
        "lookback_start": LOOKBACK,
    }
    args.update(extra)
    return await te.create(session, **args)


@contextmanager
def _counting(session):  # type: ignore[no-untyped-def]
    statements: list[str] = []
    engine = session.bind.sync_engine

    def record(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def test_create_then_exact_duplicate_returns_existing(db_session) -> None:
    subject = uuid.uuid4()
    first = await _create(db_session, subject, T0, T0 + 2 * H)
    again = await _create(db_session, subject, T0, T0 + 2 * H, reason="other", created_by="bob")
    assert first.created is True
    assert again.created is False
    assert again.row.id == first.row.id
    assert again.row.created_by == "alice"
    count = await db_session.scalar(select(func.count()).select_from(BaselineTrainingExclusion))
    assert count == 1


@pytest.mark.parametrize(
    ("start", "end"),
    [(T0 + H, T0 + 3 * H), (T0 - H, T0 + H), (T0 + H // 2, T0 + 1 * H), (T0 - H, T0 + 5 * H)],
)
async def test_partial_overlap_is_refused(db_session, start, end) -> None:
    subject = uuid.uuid4()
    await _create(db_session, subject, T0, T0 + 2 * H)
    with pytest.raises(te.ExclusionError) as caught:
        await _create(db_session, subject, start, end)
    assert caught.value.code == te.OVERLAP


async def test_adjacent_intervals_and_other_subject_or_scope_are_allowed(db_session) -> None:
    subject = uuid.uuid4()
    await _create(db_session, subject, T0, T0 + 2 * H)
    await _create(db_session, subject, T0 + 2 * H, T0 + 3 * H)
    await _create(db_session, subject, T0 - H, T0, scope_key="log_source:other")
    await _create(db_session, uuid.uuid4(), T0, T0 + 2 * H)


async def test_revoked_row_no_longer_blocks_overlap_or_duplicate(db_session) -> None:
    subject = uuid.uuid4()
    first = await _create(db_session, subject, T0, T0 + 2 * H)
    await te.revoke(db_session, first.row.id, revoked_by="bob", reason="mistake")
    again = await _create(db_session, subject, T0, T0 + 2 * H)
    assert again.created is True
    assert again.row.id != first.row.id


async def test_fifty_first_relevant_row_is_refused(db_session) -> None:
    subject = uuid.uuid4()
    for i in range(te.MAX_RELEVANT_PER_SCOPE):
        start = T0 + timedelta(hours=2 * i)
        await _create(db_session, subject, start, start + H)
    with pytest.raises(te.ExclusionError) as caught:
        await _create(db_session, subject, T0 + timedelta(hours=200), T0 + timedelta(hours=201))
    assert caught.value.code == te.LIMIT_REACHED
    # Another subject is unaffected.
    await _create(db_session, uuid.uuid4(), T0, T0 + H)


async def test_rows_outside_the_lookback_do_not_count_toward_the_cap(db_session) -> None:
    subject = uuid.uuid4()
    old = LOOKBACK - timedelta(days=3)
    for i in range(te.MAX_RELEVANT_PER_SCOPE):
        start = old + timedelta(hours=2 * i)
        await _create(db_session, subject, start, start + H)
    result = await _create(db_session, subject, T0, T0 + H)
    assert result.created is True


async def test_revoked_rows_do_not_count_toward_the_cap(db_session) -> None:
    subject = uuid.uuid4()
    rows = []
    for i in range(te.MAX_RELEVANT_PER_SCOPE):
        start = T0 + timedelta(hours=2 * i)
        rows.append((await _create(db_session, subject, start, start + H)).row)
    await te.revoke(db_session, rows[0].id, revoked_by="bob", reason="mistake")
    late = T0 + timedelta(hours=300)
    result = await _create(db_session, subject, late, late + H)
    assert result.created is True


async def test_revoke_sets_fields_together_and_is_idempotent(db_session) -> None:
    created = await _create(db_session, uuid.uuid4(), T0, T0 + H)
    when = T0 + 10 * H
    first = await te.revoke(
        db_session, created.row.id, revoked_by="bob", reason="mistake", now=when
    )
    assert first.changed is True
    assert (first.row.revoked_by, first.row.revoked_at, first.row.revocation_reason) == (
        "bob",
        when,
        "mistake",
    )
    second = await te.revoke(
        db_session, created.row.id, revoked_by="carol", reason="later", now=when + H
    )
    assert second.changed is False
    assert (second.row.revoked_by, second.row.revoked_at, second.row.revocation_reason) == (
        "bob",
        when,
        "mistake",
    )


async def test_revoke_unknown_id_is_not_found(db_session) -> None:
    with pytest.raises(te.ExclusionError) as caught:
        await te.revoke(db_session, uuid.uuid4(), revoked_by="bob", reason="x")
    assert caught.value.code == te.NOT_FOUND


async def test_load_active_is_one_statement_and_coalesces(db_session) -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _create(db_session, a, T0, T0 + H)
    await _create(db_session, a, T0 + H, T0 + 2 * H)  # adjacent: coalesces
    await _create(db_session, a, T0 + 4 * H, T0 + 5 * H)
    await _create(db_session, b, T0 + H, T0 + 2 * H)
    revoked = await _create(db_session, b, T0 + 6 * H, T0 + 7 * H)
    await te.revoke(db_session, revoked.row.id, revoked_by="bob", reason="mistake")
    await _create(db_session, c, T0 - timedelta(days=40), T0 - timedelta(days=39))  # too old
    await _create(db_session, a, T0, T0 + H, scope_key="log_source:other")
    await db_session.commit()

    with _counting(db_session) as statements:
        loaded = await te.load_active(
            db_session, "log_source", [a, b, c, uuid.uuid4()], SCOPE, LOOKBACK, T0 + 30 * H
        )
    assert len(statements) == 1
    assert loaded == {
        ("log_source", a, SCOPE): [(T0, T0 + 2 * H), (T0 + 4 * H, T0 + 5 * H)],
        ("log_source", b, SCOPE): [(T0 + H, T0 + 2 * H)],
    }


async def test_load_active_statement_count_does_not_grow_with_subjects(db_session) -> None:
    subjects = [uuid.uuid4() for _ in range(12)]
    for subject in subjects:
        await _create(db_session, subject, T0, T0 + H)
    await db_session.commit()
    counts = []
    for ids in (subjects[:1], subjects):
        with _counting(db_session) as statements:
            await te.load_active(db_session, "log_source", ids, SCOPE, LOOKBACK, T0 + 30 * H)
        counts.append(len(statements))
    assert counts == [1, 1]


async def test_load_active_without_subjects_issues_no_statement(db_session) -> None:
    with _counting(db_session) as statements:
        assert await te.load_active(db_session, "log_source", [], SCOPE, LOOKBACK, T0) == {}
    assert statements == []


async def test_load_active_excludes_rows_starting_at_or_after_lookback_end(db_session) -> None:
    subject = uuid.uuid4()
    await _create(db_session, subject, T0 + 2 * H, T0 + 3 * H)
    loaded = await te.load_active(db_session, "log_source", [subject], SCOPE, LOOKBACK, T0 + 2 * H)
    assert loaded == {}
