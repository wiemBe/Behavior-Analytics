"""WAF endpoint baseline computation, in bulk.

Same algorithm as Phase A — group by (weekday, hour), median/MAD/p05/p95,
exclusion tally, completeness ratio — through the shared pure rules in
`app.anomaly.seasonal`. Phase A's `BaselineBuilder` itself is not reused: it is
typed on `LogSource`/`LogSourceMetric`/`LogSourceBaseline` throughout, and each
domain keeps its own persistence.

Phase B differences, each recorded in the exclusion tally so an operator can
see why a baseline is thin:

  * `low_coverage` — a bucket where too little of the source's traffic carried
    a parseable endpoint. Phase A has no equivalent because a log source's
    volume needs no field to parse.
  * `incompatible_semantics` — an observation under a different metric-semantics
    identity (app/waf/semantics.py), including every row collected before
    identities existed. It never trains a cell.
  * `maintenance` — a bucket the shared rule `app.anomaly.seasonal.in_maintenance`
    placed in its source's maintenance window *when the bucket was collected*, as
    recorded on its `WafSourceBucket`. The recorded decision is used rather than
    re-evaluating the rule against the current flag, because the flag has no
    start: re-evaluated now, turning maintenance on would exclude every earlier
    bucket in the lookback, and turning it off would readmit the maintenance
    buckets. Only a bucket with no record falls back to the rule evaluated now.

Observations come from `app.waf.observations`, so a sparse history -- rows only
where an endpoint had traffic -- trains exactly the cells a dense history of
explicit zeros would: an exact bucket contributes a zero for every tracked
endpoint without a row, and a lower-bound bucket contributes nothing but an
`incomplete` exclusion, as its explicit PARTIAL zero row did.

**Cost.** A rebuild of any number of endpoints loads the source's bucket records
once, then per chunk of `WAF_BASELINE_REBUILD_CHUNK_SIZE` endpoints issues one
query for their rows, one for their incident spans, one bulk upsert for all
their cells and one batched update of their rebuild time. Statements scale with
chunks, never with endpoints or cells.

A rebuild may be bounded by `train_through`, so scheduled rebuilds train only on
buckets the detector has already judged (app/services/waf_pipeline.py).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import Table, func, select
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
from app.models.enums import WafBreakdown, WafMeasurement, WafTelemetryStatus
from app.models.log_source import LogSource
from app.models.waf import (
    WafAnomaly,
    WafEndpoint,
    WafEndpointBaseline,
    WafEndpointMetric,
    WafSourceBucket,
)
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from app.waf.observations import BucketFacts, EndpointObservation, RowFacts, endpoint_series

#: The request-count metric this builder trains. `distinct_client_count` is collected but
#: not yet baselined -- it has no detector, and a baseline nothing reads is
#: storage without a reader.
WAF_BASELINE_METRIC = "request_count"
#: Phase B.2 proportion metrics. Each cell keeps its pooled numerator and
#: denominator; see `RATIO_METRICS` for what each divides.
BLOCK_RATIO_METRIC = "block_ratio"
SERVER_ERROR_RATIO_METRIC = "server_error_ratio"
CLIENT_ERROR_RATIO_METRIC = "client_error_ratio"
TOP_CLIENT_SHARE_METRIC = "top1_client_share"


@dataclass(frozen=True)
class RatioMetric:
    """How one stored breakdown becomes a proportion baseline."""

    numerator: str
    denominator: str
    breakdown: WafBreakdown
    status: str
    semantics: str
    uncertain: str | None
    materiality_setting: str


RATIO_METRICS: dict[str, RatioMetric] = {
    BLOCK_RATIO_METRIC: RatioMetric(
        numerator="blocked",
        denominator="action_events",
        breakdown=WafBreakdown.ACTION,
        status="action",
        semantics="action_semantics",
        uncertain="unmapped_actions",
        materiality_setting="waf_ratio_min_denominator",
    ),
    SERVER_ERROR_RATIO_METRIC: RatioMetric(
        numerator="server_errors",
        denominator="response_code_events",
        breakdown=WafBreakdown.RESPONSE_CODE,
        status="response_code",
        semantics="response_code_semantics",
        uncertain="unclassified_responses",
        materiality_setting="waf_ratio_min_denominator",
    ),
    CLIENT_ERROR_RATIO_METRIC: RatioMetric(
        numerator="client_errors",
        denominator="response_code_events",
        breakdown=WafBreakdown.RESPONSE_CODE,
        status="response_code",
        semantics="response_code_semantics",
        uncertain="unclassified_responses",
        materiality_setting="waf_ratio_min_denominator",
    ),
    TOP_CLIENT_SHARE_METRIC: RatioMetric(
        numerator="top1_client_requests",
        denominator="client_events",
        breakdown=WafBreakdown.SOURCE_IP,
        status="client",
        semantics="client_semantics",
        uncertain=None,
        materiality_setting="waf_concentration_min_endpoint_requests",
    ),
}


@dataclass
class WafExclusionTally:
    known_anomaly: int = 0
    incomplete: int = 0
    unfinished: int = 0
    low_coverage: int = 0
    maintenance: int = 0
    incompatible_semantics: int = 0

    @property
    def total(self) -> int:
        return sum(self.as_dict().values())

    def as_dict(self) -> dict[str, int]:
        return {
            "known_anomaly": self.known_anomaly,
            "incomplete": self.incomplete,
            "unfinished": self.unfinished,
            "low_coverage": self.low_coverage,
            "maintenance": self.maintenance,
            "incompatible_semantics": self.incompatible_semantics,
        }


@dataclass
class WafRatioExclusionTally(WafExclusionTally):
    """Ratio-only exclusions; request-count tallies retain their original shape."""

    unavailable: int = 0
    uncertain_values: int = 0
    inexact_request_count: int = 0
    low_source_coverage: int = 0
    immaterial: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            **super().as_dict(),
            "unavailable": self.unavailable,
            "uncertain_values": self.uncertain_values,
            "inexact_request_count": self.inexact_request_count,
            "low_source_coverage": self.low_source_coverage,
            "immaterial": self.immaterial,
        }


@dataclass(frozen=True)
class RatioSample:
    bucket_start: datetime
    proportion: float
    numerator: int
    denominator: int


def clean_samples(
    series: Sequence[EndpointObservation],
    *,
    end: datetime,
    semantics: str,
    min_coverage: float,
    spans: Sequence[tuple[datetime, datetime]],
    maintenance_rule: Callable[[datetime], bool],
) -> tuple[list[tuple[datetime, float]], WafExclusionTally, int]:
    """The observations that may train, and why the others may not. Pure.

    Returns (samples, tally, candidates). The order of the checks is the order
    exclusions are attributed in, and is part of the contract operators read.
    """
    tally = WafExclusionTally()
    out: list[tuple[datetime, float]] = []
    for obs in series:
        # The bucket containing `end` is still accumulating; its partial count
        # would drag every cell it lands in downwards.
        if obs.bucket_end > end:
            tally.unfinished += 1
            continue
        if obs.metric_semantics != semantics:
            tally.incompatible_semantics += 1
            continue
        recorded = obs.in_maintenance
        if recorded if recorded is not None else maintenance_rule(obs.bucket_start):
            tally.maintenance += 1
            continue
        if not obs.is_complete:
            tally.incomplete += 1
            continue
        if obs.endpoint_coverage_ratio < min_coverage:
            tally.low_coverage += 1
            continue
        if in_any_span(obs.bucket_start, spans):
            tally.known_anomaly += 1
            continue
        out.append((obs.bucket_start, float(obs.request_count)))
    return out, tally, len(series)


def clean_ratio_samples(
    series: Sequence[EndpointObservation],
    metric: RatioMetric,
    *,
    end: datetime,
    semantics: str,
    min_coverage: float,
    min_denominator: int,
    spans: Sequence[tuple[datetime, datetime]],
    maintenance_rule: Callable[[datetime], bool],
) -> tuple[list[RatioSample], WafRatioExclusionTally, int]:
    """Material exact proportions that may train one ratio metric. Pure.

    The order of the checks is the order exclusions are attributed in, and is
    part of the contract operators read: unfinished, incompatible_semantics,
    maintenance, unavailable, incomplete, uncertain_values, inexact_request_count,
    low_source_coverage, immaterial (empty population), low_coverage, immaterial
    (below the minimum denominator), known_anomaly.

    `low_source_coverage` is the share of the source's events that parsed to an
    endpoint at all (decision D-3). `low_coverage` is the breakdown's share of
    the endpoint's visible requests. Unparseable requests may differ in kind from
    parseable ones, so the visible proportion bounds nothing when the former is
    low, however complete the latter is.
    """
    tally = WafRatioExclusionTally()
    out: list[RatioSample] = []
    for obs in series:
        if obs.bucket_end > end:
            tally.unfinished += 1
            continue
        status = getattr(obs.breakdowns, metric.status)
        if (
            status in (WafTelemetryStatus.EXACT, WafTelemetryStatus.LOWER_BOUND)
            and getattr(obs.breakdowns, metric.semantics) != semantics
        ):
            tally.incompatible_semantics += 1
            continue
        recorded = obs.in_maintenance
        if recorded if recorded is not None else maintenance_rule(obs.bucket_start):
            tally.maintenance += 1
            continue

        if status in (WafTelemetryStatus.NOT_COLLECTED, WafTelemetryStatus.FAILED):
            tally.unavailable += 1
            continue
        if status is not WafTelemetryStatus.EXACT:
            tally.incomplete += 1
            continue
        numerator = getattr(obs.counts, metric.numerator)
        denominator = getattr(obs.counts, metric.denominator)
        uncertain = getattr(obs.counts, metric.uncertain) if metric.uncertain else 0
        if numerator is None or denominator is None or uncertain is None:
            tally.unavailable += 1
            continue
        if uncertain:
            tally.uncertain_values += 1
            continue
        if obs.measurement is not WafMeasurement.EXACT:
            tally.inexact_request_count += 1
            continue
        if obs.endpoint_coverage_ratio < min_coverage:
            tally.low_source_coverage += 1
            continue
        # A sparse exact breakdown reconstructs 0/0 for an absent endpoint.
        # It says no ratio population existed, not that the proportion was zero.
        if denominator == 0:
            tally.immaterial += 1
            continue
        coverage = denominator / obs.request_count if obs.request_count else 0.0
        if coverage < min_coverage:
            tally.low_coverage += 1
            continue
        if denominator < min_denominator:
            tally.immaterial += 1
            continue
        if in_any_span(obs.bucket_start, spans):
            tally.known_anomaly += 1
            continue
        out.append(RatioSample(obs.bucket_start, numerator / denominator, numerator, denominator))
    return out, tally, len(series)


class WafBaselineBuilder:
    def __init__(
        self, session: AsyncSession, *, settings: Settings | None = None
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()

    async def rebuild_for_endpoint(
        self,
        endpoint: WafEndpoint,
        *,
        semantics: str,
        now: datetime | None = None,
        train_through: datetime | None = None,
        log_source: LogSource | None = None,
        breakdown_identities: Mapping[WafBreakdown, str] | None = None,
    ) -> int:
        """Test-only wrapper around ``rebuild_endpoints`` for a single endpoint."""
        written = await self.rebuild_endpoints(
            [endpoint],
            semantics=semantics,
            now=now,
            train_through=train_through,
            log_source=log_source,
            breakdown_identities=breakdown_identities,
        )
        return written[endpoint.id]

    async def rebuild_endpoints(
        self,
        endpoints: Sequence[WafEndpoint],
        *,
        semantics: str,
        now: datetime | None = None,
        train_through: datetime | None = None,
        log_source: LogSource | None = None,
        source_buckets: Mapping[datetime, BucketFacts] | None = None,
        breakdown_identities: Mapping[WafBreakdown, str] | None = None,
    ) -> dict[uuid.UUID, int]:
        """Recompute the cells of many endpoints of one source. Returns cells per endpoint.

        `semantics` is the request-count identity being rebuilt. Ratio metrics
        are rebuilt only for breakdowns present in `breakdown_identities`, and
        each uses that breakdown's identity. `train_through` bounds the window's
        end below `now`. `source_buckets` may be supplied by a caller that
        already loaded the source's bucket records for the same window.
        """
        written: dict[uuid.UUID, int] = {e.id: 0 for e in endpoints}
        if not endpoints:
            return written
        now = now or datetime.now(UTC)
        window_end = min(now, train_through) if train_through is not None else now
        lookback_start = window_end - timedelta(days=self.settings.baseline_lookback_days)
        source_id = endpoints[0].log_source_id
        if any(e.log_source_id != source_id for e in endpoints):
            raise ValueError("rebuild_endpoints takes endpoints of one log source")
        if log_source is None:
            log_source = await self.session.get(LogSource, source_id)
        if source_buckets is None:
            source_buckets = await self.source_bucket_facts(source_id, lookback_start, window_end)

        def rule(bucket_start: datetime) -> bool:
            return log_source is not None and in_maintenance(
                bucket_start,
                maintenance_mode=log_source.maintenance_mode,
                maintenance_until=log_source.maintenance_until,
            )

        chunk_size = self.settings.waf_baseline_rebuild_chunk_size
        for i in range(0, len(endpoints), chunk_size):
            chunk = endpoints[i : i + chunk_size]
            rows = await self._rows(chunk, lookback_start, window_end)
            spans = await self._anomaly_spans(chunk, window_end)
            cell_rows: list[dict] = []
            for endpoint in chunk:
                series = endpoint_series(
                    rows.get(endpoint.id, ()),
                    source_buckets,
                    tracked_at=endpoint.tracked_at,
                    normalizer_version=endpoint.normalizer_version,
                    start=lookback_start,
                    end=window_end,
                )
                values, tally, candidates = clean_samples(
                    series,
                    end=window_end,
                    semantics=semantics,
                    min_coverage=self.settings.waf_min_endpoint_coverage,
                    spans=spans.get(endpoint.id, ()),
                    maintenance_rule=rule,
                )
                cells: dict[tuple[int, int], list[float]] = {}
                for bucket_start, value in values:
                    cells.setdefault(seasonal_key(bucket_start), []).append(value)
                completeness = retained_fraction(len(values), candidates)
                for (weekday, hour), samples in cells.items():
                    cell_rows.append(
                        self._cell_row(
                            endpoint.id, WAF_BASELINE_METRIC, weekday, hour, samples,
                            lookback_start, window_end,
                            completeness=completeness, tally=tally, semantics=semantics,
                        )
                    )
                written[endpoint.id] += len(cells)

                for metric_name, metric in RATIO_METRICS.items():
                    identity = (breakdown_identities or {}).get(metric.breakdown)
                    if identity is None:
                        continue
                    ratio_values, ratio_tally, ratio_candidates = clean_ratio_samples(
                        series,
                        metric,
                        end=window_end,
                        semantics=identity,
                        min_coverage=self.settings.waf_min_endpoint_coverage,
                        min_denominator=getattr(self.settings, metric.materiality_setting),
                        spans=spans.get(endpoint.id, ()),
                        maintenance_rule=rule,
                    )
                    ratio_cells: dict[tuple[int, int], list[RatioSample]] = {}
                    for sample in ratio_values:
                        ratio_cells.setdefault(
                            seasonal_key(sample.bucket_start), []
                        ).append(sample)
                    ratio_completeness = retained_fraction(
                        len(ratio_values), ratio_candidates
                    )
                    for (weekday, hour), ratio_samples in ratio_cells.items():
                        cell_rows.append(
                            self._cell_row(
                                endpoint.id,
                                metric_name,
                                weekday,
                                hour,
                                [sample.proportion for sample in ratio_samples],
                                lookback_start,
                                window_end,
                                completeness=ratio_completeness,
                                tally=ratio_tally,
                                semantics=identity,
                                numerator_sum=sum(
                                    sample.numerator for sample in ratio_samples
                                ),
                                denominator_sum=sum(
                                    sample.denominator for sample in ratio_samples
                                ),
                            )
                        )
                    written[endpoint.id] += len(ratio_cells)
                endpoint.baseline_rebuilt_at = now
            await self._upsert_cells(cell_rows)
        await self.session.flush()
        return written

    async def source_bucket_facts(
        self, log_source_id: uuid.UUID, start: datetime, end: datetime
    ) -> dict[datetime, BucketFacts]:
        """Every collected bucket's record in `[start, end)`. One query."""
        b = WafSourceBucket
        rows = await self.session.execute(
            select(
                b.bucket_start, b.bucket_seconds, b.completeness, b.endpoint_coverage_ratio,
                b.metric_semantics, b.in_maintenance, b.request_count_measurement,
                b.sparse_endpoint_rows, b.action_measurement, b.action_semantics,
                b.response_code_measurement, b.response_code_semantics,
                b.client_measurement, b.client_semantics,
            ).where(b.log_source_id == log_source_id, b.bucket_start >= start, b.bucket_start < end)
        )
        return {row.bucket_start: BucketFacts.of(row) for row in rows.all()}

    async def _rows(
        self, endpoints: Sequence[WafEndpoint], start: datetime, end: datetime
    ) -> dict[uuid.UUID, list[RowFacts]]:
        m = WafEndpointMetric
        result = await self.session.execute(
            select(
                m.waf_endpoint_id, m.bucket_start, m.bucket_seconds, m.request_count,
                m.distinct_client_count, m.completeness, m.endpoint_coverage_ratio,
                m.metric_semantics, m.normalizer_version, m.action_events, m.blocked_count,
                m.unmapped_action_count, m.response_code_events, m.client_error_count,
                m.server_error_count, m.unclassified_response_count, m.client_events,
                m.top1_client_requests, m.top5_client_requests, m.client_sources,
                m.top_clients,
            ).where(
                m.waf_endpoint_id.in_([e.id for e in endpoints]),
                m.bucket_start >= start,
                m.bucket_start < end,
                m.normalizer_version == ENDPOINT_NORMALIZER_VERSION,
            )
        )
        out: dict[uuid.UUID, list[RowFacts]] = {}
        for row in result.all():
            out.setdefault(row.waf_endpoint_id, []).append(RowFacts.of(row))
        return out

    async def _anomaly_spans(
        self, endpoints: Sequence[WafEndpoint], end: datetime
    ) -> dict[uuid.UUID, list[tuple[datetime, datetime]]]:
        """Spans of any incident on these endpoints, resolved or not.

        A span ends when the anomaly *stopped*, not when it was resolved. Phase
        A learned this the hard way (commit 4892f8e): bounding by `resolved_at`
        extends the span to the end of the lookback for anything unresolved,
        which deadlocks an OPEN incident -- it excludes the very buckets the
        baseline needs to judge recovery, so the detector returns
        INSUFFICIENT_DATA forever and the incident can never close.
        """
        a = WafAnomaly
        result = await self.session.execute(
            select(a.waf_endpoint_id, a.detected_at, a.anomaly_end, a.resolved_at).where(
                a.waf_endpoint_id.in_([e.id for e in endpoints]), a.detected_at < end
            )
        )
        out: dict[uuid.UUID, list[tuple[datetime, datetime]]] = {}
        for row in result.all():
            out.setdefault(row.waf_endpoint_id, []).append(
                anomaly_exclusion_span(row.detected_at, row.anomaly_end, row.resolved_at, end)
            )
        return out

    def _cell_row(
        self,
        endpoint_id: uuid.UUID,
        metric_name: str,
        weekday: int,
        hour: int,
        values: list[float],
        window_start: datetime,
        window_end: datetime,
        *,
        completeness: float,
        tally: WafExclusionTally,
        semantics: str,
        numerator_sum: float | None = None,
        denominator_sum: float | None = None,
    ) -> dict:
        cell = summarize_cell(values, min_samples=self.settings.baseline_min_samples)
        return {
            "id": uuid.uuid4(),
            "waf_endpoint_id": endpoint_id,
            "metric_name": metric_name,
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
            "normalizer_version": ENDPOINT_NORMALIZER_VERSION,
            "observations": cell.observations,
            "completeness": completeness,
            "numerator_sum": numerator_sum,
            "denominator_sum": denominator_sum,
            "excluded_sample_count": tally.total,
            "exclusion_counts": tally.as_dict(),
            "metric_semantics": semantics,
        }

    async def _upsert_cells(self, cell_rows: list[dict]) -> None:
        """One statement for every cell of a chunk; a rewrite bumps its version."""
        if not cell_rows:
            return
        table = cast(Table, WafEndpointBaseline.__table__)
        stmt = pg_insert(table)
        replaced = {
            c: stmt.excluded[c]
            for c in cell_rows[0]
            if c not in ("id", "waf_endpoint_id", "metric_name", "weekday", "hour",
                         "normalizer_version", "baseline_version")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["waf_endpoint_id", "metric_name", "weekday", "hour",
                            "normalizer_version"],
            set_={
                **replaced,
                "baseline_version": table.c.baseline_version + 1,
                "updated_at": func.now(),
            },
        )
        await self.session.execute(stmt, cell_rows)
        # The upsert bypassed the unit of work; cells already loaded into this
        # session must not keep serving their old statistics.
        for obj in list(self.session.identity_map.values()):
            if isinstance(obj, WafEndpointBaseline):
                self.session.expire(obj)
