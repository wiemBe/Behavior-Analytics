"""WAF endpoint baseline computation.

Same algorithm as Phase A — group by (weekday, hour), median/MAD/p05/p95,
exclusion tally, completeness ratio — reusing `app.anomaly.statistics`
unchanged. Phase A's `BaselineBuilder` itself is not reused because it is typed
on `LogSource`/`LogSourceMetric`/`LogSourceBaseline` throughout; extracting that
is the "minimal additive genericization" of the design's section 15.1, and is
deliberately deferred until a second consumer proves the shape.

Two Phase B differences, both recorded in the exclusion tally so an operator can
see why a baseline is thin:

  * `low_coverage` — a bucket where too little of the source's traffic carried
    a parseable endpoint. Phase A has no equivalent because a log source's
    volume needs no field to parse.
  * baselines are scoped to a normalizer version, so a rule change makes cells
    stale rather than blending incomparable observations.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly import statistics as rstats
from app.core.config import Settings, get_settings
from app.models.waf import (
    WafAnomaly,
    WafEndpoint,
    WafEndpointBaseline,
    WafEndpointMetric,
)
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION

#: The metric baselined for the MVP. `distinct_client_count` is collected but
#: not yet baselined -- it has no detector, and a baseline nothing reads is
#: storage without a reader.
WAF_BASELINE_METRIC = "request_count"


@dataclass
class WafExclusionTally:
    known_anomaly: int = 0
    incomplete: int = 0
    unfinished: int = 0
    low_coverage: int = 0

    @property
    def total(self) -> int:
        return (
            self.known_anomaly
            + self.incomplete
            + self.unfinished
            + self.low_coverage
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "known_anomaly": self.known_anomaly,
            "incomplete": self.incomplete,
            "unfinished": self.unfinished,
            "low_coverage": self.low_coverage,
        }


class WafBaselineBuilder:
    def __init__(
        self, session: AsyncSession, *, settings: Settings | None = None
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()

    async def rebuild_for_endpoint(
        self, endpoint: WafEndpoint, *, now: datetime | None = None
    ) -> int:
        """Recompute all baseline cells for one endpoint. Returns cells written."""
        now = now or datetime.now(UTC)
        lookback_start = now - timedelta(days=self.settings.baseline_lookback_days)

        values, tally, candidates = await self._clean_observations(
            endpoint, lookback_start, now
        )

        cells: dict[tuple[int, int], list[float]] = {}
        for bucket_start, value in values:
            cells.setdefault(
                (bucket_start.isoweekday(), bucket_start.hour), []
            ).append(value)

        completeness = (len(values) / candidates) if candidates else 0.0

        written = 0
        for (weekday, hour), samples in cells.items():
            await self._upsert_cell(
                endpoint.id,
                weekday,
                hour,
                samples,
                lookback_start,
                now,
                completeness=completeness,
                tally=tally,
            )
            written += 1
        await self.session.flush()
        return written

    async def _clean_observations(
        self, endpoint: WafEndpoint, start: datetime, end: datetime
    ) -> tuple[list[tuple[datetime, float]], WafExclusionTally, int]:
        rows = list(
            (
                await self.session.scalars(
                    select(WafEndpointMetric)
                    .where(
                        WafEndpointMetric.waf_endpoint_id == endpoint.id,
                        WafEndpointMetric.bucket_start >= start,
                        WafEndpointMetric.bucket_start < end,
                        WafEndpointMetric.normalizer_version
                        == ENDPOINT_NORMALIZER_VERSION,
                    )
                    .order_by(WafEndpointMetric.bucket_start)
                )
            ).all()
        )
        excluded = await self._anomaly_spans(endpoint, end)
        tally = WafExclusionTally()
        out: list[tuple[datetime, float]] = []

        for m in rows:
            # The bucket containing `end` is still accumulating; its partial
            # count would drag every cell it lands in downwards.
            if m.bucket_end > end:
                tally.unfinished += 1
                continue
            if not m.is_complete:
                tally.incomplete += 1
                continue
            if m.endpoint_coverage_ratio < self.settings.waf_min_endpoint_coverage:
                tally.low_coverage += 1
                continue
            if any(lo <= m.bucket_start <= hi for lo, hi in excluded):
                tally.known_anomaly += 1
                continue
            out.append((m.bucket_start, float(m.request_count)))
        return out, tally, len(rows)

    async def _anomaly_spans(
        self, endpoint: WafEndpoint, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        """Spans of any incident on this endpoint, resolved or not.

        A span ends when the anomaly *stopped*, not when it was resolved. Phase
        A learned this the hard way (commit 4892f8e): bounding by `resolved_at`
        extends the span to the end of the lookback for anything unresolved,
        which deadlocks an OPEN incident -- it excludes the very buckets the
        baseline needs to judge recovery, so the detector returns
        INSUFFICIENT_DATA forever and the incident can never close.
        """
        anomalies = list(
            (
                await self.session.scalars(
                    select(WafAnomaly).where(
                        WafAnomaly.waf_endpoint_id == endpoint.id,
                        WafAnomaly.detected_at < end,
                    )
                )
            ).all()
        )
        return [
            (a.detected_at, a.resolved_at or a.anomaly_end or end) for a in anomalies
        ]

    async def _upsert_cell(
        self,
        endpoint_id: uuid.UUID,
        weekday: int,
        hour: int,
        values: list[float],
        window_start: datetime,
        window_end: datetime,
        *,
        completeness: float,
        tally: WafExclusionTally,
    ) -> None:
        med = rstats.median(values)
        mad_value = rstats.mad(values, med)
        p05 = rstats.percentile(values, 5)
        p95 = rstats.percentile(values, 95)
        reliable = len(values) >= self.settings.baseline_min_samples
        stored = [round(v, 4) for v in values[-200:]]

        existing = await self.session.scalar(
            select(WafEndpointBaseline).where(
                WafEndpointBaseline.waf_endpoint_id == endpoint_id,
                WafEndpointBaseline.metric_name == WAF_BASELINE_METRIC,
                WafEndpointBaseline.weekday == weekday,
                WafEndpointBaseline.hour == hour,
                WafEndpointBaseline.normalizer_version == ENDPOINT_NORMALIZER_VERSION,
            )
        )
        if existing is None:
            self.session.add(
                WafEndpointBaseline(
                    waf_endpoint_id=endpoint_id,
                    metric_name=WAF_BASELINE_METRIC,
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
                    normalizer_version=ENDPOINT_NORMALIZER_VERSION,
                    observations=stored,
                    completeness=completeness,
                    excluded_sample_count=tally.total,
                    exclusion_counts=tally.as_dict(),
                )
            )
            return

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
        existing.observations = stored
        existing.completeness = completeness
        existing.excluded_sample_count = tally.total
        existing.exclusion_counts = tally.as_dict()
