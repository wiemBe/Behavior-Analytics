"""Atomic cadence-slot claims and operator-managed search-family settings."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.enums import SearchCadence, SearchFamily, default_search_cadence
from app.models.search_cadence import SearchCadenceSetting
from app.repositories.audit import record_audit

logger = logging.getLogger("app.search_cadence")

_FAMILY_SWITCHES: dict[SearchFamily, str] = {
    SearchFamily.WAF_COLLECTION: "waf_collection_enabled",
    SearchFamily.ANOMALY_EXPLANATION: "explanation_enabled",
    SearchFamily.RULE_MATCH: "rule_match_collection_enabled",
    SearchFamily.IPS_COLLECTION: "ips_collection_enabled",
    SearchFamily.PROPERTY_WATCH: "pw_collection_enabled",
    SearchFamily.CATCHALL_SENDERS: "catchall_collection_enabled",
    SearchFamily.GEO_COLLECTION: "geo_collection_enabled",
}


class InvalidSearchCadenceError(ValueError):
    """A cadence value is not one of the operator-visible presets."""


def bucket_seconds(family: SearchFamily, settings: Settings) -> int | None:
    """Return the stored bucket width a search family collects, if it has one."""
    if family in {
        SearchFamily.PHASE_A_VOLUME,
        SearchFamily.WAF_COLLECTION,
        SearchFamily.IPS_COLLECTION,
        SearchFamily.PROPERTY_WATCH,
        SearchFamily.CATCHALL_SENDERS,
        SearchFamily.GEO_COLLECTION,
    }:
        return settings.collection_interval_seconds
    if family == SearchFamily.RULE_MATCH:
        return settings.rule_match_window_seconds
    return None


def family_enabled(family: SearchFamily, settings: Settings) -> bool:
    """Whether Beat may dispatch this family under the current settings."""
    setting = _FAMILY_SWITCHES.get(family)
    return True if setting is None else bool(getattr(settings, setting))


def slot_start(now: datetime, cadence_seconds: int, settle_seconds: int) -> datetime:
    """Return the settled epoch-aligned cadence slot containing ``now``."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    adjusted = now.astimezone(UTC) - timedelta(seconds=settle_seconds)
    epoch_seconds = math.floor(adjusted.timestamp() / cadence_seconds) * cadence_seconds
    return datetime.fromtimestamp(epoch_seconds, tz=UTC)


class SearchCadenceService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def list(self) -> list[SearchCadenceSetting]:
        stored = {
            SearchFamily(row.family): row
            for row in (
                await self.session.scalars(
                    select(SearchCadenceSetting).order_by(SearchCadenceSetting.family)
                )
            ).all()
        }
        return [stored.get(family) or self._missing_default(family) for family in SearchFamily]

    async def get(self, family: SearchFamily) -> SearchCadenceSetting:
        row = await self.session.get(SearchCadenceSetting, family.value)
        return row or self._missing_default(family)

    async def claim_slot(self, family: SearchFamily) -> datetime | None:
        cadence_seconds = await self.session.scalar(
            select(SearchCadenceSetting.cadence_seconds).where(
                SearchCadenceSetting.family == family.value
            )
        )
        if cadence_seconds is None:
            logger.warning(
                "search cadence seed row missing; restoring default",
                extra={"family": family.value},
            )
            cadence_seconds = int(default_search_cadence(family))
            await self.session.execute(
                insert(SearchCadenceSetting)
                .values(
                    family=family.value,
                    cadence_seconds=cadence_seconds,
                    updated_at=self._clock(),
                )
                .on_conflict_do_nothing(index_elements=[SearchCadenceSetting.family])
            )

        slot = slot_start(
            self._clock(), cadence_seconds, self.settings.search_cadence_settle_seconds
        )
        return await self.session.scalar(
            update(SearchCadenceSetting)
            .where(
                SearchCadenceSetting.family == family.value,
                (SearchCadenceSetting.last_slot_start.is_(None))
                | (SearchCadenceSetting.last_slot_start < slot),
            )
            .values(last_slot_start=slot)
            .returning(SearchCadenceSetting.last_slot_start)
        )

    async def set_cadence(
        self,
        family: SearchFamily,
        cadence_seconds: int,
        *,
        actor: str,
        source_ip: str | None,
    ) -> SearchCadenceSetting:
        try:
            cadence = SearchCadence(cadence_seconds)
        except ValueError as exc:
            raise InvalidSearchCadenceError(
                f"unsupported search cadence: {cadence_seconds}"
            ) from exc

        row = await self.get(family)
        old = row.cadence_seconds
        if old == int(cadence):
            return row

        if row not in self.session:
            self.session.add(row)
        row.cadence_seconds = int(cadence)
        row.updated_at = self._clock()
        row.updated_by = actor
        await self.session.flush()
        await record_audit(
            self.session,
            action="search_cadence.update",
            actor=actor,
            object_type="search_cadence",
            object_id=family.value,
            detail={"old": old, "new": int(cadence)},
            source_ip=source_ip,
        )
        return row

    async def record_run(
        self,
        family: SearchFamily,
        *,
        outcome: str,
        searches: int | None,
        buckets: int | None,
        splits: int | None,
    ) -> None:
        row = await self.get(family)
        if row not in self.session:
            self.session.add(row)
        row.last_run_at = self._clock()
        row.last_run_outcome = outcome
        row.last_run_searches = searches
        row.last_run_buckets = buckets
        row.last_run_splits = splits
        await self.session.flush()

    def next_dispatch_at(self, row: SearchCadenceSetting, now: datetime) -> datetime:
        current = slot_start(
            now, row.cadence_seconds, self.settings.search_cadence_settle_seconds
        )
        if row.last_slot_start is None:
            next_slot = current
        else:
            claimed = row.last_slot_start
            if claimed.tzinfo is None or claimed.utcoffset() is None:
                claimed = claimed.replace(tzinfo=UTC)
            else:
                claimed = claimed.astimezone(UTC)
            # A cadence may have changed since this slot was claimed. Advance
            # from the new cadence's epoch-aligned grid so this timestamp is
            # the first slot the claim UPDATE's strict `< slot` accepts.
            first_after_claimed = slot_start(claimed, row.cadence_seconds, 0) + timedelta(
                seconds=row.cadence_seconds
            )
            next_slot = max(first_after_claimed, current)
        return next_slot + timedelta(seconds=self.settings.search_cadence_settle_seconds)

    def _missing_default(self, family: SearchFamily) -> SearchCadenceSetting:
        logger.warning(
            "search cadence seed row missing; using default",
            extra={"family": family.value},
        )
        return SearchCadenceSetting(
            family=family.value,
            cadence_seconds=int(default_search_cadence(family)),
            updated_at=self._clock(),
        )
