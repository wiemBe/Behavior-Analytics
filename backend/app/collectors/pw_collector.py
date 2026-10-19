"""Property-watch collection with bounded admission and bulk persistence (design §G.4-§G.7).

One run plans every due watch of an instance together, under one `PwSearchBudget`. Per
watch it resolves the scope from the inventory read once for all watches, runs the grouped,
totals and (when values are pinned) pinned searches per scope chunk, folds each collected
bucket, applies admission and persists. Raw values live only inside the pure fold and
admission; what is written is counts, the bounded provenance and the value texts that
were admitted or pinned (AGENTS.md rule 3).

The watermark is `property_watch.collected_through`. It advances only past a contiguous
run of buckets that completed -- the grouped and totals searches (and the pinned search,
when values are pinned) all measured. The first bucket that did not complete holds it; when
a search failed, that bucket is stored `NOT_COLLECTED` so the gap is visible and a failed
search is never a zero. A bucket that was only not searched (the budget was spent) is
simply left for the next tick. **The first run seeds the watermark at the latest closed
bucket and replays nothing** (docs/SEARCH-CADENCE.md §SC.6).

The statements of a run are constant in the number of watches, values and buckets: the
reads are bulk (watches, groups, sources, values, scope history) and each table gets one
write (design §G.14.2, AGENTS.md rule 9). Evaluation and training are PW-3b's.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.anomaly.seasonal import in_maintenance
from app.collectors.metric_collector import floor_to_interval
from app.core.config import Settings, get_settings
from app.models.enums import (
    PwBucketMeasurement,
    PwPinnedMeasurement,
    PwScopeKind,
    PwWatchStatus,
    SearchFamily,
)
from app.models.instance import QRadarInstance
from app.models.property_watch import PropertyWatch, PwBucket, PwValue
from app.property_watch.admission import (
    AdmittedBucket,
    PwAdmissionPolicy,
    TrackedValue,
    admit_bucket,
    components_of,
    retire_other_versions,
    retire_stale,
    sync_pins,
)
from app.property_watch.facts import PwBucketFact
from app.property_watch.fold import component_limit, fold_bucket, pinned_value_keys
from app.property_watch.keyed_search import (
    PwBucketOutcome,
    PwCellState,
    PwSearchBudget,
    PwSearchPlan,
    combine_chunks,
    run_search,
)
from app.property_watch.reference import validate_pinned_values, validate_properties
from app.property_watch.scope import (
    ResolvedScope,
    ScopeRecord,
    SourceRow,
    resolve_scope,
    scope_changed_flags,
)
from app.providers.base import QRadarProvider
from app.providers.dto import PwSearchKind
from app.repositories.property_watch import PropertyWatchRepository
from app.services.search_cadence import SearchCadenceService

logger = logging.getLogger("app.collectors.pw_collector")

#: The statuses collection itself sets, and so the only ones it clears again.
_COLLECTION_STATUSES = frozenset(
    {
        PwWatchStatus.SCOPE_EMPTY.value,
        PwWatchStatus.SCOPE_STALE.value,
        PwWatchStatus.SCOPE_TOO_LARGE.value,
        PwWatchStatus.NOT_APPLICABLE.value,
        PwWatchStatus.STALE.value,
    }
)

#: `NOT_APPLICABLE` needs this many consecutive collected buckets on which the property
#: parsed on no event, counted across ticks, whatever the cadence.
_NOT_APPLICABLE_MIN_BUCKETS = 12
_NOT_APPLICABLE_REASON = (
    "the property parsed on no event in scope; the values are not measured"
)


@dataclass
class PwWatchReport:
    watch_id: uuid.UUID
    #: `collected`, `held`, `scope`, `nothing_to_collect` or `failed`.
    status: str
    buckets_written: int = 0
    searches_sent: int = 0
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "watch_id": str(self.watch_id),
            "status": self.status,
            "buckets_written": self.buckets_written,
            "searches_sent": self.searches_sent,
            "reason": self.reason,
        }


@dataclass
class PwCollectionReport:
    instance_id: uuid.UUID
    status: str = "ok"
    watches_enabled: int = 0
    watches_due: int = 0
    searches_sent: int = 0
    buckets_written: int = 0
    metric_rows_written: int = 0
    values_written: int = 0
    values_admitted: int = 0
    values_retired: int = 0
    watermarks_advanced: int = 0
    watches: list[PwWatchReport] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "watches_enabled": self.watches_enabled,
            "watches_due": self.watches_due,
            "searches_sent": self.searches_sent,
            "buckets_written": self.buckets_written,
            "metric_rows_written": self.metric_rows_written,
            "values_written": self.values_written,
            "values_admitted": self.values_admitted,
            "values_retired": self.values_retired,
            "watermarks_advanced": self.watermarks_advanced,
            "results": [item.as_dict() for item in self.watches],
        }


@dataclass
class _Plan:
    watch: PropertyWatch
    index: int
    window: tuple[datetime, datetime]
    scope: ResolvedScope | None = None


@dataclass
class _Staged:
    admitted: int = 0
    retired: int = 0


@dataclass
class _Writes:
    """The rows one tick writes, gathered across watches before any statement is sent."""

    values: list[dict[str, Any]] = field(default_factory=list)
    buckets: list[dict[str, Any]] = field(default_factory=list)
    metrics: list[dict[str, Any]] = field(default_factory=list)
    watches: list[dict[str, Any]] = field(default_factory=list)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _tracked(row: PwValue, property_count: int) -> TrackedValue:
    return TrackedValue(
        id=row.id,
        value_key=row.value_key,
        components=components_of(row.value_key, property_count),
        pinned=row.pinned,
        active=row.active,
        first_seen_at=_aware(row.first_seen_at),
        last_seen_at=_aware(row.last_seen_at),
        tracked_since=_aware(row.tracked_since),
        tracked_until=_aware(row.tracked_until),
        retired_at=_aware(row.retired_at),
        truncated=row.truncated,
    )


def _value_row(watch_id: uuid.UUID, version: int, value: TrackedValue) -> dict[str, Any]:
    components = value.components
    return {
        "id": value.id,
        "watch_id": watch_id,
        "watch_version": version,
        "value_key": value.value_key,
        "component_0": components[0] if components else None,
        "component_1": components[1] if len(components) > 1 else None,
        "truncated": value.truncated,
        "pinned": value.pinned,
        "first_seen_at": value.first_seen_at,
        "last_seen_at": value.last_seen_at,
        "tracked_since": value.tracked_since,
        "tracked_until": value.tracked_until,
        "active": value.active,
        "retired_at": value.retired_at,
    }


def _provenance(outcome: PwBucketOutcome, chunks: int) -> dict[str, object] | None:
    p = outcome.provenance
    if p is None:
        return None
    return {
        "state": outcome.state.value,
        "search_window_start": p.search_window_start.isoformat(),
        "search_window_end": p.search_window_end.isoformat(),
        "search_bucket_count": p.search_bucket_count,
        "split_depth": p.split_depth,
        "chunks": chunks,
        "query": p.query,
    }


def _scope_in_maintenance(scope: ResolvedScope, bucket_start: datetime) -> bool:
    """Source maintenance, or every resolved group member; never a type scope (§G.10.1)."""
    if scope.scope_kind is PwScopeKind.LOG_SOURCE_TYPE or not scope.members:
        return False
    return all(
        in_maintenance(
            bucket_start,
            maintenance_mode=source.maintenance_mode,
            maintenance_until=_aware(source.maintenance_until),
        )
        for source in scope.members
    )


def _bucket_row(
    watch_id: uuid.UUID,
    fact: PwBucketFact,
    *,
    provenance: dict[str, object],
    collected_at: datetime,
) -> dict[str, Any]:
    return {
        "watch_id": watch_id,
        "bucket_start": fact.bucket_start,
        "bucket_seconds": fact.bucket_seconds,
        "watch_version": fact.watch_version,
        "origin": "LIVE",
        "total_events": fact.total_events,
        "parsed_events": fact.parsed_events,
        "unparsed_events": fact.unparsed_events,
        "untracked_events": fact.untracked_events,
        "values_measurement": fact.values_measurement.value,
        "pinned_measurement": fact.pinned_measurement.value,
        "totals_complete": fact.totals_complete,
        "observed_values": fact.observed_values,
        "tracked_values_observed": fact.tracked_values_observed,
        "admitted_values": fact.admitted_values,
        "below_admission_threshold": fact.below_admission_threshold,
        "rejected_over_quota": fact.rejected_over_quota,
        "admission_truncated": fact.admission_truncated,
        "scope_member_count": fact.scope_member_count,
        "scope_fingerprint": fact.scope_fingerprint,
        "scope_changed": fact.scope_changed,
        "in_maintenance": fact.in_maintenance,
        "query_provenance": provenance,
        "collected_at": collected_at,
    }


def not_collected_fact(
    *, bucket_start: datetime, bucket_seconds: int, watch_version: int, pinned: bool
) -> PwBucketFact:
    """A bucket a search failed to measure: counts unknown, never zero."""
    return PwBucketFact(
        bucket_start=bucket_start,
        bucket_seconds=bucket_seconds,
        watch_version=watch_version,
        totals_complete=False,
        values_measurement=PwBucketMeasurement.NOT_COLLECTED,
        pinned_measurement=(
            PwPinnedMeasurement.NOT_COLLECTED if pinned else PwPinnedMeasurement.NOT_REQUESTED
        ),
    )


def collected_prefix(
    grouped: Sequence[PwBucketOutcome],
    totals: Sequence[PwBucketOutcome],
    pinned: Sequence[PwBucketOutcome] | None,
) -> int:
    """How many leading buckets completed: the watermark may advance past exactly these.

    A bucket completes when the grouped search measured it (whole, or cut at one bucket and
    stored as a lower bound), the totals search was whole, and the pinned search, when one
    was asked, measured it. Anything else is transient and holds the watermark.
    """
    count = 0
    for index, group in enumerate(grouped):
        total = totals[index]
        pin = pinned[index] if pinned is not None else None
        if not group.measured or total.state is not PwCellState.COMPLETE:
            break
        if pin is not None and not pin.measured:
            break
        count += 1
    return count


class PwCollector:
    def __init__(
        self,
        session: AsyncSession,
        provider: QRadarProvider,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.repo = PropertyWatchRepository(session)

    async def collect(self, instance: QRadarInstance) -> PwCollectionReport:
        report = PwCollectionReport(instance.id)
        if not self.settings.pw_collection_enabled:
            report.status = "disabled"
            return report
        now = self._clock()
        interval = self.settings.collection_interval_seconds
        step = timedelta(seconds=interval)
        watches = await self.repo.enabled_watches(instance.id)
        report.watches_enabled = len(watches)
        if not watches:
            return report
        # The latest closed bucket: its end plus the settle time is not after `now`.
        boundary = floor_to_interval(
            now - timedelta(seconds=self.settings.search_cadence_settle_seconds), interval
        )
        cadence = await SearchCadenceService(self.session, self.settings).get(
            SearchFamily.PROPERTY_WATCH
        )
        max_buckets = (
            max(1, cadence.cadence_seconds // interval)
            + self.settings.collection_max_backfill_intervals
        )
        plans: list[_Plan] = []
        for watch in watches:
            through = _aware(watch.collected_through)
            # The first run starts at the latest closed bucket: nothing before it is replayed.
            start = boundary - step if through is None else through + step
            end = min(boundary, start + step * max_buckets)
            if start < end:
                plans.append(_Plan(watch, len(plans), (start, end)))
            else:
                report.watches.append(PwWatchReport(watch.id, "nothing_to_collect"))
        report.watches_due = len(plans)
        if not plans:
            return report

        # ---- the bulk reads: constant in the number of watches and values
        whole_inventory = any(
            p.watch.scope_kind != PwScopeKind.LOG_SOURCE.value for p in plans
        )
        sources = await self.repo.sources(
            instance.id,
            source_ids={
                p.watch.log_source_id for p in plans if p.watch.log_source_id is not None
            },
            whole_inventory=whole_inventory,
        )
        groups = (
            await self.repo.groups(instance.id)
            if any(p.watch.scope_kind == PwScopeKind.LOG_SOURCE_GROUP.value for p in plans)
            else {}
        )
        value_rows = await self.repo.values(
            [(p.watch.id, p.watch.watch_version) for p in plans]
        )
        grace = self.settings.pw_scope_change_grace_buckets
        started = [_aware(p.watch.collected_through) for p in plans]
        known = [s for s in started if s is not None]
        history = await self.repo.scope_history(
            [p.watch.id for p in plans if p.watch.collected_through is not None],
            depth=grace + 1,
            since=(min(known) - step * (grace + 2)) if known else now,
        )

        unparsed_before = await self._unparsed_streaks(
            [p.watch.id for p in plans if p.watch.collected_through is not None],
            since=(
                (min(known) - step * (_NOT_APPLICABLE_MIN_BUCKETS + 2)) if known else now
            ),
        )

        budget = PwSearchBudget(
            self.settings.pw_max_searches_per_tick,
            max_per_watch=self.settings.pw_max_searches_per_watch_per_tick,
        )
        rows_by_watch: dict[uuid.UUID, list[PwValue]] = {}
        for row in value_rows:
            rows_by_watch.setdefault(row.watch_id, []).append(row)
        writes = _Writes()
        for plan in plans:
            watch_report = await self._collect_watch(
                plan,
                now=now,
                sources=sources,
                groups=groups,
                value_rows=rows_by_watch.get(plan.watch.id, []),
                history=history.get(plan.watch.id, []),
                budget=budget,
                writes=writes,
                report=report,
                unparsed_before=unparsed_before.get(plan.watch.id, 0),
            )
            report.watches.append(watch_report)
        report.searches_sent = budget.used
        await self._persist(writes, report)
        by_id = {p.watch.id: p.watch for p in plans}
        for update in writes.watches:
            # The loaded rows follow the bulk UPDATE, so the pipeline step after this one
            # reads the watermark and status this tick wrote.
            loaded = by_id[update["watch_id"]]
            for column in ("collected_through", "last_status", "last_status_reason", "last_run_at"):
                set_committed_value(loaded, column, update[column])
        return report

    # -------------------------------------------------------------- one watch
    async def _collect_watch(
        self,
        plan: _Plan,
        *,
        now: datetime,
        sources: Sequence[SourceRow],
        groups: dict[int, Any],
        value_rows: Sequence[PwValue],
        history: Sequence[ScopeRecord],
        budget: PwSearchBudget,
        writes: _Writes,
        report: PwCollectionReport,
        unparsed_before: int,
    ) -> PwWatchReport:
        watch = plan.watch
        settings = self.settings
        kind = PwScopeKind(watch.scope_kind)
        status_row = {
            "watch_id": watch.id,
            "collected_through": watch.collected_through,
            "last_status": watch.last_status,
            "last_status_reason": watch.last_status_reason,
            "last_run_at": now,
        }
        scope = resolve_scope(
            kind,
            log_source_id=watch.log_source_id,
            type_id=watch.scope_type_id,
            group_id=watch.scope_group_id,
            groups=groups,
            sources=sources,
            now=now,
            stale_after=timedelta(hours=settings.pw_scope_stale_after_hours),
            max_sources=settings.pw_max_sources_per_search,
            max_chunks=settings.pw_max_chunks_per_watch,
        )
        plan.scope = scope
        if not scope.resolved:
            # No bucket and no zero: the watch takes the reason and the next tick retries.
            status_row["last_status"] = scope.status.value if scope.status else None
            status_row["last_status_reason"] = scope.reason
            writes.watches.append(status_row)
            return PwWatchReport(watch.id, "scope", reason=scope.reason)

        try:
            references = validate_properties(watch.properties)
            pinned = validate_pinned_values(
                watch.pinned_values,
                property_count=len(references),
                max_pinned=settings.pw_max_pinned_values,
            )
            properties = tuple((r.kind.value, r.name) for r in references)
            used_before = budget.used
            grouped, totals, pinned_out = await self._search(
                plan, scope, properties, pinned, budget
            )
            searches_sent = budget.used - used_before
            count = collected_prefix(grouped, totals, pinned_out)
            staged = self._fold_and_stage(
                plan,
                scope,
                grouped=grouped,
                totals=totals,
                pinned_out=pinned_out,
                pinned=pinned,
                property_count=len(references),
                count=count,
                value_rows=value_rows,
                history=history,
                now=now,
                writes=writes,
                status_row=status_row,
                unparsed_before=unparsed_before,
            )
        except ValueError as exc:
            # A corrupt stored watch: the class only, never the text (it may hold a value).
            logger.warning(
                "property-watch collection refused a watch",
                extra={"watch_id": str(watch.id), "error_class": type(exc).__name__},
            )
            writes.watches.append(status_row)
            return PwWatchReport(watch.id, "failed", reason=type(exc).__name__)
        writes.watches.append(status_row)
        report.values_admitted += staged.admitted
        report.values_retired += staged.retired
        if count:
            report.watermarks_advanced += 1
        return PwWatchReport(
            watch.id,
            "collected" if count == len(grouped) else "held",
            buckets_written=count,
            searches_sent=searches_sent,
            reason=None if count == len(grouped) else "a bucket did not complete",
        )

    async def _search(
        self,
        plan: _Plan,
        scope: ResolvedScope,
        properties: tuple[tuple[str, str], ...],
        pinned: tuple[tuple[str, ...], ...],
        budget: PwSearchBudget,
    ) -> tuple[list[PwBucketOutcome], list[PwBucketOutcome], list[PwBucketOutcome] | None]:
        settings = self.settings
        kinds = [PwSearchKind.GROUPED, PwSearchKind.TOTALS]
        if pinned:
            kinds.append(PwSearchKind.PINNED)
        combined: dict[PwSearchKind, list[PwBucketOutcome]] = {}
        for search in kinds:
            chunks: list[list[PwBucketOutcome]] = []
            for ids in scope.chunks:
                chunks.append(
                    await run_search(
                        self.provider,
                        PwSearchPlan(
                            search=search,
                            scope_kind=scope.scope_kind,
                            scope_ids=ids,
                            properties=properties if search is not PwSearchKind.TOTALS else (),
                            pinned=pinned if search is PwSearchKind.PINNED else (),
                        ),
                        plan.window,
                        settings.collection_interval_seconds,
                        max_rows=settings.pw_max_rows_per_search,
                        budget=budget,
                        max_sources=settings.pw_max_sources_per_search,
                        watch=plan.index,
                    )
                )
            combined[search] = combine_chunks(chunks)
        return (
            combined[PwSearchKind.GROUPED],
            combined[PwSearchKind.TOTALS],
            combined.get(PwSearchKind.PINNED),
        )

    def _fold_and_stage(
        self,
        plan: _Plan,
        scope: ResolvedScope,
        *,
        grouped: Sequence[PwBucketOutcome],
        totals: Sequence[PwBucketOutcome],
        pinned_out: Sequence[PwBucketOutcome] | None,
        pinned: Sequence[Sequence[str]],
        property_count: int,
        count: int,
        value_rows: Sequence[PwValue],
        history: Sequence[ScopeRecord],
        now: datetime,
        writes: _Writes,
        status_row: dict[str, Any],
        unparsed_before: int,
    ) -> _Staged:
        watch = plan.watch
        settings = self.settings
        interval = settings.collection_interval_seconds
        chunks = len(scope.chunks)
        version = watch.watch_version
        has_pins = bool(pinned)

        if count < len(grouped):
            # The first bucket that did not complete. A failed search is written so the gap
            # is visible; one that was only not searched (the budget) is left for the next
            # tick. Either way the watermark holds here.
            held = count
            outcomes = [grouped[held], totals[held]] + (
                [pinned_out[held]] if pinned_out is not None else []
            )
            if any(o.state is PwCellState.FAILED for o in outcomes):
                fact = not_collected_fact(
                    bucket_start=grouped[held].bucket_start,
                    bucket_seconds=interval,
                    watch_version=version,
                    pinned=has_pins,
                )
                fact = replace(
                    fact,
                    scope_member_count=scope.member_count,
                    scope_fingerprint=scope.fingerprint,
                )
                writes.buckets.append(
                    _bucket_row(
                        watch.id,
                        fact,
                        provenance={
                            "grouped": _provenance(grouped[held], chunks),
                            "totals": _provenance(totals[held], chunks),
                            "pinned": (
                                _provenance(pinned_out[held], chunks)
                                if pinned_out is not None
                                else None
                            ),
                        },
                        collected_at=now,
                    )
                )
                if count == 0:
                    # Nothing newer than the last evaluated bucket exists: the watch must not
                    # keep reading OK. A later collected bucket clears this (STALE is one of
                    # collection's own statuses).
                    status_row["last_status"] = PwWatchStatus.STALE.value
                    status_row["last_status_reason"] = (
                        "the latest search failed; bucket "
                        f"{grouped[held].bucket_start.isoformat()} is stored NOT_COLLECTED"
                    )
        if count == 0:
            return _Staged()

        policy = PwAdmissionPolicy(
            min_events=settings.pw_min_admission_events,
            max_new_per_bucket=settings.pw_max_new_values_per_bucket,
            max_active=settings.pw_max_active_values_per_watch,
            active_days=settings.pw_value_active_days,
        )
        values: dict[str, TrackedValue] = {}
        other_versions: list[tuple[int, TrackedValue]] = []
        for row in value_rows:
            if row.watch_version == version:
                values[row.value_key] = _tracked(row, property_count)
            elif row.active:
                other_versions.append((row.watch_version, _tracked(row, property_count)))
        limit = component_limit(property_count, settings.pw_value_max_length)
        pins = pinned_value_keys(pinned, property_count, limit)
        first_bucket = grouped[0].bucket_start
        sync_pins(values, pins, first_bucket=first_bucket)
        retire_other_versions([v for _, v in other_versions], at=first_bucket)

        flags = scope_changed_flags(
            history, scope.fingerprint, count, settings.pw_scope_change_grace_buckets
        )
        facts: list[AdmittedBucket] = []
        collected_facts: list[PwBucketFact] = []
        for index in range(count):
            bucket_start = grouped[index].bucket_start
            fact = fold_bucket(
                bucket_start=bucket_start,
                bucket_seconds=interval,
                watch_version=version,
                property_count=property_count,
                grouped=grouped[index],
                totals=totals[index],
                pinned_outcome=pinned_out[index] if pinned_out is not None else None,
                pinned=pinned,
                tracked={key for key, value in values.items() if value.active},
                max_values=settings.pw_max_values_per_bucket,
                value_max_length=settings.pw_value_max_length,
            )
            fact = replace(
                fact,
                scope_member_count=scope.member_count,
                scope_fingerprint=scope.fingerprint,
                scope_changed=flags[index],
                in_maintenance=_scope_in_maintenance(scope, bucket_start),
            )
            admitted = admit_bucket(fact, values, policy, property_count=property_count)
            facts.append(admitted)
            collected_facts.append(admitted.fact)
            writes.buckets.append(
                _bucket_row(
                    watch.id,
                    admitted.fact,
                    provenance={
                        "grouped": _provenance(grouped[index], chunks),
                        "totals": _provenance(totals[index], chunks),
                        "pinned": (
                            _provenance(pinned_out[index], chunks)
                            if pinned_out is not None
                            else None
                        ),
                    },
                    collected_at=now,
                )
            )
        reference = grouped[count - 1].bucket_start
        retired = retire_stale(values, reference=reference, policy=policy)
        for admitted in facts:
            for key, events in admitted.metrics.items():
                writes.metrics.append(
                    {
                        "pw_value_id": values[key].id,
                        "bucket_start": admitted.fact.bucket_start,
                        "events": events,
                        "bucket_seconds": interval,
                    }
                )
        for value in values.values():
            if value.dirty:
                writes.values.append(_value_row(watch.id, version, value))
        for old_version, value in other_versions:
            if value.dirty:
                writes.values.append(_value_row(watch.id, old_version, value))
        status_row["collected_through"] = reference
        status = status_row["last_status"]
        trailing = 0
        for collected in reversed(collected_facts):
            if not collected.property_not_parsed:
                break
            trailing += 1
        if trailing == count:
            trailing += unparsed_before
        if trailing >= _NOT_APPLICABLE_MIN_BUCKETS:
            status_row["last_status"] = PwWatchStatus.NOT_APPLICABLE.value
            status_row["last_status_reason"] = _NOT_APPLICABLE_REASON
        elif status in _COLLECTION_STATUSES:
            status_row["last_status"] = None
            status_row["last_status_reason"] = None
        return _Staged(
            admitted=sum(f.fact.admitted_values for f in facts), retired=len(retired)
        )

    async def _unparsed_streaks(
        self, watch_ids: Sequence[uuid.UUID], *, since: datetime
    ) -> dict[uuid.UUID, int]:
        """Per watch, how many of its newest stored buckets, at or before the watermark, in a
        row hold events on which the property parsed on none (one statement for all watches).

        A bucket that measured nothing ends the run. Only the current version counts. The
        count is capped at what `NOT_APPLICABLE` needs, so the scan is bounded.
        """
        if not watch_ids:
            return {}
        depth = _NOT_APPLICABLE_MIN_BUCKETS - 1
        ranked = (
            select(
                PwBucket.watch_id.label("watch_id"),
                (
                    (PwBucket.values_measurement == PwBucketMeasurement.EXACT.value)
                    & (PwBucket.total_events > 0)
                    & (PwBucket.parsed_events == 0)
                ).label("unparsed"),
                func.row_number()
                .over(partition_by=PwBucket.watch_id, order_by=PwBucket.bucket_start.desc())
                .label("rank"),
            )
            .join(PropertyWatch, PropertyWatch.id == PwBucket.watch_id)
            .where(
                PwBucket.watch_id.in_(list(watch_ids)),
                PwBucket.watch_version == PropertyWatch.watch_version,
                PwBucket.bucket_start <= PropertyWatch.collected_through,
                PwBucket.bucket_start >= since,
            )
            .subquery()
        )
        rows = await self.session.execute(
            select(ranked.c.watch_id, ranked.c.unparsed)
            .where(ranked.c.rank <= depth)
            .order_by(ranked.c.watch_id, ranked.c.rank)
        )
        streaks: dict[uuid.UUID, int] = {}
        ended: set[uuid.UUID] = set()
        for watch_id, unparsed in rows.all():
            if watch_id in ended:
                continue
            if unparsed:
                streaks[watch_id] = streaks.get(watch_id, 0) + 1
            else:
                ended.add(watch_id)
        return streaks

    async def _persist(self, writes: _Writes, report: PwCollectionReport) -> None:
        # Values first: the metric rows reference them.
        await self.repo.write_values(writes.values)
        await self.repo.write_buckets(writes.buckets)
        await self.repo.write_metrics(writes.metrics)
        await self.repo.update_watches(writes.watches)
        report.values_written = len(writes.values)
        report.buckets_written = sum(
            1
            for row in writes.buckets
            if row["values_measurement"] != PwBucketMeasurement.NOT_COLLECTED.value
        )
        report.metric_rows_written = len(writes.metrics)
