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

The scope resolution here is PW-2b's pure resolver (`app/property_watch/scope.py`) over the one
bulk inventory read a tick uses: the capacity check, the preview, the pickers and `/scope` all
resolve through it, so the API, the enable caps and the collector can never disagree about a
scope. The only group walking left in this module is `group_path`, a display helper.
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
from itertools import pairwise
from typing import Any, Literal, cast

from fastapi import HTTPException
from sqlalchemy import and_, func, null, select, tuple_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.dispatcher import AlertEnqueuer
from app.alerts.service import AlertResolution, AlertService
from app.core.config import Settings
from app.i18n.messages import MessageRef
from app.i18n.refusal import CodedHTTPException
from app.models.alert import Alert
from app.models.custom_rule import CustomRule
from app.models.enums import (
    ACTIVE_ANOMALY_STATES,
    AlertStatus,
    PwAnomalyType,
    PwBucketMeasurement,
    PwBucketOrigin,
    PwHistoryState,
    PwPinnedMeasurement,
    PwScopeKind,
    SearchFamily,
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
from app.property_watch.aql import build_query, pinned_rows_per_bucket, validate_query_inputs
from app.property_watch.bootstrap import (
    UNREADABLE_PROGRESS,
    BootstrapStateError,
    history_budget_sentence,
    history_step_parts,
    history_step_searches,
    pw_planner_settings,
    read_progress,
)
from app.property_watch.estimate import estimate_history
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
    search_row_cap,
)
from app.property_watch.reference import (
    PW_MAX_PROPERTIES,
    PwFieldReference,
    PwReferenceError,
    validate_pinned_value,
)
from app.property_watch.scope import ResolvedScope, resolve_scope
from app.providers.base import QRadarProvider
from app.providers.dto import PwSearchKind
from app.repositories.property_watch import PropertyWatchRepository
from app.schemas.property_watch import (
    BaselineState,
    CoverageOut,
    DetectorStateOut,
    HistoryCatalogOut,
    HistoryEstimateOut,
    HistoryGapOut,
    PinRuleOut,
    PreviewOut,
    PreviewQueryOut,
    PreviewRowOut,
    PropertyWatchOut,
    PwPropertyRef,
    ScopeChangeOut,
    ScopeGroupOut,
    ScopeSummaryOut,
    SeriesPointOut,
    ValueSeriesOut,
    WatchAqlLastBucketOut,
    WatchAqlOut,
    WatchAqlQueryOut,
    WatchBucketOut,
    WatchHistoryOut,
    WatchScopeOut,
    WatchValueOut,
)
from app.schemas.search_cadence import CadenceOut
from app.services.ariel_permit import ariel_background_priority
from app.services.search_cadence import SearchCadenceService, bucket_seconds

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
        "history_days": watch.history_days,
        "pinned_count": len(watch.pinned_values),
        "pinned_digest": pinned_digest(watch.pinned_values),
    }


# --------------------------------------------------------------------------- scopes
#: The most member names a `/scope` read returns; the true count is reported beside them.
SCOPE_MEMBERS_LIMIT = 200
#: The newest stored buckets a `/scope` read walks back for membership changes.
SCOPE_HISTORY_BUCKETS = 500


@dataclass(frozen=True)
class ScopeSubject:
    """One scope to resolve: a watch's kind and the columns that name it."""

    instance_id: uuid.UUID
    scope_kind: PwScopeKind
    log_source_id: uuid.UUID | None = None
    scope_type_id: int | None = None
    scope_group_id: int | None = None

    @classmethod
    def of(cls, watch: PropertyWatch) -> ScopeSubject:
        return cls(
            instance_id=watch.instance_id,
            scope_kind=PwScopeKind(str(watch.scope_kind)),
            log_source_id=watch.log_source_id,
            scope_type_id=watch.scope_type_id,
            scope_group_id=watch.scope_group_id,
        )


