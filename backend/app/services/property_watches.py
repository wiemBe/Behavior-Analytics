"""The property-watch API's logic: validation, snapshots, capacity, reads and the preview.

The routes (`app/api/routes/property_watches.py`) stay thin; everything that decides a 422, a
409 or a number lives here, where a unit test can reach it.

Three rules shape it:

* **Every reason at once.** `validate_draft` reports every problem of a draft in one list; the
  route turns it into the F.11 422 `{"message", "reasons"}`.
* **Values are never logged or audited.** `watch_snapshot` carries the count and a SHA-256
  digest of the sorted pins, never the pins (design §G.7.2). The preview's rows leave this
  module only in the response.
* **Reads are bulk.** A list of watches costs a fixed number of statements, whatever their
  number (AGENTS.md rule 9): scope names, the newest bucket of each, and the open incidents.

The group resolution here (`resolve_groups`) is the minimum the API needs: the capacity check,
the preview and the property picker. The collector's pure resolver (stale sync, fingerprint,
chunking, membership history) is PW-2b's, and PW-5b replaces this with it.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, null, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.dispatcher import AlertEnqueuer
from app.alerts.service import AlertResolution, AlertService
from app.core.config import Settings
from app.models.alert import Alert
from app.models.custom_rule import CustomRule
from app.models.enums import (
    ACTIVE_ANOMALY_STATES,
    AlertStatus,
    PwAnomalyType,
    PwBucketMeasurement,
    PwPinnedMeasurement,
    PwScopeKind,
)
from app.models.fleet import LogSourceGroup, LogSourceType
from app.models.log_source import LogSource
from app.models.property_watch import (
    PropertyWatch,
    PwAnomaly,
    PwBucket,
    PwValue,
    PwValueBaseline,
    PwValueMetric,
)
from app.property_watch.facts import (
    PwBucketFact,
    PwFactError,
    PwReadingKind,
    join_tuple_key,
    reading_for,
    split_tuple_key,
)
from app.property_watch.fold import fold_bucket
from app.property_watch.inputs import PW_VALUE_EVENTS_METRIC
from app.property_watch.keyed_search import (
    PwBucketOutcome,
    PwCellState,
    PwSearchBudget,
    PwSearchPlan,
    run_search,
)
from app.property_watch.reference import (
    PW_MAX_PROPERTIES,
    PwFieldReference,
    PwReferenceError,
    validate_pinned_value,
)
from app.providers.base import QRadarProvider
from app.providers.dto import PwSearchKind
from app.schemas.property_watch import (
    BaselineState,
    CoverageOut,
    DetectorStateOut,
    PreviewOut,
    PreviewQueryOut,
    PreviewRowOut,
    PropertyWatchOut,
    PwPropertyRef,
    ScopeSummaryOut,
    SeriesPointOut,
    ValueSeriesOut,
    WatchBucketOut,
    WatchValueOut,
)

#: Inclusive bounds of the `hours` query on the history reads and the default, as F.1's preview.
HOURS_MIN = 1
HOURS_MAX = 168
HOURS_DEFAULT = 24

#: The most values one series request reads (design §G.11).
SERIES_MAX_VALUES = 12

#: A preview is exactly this many searches: the totals and the grouped one (design §G.6).
PREVIEW_SEARCHES = 2
#: The most rows a preview returns. The fold already bounds them per bucket.
PREVIEW_ROW_LIMIT = 100
#: What "the last closed hour" is made of: whole buckets that add up to this many seconds.
PREVIEW_WINDOW_SECONDS = 3600

#: Hour-of-week cells in a full seasonal baseline.
CELLS_PER_VALUE = 168

_SCOPE_COLUMNS: dict[PwScopeKind, str] = {
    PwScopeKind.LOG_SOURCE: "log_source_id",
    PwScopeKind.LOG_SOURCE_TYPE: "scope_type_id",
    PwScopeKind.LOG_SOURCE_GROUP: "scope_group_id",
}

def default_detectors(property_count: int) -> list[PwAnomalyType]:
    """Every detector that applies: a new combination needs a tuple, so two properties."""
    return [
        d
        for d in PwAnomalyType
        if d is not PwAnomalyType.PW_NEW_COMBINATION or property_count == PW_MAX_PROPERTIES
    ]


class PreviewRefused(Exception):
    """The stored watch cannot be previewed as it stands; the message is the reason."""


# ------------------------------------------------------------------------ validation
@dataclass(frozen=True)
class WatchDraft:
    """The fields of a watch that validation reads. Plain data: raw until validated."""

    scope_kind: PwScopeKind
    log_source_id: uuid.UUID | None
    scope_type_id: int | None
    scope_group_id: int | None
    properties: Sequence[dict[str, str]]
    pinned_values: Sequence[Sequence[str]]
    detectors: Sequence[str]


def _scope_reasons(draft: WatchDraft) -> list[str]:
    reasons: list[str] = []
    wanted = _SCOPE_COLUMNS[draft.scope_kind]
    values: dict[str, object] = {
        "log_source_id": draft.log_source_id,
        "scope_type_id": draft.scope_type_id,
        "scope_group_id": draft.scope_group_id,
    }
    for column, value in values.items():
        if column == wanted and value is None:
            reasons.append(f"{column} is required for a {draft.scope_kind.value} scope")
        elif column != wanted and value is not None:
            reasons.append(f"{column} must be empty for a {draft.scope_kind.value} scope")
    for column in ("scope_type_id", "scope_group_id"):
        value = values[column]
        if isinstance(value, int) and value < 0:
            reasons.append(f"{column} must not be negative")
    return reasons


def _property_reasons(properties: Sequence[dict[str, str]]) -> list[str]:
    reasons: list[str] = []
    if not 1 <= len(properties) <= PW_MAX_PROPERTIES:
        reasons.append(f"a watch names one or two properties, not {len(properties)}")
    references: list[PwFieldReference] = []
    for index, item in enumerate(properties, start=1):
        try:
            references.append(PwFieldReference.from_dict(item))
        except PwReferenceError as exc:
            reasons.append(f"property {index}: {exc}")
    if len(set(references)) != len(references):
        reasons.append("a watch cannot name the same property twice")
    return reasons


def pin_reasons(
    pins: Sequence[Sequence[str]], property_count: int, max_pinned: int
) -> list[str]:
    """Every problem of the pinned list. No reason echoes a pinned value."""
    reasons: list[str] = []
    if len(pins) > max_pinned:
        reasons.append(f"a watch pins at most {max_pinned} values, not {len(pins)}")
    seen: set[tuple[str, ...]] = set()
    for index, entry in enumerate(pins, start=1):
        if 1 <= property_count <= PW_MAX_PROPERTIES and len(entry) != property_count:
            reasons.append(
                f"pinned value {index}: needs {property_count} string(s), one per property"
            )
        for part, component in enumerate(entry, start=1):
            try:
                validate_pinned_value(component)
            except PwReferenceError as exc:
                reasons.append(f"pinned value {index}, part {part}: {exc}")
        key = tuple(entry)
        if key in seen:
            reasons.append(f"pinned value {index}: pinned twice")
        seen.add(key)
    return reasons


def validate_draft(draft: WatchDraft, *, max_pinned: int) -> list[str]:
    """Every reason a watch cannot be saved as drafted, in a stable order."""
    reasons = _scope_reasons(draft)
    reasons.extend(_property_reasons(draft.properties))
    reasons.extend(pin_reasons(draft.pinned_values, len(draft.properties), max_pinned))
    if not draft.detectors:
        reasons.append("a watch evaluates at least one detector")
    if (
        PwAnomalyType.PW_NEW_COMBINATION.value in draft.detectors
        and len(draft.properties) != PW_MAX_PROPERTIES
    ):
        reasons.append("PW_NEW_COMBINATION needs a watch with two properties")
    return reasons


async def scope_existence_reasons(
    session: AsyncSession, draft: WatchDraft, instance_id: uuid.UUID
) -> list[str]:
    """The scope's subject is not in the synced inventory of the instance."""
    if draft.scope_kind is PwScopeKind.LOG_SOURCE and draft.log_source_id is not None:
        found = await session.scalar(
            select(LogSource.id).where(
                LogSource.id == draft.log_source_id, LogSource.instance_id == instance_id
            )
        )
        return [] if found else [f"log source {draft.log_source_id} does not exist"]
    if draft.scope_kind is PwScopeKind.LOG_SOURCE_TYPE and draft.scope_type_id is not None:
        found = await session.scalar(
            select(LogSourceType.type_id).where(
                LogSourceType.instance_id == instance_id,
                LogSourceType.type_id == draft.scope_type_id,
            )
        )
        return [] if found is not None else [
            f"log source type {draft.scope_type_id} is not in the synced inventory"
        ]
    if draft.scope_kind is PwScopeKind.LOG_SOURCE_GROUP and draft.scope_group_id is not None:
        found = await session.scalar(
            select(LogSourceGroup.qradar_id).where(
                LogSourceGroup.instance_id == instance_id,
                LogSourceGroup.qradar_id == draft.scope_group_id,
            )
        )
        return [] if found is not None else [
            f"log source group {draft.scope_group_id} is not in the synced inventory"
        ]
    return []


