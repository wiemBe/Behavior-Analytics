"""Bounded bulk baseline training for property-watch values."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.seasonal import (
    anomaly_exclusion_span,
    in_any_span,
    retained_fraction,
    seasonal_key,
)
from app.anomaly.training_exclusions import PW_VALUE_SCOPE, Span, SpanKey, load_active
from app.core.config import Settings, get_settings
from app.models.enums import WafMeasurement
from app.models.instance import QRadarInstance
from app.models.property_watch import PwBucket, PwValue
from app.property_watch.baseline import (
    PW_VALUE_EVENTS_SEMANTICS,
    PwBaselineReading,
    build_value_baseline,
)
from app.property_watch.engine import _aware, _bucket_fact, _tracked_at
from app.property_watch.facts import PwReadingKind, reading_for
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


def _in_half_open(when: datetime, spans: Sequence[tuple[datetime, datetime]]) -> bool:
    """Whether ``when`` falls in a half-open ``[start, end)`` span.

    Analyst exclusions use this. ``in_any_span`` is closed on the end, which is
    what an incident span wants and what an analyst interval does not.
    """
    return any(start <= when < end for start, end in spans)


def _aware_spans(
    spans: Sequence[tuple[datetime, datetime]],
) -> tuple[tuple[datetime, datetime], ...]:
    return tuple((_aware(start) or start, _aware(end) or end) for start, end in spans)


def _hold_out_analyst(
    readings: list[PwBaselineReading],
    *,
    watch_version: int,
    min_coverage: float,
    incident_spans: Sequence[tuple[datetime, datetime]],
    analyst_spans: Sequence[tuple[datetime, datetime]],
) -> tuple[list[PwBaselineReading], int, dict[tuple[int, int], int], dict[tuple[int, int], int]]:
    """Drop readings an analyst exclusion would have trained, and count them.

    The decision order matches ``build_value_baseline``. An analyst span is
    half-open and is charged only when no automatic reason already applies, so
    a bucket an incident span already removes is counted once, as
    ``known_anomaly``. ``NOT_AN_OBSERVATION`` is not a candidate. ``candidates``
    counts every other reading of the slot, including the ones held out, so
    completeness can be repaired after the builder runs on the remainder.
    """
    if not analyst_spans:
        return readings, 0, {}, {}
    kept: list[PwBaselineReading] = []
    analyst_count = 0
    analyst_slots: dict[tuple[int, int], int] = {}
    candidates: dict[tuple[int, int], int] = {}
    for item in readings:
        bucket, reading = item.bucket, item.reading
        if reading.kind is PwReadingKind.NOT_AN_OBSERVATION:
            kept.append(item)
            continue
        key = seasonal_key(bucket.bucket_start)
        candidates[key] = candidates.get(key, 0) + 1
        automatic = (
            reading.measurement is WafMeasurement.LOWER_BOUND
            or reading.events is None
            or reading.measurement is None
            or not bucket.coverage.reaches(min_coverage)
            or bucket.scope_changed
            or bucket.in_maintenance
            or bucket.watch_version != watch_version
            or in_any_span(bucket.bucket_start, incident_spans)
        )
        if automatic or not _in_half_open(bucket.bucket_start, analyst_spans):
            kept.append(item)
            continue
        analyst_count += 1
        analyst_slots[key] = analyst_slots.get(key, 0) + 1
    return kept, analyst_count, analyst_slots, candidates


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
        # One exclusion read for every due value, before the chunk loop. An
        # empty due list returned above and issued none (SOC-13h).
        ends = {value.id: min(now, _aware(end) or end) for value, end in due}
        lookback = timedelta(days=self.settings.pw_baseline_lookback_days)
        # The sample window includes a bucket that starts at the window end
        # (`bucket_start <= value_end`, and `value_end` is `evaluated_through`,
        # the last bucket start). `load_active` keeps a span only when
        # `starts_at < lookback_end`, so the end passed here is one microsecond
        # later. A span that starts on that last bucket is loaded; a span that
        # starts after it is not.
        analyst = await load_active(
            self.session,
            "value",
            [value.id for value, _end in due],
            PW_VALUE_SCOPE,
            min(ends.values()) - lookback,
            max(ends.values()) + timedelta(microseconds=1),
        )
        chunk_size = self.settings.pw_baseline_values_per_run
        cells_written = 0
        chunks = 0
        for offset in range(0, len(due), chunk_size):
            chunk = due[offset : offset + chunk_size]
            cells_written += await self._rebuild_chunk(chunk, now=now, analyst=analyst)
            chunks += 1
        return PwTrainingReport(len(due), cells_written, chunks)

    async def _rebuild_chunk(
        self,
        due: list[tuple[PwValue, datetime]],
        *,
        now: datetime,
        analyst: Mapping[SpanKey, Sequence[Span]],
    ) -> int:
        ends = {value.id: min(now, _aware(end) or end) for value, end in due}
        values = [value for value, _ in due]
        start = min(ends.values()) - timedelta(days=self.settings.pw_baseline_lookback_days)
        end = max(ends.values())
        watch_ids = sorted({value.watch_id for value in values})
        buckets = await self.repo.buckets_between(watch_ids, start=start, end=end)
        metrics = await self.repo.training_metrics_between(
            [value.id for value in values], start=start, end=end
        )
        spans = await self.repo.active_incident_spans([value.id for value in values], end=end)
        by_watch: dict[uuid.UUID, list[PwBucket]] = {}
        for bucket in buckets:
            by_watch.setdefault(bucket.watch_id, []).append(bucket)
        metric_map = {
            (value_id, _aware(bucket_start) or bucket_start): events
            for value_id, bucket_start, events in metrics
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
            incident_spans = span_map.get(value.id, ())
            kept, analyst_count, analyst_slots, candidates = _hold_out_analyst(
                readings,
                watch_version=value.watch_version,
                min_coverage=self.settings.pw_min_coverage,
                incident_spans=incident_spans,
                analyst_spans=_aware_spans(
                    analyst.get(("value", value.id, PW_VALUE_SCOPE), ())
                ),
            )
            cells, tally = build_value_baseline(
                kept,
                watch_version=value.watch_version,
                min_coverage=self.settings.pw_min_coverage,
                baseline_min_samples=self.settings.baseline_min_samples,
                baseline_min_visits=self.settings.pw_baseline_min_visits,
                incident_spans=incident_spans,
            )
            # Present only when an analyst exclusion took effect, so a value
            # without one stores exactly the document it always did.
            counts = tally.as_dict()
            excluded = tally.total
            if analyst_count:
                counts["analyst_exclusion"] = analyst_count
                excluded += analyst_count
            for cell in cells.values():
                slot = (cell.weekday, cell.hour)
                completeness = cell.completeness
                if analyst_slots.get(slot, 0):
                    completeness = retained_fraction(cell.sample_count, candidates[slot])
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
                        "completeness": completeness,
                        "observations": cell.observations,
                        "window_start": value_start,
                        "window_end": value_end,
                        "computed_at": now,
                        "baseline_version": 1,
                        "excluded_sample_count": excluded,
                        "exclusion_counts": dict(counts),
                        "metric_semantics": PW_VALUE_EVENTS_SEMANTICS,
                        "watch_version": value.watch_version,
                    }
                )
        await self.repo.upsert_baselines(rows)
        # Any cell of these values the rebuild did not write is stale: its samples aged out
        # of the window, or an incident span or an analyst exclusion emptied it. Removal
        # is how a property-watch detector fails closed (no row, so no baseline). The
        # delete matches ``computed_at`` to this run's ``now`` (PW-3c item 3, SOC-13h).
        await self.repo.delete_stale_baselines(
            [value.id for value in values], computed_at=now
        )
        return len(rows)
