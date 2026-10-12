"""Fleet summary: per-class volume, expectation and incidents in a fixed number of queries.

Four grouped statements -- sources, metrics, baselines, incidents -- however many
sources or classes exist. Per-source figures are folded in Python from the one
metric statement, so there is no query per class and none per source.

Honesty rules (AGENTS.md rule 8): only COMPLETE buckets contribute to events and
EPS; a class with no complete bucket reports null, not zero; expected, complete
and partial bucket counts travel with every figure.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, func, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    ACTIVE_ANOMALY_STATES,
    BucketCompleteness,
    ClassificationReviewStatus,
    SourceClass,
)
from app.models.fleet import LogSourceClassification
from app.models.log_source import (
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceMetric,
)
from app.schemas.fleet import (
    WINDOW_SECONDS,
    BucketCounts,
    ClassSummary,
    FleetSummary,
    FleetWindow,
    InternalSummary,
    SourceCounts,
    TopSource,
)

TOP_SOURCES = 5
EPS_METRIC = "average_eps"
_UNCLASSIFIED = SourceClass.UNCLASSIFIED.value
_INTERNAL = SourceClass.QRADAR_INTERNAL.value


def _class_of() -> ColumnElement[str]:
    """A source with no classification row is UNCLASSIFIED, never absent."""
    return func.coalesce(LogSourceClassification.source_class, literal(_UNCLASSIFIED))


async def build_fleet_summary(
    session: AsyncSession,
    *,
    window: FleetWindow,
    now: datetime,
    bucket_seconds: int,
) -> FleetSummary:
    window_seconds = WINDOW_SECONDS[window]
    window_start = now - timedelta(seconds=window_seconds)
    buckets_per_source = window_seconds // bucket_seconds
    source_class = _class_of()

    # 1. Sources by class: counts, monitored-enabled count for expectations, and
    #    the NEW / conflict tallies, all in one pass.
    monitored = (LogSource.enabled.is_(True)) & (LogSource.monitoring_enabled.is_(True))
    source_rows = (
        await session.execute(
            select(
                source_class,
                func.count().filter(LogSource.enabled.is_(True)),
                func.count().filter(LogSource.enabled.is_(False)),
                func.count().filter(monitored),
                func.count().filter(
                    LogSourceClassification.review_status
                    == ClassificationReviewStatus.NEW.value
                ),
                func.count().filter(
                    func.jsonb_array_length(LogSourceClassification.conflicts) > 0
                ),
            )
            .select_from(LogSource)
            .outerjoin(
                LogSourceClassification,
                LogSourceClassification.log_source_id == LogSource.id,
            )
            .group_by(source_class)
        )
    ).all()
    enabled: dict[str, int] = {}
    disabled: dict[str, int] = {}
    monitored_count: dict[str, int] = {}
    new_sources = conflict_sources = 0
    for cls, n_enabled, n_disabled, n_monitored, n_new, n_conflict in source_rows:
        enabled[cls], disabled[cls], monitored_count[cls] = n_enabled, n_disabled, n_monitored
        new_sources += n_new
        conflict_sources += n_conflict

    # 2. Metrics per source in the window. COMPLETE rows feed events and EPS;
    #    PARTIAL rows are counted only to say how much of the window is unusable.
    complete = LogSourceMetric.completeness == BucketCompleteness.COMPLETE.value
    partial = LogSourceMetric.completeness == BucketCompleteness.PARTIAL.value
    metric_rows = (
        await session.execute(
            select(
                LogSource.id,
                LogSource.name,
                source_class,
                func.count().filter(complete),
                func.count().filter(partial),
                func.coalesce(func.sum(LogSourceMetric.event_count).filter(complete), 0),
                func.coalesce(func.sum(LogSourceMetric.bucket_seconds).filter(complete), 0),
            )
            .select_from(LogSourceMetric)
            .join(LogSource, LogSource.id == LogSourceMetric.log_source_id)
            .outerjoin(
                LogSourceClassification,
                LogSourceClassification.log_source_id == LogSource.id,
            )
            .where(
                LogSourceMetric.bucket_start >= window_start,
                LogSourceMetric.bucket_start < now,
            )
            .group_by(LogSource.id, LogSource.name, source_class)
        )
    ).all()
    complete_buckets: dict[str, int] = defaultdict(int)
    partial_buckets: dict[str, int] = defaultdict(int)
    events: dict[str, int] = defaultdict(int)
    eps: dict[str, float] = defaultdict(float)
    top: dict[str, list[TopSource]] = defaultdict(list)
    for sid, name, cls, n_complete, n_partial, n_events, n_seconds in metric_rows:
        complete_buckets[cls] += n_complete
        partial_buckets[cls] += n_partial
        if n_complete:
            events[cls] += int(n_events)
            # Rate over the seconds actually measured for this source, summed
            # across the class: a source with half its buckets missing is not
            # diluted by the gap.
            eps[cls] += float(n_events) / float(n_seconds)
            top[cls].append(TopSource(id=sid, name=name, events=int(n_events)))

    # 3. Expected EPS. Per source, the mean of its reliable average_eps cell
    #    medians over every (UTC ISO weekday, hour) cell the window spans: one
    #    cell for 1h, 24 for 24h, 168 for 7d. A source counts only when *all*
    #    spanned cells are reliable; a partial set would compare the window with
    #    a different slice of the week, so such a source is left out (and lowers
    #    the share) rather than treated as 0.
    cells = sorted(
        {
            ((t := now - timedelta(hours=k)).isoweekday(), t.hour)
            for k in range(max(1, window_seconds // 3600))
        }
    )
    per_source = (
        select(
            LogSourceBaseline.log_source_id.label("log_source_id"),
            func.avg(LogSourceBaseline.median).label("expected"),
        )
        .where(
            LogSourceBaseline.metric_name == EPS_METRIC,
            LogSourceBaseline.is_reliable.is_(True),
            tuple_(LogSourceBaseline.weekday, LogSourceBaseline.hour).in_(cells),
        )
        .group_by(LogSourceBaseline.log_source_id)
        .having(func.count() == len(cells))
        .subquery()
    )
    baseline_rows = (
        await session.execute(
            select(source_class, func.count(), func.sum(per_source.c.expected))
            .select_from(per_source)
            .join(LogSource, LogSource.id == per_source.c.log_source_id)
            .outerjoin(
                LogSourceClassification,
                LogSourceClassification.log_source_id == LogSource.id,
            )
            .where(monitored)
            .group_by(source_class)
        )
    ).all()
    baseline_sources = {cls: int(n) for cls, n, _ in baseline_rows}
    expected_eps = {cls: float(total) for cls, _, total in baseline_rows}

    # 4. Open Phase A incidents by class and type (suppressed ones are not open).
    incident_rows = (
        await session.execute(
            select(source_class, LogSourceAnomaly.anomaly_type, func.count())
            .select_from(LogSourceAnomaly)
            .join(LogSource, LogSource.id == LogSourceAnomaly.log_source_id)
            .outerjoin(
                LogSourceClassification,
                LogSourceClassification.log_source_id == LogSource.id,
            )
            .where(
                LogSourceAnomaly.state.in_([s.value for s in ACTIVE_ANOMALY_STATES]),
                LogSourceAnomaly.suppressed.is_(False),
            )
            .group_by(source_class, LogSourceAnomaly.anomaly_type)
        )
    ).all()
    incidents: dict[str, dict[str, int]] = defaultdict(dict)
    for cls, anomaly_type, n in incident_rows:
        incidents[cls][str(anomaly_type)] = n

    classes: list[ClassSummary] = []
    for member in SourceClass:
        cls = member.value
        if cls == _INTERNAL:
            continue
        expected = monitored_count.get(cls, 0) * buckets_per_source
        n_complete = complete_buckets.get(cls, 0)
        n_partial = partial_buckets.get(cls, 0)
        n_baselines = baseline_sources.get(cls, 0)
        ranked = sorted(top.get(cls, []), key=lambda item: (-item.events, item.name))
        classes.append(
            ClassSummary(
                source_class=member,
                sources=SourceCounts(enabled=enabled.get(cls, 0), disabled=disabled.get(cls, 0)),
                buckets=BucketCounts(
                    expected=expected,
                    complete=n_complete,
                    partial=n_partial,
                    missing=max(0, expected - n_complete - n_partial),
                ),
                events=events[cls] if n_complete else None,
                mean_eps=eps[cls] if n_complete else None,
                expected_eps=expected_eps.get(cls) if n_baselines else None,
                baseline_sources=n_baselines,
                baseline_share=(
                    n_baselines / monitored_count[cls]
                    if monitored_count.get(cls)
                    else None
                ),
                open_incidents=dict(sorted(incidents.get(cls, {}).items())),
                top_sources=ranked[:TOP_SOURCES],
            )
        )

    return FleetSummary(
        window=window,
        window_start=window_start,
        window_end=now,
        bucket_seconds=bucket_seconds,
        classes=classes,
        internal=InternalSummary(
            sources=SourceCounts(
                enabled=enabled.get(_INTERNAL, 0), disabled=disabled.get(_INTERNAL, 0)
            )
        ),
        new_sources=new_sources,
        conflict_sources=conflict_sources,
        unclassified_sources=enabled.get(_UNCLASSIFIED, 0) + disabled.get(_UNCLASSIFIED, 0),
    )
