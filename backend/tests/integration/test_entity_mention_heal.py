"""SOC-16f7: the index heals end to end (stored parents -> backfill -> failed write -> backfill)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.cli import entity_mentions as cli
from app.collectors.catchall_collector import CatchallCollector
from app.models.enums import SearchFamily
from app.models.monitoring import CollectionWatermark
from app.models.search_cadence import SearchCadenceSetting
from app.schemas.entity_timeline import EntityTimelineKind
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_catchall_collection import (
    BUCKET,
    FIRST,
    LAST,
    Script,
    row,
    steady,
)
from tests.integration.test_catchall_collection import NOW as CATCHALL_NOW
from tests.integration.test_entity_mention_backfill import service
from tests.integration.test_entity_mention_lanes import (
    START_A,
    explain,
    explanation_env,
)
from tests.integration.test_soc16d1_entity_lanes import _settings, _timeline

pytestmark = pytest.mark.integration

INCOMPLETE = "entity_timeline.reason.index_incomplete"


async def _failing(session, *_args, **_kwargs):  # type: ignore[no-untyped-def]
    await session.execute(text("SELECT 1/0"))


@pytest.fixture
def operator(db_schema, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Point the operator CLI at the test database."""

    def maker() -> async_sessionmaker:  # type: ignore[type-arg]
        engine = create_async_engine(db_schema, poolclass=NullPool)
        return async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(cli, "get_sessionmaker", maker)


async def _lane(session, instance_id, kind, value, source, start, end):  # type: ignore[no-untyped-def]
    page = await _timeline(
        session, instance_id, kind=kind, value=value, value_norm=value.lower(),
        start=start, end=end,
    )
    lane = next(lane for lane in page.lanes if lane.source.value == source)
    items = [item for item in page.items if item.source.value == source]
    return lane.state.value, lane.reason_code, lane.total, len(items), page.dangling


async def _forget_rows(session) -> None:  # type: ignore[no-untyped-def]
    """Parents stored before the index existed: no state rows at all."""
    await session.execute(text("DELETE FROM entity_mention_backfill"))
    await session.commit()


async def _status_lines(session, capsys, source: str) -> list[str]:  # type: ignore[no-untyped-def]
    await session.commit()
    # `main` runs its own event loop, so it cannot be called from this one.
    assert await asyncio.to_thread(cli.main, ["status"]) == 0
    out = capsys.readouterr().out
    return [line for line in out.splitlines() if f" {source} " in line]


async def test_catchall_lane_heals_end_to_end(  # type: ignore[no-untyped-def]
    db_session, monkeypatch, operator, capsys
) -> None:
    instance = await make_instance(db_session)
    await make_log_source(db_session, instance, qradar_id=1001, name="SIM Generic", type_id=67)
    db_session.add(
        SearchCadenceSetting(
            family=SearchFamily.CATCHALL_SENDERS.value,
            cadence_seconds=3600,
            updated_at=CATCHALL_NOW,
        )
    )
    await db_session.flush()
    end = LAST + timedelta(seconds=BUCKET)
    iid = instance.id

    def collector(rows):  # type: ignore[no-untyped-def]
        return CatchallCollector(
            db_session,
            Script(steady(rows)),
            settings=_settings(search_cadence_max_searches_per_tick=50),
            clock=lambda: CATCHALL_NOW,
        )

    async def reset_watermark() -> None:
        from sqlalchemy import select

        mark = await db_session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.instance_id == iid,
                CollectionWatermark.collector == "catchall_senders",
            )
        )
        mark.watermark_at = None

    # 1. Stored buckets, no mentions, no state row: PARTIAL index_incomplete, never OK.
    with monkeypatch.context() as m:
        m.setattr("app.collectors.catchall_collector.record_mentions", _failing)
        await collector([row("192.0.2.77", 12)]).collect(instance)
    await _forget_rows(db_session)
    ip = (EntityTimelineKind.IP, "192.0.2.77", "CATCHALL", FIRST, end)
    assert await _lane(db_session, iid, *ip) == ("PARTIAL", INCOMPLETE, None, 0, 0)
    assert await asyncio.to_thread(cli.main, ["status"]) == 0
    assert "has not started" in capsys.readouterr().out

    # 2. The backfill completes: items appear and an absent address is a proven empty OK.
    assert (await service(db_session).run()).complete
    state, _r, total, items, dangling = await _lane(db_session, iid, *ip)
    assert (state, items, dangling) == ("OK", 12, 0) and total == 12
    absent = (EntityTimelineKind.IP, "192.0.2.250", "CATCHALL", FIRST, end)
    assert await _lane(db_session, iid, *absent) == ("OK", None, 0, 0, 0)
    (line,) = await _status_lines(db_session, capsys, "CATCHALL")
    assert "COMPLETE" in line and "rows_written=12" in line and "last_error=-" in line

    # 3. A live write fails for a new sender: the row goes back to PENDING and the lane degrades.
    await reset_watermark()
    with monkeypatch.context() as m:
        m.setattr("app.collectors.catchall_collector.record_mentions", _failing)
        await collector([row("192.0.2.77", 12), row("192.0.2.88", 9)]).collect(instance)
    await db_session.commit()
    new = (EntityTimelineKind.IP, "192.0.2.88", "CATCHALL", FIRST, end)
    assert await _lane(db_session, iid, *new) == ("PARTIAL", INCOMPLETE, None, 0, 0)
    assert (await _lane(db_session, iid, *ip))[0] == "PARTIAL"
    (line,) = await _status_lines(db_session, capsys, "CATCHALL")
    assert "PENDING" in line and "last_error=mention_write_failed" in line

    # 4. The next backfill writes the missing mentions and the lane is OK again.
    assert (await service(db_session).run()).complete
    assert await _lane(db_session, iid, *new) == ("OK", None, 12, 12, 0)
    assert (await _lane(db_session, iid, *ip))[0] == "OK"
    (line,) = await _status_lines(db_session, capsys, "CATCHALL")
    assert "COMPLETE" in line and "last_error=-" in line
    assert await db_session.scalar(
        text(
            "SELECT count(*) FROM entity_mention "
            "WHERE source='CATCHALL' AND value_norm='192.0.2.88'"
        )
    ) == 12