async def resolve_subjects(
    session: AsyncSession,
    settings: Settings,
    subjects: Sequence[ScopeSubject],
    *,
    now: datetime,
) -> list[ResolvedScope]:
    """Resolve every subject with PW-2b's pure resolver over one bulk read per instance.

    The read the collector does: the instance's groups when a scope needs the walk, and its
    sources (the whole inventory for a type or group scope, the named ones for a source
    scope). One statement each, whatever the number of subjects (AGENTS.md rule 9).
    """
    if not subjects:
        return []
    repo = PropertyWatchRepository(session)
    by_instance: dict[uuid.UUID, list[int]] = defaultdict(list)
    for index, subject in enumerate(subjects):
        by_instance[subject.instance_id].append(index)
    resolved: list[ResolvedScope | None] = [None] * len(subjects)
    for instance_id, indexes in by_instance.items():
        whole = any(subjects[i].scope_kind is not PwScopeKind.LOG_SOURCE for i in indexes)
        source_ids: set[uuid.UUID] = set()
        for i in indexes:
            named = subjects[i].log_source_id
            if named is not None:
                source_ids.add(named)
        sources = await repo.sources(instance_id, source_ids=source_ids, whole_inventory=whole)
        groups = (
            await repo.groups(instance_id)
            if any(subjects[i].scope_kind is PwScopeKind.LOG_SOURCE_GROUP for i in indexes)
            else {}
        )
        for i in indexes:
            subject = subjects[i]
            resolved[i] = resolve_scope(
                subject.scope_kind,
                log_source_id=subject.log_source_id,
                type_id=subject.scope_type_id,
                group_id=subject.scope_group_id,
                groups=groups,
                sources=sources,
                now=now,
                stale_after=timedelta(hours=settings.pw_scope_stale_after_hours),
                max_sources=settings.pw_max_sources_per_search,
                max_chunks=settings.pw_max_chunks_per_watch,
            )
    return [scope for scope in resolved if scope is not None]


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
#: The stable codes of the capacity refusals (T-145). The browser branches on the code, never
#: on the message: a reworded reason must not change what the operator is offered.
CAP_ENABLED = "ENABLED_CAP"
CAP_GROUP_TOO_LARGE = "GROUP_TOO_LARGE"
CAP_WATCH_SEARCH_LIMIT = "WATCH_SEARCH_LIMIT"
CAP_TICK_BUDGET = "TICK_BUDGET"
CAP_HISTORY_BUDGET = "HISTORY_BUDGET"


