"""Baseline builder: grouping, MAD=0, min samples, maintenance/business-hours
exclusion, versioning. DB-gated."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.anomaly.baseline import (
    BASELINE_METRICS,
    BaselineBuilder,
    ExclusionTally,
)
from app.anomaly.seasonal import retained_fraction, seasonal_key, summarize_cell
from app.core.config import Settings
from app.models.enums import AnomalyState, AnomalyType, EvidenceStatus, Severity
from app.models.log_source import LogSourceAnomaly, LogSourceBaseline, LogSourceMetric
from tests.integration.factories import add_metric, make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _settings(min_samples: int = 8) -> Settings:
    return Settings(encryption_key="x" * 44, baseline_min_samples=min_samples,
                    baseline_lookback_days=60)


async def _weekly_series(session, ls, *, weeks: int, hour: int, eps_values: list[float]):
    """Insert one metric per week at a fixed weekday+hour (Wednesday)."""
    # 2026-07-01 is a Wednesday.
    base = datetime(2026, 7, 1, hour, 0, tzinfo=UTC)
    for w in range(weeks):
        eps = eps_values[w % len(eps_values)]
        await add_metric(
            session, ls, base + timedelta(weeks=w),
            average_eps=eps, event_count=int(eps * 300),
        )
    return base + timedelta(weeks=weeks)


async def test_reliable_baseline_from_enough_samples(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    now = await _weekly_series(db_session, ls, weeks=12, hour=10,
                               eps_values=[48, 50, 52, 49, 51])

    builder = BaselineBuilder(db_session, settings=_settings())
    cells = await builder.rebuild_for_source(ls, now=now)
    assert cells > 0

    cell = await db_session.scalar(
        select(LogSourceBaseline).where(
            LogSourceBaseline.log_source_id == ls.id,
            LogSourceBaseline.metric_name == "average_eps",
            LogSourceBaseline.weekday == 3, LogSourceBaseline.hour == 10,
        )
    )
    assert cell is not None
    assert cell.is_reliable is True
    # 12 weekly samples are written, but baseline_lookback_days=60 admits only
    # those from 2026-07-25 onwards -- weeks 4..11, i.e. 8. That is exactly
    # baseline_min_samples, so this is also the reliability boundary.
    assert cell.sample_count == 8
    assert 48 <= cell.median <= 52


async def test_mad_zero_when_identical(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    now = await _weekly_series(db_session, ls, weeks=10, hour=9, eps_values=[42.0])

    builder = BaselineBuilder(db_session, settings=_settings())
    await builder.rebuild_for_source(ls, now=now)
    cell = await db_session.scalar(
        select(LogSourceBaseline).where(
            LogSourceBaseline.log_source_id == ls.id,
            LogSourceBaseline.metric_name == "average_eps",
            LogSourceBaseline.hour == 9,
        )
    )
    assert cell.median == 42.0
    assert cell.mad == 0.0  # all identical
    assert cell.is_reliable is True


async def test_insufficient_samples_marked_unreliable(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    now = await _weekly_series(db_session, ls, weeks=3, hour=11, eps_values=[50])

    builder = BaselineBuilder(db_session, settings=_settings(min_samples=8))
    await builder.rebuild_for_source(ls, now=now)
    cell = await db_session.scalar(
        select(LogSourceBaseline).where(
            LogSourceBaseline.log_source_id == ls.id,
            LogSourceBaseline.hour == 11,
            LogSourceBaseline.metric_name == "average_eps",
        )
    )
    assert cell.sample_count == 3
    assert cell.is_reliable is False  # exists but must not drive alerts


async def test_maintenance_intervals_excluded(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(
        db_session, inst, maintenance_mode=True,
        maintenance_until=datetime(2027, 1, 1, tzinfo=UTC),
    )
    now = await _weekly_series(db_session, ls, weeks=12, hour=10, eps_values=[50])
    builder = BaselineBuilder(db_session, settings=_settings())
    cells = await builder.rebuild_for_source(ls, now=now)
    assert cells == 0  # everything in maintenance -> nothing baselined


async def test_business_hours_only_excludes_offhours(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(
        db_session, inst, business_hours_only=True,
        business_hours_start=8, business_hours_end=18, business_days=[1, 2, 3, 4, 5],
    )
    # Series at 03:00 (off-hours) must be excluded entirely.
    now = await _weekly_series(db_session, ls, weeks=12, hour=3, eps_values=[50])
    builder = BaselineBuilder(db_session, settings=_settings())
    cells = await builder.rebuild_for_source(ls, now=now)
    assert cells == 0


async def test_recompute_bumps_version(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    now = await _weekly_series(db_session, ls, weeks=10, hour=10, eps_values=[50])
    builder = BaselineBuilder(db_session, settings=_settings())
    await builder.rebuild_for_source(ls, now=now)
    await builder.rebuild_for_source(ls, now=now)
    cell = await db_session.scalar(
        select(LogSourceBaseline).where(
            LogSourceBaseline.log_source_id == ls.id,
            LogSourceBaseline.hour == 10,
            LogSourceBaseline.metric_name == "average_eps",
        )
    )
    assert cell.baseline_version == 2
    assert cell.observations  # observations retained for reproducibility


class TestExclusionSpanEnd:
    """An anomaly's exclusion span ends when it stopped, not when it resolved.

    Bounding by `resolved_at` alone extends the span to the end of the lookback
    window for anything unresolved. Observed live on 2026-08-02 against the lab
    appliance, that produced a deadlock: an OPEN incident excluded the buckets a
    new seasonal cell needed, so the detector had no baseline, returned
    INSUFFICIENT_DATA rather than a healthy verdict, and the incident could
    never recover -- it required the baseline its own existence prevented.
    """

    @staticmethod
    async def _anomaly(session, ls, *, detected_at, anomaly_end, resolved_at, state):
        session.add(
            LogSourceAnomaly(
                log_source_id=ls.id,
                anomaly_type=AnomalyType.VOLUME_DROP,
                severity=Severity.HIGH,
                state=state,
                detected_at=detected_at,
                anomaly_start=detected_at,
                anomaly_end=anomaly_end,
                resolved_at=resolved_at,
                consecutive_buckets=1,
                policy_version=1,
                evidence_status=EvidenceStatus.NOT_REQUESTED,
            )
        )
        await session.flush()

    async def _cell(self, session, ls):
        return await session.scalar(
            select(LogSourceBaseline).where(
                LogSourceBaseline.log_source_id == ls.id,
                LogSourceBaseline.metric_name == "average_eps",
                LogSourceBaseline.weekday == 3,
                LogSourceBaseline.hour == 10,
            )
        )

    async def test_open_incident_does_not_block_its_own_recovery_baseline(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst)
        now = await _weekly_series(
            db_session, ls, weeks=12, hour=10, eps_values=[50]
        )
        # Still OPEN, but it stopped being abnormal long ago: anomaly_end sits
        # before the samples the new cell must learn from.
        await self._anomaly(
            db_session, ls,
            detected_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
            anomaly_end=datetime(2026, 7, 1, 10, 5, tzinfo=UTC),
            resolved_at=None,
            state=AnomalyState.OPEN,
        )

        builder = BaselineBuilder(db_session, settings=_settings())
        await builder.rebuild_for_source(ls, now=now)

        cell = await self._cell(db_session, ls)
        assert cell is not None, "an unresolved incident must not erase the whole cell"
        assert cell.is_reliable is True

    async def test_unopened_candidate_does_not_poison_the_cell_forever(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst)
        now = await _weekly_series(
            db_session, ls, weeks=12, hour=10, eps_values=[50]
        )
        # Returned to normal before opening: resolved_at stays NULL permanently.
        await self._anomaly(
            db_session, ls,
            detected_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
            anomaly_end=datetime(2026, 7, 1, 10, 5, tzinfo=UTC),
            resolved_at=None,
            state=AnomalyState.NORMAL,
        )

        builder = BaselineBuilder(db_session, settings=_settings())
        await builder.rebuild_for_source(ls, now=now)

        cell = await self._cell(db_session, ls)
        assert cell is not None
        assert cell.is_reliable is True

    async def test_in_progress_anomaly_still_excludes_to_the_window_end(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        ls = await make_log_source(db_session, inst)
        now = await _weekly_series(
            db_session, ls, weeks=12, hour=10, eps_values=[50]
        )
        # No anomaly_end yet: genuinely still in progress, so nothing after it
        # may be learned from.
        await self._anomaly(
            db_session, ls,
            detected_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
            anomaly_end=None,
            resolved_at=None,
            state=AnomalyState.OPEN,
        )

        builder = BaselineBuilder(db_session, settings=_settings())
        await builder.rebuild_for_source(ls, now=now)

        cell = await self._cell(db_session, ls)
        assert cell is None or cell.is_reliable is False


# ------------------------------------------------------------------- one-statement write
#
# HB-12 D2: the rebuild writes a source's cells in one bulk upsert. The per-cell
# SELECT-then-insert/update path this replaces is kept here, verbatim, as the
# equivalence reference -- it must never come back into `app/`.


async def _reference_upsert_cell(
    session,
    settings: Settings,
    log_source_id,
    metric: str,
    weekday: int,
    hour: int,
    values: list[float],
    window_start: datetime,
    window_end: datetime,
    *,
    completeness: float,
    tally: ExclusionTally,
) -> None:
    """Today's per-cell path: one SELECT of the row, then an ORM insert or update."""
    cell = summarize_cell(values, min_samples=settings.baseline_min_samples)
    med, mad_value, p05, p95 = cell.median, cell.mad, cell.p05, cell.p95
    reliable = cell.is_reliable
    stored_obs = cell.observations

    existing = await session.scalar(
        select(LogSourceBaseline).where(
            LogSourceBaseline.log_source_id == log_source_id,
            LogSourceBaseline.metric_name == metric,
            LogSourceBaseline.weekday == weekday,
            LogSourceBaseline.hour == hour,
        )
    )
    if existing is None:
        session.add(
            LogSourceBaseline(
                log_source_id=log_source_id,
                metric_name=metric,
                weekday=weekday,
                hour=hour,
                median=med,
                mad=mad_value,
                p05=p05,
                p95=p95,
                sample_count=len(values),
                is_reliable=reliable,
                window_start=window_start,
                window_end=window_end,
                computed_at=window_end,
                baseline_version=1,
                observations=stored_obs,
                completeness=completeness,
                excluded_sample_count=tally.total,
                exclusion_counts=tally.as_dict(),
            )
        )
    else:
        existing.median = med
        existing.mad = mad_value
        existing.p05 = p05
        existing.p95 = p95
        existing.sample_count = len(values)
        existing.is_reliable = reliable
        existing.window_start = window_start
        existing.window_end = window_end
        existing.computed_at = window_end
        existing.baseline_version += 1
        existing.observations = stored_obs
        existing.completeness = completeness
        existing.excluded_sample_count = tally.total
        existing.exclusion_counts = tally.as_dict()


