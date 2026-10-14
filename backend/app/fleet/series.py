"""Fleet EPS series: observed and expected EPS per step in a fixed number of queries.

Three statements -- monitored sources, metric cells grouped by class and step,
baselines -- however many sources, classes or steps exist. Both `group_by` values
are folded in Python from the same rows.

Honesty rules (AGENTS.md rule 8): a step is summed only when every monitored,
non-internal source in scope has a COMPLETE metric row in every bucket of the step;
otherwise events and EPS are null, never a smaller sum. Expected EPS is the sum of
per-source reliable medians and is null unless every source in scope has one. No
band is built from the sums.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fleet.summary import EPS_METRIC, _class_of
from app.models.enums import BucketCompleteness, SourceClass
from app.models.fleet import LogSourceClassification
from app.models.log_source import LogSource, LogSourceBaseline, LogSourceMetric
from app.schemas.fleet import (
    WINDOW_SECONDS,
    EpsCoverage,
    EpsSeries,
    EpsSeriesPoint,
    FleetEpsSeries,
    FleetSeriesGroup,
    FleetWindow,
)

# Step per window; 1h uses the collection bucket (None). A step the bucket width
# does not divide falls back to the bucket width.
STEP_SECONDS: dict[str, int | None] = {"1h": None, "24h": 300, "7d": 3600}
FLEET_KEY = "FLEET"
_INTERNAL = SourceClass.QRADAR_INTERNAL.value
_HOUR = 3600

Cell = tuple[int, int]


def step_seconds_for(window: FleetWindow, bucket_seconds: int) -> int:
    step = STEP_SECONDS[window]
    if step is None or step % bucket_seconds != 0 or WINDOW_SECONDS[window] % step != 0:
        return bucket_seconds
    return step


def _cell_of_step(start: datetime, step_seconds: int) -> Cell | None:
    """The one (UTC ISO weekday, hour) cell a step lies in, or None.

    Steps are floored to the step width, so a step whose width divides an hour never
    straddles two cells. Any other width (for example 420 s, or 2 h) can, and its
    expected EPS is left null: a mean of medians would be invented, not measured.
    """
    if _HOUR % step_seconds != 0:
        return None
    return (start.isoweekday(), start.hour)


async def build_fleet_eps_series(
    session: AsyncSession,
    *,
    window: FleetWindow,
    group_by: FleetSeriesGroup,
    now: datetime,
    bucket_seconds: int,
) -> FleetEpsSeries:
    """`now` is the last closed bucket; the window ends at `now` floored to the step.

    Up to one step minus one bucket of the newest data is left out, so every step is a
    whole step and maps to a single baseline cell.
    """
    window_seconds = WINDOW_SECONDS[window]
    step_seconds = step_seconds_for(window, bucket_seconds)
    now = datetime.fromtimestamp(now.timestamp() // step_seconds * step_seconds, now.tzinfo)
    window_start = now - timedelta(seconds=window_seconds)
    steps = window_seconds // step_seconds
    per_step_buckets = step_seconds // bucket_seconds
    source_class = _class_of()
    monitored = (LogSource.enabled.is_(True)) & (LogSource.monitoring_enabled.is_(True))
    in_scope = monitored & (source_class != _INTERNAL)

    # 1. Monitored, non-internal sources and their class.
    source_rows = (
        await session.execute(
            select(LogSource.id, source_class)
            .select_from(LogSource)
            .outerjoin(
                LogSourceClassification,
                LogSourceClassification.log_source_id == LogSource.id,
            )
            .where(in_scope)
        )
    ).all()
    class_of: dict[object, str] = {row[0]: row[1] for row in source_rows}
    per_class_sources: dict[str, int] = defaultdict(int)
    for cls in class_of.values():
        per_class_sources[cls] += 1

    # 2. COMPLETE cells and their events, per class and step. A row of another
    #    width is not a cell of this grid. Rows of sources no longer monitored
    #    are filtered out by the join.
    complete = LogSourceMetric.completeness == BucketCompleteness.COMPLETE.value
    step_index = cast(
        func.floor(
            (
                func.extract("epoch", LogSourceMetric.bucket_start)
                - int(window_start.timestamp())
            )
            / step_seconds
        ),
        Integer,
    )
    metric_rows = (
        await session.execute(
            select(
                source_class,
                step_index,
                func.count().filter(complete),
                func.coalesce(func.sum(LogSourceMetric.event_count).filter(complete), 0),
            )
            .select_from(LogSourceMetric)
            .join(LogSource, LogSource.id == LogSourceMetric.log_source_id)
            .outerjoin(
                LogSourceClassification,
                LogSourceClassification.log_source_id == LogSource.id,
            )
            .where(
                in_scope,
                LogSourceMetric.bucket_seconds == bucket_seconds,
                LogSourceMetric.bucket_start >= window_start,
                LogSourceMetric.bucket_start < now,
            )
            .group_by(source_class, step_index)
        )
    ).all()
    cell_complete: dict[tuple[str, int], int] = {}
    cell_events: dict[tuple[str, int], int] = {}
    for cls, idx, n_complete, n_events in metric_rows:
        cell_complete[(cls, idx)] = int(n_complete)
        cell_events[(cls, idx)] = int(n_events)

    # 3. Reliable baseline medians of monitored, non-internal sources.
    baseline_rows = (
        await session.execute(
            select(
                LogSourceBaseline.log_source_id,
                LogSourceBaseline.weekday,
                LogSourceBaseline.hour,
                LogSourceBaseline.median,
            )
            .join(LogSource, LogSource.id == LogSourceBaseline.log_source_id)
            .outerjoin(
                LogSourceClassification,
                LogSourceClassification.log_source_id == LogSource.id,
            )
            .where(
                in_scope,
                LogSourceBaseline.metric_name == EPS_METRIC,
                LogSourceBaseline.is_reliable.is_(True),
            )
        )
    ).all()
    medians: dict[object, dict[Cell, float]] = defaultdict(dict)
    for sid, weekday, hour, median in baseline_rows:
        medians[sid][(weekday, hour)] = float(median)

    step_cells = [
        _cell_of_step(window_start + timedelta(seconds=i * step_seconds), step_seconds)
        for i in range(steps)
    ]

    def expected_for(source_ids: list[object], i: int) -> float | None:
        """Sum of per-source expected EPS for step i; None unless every source has it."""
        if not source_ids:
            return None
        total = 0.0
        for sid in source_ids:
            by_cell = medians.get(sid)
            if not by_cell:
                return None
            cell = step_cells[i]
            if cell is None or cell not in by_cell:
                return None
            total += by_cell[cell]
        return total

    # Series scopes: (key, label, class members, source ids).
    scopes: list[tuple[str, str, list[str], list[object]]] = []
    if group_by == "none":
        scopes.append((FLEET_KEY, "Fleet", sorted(per_class_sources), list(class_of)))
    else:
        for member in SourceClass:
            if member.value == _INTERNAL or not per_class_sources.get(member.value):
                continue
            scopes.append(
                (
                    member.value,
                    member.value,
                    [member.value],
                    [sid for sid, cls in class_of.items() if cls == member.value],
                )
            )

    series: list[EpsSeries] = []
    steps_measured = 0
    for key, label, classes, source_ids in scopes:
        n_sources = len(source_ids)
        cells_expected = n_sources * per_step_buckets
        points: list[EpsSeriesPoint] = []
        for i in range(steps):
            n_complete = sum(cell_complete.get((cls, i), 0) for cls in classes)
            measured = cells_expected > 0 and n_complete == cells_expected
            n_events = sum(cell_events.get((cls, i), 0) for cls in classes)
            steps_measured += 1 if measured else 0
            points.append(
                EpsSeriesPoint(
                    step_start=window_start + timedelta(seconds=i * step_seconds),
                    events=n_events if measured else None,
                    eps=n_events / step_seconds if measured else None,
                    expected_eps=expected_for(source_ids, i),
                    cells_complete=n_complete,
                    cells_expected=cells_expected,
                    sources_expected=n_sources,
                )
            )
        series.append(EpsSeries(key=key, label=label, sources_expected=n_sources, points=points))

    return FleetEpsSeries(
        window=window,
        group_by=group_by,
        window_start=window_start,
        window_end=now,
        bucket_seconds=bucket_seconds,
        step_seconds=step_seconds,
        series=series,
        coverage=EpsCoverage(steps_total=steps * len(series), steps_measured=steps_measured),
    )