# --------------------------------------------------------------------------- snapshot
def pinned_digest(pins: Iterable[Sequence[str]]) -> str:
    """`sha256:<hex>` of the sorted pinned list: a change is visible, the values are not.

    The prefix keeps the audit redaction (which masks long token-shaped strings) from
    turning the digest into a mask.
    """
    ordered = sorted(list(entry) for entry in pins)
    payload = json.dumps(ordered, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def scope_label(watch: PropertyWatch) -> str:
    """`KIND:id`, one string. The colon keeps a UUID from being masked as a token."""
    kind = PwScopeKind(str(watch.scope_kind))
    if kind is PwScopeKind.LOG_SOURCE:
        return f"{kind.value}:{watch.log_source_id}"
    if kind is PwScopeKind.LOG_SOURCE_TYPE:
        return f"{kind.value}:{watch.scope_type_id}"
    return f"{kind.value}:{watch.scope_group_id}"


def watch_snapshot(watch: PropertyWatch) -> dict[str, Any]:
    """What an audit row holds of a watch: the definition, the pin count and the digest.

    Property names are configuration, not data. Pinned values are data: they never enter.
    """
    return {
        "name": watch.name,
        "description": watch.description,
        "scope": scope_label(watch),
        "properties": [dict(p) for p in watch.properties],
        "detectors": [str(d) for d in watch.detectors],
        "severity": str(watch.severity),
        "enabled": bool(watch.enabled),
        "watch_version": int(watch.watch_version),
        "pinned_count": len(watch.pinned_values),
        "pinned_digest": pinned_digest(watch.pinned_values),
    }


# --------------------------------------------------------------------------- groups
@dataclass(frozen=True)
class GroupResolution:
    """A group as the synced inventory describes it. Not the collector's resolver."""

    exists: bool
    synced_at: datetime | None = None
    #: Member `qradar_id`s: every source whose `group_ids` holds the group or a descendant.
    members: tuple[int, ...] = ()
    #: The groups walked, the group itself first.
    walked: tuple[int, ...] = ()
    #: The distinct log-source types of the members.
    type_ids: tuple[int, ...] = ()


def _children(groups: Iterable[LogSourceGroup]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = defaultdict(list)
    for group in groups:
        if group.parent_qradar_id is not None:
            out[group.parent_qradar_id].append(group.qradar_id)
    return out


def _descendants(root: int, children: dict[int, list[int]]) -> tuple[int, ...]:
    """The group and every group below it. Cycle-safe: the lab's own groups name each
    other as parent (design §G.4)."""
    visited: list[int] = []
    seen: set[int] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        visited.append(current)
        stack.extend(sorted(children.get(current, ()), reverse=True))
    return tuple(visited)


async def resolve_groups(
    session: AsyncSession, instance_id: uuid.UUID, group_ids: Iterable[int]
) -> dict[int, GroupResolution]:
    """Two statements for any number of groups: the instance's groups and its sources."""
    wanted = sorted(set(group_ids))
    if not wanted:
        return {}
    groups = list(
        (
            await session.scalars(
                select(LogSourceGroup).where(LogSourceGroup.instance_id == instance_id)
            )
        ).all()
    )
    by_id = {g.qradar_id: g for g in groups}
    children = _children(groups)
    sources = (
        await session.execute(
            select(LogSource.qradar_id, LogSource.type_id, LogSource.group_ids).where(
                LogSource.instance_id == instance_id, LogSource.group_ids.is_not(None)
            )
        )
    ).all()
    out: dict[int, GroupResolution] = {}
    for gid in wanted:
        group = by_id.get(gid)
        if group is None:
            out[gid] = GroupResolution(exists=False)
            continue
        walked = _descendants(gid, children)
        reach = set(walked)
        members: list[int] = []
        types: set[int] = set()
        for qradar_id, type_id, group_ids_value in sources:
            if not isinstance(group_ids_value, list):
                continue
            if reach.intersection(v for v in group_ids_value if isinstance(v, int)):
                members.append(qradar_id)
                if type_id is not None:
                    types.add(type_id)
        out[gid] = GroupResolution(
            exists=True,
            synced_at=group.synced_at,
            members=tuple(sorted(members)),
            walked=walked,
            type_ids=tuple(sorted(types)),
        )
    return out


def group_path(group_id: int, groups: dict[int, LogSourceGroup]) -> str:
    """The names from the root down, cycle-safe, `" / "`-joined."""
    names: list[str] = []
    seen: set[int] = set()
    current: int | None = group_id
    while current is not None and current in groups and current not in seen:
        seen.add(current)
        names.append(groups[current].name)
        current = groups[current].parent_qradar_id
    return " / ".join(reversed(names))


# ---------------------------------------------------------------------------- capacity
def search_cost(
    scope_kind: PwScopeKind, member_count: int, *, pinned: bool, max_sources: int
) -> int:
    """Searches per tick (design §G.14.1): `chunks x (2 or 3)`; a source or type is one chunk."""
    chunks = 1
    if scope_kind is PwScopeKind.LOG_SOURCE_GROUP:
        chunks = max(1, math.ceil(member_count / max_sources))
    return chunks * (3 if pinned else 2)


async def static_costs(
    session: AsyncSession, watches: Sequence[PropertyWatch], settings: Settings
) -> dict[uuid.UUID, tuple[int, int]]:
    """`{watch id: (searches per tick, resolved members)}`, group resolution done in bulk."""
    by_instance: dict[uuid.UUID, set[int]] = defaultdict(set)
    for watch in watches:
        if PwScopeKind(str(watch.scope_kind)) is PwScopeKind.LOG_SOURCE_GROUP:
            assert watch.scope_group_id is not None
            by_instance[watch.instance_id].add(watch.scope_group_id)
    resolved: dict[tuple[uuid.UUID, int], GroupResolution] = {}
    for instance_id, ids in by_instance.items():
        for gid, resolution in (await resolve_groups(session, instance_id, ids)).items():
            resolved[(instance_id, gid)] = resolution
    out: dict[uuid.UUID, tuple[int, int]] = {}
    for watch in watches:
        kind = PwScopeKind(str(watch.scope_kind))
        members = 1
        if kind is PwScopeKind.LOG_SOURCE_GROUP:
            assert watch.scope_group_id is not None
            members = len(resolved[(watch.instance_id, watch.scope_group_id)].members)
        out[watch.id] = (
            search_cost(
                kind,
                members,
                pinned=bool(watch.pinned_values),
                max_sources=settings.pw_max_sources_per_search,
            ),
            members,
        )
    return out


async def enabled_cost_total(session: AsyncSession, settings: Settings) -> tuple[int, int]:
    """`(enabled watches, their summed static cost)`."""
    enabled = list(
        (await session.scalars(select(PropertyWatch).where(PropertyWatch.enabled.is_(True)))).all()
    )
    costs = await static_costs(session, enabled, settings)
    return len(enabled), sum(cost for cost, _ in costs.values())


async def enable_reasons(
    session: AsyncSession, watch: PropertyWatch, settings: Settings
) -> list[str]:
    """Why this watch cannot be enabled as it stands: the caps of design §G.14.

    Called with the watch's *new* state applied (not flushed) under the enabled-cap lock.
    Every reason names its number, so the 409 tells the operator what to change.
    """
    others = list(
        (
            await session.scalars(
                select(PropertyWatch).where(
                    PropertyWatch.enabled.is_(True), PropertyWatch.id != watch.id
                )
            )
        ).all()
    )
    reasons: list[str] = []
    if len(others) + 1 > settings.pw_max_enabled_watches:
        reasons.append(
            f"at most {settings.pw_max_enabled_watches} property watches may be enabled at once"
        )
    costs = await static_costs(session, [*others, watch], settings)
    own_cost, members = costs[watch.id]
    kind = PwScopeKind(str(watch.scope_kind))
    cap = settings.pw_max_chunks_per_watch * settings.pw_max_sources_per_search
    if kind is PwScopeKind.LOG_SOURCE_GROUP and members > cap:
        reasons.append(
            f"the group resolves to {members} sources, above the limit of {cap} "
            f"({settings.pw_max_chunks_per_watch} searches of {settings.pw_max_sources_per_search}"
            "); split the group"
        )
    if own_cost > settings.pw_max_searches_per_watch_per_tick:
        reasons.append(
            f"this watch needs {own_cost} searches per tick, above the per-watch limit of "
            f"{settings.pw_max_searches_per_watch_per_tick}"
        )
    total = own_cost + sum(costs[o.id][0] for o in others)
    if total > settings.pw_max_searches_per_tick:
        reasons.append(
            f"the enabled watches would need {total} searches per tick against a budget of "
            f"{settings.pw_max_searches_per_tick} (this watch needs {own_cost})"
        )
    return reasons


# ----------------------------------------------------------------------------- reads
def _ratio(parsed: int | None, total: int | None) -> float | None:
    if parsed is None or not total:
        return None
    return parsed / total


async def _scope_summaries(
    session: AsyncSession, watches: Sequence[PropertyWatch]
) -> dict[uuid.UUID, ScopeSummaryOut]:
    source_ids = {w.log_source_id for w in watches if w.log_source_id is not None}
    types = {
        (w.instance_id, w.scope_type_id) for w in watches if w.scope_type_id is not None
    }
    group_instances = {w.instance_id for w in watches if w.scope_group_id is not None}
    sources: dict[uuid.UUID, tuple[int, str, str | None]] = {}
    if source_ids:
        rows = await session.execute(
            select(LogSource.id, LogSource.qradar_id, LogSource.name, LogSource.type_name).where(
                LogSource.id.in_(source_ids)
            )
        )
        sources = {r[0]: (r[1], r[2], r[3]) for r in rows.all()}
    type_names: dict[tuple[uuid.UUID, int], str] = {}
    if types:
        rows = await session.execute(
            select(LogSourceType.instance_id, LogSourceType.type_id, LogSourceType.name).where(
                tuple_(LogSourceType.instance_id, LogSourceType.type_id).in_(list(types))
            )
        )
        type_names = {(r[0], r[1]): r[2] for r in rows.all()}
    groups: dict[uuid.UUID, dict[int, LogSourceGroup]] = {}
    if group_instances:
        found = (
            await session.scalars(
                select(LogSourceGroup).where(LogSourceGroup.instance_id.in_(group_instances))
            )
        ).all()
        for group in found:
            groups.setdefault(group.instance_id, {})[group.qradar_id] = group
    out: dict[uuid.UUID, ScopeSummaryOut] = {}
    for watch in watches:
        kind = PwScopeKind(str(watch.scope_kind))
        if kind is PwScopeKind.LOG_SOURCE and watch.log_source_id is not None:
            qid, name, type_name = sources.get(watch.log_source_id, (None, None, None))
            out[watch.id] = ScopeSummaryOut(
                kind=kind, name=name, detail=type_name, qradar_id=qid
            )
        elif kind is PwScopeKind.LOG_SOURCE_TYPE and watch.scope_type_id is not None:
            out[watch.id] = ScopeSummaryOut(
                kind=kind,
                name=type_names.get((watch.instance_id, watch.scope_type_id)),
                qradar_id=watch.scope_type_id,
            )
        else:
            assert watch.scope_group_id is not None
            known = groups.get(watch.instance_id, {})
            found_group = known.get(watch.scope_group_id)
            out[watch.id] = ScopeSummaryOut(
                kind=kind,
                name=found_group.name if found_group else None,
                detail=group_path(watch.scope_group_id, known) if found_group else None,
                qradar_id=watch.scope_group_id,
            )
    return out


async def _latest_coverage(
    session: AsyncSession, watch_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, CoverageOut]:
    """The newest bucket of each watch's current version: one statement."""
    if not watch_ids:
        return {}
    rows = await session.execute(
        select(
            PwBucket.watch_id,
            PwBucket.bucket_start,
            PwBucket.total_events,
            PwBucket.parsed_events,
            PwBucket.values_measurement,
        )
        .join(
            PropertyWatch,
            and_(
                PropertyWatch.id == PwBucket.watch_id,
                PropertyWatch.watch_version == PwBucket.watch_version,
            ),
        )
        .where(PwBucket.watch_id.in_(watch_ids))
        .distinct(PwBucket.watch_id)
        .order_by(PwBucket.watch_id, PwBucket.bucket_start.desc())
    )
    return {
        r[0]: CoverageOut(
            bucket_start=r[1],
            total_events=r[2],
            parsed_events=r[3],
            ratio=_ratio(r[3], r[2]),
            measurement=str(r[4]),
        )
        for r in rows.all()
    }


async def _active_incidents(
    session: AsyncSession, watch_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, dict[str, int]]:
    if not watch_ids:
        return {}
    rows = await session.execute(
        select(PwAnomaly.watch_id, PwAnomaly.detector_type, func.count())
        .where(
            PwAnomaly.watch_id.in_(watch_ids),
            PwAnomaly.state.in_([s.value for s in ACTIVE_ANOMALY_STATES]),
        )
        .group_by(PwAnomaly.watch_id, PwAnomaly.detector_type)
    )
    out: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
    for watch_id, detector, count in rows.all():
        out[watch_id][str(detector)] = int(count)
    return out


async def watch_outs(
    session: AsyncSession,
    watches: Sequence[PropertyWatch],
    settings: Settings,
    *,
    alerting_mode: str,
    detail: bool = False,
) -> list[PropertyWatchOut]:
    """The watches as the API serves them: three bulk statements plus up to three for names."""
    ids = [w.id for w in watches]
    scopes = await _scope_summaries(session, watches)
    coverage = await _latest_coverage(session, ids)
    incidents = await _active_incidents(session, ids)
    rendered: list[PropertyWatchOut] = []
    for watch in watches:
        counts = incidents.get(watch.id, {})
        states = (
            [
                DetectorStateOut(
                    detector_type=PwAnomalyType(str(d)), active_incidents=counts.get(str(d), 0)
                )
                for d in watch.detectors
            ]
            if detail
            else None
        )
        rendered.append(
            PropertyWatchOut(
                id=watch.id,
                instance_id=watch.instance_id,
                name=watch.name,
                description=watch.description,
                scope_kind=PwScopeKind(str(watch.scope_kind)),
                log_source_id=watch.log_source_id,
                scope_type_id=watch.scope_type_id,
                scope_group_id=watch.scope_group_id,
                scope=scopes[watch.id],
                properties=[PwPropertyRef.model_validate(p) for p in watch.properties],
                pinned_values=[list(p) for p in watch.pinned_values],
                detectors=[PwAnomalyType(str(d)) for d in watch.detectors],
                severity=watch.severity,
                watch_version=int(watch.watch_version),
                enabled=bool(watch.enabled),
                status=watch.last_status,
                status_reason=watch.last_status_reason,
                collected_through=watch.collected_through,
                evaluated_through=watch.evaluated_through,
                last_run_at=watch.last_run_at,
                coverage=coverage.get(watch.id),
                open_incidents=sum(counts.values()),
                detector_states=states,
                alerting_mode="alerting" if alerting_mode == "alerting" else "shadow",
                created_at=watch.created_at,
                updated_at=watch.updated_at,
                created_by=watch.created_by,
                updated_by=watch.updated_by,
            )
        )
    return rendered


def bucket_out(row: PwBucket) -> WatchBucketOut:
    return WatchBucketOut(
        bucket_start=row.bucket_start,
        bucket_seconds=row.bucket_seconds,
        watch_version=row.watch_version,
        total_events=row.total_events,
        parsed_events=row.parsed_events,
        unparsed_events=row.unparsed_events,
        untracked_events=row.untracked_events,
        totals_complete=row.totals_complete,
        values_measurement=str(row.values_measurement),
        pinned_measurement=str(row.pinned_measurement),
        observed_values=row.observed_values,
        tracked_values_observed=row.tracked_values_observed,
        admitted_values=row.admitted_values,
        below_admission_threshold=row.below_admission_threshold,
        rejected_over_quota=row.rejected_over_quota,
        admission_truncated=row.admission_truncated,
        scope_member_count=row.scope_member_count,
        scope_changed=row.scope_changed,
        in_maintenance=row.in_maintenance,
        collected_at=row.collected_at,
    )


def _fact(row: Any) -> PwBucketFact | None:
    """A stored bucket as the pure fact, or `None` when the row contradicts itself.

    `None` reads as "no bucket" in `reading_for`: fail closed, never a zero.
    """
    try:
        return PwBucketFact(
            bucket_start=row.bucket_start,
            bucket_seconds=row.bucket_seconds,
            watch_version=row.watch_version,
            totals_complete=row.totals_complete,
            values_measurement=PwBucketMeasurement(str(row.values_measurement)),
            pinned_measurement=PwPinnedMeasurement(str(row.pinned_measurement)),
            total_events=row.total_events,
            parsed_events=row.parsed_events,
            untracked_events=row.untracked_events,
        )
    except (PwFactError, ValueError):
        return None


def _components(value: PwValue) -> list[str]:
    return list(split_tuple_key(value.value_key))


@dataclass
class _Cells:
    """A value's baseline: the cell of the reading's hour of week, and how many are reliable."""

    by_cell: dict[tuple[int, int], PwValueBaseline] = field(default_factory=dict)

    @property
    def reliable(self) -> int:
        return sum(1 for c in self.by_cell.values() if c.is_reliable)

    def at(self, when: datetime) -> tuple[BaselineState, float | None, float | None, float | None]:
        cell = self.by_cell.get((when.isoweekday(), when.hour))
        if cell is None:
            return "NONE", None, None, None
        if not cell.is_reliable:
            return "LEARNING", None, None, None
        return "RELIABLE", cell.p05, cell.median, cell.p95


async def _baseline_cells(
    session: AsyncSession, value_ids: Sequence[uuid.UUID], watch_version: int
) -> dict[uuid.UUID, _Cells]:
    out: dict[uuid.UUID, _Cells] = {v: _Cells() for v in value_ids}
    if not value_ids:
        return out
    rows = (
        await session.scalars(
            select(PwValueBaseline).where(
                PwValueBaseline.pw_value_id.in_(value_ids),
                PwValueBaseline.metric_name == PW_VALUE_EVENTS_METRIC,
                PwValueBaseline.watch_version == watch_version,
            )
        )
    ).all()
    for row in rows:
        out[row.pw_value_id].by_cell[(row.weekday, row.hour)] = row
    return out


def _tracked_at(value: PwValue, bucket_start: datetime) -> bool:
    if value.tracked_since is None or value.tracked_since > bucket_start:
        return False
    return value.tracked_until is None or bucket_start <= value.tracked_until


def _measurement_label(measurement: Any) -> str | None:
    return None if measurement is None else str(measurement.value)


VALUE_SORTS = ("events", "value", "first_seen")


async def list_values(
    session: AsyncSession,
    watch: PropertyWatch,
    *,
    hours: int,
    sort: str,
    limit: int,
    offset: int,
    now: datetime,
) -> tuple[list[WatchValueOut], int]:
    """Tracked values of the current version, pinned first, with their newest reading.

    Statements: the newest bucket, the page count, the page (joined to the metric of that
    bucket), the baseline cells of the page. Four, whatever the page holds.
    """
    since = now - timedelta(hours=hours)
    newest = (
        await session.execute(
            select(
                PwBucket.bucket_start,
                PwBucket.bucket_seconds,
                PwBucket.watch_version,
                PwBucket.totals_complete,
                PwBucket.values_measurement,
                PwBucket.pinned_measurement,
                PwBucket.total_events,
                PwBucket.parsed_events,
                PwBucket.untracked_events,
            )
            .where(
                PwBucket.watch_id == watch.id, PwBucket.watch_version == watch.watch_version
            )
            .order_by(PwBucket.bucket_start.desc())
            .limit(1)
        )
    ).first()
    bucket_start = newest.bucket_start if newest is not None else None
    fact = _fact(newest) if newest is not None else None
    conditions = [
        PwValue.watch_id == watch.id,
        PwValue.watch_version == watch.watch_version,
        # Listed while tracked, or if its tracking ended inside the window.
        PwValue.tracked_until.is_(None) | (PwValue.tracked_until >= since),
    ]
    total = int(
        await session.scalar(select(func.count()).select_from(PwValue).where(*conditions)) or 0
    )
    order: list[Any]
    if bucket_start is not None:
        metric = (
            select(PwValueMetric.pw_value_id, PwValueMetric.events)
            .where(PwValueMetric.bucket_start == bucket_start)
            .subquery()
        )
        query = select(PwValue, metric.c.events).outerjoin(
            metric, metric.c.pw_value_id == PwValue.id
        )
        by_events: Any = metric.c.events.desc().nulls_last()
    else:
        query = select(PwValue, null())
        by_events = PwValue.value_key.asc()
    if sort == "value":
        order = [PwValue.value_key.asc()]
    elif sort == "first_seen":
        order = [PwValue.first_seen_at.asc().nulls_last(), PwValue.value_key.asc()]
    else:
        order = [by_events, PwValue.value_key.asc()]
    page = (
        await session.execute(
            query.where(*conditions)
            .order_by(PwValue.pinned.desc(), *order, PwValue.id.asc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    cells = await _baseline_cells(session, [row[0].id for row in page], watch.watch_version)
    items: list[WatchValueOut] = []
    for row in page:
        value, events = row[0], row[1] if len(row) > 1 else None
        reading = reading_for(
            fact,
            pinned=bool(value.pinned),
            tracked_at_bucket=bucket_start is not None and _tracked_at(value, bucket_start),
            metric_events=None if events is None else int(events),
        )
        state: BaselineState = "NONE"
        low = med = high = None
        if bucket_start is not None and reading.kind in (
            PwReadingKind.COUNT,
            PwReadingKind.MEASURED_ZERO,
            PwReadingKind.UNKNOWN,
        ):
            state, low, med, high = cells[value.id].at(bucket_start)
        items.append(
            WatchValueOut(
                id=value.id,
                watch_version=value.watch_version,
                value_key=value.value_key,
                components=_components(value),
                truncated=bool(value.truncated),
                pinned=bool(value.pinned),
                active=bool(value.active),
                first_seen_at=value.first_seen_at,
                last_seen_at=value.last_seen_at,
                tracked_since=value.tracked_since,
                tracked_until=value.tracked_until,
                latest_bucket_start=bucket_start,
                reading=reading.kind.value,
                events=reading.events,
                measurement=_measurement_label(reading.measurement),
                baseline_state=state,
                expected_low=low,
                expected_median=med,
                expected_high=high,
                reliable_cells=cells[value.id].reliable,
            )
        )
    return items, total


async def value_series(
    session: AsyncSession,
    watch: PropertyWatch,
    value_ids: Sequence[uuid.UUID],
    *,
    hours: int,
    now: datetime,
) -> tuple[list[ValueSeriesOut], list[uuid.UUID]]:
    """Per-bucket readings of at most 12 values with their bands, in one bulk read.

    Returns the series and the ids that are not values of this watch's current version.
    Points exist only for buckets that were collected: a gap is an absent point, never a zero.
    Statements: the values, the buckets, the metric rows, the baseline cells.
    """
    since = now - timedelta(hours=hours)
    values = list(
        (
            await session.scalars(
                select(PwValue).where(
                    PwValue.id.in_(value_ids),
                    PwValue.watch_id == watch.id,
                    PwValue.watch_version == watch.watch_version,
                )
            )
        ).all()
    )
    found = {v.id for v in values}
    missing = [v for v in value_ids if v not in found]
    if not values:
        return [], missing
    buckets = (
        await session.execute(
            select(
                PwBucket.bucket_start,
                PwBucket.bucket_seconds,
                PwBucket.watch_version,
                PwBucket.totals_complete,
                PwBucket.values_measurement,
                PwBucket.pinned_measurement,
                PwBucket.total_events,
                PwBucket.parsed_events,
                PwBucket.untracked_events,
            )
            .where(
                PwBucket.watch_id == watch.id,
                PwBucket.watch_version == watch.watch_version,
                PwBucket.bucket_start >= since,
            )
            .order_by(PwBucket.bucket_start.asc())
        )
    ).all()
    metrics: dict[tuple[uuid.UUID, datetime], int] = {}
    if buckets:
        rows = await session.execute(
            select(PwValueMetric.pw_value_id, PwValueMetric.bucket_start, PwValueMetric.events)
            .where(
                PwValueMetric.pw_value_id.in_(found),
                PwValueMetric.bucket_start >= since,
            )
        )
        metrics = {(r[0], r[1]): int(r[2]) for r in rows.all()}
    cells = await _baseline_cells(session, list(found), watch.watch_version)
    ordered = sorted(values, key=lambda v: (not v.pinned, v.value_key))
    series: list[ValueSeriesOut] = []
    for value in ordered:
        points: list[SeriesPointOut] = []
        for bucket in buckets:
            reading = reading_for(
                _fact(bucket),
                pinned=bool(value.pinned),
                tracked_at_bucket=_tracked_at(value, bucket.bucket_start),
                metric_events=metrics.get((value.id, bucket.bucket_start)),
            )
            state, low, med, high = cells[value.id].at(bucket.bucket_start)
            points.append(
                SeriesPointOut(
                    bucket_start=bucket.bucket_start,
                    reading=reading.kind.value,
                    events=reading.events,
                    measurement=_measurement_label(reading.measurement),
                    baseline_state=state,
                    expected_low=low,
                    expected_median=med,
                    expected_high=high,
                )
            )
        series.append(
            ValueSeriesOut(
                value_id=value.id, value_key=value.value_key, pinned=bool(value.pinned),
                points=points,
            )
        )
    return series, missing


async def watch_buckets(
    session: AsyncSession, watch: PropertyWatch, *, hours: int, now: datetime
) -> list[WatchBucketOut]:
    rows = (
        await session.scalars(
            select(PwBucket)
            .where(
                PwBucket.watch_id == watch.id,
                PwBucket.bucket_start >= now - timedelta(hours=hours),
            )
            .order_by(PwBucket.bucket_start.asc())
        )
    ).all()
    return [bucket_out(r) for r in rows]


# --------------------------------------------------------------------------- preview
def preview_window(settings: Settings, now: datetime) -> tuple[datetime, datetime, int]:
    """The last hour of closed buckets: ends on a bucket edge, after the settle delay."""
    bucket = settings.collection_interval_seconds
    edge = int((now - timedelta(seconds=settings.search_cadence_settle_seconds)).timestamp())
    end = datetime.fromtimestamp(edge // bucket * bucket, UTC)
    count = max(1, PREVIEW_WINDOW_SECONDS // bucket)
    return end - timedelta(seconds=bucket * count), end, bucket


async def preview_scope_ids(
    session: AsyncSession, watch: PropertyWatch, settings: Settings, now: datetime
) -> tuple[int, ...]:
    """The ids the preview's searches name, or `PreviewRefused` with the reason."""
    kind = PwScopeKind(str(watch.scope_kind))
    if kind is PwScopeKind.LOG_SOURCE:
        qradar_id = await session.scalar(
            select(LogSource.qradar_id).where(LogSource.id == watch.log_source_id)
        )
        if qradar_id is None:
            raise PreviewRefused("the watch's log source is not in the synced inventory")
        return (int(qradar_id),)
    if kind is PwScopeKind.LOG_SOURCE_TYPE:
        assert watch.scope_type_id is not None
        return (int(watch.scope_type_id),)
    assert watch.scope_group_id is not None
    group = (await resolve_groups(session, watch.instance_id, [watch.scope_group_id]))[
        watch.scope_group_id
    ]
    if not group.exists:
        raise PreviewRefused("SCOPE_EMPTY: the group is not in the synced inventory")
    assert group.synced_at is not None
    if now - group.synced_at > timedelta(hours=settings.pw_scope_stale_after_hours):
        raise PreviewRefused(
            "SCOPE_STALE: the group was last synced more than "
            f"{settings.pw_scope_stale_after_hours} hours ago, so its members cannot be trusted"
        )
    if not group.members:
        raise PreviewRefused("SCOPE_EMPTY: the group resolves to no synced log source")
    if len(group.members) > settings.pw_max_sources_per_search:
        raise PreviewRefused(
            f"the group resolves to {len(group.members)} sources and a preview covers one "
            f"search chunk of at most {settings.pw_max_sources_per_search}; preview a smaller "
            "group, a type or a source"
        )
    return group.members


async def run_preview(
    watch: PropertyWatch,
    scope_ids: Sequence[int],
    settings: Settings,
    provider: QRadarProvider,
    *,
    now: datetime,
) -> PreviewOut:
    """One bounded run of the stored watch: the totals and the grouped search, nothing else.

    The budget is exactly two searches. A grouped search that is cut at the row limit would
    be split by the collector; here the split would be a third search, so the budget refuses
    it and the buckets are `NOT_COLLECTED`, which the response says. Nothing is stored.
    """
    start, end, bucket_seconds = preview_window(settings, now)
    scope_kind = PwScopeKind(str(watch.scope_kind))
    properties = tuple((p["kind"], p["name"]) for p in watch.properties)
    budget = PwSearchBudget(PREVIEW_SEARCHES)
    common: dict[str, Any] = {
        "max_rows": settings.pw_max_rows_per_search,
        "budget": budget,
        "max_sources": settings.pw_max_sources_per_search,
    }
    try:
        totals = await run_search(
            provider,
            PwSearchPlan(PwSearchKind.TOTALS, scope_kind, tuple(scope_ids)),
            (start, end),
            bucket_seconds,
            **common,
        )
        grouped = await run_search(
            provider,
            PwSearchPlan(PwSearchKind.GROUPED, scope_kind, tuple(scope_ids), properties),
            (start, end),
            bucket_seconds,
            **common,
        )
    except ValueError as exc:  # a reference or a window the validators refuse
        raise PreviewRefused(f"the stored watch cannot be searched: {exc}") from exc
    facts = [
        fold_bucket(
            bucket_start=g.bucket_start,
            bucket_seconds=bucket_seconds,
            watch_version=int(watch.watch_version),
            property_count=len(properties),
            grouped=g,
            totals=t,
            max_values=settings.pw_max_values_per_bucket,
            value_max_length=settings.pw_value_max_length,
        )
        for g, t in zip(grouped, totals, strict=True)
    ]
    return _preview_out(watch, facts, grouped, totals, budget.used, (start, end), bucket_seconds)


def _preview_reason(outcomes: Sequence[PwBucketOutcome], lower: int) -> str | None:
    states = {o.state for o in outcomes}
    if PwCellState.FAILED in states:
        return "a search failed, so its buckets were not measured"
    if PwCellState.NOT_COLLECTED in states:
        return (
            "the grouped search was cut at the row limit and a preview does not split it; "
            "narrow the scope or preview a type or a source"
        )
    if lower:
        return "a bucket was cut at the row limit: its counts are lower bounds"
    return None


def _preview_out(
    watch: PropertyWatch,
    facts: Sequence[PwBucketFact],
    grouped: Sequence[PwBucketOutcome],
    totals: Sequence[PwBucketOutcome],
    searches: int,
    window: tuple[datetime, datetime],
    bucket_seconds: int,
) -> PreviewOut:
    exact = [f for f in facts if f.values_measurement is PwBucketMeasurement.EXACT]
    lower = [f for f in facts if f.values_measurement is PwBucketMeasurement.LOWER_BOUND]
    unmeasured = [f for f in facts if f.values_measurement is PwBucketMeasurement.NOT_COLLECTED]
    all_totals = bool(facts) and all(f.total_events is not None for f in facts)
    all_values = bool(facts) and not unmeasured
    total = sum(f.total_events or 0 for f in facts) if all_totals else None
    parsed = sum(f.parsed_events or 0 for f in facts) if all_values else None
    unparsed = total - parsed if total is not None and parsed is not None else None
    any_parsed = any(f.parsed_events for f in facts)
    parses: bool | None = None
    if any_parsed:
        parses = True
    elif len(exact) == len(facts) and total is not None and total > 0 and parsed == 0:
        parses = False
    whole = (
        len(exact) == len(facts)
        and all(f.untracked_events == 0 for f in facts)
    )
    sums: dict[str, list[Any]] = {}
    for fact in facts:
        for value in fact.values:
            held = sums.setdefault(value.value_key, [0, value.components, False])
            held[0] += value.events
            held[2] = held[2] or value.truncated
    ranked = sorted(sums.items(), key=lambda item: (-item[1][0], item[0]))[:PREVIEW_ROW_LIMIT]
    if not exact and not lower:
        outcome = "NOT_MEASURED"
    elif lower or unmeasured or total is None:
        outcome = "PARTIAL"
    else:
        outcome = "MEASURED"
    queries: list[PreviewQueryOut] = []
    for search, outcomes in (("GROUPED", grouped), ("TOTALS", totals)):
        text = next(
            (o.provenance.query for o in outcomes if o.provenance and o.provenance.query), None
        )
        if text:
            queries.append(PreviewQueryOut(search=search, text=text))
    return PreviewOut(
        watch_id=watch.id,
        watch_version=int(watch.watch_version),
        window_start=window[0],
        window_end=window[1],
        bucket_seconds=bucket_seconds,
        searches_sent=searches,
        outcome=outcome,
        reason=_preview_reason([*grouped, *totals], len(lower)),
        buckets_measured=len(exact),
        buckets_lower_bound=len(lower),
        buckets_unmeasured=len(unmeasured),
        total_events=total,
        parsed_events=parsed,
        unparsed_events=unparsed,
        unparsed_bound=(
            None if unparsed is None else ("EXACT" if not lower else "UPPER_BOUND")
        ),
        coverage=_ratio(parsed, total),
        property_parses=parses,
        rows_measurement=(
            None if not exact and not lower else ("EXACT" if whole else "LOWER_BOUND")
        ),
        rows=[
            PreviewRowOut(
                value_key=key,
                components=list(held[1]),
                events=int(held[0]),
                share=_ratio_share(int(held[0]), parsed),
                truncated=bool(held[2]),
            )
            for key, held in ranked
        ],
        queries=queries,
    )


def _ratio_share(events: int, parsed: int | None) -> float | None:
    if not parsed:
        return None
    return events / parsed


def preview_audit_counts(preview: PreviewOut) -> dict[str, Any]:
    """The audit detail of a preview: counts and states only, never a value."""
    return {
        "watch_version": preview.watch_version,
        "searches": preview.searches_sent,
        "outcome": preview.outcome,
        "buckets_measured": preview.buckets_measured,
        "buckets_lower_bound": preview.buckets_lower_bound,
        "buckets_unmeasured": preview.buckets_unmeasured,
        "rows": len(preview.rows),
        "total_events": preview.total_events,
        "parsed_events": preview.parsed_events,
        "property_parses": preview.property_parses,
    }


# --------------------------------------------------------- F.1 rules that pin a value (PW-6)
@dataclass(frozen=True)
class DependentRule:
    """A custom rule that pins one of a watch's values (design §G.10.2)."""

    id: uuid.UUID
    name: str


async def dependent_rules(
    session: AsyncSession,
    watch_id: uuid.UUID,
    *,
    value_keys: Iterable[str] | None = None,
) -> list[DependentRule]:
    """The F.1 rules that pin a value of this watch, by name; one statement.

    `value_keys` narrows to the named values (an unpin); `None` is every value (a delete or a
    change of `watch_version`). A rule holds a `pw_value` row of some version, so the version
    is not part of the question.
    """
    statement = (
        select(CustomRule.id, CustomRule.name)
        .join(PwValue, PwValue.id == CustomRule.pw_value_id)
        .where(PwValue.watch_id == watch_id)
        .order_by(CustomRule.name.asc(), CustomRule.id.asc())
    )
    if value_keys is not None:
        keys = list(value_keys)
        if not keys:
            return []
        statement = statement.where(PwValue.value_key.in_(keys))
    rows = (await session.execute(statement)).all()
    return [DependentRule(id=row[0], name=row[1]) for row in rows]


def removed_pin_keys(
    current: Iterable[Sequence[str]], wanted: Iterable[Sequence[str]]
) -> list[str]:
    """The `value_key` of every pin in `current` that `wanted` no longer holds."""
    keep = {tuple(pin) for pin in wanted}
    return [join_tuple_key(tuple(pin)) for pin in current if tuple(pin) not in keep]


def dependency_refusal(rules: Sequence[DependentRule], action: str) -> dict[str, Any]:
    """The 409 body: what was refused, why, and the rules to detach first."""
    names = ", ".join(f"'{rule.name}'" for rule in rules)
    return {
        "message": (
            f"cannot {action}: custom rule(s) {names} pin a value of this watch; "
            "detach or delete the rule(s) first"
        ),
        "rules": [{"id": str(rule.id), "name": rule.name} for rule in rules],
    }


# --------------------------------------------------------- alerts of a deleted watch (PW-4)
ALERT_SOURCE_TYPE = "pw_value"
REASON_WATCH_DELETED = "the watch was deleted"


async def resolve_watch_alerts(
    session: AsyncSession,
    watch_id: uuid.UUID,
    *,
    actor: str,
    enqueuer: AlertEnqueuer | None = None,
    clock: Any = None,
) -> int:
    """Resolve every open alert of this watch before the watch row is deleted (design §G.15).

    The alerts are found through the watch id the engine put in their context, not through the
    incident rows, so an alert whose incident is gone is resolved too. The actor is the
    deleting principal. Two statements and one flush however many alerts there are; none
    open is a no-op and enqueues nothing. Returns the number resolved.
    """
    if not actor.strip():
        raise ValueError("actor is required")
    fingerprints = list(
        (
            await session.scalars(
                select(Alert.dedup_key).where(
                    Alert.source_type == ALERT_SOURCE_TYPE,
                    Alert.status != AlertStatus.RESOLVED,
                    Alert.context["watch_id"].astext == str(watch_id),
                )
            )
        ).all()
    )
    if not fingerprints:
        return 0
    _, resolved = await AlertService(session, clock=clock).apply_bulk(
        [],
        [
            AlertResolution(fingerprint=item, actor=actor, reason=REASON_WATCH_DELETED)
            for item in fingerprints
        ],
    )
    count = 0
    for result in resolved:
        if result is None or result.transition is None:
            continue
        count += 1
        if enqueuer is not None:
            await enqueuer.enqueue(result.alert, result.transition)
    return count
