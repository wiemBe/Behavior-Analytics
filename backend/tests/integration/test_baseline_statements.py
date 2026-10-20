"""Statement pins for the Phase A baseline rebuild (HB-12 D2).

`BaselineBuilder.rebuild_for_source` writes a source's seasonal cells in one bulk
upsert: two reads (observations, exclusion spans) and one write, whatever the
number of cells. The collector's rebuild step then costs that constant per source
rebuilt, never per bucket of history or per cell.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.anomaly.baseline import BaselineBuilder
from app.core.config import Settings
from app.models.log_source import LogSourceMetric
from tests.integration.factories import make_instance, make_log_source
from tests.integration.history_phase_a_support import (
    STEP,
    ScriptedMockProvider,
    make_console,
    read_all,
    say_yes,
    settings_for,
    tick,
)
from tests.integration.test_waf_scale import counting

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

#: Two reads plus the bulk upsert.
REBUILD_STATEMENTS = 3


def _settings() -> Settings:
    return Settings(
        encryption_key="x" * 44, baseline_min_samples=8, baseline_lookback_days=60
    )


async def _seed_cells(session, ls, *, now: datetime, hours: int) -> None:
    """One sample in each of `hours` distinct weekday/hour slots."""
    start = now - timedelta(days=7)
    session.add_all(
        [
            LogSourceMetric(
                log_source_id=ls.id,
                bucket_start=start + timedelta(hours=index),
                average_eps=40.0 + index,
                event_count=1000 + index,
            )
            for index in range(hours)
        ]
    )
    await session.flush()


async def test_a_rebuild_writes_every_cell_in_one_statement(db_session) -> None:
    """One cell or all 168 (per metric): the same three statements."""
    inst = await make_instance(db_session)
    one = await make_log_source(db_session, inst, qradar_id=1, name="one-cell")
    every = await make_log_source(db_session, inst, qradar_id=2, name="every-cell")
    now = datetime(2026, 7, 15, tzinfo=UTC)
    await _seed_cells(db_session, one, now=now, hours=1)
    await _seed_cells(db_session, every, now=now, hours=168)

    builder = BaselineBuilder(db_session, settings=_settings())
    with counting(db_session) as one_statements:
        written_one = await builder.rebuild_for_source(one, now=now)
    with counting(db_session) as every_statements:
        written_every = await builder.rebuild_for_source(every, now=now)

    assert written_one == 2  # average_eps and event_count
    assert written_every == 168 * 2
    assert len(one_statements) == len(every_statements) == REBUILD_STATEMENTS, (
        one_statements,
        every_statements,
    )


async def test_the_collector_rebuild_step_costs_a_constant_per_source(db_session) -> None:
    """One source or twenty-five in one tick: the same three statements per source."""
    # Twenty-seven sources: the reading's finishing tick rebuilds one of them,
    # so twenty-six are pending when the batches below run.
    qids = list(range(1, 28))
    console = await make_console(db_session, qids)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(qids)
    scope, _, at = await read_all(
        db_session,
        console,
        provider,
        settings_for(history_backfill_baseline_sources_per_tick=1),
    )
    assert scope.state == "COMPLETE"

    async def statements_of(batch: int, when: datetime) -> int:
        settings = settings_for(history_backfill_baseline_sources_per_tick=batch)
        with counting(db_session) as statements:
            await tick(db_session, console.instance, provider, settings, when)
        return len(statements)

    single = await statements_of(1, at)  # 25 of 26 still pending
    twenty_five = await statements_of(25, at + STEP)  # none left

    # Twenty-five sources cost twenty-four more rebuilds than one, and every
    # rebuild is the same three statements.
    assert twenty_five - single == 24 * REBUILD_STATEMENTS
