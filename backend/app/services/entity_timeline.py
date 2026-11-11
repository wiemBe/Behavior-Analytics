"""Bounded read service for one stored entity timeline.

The service never calls a provider, never runs AQL and never writes.  With an
explicit instance id and a fully authorised caller its SQL cost is exactly
``3 + H`` statements: one ``entity_mention`` range read (``limit + 1``), one
offense-watermark read through :class:`RelatedActivityRepository`, one combined
collection-coverage read, and one IN-list hydrate for each of the ``H`` distinct
supported ``ref_table`` values on the page.  The coverage statement also reads
``entity_mention_backfill``; that does not add a statement.  The count is
independent of the window size, number of sources and number of mentions.  A
caller forbidden from the offense or fleet lane executes neither that lane's
watermark/hydrate read nor a matching inventory subquery.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from sqlalchemy import and_, exists, false, func, literal, literal_column, or_, select, tuple_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.history.origin import is_live_clause
from app.i18n.messages import MessageRef
from app.models.catchall import CatchallBucket
from app.models.entity import EntityMention, EntityMentionBackfill
from app.models.enums import (
    DimensionAvailability,
    EvidenceStatus,
    PwBucketMeasurement,
    PwPinnedMeasurement,
    Severity,
    WafTelemetryStatus,
)
from app.models.explanation import (
    AnomalyExplanation,
    AnomalyExplanationContributor,
    AnomalyExplanationDimension,
)
from app.models.ips import IpsEntity, IpsSourceBucket
from app.models.log_source import LogSource, LogSourceAnomaly
from app.models.offense import OffenseSnapshot
from app.models.property_watch import PropertyWatch, PwBucket, PwValue, PwValueMetric
from app.models.waf import WafEndpoint, WafEndpointMetric, WafSourceBucket
from app.repositories.related_activity import RelatedActivityRepository
from app.schemas.entity_timeline import (
    EntityOut,
    EntityTimelineItemOut,
    EntityTimelineKind,
    EntityTimelineLane,
    EntityTimelineLaneOut,
    EntityTimelineOut,
    EntityWindowOut,
)
from app.schemas.messages import stored_message
from app.schemas.related_activity import RelatedSectionState
from app.security.rbac import (
    PERM_FLEET_READ,
    PERM_OFFENSE_READ,
    PERM_PROPERTY_WATCH_READ,
    Principal,
)
from app.security.sanitizer import sanitize_text
from app.services.entity_mentions import classify_entity, normalize_entity, user_match_forms
from app.services.related_activity import offense_collection_state

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
DEFAULT_WINDOW = timedelta(hours=24)
MAX_WINDOW = timedelta(days=7)

REASON_PAGE_TRUNCATED = "entity_timeline.reason.page_truncated"
REASON_PAGE_PARTIAL = "entity_timeline.reason.partial"
REASON_PAGE_NOT_COLLECTED = "entity_timeline.reason.not_collected"
REASON_FAMILY_OFF = "entity_timeline.reason.family_off"
REASON_NO_COVERAGE = "entity_timeline.reason.no_coverage"
REASON_BOUNDED = "entity_timeline.reason.not_among_stored_top"
REASON_OFFENSE_NOT_COLLECTED = "entity_timeline.reason.offense_not_collected"
REASON_OFFENSE_STALE = "entity_timeline.reason.offense_stale"
REASON_PERMISSION = "entity_timeline.reason.permission"
REASON_INDEX_INCOMPLETE = "entity_timeline.reason.index_incomplete"

_SECOND: Any = literal_column("INTERVAL '1 second'")
_SUPPORTED_REF_TABLES = {
    "anomaly_explanation_contributor": EntityTimelineLane.EXPLANATION,
    "waf_endpoint_metric": EntityTimelineLane.WAF_TOP,
    "ips_source_bucket": EntityTimelineLane.IPS_TOP,
    "ips_entity": EntityTimelineLane.IPS_ENTITY,
    "offense_snapshot": EntityTimelineLane.OFFENSE,
    "log_source": EntityTimelineLane.LOG_SOURCE,
    "catchall_bucket": EntityTimelineLane.CATCHALL,
    "pw_value_metric": EntityTimelineLane.PW_VALUE,
}
_LANES = tuple(EntityTimelineLane)


class EntityTimelineInputError(ValueError):
    """A request value that must become a coded HTTP 422."""

    def __init__(self, ref: MessageRef) -> None:
        self.ref = ref
        super().__init__(ref.code)


@dataclass(frozen=True)
class CursorPosition:
    occurred_at: datetime
    source: str
    ref_id: str


@dataclass(frozen=True)
class LaneEvidence:
    source: EntityTimelineLane
    item_count: int
    permitted: bool = True
    permission: str | None = None
    enabled: bool = True
    collected: bool = True
    complete: bool = True
    proves_absence: bool = False
    freshness: RelatedSectionState = RelatedSectionState.OK
    count_is_total: bool = True
    incomplete_reason: str = REASON_BOUNDED
    dangling_count: int = 0
    # False when this source has no COMPLETE entity_mention_backfill row.
    # Items still render. An empty collected lane is PARTIAL index_incomplete.
    index_complete: bool = True


@dataclass(frozen=True)
class Coverage:
    explanation_collected: bool
    explanation_proves_absence: bool
    waf_collected: bool
    waf_proves_absence: bool
    ips_top_collected: bool
    ips_top_complete: bool
    ips_entity_collected: bool
    ips_entity_complete: bool
    log_source_match: bool
    catchall_collected: bool
    catchall_proves_absence: bool
    pw_collected: bool
    pw_proves_absence: bool
    explanation_index_complete: bool
    waf_index_complete: bool
    ips_top_index_complete: bool
    ips_entity_index_complete: bool
    offense_index_complete: bool
    catchall_index_complete: bool
    pw_index_complete: bool


@dataclass(frozen=True)
class ParentPresentation:
    title: str
    href: str
    severity: Severity | int | None = None
    measurement: str | None = None
    title_code: str | None = None
    title_params: dict[str, Any] | None = None


class CursorCodec:
    """Signed, URL-safe cursor for the design's three-part ordering key."""

    def __init__(self, secret: str) -> None:
        self._key = secret.encode()

    def encode(self, position: CursorPosition) -> str:
        payload = json.dumps(
            {
                "at": position.occurred_at.astimezone(UTC).isoformat(),
                "ref": position.ref_id,
                "source": position.source,
                "v": 1,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        signature = hmac.new(self._key, payload, hashlib.sha256).digest()
        return f"{_b64(payload)}.{_b64(signature)}"

    def decode(self, token: str) -> CursorPosition:
        try:
            encoded_payload, encoded_signature = token.split(".", 1)
            payload = _unb64(encoded_payload)
            signature = _unb64(encoded_signature)
            expected = hmac.new(self._key, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature")
            value = json.loads(payload)
            if set(value) != {"at", "ref", "source", "v"} or value["v"] != 1:
                raise ValueError("shape")
            occurred_at = datetime.fromisoformat(value["at"])
            if occurred_at.tzinfo is None:
                raise ValueError("timezone")
            if not isinstance(value["source"], str) or not isinstance(value["ref"], str):
                raise ValueError("types")
            if value["source"] not in {lane.value for lane in _LANES}:
                raise ValueError("source")
            return CursorPosition(occurred_at, value["source"], value["ref"])
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise EntityTimelineInputError(
                MessageRef("entity_timeline.input.cursor_invalid")
            ) from exc


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    decoded = base64.b64decode(
        value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
    )
    if _b64(decoded) != value:
        raise ValueError("non-canonical base64")
    return decoded


def validate_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_LIMIT:
        raise EntityTimelineInputError(
            MessageRef(
                "entity_timeline.input.limit",
                {"minimum": 1, "maximum": MAX_LIMIT},
            )
        )
    return limit


def window_bounds(
    start: datetime | None,
    end: datetime | None,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    resolved_end = _aware(end or now or datetime.now(UTC))
    resolved_start = _aware(start) if start is not None else resolved_end - DEFAULT_WINDOW
    if resolved_start >= resolved_end:
        raise EntityTimelineInputError(MessageRef("entity_timeline.input.window_order"))
    if resolved_end - resolved_start > MAX_WINDOW:
        raise EntityTimelineInputError(
            MessageRef("entity_timeline.input.window_max", {"days": MAX_WINDOW.days})
        )
    return resolved_start, resolved_end


def resolve_entity(kind: str | None, value: str) -> tuple[EntityTimelineKind, str, str]:
    cleaned = (sanitize_text(value) or "").strip()
    resolved = kind.upper() if kind is not None else classify_entity(cleaned)
    if resolved is None:
        raise EntityTimelineInputError(MessageRef("entity_timeline.input.kind_required"))
    if resolved not in {item.value for item in EntityTimelineKind}:
        raise EntityTimelineInputError(MessageRef("entity_timeline.input.kind_invalid"))
    value_norm = normalize_entity(resolved, cleaned)
    if value_norm is None:
        raise EntityTimelineInputError(MessageRef("entity_timeline.input.value_invalid"))
    return EntityTimelineKind(resolved), cleaned, value_norm


def decide_lane(evidence: LaneEvidence) -> EntityTimelineLaneOut:
    """Fold permissions, coverage and boundedness into one lane honesty state.

    ``index_complete`` is false when that source's mention backfill row is missing
    or not COMPLETE. Items still render. A collected empty lane is then PARTIAL
    ``index_incomplete`` and never an empty OK. A lane with no measurement stays
    not collected.
    """
    if not evidence.permitted:
        return EntityTimelineLaneOut(
            source=evidence.source,
            state=RelatedSectionState.NOT_COLLECTED,
            reason_code=REASON_PERMISSION,
            permission=evidence.permission,
        )
    if evidence.freshness is RelatedSectionState.NOT_COLLECTED:
        return EntityTimelineLaneOut(
            source=evidence.source,
            state=RelatedSectionState.NOT_COLLECTED,
            reason_code=REASON_OFFENSE_NOT_COLLECTED,
        )
    if evidence.freshness is RelatedSectionState.STALE:
        return EntityTimelineLaneOut(
            source=evidence.source,
            state=RelatedSectionState.STALE,
            reason_code=REASON_OFFENSE_STALE,
            total=(
                evidence.item_count
                if evidence.item_count and evidence.count_is_total
                else None
            ),
        )
    if evidence.dangling_count:
        return EntityTimelineLaneOut(
            source=evidence.source,
            state=RelatedSectionState.PARTIAL,
            reason_code=REASON_INDEX_INCOMPLETE,
        )
    if evidence.item_count:
        if (
            evidence.index_complete
            and evidence.enabled
            and evidence.collected
            and evidence.complete
        ):
            return EntityTimelineLaneOut(
                source=evidence.source,
                state=RelatedSectionState.OK,
                total=evidence.item_count if evidence.count_is_total else None,
            )
        if evidence.enabled and evidence.collected and not evidence.index_complete:
            reason = REASON_INDEX_INCOMPLETE
        else:
            reason = REASON_FAMILY_OFF if not evidence.enabled else REASON_NO_COVERAGE
        return EntityTimelineLaneOut(
            source=evidence.source,
            state=RelatedSectionState.PARTIAL,
            reason_code=reason,
            total=evidence.item_count if evidence.count_is_total else None,
        )
    if not evidence.enabled:
        return EntityTimelineLaneOut(
            source=evidence.source,
            state=RelatedSectionState.NOT_COLLECTED,
            reason_code=REASON_FAMILY_OFF,
        )
    if not evidence.collected:
        return EntityTimelineLaneOut(
            source=evidence.source,
            state=RelatedSectionState.NOT_COLLECTED,
            reason_code=REASON_NO_COVERAGE,
        )
    if not evidence.index_complete:
        return EntityTimelineLaneOut(
            source=evidence.source,
            state=RelatedSectionState.PARTIAL,
            reason_code=REASON_INDEX_INCOMPLETE,
        )
    if evidence.proves_absence:
        return EntityTimelineLaneOut(
            source=evidence.source,
            state=RelatedSectionState.OK,
            total=0,
        )
    return EntityTimelineLaneOut(
        source=evidence.source,
        state=RelatedSectionState.PARTIAL,
        reason_code=evidence.incomplete_reason,
    )


def page_state(
    lanes: Sequence[EntityTimelineLaneOut], *, truncated: bool
) -> tuple[RelatedSectionState, str | None]:
    if truncated:
        return RelatedSectionState.TRUNCATED, REASON_PAGE_TRUNCATED
    if all(lane.state is RelatedSectionState.NOT_COLLECTED for lane in lanes):
        return RelatedSectionState.NOT_COLLECTED, REASON_PAGE_NOT_COLLECTED
    if any(lane.state is not RelatedSectionState.OK for lane in lanes):
        return RelatedSectionState.PARTIAL, REASON_PAGE_PARTIAL
    return RelatedSectionState.OK, None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class EntityTimelineService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        principal: Principal,
        *,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.principal = principal
        self.now = now or datetime.now(UTC)
        self.cursor_codec = CursorCodec(settings.secret_key.get_secret_value())

    async def read(
        self,
        *,
        instance_id: uuid.UUID,
        kind: EntityTimelineKind,
        value: str,
        value_norm: str,
        start: datetime,
        end: datetime,
        limit: int,
        cursor: str | None,
    ) -> EntityTimelineOut:
        position = self.cursor_codec.decode(cursor) if cursor is not None else None
        offense_allowed = self.principal.has(PERM_OFFENSE_READ)
        fleet_allowed = self.principal.has(PERM_FLEET_READ)
        property_watch_allowed = self.principal.has(PERM_PROPERTY_WATCH_READ)
        allowed_sources = [
            EntityTimelineLane.EXPLANATION.value,
            EntityTimelineLane.WAF_TOP.value,
            EntityTimelineLane.IPS_TOP.value,
            EntityTimelineLane.IPS_ENTITY.value,
        ]
        if offense_allowed:
            allowed_sources.append(EntityTimelineLane.OFFENSE.value)
        if fleet_allowed:
            allowed_sources.append(EntityTimelineLane.LOG_SOURCE.value)
            allowed_sources.append(EntityTimelineLane.CATCHALL.value)
        if property_watch_allowed:
            allowed_sources.append(EntityTimelineLane.PW_VALUE.value)

        forms = user_match_forms(value) if kind is EntityTimelineKind.USER else (value_norm,)
        mentions = await self._mentions(
            instance_id=instance_id,
            kind=kind,
            forms=forms,
            start=start,
            end=end,
            allowed_sources=allowed_sources,
            limit=limit,
            cursor=position,
        )
        truncated = len(mentions) > limit
        page_mentions = mentions[:limit]

        watermark = None
        if offense_allowed:
            watermark = await RelatedActivityRepository(self.session).offense_watermark(
                instance_id
            )
        offense_freshness = (
            offense_collection_state(
                last_run_at=watermark.last_run_at if watermark is not None else None,
                consecutive_failures=(
                    watermark.consecutive_failures if watermark is not None else 0
                ),
                now=self.now,
                stale_after_seconds=self.settings.offense_stale_after_seconds,
            )
            if offense_allowed
            else RelatedSectionState.NOT_COLLECTED
        )
        coverage = await self._coverage(
            instance_id=instance_id,
            kind=kind,
            value_norm=value_norm,
            start=start,
            end=end,
            include_log_source=fleet_allowed,
            include_catchall=fleet_allowed,
            include_property_watch=property_watch_allowed,
        )

        presentations = await self._hydrate(instance_id, page_mentions)
        items: list[EntityTimelineItemOut] = []
        dangling = 0
        item_counts: Counter[EntityTimelineLane] = Counter()
        dangling_counts: Counter[EntityTimelineLane] = Counter()
        for mention in page_mentions:
            lane = EntityTimelineLane(mention.source)
            expected = _SUPPORTED_REF_TABLES.get(mention.ref_table)
            presentation = (
                presentations.get(_mention_key(mention)) if expected is lane else None
            )
            if presentation is None:
                dangling += 1
                dangling_counts[lane] += 1
                continue
            item_counts[lane] += 1
            items.append(
                EntityTimelineItemOut(
                    occurred_at=mention.occurred_at,
                    occurred_end=mention.occurred_end,
                    source=lane,
                    role=mention.role,
                    title=presentation.title,
                    title_code=presentation.title_code,
                    title_params=presentation.title_params,
                    href=presentation.href,
                    severity=presentation.severity,
                    measurement=presentation.measurement,
                )
            )

        lanes = self._lanes(
            kind=kind,
            counts=item_counts,
            dangling_counts=dangling_counts,
            coverage=coverage,
            offense_allowed=offense_allowed,
            fleet_allowed=fleet_allowed,
            property_watch_allowed=property_watch_allowed,
            offense_freshness=offense_freshness,
            truncated=truncated,
        )
        state, reason = page_state(lanes, truncated=truncated)
        next_cursor = None
        if truncated and page_mentions:
            last = page_mentions[-1]
            next_cursor = self.cursor_codec.encode(
                CursorPosition(last.occurred_at, last.source, last.ref_id)
            )
        return EntityTimelineOut(
            entity=EntityOut(kind=kind, value=value, value_norm=value_norm),
            window=EntityWindowOut(start=start, end=end),
            state=state,
            reason_code=reason,
            lanes=lanes,
            items=items,
            dangling=dangling,
            truncated=truncated,
            next_cursor=next_cursor,
        )

    async def _mentions(
        self,
        *,
        instance_id: uuid.UUID,
        kind: EntityTimelineKind,
        forms: Sequence[str],
        start: datetime,
        end: datetime,
        allowed_sources: Sequence[str],
        limit: int,
        cursor: CursorPosition | None,
    ) -> list[EntityMention]:
        conditions = [
            EntityMention.instance_id == instance_id,
            EntityMention.kind == kind.value,
            EntityMention.value_norm.in_(forms),
            EntityMention.occurred_at >= start,
            EntityMention.occurred_at < end,
            EntityMention.source.in_(allowed_sources),
        ]
        if cursor is not None:
            conditions.append(
                or_(
                    EntityMention.occurred_at < cursor.occurred_at,
                    and_(
                        EntityMention.occurred_at == cursor.occurred_at,
                        EntityMention.source > cursor.source,
                    ),
                    and_(
                        EntityMention.occurred_at == cursor.occurred_at,
                        EntityMention.source == cursor.source,
                        EntityMention.ref_id > cursor.ref_id,
                    ),
                )
            )
        rows = await self.session.scalars(
            select(EntityMention)
            .where(*conditions)
            .order_by(
                EntityMention.occurred_at.desc(),
                EntityMention.source.asc(),
                EntityMention.ref_id.asc(),
            )
            .limit(limit + 1)
        )
        return list(rows.all())

    async def _coverage(
        self,
        *,
        instance_id: uuid.UUID,
        kind: EntityTimelineKind,
        value_norm: str,
        start: datetime,
        end: datetime,
        include_log_source: bool,
        include_catchall: bool,
        include_property_watch: bool,
    ) -> Coverage:
        candidate_dimensions = {
            EntityTimelineKind.IP: ("source_ip", "destination_ip"),
            EntityTimelineKind.USER: ("username",),
            EntityTimelineKind.HOST: (),
        }[kind]
        dimensions = tuple(
            dimension
            for dimension in candidate_dimensions
            if dimension in self.settings.explanation_dimensions
        )
        explanation_packages = (
            select(AnomalyExplanation.id)
            .join(LogSourceAnomaly, LogSourceAnomaly.id == AnomalyExplanation.anomaly_id)
            .join(LogSource, LogSource.id == LogSourceAnomaly.log_source_id)
            .where(
                LogSource.instance_id == instance_id,
                AnomalyExplanation.anomaly_window_start < end,
                AnomalyExplanation.anomaly_window_end > start,
            )
        )
        explanation_collected: Any
        explanation_proves: Any
        if dimensions:
            relevant_dimension_count = (
                select(func.count(func.distinct(AnomalyExplanationDimension.dimension)))
                .where(
                    AnomalyExplanationDimension.explanation_id == AnomalyExplanation.id,
                    AnomalyExplanationDimension.dimension.in_(dimensions),
                )
                .correlate(AnomalyExplanation)
                .scalar_subquery()
            )
            bad_dimension = exists(
                select(AnomalyExplanationDimension.id).where(
                    AnomalyExplanationDimension.explanation_id == AnomalyExplanation.id,
                    AnomalyExplanationDimension.dimension.in_(dimensions),
                    or_(
                        AnomalyExplanationDimension.availability
                        != DimensionAvailability.AVAILABLE,
                        AnomalyExplanationDimension.truncated.is_(True),
                    ),
                )
            )
            explanation_collected = exists(
                explanation_packages.where(relevant_dimension_count > 0)
            )
            bad_explanation = exists(
                explanation_packages.where(
                    or_(
                        AnomalyExplanation.status != EvidenceStatus.COMPLETE,
                        relevant_dimension_count < len(dimensions),
                        bad_dimension,
                    )
                )
            )
            explanation_proves = and_(explanation_collected, ~bad_explanation)
        else:
            explanation_collected = literal(False)
            explanation_proves = literal(False)

        # Stored buckets of both origins count for WAF and IPS once that source's
        # mention backfill is COMPLETE. Volume-only WAF history records the bucket
        # with client_measurement NOT_COLLECTED and no client list; that is not a
        # collected client measurement (D-64), so it cannot prove absence.
        waf_window = (
            WafSourceBucket.bucket_start < end,
            WafSourceBucket.bucket_start + WafSourceBucket.bucket_seconds * _SECOND > start,
        )
        waf_collected = (
            exists(
                select(WafSourceBucket.log_source_id)
                .join(LogSource, LogSource.id == WafSourceBucket.log_source_id)
                .where(
                    LogSource.instance_id == instance_id,
                    *waf_window,
                    WafSourceBucket.client_measurement
                    != WafTelemetryStatus.NOT_COLLECTED,
                )
            )
            if kind is EntityTimelineKind.IP
            else literal(False)
        )
        waf_bad_bucket = exists(
            select(WafSourceBucket.log_source_id)
            .join(LogSource, LogSource.id == WafSourceBucket.log_source_id)
            .where(
                LogSource.instance_id == instance_id,
                *waf_window,
                WafSourceBucket.client_measurement != WafTelemetryStatus.EXACT,
            )
        )
        # Metrics carry collection_source, not origin. Join the source bucket so a
        # metric counts only with the bucket it belongs to, live or history.
        waf_bad_metric = exists(
            select(WafEndpointMetric.waf_endpoint_id)
            .join(WafEndpoint, WafEndpoint.id == WafEndpointMetric.waf_endpoint_id)
            .join(
                WafSourceBucket,
                and_(
                    WafSourceBucket.log_source_id == WafEndpoint.log_source_id,
                    WafSourceBucket.bucket_start == WafEndpointMetric.bucket_start,
                ),
            )
            .where(
                WafEndpoint.instance_id == instance_id,
                WafEndpointMetric.bucket_start < end,
                WafEndpointMetric.bucket_start
                + WafEndpointMetric.bucket_seconds * _SECOND
                > start,
                or_(
                    WafEndpointMetric.client_sources.is_(None),
                    WafEndpointMetric.top_clients.is_(None),
                    WafEndpointMetric.client_sources
                    > func.jsonb_array_length(WafEndpointMetric.top_clients),
                ),
            )
        )

        ips_window = (
            IpsSourceBucket.bucket_start < end,
            IpsSourceBucket.bucket_start + IpsSourceBucket.bucket_seconds * _SECOND > start,
        )
        ips_top_collected = (
            exists(
                select(IpsSourceBucket.log_source_id)
                .join(LogSource, LogSource.id == IpsSourceBucket.log_source_id)
                .where(
                    LogSource.instance_id == instance_id,
                    *ips_window,
                    IpsSourceBucket.source_ip_measurement == "EXACT",
                )
            )
            if kind is EntityTimelineKind.IP
            else literal(False)
        )
        ips_top_incomplete = exists(
            select(IpsSourceBucket.log_source_id)
            .join(LogSource, LogSource.id == IpsSourceBucket.log_source_id)
            .where(
                LogSource.instance_id == instance_id,
                *ips_window,
                IpsSourceBucket.source_ip_measurement != "EXACT",
            )
        )
        ips_entity_collected = (
            exists(
                select(IpsSourceBucket.log_source_id)
                .join(LogSource, LogSource.id == IpsSourceBucket.log_source_id)
                .where(
                    LogSource.instance_id == instance_id,
                    *ips_window,
                    IpsSourceBucket.destination_ip_measurement == "EXACT",
                )
            )
            if kind is EntityTimelineKind.IP
            else literal(False)
        )
        ips_entity_incomplete = exists(
            select(IpsSourceBucket.log_source_id)
            .join(LogSource, LogSource.id == IpsSourceBucket.log_source_id)
            .where(
                LogSource.instance_id == instance_id,
                *ips_window,
                IpsSourceBucket.destination_ip_measurement != "EXACT",
            )
        )

        log_match: Any = literal(False)
        if include_log_source and kind in (EntityTimelineKind.IP, EntityTimelineKind.HOST):
            candidates = [func.lower(func.rtrim(LogSource.identifier, ".")) == value_norm]
            if kind is EntityTimelineKind.HOST:
                candidates.append(func.lower(func.rtrim(LogSource.name, ".")) == value_norm)
            log_match = exists(
                select(LogSource.id).where(
                    LogSource.instance_id == instance_id,
                    or_(*candidates),
                )
            )

        catchall_collected: Any = literal(False)
        catchall_proves: Any = literal(False)
        if include_catchall and kind in (EntityTimelineKind.IP, EntityTimelineKind.HOST):
            catchall_window = (
                is_live_clause(CatchallBucket),
                CatchallBucket.bucket_start < end,
                CatchallBucket.bucket_start
                + CatchallBucket.bucket_seconds * _SECOND
                > start,
            )
            catchall_buckets = (
                select(CatchallBucket.catchall_log_source_id)
                .join(
                    LogSource,
                    LogSource.id == CatchallBucket.catchall_log_source_id,
                )
                .where(LogSource.instance_id == instance_id, *catchall_window)
            )
            catchall_collected = exists(catchall_buckets)
            catchall_bad = exists(
                catchall_buckets.where(
                    or_(
                        CatchallBucket.measurement != "EXACT",
                        CatchallBucket.distinct_senders_lower_bound.is_(True),
                        CatchallBucket.untracked_senders > 0,
                        CatchallBucket.distinct_senders
                        > func.jsonb_array_length(CatchallBucket.top_senders),
                    )
                )
            )
            catchall_proves = and_(catchall_collected, ~catchall_bad)

        pw_collected: Any = literal(False)
        pw_proves: Any = literal(False)
        property_names = {
            EntityTimelineKind.IP: ("sourceip", "destinationip"),
            EntityTimelineKind.USER: ("username",),
            EntityTimelineKind.HOST: (),
        }[kind]
        if include_property_watch and property_names:
            property_name = func.lower(
                func.btrim(PropertyWatch.properties[0]["name"].as_string())
            )
            pw_window = (
                PropertyWatch.instance_id == instance_id,
                PropertyWatch.enabled.is_(True),
                func.jsonb_array_length(PropertyWatch.properties) == 1,
                property_name.in_(property_names),
                PwBucket.watch_version == PropertyWatch.watch_version,
                is_live_clause(PwBucket),
                PwBucket.bucket_start < end,
                PwBucket.bucket_start + PwBucket.bucket_seconds * _SECOND > start,
            )
            pw_buckets = (
                select(PwBucket.watch_id)
                .join(PropertyWatch, PropertyWatch.id == PwBucket.watch_id)
                .where(*pw_window)
            )
            pw_collected = exists(
                pw_buckets.where(
                    PwBucket.values_measurement != PwBucketMeasurement.NOT_COLLECTED.value
                )
            )
            pw_bad = exists(
                pw_buckets.where(
                    or_(
                        PwBucket.values_measurement != PwBucketMeasurement.EXACT.value,
                        PwBucket.admission_truncated.is_(True),
                        PwBucket.untracked_events.is_(None),
                        PwBucket.untracked_events > 0,
                        PwBucket.pinned_measurement.not_in(
                            (
                                PwPinnedMeasurement.EXACT.value,
                                PwPinnedMeasurement.NOT_REQUESTED.value,
                            )
                        ),
                    )
                )
            )
            pw_proves = and_(pw_collected, ~pw_bad)

        def backfill_complete(source: str) -> Any:
            return exists(
                select(EntityMentionBackfill.instance_id).where(
                    EntityMentionBackfill.instance_id == instance_id,
                    EntityMentionBackfill.source == source,
                    EntityMentionBackfill.status == "COMPLETE",
                )
            )

        explanation_index = backfill_complete(EntityTimelineLane.EXPLANATION.value)
        waf_index = backfill_complete(EntityTimelineLane.WAF_TOP.value)
        ips_top_index = backfill_complete(EntityTimelineLane.IPS_TOP.value)
        ips_entity_index = backfill_complete(EntityTimelineLane.IPS_ENTITY.value)
        offense_index = backfill_complete(EntityTimelineLane.OFFENSE.value)
        catchall_index = backfill_complete(EntityTimelineLane.CATCHALL.value)
        pw_index = backfill_complete(EntityTimelineLane.PW_VALUE.value)

        row = (
            await self.session.execute(
                select(
                    explanation_collected.label("explanation_collected"),
                    and_(explanation_proves, explanation_index).label(
                        "explanation_proves"
                    ),
                    waf_collected.label("waf_collected"),
                    and_(
                        waf_collected, ~waf_bad_bucket, ~waf_bad_metric, waf_index
                    ).label("waf_proves"),
                    ips_top_collected.label("ips_top_collected"),
                    and_(
                        ips_top_collected, ~ips_top_incomplete, ips_top_index
                    ).label("ips_top_complete"),
                    ips_entity_collected.label("ips_entity_collected"),
                    and_(
                        ips_entity_collected, ~ips_entity_incomplete, ips_entity_index
                    ).label("ips_entity_complete"),
                    log_match.label("log_match"),
                    catchall_collected.label("catchall_collected"),
                    and_(catchall_proves, catchall_index).label("catchall_proves"),
                    pw_collected.label("pw_collected"),
                    and_(pw_proves, pw_index).label("pw_proves"),
                    explanation_index.label("explanation_index"),
                    waf_index.label("waf_index"),
                    ips_top_index.label("ips_top_index"),
                    ips_entity_index.label("ips_entity_index"),
                    offense_index.label("offense_index"),
                    catchall_index.label("catchall_index"),
                    pw_index.label("pw_index"),
                )
            )
        ).one()
        return Coverage(*(bool(value) for value in row))

    async def _hydrate(
        self, instance_id: uuid.UUID, mentions: Sequence[EntityMention]
    ) -> dict[tuple[str, str, datetime], ParentPresentation]:
        grouped: dict[str, list[EntityMention]] = defaultdict(list)
        for mention in mentions:
            if _SUPPORTED_REF_TABLES.get(mention.ref_table) is EntityTimelineLane(
                mention.source
            ):
                grouped[mention.ref_table].append(mention)

        out: dict[tuple[str, str, datetime], ParentPresentation] = {}
        for ref_table, rows in grouped.items():
            loader = getattr(self, f"_hydrate_{ref_table}")
            out.update(await loader(instance_id, rows))
        return out

    async def _hydrate_anomaly_explanation_contributor(
        self, instance_id: uuid.UUID, mentions: Sequence[EntityMention]
    ) -> dict[tuple[str, str, datetime], ParentPresentation]:
        ids = _uuid_values(mentions)
        rows = (
            await self.session.execute(
                select(
                    AnomalyExplanationContributor,
                    AnomalyExplanation,
                    LogSourceAnomaly,
                )
                .join(
                    AnomalyExplanation,
                    AnomalyExplanation.id
                    == AnomalyExplanationContributor.explanation_id,
                )
                .join(LogSourceAnomaly, LogSourceAnomaly.id == AnomalyExplanation.anomaly_id)
                .join(LogSource, LogSource.id == LogSourceAnomaly.log_source_id)
                .where(
                    LogSource.instance_id == instance_id,
                    AnomalyExplanationContributor.id.in_(ids) if ids else false(),
                )
            )
        ).all()
        by_id: dict[str, ParentPresentation] = {}
        for contributor, explanation, anomaly in rows:
            message = stored_message(anomaly.explanation_i18n)
            title = sanitize_text(anomaly.explanation) or (
                f"{contributor.dimension} contributor in behavior anomaly"
            )
            by_id[str(contributor.id)] = ParentPresentation(
                title=title,
                href=f"/anomalies/{explanation.anomaly_id}",
                severity=Severity(anomaly.severity),
                measurement="TOP_N",
                title_code=message.code if message is not None else None,
                title_params=message.params if message is not None else None,
            )
        return _presentations_for_mentions(mentions, by_id)

    async def _hydrate_waf_endpoint_metric(
        self, instance_id: uuid.UUID, mentions: Sequence[EntityMention]
    ) -> dict[tuple[str, str, datetime], ParentPresentation]:
        pairs = _uuid_time_pairs(mentions)
        rows = (
            await self.session.execute(
                select(WafEndpointMetric, WafEndpoint)
                .join(WafEndpoint, WafEndpoint.id == WafEndpointMetric.waf_endpoint_id)
                .where(
                    WafEndpoint.instance_id == instance_id,
                    tuple_(
                        WafEndpointMetric.waf_endpoint_id,
                        WafEndpointMetric.bucket_start,
                    ).in_(pairs)
                    if pairs
                    else false()
                )
            )
        ).all()
        by_pair: dict[tuple[str, datetime], ParentPresentation] = {}
        for metric, endpoint in rows:
            label = sanitize_text(
                f"WAF top client for {endpoint.http_method} {endpoint.normalized_endpoint}"
            ) or "WAF top client"
            by_pair[(str(endpoint.id), metric.bucket_start)] = ParentPresentation(
                title=label,
                href=f"/behavior/waf/sources/{endpoint.log_source_id}",
                measurement="TOP_N",
            )
        return _presentations_for_pairs(mentions, by_pair)

    async def _hydrate_ips_source_bucket(
        self, instance_id: uuid.UUID, mentions: Sequence[EntityMention]
    ) -> dict[tuple[str, str, datetime], ParentPresentation]:
        pairs = _uuid_time_pairs(mentions)
        rows = (
            await self.session.execute(
                select(IpsSourceBucket, LogSource)
                .join(LogSource, LogSource.id == IpsSourceBucket.log_source_id)
                .where(
                    LogSource.instance_id == instance_id,
                    tuple_(IpsSourceBucket.log_source_id, IpsSourceBucket.bucket_start).in_(
                        pairs
                    )
                    if pairs
                    else false()
                )
            )
        ).all()
        by_pair: dict[tuple[str, datetime], ParentPresentation] = {}
        for bucket, source in rows:
            name = sanitize_text(source.name) or "IPS source"
            by_pair[(str(source.id), bucket.bucket_start)] = ParentPresentation(
                title=f"IPS source contributor on {name}",
                href=(
                    "/anomalies?subject_kind=IPS_SOURCE&log_source_id="
                    f"{source.id}"
                ),
                measurement="TOP_N",
            )
        return _presentations_for_pairs(mentions, by_pair)

    async def _hydrate_ips_entity(
        self, instance_id: uuid.UUID, mentions: Sequence[EntityMention]
    ) -> dict[tuple[str, str, datetime], ParentPresentation]:
        ids = _uuid_values(mentions)
        rows = (
            await self.session.execute(
                select(IpsEntity, LogSource)
                .join(LogSource, LogSource.id == IpsEntity.log_source_id)
                .where(
                    IpsEntity.instance_id == instance_id,
                    IpsEntity.id.in_(ids) if ids else false(),
                )
            )
        ).all()
        by_id: dict[str, ParentPresentation] = {}
        for entity, source in rows:
            label = sanitize_text(entity.display_name or entity.value_key) or entity.value_key
            by_id[str(entity.id)] = ParentPresentation(
                title=f"IPS destination {label} on {sanitize_text(source.name) or 'IPS source'}",
                href=f"/anomalies?subject_kind=IPS_ENTITY&ips_entity_id={entity.id}",
                measurement="ADMITTED",
            )
        return _presentations_for_mentions(mentions, by_id)

    async def _hydrate_offense_snapshot(
        self, instance_id: uuid.UUID, mentions: Sequence[EntityMention]
    ) -> dict[tuple[str, str, datetime], ParentPresentation]:
        ids = _int_values(mentions)
        snapshots = (
            await self.session.scalars(
                select(OffenseSnapshot)
                .ext(
                    distinct_on(
                        OffenseSnapshot.instance_id,
                        OffenseSnapshot.qradar_offense_id,
                    )
                )
                .where(
                    OffenseSnapshot.instance_id == instance_id,
                    OffenseSnapshot.qradar_offense_id.in_(ids) if ids else false(),
                )
                .order_by(
                    OffenseSnapshot.instance_id,
                    OffenseSnapshot.qradar_offense_id,
                    OffenseSnapshot.captured_at.desc(),
                )
            )
        ).all()
        by_id = {
            str(row.qradar_offense_id): ParentPresentation(
                title=sanitize_text(row.description) or f"Offense {row.qradar_offense_id}",
                href=(
                    f"/offenses/{row.qradar_offense_id}?instance_id={row.instance_id}"
                ),
                severity=row.severity,
            )
            for row in snapshots
        }
        return _presentations_for_mentions(mentions, by_id)

    async def _hydrate_log_source(
        self, instance_id: uuid.UUID, mentions: Sequence[EntityMention]
    ) -> dict[tuple[str, str, datetime], ParentPresentation]:
        ids = _uuid_values(mentions)
        sources = (
            await self.session.scalars(
                select(LogSource).where(
                    LogSource.instance_id == instance_id,
                    LogSource.id.in_(ids) if ids else false(),
                )
            )
        ).all()
        by_id = {
            str(source.id): ParentPresentation(
                title=f"Log source {sanitize_text(source.name) or 'unnamed'}",
                href=f"/fleet/sources?q={quote(source.name, safe='')}",
                measurement="EXACT",
            )
            for source in sources
        }
        return _presentations_for_mentions(mentions, by_id)

    async def _hydrate_catchall_bucket(
        self, instance_id: uuid.UUID, mentions: Sequence[EntityMention]
    ) -> dict[tuple[str, str, datetime], ParentPresentation]:
        pairs = _uuid_time_pairs(mentions)
        rows = (
            await self.session.execute(
                select(CatchallBucket, LogSource)
                .join(
                    LogSource,
                    LogSource.id == CatchallBucket.catchall_log_source_id,
                )
                .where(
                    LogSource.instance_id == instance_id,
                    tuple_(
                        CatchallBucket.catchall_log_source_id,
                        CatchallBucket.bucket_start,
                    ).in_(pairs)
                    if pairs
                    else false(),
                )
            )
        ).all()
        by_pair = {
            (str(source.id), bucket.bucket_start): ParentPresentation(
                title=f"Catch-all sender on {sanitize_text(source.name) or 'unnamed source'}",
                href="/fleet/catchall",
                measurement=str(bucket.measurement),
            )
            for bucket, source in rows
        }
        return _presentations_for_pairs(mentions, by_pair)

    async def _hydrate_pw_value_metric(
        self, instance_id: uuid.UUID, mentions: Sequence[EntityMention]
    ) -> dict[tuple[str, str, datetime], ParentPresentation]:
        pairs = _uuid_time_pairs(mentions)
        rows = (
            await self.session.execute(
                select(PwValueMetric, PwValue, PropertyWatch)
                .join(PwValue, PwValue.id == PwValueMetric.pw_value_id)
                .join(PropertyWatch, PropertyWatch.id == PwValue.watch_id)
                .where(
                    PropertyWatch.instance_id == instance_id,
                    tuple_(PwValueMetric.pw_value_id, PwValueMetric.bucket_start).in_(pairs)
                    if pairs
                    else false(),
                )
            )
        ).all()
        by_pair: dict[tuple[str, datetime], ParentPresentation] = {}
        for metric, value, watch in rows:
            watch_name = sanitize_text(watch.name) or "unnamed property watch"
            value_name = sanitize_text(value.value_key) or "stored value"
            by_pair[(str(value.id), metric.bucket_start)] = ParentPresentation(
                title=f"Property watch value {value_name} in {watch_name}",
                href=f"/behavior/watches/{watch.id}",
                measurement="PINNED" if value.pinned else "ADMITTED",
            )
        return _presentations_for_pairs(mentions, by_pair)

    def _lanes(
        self,
        *,
        kind: EntityTimelineKind,
        counts: Mapping[EntityTimelineLane, int],
        dangling_counts: Mapping[EntityTimelineLane, int],
        coverage: Coverage,
        offense_allowed: bool,
        fleet_allowed: bool,
        property_watch_allowed: bool,
        offense_freshness: RelatedSectionState,
        truncated: bool,
    ) -> list[EntityTimelineLaneOut]:
        return [
            decide_lane(
                LaneEvidence(
                    EntityTimelineLane.EXPLANATION,
                    counts.get(EntityTimelineLane.EXPLANATION, 0),
                    enabled=self.settings.explanation_enabled,
                    collected=coverage.explanation_collected,
                    complete=coverage.explanation_proves_absence,
                    proves_absence=coverage.explanation_proves_absence,
                    count_is_total=not truncated,
                    dangling_count=dangling_counts.get(EntityTimelineLane.EXPLANATION, 0),
                    index_complete=coverage.explanation_index_complete,
                )
            ),
            decide_lane(
                LaneEvidence(
                    EntityTimelineLane.WAF_TOP,
                    counts.get(EntityTimelineLane.WAF_TOP, 0),
                    enabled=self.settings.waf_collection_enabled,
                    collected=coverage.waf_collected,
                    complete=coverage.waf_proves_absence,
                    proves_absence=coverage.waf_proves_absence,
                    count_is_total=not truncated,
                    dangling_count=dangling_counts.get(EntityTimelineLane.WAF_TOP, 0),
                    index_complete=coverage.waf_index_complete,
                )
            ),
            decide_lane(
                LaneEvidence(
                    EntityTimelineLane.IPS_TOP,
                    counts.get(EntityTimelineLane.IPS_TOP, 0),
                    enabled=self.settings.ips_collection_enabled,
                    collected=coverage.ips_top_collected,
                    complete=coverage.ips_top_complete,
                    proves_absence=False,
                    count_is_total=not truncated,
                    dangling_count=dangling_counts.get(EntityTimelineLane.IPS_TOP, 0),
                    index_complete=coverage.ips_top_index_complete,
                )
            ),
            decide_lane(
                LaneEvidence(
                    EntityTimelineLane.IPS_ENTITY,
                    counts.get(EntityTimelineLane.IPS_ENTITY, 0),
                    enabled=self.settings.ips_collection_enabled,
                    collected=coverage.ips_entity_collected,
                    complete=coverage.ips_entity_complete,
                    proves_absence=False,
                    count_is_total=not truncated,
                    dangling_count=dangling_counts.get(EntityTimelineLane.IPS_ENTITY, 0),
                    index_complete=coverage.ips_entity_index_complete,
                )
            ),
            decide_lane(
                LaneEvidence(
                    EntityTimelineLane.OFFENSE,
                    counts.get(EntityTimelineLane.OFFENSE, 0),
                    permitted=offense_allowed,
                    permission=PERM_OFFENSE_READ,
                    collected=offense_freshness is not RelatedSectionState.NOT_COLLECTED,
                    complete=coverage.offense_index_complete,
                    proves_absence=(
                        kind is not EntityTimelineKind.USER
                        and coverage.offense_index_complete
                    ),
                    freshness=offense_freshness,
                    count_is_total=not truncated,
                    dangling_count=dangling_counts.get(EntityTimelineLane.OFFENSE, 0),
                    index_complete=coverage.offense_index_complete,
                )
            ),
            decide_lane(
                LaneEvidence(
                    EntityTimelineLane.LOG_SOURCE,
                    counts.get(EntityTimelineLane.LOG_SOURCE, 0),
                    permitted=fleet_allowed,
                    permission=PERM_FLEET_READ,
                    collected=kind is not EntityTimelineKind.USER,
                    proves_absence=not coverage.log_source_match,
                    count_is_total=not truncated,
                    incomplete_reason=REASON_INDEX_INCOMPLETE,
                    dangling_count=dangling_counts.get(EntityTimelineLane.LOG_SOURCE, 0),
                )
            ),
            decide_lane(
                LaneEvidence(
                    EntityTimelineLane.CATCHALL,
                    counts.get(EntityTimelineLane.CATCHALL, 0),
                    permitted=fleet_allowed,
                    permission=PERM_FLEET_READ,
                    enabled=self.settings.catchall_collection_enabled,
                    collected=coverage.catchall_collected,
                    complete=coverage.catchall_proves_absence,
                    proves_absence=coverage.catchall_proves_absence,
                    count_is_total=not truncated,
                    dangling_count=dangling_counts.get(EntityTimelineLane.CATCHALL, 0),
                    index_complete=coverage.catchall_index_complete,
                )
            ),
            decide_lane(
                LaneEvidence(
                    EntityTimelineLane.PW_VALUE,
                    counts.get(EntityTimelineLane.PW_VALUE, 0),
                    permitted=property_watch_allowed,
                    permission=PERM_PROPERTY_WATCH_READ,
                    enabled=self.settings.pw_collection_enabled,
                    collected=coverage.pw_collected,
                    complete=coverage.pw_proves_absence,
                    proves_absence=coverage.pw_proves_absence,
                    count_is_total=not truncated,
                    dangling_count=dangling_counts.get(EntityTimelineLane.PW_VALUE, 0),
                    index_complete=coverage.pw_index_complete,
                )
            ),
        ]


def _mention_key(mention: EntityMention) -> tuple[str, str, datetime]:
    return mention.ref_table, mention.ref_id, mention.occurred_at


def _uuid_values(mentions: Sequence[EntityMention]) -> list[uuid.UUID]:
    values: list[uuid.UUID] = []
    for mention in mentions:
        try:
            values.append(uuid.UUID(mention.ref_id))
        except ValueError:
            continue
    return list(dict.fromkeys(values))


def _int_values(mentions: Sequence[EntityMention]) -> list[int]:
    values: list[int] = []
    for mention in mentions:
        try:
            values.append(int(mention.ref_id))
        except ValueError:
            continue
    return list(dict.fromkeys(values))


def _uuid_time_pairs(
    mentions: Sequence[EntityMention],
) -> list[tuple[uuid.UUID, datetime]]:
    values: list[tuple[uuid.UUID, datetime]] = []
    for mention in mentions:
        try:
            values.append((uuid.UUID(mention.ref_id), mention.occurred_at))
        except ValueError:
            continue
    return list(dict.fromkeys(values))


def _presentations_for_mentions(
    mentions: Sequence[EntityMention],
    by_id: Mapping[str, ParentPresentation],
) -> dict[tuple[str, str, datetime], ParentPresentation]:
    return {
        _mention_key(mention): by_id[mention.ref_id]
        for mention in mentions
        if mention.ref_id in by_id
    }


def _presentations_for_pairs(
    mentions: Sequence[EntityMention],
    by_pair: Mapping[tuple[str, datetime], ParentPresentation],
) -> dict[tuple[str, str, datetime], ParentPresentation]:
    return {
        _mention_key(mention): by_pair[(mention.ref_id, mention.occurred_at)]
        for mention in mentions
        if (mention.ref_id, mention.occurred_at) in by_pair
    }
