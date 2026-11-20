"""JDBC-1 P9a: the baseline builder drops buckets that overlap a QRadar pause.

The policy is an explicit constructor argument (default off). Off, the builder issues the
statements it always did and stores the documents it always did. On, the overlap flag rides
in the existing metric SELECT, so the per-source cost stays two reads and one bulk upsert.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.anomaly.baseline import BaselineBuilder
from app.core.config import Settings
from app.models.log_source import LogSourceBaseline
from app.models.log_source_pause import LogSourcePause
from tests.integration.factories import add_metric, make_instance, make_log_source
from tests.integration.test_waf_scale import counting

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

REASON = "QRADAR_DISABLED"
BUCKET = timedelta(seconds=300)
# 2026-07-01 is a Wednesday.
BASE = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
WEEKS = 6
NOW = BASE + timedelta(weeks=WEEKS + 1)
#: Two reads plus the bulk upsert.
REBUILD_STATEMENTS = 3


def _settings() -> Settings:
    return Settings(encryption_key="x" * 44, baseline_min_samples=3, baseline_lookback_days=120)


def _builder(session, *, policy: bool) -> BaselineBuilder:  # type: ignore[no-untyped-def]
    return BaselineBuilder(session, settings=_settings(), source_pause_policy=policy)


async def _weekly(session, ls, *, hour: int = 10, eps: float = 50.0) -> list[datetime]:  # type: ignore[no-untyped-def]
    """One clean sample a week at the same weekday and hour: one seasonal cell."""
    starts = [BASE.replace(hour=hour) + timedelta(weeks=week) for week in range(WEEKS)]
    for start in starts:
        await add_metric(session, ls, start, average_eps=eps, event_count=int(eps * 300))
    return starts


async def _pause(session, ls, starts_at, ends_at):  # type: ignore[no-untyped-def]
    session.add(
        LogSourcePause(
            log_source_id=ls.id, reason=REASON, starts_at=starts_at, ends_at=ends_at
        )
    )
    await session.flush()


async def _cells(session, ls) -> dict[tuple[str, int, int], LogSourceBaseline]:  # type: ignore[no-untyped-def]
    rows = (
        await session.scalars(
            select(LogSourceBaseline).where(LogSourceBaseline.log_source_id == ls.id)
        )
    ).all()
    return {(r.metric_name, r.weekday, r.hour): r for r in rows}


async def _sources(session, count: int = 1):  # type: ignore[no-untyped-def]
    inst = await make_instance(session)
    return [
        await make_log_source(session, inst, qradar_id=100 + n, name=f"s{n}")
        for n in range(count)
    ]


async def _source(session):  # type: ignore[no-untyped-def]
    return (await _sources(session))[0]


async def test_zero_positive_tail_excluded(db_session) -> None:
    """Zeros and the racing positive tail inside a pause leave no trace in the cell."""
    ls = await _source(db_session)
    await _weekly(db_session, ls)
    # The last Wednesday hour: eight zero buckets, then a late positive tail that raced the
    # disable (events still draining into the buckets the pause covers).
    hour = BASE.replace(hour=10) + timedelta(weeks=WEEKS)
    for index, eps in enumerate([0.0] * 8 + [3.0, 9.0, 21.0, 40.0]):
        await add_metric(
            db_session, ls, hour + index * BUCKET, average_eps=eps, event_count=int(eps * 300)
        )
    await _pause(db_session, ls, hour, hour + timedelta(hours=1))

    builder = _builder(db_session, policy=True)
    observations, tally, candidates = await builder._load_clean_observations(
        ls, NOW - timedelta(days=120), NOW
    )

    assert tally.qradar_paused == 12
    assert candidates == WEEKS + 12
    assert [o.values["average_eps"] for o in observations] == [50.0] * WEEKS
    assert tally.total == 12

    await builder.rebuild_for_source(ls, now=NOW)
    cell = (await _cells(db_session, ls))[("average_eps", 3, 10)]
    assert cell.sample_count == WEEKS
    assert cell.median == 50.0
    assert cell.excluded_sample_count == 12
    assert cell.exclusion_counts["qradar_paused"] == 12


async def test_emptied_cells_unreliable(db_session) -> None:
    """A cell whose only samples fall in a pause is invalidated, not left with its old band."""
    ls = await _source(db_session)
    kept = await _weekly(db_session, ls, hour=9)
    emptied = await _weekly(db_session, ls, hour=14)
    await _builder(db_session, policy=True).rebuild_for_source(ls, now=NOW)
    before = await _cells(db_session, ls)
    assert before[("average_eps", 3, 14)].is_reliable
    assert before[("average_eps", 3, 14)].sample_count == WEEKS
    versions = {key: cell.baseline_version for key, cell in before.items()}

    for start in emptied:
        await _pause(db_session, ls, start, start + BUCKET)
    await _builder(db_session, policy=True).rebuild_for_source(ls, now=NOW)

    after = await _cells(db_session, ls)
    for metric in ("average_eps", "event_count"):
        stale = after[(metric, 3, 14)]
        assert stale.is_reliable is False
        assert stale.sample_count == 0
        assert stale.p05 is None and stale.p95 is None
        assert stale.observations == []
        assert stale.completeness == 0.0
        assert stale.baseline_version == versions[(metric, 3, 14)] + 1
        # The cell nobody paused is rebuilt from its own samples.
        fresh = after[(metric, 3, 9)]
        assert fresh.is_reliable is True
        assert fresh.sample_count == len(kept)


async def test_clean_pre_pause_observations_retained(db_session) -> None:
    """Samples before `starts_at` and after `ends_at` stay; the half-open edges are exact."""
    ls = await _source(db_session)
    start = BASE.replace(hour=10)
    end = start + timedelta(hours=1)
    grid = [start - BUCKET + index * BUCKET for index in range(15)]  # 09:55 .. 11:05
    for bucket in grid:
        await add_metric(db_session, ls, bucket, average_eps=50.0, event_count=15000)
    await _pause(db_session, ls, start, end)

    observations, tally, _ = await _builder(db_session, policy=True)._load_clean_observations(
        ls, NOW - timedelta(days=120), NOW
    )
    kept = [o.bucket_start for o in observations]

    # 09:55 ends exactly at `starts_at` (touching, kept); 11:00 starts at `ends_at` (kept);
    # 10:00 .. 10:55 overlap.
    assert kept == [grid[0], *grid[13:]]
    assert kept[0] == start - BUCKET and kept[1] == end
    assert tally.qradar_paused == 12

    # An open span excludes everything from its start, and nothing before it.
    open_ls = await _source_with_open_span(db_session, start, grid)
    observations, tally, _ = await _builder(db_session, policy=True)._load_clean_observations(
        open_ls, NOW - timedelta(days=120), NOW
    )
    assert [o.bucket_start for o in observations] == [grid[0]]
    assert tally.qradar_paused == len(grid) - 1


async def _source_with_open_span(session, start, grid):  # type: ignore[no-untyped-def]
    ls = await _source(session)
    for bucket in grid:
        await add_metric(session, ls, bucket, average_eps=50.0, event_count=15000)
    await _pause(session, ls, start, None)
    return ls


async def test_a_pause_of_one_source_never_excludes_another(db_session) -> None:
    ls, other = await _sources(db_session, 2)
    for source in (ls, other):
        await _weekly(db_session, source)
    await _pause(db_session, ls, BASE, BASE + timedelta(weeks=WEEKS + 1))

    for source, expected in ((ls, 0), (other, WEEKS)):
        observations, _, _ = await _builder(db_session, policy=True)._load_clean_observations(
            source, NOW - timedelta(days=120), NOW
        )
        assert len(observations) == expected


async def test_a_bucket_is_charged_to_one_reason_only(db_session) -> None:
    """Maintenance and analyst exclusions keep their bucket; the pause never double-counts."""
    ls = await _source(db_session)
    starts = await _weekly(db_session, ls)
    await _pause(db_session, ls, starts[0], starts[-1] + BUCKET)
    ls.maintenance_mode = True
    await db_session.flush()

    _, tally, _ = await _builder(db_session, policy=True)._load_clean_observations(
        ls, NOW - timedelta(days=120), NOW
    )
    assert tally.maintenance == WEEKS
    assert tally.qradar_paused == 0
    assert tally.total == WEEKS

    ls.maintenance_mode = False
    await db_session.flush()
    span = (starts[0], starts[-1] + BUCKET)
    _, tally, _ = await _builder(db_session, policy=True)._load_clean_observations(
        ls, NOW - timedelta(days=120), NOW, analyst_spans=[span]  # type: ignore[list-item]
    )
    assert tally.analyst_exclusion == WEEKS
    assert tally.qradar_paused == 0


async def test_policy_on_without_a_pause_stores_the_same_document(db_session) -> None:
    """`qradar_paused` is absent when zero, so an unaffected source's cells never change."""
    off, on = await _sources(db_session, 2)
    for source in (off, on):
        await _weekly(db_session, source)
    await _builder(db_session, policy=False).rebuild_for_source(off, now=NOW)
    await _builder(db_session, policy=True).rebuild_for_source(on, now=NOW)

    assert _documents(await _cells(db_session, off)) == _documents(await _cells(db_session, on))
    cell = (await _cells(db_session, on))[("average_eps", 3, 10)]
    assert "qradar_paused" not in cell.exclusion_counts