@dataclass(frozen=True)
class CapacityReason:
    """One capacity refusal: the code the browser branches on and the sentence it shows.

    The code is stable; the message names the numbers, as every reason of design §G.14 does.
    """

    code: str
    message: str


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
    """`{watch id: (searches per tick, resolved members)}`, scope resolution done in bulk."""
    scopes = await resolve_subjects(
        session, settings, [ScopeSubject.of(watch) for watch in watches], now=datetime.now(UTC)
    )
    out: dict[uuid.UUID, tuple[int, int]] = {}
    for watch, scope in zip(watches, scopes, strict=True):
        members = scope.member_count or 0
        out[watch.id] = (
            search_cost(
                PwScopeKind(str(watch.scope_kind)),
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
) -> list[CapacityReason]:
    """Why this watch cannot be enabled as it stands: the caps of design §G.14.

    Called with the watch's *new* state applied (not flushed) under the enabled-cap lock.
    Every reason names its number, so the 409 tells the operator what to change, and carries
    the stable code the browser branches on (T-145).
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
    reasons: list[CapacityReason] = []
    if len(others) + 1 > settings.pw_max_enabled_watches:
        reasons.append(
            CapacityReason(
                CAP_ENABLED,
                f"at most {settings.pw_max_enabled_watches} property watches may be "
                "enabled at once",
            )
        )
    costs = await static_costs(session, [*others, watch], settings)
    own_cost, members = costs[watch.id]
    kind = PwScopeKind(str(watch.scope_kind))
    cap = settings.pw_max_chunks_per_watch * settings.pw_max_sources_per_search
    if kind is PwScopeKind.LOG_SOURCE_GROUP and members > cap:
        reasons.append(
            CapacityReason(
                CAP_GROUP_TOO_LARGE,
                f"the group resolves to {members} sources, above the limit of {cap} "
                f"({settings.pw_max_chunks_per_watch} searches of "
                f"{settings.pw_max_sources_per_search}); split the group",
            )
        )
    if own_cost > settings.pw_max_searches_per_watch_per_tick:
        reasons.append(
            CapacityReason(
                CAP_WATCH_SEARCH_LIMIT,
                f"this watch needs {own_cost} searches per tick, above the per-watch limit of "
                f"{settings.pw_max_searches_per_watch_per_tick}",
            )
        )
    total = own_cost + sum(costs[o.id][0] for o in others)
    if total > settings.pw_max_searches_per_tick:
        reasons.append(
            CapacityReason(
                CAP_TICK_BUDGET,
                f"the enabled watches would need {total} searches per tick against a budget of "
                f"{settings.pw_max_searches_per_tick} (this watch needs {own_cost})",
            )
        )
    reasons.extend(_history_budget_reasons(watch, total, settings))
    return reasons


def watch_reads_history(watch: PropertyWatch) -> bool:
    """A watch that asked for history and has not finished reading it (design §H.4.4)."""
    return (
        watch.history_days is not None
        and watch.history_days >= 1
        and PwHistoryState(str(watch.history_state))
        in (PwHistoryState.NOT_REQUESTED, PwHistoryState.READING)
    )


def _history_budget_reasons(
    watch: PropertyWatch, live_total: int, settings: Settings
) -> list[CapacityReason]:
    """The 409 for a full tick budget (design §H.9.1): a history step needs searches left.

    A step is TOTALS, one GROUPED and, with pins, PINNED: 3 searches with pins, 2 without.
    The enabled watches' static live cost goes first (§H.4.4), so what is left after it must
    cover the smallest step; the history sub-budget of the same section caps that again, so a
    reading gets `min(pw_bootstrap_searches_per_tick, free)` searches a tick. A watch that
    does not read history is not asked. The reason names the limit that binds.
    """
    if not settings.pw_bootstrap_enabled or not watch_reads_history(watch):
        return []
    needed = history_step_searches(bool(watch.pinned_values))
    free = max(0, settings.pw_max_searches_per_tick - live_total)
    setting = settings.pw_bootstrap_searches_per_tick
    if min(setting, free) >= needed:
        return []
    if setting <= free:
        # The setting binds: the history sub-budget of a tick cannot pay for one step.
        return [
            CapacityReason(
                CAP_HISTORY_BUDGET, history_budget_sentence(needed, setting=setting)
            )
        ]
    return [
        CapacityReason(
            CAP_HISTORY_BUDGET,
            f"reading history needs at least {needed} free searches per tick "
            f"({history_step_parts(needed)}), "
            f"but {free} are left after the enabled watches' live cost of {live_total} of "
            f"{settings.pw_max_searches_per_tick}",
        )
    ]


async def history_budget_reasons(
    session: AsyncSession, watch: PropertyWatch, settings: Settings
) -> list[CapacityReason]:
    """Only the history reason, for a request made on a watch that is already enabled.

    Called under the enabled-cap lock with the watch's new `history_days` applied.
    """
    if not settings.pw_bootstrap_enabled or not watch_reads_history(watch):
        return []
    others = list(
        (
            await session.scalars(
                select(PropertyWatch).where(
                    PropertyWatch.enabled.is_(True), PropertyWatch.id != watch.id
                )
            )
        ).all()
    )
    costs = await static_costs(session, [*others, watch], settings)
    total = costs[watch.id][0] + sum(costs[o.id][0] for o in others)
    return _history_budget_reasons(watch, total, settings)


# ---------------------------------------------------------------- history requests
REASON_HISTORY_SWITCHED_OFF = "history reading is switched off"
REASON_HISTORY_STARTS = "the reading starts at the next collection"


def history_days_reasons(days: int, settings: Settings) -> list[str]:
    """422 reasons for a `history_days` value: `0`, or `pw_new_value_history_days` to
    `pw_bootstrap_days` inclusive (design §H.6 row 13)."""
    low, high = settings.pw_new_value_history_days, settings.pw_bootstrap_days
    if days == 0 or low <= days <= high:
        return []
    return [f"history_days must be 0 (no history) or between {low} and {high}, not {days}"]


def pinned_rows(watch_like: PropertyWatch | WatchDraft, settings: Settings) -> int | None:
    """The most rows one bucket of the pinned search can return; 0 without pins.

    `None` when the stored watch does not validate (a read must then say nothing, not guess).
    The scope id is irrelevant to the count, so a placeholder stands in for it.
    """
    if not watch_like.pinned_values:
        return 0
    properties = tuple((str(p["kind"]), str(p["name"])) for p in watch_like.properties)
    try:
        spec = validate_query_inputs(
            PwSearchKind.PINNED,
            scope_kind=PwScopeKind(str(watch_like.scope_kind)),
            scope_ids=(0,),
            properties=properties,
            pinned=watch_like.pinned_values,
            max_sources=settings.pw_max_sources_per_search,
        )
    except ValueError:
        return None
    return pinned_rows_per_bucket(spec)


async def pinned_rows_reasons(
    session: AsyncSession,
    watch_like: PropertyWatch | WatchDraft,
    settings: Settings,
    *,
    cadence: CadenceOut | None = None,
) -> list[str]:
    """422 when the pinned search could return more rows per bucket than a search may carry.

    Such a watch would end `PARTIAL` with every span a gap (PW-13b open point 5): refused
    here, with both numbers, before it asks for history.
    """
    rows = pinned_rows(watch_like, settings)
    if rows is None:
        return []
    if cadence is None:
        cadence = await property_watch_cadence(session, settings)
    target = pw_planner_settings(
        settings, cadence_seconds=cadence.cadence_seconds
    ).row_target_rows
    if rows <= target:
        return []
    return [
        f"the pinned values can return up to {rows} rows per bucket, above the {target} rows "
        "one history search may carry; unpin values or request no history"
    ]


async def history_change_reasons(session: AsyncSession, watch: PropertyWatch) -> list[str]:
    """409 reasons against changing `history_days` (design §H.5.5): empty when it may change.

    Only a watch that never read, holds no bucket of its current version and has no watermark
    can ask. Anything else would prepend history to values and incidents already in use.
    """
    advice = "create a new watch to read history"
    state = PwHistoryState(str(watch.history_state))
    if state is not PwHistoryState.NOT_REQUESTED:
        return [f"history is {state.value} on this watch and cannot be requested again; {advice}"]
    marks = [
        name
        for name in ("collected_through", "evaluated_through")
        if getattr(watch, name) is not None
    ]
    if marks:
        return [f"this watch has already collected ({' and '.join(marks)} set); {advice}"]
    stored = int(
        await session.scalar(
            select(func.count())
            .select_from(PwBucket)
            .where(PwBucket.watch_id == watch.id, PwBucket.watch_version == watch.watch_version)
        )
        or 0
    )
    if stored:
        return [
            f"this watch holds {stored} stored bucket(s) of its current version; {advice}"
        ]
    return []


# ----------------------------------------------------------------------------- reads
def _ratio(parsed: int | None, total: int | None) -> float | None:
    if parsed is None or not total:
        return None
    return parsed / total


async def _scope_summaries(
    session: AsyncSession,
    watches: Sequence[PropertyWatch],
    settings: Settings,
    *,
    with_members: bool,
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
    # The resolved member count comes from the same resolver as `/scope` and the collector,
    # but only a detail read pays for the extra bulk read (the list stays its fixed cost).
    members: dict[uuid.UUID, int | None] = {}
    if with_members and watches:
        scopes = await resolve_subjects(
            session,
            settings,
            [ScopeSubject.of(watch) for watch in watches],
            now=datetime.now(UTC),
        )
        members = {w.id: s.member_count for w, s in zip(watches, scopes, strict=True)}
    out: dict[uuid.UUID, ScopeSummaryOut] = {}
    for watch in watches:
        kind = PwScopeKind(str(watch.scope_kind))
        member_count = members.get(watch.id)
        if kind is PwScopeKind.LOG_SOURCE and watch.log_source_id is not None:
            qid, name, type_name = sources.get(watch.log_source_id, (None, None, None))
            out[watch.id] = ScopeSummaryOut(
                kind=kind, name=name, detail=type_name, qradar_id=qid, member_count=member_count
            )
        elif kind is PwScopeKind.LOG_SOURCE_TYPE and watch.scope_type_id is not None:
            out[watch.id] = ScopeSummaryOut(
                kind=kind,
                name=type_names.get((watch.instance_id, watch.scope_type_id)),
                qradar_id=watch.scope_type_id,
                member_count=member_count,
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
                member_count=member_count,
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
        .ext(distinct_on(PwBucket.watch_id))
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


def _as_utc(value: datetime) -> datetime:
    """A stored timestamp as UTC, whether the driver returned it naive or aware."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _walked_groups(
    session: AsyncSession, instance_id: uuid.UUID, group_ids: Sequence[int]
) -> tuple[list[ScopeGroupOut], datetime | None]:
    """The walked groups' ids and names, and the oldest of their `synced_at`: one statement."""
    if not group_ids:
        return [], None
    rows = (
        await session.execute(
            select(LogSourceGroup.qradar_id, LogSourceGroup.name, LogSourceGroup.synced_at)
            .where(
                LogSourceGroup.instance_id == instance_id,
                LogSourceGroup.qradar_id.in_(list(group_ids)),
            )
            .order_by(LogSourceGroup.qradar_id)
        )
    ).all()
    groups = [ScopeGroupOut(qradar_id=qradar_id, name=name) for qradar_id, name, _ in rows]
    oldest = min((synced_at for _, _, synced_at in rows), default=None)
    return groups, oldest


