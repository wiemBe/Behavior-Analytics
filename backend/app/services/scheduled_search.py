"""Scheduled-search lifecycle: create, version, update, list, manual trigger.

Only persisted, validated search definitions can ever be scheduled or executed.
The AQL text is validated at authoring time and stored; every change to the AQL
mints a new immutable SearchQueryVersion, and each execution records which
version it ran. Nothing here accepts ad-hoc AQL from a request for direct
execution — a manual run always executes the stored query of an existing search.
"""

from __future__ import annotations

import asyncio
import builtins
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.search import ScheduledSearch, SearchExecution, SearchQueryVersion
from app.schemas.search import (
    ScheduledSearchCreate,
    ScheduledSearchUpdate,
    ThresholdUnreachableError,
    unreachable_threshold_reason,
)


class EnabledSearchLimitError(Exception):
    """Creating or enabling a search would pass the enabled-search cap."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"the limit is {limit} enabled searches")


class ScheduledSearchService:
    def __init__(self, session: AsyncSession, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    async def count_enabled(self) -> int:
        """How many searches are enabled. One query, for the whole table."""
        counted = await self.session.scalar(
            select(func.count())
            .select_from(ScheduledSearch)
            .where(ScheduledSearch.enabled.is_(True))
        )
        return int(counted or 0)

    async def _refuse_over_cap(self) -> None:
        limit = self.settings.scheduled_searches_max_enabled
        if await self.count_enabled() >= limit:
            raise EnabledSearchLimitError(limit)

    async def _refuse_fast_cron(self, cron: str, timezone: str) -> None:
        # Local import: search_scheduler imports this module. The check is a
        # few milliseconds for a 5-minute schedule and longer only while it
        # walks a DST transition. It stays off the event loop either way.
        from app.services.search_scheduler import CronFloorError, check_cron_floor

        outcome = await asyncio.to_thread(check_cron_floor, cron, timezone)
        if not outcome.accepted:
            raise CronFloorError(outcome.reason or "schedule_cron is below the floor")

    async def create(self, payload: ScheduledSearchCreate, *, actor: str | None) -> ScheduledSearch:
        # Floor first, so a too-frequent cron is refused before the cap query.
        await self._refuse_fast_cron(payload.schedule_cron, payload.schedule_timezone)
        if payload.enabled:
            await self._refuse_over_cap()
        search = ScheduledSearch(
            name=payload.name,
            description=payload.description,
            owner=payload.owner,
            category=payload.category,
            mitre_techniques=payload.mitre_techniques,
            aql_query=payload.aql_query,
            query_version=1,
            schedule_cron=payload.schedule_cron,
            schedule_timezone=payload.schedule_timezone,
            threshold_value=payload.threshold_value,
            threshold_operator=payload.threshold_operator,
            severity=payload.severity,
            timeout_seconds=payload.timeout_seconds,
            max_time_range_hours=payload.max_time_range_hours,
            max_result_rows=payload.max_result_rows,
            visualization_type=payload.visualization_type,
            enabled=payload.enabled,
        )
        self.session.add(search)
        await self.session.flush()
        self._add_version(search, actor=actor, note="initial version")
        await self.session.flush()
        return search

    async def get(self, search_id: uuid.UUID) -> ScheduledSearch | None:
        return await self.session.get(ScheduledSearch, search_id)

    async def list(self) -> list[ScheduledSearch]:
        result = await self.session.scalars(
            select(ScheduledSearch).order_by(ScheduledSearch.name)
        )
        return list(result.all())

    async def versions(self, search_id: uuid.UUID) -> builtins.list[SearchQueryVersion]:
        return list(
            (
                await self.session.scalars(
                    select(SearchQueryVersion)
                    .where(SearchQueryVersion.search_id == search_id)
                    .order_by(SearchQueryVersion.version.desc())
                )
            ).all()
        )

    async def update(
        self, search: ScheduledSearch, payload: ScheduledSearchUpdate, *, actor: str | None
    ) -> ScheduledSearch:
        data = payload.model_dump(exclude_unset=True, exclude={"change_note"})
        new_aql = data.pop("aql_query", None)

        # An unchanged expression is kept verbatim, even when it would fail the
        # floor. Only a new string is checked. A timezone-only edit does not
        # recheck the stored cron (SEARCH-CADENCE, "Custom").
        new_cron = data.get("schedule_cron")
        if isinstance(new_cron, str) and new_cron != search.schedule_cron:
            timezone = data.get("schedule_timezone", search.schedule_timezone)
            if not isinstance(timezone, str) or not timezone:
                timezone = search.schedule_timezone
            await self._refuse_fast_cron(new_cron, timezone)
        if data.get("enabled") is True and not search.enabled:
            await self._refuse_over_cap()

        # The threshold rule applies to the *effective* search after this patch:
        # lowering max_result_rows under an existing threshold, raising the
        # threshold above the existing cap, or changing only the operator is
        # refused before anything is written. A reachable patch never gets here.
        effective_operator = data.get("threshold_operator", search.threshold_operator)
        effective_threshold = data.get("threshold_value", search.threshold_value)
        effective_max_rows = data.get("max_result_rows", search.max_result_rows)
        reason = unreachable_threshold_reason(
            effective_operator, effective_threshold, effective_max_rows
        )
        if reason is not None:
            raise ThresholdUnreachableError(reason)

        for field, value in data.items():
            setattr(search, field, value)

        # An AQL change is a new immutable version; the query string in-place is
        # kept as the "current" pointer, history lives in SearchQueryVersion.
        if new_aql is not None and new_aql != search.aql_query:
            search.aql_query = new_aql
            search.query_version += 1
            self._add_version(search, actor=actor, note=payload.change_note or "aql updated")

        await self.session.flush()
        return search

    async def executions(
        self, search_id: uuid.UUID, *, limit: int = 50
    ) -> builtins.list[SearchExecution]:
        return list(
            (
                await self.session.scalars(
                    select(SearchExecution)
                    .where(SearchExecution.search_id == search_id)
                    .order_by(SearchExecution.created_at.desc())
                    .limit(limit)
                )
            ).all()
        )

    def manual_run_key(self, search: ScheduledSearch, actor: str) -> str:
        """A run key unique to this manual trigger instant, so two manual runs
        are distinct executions but a retry of one reuses its row."""
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        return f"manual:{actor}:{stamp}"

    @staticmethod
    def scheduled_run_key(search: ScheduledSearch, scheduled_for: datetime) -> str:
        """Deterministic per-tick key: the same scheduled instant maps to the
        same execution, so a duplicate beat delivery cannot double-run."""
        return f"scheduled:{scheduled_for.strftime('%Y%m%dT%H%M%S')}"

    def _add_version(self, search: ScheduledSearch, *, actor: str | None, note: str | None) -> None:
        self.session.add(
            SearchQueryVersion(
                search_id=search.id,
                version=search.query_version,
                aql_query=search.aql_query,
                changed_by=actor,
                change_note=note,
            )
        )