async def _reference_rebuild_for_source(session, log_source, settings: Settings, *, now):
    """The whole old rebuild loop, over the reference per-cell write."""
    lookback_start = now - timedelta(days=settings.baseline_lookback_days)
    builder = BaselineBuilder(session, settings=settings)
    observations, tally, candidates = await builder._load_clean_observations(
        log_source, lookback_start, now
    )
    cells: dict[tuple[str, int, int], list[float]] = {}
    for obs in observations:
        weekday, hour = seasonal_key(obs.bucket_start)
        for metric, value in obs.values.items():
            cells.setdefault((metric, weekday, hour), []).append(value)
    completeness = retained_fraction(len(observations), candidates)
    for (metric, weekday, hour), values in cells.items():
        await _reference_upsert_cell(
            session,
            settings,
            log_source.id,
            metric,
            weekday,
            hour,
            values,
            lookback_start,
            now,
            completeness=completeness,
            tally=tally,
        )
    await session.flush()
    return len(cells)


#: Decision 2's columns: every non-key column the rebuild writes.
_EQUIVALENT_COLUMNS = (
    "median",
    "mad",
    "p05",
    "p95",
    "sample_count",
    "is_reliable",
    "window_start",
    "window_end",
    "computed_at",
    "baseline_version",
    "observations",
    "completeness",
    "excluded_sample_count",
    "exclusion_counts",
)