def _member_predicate(subject: ScopeSubject, scope: ResolvedScope) -> Any:
    """The one predicate that names a scope's members, matching the resolver's membership."""
    if subject.scope_kind is PwScopeKind.LOG_SOURCE:
        return LogSource.id == subject.log_source_id
    if subject.scope_kind is PwScopeKind.LOG_SOURCE_TYPE:
        return LogSource.type_id == subject.scope_type_id
    return LogSource.id.in_([member.id for member in scope.members])


async def _member_names(
    session: AsyncSession, subject: ScopeSubject, scope: ResolvedScope
) -> list[str]:
    """Up to `SCOPE_MEMBERS_LIMIT` member names, sorted: one statement, bounded.

    For a group the members come from the resolver's own walk (its `SourceRow` set), so the
    names cannot disagree with the count; for a source or a type the inventory is filtered by
    the same column the resolver counts. A group the walk found no member for filters to an
    empty `IN` list, so the read still costs its one statement at every scope size.
    """
    query = (
        select(LogSource.name)
        .where(LogSource.instance_id == subject.instance_id, _member_predicate(subject, scope))
        .order_by(LogSource.name, LogSource.id)
        .limit(SCOPE_MEMBERS_LIMIT)
    )
    return list((await session.scalars(query)).all())


async def _scope_changes(
    session: AsyncSession, watch: PropertyWatch
) -> list[ScopeChangeOut]:
    """The stored membership changes of the current version, newest first: one statement.

    The newest `SCOPE_HISTORY_BUCKETS` collected buckets are walked; a bucket that measured
    nothing says nothing about the scope and is left out, as the collector's history read
    does. A change is a stored bucket whose `scope_fingerprint` differs from the one before
    it; its `scope_changed` flag says whether it is in the grace window.
    """
    rows = (
        await session.execute(
            select(
                PwBucket.bucket_start,
                PwBucket.scope_member_count,
                PwBucket.scope_fingerprint,
                PwBucket.scope_changed,
            )
            .where(
                PwBucket.watch_id == watch.id,
                PwBucket.watch_version == watch.watch_version,
                PwBucket.values_measurement != PwBucketMeasurement.NOT_COLLECTED.value,
            )
            .order_by(PwBucket.bucket_start.desc())
            .limit(SCOPE_HISTORY_BUCKETS)
        )
    ).all()
    ordered = list(reversed(rows))
    changes: list[ScopeChangeOut] = []
    for previous, current in pairwise(ordered):
        if current[2] == previous[2]:
            continue
        changes.append(
            ScopeChangeOut(
                bucket_start=current[0],
                member_count_before=previous[1],
                member_count_after=current[1],
                in_grace_window=bool(current[3]),
            )
        )
    changes.reverse()
    return changes