def _documents(cells):  # type: ignore[no-untyped-def]
    return {
        key: (
            c.median, c.mad, c.p05, c.p95, c.sample_count, c.is_reliable, c.observations,
            c.completeness, c.excluded_sample_count, c.exclusion_counts, c.baseline_version,
        )
        for key, c in cells.items()
    }


async def test_policy_off_preserves_existing_behavior(db_session) -> None:
    """The same history with a pause row present builds exactly today's cells when off."""
    paused, control = await _sources(db_session, 2)
    for source in (paused, control):
        starts = await _weekly(db_session, source)
    await _pause(db_session, paused, starts[0], starts[-1] + BUCKET)

    await _builder(db_session, policy=False).rebuild_for_source(paused, now=NOW)
    await _builder(db_session, policy=False).rebuild_for_source(control, now=NOW)
    # The default is off too, whatever the pause table holds.
    default = BaselineBuilder(db_session, settings=_settings())
    assert default.source_pause_policy is False

    cells = await _cells(db_session, paused)
    assert _documents(cells) == _documents(await _cells(db_session, control))
    cell = cells[("average_eps", 3, 10)]
    assert cell.sample_count == WEEKS and cell.is_reliable is True
    assert cell.exclusion_counts.get("qradar_paused") is None
    assert cell.excluded_sample_count == 0