async def test_explanation_lane_heals_end_to_end(  # type: ignore[no-untyped-def]
    db_session, monkeypatch, operator, capsys
) -> None:
    instance, _source, anomaly = await explanation_env(db_session)
    iid = instance.id
    end = START_A + timedelta(minutes=5)
    # Every configured dimension is available, so the package is COMPLETE and can prove absence.
    other = {"event_name": ["login"], "destination_port": ["443"], "qid": ["1001"]}
    first = {
        "source_ip": ["203.0.113.50"], "destination_ip": ["10.0.0.5"], "username": ["dave"],
        **other,
    }

    with monkeypatch.context() as m:
        m.setattr("app.collectors.explanation_collector.record_mentions", _failing)
        await explain(db_session, anomaly, first)
    await _forget_rows(db_session)
    probe = (EntityTimelineKind.IP, "203.0.113.50", "EXPLANATION", START_A, end)
    assert await _lane(db_session, iid, *probe) == ("PARTIAL", INCOMPLETE, None, 0, 0)

    assert (await service(db_session).run()).complete
    state, _reason, total, items, dangling = await _lane(db_session, iid, *probe)
    assert (state, items, dangling) == ("OK", 1, 0) and total == 1
    absent = (EntityTimelineKind.IP, "203.0.113.99", "EXPLANATION", START_A, end)
    assert await _lane(db_session, iid, *absent) == ("OK", None, 0, 0, 0)
    (line,) = await _status_lines(db_session, capsys, "EXPLANATION")
    assert "COMPLETE" in line

    # The package is collected again, and this time the mention insert fails.
    second = {**first, "source_ip": ["203.0.113.60"]}
    with monkeypatch.context() as m:
        m.setattr("app.collectors.explanation_collector.record_mentions", _failing)
        await explain(db_session, anomaly, second)
    await db_session.commit()
    new = (EntityTimelineKind.IP, "203.0.113.60", "EXPLANATION", START_A, end)
    assert await _lane(db_session, iid, *new) == ("PARTIAL", INCOMPLETE, None, 0, 0)
    (line,) = await _status_lines(db_session, capsys, "EXPLANATION")
    assert "PENDING" in line and "last_error=mention_write_failed" in line

    assert (await service(db_session).run()).complete
    assert await _lane(db_session, iid, *new) == ("OK", None, 1, 1, 0)
    assert await _lane(db_session, iid, *absent) == ("OK", None, 0, 0, 0)
    (line,) = await _status_lines(db_session, capsys, "EXPLANATION")
    assert "COMPLETE" in line and "last_error=-" in line
    assert await db_session.scalar(
        text(
            "SELECT count(*) FROM entity_mention "
            "WHERE source='EXPLANATION' AND value_norm='203.0.113.60'"
        )
    ) == 1