async def watch_scope(
    session: AsyncSession,
    watch: PropertyWatch,
    settings: Settings,
    *,
    now: datetime,
) -> WatchScopeOut:
    """What the watch's scope resolves to, and how its membership changed (design §G.4).

    Statements: the resolver's one read per group/source set, the walked groups' names, the
    member names, and the stored changes. Each is one statement whatever the number of groups
    or sources (AGENTS.md rule 9). A scope that does not resolve still returns what the
    inventory holds, marked with its state and reason, never a zero.
    """
    subject = ScopeSubject.of(watch)
    scope = (await resolve_subjects(session, settings, [subject], now=now))[0]
    groups, synced_at = await _walked_groups(session, watch.instance_id, scope.groups_walked)
    names = await _member_names(session, subject, scope)
    changes = await _scope_changes(session, watch)
    age = None if synced_at is None else int((now - _as_utc(synced_at)).total_seconds())
    return WatchScopeOut(
        watch_id=watch.id,
        watch_version=int(watch.watch_version),
        kind=scope.scope_kind,
        resolved=scope.resolved,
        state=scope.status,
        reason=scope.reason,
        member_count=scope.member_count,
        members=names,
        members_total=scope.member_count,
        groups_walked=groups,
        inventory_synced_at=synced_at,
        inventory_age_seconds=age,
        changes=changes,
    )


async def property_watch_cadence(session: AsyncSession, settings: Settings) -> CadenceOut:
    """The family timing, read once per request, as the collector reads it."""
    family = SearchFamily.PROPERTY_WATCH
    row = await SearchCadenceService(session, settings).get(family)
    return CadenceOut(
        family=family,
        cadence_seconds=int(row.cadence_seconds),
        bucket_seconds=bucket_seconds(family, settings),
        settle_seconds=settings.search_cadence_settle_seconds,
    )