async def _seed_every_cell(session, ls, *, now: datetime, weeks: int = 8) -> int:
    """Eight weeks of hourly samples: every one of the 168 cells, for both metrics."""
    start = now - timedelta(weeks=weeks)
    rows = []
    for index in range(168):
        for week in range(weeks):
            at = start + timedelta(hours=index, weeks=week)
            eps = 20.0 + index * 0.5 + week * 0.25
            rows.append(
                LogSourceMetric(
                    log_source_id=ls.id,
                    bucket_start=at,
                    average_eps=eps,
                    event_count=round(eps * 300),
                )
            )
    session.add_all(rows)
    await session.flush()
    return len(rows)


async def _cells_map(session, ls) -> dict[tuple[str, int, int], LogSourceBaseline]:
    rows = (
        await session.scalars(
            select(LogSourceBaseline)
            .where(LogSourceBaseline.log_source_id == ls.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    return {(row.metric_name, row.weekday, row.hour): row for row in rows}


def _assert_same_cells(reference, bulk) -> None:
    assert set(reference) == set(bulk)
    for key, expected in reference.items():
        actual = bulk[key]
        for name in _EQUIVALENT_COLUMNS:
            assert getattr(actual, name) == getattr(expected, name), (key, name)


def _cell_state(cell) -> tuple:
    """Every column of a cell, key and timestamps included."""
    return (
        *(getattr(cell, name) for name in _EQUIVALENT_COLUMNS),
        cell.id,
        cell.created_at,
        cell.updated_at,
    )


async def _drop_samples(session, ls, *, now: datetime, weeks: int, cell: int, keep: int):
    """Leave only the last `keep` samples of one cell; drop the rest of its history."""
    start = now - timedelta(weeks=weeks)
    dropped = [start + timedelta(hours=cell, weeks=week) for week in range(weeks - keep)]
    await session.execute(
        delete(LogSourceMetric).where(
            LogSourceMetric.log_source_id == ls.id,
            LogSourceMetric.bucket_start.in_(dropped),
        )
    )
    await session.flush()


async def test_the_bulk_write_is_column_for_column_the_per_cell_path(db_session) -> None:
    inst = await make_instance(db_session)
    reference = await make_log_source(db_session, inst, qradar_id=2001, name="reference")
    bulk = await make_log_source(db_session, inst, qradar_id=2002, name="bulk")
    now = datetime(2026, 7, 8, tzinfo=UTC)
    assert await _seed_every_cell(db_session, reference, now=now) == 168 * 8
    assert await _seed_every_cell(db_session, bulk, now=now) == 168 * 8
    settings = _settings()

    # First rebuild: every cell is new, so every version is 1.
    assert await _reference_rebuild_for_source(
        db_session, reference, settings, now=now
    ) == 168 * len(BASELINE_METRICS)
    assert await BaselineBuilder(
        db_session, settings=settings
    ).rebuild_for_source(bulk, now=now) == 168 * len(BASELINE_METRICS)

    first_reference = await _cells_map(db_session, reference)
    first_bulk = await _cells_map(db_session, bulk)
    assert len(first_bulk) == 168 * len(BASELINE_METRICS)
    _assert_same_cells(first_reference, first_bulk)
    assert {cell.baseline_version for cell in first_bulk.values()} == {1}
    assert {cell.sample_count for cell in first_bulk.values()} == {8}
    created_at = {key: cell.created_at for key, cell in first_bulk.items()}

    # Second rebuild: versions are 2, the values still agree cell by cell.
    later = now + timedelta(days=1)
    assert await _reference_rebuild_for_source(db_session, reference, settings, now=later) == (
        168 * len(BASELINE_METRICS)
    )
    assert await BaselineBuilder(
        db_session, settings=settings
    ).rebuild_for_source(bulk, now=later) == 168 * len(BASELINE_METRICS)

    second_reference = await _cells_map(db_session, reference)
    second_bulk = await _cells_map(db_session, bulk)
    _assert_same_cells(second_reference, second_bulk)
    assert {cell.baseline_version for cell in second_bulk.values()} == {2}
    # Decision 2: `updated_at` moves on an update, `created_at` does not.
    for key, cell in second_bulk.items():
        assert cell.created_at == created_at[key], key
        assert cell.updated_at == later, key


async def test_a_rebuild_does_not_leave_a_stale_cell_in_the_session(db_session) -> None:
    """Decision 3: a cell read before the rebuild must not keep its old values."""
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    now = await _weekly_series(db_session, ls, weeks=12, hour=10, eps_values=[50])
    settings = _settings()
    builder = BaselineBuilder(db_session, settings=settings)
    await builder.rebuild_for_source(ls, now=now)

    query = select(LogSourceBaseline).where(
        LogSourceBaseline.log_source_id == ls.id,
        LogSourceBaseline.metric_name == "average_eps",
        LogSourceBaseline.weekday == 3,
        LogSourceBaseline.hour == 10,
    )
    first = await db_session.scalar(query)
    assert first is not None and first.baseline_version == 1

    later = now + timedelta(days=1)
    await builder.rebuild_for_source(ls, now=later)

    # The same session, and the same identity-map object, must now read the
    # rebuilt row rather than the values it held before the bulk statement.
    again = await db_session.scalar(query)
    assert again is first
    assert again.baseline_version == 2
    assert again.updated_at == later


async def test_a_thin_cell_is_written_exactly_as_the_per_cell_path(db_session) -> None:
    """A cell below the minimum sample count is stored the same way as before.

    It exists, it keeps its observations, and it is marked not reliable so it
    cannot drive an alert. The other cells of the same source are reliable, so
    the rebuild writes both kinds in one statement.
    """
    inst = await make_instance(db_session)
    reference = await make_log_source(db_session, inst, qradar_id=2003, name="reference")
    bulk = await make_log_source(db_session, inst, qradar_id=2004, name="bulk")
    now = datetime(2026, 7, 8, tzinfo=UTC)
    for ls in (reference, bulk):
        await _seed_every_cell(db_session, ls, now=now)
        # Three samples in one cell: below `baseline_min_samples` (8).
        await _drop_samples(db_session, ls, now=now, weeks=8, cell=0, keep=3)
    settings = _settings()

    assert await _reference_rebuild_for_source(
        db_session, reference, settings, now=now
    ) == 168 * len(BASELINE_METRICS)
    assert await BaselineBuilder(
        db_session, settings=settings
    ).rebuild_for_source(bulk, now=now) == 168 * len(BASELINE_METRICS)

    reference_cells = await _cells_map(db_session, reference)
    bulk_cells = await _cells_map(db_session, bulk)
    _assert_same_cells(reference_cells, bulk_cells)

    weekday, hour = seasonal_key(now - timedelta(weeks=8))
    thin = [cell for key, cell in bulk_cells.items() if key[1:] == (weekday, hour)]
    assert len(thin) == len(BASELINE_METRICS)
    for cell in thin:
        assert cell.is_reliable is False
        assert cell.sample_count == 3
        assert cell.observations
    others = [
        cell for key, cell in bulk_cells.items() if key[1:] != (weekday, hour)
    ]
    assert others and {cell.is_reliable for cell in others} == {True}


async def test_a_cell_without_an_observation_is_left_untouched(db_session) -> None:
    """A cell that exists but has no observation this time keeps its row.

    Its values, its version and its timestamps stay as they were: there is
    nothing to rebuild it from, so the rebuild leaves it alone.
    """
    inst = await make_instance(db_session)
    reference = await make_log_source(db_session, inst, qradar_id=2005, name="reference")
    bulk = await make_log_source(db_session, inst, qradar_id=2006, name="bulk")
    now = datetime(2026, 7, 8, tzinfo=UTC)
    for ls in (reference, bulk):
        await _seed_every_cell(db_session, ls, now=now)
    settings = _settings()

    await _reference_rebuild_for_source(db_session, reference, settings, now=now)
    await BaselineBuilder(db_session, settings=settings).rebuild_for_source(bulk, now=now)
    first_reference = await _cells_map(db_session, reference)
    first_bulk = await _cells_map(db_session, bulk)

    # The history of one cell is gone: it has nothing to be rebuilt from.
    weekday, hour = seasonal_key(now - timedelta(weeks=8))
    for ls in (reference, bulk):
        await _drop_samples(db_session, ls, now=now, weeks=8, cell=0, keep=0)

    later = now + timedelta(days=1)
    await _reference_rebuild_for_source(db_session, reference, settings, now=later)
    await BaselineBuilder(
        db_session, settings=settings
    ).rebuild_for_source(bulk, now=later)

    second_reference = await _cells_map(db_session, reference)
    second_bulk = await _cells_map(db_session, bulk)
    _assert_same_cells(second_reference, second_bulk)
    for cells, first in ((second_reference, first_reference), (second_bulk, first_bulk)):
        for key, cell in cells.items():
            if key[1:] == (weekday, hour):
                assert _cell_state(cell) == _cell_state(first[key]), key
            else:
                assert cell.baseline_version == 2, key
