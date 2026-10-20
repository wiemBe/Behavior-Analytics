"""Baseline computation.

Groups a log source's historical observations of a metric by (weekday, hour) and
computes a robust (median, MAD) baseline per cell. Design points required by the
spec:

  * Group by weekday and hour (same weekday+hour across weeks are comparable).
  * Median + MAD; MAD = 0 handled by app.anomaly.statistics.effective_scale.
  * Configurable minimum sample count; below it the cell is stored but marked
    not-reliable and must not drive alerts.
  * Exclude maintenance windows and intervals overlapping an active/known
    anomaly, so an ongoing incident does not poison the baseline it will later
    be judged against.
  * Store baseline version and the observations used.
  * Runs asynchronously (invoked from a Celery task), never inline with
    detection.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.seasonal import (
    anomaly_exclusion_span,
    in_any_span,
    in_maintenance,
    retained_fraction,
    seasonal_key,
    summarize_cell,
)
from app.core.config import Settings, get_settings
from app.models.log_source import (
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceMetric,
)

# Which metric columns get baselines. These are the ones anomaly detection
# compares against a historical norm.
BASELINE_METRICS = ("average_eps", "event_count")


@dataclass
class Observation:
    bucket_start: datetime
    values: dict[str, float]


@dataclass
class ExclusionTally:
    """Why candidate buckets were dropped, for baseline completeness.

    An unexpectedly thin baseline is a support question ("why is this source
    still INSUFFICIENT_DATA after three weeks?"). Recording the reason at build
    time answers it without re-running the build against history that may have
    since aged out.
    """

    maintenance: int = 0
    off_hours: int = 0
    known_anomaly: int = 0
    incomplete: int = 0
    unfinished: int = 0

    @property
    def total(self) -> int:
        return (
            self.maintenance
            + self.off_hours
            + self.known_anomaly
            + self.incomplete
            + self.unfinished
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "maintenance": self.maintenance,
            "off_hours": self.off_hours,
            "known_anomaly": self.known_anomaly,
            "incomplete": self.incomplete,
            "unfinished": self.unfinished,
        }


class BaselineBuilder:
    def __init__(self, session: AsyncSession, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    async def rebuild_for_source(
        self, log_source: LogSource, *, now: datetime | None = None
    ) -> int:
        """Recompute all baseline cells for one source. Returns cells written."""
        now = now or datetime.now(UTC)
        lookback_start = now - timedelta(days=self.settings.baseline_lookback_days)

        observations, tally, candidates = await self._load_clean_observations(
            log_source, lookback_start, now
        )

        # Group by (metric, weekday, hour). Phase A seasonality is ISO weekday
        # crossed with hour of day: 168 cells, capturing the working day and the
        # weekend without needing months of history to populate.
        cells: dict[tuple[str, int, int], list[float]] = {}
        for obs in observations:
            weekday, hour = seasonal_key(obs.bucket_start)
            for metric, value in obs.values.items():
                cells.setdefault((metric, weekday, hour), []).append(value)

        # Completeness is a property of the whole rebuild: what fraction of the
        # candidate buckets in the lookback actually survived exclusion. A cell
        # built from 4 of 28 buckets is far weaker than one built from 26 even
        # when both clear the minimum sample count.
        completeness = retained_fraction(len(observations), candidates)

        rows = [
            self._cell_row(
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
            for (metric, weekday, hour), values in cells.items()
        ]
        await self._upsert_cells(rows)
        return len(rows)

    async def _load_clean_observations(
        self, log_source: LogSource, start: datetime, end: datetime
    ) -> tuple[list[Observation], ExclusionTally, int]:
        """Return usable observations, why the rest were dropped, and how many
        candidate buckets were considered."""
        rows = list(
            (
                await self.session.scalars(
                    select(LogSourceMetric)
                    .where(
                        LogSourceMetric.log_source_id == log_source.id,
                        LogSourceMetric.bucket_start >= start,
                        LogSourceMetric.bucket_start < end,
                    )
                    .order_by(LogSourceMetric.bucket_start)
                )
            ).all()
        )

        excluded = await self._excluded_intervals(log_source, start, end)
        tally = ExclusionTally()

        # The bucket containing `end` is still accumulating. Its partial count
        # would drag every cell it lands in downwards, so it never enters a
        # baseline regardless of what completeness it claims.
        observations: list[Observation] = []
        for m in rows:
            if m.bucket_end > end:
                tally.unfinished += 1
                continue
            if not m.is_complete:
                tally.incomplete += 1
                continue
            reason = self._exclusion_reason(m.bucket_start, log_source, excluded)
            if reason is not None:
                setattr(tally, reason, getattr(tally, reason) + 1)
                continue
            observations.append(
                Observation(
                    bucket_start=m.bucket_start,
                    values={
                        "average_eps": float(m.average_eps),
                        "event_count": float(m.event_count),
                    },
                )
            )
        return observations, tally, len(rows)

    async def _excluded_intervals(
        self, log_source: LogSource, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        """Intervals to exclude: spans of any anomaly (resolved or not) for this
        source. An active anomaly must not poison the baseline used to judge it.

        A span ends when the anomaly stopped, which is not the same as when it
        was resolved. Bounding by `resolved_at` alone extends the span to the
        end of the lookback window for anything unresolved, which breaks two
        ways:

          * Deadlock. An OPEN incident then excludes the very buckets a new
            seasonal cell needs, so the detector has no baseline, returns
            INSUFFICIENT_DATA instead of a healthy verdict, and the incident can
            never recover -- it needs the baseline that its own existence
            prevents. Observed live on 2026-08-02: source 262 sat OPEN through
            seven consecutive normal buckets with `consecutive_healthy` at zero.
          * Permanent poisoning. A CANDIDATE that returns to normal before
            opening is never resolved, so `resolved_at` stays NULL forever and
            that source's buckets are excluded indefinitely.

        Buckets after `anomaly_end` are the recovery buckets -- normal by
        definition, and exactly what recovery must be judged on. An incident
        with no `anomaly_end` yet is still genuinely in progress, so it keeps
        excluding to the end of the window.
        """
        anomalies = list(
            (
                await self.session.scalars(
                    select(LogSourceAnomaly).where(
                        LogSourceAnomaly.log_source_id == log_source.id,
                        LogSourceAnomaly.detected_at < end,
                    )
                )
            ).all()
        )
        return [
            anomaly_exclusion_span(a.detected_at, a.anomaly_end, a.resolved_at, end)
            for a in anomalies
        ]

    def _exclusion_reason(
        self,
        when: datetime,
        log_source: LogSource,
        excluded: list[tuple[datetime, datetime]],
    ) -> str | None:
        """The ExclusionTally field name to charge this bucket to, or None."""
        # Maintenance window.
        if in_maintenance(
            when,
            maintenance_mode=log_source.maintenance_mode,
            maintenance_until=log_source.maintenance_until,
        ):
            return "maintenance"
        # Business-hours-only sources: off-hours intervals are expected-empty and
        # must not be baselined as if they were real observations.
        if log_source.business_hours_only and not self._in_business_hours(when, log_source):
            return "off_hours"
        # Known anomaly spans. An ongoing incident must not poison the baseline
        # it will later be judged against.
        if in_any_span(when, excluded):
            return "known_anomaly"
        return None

    def _in_business_hours(self, when: datetime, log_source: LogSource) -> bool:
        weekday = when.isoweekday()
        if weekday not in (log_source.business_days or [1, 2, 3, 4, 5]):
            return False
        return log_source.business_hours_start <= when.hour < log_source.business_hours_end

    def _cell_row(
        self,
        log_source_id: uuid.UUID,
        metric: str,
        weekday: int,
        hour: int,
        values: list[float],
        window_start: datetime,
        window_end: datetime,
        *,
        completeness: float,
        tally: ExclusionTally,
    ) -> dict[str, object]:
        """One cell of the bulk upsert, with every column the rebuild sets."""
        cell = summarize_cell(values, min_samples=self.settings.baseline_min_samples)
        return {
            "id": uuid.uuid4(),
            "log_source_id": log_source_id,
            "metric_name": metric,
            "weekday": weekday,
            "hour": hour,
            "median": cell.median,
            "mad": cell.mad,
            "p05": cell.p05,
            "p95": cell.p95,
            "sample_count": len(values),
            "is_reliable": cell.is_reliable,
            "window_start": window_start,
            "window_end": window_end,
            "computed_at": window_end,
            "baseline_version": 1,
            "observations": cell.observations,
            "completeness": completeness,
            "excluded_sample_count": tally.total,
            "exclusion_counts": tally.as_dict(),
            # The mixins' Python-side defaults do not run on a Core insert, so
            # the primary key and both timestamps are set here. `created_at` is
            # deliberately absent from the conflict update below: it must keep
            # its original value, while `updated_at` must move.
            "created_at": window_end,
            "updated_at": window_end,
        }

    async def _upsert_cells(self, rows: list[dict[str, object]]) -> None:
        """Write a source's cells in one statement.

        One prepared INSERT, executed once per cell by the driver: the shape the
        WAF and IPS baseline writers use, and one statement whatever the number
        of cells. The rows travel as typed binds -- 168 cells x 2 Phase A
        metrics x 21 columns is 7,056 parameters, far below PostgreSQL's
        65,535 -- so a JSON document (as the WAF history path uses) would buy
        nothing.

        `baseline_version` is 1 on insert and the stored value + 1 on update, in
        SQL, so no per-cell read is needed. A cell that exists but has no
        observation this time is not in `rows` and is left untouched.
        """
        if not rows:
            return
        table = cast(Table, LogSourceBaseline.__table__)
        statement = pg_insert(table)
        # Columns the insert owns: the key, the version (computed below) and the
        # creation time (which keeps its original value on update).
        insert_only = {
            "id",
            "log_source_id",
            "metric_name",
            "weekday",
            "hour",
            "baseline_version",
            "created_at",
        }
        replaced = {
            name: statement.excluded[name] for name in rows[0] if name not in insert_only
        }
        statement = statement.on_conflict_do_update(
            constraint="uq_log_source_baseline_cell",
            set_={
                **replaced,
                "baseline_version": table.c.baseline_version + 1,
                "updated_at": statement.excluded.updated_at,
            },
        )
        await self.session.execute(statement, rows)
        # A Core statement bypasses the ORM, so an object already loaded for this
        # source would keep its old values in the identity map, and a read
        # through this same session would return them. Neither caller loads a
        # baseline into its session (the collector tick and the nightly task
        # read only sources and metrics), but a caller that does must not be
        # served the values the bulk write just replaced.
        for obj in list(self.session.identity_map.values()):
            if isinstance(obj, LogSourceBaseline):
                self.session.expire(obj)