def days_read(
    floor: datetime | None, through: datetime | None, bucket_seconds: int
) -> float | None:
    """Whole buckets from `floor` through `through` (the last bucket written), in days.

    One decimal. None when either end is missing or `through` precedes `floor`: a
    measurement that is not there is not zero. Never read from `history_end`, and never an
    ETA. The same rule as the platform families' `days_read`.
    """
    if floor is None or through is None:
        return None
    floor_at, through_at = _as_utc(floor), _as_utc(through)
    if through_at < floor_at:
        return None
    buckets = int((through_at - floor_at).total_seconds() // bucket_seconds) + 1
    return round(buckets * bucket_seconds / 86400, 1)


def watch_history(
    watch: PropertyWatch,
    settings: Settings,
    *,
    estimate: HistoryEstimateOut | None = None,
) -> WatchHistoryOut:
    """The history block of a watch, from its stored columns and `history_progress`.

    No statement: the columns are on the row. A progress that does not read back answers
    `None` counters with `progress_unreadable`, never a zero (AGENTS.md rule 8). The reason of
    a switched-off reading is not stored: `PARTIAL` with `{}` and no target is the only state
    `begin_reading` writes with the switch off, so it is derived here.
    """
    state = PwHistoryState(str(watch.history_state))
    searches: int | None = None
    buckets_read: int | None = None
    gaps: list[HistoryGapOut] | None = None
    reason: str | None = None
    unreadable = False
    try:
        stored = read_progress(cast(Any, watch))
    except BootstrapStateError:
        stored = None
        unreadable = True
        reason = UNREADABLE_PROGRESS
    if unreadable:
        pass
    elif stored is None:
        if state is PwHistoryState.PARTIAL and watch.history_target_start is None:
            reason = REASON_HISTORY_SWITCHED_OFF
        elif state is PwHistoryState.READING:
            reason = REASON_HISTORY_STARTS
    else:
        progress, reason = stored
        searches = progress.searches
        buckets_read = progress.buckets_read
        gaps = [HistoryGapOut(start=g.start, end=g.end, reason=g.reason) for g in progress.gaps]
    return WatchHistoryOut(
        state=state.value,
        days_requested=watch.history_days,
        target_start=watch.history_target_start,
        floor=watch.history_floor,
        through=watch.history_through,
        end=watch.history_end,
        days_read=(
            None
            if unreadable
            else days_read(
                watch.history_floor, watch.history_through, settings.collection_interval_seconds
            )
        ),
        searches=searches,
        buckets_read=buckets_read,
        gaps=gaps,
        reason=reason,
        progress_unreadable=unreadable,
        started_at=watch.history_started_at,
        finished_at=watch.history_finished_at,
        estimate=estimate,
    )


def history_estimate(
    watch: PropertyWatch, settings: Settings, *, cadence_seconds: int
) -> HistoryEstimateOut | None:
    """The planner's own cost of the request, for a watch that asked and has not finished.

    `None` otherwise, and when the figure cannot be given (the stored pins do not validate,
    or the reading would not hand off): no estimate is better than a guess.
    """
    days = watch.history_days
    if days is None or not watch_reads_history(watch):
        return None
    rows = pinned_rows(watch, settings)
    if rows is None:
        return None
    found = estimate_history(
        days=days,
        pinned_rows_per_bucket=rows,
        settings=settings,
        cadence_seconds=cadence_seconds,
    )
    if found is None:
        return None
    return HistoryEstimateOut(
        searches_min=found.searches_min,
        searches_max=found.searches_max,
        ticks_min=found.ticks_min,
        ticks_max=found.ticks_max,
        cadence_seconds=found.cadence_seconds,
        assumptions=found.assumptions,
    )


async def reading_count(session: AsyncSession) -> int:
    """Watches that are reading history right now: one statement."""
    return int(
        await session.scalar(
            select(func.count())
            .select_from(PropertyWatch)
            .where(PropertyWatch.history_state == PwHistoryState.READING.value)
        )
        or 0
    )


def history_catalog(
    settings: Settings, *, reading: int, searches_free: int
) -> HistoryCatalogOut:
    """The history bounds; `searches_free` is `search_budget - budget_used`."""
    return HistoryCatalogOut(
        enabled=settings.pw_bootstrap_enabled,
        days_default=settings.pw_bootstrap_days,
        days_min=settings.pw_new_value_history_days,
        days_max=settings.pw_bootstrap_days,
        searches_per_tick=settings.pw_bootstrap_searches_per_tick,
        seconds_per_tick=settings.pw_bootstrap_seconds_per_tick,
        reading_count=reading,
        searches_free=searches_free,
    )


async def watch_outs(
    session: AsyncSession,
    watches: Sequence[PropertyWatch],
    settings: Settings,
    *,
    alerting_mode: str,
    detail: bool = False,
    cadence: CadenceOut | None = None,
) -> list[PropertyWatchOut]:
    """The watches as the API serves them: three bulk statements plus up to three for names.

    A detail read additionally resolves each scope's member count, through the same resolver
    as `/scope` (one more bulk read per instance). A caller that already needed the cadence
    for validation passes it in, keeping one cadence-table read for the whole request.
    """
    ids = [w.id for w in watches]
    if cadence is None:
        cadence = await property_watch_cadence(session, settings)
    pinned_by = await pinning_rules_by_watch(session, ids) if detail else {}
    scopes = await _scope_summaries(session, watches, settings, with_members=detail)
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
                cadence_seconds=cadence.cadence_seconds,
                history=watch_history(
                    watch,
                    settings,
                    estimate=(
                        history_estimate(
                            watch, settings, cadence_seconds=cadence.cadence_seconds
                        )
                        if detail
                        else None
                    ),
                ),
                cadence=cadence,
                threshold_rules=(
                    [
                        PinRuleOut(
                            id=r.id,
                            name=r.name,
                            pw_value_id=r.pw_value_id,
                            value_key=r.value_key,
                            components=r.components,
                            enabled=r.enabled,
                        )
                        for r in pinned_by.get(watch.id, [])
                    ]
                    if detail
                    else None
                ),
                created_at=watch.created_at,
                updated_at=watch.updated_at,
                created_by=watch.created_by,
                updated_by=watch.updated_by,
            )
        )
    return rendered


def origin_label(origin: object) -> Literal["LIVE", "HISTORY"]:
    """`HISTORY` for a bucket read from QRadar's past, `LIVE` for every other one."""
    return "HISTORY" if str(origin) == PwBucketOrigin.HISTORY.value else "LIVE"


