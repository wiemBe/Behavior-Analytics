"""Bounded bulk baseline training for property-watch values."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.seasonal import anomaly_exclusion_span
from app.core.config import Settings, get_settings
from app.models.instance import QRadarInstance
from app.models.property_watch import PwBucket, PwValue
from app.property_watch.baseline import (
    PW_VALUE_EVENTS_SEMANTICS,
    PwBaselineReading,
    build_value_baseline,
)
from app.property_watch.engine import _aware, _bucket_fact, _tracked_at
from app.property_watch.facts import reading_for
from app.repositories.property_watch import PropertyWatchRepository


@dataclass(frozen=True)
class PwTrainingReport:
    values_rebuilt: int = 0
    cells_written: int = 0
    chunks: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "values_rebuilt": self.values_rebuilt,
            "cells_written": self.cells_written,
            "chunks": self.chunks,
        }


class PwBaselineTrainer:
    """Rebuild least-recently-computed values, with constant SQL per chunk."""

    def __init__(self, session: AsyncSession, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.repo = PropertyWatchRepository(session)

    async def rebuild_due(
        self,
        instance: QRadarInstance,
        *,
        now: datetime | None = None,
    ) -> PwTrainingReport:
        now = now or datetime.now(UTC)
        due = await self.repo.due_baseline_values(
            instance.id, limit=self.settings.pw_baseline_values_per_run
        )
        if not due:
            return PwTrainingReport()
        chunk_size = self.settings.pw_baseline_values_per_run
        cells_written = 0
        chunks = 0
        for offset in range(0, len(due), chunk_size):
            chunk = due[offset : offset + chunk_size]
            cells_written += await self._rebuild_chunk(chunk, now=now)
            chunks += 1
        return PwTrainingReport(len(due), cells_written, chunks)

    async def _rebuild_chunk(
        self, due: list[tuple[PwValue, datetime]], *, now: datetime
    ) -> int:
        ends = {value.id: min(now, _aware(end) or end) for value, end in due}
        values = [value for value, _ in due]
        start = min(ends.values()) - timedelta(days=self.settings.pw_baseline_lookback_days)
        end = max(ends.values())
        watch_ids = sorted({value.watch_id for value in values})
        buckets = await self.repo.buckets_between(watch_ids, start=start, end=end)
        metrics = await self.repo.metrics_between(
            [value.id for value in values], start=start, end=end
        )
        spans = await self.repo.active_incident_spans([value.id for value in values], end=end)
        by_watch: dict[uuid.UUID, list[PwBucket]] = {}
        for bucket in buckets:
            by_watch.setdefault(bucket.watch_id, []).append(bucket)
        metric_map = {
            (row.pw_value_id, _aware(row.bucket_start) or row.bucket_start): row.events
            for row in metrics
        }
        span_map: dict[uuid.UUID, list[tuple[datetime, datetime]]] = {}
        for value_id, detected_at, anomaly_end, resolved_at in spans:
            span_map.setdefault(value_id, []).append(
                anomaly_exclusion_span(
                    _aware(detected_at) or detected_at,
                    _aware(anomaly_end),
                    _aware(resolved_at),
                    end,
                )
            )
        rows: list[dict[str, object]] = []
        for value in values:
            value_end = ends[value.id]
            value_start = value_end - timedelta(days=self.settings.pw_baseline_lookback_days)
            readings = []
            for bucket_row in by_watch.get(value.watch_id, ()):
                fact = _bucket_fact(bucket_row)
                if not value_start <= fact.bucket_start <= value_end:
                    continue
                readings.append(
                    PwBaselineReading(
                        fact,
                        reading_for(
                            fact,
                            pinned=value.pinned,
                            tracked_at_bucket=_tracked_at(value, fact.bucket_start),
                            metric_events=metric_map.get((value.id, fact.bucket_start)),
                        ),
                    )
                )
            cells, tally = build_value_baseline(
                readings,
                watch_version=value.watch_version,
                min_coverage=self.settings.pw_min_coverage,
                baseline_min_samples=self.settings.baseline_min_samples,
                incident_spans=span_map.get(value.id, ()),
            )
            for cell in cells.values():
                rows.append(
                    {
                        "id": uuid.uuid4(),
                        "pw_value_id": value.id,
                        "metric_name": cell.metric_name,
                        "weekday": cell.weekday,
                        "hour": cell.hour,
                        "median": cell.median,
                        "mad": cell.mad,
                        "p05": cell.p05,
                        "p95": cell.p95,
                        "sample_count": cell.sample_count,
                        "is_reliable": cell.is_reliable,
                        "completeness": cell.completeness,
                        "observations": cell.observations,
                        "window_start": value_start,
                        "window_end": value_end,
                        "computed_at": now,
                        "baseline_version": 1,
                        "excluded_sample_count": tally.total,
                        "exclusion_counts": tally.as_dict(),
                        "metric_semantics": PW_VALUE_EVENTS_SEMANTICS,
                        "watch_version": value.watch_version,
                    }
                )
        await self.repo.upsert_baselines(rows)
        return len(rows)
