"""The history-backfill admin surface: what was read, what it would cost, and the requests.

HISTORY-BACKFILL §B.10.3. The API writes only `history_backfill_decision` (HB-2b): the
answer, a later NO-to-YES upgrade and the two requests ("Read history now", "Stop
reading"). It never inserts, updates or deletes a `history_backfill_scope` row; each
family's collector applies a request at its next tick.

Every read is one statement per table or family, whatever the number of sources, rules or
endpoints (AGENTS.md rule 9). Nothing here carries an event value, a property value or AQL.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.history.estimate import (
    IPS_ACTION_VALUES_PER_SOURCE,
    IPS_FANOUT_ROWS_PER_SOURCE,
    IPS_SOURCE_IPS_PER_SOURCE,
    FamilyEstimate,
    IpsGroup,
    catchall_searches,
    estimate_family,
    ips_searches,
    not_estimable,
    phase_a_searches,
    rule_match_searches,
    waf_searches,
)
from app.history.families import FAMILY_SPECS, FamilySpec, fit_family_window
from app.history.lifecycle import DecisionView, console_mode
from app.history.origin import is_live_clause
from app.history.planner import DEFAULT_MIN_WINDOW_DAYS, PlannerInputError, PlannerSettings
from app.history.progress_codec import ProgressCodecError, decode_progress
from app.history.state import SCOPE_TABLE_FAMILIES, HistoryFamily, HistoryState
from app.models.catchall import CatchallBucket
from app.models.enums import HistoryDecisionAnswer, HistoryDecisionSource
from app.models.history_backfill import (
    PER_SOURCE_FAMILIES,
    HistoryBackfillDecision,
    HistoryBackfillScope,
)
from app.models.instance import QRadarInstance
from app.models.ips import IpsEntity, IpsSourceBucket, IpsSourceProfile
from app.models.log_source import LogSource
from app.models.rule import AnalyticsRule
from app.models.waf import WafEndpoint, WafSourceBucket, WafSourceProfile
from app.repositories.audit import record_audit
from app.repositories.history_backfill import HistoryBackfillRepository
from app.schemas.history_backfill import (
    ConsoleEstimateOut,
    ConsoleOut,
    DecisionOut,
    EstimateOut,
    FamilyOut,
    GapOut,
    ScopeOut,
    SourceOut,
    WindowOut,
)
from app.services.search_cadence import SearchCadenceService

#: The families in the order the page shows them (the registry's order, scope-table only).
FAMILY_ORDER: tuple[HistoryFamily, ...] = tuple(
    family for family in FAMILY_SPECS if family in SCOPE_TABLE_FAMILIES
)
_PER_SOURCE = frozenset(PER_SOURCE_FAMILIES)

#: Families whose `floor` column holds the floor QRadar proved for the scope (§B.3.2).
#: Phase A and catch-all prove one floor per source instead, which the scope column cannot
#: hold: their `floor` is only where the read started (`T0 - m`), and the per-source floors
#: live in `progress` (§B.3.3). A read start is never presented as a proved floor.
PROVED_FLOOR_FAMILIES: frozenset[HistoryFamily] = frozenset(
    {HistoryFamily.WAF, HistoryFamily.IPS, HistoryFamily.RULE_MATCH}
)

LATER_YES_POSSIBLE = "possible"
LATER_YES_NOT_POSSIBLE = "not possible: this source already collected"
LATER_YES_HAS_SCOPE = "not needed: this source has a history scope"

REQUEST_MESSAGES = {
    "start": (
        "Read history now is recorded. Each family applies it at its next collection; "
        "no scope has changed yet."
    ),
    "stop": (
        "Stop reading is recorded. Each family ends its reading scopes at its next "
        "collection; no scope has changed yet."
    ),
}

#: Catch-all senders per bucket come from the live buckets of this many recent days.
CATCHALL_SENDER_DAYS = 7

#: A reading that ended, one way or another. `NOT_ELIGIBLE` is not among them: a
#: source that never read has nothing finished to count.
_FINISHED_STATES = (
    HistoryState.COMPLETE.value,
    HistoryState.PARTIAL.value,
    HistoryState.UNAVAILABLE.value,
)

_AUDIT_DECIDE = "history_backfill.decide"
_AUDIT_START = "history_backfill.start"
_AUDIT_STOP = "history_backfill.stop"

#: Why a start or a stop is refused: the console has no Yes to apply it to.
_REQUEST_REFUSED = "record Yes first: the console has no Yes answer to start or stop"


class ConsoleNotFoundError(LookupError):
    """No enabled console has this id. Says nothing about which consoles exist."""


@dataclass(frozen=True)
class DecideOutcome:
    """What `decide` did. `status` is `created`, `upgraded`, `unchanged` or `conflict`."""

    status: Literal["created", "upgraded", "unchanged", "conflict"]
    console: ConsoleOut
    detail: str | None = None


@dataclass(frozen=True)
class RequestOutcome:
    """What a start or stop request did. `refused` carries the reason in `detail`."""

    console: ConsoleOut
    refused: bool
    detail: str | None = None


# --------------------------------------------------------------------------- pure helpers


def aware(value: datetime | None) -> datetime | None:
    """The time as it is stored: UTC, never a naive one."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def decision_view(row: HistoryBackfillDecision) -> DecisionView:
    return DecisionView(
        answer=row.answer,
        days=row.days,
        start_requested_at=aware(row.start_requested_at),
        start_requested_by=row.start_requested_by,
        stop_requested_at=aware(row.stop_requested_at),
        stop_requested_by=row.stop_requested_by,
    )