def bucket_out(row: PwBucket) -> WatchBucketOut:
    return WatchBucketOut(
        bucket_start=row.bucket_start,
        bucket_seconds=row.bucket_seconds,
        watch_version=row.watch_version,
        origin=origin_label(row.origin),
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
        # Bounded by the watch: `pw_value_metric` carries no watch id, so the metric rows are
        # joined to this watch's values of the current version before the page joins them.
        metric = (
            select(PwValueMetric.pw_value_id, PwValueMetric.events)
            .join(PwValue, PwValue.id == PwValueMetric.pw_value_id)
            .where(
                PwValueMetric.bucket_start == bucket_start,
                PwValue.watch_id == watch.id,
                PwValue.watch_version == watch.watch_version,
            )
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
                PwBucket.origin,
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
                    origin=origin_label(bucket.origin),
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
    scope = (await resolve_subjects(session, settings, [ScopeSubject.of(watch)], now=now))[0]
    if not scope.resolved:
        status = scope.status.value if scope.status is not None else "SCOPE_UNRESOLVED"
        raise PreviewRefused(f"{status}: {scope.reason}")
    if kind is PwScopeKind.LOG_SOURCE_GROUP and len(scope.chunks) > 1:
        raise PreviewRefused(
            f"the group resolves to {scope.member_count} sources and a preview covers one "
            f"search chunk of at most {settings.pw_max_sources_per_search}; preview a smaller "
            "group, a type or a source"
        )
    return scope.chunks[0]


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
        with ariel_background_priority():
            totals = await run_search(
                provider,
                PwSearchPlan(PwSearchKind.TOTALS, scope_kind, tuple(scope_ids)),
                (start, end),
                bucket_seconds,
                **common,
            )
        with ariel_background_priority():
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


# --------------------------------------------------------------------------- generated AQL
async def watch_aql(
    session: AsyncSession, watch: PropertyWatch, settings: Settings, now: datetime
) -> WatchAqlOut:
    """The generated AQL of a stored watch, read-only: no Ariel call, nothing accepted.

    Statements: the scope resolution (one for a source, none for a type, two for a group) and
    the newest bucket. The current text is the preview's generator over the preview window;
    when the scope does not resolve, `current_unavailable` says why instead of a guess.
    """
    start, end, bucket_seconds = preview_window(settings, now)
    current: list[WatchAqlQueryOut] = []
    unavailable: str | None = None
    try:
        scope_ids = await preview_scope_ids(session, watch, settings, now)
        scope_kind = PwScopeKind(str(watch.scope_kind))
        properties = tuple((p["kind"], p["name"]) for p in watch.properties)
        buckets = int((end - start).total_seconds()) // bucket_seconds
        for kind in (PwSearchKind.GROUPED, PwSearchKind.TOTALS):
            spec = validate_query_inputs(
                kind,
                scope_kind=scope_kind,
                scope_ids=scope_ids,
                properties=properties,
                max_sources=settings.pw_max_sources_per_search,
            )
            text = build_query(
                spec,
                start_ms=int(start.timestamp() * 1000),
                end_ms=int(end.timestamp() * 1000),
                bucket_ms=bucket_seconds * 1000,
                limit=search_row_cap(spec, buckets, settings.pw_max_rows_per_search) + 1,
            )
            current.append(WatchAqlQueryOut(search=kind.value, text=text))
    except PreviewRefused as exc:
        current, unavailable = [], str(exc)
    except ValueError as exc:  # a stored reference the validators now refuse
        current, unavailable = [], f"the stored watch cannot be searched: {exc}"
    newest = (
        await session.execute(
            select(
                PwBucket.bucket_start,
                PwBucket.collected_at,
                PwBucket.watch_version,
                PwBucket.query_provenance,
            )
            .where(PwBucket.watch_id == watch.id)
            .order_by(PwBucket.bucket_start.desc())
            .limit(1)
        )
    ).first()
    return WatchAqlOut(
        watch_id=watch.id,
        watch_version=int(watch.watch_version),
        window_start=None if unavailable else start,
        window_end=None if unavailable else end,
        bucket_seconds=None if unavailable else bucket_seconds,
        current=current,
        current_unavailable=unavailable,
        last_bucket=(
            None
            if newest is None
            else WatchAqlLastBucketOut(
                bucket_start=newest.bucket_start,
                collected_at=newest.collected_at,
                watch_version=int(newest.watch_version),
                queries=_stored_queries(newest.query_provenance),
            )
        ),
    )


def _stored_queries(provenance: Any) -> list[WatchAqlQueryOut]:
    """The query texts of a bucket's stored provenance, by named key; anything else is dropped."""
    out: list[WatchAqlQueryOut] = []
    if not isinstance(provenance, dict):
        return out
    for key, kind in (("grouped", "GROUPED"), ("totals", "TOTALS"), ("pinned", "PINNED")):
        part = provenance.get(key)
        text = part.get("query") if isinstance(part, dict) else None
        if isinstance(text, str) and text:
            out.append(WatchAqlQueryOut(search=kind, text=text))
    return out


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


# The actions a dependency refusal names, as the English phrase the callers pass and the
# registry's ``propertywatch.action.*`` code it is coded as.
_ACTION_CODES = {
    "change the scope, properties or pins": "propertywatch.action.change_pins",
    "change the scope or properties (the watch version would change)": (
        "propertywatch.action.change_scope"
    ),
    "unpin a value": "propertywatch.action.unpin",
    "delete the watch": "propertywatch.action.delete",
}
ACTION_CHANGE_PINS = "change the scope, properties or pins"
ACTION_CHANGE_SCOPE = "change the scope or properties (the watch version would change)"
ACTION_UNPIN = "unpin a value"
ACTION_DELETE = "delete the watch"


def action_ref(action: str) -> MessageRef | None:
    """The code of an action phrase, or None for a phrase the registry does not know."""
    code = _ACTION_CODES.get(action)
    return None if code is None else MessageRef(code)


def dependency_refusal(rules: Sequence[DependentRule], action: str) -> HTTPException:
    """The 409: what was refused, why, and the rules to detach first.

    An action phrase the registry does not know keeps the plain English body, uncoded.
    """
    names = ", ".join(f"'{rule.name}'" for rule in rules)
    listed = [{"id": str(rule.id), "name": rule.name} for rule in rules]
    ref = action_ref(action)
    if ref is None:
        return HTTPException(
            409,
            {
                "message": (
                    f"cannot {action}: custom rule(s) {names} pin a value of this watch; "
                    "detach or delete the rule(s) first"
                ),
                "rules": listed,
            },
        )
    return CodedHTTPException(
        409,
        MessageRef("propertywatch.dependency.refused", {"action": ref, "names": names}),
        fields={"rules": listed},
    )


def dependency_raced(action: str) -> HTTPException:
    """The 409 for a rule that pinned a value after the check: no rule is left to name."""
    ref = action_ref(action)
    if ref is None:
        return HTTPException(
            409,
            {
                "message": f"cannot {action}: a custom rule pinned a value of this watch while "
                "the request ran; reload and try again",
                "rules": [],
            },
        )
    return CodedHTTPException(
        409,
        MessageRef("propertywatch.dependency.raced", {"action": ref}),
        fields={"rules": []},
    )


# ------------------------------------------- alerts of a deleted or disabled watch (PW-4, G.10.1)
ALERT_SOURCE_TYPE = "pw_value"
REASON_WATCH_DELETED = "the watch was deleted"
#: The reason a `PATCH` that disables a watch resolves its incidents and alerts with.
REASON_WATCH_DISABLED = "watch disabled; no longer evaluated"


async def resolve_watch_alerts(
    session: AsyncSession,
    watch_id: uuid.UUID,
    *,
    actor: str,
    reason: str = REASON_WATCH_DELETED,
    enqueuer: AlertEnqueuer | None = None,
    clock: Any = None,
) -> int:
    """Resolve every open alert of this watch before its watch row is deleted, or on a disable.

    The alerts are found through the watch id the engine put in their context, not through the
    incident rows, so an alert whose incident is gone is resolved too. The actor is the
    principal who deleted or disabled the watch. Two statements and one flush however many
    alerts there are; none open is a no-op and enqueues nothing. Returns the number resolved.
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
            AlertResolution(fingerprint=item, actor=actor, reason=reason)
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


@dataclass(frozen=True)
class PinningRule:
    """A rule and the value it pins, for the pins editor and the watch's rules tab."""

    id: uuid.UUID
    name: str
    pw_value_id: uuid.UUID
    value_key: str
    components: list[str]
    enabled: bool


async def pinning_rules_by_watch(
    session: AsyncSession, watch_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[PinningRule]]:
    """Every F.1 rule that pins a value of each watch, ordered by name; one statement."""
    if not watch_ids:
        return {}
    rows = (
        await session.execute(
            select(
                CustomRule.id,
                CustomRule.name,
                CustomRule.pw_value_id,
                CustomRule.enabled,
                PwValue.watch_id,
                PwValue.value_key,
            )
            .join(PwValue, PwValue.id == CustomRule.pw_value_id)
            .where(PwValue.watch_id.in_(list(watch_ids)))
            .order_by(CustomRule.name.asc(), CustomRule.id.asc())
        )
    ).all()
    found: dict[uuid.UUID, list[PinningRule]] = {}
    for rule_id, name, value_id, enabled, watch_id, value_key in rows:
        # The inner join makes a null value id unreachable; a row that has one is malformed.
        if value_id is None:
            continue
        found.setdefault(watch_id, []).append(
            PinningRule(
                id=rule_id,
                name=name,
                pw_value_id=value_id,
                value_key=value_key,
                components=list(split_tuple_key(value_key)),
                enabled=bool(enabled),
            )
        )
    return found
