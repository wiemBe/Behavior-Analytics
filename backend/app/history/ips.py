"""Pure IPS history folding and planner settings (HISTORY-BACKFILL §B.4.3).

History is progressed per QRadar log source, not per live search group. A grouped keyed
search would return roughly ``n`` times the rows and therefore costs about the same
number of row-capped searches, while coupling independent sources' progress, floors and
failures. The collector consequently plans each source separately and shares only the
tick's history budget between them.

The live IPS writer already folds a whole fleet in memory. This module gives the history
path the same aggregate shape while replaying admission and retirement oldest first,
with each bucket as the retirement reference. Raw values remain inside the existing IPS
fold functions; only admitted entities, bounded contributors and aggregate rows leave
this fold. No database, provider, settings import or clock.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

from app.history.origin import HistoryOrigin, marker_values
from app.history.planner import PlannerSettings
from app.history.state import HistoryFamily
from app.ips.fold import (
    IpsActionFact,
    IpsEntityBucketFact,
    IpsEntityFact,
    IpsFanoutFact,
    IpsSourceIpFact,
    IpsTotalsFact,
    coverage,
    fold_outcome,
)
from app.ips.keyed_search import IpsCellOutcome
from app.ips.profile import IpsFieldMapping
from app.models.enums import IpsCellState, IpsDimension, IpsMeasurement
from app.models.ips import IPS_NORMALIZER_VERSION
from app.waf.admission import AdmissionPolicy, decide_admission

INITIAL_CHUNK_BUCKETS = 288
MIN_CHUNK_BUCKETS = 1
MAX_GAPS = 20
ROW_TARGET_SHARE = 0.8
ENTITY_DIMENSIONS = (
    IpsDimension.DESTINATION_PORT,
    IpsDimension.DESTINATION_IP,
    IpsDimension.SIGNATURE,
)


def ips_planner_settings(settings: Any, *, cadence_seconds: int) -> PlannerSettings:
    """The shared planner's IPS numbers; attributes come from the caller's settings."""
    return PlannerSettings(
        bucket_seconds=int(settings.collection_interval_seconds),
        floor_margin_hours=int(settings.history_backfill_floor_margin_hours),
        row_cap=int(settings.ariel_multi_bucket_max_rows),
        row_target_share=ROW_TARGET_SHARE,
        initial_chunk_buckets=INITIAL_CHUNK_BUCKETS,
        min_chunk_buckets=MIN_CHUNK_BUCKETS,
        max_failures=int(settings.history_backfill_max_failures),
        max_lower_bound_share=float(settings.history_backfill_max_lower_bound_share),
        max_hours=int(settings.history_backfill_max_hours),
        max_gaps=MAX_GAPS,
        searches_per_tick=int(settings.history_backfill_searches_per_tick),
        cadence_seconds=cadence_seconds,
    )


@dataclass(frozen=True)
class HistoryIpsEntity:
    id: uuid.UUID
    instance_id: uuid.UUID
    log_source_id: uuid.UUID
    dimension: IpsDimension
    value_key: str
    display_name: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    active: bool
    retired_at: datetime | None
    normalizer_version: int

    @property
    def key(self) -> tuple[IpsDimension, str]:
        return self.dimension, self.value_key


@dataclass(frozen=True)
class HistoryIpsBucket:
    bucket_start: datetime
    outcomes: Mapping[IpsDimension, IpsCellOutcome]


@dataclass(frozen=True)
class _Candidate:
    fact: IpsEntityFact
    dimension: IpsDimension

    @property
    def virtual_host(self) -> None:
        return None

    @property
    def endpoint(self) -> str:
        return self.fact.value_key

    @property
    def method(self) -> str:
        return self.dimension.value

    @property
    def request_count(self) -> int:
        return self.fact.events


@dataclass(frozen=True)
class IpsHistoryFold:
    entities: tuple[dict[str, object], ...]
    buckets: tuple[dict[str, object], ...]
    metrics: tuple[dict[str, object], ...]
    admitted: int
    reactivated: int
    retired: int


def _entity_values(entity: HistoryIpsEntity) -> dict[str, object]:
    return {
        "id": entity.id,
        "instance_id": entity.instance_id,
        "log_source_id": entity.log_source_id,
        "dimension": entity.dimension.value,
        "value_key": entity.value_key,
        "display_name": entity.display_name,
        "first_seen_at": entity.first_seen_at,
        "last_seen_at": entity.last_seen_at,
        "active": entity.active,
        "retired_at": entity.retired_at,
        "normalizer_version": entity.normalizer_version,
    }


def _metric_values(
    entity_id: uuid.UUID, bucket_start: datetime, bucket_seconds: int, events: int
) -> dict[str, object]:
    return {
        "ips_entity_id": entity_id,
        "bucket_start": bucket_start,
        "events": events,
        "bucket_seconds": bucket_seconds,
    }


def fold_ips_history(
    buckets: Sequence[HistoryIpsBucket],
    existing: Sequence[HistoryIpsEntity],
    *,
    instance_id: uuid.UUID,
    log_source_id: uuid.UUID,
    mapping: IpsFieldMapping,
    profile_version: int,
    bucket_seconds: int,
    floor: datetime,
    collected_at: datetime,
    max_entities_per_bucket: int,
    policy: AdmissionPolicy,
    active_days: int,
    maintenance: Callable[[datetime], bool],
    id_factory: Callable[[], uuid.UUID],
) -> IpsHistoryFold:
    """Replay one source's completed spans into the three aggregate table payloads."""
    ordered = sorted(buckets, key=lambda item: item.bucket_start)
    if list(buckets) != ordered:
        raise ValueError("IPS history buckets must be oldest first")
    if any(
        left.bucket_start >= right.bucket_start
        for left, right in pairwise(ordered)
    ):
        raise ValueError("IPS history bucket starts must increase")

    ledger = {entity.key: entity for entity in existing}
    changed: set[tuple[IpsDimension, str]] = set()
    metric_rows: list[dict[str, object]] = []
    bucket_rows: list[dict[str, object]] = []
    admitted_count = reactivated = retired = 0

    for item in ordered:
        start = item.bucket_start
        outcomes = item.outcomes
        totals_outcome = outcomes.get(IpsDimension.TOTALS)
        if totals_outcome is None or totals_outcome.state is not IpsCellState.COMPLETE:
            raise ValueError("an IPS history bucket needs complete TOTALS")
        totals = fold_outcome(
            totals_outcome, mapping=mapping, max_entities=max_entities_per_bucket
        )
        if not isinstance(totals, IpsTotalsFact):
            raise ValueError("an IPS history bucket needs a totals fact")

        measurements: dict[IpsDimension, str] = {}
        facts: dict[IpsDimension, object | None] = {}
        provenance: dict[str, object] = {
            "history": True,
            "floor": floor.isoformat(),
        }
        for dimension in IpsDimension:
            outcome = outcomes.get(dimension)
            if outcome is None:
                if dimension is not IpsDimension.TOTALS:
                    measurements[dimension] = IpsCellState.NOT_COLLECTED.value
                facts[dimension] = None
                continue
            if dimension is not IpsDimension.TOTALS:
                measurements[dimension] = (
                    IpsMeasurement.EXACT.value
                    if outcome.state is IpsCellState.COMPLETE
                    else outcome.state.value
                )
            facts[dimension] = fold_outcome(
                outcome, mapping=mapping, max_entities=max_entities_per_bucket
            )
            if outcome.provenance is not None:
                p = outcome.provenance
                provenance[dimension.value] = {
                    "search_window_start": p.search_window_start.isoformat(),
                    "search_window_end": p.search_window_end.isoformat(),
                    "search_bucket_count": p.search_bucket_count,
                    "search_source_count": p.search_source_count,
                    "split_depth": p.split_depth,
                    "bucket_key": p.bucket_key,
                }

        active_counts = {
            dimension: sum(
                entity.active and entity.dimension is dimension for entity in ledger.values()
            )
            for dimension in ENTITY_DIMENSIONS
        }
        admitted: dict[str, int] = {}
        below: dict[str, int] = {}
        rejected: dict[str, int] = {}
        untracked: dict[str, int] = {}
        untracked_events: dict[str, int] = {}
        admission_truncated: dict[str, bool] = {}

        for dimension in ENTITY_DIMENSIONS:
            fact = facts.get(dimension)
            if not isinstance(fact, IpsEntityBucketFact):
                continue
            tracked: list[tuple[IpsEntityFact, HistoryIpsEntity]] = []
            unseen: list[_Candidate] = []
            for entity_fact in fact.entities:
                current = ledger.get((dimension, entity_fact.value_key))
                if current is not None and current.active:
                    tracked.append((entity_fact, current))
                else:
                    unseen.append(_Candidate(entity_fact, dimension))
            decision = decide_admission(
                unseen, active_count=active_counts[dimension], policy=policy
            )
            overflow_count = max(0, fact.entity_count - len(fact.entities))
            overflow_events = max(
                0,
                fact.dimension_events
                - fact.unattributed_events
                - sum(entity.events for entity in fact.entities),
            )
            admitted[dimension.value] = len(decision.admitted)
            below[dimension.value] = len(decision.below_threshold)
            rejected[dimension.value] = len(decision.over_quota)
            untracked[dimension.value] = len(decision.untracked) + overflow_count
            untracked_events[dimension.value] = decision.untracked_requests + overflow_events
            admission_truncated[dimension.value] = (
                fact.measurement is IpsMeasurement.LOWER_BOUND
                or decision.truncated
                or overflow_count > 0
            )

            for entity_fact, current in tracked:
                updated = replace(
                    current,
                    display_name=entity_fact.display_name or current.display_name,
                    last_seen_at=max(current.last_seen_at, start),
                    normalizer_version=IPS_NORMALIZER_VERSION,
                )
                ledger[updated.key] = updated
                changed.add(updated.key)
                metric_rows.append(
                    _metric_values(updated.id, start, bucket_seconds, entity_fact.events)
                )
            for candidate in decision.admitted:
                entity_fact = candidate.fact
                key = (dimension, entity_fact.value_key)
                current = ledger.get(key)
                if current is None:
                    current = HistoryIpsEntity(
                        id=id_factory(),
                        instance_id=instance_id,
                        log_source_id=log_source_id,
                        dimension=dimension,
                        value_key=entity_fact.value_key,
                        display_name=entity_fact.display_name,
                        first_seen_at=start,
                        last_seen_at=start,
                        active=True,
                        retired_at=None,
                        normalizer_version=IPS_NORMALIZER_VERSION,
                    )
                    admitted_count += 1
                else:
                    current = replace(
                        current,
                        display_name=entity_fact.display_name or current.display_name,
                        last_seen_at=max(current.last_seen_at, start),
                        active=True,
                        retired_at=None,
                        normalizer_version=IPS_NORMALIZER_VERSION,
                    )
                    reactivated += 1
                ledger[key] = current
                changed.add(key)
                active_counts[dimension] += 1
                metric_rows.append(
                    _metric_values(current.id, start, bucket_seconds, entity_fact.events)
                )

        def coverage_values(
            dimension: IpsDimension,
            bucket_facts: Mapping[IpsDimension, object | None] = facts,
            bucket_totals: IpsTotalsFact = totals,
        ) -> tuple[float | None, bool | None]:
            fact = bucket_facts.get(dimension)
            measurable = (
                fact
                if isinstance(fact, (IpsEntityBucketFact, IpsSourceIpFact, IpsActionFact))
                else None
            )
            measured = coverage(measurable, bucket_totals)
            if measured is None:
                return None, None
            return min(1.0, max(0.0, measured.ratio)), measured.lower_bound

        source_fact = facts.get(IpsDimension.SOURCE_IP)
        fanout_fact = facts.get(IpsDimension.PORT_FANOUT)
        action_fact = facts.get(IpsDimension.ACTION)
        contributors: dict[str, object] = {}
        if isinstance(source_fact, IpsSourceIpFact):
            contributors[IpsDimension.SOURCE_IP.value] = [
                {"source_ip": address, "events": events}
                for address, events in source_fact.contributors
            ]
        if isinstance(fanout_fact, IpsFanoutFact):
            contributors[IpsDimension.PORT_FANOUT.value] = [
                {"source_ip": address, "distinct_ports": ports}
                for address, ports in fanout_fact.contributors
            ]
        row: dict[str, object] = {
            "log_source_id": log_source_id,
            "bucket_start": start,
            "bucket_seconds": bucket_seconds,
            **marker_values(HistoryFamily.IPS, HistoryOrigin.HISTORY),
            "total_events": totals.events,
            "total_records": totals.records,
            "port_fanout_measurement": measurements[IpsDimension.PORT_FANOUT],
            "contributors": contributors,
            "admitted": admitted,
            "below_admission_threshold": below,
            "rejected_over_quota": rejected,
            "untracked_entities": untracked,
            "untracked_events": untracked_events,
            "admission_truncated": admission_truncated,
            "profile_version": profile_version,
            "normalizer_version": IPS_NORMALIZER_VERSION,
            "query_provenance": provenance,
            "in_maintenance": maintenance(start),
            "collected_at": collected_at,
            "source_events": (
                source_fact.source_events if isinstance(source_fact, IpsSourceIpFact) else None
            ),
            "distinct_sources": (
                source_fact.distinct_sources
                if isinstance(source_fact, IpsSourceIpFact)
                else None
            ),
            "distinct_sources_lower_bound": (
                source_fact.measurement is IpsMeasurement.LOWER_BOUND
                if isinstance(source_fact, IpsSourceIpFact)
                else None
            ),
            "top1_events": (
                source_fact.top1_events if isinstance(source_fact, IpsSourceIpFact) else None
            ),
            "top5_events": (
                source_fact.top5_events if isinstance(source_fact, IpsSourceIpFact) else None
            ),
            "max_distinct_ports": (
                fanout_fact.max_distinct_ports if isinstance(fanout_fact, IpsFanoutFact) else None
            ),
            "blocked_events": (
                action_fact.blocked_events if isinstance(action_fact, IpsActionFact) else None
            ),
            "allowed_events": (
                action_fact.allowed_events if isinstance(action_fact, IpsActionFact) else None
            ),
            "unmapped_events": (
                action_fact.unmapped_events if isinstance(action_fact, IpsActionFact) else None
            ),
        }
        for dimension, prefix in (
            (IpsDimension.DESTINATION_PORT, "destination_port"),
            (IpsDimension.DESTINATION_IP, "destination_ip"),
            (IpsDimension.SIGNATURE, "signature"),
            (IpsDimension.SOURCE_IP, "source_ip"),
            (IpsDimension.ACTION, "action"),
        ):
            ratio, lower = coverage_values(dimension)
            row[f"{prefix}_measurement"] = measurements[dimension]
            row[f"{prefix}_coverage_ratio"] = ratio
            row[f"{prefix}_coverage_lower_bound"] = lower
        bucket_rows.append(row)

        cutoff = start - timedelta(days=active_days)
        for key, entity in tuple(ledger.items()):
            if entity.active and entity.last_seen_at < cutoff:
                ledger[key] = replace(entity, active=False, retired_at=start)
                changed.add(key)
                retired += 1

    return IpsHistoryFold(
        entities=tuple(_entity_values(ledger[key]) for key in sorted(changed)),
        buckets=tuple(bucket_rows),
        metrics=tuple(metric_rows),
        admitted=admitted_count,
        reactivated=reactivated,
        retired=retired,
    )