def decision_out(row: HistoryBackfillDecision) -> DecisionOut:
    return DecisionOut(
        answer=row.answer,
        days=row.days,
        decided_at=row.decided_at,
        decided_by=row.decided_by,
        source=row.source,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
        start_requested_at=row.start_requested_at,
        start_requested_by=row.start_requested_by,
        stop_requested_at=row.stop_requested_at,
        stop_requested_by=row.stop_requested_by,
    )


def bucket_seconds_of(family: HistoryFamily, settings: Settings) -> int:
    """The family's bucket: rule matches read hourly windows, the rest 5-minute buckets."""
    if family is HistoryFamily.RULE_MATCH:
        return int(settings.rule_match_window_seconds)
    return int(settings.collection_interval_seconds)


def days_read(
    family: HistoryFamily,
    floor: datetime | None,
    through: datetime | None,
    bucket_seconds: int,
    *,
    target_start: datetime | None = None,
) -> float | None:
    """Whole buckets from the start this family can prove through `through`, in days.

    The `floor` column does not mean the same thing for every family (§B.3.2): for WAF,
    IPS and rule matches it is the floor QRadar proved (`floor_proved`), so the read may
    begin there. For Phase A and catch-all it is only where the read started, `T0 - m`,
    and the real floors are per source in `progress`; those two families therefore count
    from `max(floor, target_start)` when `target_start` is known, so the margin buckets
    before `T0` are never counted as read.

    One decimal. None when either end is missing or `through` precedes the start: a
    measurement that is not there is not zero. This is what was read, never an ETA.
    """
    floor_at, through_at = aware(floor), aware(through)
    start = floor_at
    if family not in PROVED_FLOOR_FAMILIES:
        target_at = aware(target_start)
        if target_at is not None and (start is None or target_at > start):
            start = target_at
    if start is None or through_at is None or through_at < start:
        return None
    buckets = int((through_at - start).total_seconds() // bucket_seconds) + 1
    return round(buckets * bucket_seconds / 86400, 1)


def scope_out(
    row: HistoryBackfillScope, bucket_seconds: int, name: str | None = None
) -> ScopeOut:
    """One scope row as the page shows it.

    The counts come from the decoded progress. A row whose progress is `{}` has not
    searched yet and a row whose progress does not decode is unknown: both answer
    `null`, never a zero (rule 8), and the second says so with `progress_unreadable`.
    `name` is the scope's log-source name for WAF and IPS, looked up once for the whole
    console (never one query per row); a console-wide row has none.
    """
    searches: int | None = None
    buckets_read: int | None = None
    gaps: list[GapOut] | None = None
    unreadable = False
    if row.progress:  # `{}` is a scope that has not stepped yet: no counts, not zeros
        try:
            progress = decode_progress(row.progress).progress
        except ProgressCodecError:
            unreadable = True
        else:
            searches, buckets_read = progress.searches, progress.buckets_read
            gaps = [GapOut(start=g.start, end=g.end, reason=g.reason) for g in progress.gaps]
    family = HistoryFamily(row.family)
    if family not in _PER_SOURCE:
        later_yes = LATER_YES_POSSIBLE
    elif row.state == HistoryState.NOT_ELIGIBLE.value:
        later_yes = LATER_YES_NOT_POSSIBLE
    else:
        later_yes = LATER_YES_HAS_SCOPE
    return ScopeOut(
        scope_key=row.scope_key,
        name=None if family not in _PER_SOURCE else name,
        state=row.state,
        days_requested=row.days_requested,
        days_target=row.days_target,
        target_start=row.target_start,
        floor=row.floor,
        # The family must prove a scope-wide floor AND the row must carry one: a
        # `NOT_ELIGIBLE` or `UNAVAILABLE` row has `floor` null and so proved nothing.
        floor_proved=family in PROVED_FLOOR_FAMILIES and row.floor is not None,
        through=row.through,
        anchor=row.anchor,
        started_at=row.started_at,
        finished_at=row.finished_at,
        reason=row.reason,
        searches=searches,
        buckets_read=buckets_read,
        days_read=days_read(
            family, row.floor, row.through, bucket_seconds, target_start=row.target_start
        ),
        gaps=gaps,
        progress_unreadable=unreadable,
        later_yes=later_yes,
    )


def collection_state(spec: FamilySpec, settings: Settings) -> tuple[bool, str]:
    """Whether the family collects, and the words for it (the registry's reason if no switch)."""
    if spec.collection_switch is None:
        return True, spec.always_on_reason or "collection has no switch"
    on = bool(getattr(settings, spec.collection_switch))
    name = spec.collection_switch.upper()
    return on, f"{name} is {'on' if on else 'off'}"


def planner_settings(
    family: HistoryFamily, settings: Settings, cadence_seconds: int
) -> PlannerSettings:
    """The numbers the estimate uses for a family, from the settings (never from the browser)."""
    row_caps = {
        HistoryFamily.CATCHALL: settings.catchall_max_rows_per_search,
        HistoryFamily.RULE_MATCH: settings.rule_match_max_rows,
    }
    return PlannerSettings(
        bucket_seconds=bucket_seconds_of(family, settings),
        floor_margin_hours=settings.history_backfill_floor_margin_hours,
        row_cap=int(row_caps.get(family, settings.ariel_multi_bucket_max_rows)),
        max_failures=settings.history_backfill_max_failures,
        max_lower_bound_share=settings.history_backfill_max_lower_bound_share,
        max_hours=settings.history_backfill_max_hours,
        searches_per_tick=settings.history_backfill_searches_per_tick,
        cadence_seconds=cadence_seconds,
    )


def floor_to_bucket(now: datetime, bucket_seconds: int) -> datetime:
    """The start of the bucket `now` falls in, so the estimate's end is a whole bucket."""
    epoch = int(now.timestamp()) // bucket_seconds * bucket_seconds
    return datetime.fromtimestamp(epoch, tz=UTC)


def group_sources(keys: Sequence[int], size: int) -> list[list[int]]:
    """Sources in plans of at most `size` (`ips_max_sources_per_search`), in a fixed order."""
    ordered = sorted(keys)
    return [ordered[i : i + size] for i in range(0, len(ordered), size)]


def duration_words(seconds: int) -> str:
    """A duration in words: minutes, hours or days, whichever reads as a duration."""
    if seconds >= 86400 and seconds % 86400 == 0:
        count, unit = seconds // 86400, "day"
    elif seconds >= 3600 and seconds % 3600 == 0:
        count, unit = seconds // 3600, "hour"
    else:
        count, unit = max(1, seconds // 60), "minute"
    return f"{count} {unit}" if count == 1 else f"{count} {unit}s"


def plural(count: int, singular: str) -> str:
    """`count` and the word, with the plural when it is more than one."""
    return f"{count} {singular}" if count == 1 else f"{count} {singular}s"


# --------------------------------------------------------------------------- the service


class HistoryBackfillService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.repo = HistoryBackfillRepository(session)

    # ------------------------------------------------------------------ consoles
    async def _enabled_consoles(self) -> list[QRadarInstance]:
        rows = await self.session.scalars(
            select(QRadarInstance)
            .where(QRadarInstance.enabled.is_(True))
            .order_by(QRadarInstance.name, QRadarInstance.id)
        )
        return list(rows)

    async def _console_row(self, instance_id: uuid.UUID) -> QRadarInstance:
        instance = await self.session.scalar(
            select(QRadarInstance).where(
                QRadarInstance.id == instance_id, QRadarInstance.enabled.is_(True)
            )
        )
        if instance is None:
            raise ConsoleNotFoundError("console not found")
        return instance

    async def consoles(self) -> list[ConsoleOut]:
        return await self._entries(await self._enabled_consoles())

    async def console(self, instance_id: uuid.UUID) -> ConsoleOut:
        return (await self._entries([await self._console_row(instance_id)]))[0]

    async def _entries(self, instances: Sequence[QRadarInstance]) -> list[ConsoleOut]:
        """The page's entry of each console: constant statements for any number of rows."""
        if not instances:
            return []
        ids = [instance.id for instance in instances]
        # Plain values first: a later statement may expire what a flush touched.
        names = {instance.id: instance.name for instance in instances}
        decisions = await self.repo.decisions(ids)
        scopes: dict[tuple[uuid.UUID, str], list[HistoryBackfillScope]] = defaultdict(list)
        for scope in await self.session.scalars(
            select(HistoryBackfillScope)
            .where(HistoryBackfillScope.instance_id.in_(ids))
            .order_by(
                HistoryBackfillScope.instance_id,
                HistoryBackfillScope.family,
                HistoryBackfillScope.scope_key,
            )
            .execution_options(populate_existing=True)
        ):
            scopes[(scope.instance_id, scope.family)].append(scope)
        # The log source's name of a WAF or IPS scope row: one statement for every row of
        # every console, never one per row (rule 9).
        scope_names = await self._scope_names(ids, scopes)
        sources = {
            HistoryFamily.WAF: await self._sources(
                WafSourceProfile, WafSourceBucket, is_live_clause(WafSourceBucket), ids
            ),
            HistoryFamily.IPS: await self._sources(
                IpsSourceProfile, IpsSourceBucket, is_live_clause(IpsSourceBucket), ids
            ),
        }

        out: list[ConsoleOut] = []
        for instance_id in ids:
            decision = decisions.get(instance_id)
            families = []
            for family in FAMILY_ORDER:
                rows = scopes.get((instance_id, family.value), [])
                families.append(
                    self._family_out(
                        family,
                        rows,
                        sources.get(family, {}).get(instance_id),
                        scope_names.get(instance_id, {}),
                    )
                )
            out.append(
                ConsoleOut(
                    instance_id=instance_id,
                    name=names[instance_id],
                    decision=None if decision is None else decision_out(decision),
                    mode=console_mode(None if decision is None else decision_view(decision)),
                    families=families,
                )
            )
        return out

    async def _scope_names(
        self,
        instance_ids: Sequence[uuid.UUID],
        scopes: Mapping[tuple[uuid.UUID, str], Sequence[HistoryBackfillScope]],
    ) -> dict[uuid.UUID, dict[str, str]]:
        """Every WAF or IPS scope row's log-source name: one statement for all consoles.

        `scope_key` is the QRadar log-source id, so this is the lookup `SourceOut` uses
        (`log_source` joined on `qradar_id`), done once per request. A key that is not an
        id, and a source that is no longer known, have no name: the page shows the key
        instead of inventing one.
        """
        wanted: dict[uuid.UUID, set[int]] = defaultdict(set)
        for (instance_id, family), rows in scopes.items():
            if HistoryFamily(family) not in _PER_SOURCE:
                continue
            for row in rows:
                try:
                    wanted[instance_id].add(int(row.scope_key))
                except ValueError:
                    continue
        ids = sorted(wanted)
        if not ids:
            return {}
        found: dict[uuid.UUID, dict[str, str]] = defaultdict(dict)
        name_rows = await self.session.execute(
            select(LogSource.instance_id, LogSource.qradar_id, LogSource.name).where(
                LogSource.instance_id.in_(ids),
                LogSource.qradar_id.in_(sorted({k for keys in wanted.values() for k in keys})),
            )
        )
        for instance_id, qradar_id, name in name_rows:
            found[instance_id][str(qradar_id)] = name
        return found

    def _family_out(
        self,
        family: HistoryFamily,
        rows: Sequence[HistoryBackfillScope],
        sources: list[tuple[str, str, bool, bool]] | None,
        names: Mapping[str, str],
    ) -> FamilyOut:
        spec = FAMILY_SPECS[family]
        enabled, reason = collection_state(spec, self.settings)
        fitted = fit_family_window(family, self.settings)
        bucket = bucket_seconds_of(family, self.settings)
        keys = {row.scope_key for row in rows}
        listed: list[SourceOut] | None = None
        if family in _PER_SOURCE:
            listed = [
                SourceOut(
                    scope_key=key,
                    name=name,
                    profile_enabled=profile_enabled,
                    later_yes=LATER_YES_NOT_POSSIBLE if has_live else LATER_YES_POSSIBLE,
                )
                for key, name, profile_enabled, has_live in (sources or [])
                if key not in keys
            ]
        return FamilyOut(
            family=family.value,
            collection_enabled=enabled,
            collection_reason=reason,
            window=WindowOut(
                days_requested=fitted.requested_days,
                days=fitted.days,
                available=fitted.available,
                limited_by=list(fitted.limited_by),
                reason=fitted.reason,
            ),
            scopes=[scope_out(row, bucket, names.get(row.scope_key)) for row in rows],
            sources=listed,
            later_yes=None if family in _PER_SOURCE else LATER_YES_POSSIBLE,
        )

    async def _sources(
        self,
        profile: Any,
        bucket: Any,
        live_clause: Any,
        instance_ids: Sequence[uuid.UUID],
    ) -> dict[uuid.UUID, list[tuple[str, str, bool, bool]]]:
        """Every configured source of a family: one statement for all consoles.

        The configured-source tables are `waf_source_profile` and `ips_source_profile`
        (one row per log source). "Has live rows" is one `EXISTS` on the family's bucket
        table, per profile row inside that one statement (§B.5). The caller names the
        live-only clause with the table it reads (`is_live_clause`), so a reader of a table
        that may hold history says so with the one spelling (§B.4.7).
        """
        has_live = exists().where(
            bucket.log_source_id == profile.log_source_id, live_clause
        )
        rows = await self.session.execute(
            select(
                profile.instance_id,
                LogSource.qradar_id,
                LogSource.name,
                profile.enabled,
                has_live,
            )
            .join(LogSource, LogSource.id == profile.log_source_id)
            .where(profile.instance_id.in_(list(instance_ids)))
            .order_by(profile.instance_id, LogSource.qradar_id)
        )
        found: dict[uuid.UUID, list[tuple[str, str, bool, bool]]] = defaultdict(list)
        for instance_id, qradar_id, name, enabled, live in rows:
            found[instance_id].append((str(qradar_id), name, bool(enabled), bool(live)))
        return found

    # ------------------------------------------------------------------ writes
    async def _stored_decision(self, instance_id: uuid.UUID) -> HistoryBackfillDecision | None:
        """The stored decision. Kept apart so a test can place a write after this read."""
        return (await self.repo.decisions([instance_id])).get(instance_id)

    async def decide(
        self,
        instance_id: uuid.UUID,
        answer: str,
        *,
        actor: str,
        source_ip: str | None,
    ) -> DecideOutcome:
        """Record Yes or No; a No may become a Yes; a Yes is never reversed (§B.5)."""
        await self._console_row(instance_id)
        asked = HistoryDecisionAnswer(answer)
        days = self.settings.history_backfill_days if asked is HistoryDecisionAnswer.YES else None
        now = self._clock()
        stored = await self._stored_decision(instance_id)

        status: Literal["created", "upgraded", "unchanged", "conflict"]
        previous: str | None = None
        if stored is None:
            row, inserted = await self.repo.record_decision(
                instance_id,
                answer=asked.value,
                days=days,
                source=HistoryDecisionSource.UI.value,
                decided_by=actor,
                at=now,
            )
            if inserted:
                status = "created"
            else:  # a row appeared between the read and the insert: the stored row wins
                status = "unchanged" if row.answer == asked.value else "conflict"
        elif stored.answer == asked.value:
            status = "unchanged"
        elif asked is HistoryDecisionAnswer.NO:
            return DecideOutcome(
                "conflict",
                await self.console(instance_id),
                "a Yes is never reversed; stop the reading instead",
            )
        else:
            previous = stored.answer
            # This branch is only reached for a Yes, so the window is the setting.
            upgraded = await self.repo.upgrade_to_yes(
                instance_id, days=int(self.settings.history_backfill_days), by=actor, at=now
            )
            status = "upgraded" if upgraded else "unchanged"  # someone upgraded it first

        if status in ("created", "upgraded"):
            await record_audit(
                self.session,
                action=_AUDIT_DECIDE,
                actor=actor,
                object_type="qradar_instance",
                object_id=str(instance_id),
                detail={
                    "answer": asked.value,
                    "days": days,
                    "source": HistoryDecisionSource.UI.value,
                    "previous": previous,
                },
                source_ip=source_ip,
            )
        console = await self.console(instance_id)
        detail = None
        if status == "conflict":
            detail = "a decision was recorded in the meantime; it is shown here"
        return DecideOutcome(status, console, detail)

    async def request(
        self,
        instance_id: uuid.UUID,
        kind: Literal["start", "stop"],
        *,
        actor: str,
        source_ip: str | None,
    ) -> RequestOutcome:
        """Record "Read history now" or "Stop reading" on a Yes. No scope row is touched."""
        await self._console_row(instance_id)
        stored = await self._stored_decision(instance_id)
        if stored is None or stored.answer != HistoryDecisionAnswer.YES.value:
            return RequestOutcome(await self.console(instance_id), True, _REQUEST_REFUSED)
        # The request is stamped by this call alone, strictly later than the opposite
        # request, so the pair never ties (HB-2b: equal times read ACTIVE).
        opposite = aware(stored.stop_requested_at if kind == "start" else stored.start_requested_at)
        at = self._clock()
        if opposite is not None and at <= opposite:
            at = opposite + timedelta(microseconds=1)
        ask = self.repo.request_start if kind == "start" else self.repo.request_stop
        if not await ask(instance_id, by=actor, at=at):
            return RequestOutcome(await self.console(instance_id), True, _REQUEST_REFUSED)
        console = await self.console(instance_id)
        scopes = [scope for family in console.families for scope in family.scopes]
        await record_audit(
            self.session,
            action=_AUDIT_START if kind == "start" else _AUDIT_STOP,
            actor=actor,
            object_type="qradar_instance",
            object_id=str(instance_id),
            detail={
                "scopes_reading": sum(s.state == HistoryState.READING.value for s in scopes),
                "scopes_finished": sum(s.state in _FINISHED_STATES for s in scopes),
            },
            source_ip=source_ip,
        )
        return RequestOutcome(console.model_copy(update={"message": REQUEST_MESSAGES[kind]}), False)

    # ------------------------------------------------------------------ estimate
    async def estimates(self) -> list[ConsoleEstimateOut]:
        """The §B.8 model per console and family, from one counting statement per family."""
        instances = await self._enabled_consoles()
        if not instances:
            return []
        ids = [instance.id for instance in instances]
        counts = await self._counts(ids)
        cadences = {row.family: row.cadence_seconds for row in await self._cadences()}
        now = self._clock()
        out: list[ConsoleEstimateOut] = []
        for instance in instances:
            families = [
                self._estimate_out(
                    self._estimate(family, instance.id, counts, cadences, now)
                )
                for family in FAMILY_ORDER
            ]
            out.append(
                ConsoleEstimateOut(instance_id=instance.id, name=instance.name, families=families)
            )
        return out

    async def _cadences(self) -> list[Any]:
        return list(await SearchCadenceService(self.session, self.settings).list())

    @staticmethod
    def _estimate_out(estimate: FamilyEstimate) -> EstimateOut:
        return EstimateOut(
            family=estimate.family,
            searches=estimate.searches,
            ticks=estimate.ticks,
            hours=None if estimate.hours is None else round(estimate.hours, 1),
            days_requested=estimate.days_requested,
            days_fitted=estimate.days_fitted,
            reduced=estimate.reduced,
            refused=estimate.refused,
            reason=estimate.reason,
            assumptions=list(estimate.assumptions),
        )

    async def _counts(self, ids: Sequence[uuid.UUID]) -> dict[str, Any]:
        """One statement per family, none for a family that is switched off."""
        settings = self.settings
        listed = list(ids)
        counts: dict[str, Any] = {}

        # The collector's own filter (`metric_collector.py`): every source the platform
        # monitors, whatever QRadar says about the source. A QRadar-disabled source still
        # costs a search, so it counts here too.
        phase_a = await self.session.execute(
            select(LogSource.instance_id, func.count())
            .where(
                LogSource.instance_id.in_(listed),
                LogSource.monitoring_enabled.is_(True),
            )
            .group_by(LogSource.instance_id)
        )
        counts[HistoryFamily.PHASE_A.value] = {i: int(n) for i, n in phase_a}

        if settings.waf_collection_enabled:
            waf = await self.session.execute(
                select(
                    WafSourceProfile.instance_id,
                    LogSource.qradar_id,
                    func.count(WafEndpoint.id),
                )
                .join(LogSource, LogSource.id == WafSourceProfile.log_source_id)
                .outerjoin(
                    WafEndpoint,
                    and_(
                        WafEndpoint.log_source_id == WafSourceProfile.log_source_id,
                        WafEndpoint.is_active.is_(True),
                    ),
                )
                .where(WafSourceProfile.instance_id.in_(listed), WafSourceProfile.enabled.is_(True))
                .group_by(WafSourceProfile.instance_id, LogSource.qradar_id)
            )
            by_console: dict[uuid.UUID, dict[int, int]] = defaultdict(dict)
            for instance_id, qradar_id, endpoints in waf:
                by_console[instance_id][int(qradar_id)] = int(endpoints)
            counts[HistoryFamily.WAF.value] = by_console

        if settings.ips_collection_enabled:
            ips = await self.session.execute(
                select(
                    IpsSourceProfile.instance_id,
                    LogSource.qradar_id,
                    IpsEntity.dimension,
                    func.count(IpsEntity.id),
                )
                .join(LogSource, LogSource.id == IpsSourceProfile.log_source_id)
                .outerjoin(
                    IpsEntity,
                    and_(
                        IpsEntity.log_source_id == IpsSourceProfile.log_source_id,
                        IpsEntity.active.is_(True),
                    ),
                )
                .where(IpsSourceProfile.instance_id.in_(listed), IpsSourceProfile.enabled.is_(True))
                .group_by(IpsSourceProfile.instance_id, LogSource.qradar_id, IpsEntity.dimension)
            )
            per_console: dict[uuid.UUID, dict[int, dict[str, int]]] = defaultdict(dict)
            for instance_id, qradar_id, dimension, entities in ips:
                dims = per_console[instance_id].setdefault(int(qradar_id), {})
                if dimension is not None:
                    dims[str(dimension)] = int(entities)
            counts[HistoryFamily.IPS.value] = per_console

        if settings.catchall_collection_enabled:
            since = self._clock() - timedelta(days=CATCHALL_SENDER_DAYS)
            # The mean over every live bucket of the window, empty ones included: a
            # bucket with no sender is a measurement of zero, not a missing one.
            catchall = await self.session.execute(
                select(
                    LogSource.instance_id,
                    func.avg(CatchallBucket.distinct_senders),
                    func.count(),
                )
                .join(LogSource, LogSource.id == CatchallBucket.catchall_log_source_id)
                .where(
                    LogSource.instance_id.in_(listed),
                    is_live_clause(CatchallBucket),
                    CatchallBucket.bucket_start >= since,
                )
                .group_by(LogSource.instance_id)
            )
            counts[HistoryFamily.CATCHALL.value] = {
                i: (math.ceil(float(avg)), int(buckets))
                for i, avg, buckets in catchall
                if avg is not None
            }

        if settings.rule_match_collection_enabled:
            rules = await self.session.execute(
                select(AnalyticsRule.instance_id, func.count())
                .where(AnalyticsRule.instance_id.in_(listed), AnalyticsRule.enabled.is_(True))
                .group_by(AnalyticsRule.instance_id)
            )
            counts[HistoryFamily.RULE_MATCH.value] = {i: int(n) for i, n in rules}
        return counts

    def _estimate(
        self,
        family: HistoryFamily,
        instance_id: uuid.UUID,
        counts: dict[str, Any],
        cadences: dict[str, int],
        now: datetime,
    ) -> FamilyEstimate:
        settings = self.settings
        spec = FAMILY_SPECS[family]
        requested = int(settings.history_backfill_days)
        cadence = int(cadences.get(spec.search_family, 300))
        per_tick = int(settings.history_backfill_searches_per_tick)
        if family is HistoryFamily.WAF:
            per_tick *= int(settings.waf_ariel_max_concurrent_sources)
        bucket = bucket_seconds_of(family, settings)
        shape = (
            "hourly windows"
            if family is HistoryFamily.RULE_MATCH
            else f"{bucket // 60}-minute buckets"
        )
        assumptions = [
            f"{shape}, {settings.history_backfill_searches_per_tick} history searches per tick"
            + (
                f" times {settings.waf_ariel_max_concurrent_sources} sources at once"
                if family is HistoryFamily.WAF
                else ""
            ),
            f"one tick every {duration_words(cadence)} (the family's Search cadence)",
            "QRadar time per search not measured on this console",
        ]

        def none(reason: str, refused: bool = False) -> FamilyEstimate:
            return not_estimable(
                family.value,
                days_requested=requested,
                reason=reason,
                assumptions=assumptions,
                refused=refused,
            )

        enabled, switch_reason = collection_state(spec, settings)
        if not enabled:
            return none(f"no estimate: collection is switched off ({switch_reason})")
        fitted = fit_family_window(family, settings)
        if not fitted.available:
            return none(fitted.reason or "history is unavailable", refused=True)
        try:
            ps = planner_settings(family, settings, cadence)
            end = floor_to_bucket(now, bucket)
            lookup = self._shape(family, instance_id, counts, settings)
            if isinstance(lookup, str):
                return none(lookup)
            searches_for_days, extra = lookup
            assumptions.extend(extra)
            assert fitted.days is not None
            result = estimate_family(
                family.value,
                searches_for_days(ps),
                days_requested=fitted.days,
                end=end,
                ps=ps,
                searches_per_tick=per_tick,
                assumptions=assumptions,
            )
        except PlannerInputError as exc:
            return none(f"no estimate: the planner refused the settings ({exc})")
        reason = result.reason
        # HB-6's adapter does not fit this window: `WafSourcePipeline._run_history_volume`
        # (`app/services/waf_pipeline.py:439`) bounds the days by the family's window only,
        # and the planner ends the scope PARTIAL at `max_hours` (`app/history/planner.py:465`).
        # Where the model would shorten or refuse the window, say what the reading will
        # really do rather than leaving the model's verdict to read as the adapter's.
        if family is HistoryFamily.WAF and (result.reduced or result.refused):
            assert fitted.days is not None
            if result.refused:
                covered = DEFAULT_MIN_WINDOW_DAYS
            else:
                assert result.days_fitted is not None
                covered = result.days_fitted
            reason = (
                "this figure is the model's, and the WAF reading does not apply it: it "
                "does not shorten its window or refuse it. It reads the "
                f"{fitted.days}-day window and ends PARTIAL when the "
                f"{settings.history_backfill_max_hours}-hour limit is reached. The model "
                f"needs {covered} days in about {result.hours:.1f} hours "
                f"({result.searches} searches, {result.ticks} ticks)"
            )
        reduced = result.reduced
        if fitted.days is not None and fitted.days < requested:
            reduced = True
            reason = f"{fitted.reason}; {result.reason}" if result.reduced else str(fitted.reason)
        return FamilyEstimate(
            family=result.family,
            searches=result.searches,
            ticks=result.ticks,
            hours=result.hours,
            days_requested=requested,
            days_fitted=result.days_fitted,
            reduced=reduced,
            refused=result.refused,
            reason=reason,
            assumptions=result.assumptions,
        )

    @staticmethod
    def _shape(
        family: HistoryFamily,
        instance_id: uuid.UUID,
        counts: dict[str, Any],
        settings: Settings,
    ) -> str | tuple[Callable[[PlannerSettings], Callable[[int], int]], list[str]]:
        """The searches-by-days function for a family, or the reason there is no count."""
        mine = counts.get(family.value, {})
        if family is HistoryFamily.PHASE_A:
            sources = mine.get(instance_id, 0)
            if sources < 1:
                return "no estimate: the console has no monitored log source yet"
            return (
                lambda ps: lambda days: phase_a_searches(sources, days, ps),
                [f"{sources} monitored sources, one row each per bucket"],
            )
        if family is HistoryFamily.WAF:
            endpoints = list(mine.get(instance_id, {}).values())
            if not endpoints:
                return "no estimate: no WAF source is enabled on this console"
            if min(endpoints) < 1:
                return "no estimate: an enabled WAF source has no tracked endpoint yet"
            breakdown = int(settings.history_backfill_waf_breakdown_days)
            return (
                lambda ps: lambda days: waf_searches(endpoints, days, breakdown, ps),
                [
                    f"{len(endpoints)} WAF sources with {sum(endpoints)} tracked endpoints",
                    f"action, response code and source IP read over {breakdown} days only",
                ],
            )
        if family is HistoryFamily.IPS:
            sources_dims: dict[int, dict[str, int]] = mine.get(instance_id, {})
            if not sources_dims:
                return "no estimate: no IPS source is enabled on this console"
            if any(not dims for dims in sources_dims.values()):
                return "no estimate: an enabled IPS source has no admitted entity yet"
            groups = [
                IpsGroup(
                    sources=len(members),
                    ports=sum(sources_dims[k].get("DESTINATION_PORT", 0) for k in members),
                    ips=sum(sources_dims[k].get("DESTINATION_IP", 0) for k in members),
                    signatures=sum(sources_dims[k].get("SIGNATURE", 0) for k in members),
                    source_ips=IPS_SOURCE_IPS_PER_SOURCE * len(members),
                )
                for members in group_sources(
                    list(sources_dims), int(settings.ips_max_sources_per_search)
                )
            ]
            return (
                lambda ps: lambda days: ips_searches(groups, days, ps),
                [
                    f"{len(sources_dims)} IPS sources in "
                    f"{plural(len(groups), 'plan')} of at most "
                    f"{settings.ips_max_sources_per_search}",
                    f"{IPS_SOURCE_IPS_PER_SOURCE} source IPs, "
                    f"{IPS_ACTION_VALUES_PER_SOURCE} action values and "
                    f"{IPS_FANOUT_ROWS_PER_SOURCE} port fan-out rows per source and bucket "
                    "assumed",
                ],
            )
        if family is HistoryFamily.CATCHALL:
            measured = mine.get(instance_id)
            if measured is None:
                return (
                    "no estimate: no catch-all bucket has been collected in the last "
                    f"{CATCHALL_SENDER_DAYS} days"
                )
            senders, buckets = measured
            return (
                lambda ps: lambda days: catchall_searches(senders, days, ps),
                [
                    f"{senders} senders per bucket (the mean of {plural(buckets, 'live bucket')} "
                    f"of the last {CATCHALL_SENDER_DAYS} days)",
                    f"{settings.catchall_max_rows_per_search} rows per search",
                ],
            )
        rules = mine.get(instance_id, 0)
        if rules < 1:
            return "no estimate: the console has no enabled rule"
        return (
            lambda ps: lambda days: rule_match_searches(rules, days, ps),
            [
                f"{rules} enabled rules, every one assumed to match in each window",
                f"{settings.rule_match_max_rows} rows per search, plus one console total",
            ],
        )