async def test_core_statements_still_three(db_session) -> None:
    """Off and on: two reads and one bulk upsert per source; the flag adds no statement."""
    clean, hit = await _sources(db_session, 2)
    for source in (clean, hit):
        starts = await _weekly(db_session, source)
        for hour in range(11, 20):  # more cells, still one upsert
            await _weekly(db_session, source, hour=hour)
    await _pause(db_session, hit, starts[1], starts[3])

    counts: dict[tuple[bool, bool], int] = {}
    for policy in (False, True):
        builder = _builder(db_session, policy=policy)
        for source in (clean, hit):
            with counting(db_session) as statements:
                await builder.rebuild_for_source(source, now=NOW)
            counts[(policy, source is hit)] = len(statements)
            # Off, no statement knows the pause table. On, the flag is inside the one
            # metric SELECT and nowhere else.
            touching = [q for q in statements if "log_source_pause" in q]
            assert len(touching) == (1 if policy else 0), touching
            if policy:
                assert "FROM log_source_metric" in touching[0]
    assert counts[(False, False)] == counts[(True, False)] == REBUILD_STATEMENTS
    assert counts[(False, True)] == REBUILD_STATEMENTS
    assert counts[(True, True)] == REBUILD_STATEMENTS


async def test_emptied_cells_of_many_sources_are_one_bulk_update(db_session) -> None:
    """`rebuild_many`: the cells pauses emptied, across sources, cost one UPDATE in all."""
    sources = await _sources(db_session, 4)
    for source in sources:
        starts = await _weekly(db_session, source)
        await _weekly(db_session, source, hour=12)  # a cell left alone, so the upsert runs
    await _builder(db_session, policy=True).rebuild_many(sources, now=NOW)
    for source in sources[:3]:
        for start in starts:
            await _pause(db_session, source, start, start + BUCKET)

    sizes: dict[bool, int] = {}
    for policy in (False, True):
        with counting(db_session) as statements:
            await _builder(db_session, policy=policy).rebuild_many(sources, now=NOW)
        sizes[policy] = len(statements)

    # One exclusion read and three statements per source; on, plus one bulk invalidation.
    assert sizes[False] == 1 + REBUILD_STATEMENTS * len(sources)
    assert sizes[True] == 1 + REBUILD_STATEMENTS * len(sources) + 1
    for source in sources[:3]:
        cell = (await _cells(db_session, source))[("average_eps", 3, 10)]
        assert cell.is_reliable is False and cell.sample_count == 0
    last = (await _cells(db_session, sources[3]))[("average_eps", 3, 10)]
    assert last.is_reliable is True
