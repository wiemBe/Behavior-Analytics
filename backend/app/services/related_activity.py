"""Related activity around one anomaly: window arithmetic, section states, ordering.

The service resolves the anchor, derives the padded window and assembles three sections
from the repository's bulk reads. It never asks QRadar and never writes: every input is
already stored, and the only clock read is the offense watermark's freshness.

Fail-closed rules live here as pure functions so they can be pinned without a database:
an uncollected offense feed is not an empty list, a stale feed is not current, and a
rule with no measured window has a null count rather than a zero.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.enums import AnomalyFamily, TriageStatus
from app.models.ips import IpsAnomaly, IpsEntity
from app.models.log_source import LogSource, LogSourceAnomaly
from app.models.property_watch import PropertyWatch, PwAnomaly
from app.models.rule import RuleMatchWindowStatus
from app.models.waf import WafAnomaly, WafEndpoint
from app.repositories.related_activity import RelatedActivityRepository
from app.rules.matches import MatchEvidence, match_evidence
from app.schemas.related_activity import (
    RelatedActivityOut,
    RelatedAnchorOut,
    RelatedAnomaliesSection,
    RelatedAnomalyOut,
    RelatedMeasurement,
    RelatedOffenseOut,
    RelatedOffensesSection,
    RelatedPad,
    RelatedRuleOut,
    RelatedRulesSection,
    RelatedSectionState,
)

#: At most this many items per section. The section's `total` is the full count.
ITEMS_LIMIT = 20

#: The padding each allowed `pad` value adds on each side of the anomaly's interval.
PAD_SECONDS: dict[RelatedPad, int] = {
    RelatedPad.THIRTY_MINUTES: 30 * 60,
    RelatedPad.ONE_HOUR: 60 * 60,
    RelatedPad.TWO_HOURS: 2 * 60 * 60,
    RelatedPad.SIX_HOURS: 6 * 60 * 60,
}

REASON_NO_LOG_SOURCE = "related_activity.reason.no_log_source"
REASON_OFFENSE_NOT_COLLECTED = "related_activity.reason.offense_not_collected"
REASON_OFFENSE_STALE = "related_activity.reason.offense_stale"
REASON_RULES_NOT_COLLECTED = "related_activity.reason.rules_not_collected"
REASON_RULES_STALE = "related_activity.reason.rules_stale"
REASON_RULES_PARTIAL = "related_activity.reason.rules_partial"
REASON_ITEMS_TRUNCATED = "related_activity.reason.items_truncated"

#: Every reason code this module emits, for the report and the frontend contract.
REASON_CODES: tuple[str, ...] = (
    REASON_NO_LOG_SOURCE,
    REASON_OFFENSE_NOT_COLLECTED,
    REASON_OFFENSE_STALE,
    REASON_RULES_NOT_COLLECTED,
    REASON_RULES_STALE,
    REASON_RULES_PARTIAL,
    REASON_ITEMS_TRUNCATED,
)


class RelatedActivityNotFound(Exception):
    """The named anomaly id is not in the family's table."""


@dataclass(frozen=True)
class Anchor:
    """The anomaly the read is anchored on, after its source has been resolved."""

    family: AnomalyFamily
    anomaly_id: uuid.UUID
    source: LogSource | None
    start: datetime
    end: datetime


@dataclass(frozen=True)
class MatchWindowSpan:
    """One measured (or attempted) rule-match window, for the pure measurement fold."""

    start: datetime
    seconds: int
    status: RuleMatchWindowStatus


def window_bounds(
    start: datetime, end: datetime, pad: RelatedPad
) -> tuple[datetime, datetime]:
    """The padded window: `[start - pad, end + pad]`.

    `end` is the anomaly's own stored end. For an incident that is still open this is
    the end of its newest evaluated bucket, written by the detector; it is deliberately
    not the wall clock, which would make the same anomaly answer differently on every
    request and pull in activity after its last observation.
    """
    delta = timedelta(seconds=PAD_SECONDS[pad])
    return start - delta, end + delta


def anomaly_span(
    *, detected_at: datetime, anomaly_start: datetime | None, anomaly_end: datetime | None
) -> tuple[datetime, datetime]:
    """The stored telemetry interval, with a defensible fallback for missing bounds."""
    start = anomaly_start or detected_at
    return start, anomaly_end or start


def offense_collection_state(
    *,
    last_run_at: datetime | None,
    consecutive_failures: int,
    now: datetime,
    stale_after_seconds: int,
) -> RelatedSectionState:
    """`NOT_COLLECTED`, `STALE` or `OK` from the offense collector's watermark.

    A missing watermark row and a row that has never run are both `NOT_COLLECTED`:
    an offense table with no successful run cannot prove that a window held none. A
    failed run (`consecutive_failures > 0`) or a last run older than the configured
    threshold is `STALE`, the same signal the stale-offense alert uses.
    """
    if last_run_at is None:
        return RelatedSectionState.NOT_COLLECTED
    if last_run_at.tzinfo is None:
        last_run_at = last_run_at.replace(tzinfo=UTC)
    if consecutive_failures > 0:
        return RelatedSectionState.STALE
    if (now - last_run_at).total_seconds() > stale_after_seconds:
        return RelatedSectionState.STALE
    return RelatedSectionState.OK


def state_with_cap(
    state: RelatedSectionState, total: int, *, limit: int = ITEMS_LIMIT
) -> RelatedSectionState:
    """Keep a stronger state; mark an otherwise clean section `TRUNCATED` above the cap."""
    if state is not RelatedSectionState.OK:
        return state
    return RelatedSectionState.TRUNCATED if total > limit else RelatedSectionState.OK


def order_anomalies(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Start descending, then family, then id ascending.

    Two stable sorts rather than one tuple: a reversed datetime key would need a
    sentinel for the impossible NaT case, while start is always set here.
    """
    ordered = sorted(rows, key=lambda row: (str(row["family"]), str(row["id"])))
    ordered.sort(key=lambda row: row["start"], reverse=True)
    return ordered


def order_rules(rules: Sequence[RelatedRuleOut]) -> list[RelatedRuleOut]:
    """Offense count descending, matched events descending with nulls last, then id."""
    return sorted(
        rules,
        key=lambda rule: (
            -rule.related_offenses,
            -(rule.matched_events if rule.matched_events is not None else -1),
            rule.qradar_id,
        ),
    )


def _covers(
    windows: Sequence[MatchWindowSpan], window_start: datetime, window_end: datetime
) -> bool:
    """Whether the union of measured windows covers the whole read window."""
    if window_end <= window_start:
        return True
    intervals = sorted(
        (window.start, window.start + timedelta(seconds=window.seconds))
        for window in windows
        if window.status
        in (RuleMatchWindowStatus.COMPLETE, RuleMatchWindowStatus.TRUNCATED)
    )
    if not intervals:
        return False
    cursor = window_start
    for start, end in intervals:
        if start > cursor:
            return False
        if end > cursor:
            cursor = end
        if cursor >= window_end:
            return True
    return False


def measure_rule_matches(
    *,
    rule_type: str | None,
    windows: Sequence[MatchWindowSpan],
    window_start: datetime,
    window_end: datetime,
    matched_events: int | None,
) -> tuple[int | None, RelatedMeasurement]:
    """What the measured windows prove about one rule's event count.

    A rule whose type an event search cannot measure, or a window with no measured row,
    yields `(None, NOT_COLLECTED)`. An EVENT rule absent from a complete, fully covering
    window is a measured zero. Any truncation, failure or partial coverage makes a
    present count a lower bound, and makes an absent count unmeasured rather than zero.
    """
    evidence = match_evidence(rule_type)
    measured = [
        window
        for window in windows
        if window.status
        in (RuleMatchWindowStatus.COMPLETE, RuleMatchWindowStatus.TRUNCATED)
    ]
    if evidence is MatchEvidence.NOT_MEASURED or not measured:
        return None, RelatedMeasurement.NOT_COLLECTED

    incomplete = (
        evidence is MatchEvidence.LOWER_BOUND
        or any(window.status is RuleMatchWindowStatus.TRUNCATED for window in windows)
        or any(window.status is RuleMatchWindowStatus.FAILED for window in windows)
        or not _covers(measured, window_start, window_end)
    )
    if matched_events is None:
        if not incomplete and evidence is MatchEvidence.EXACT:
            return 0, RelatedMeasurement.EXACT
        return None, RelatedMeasurement.NOT_COLLECTED
    return matched_events, (
        RelatedMeasurement.LOWER_BOUND if incomplete else RelatedMeasurement.EXACT
    )


class RelatedActivityService:
    def __init__(
        self, session: AsyncSession, settings: Settings, *, now: datetime | None = None
    ) -> None:
        self.session = session
        self.settings = settings
        self.repo = RelatedActivityRepository(session)
        self._now = now or datetime.now(UTC)

    # ------------------------------------------------------------------ anchor
    async def _anchor(self, family: AnomalyFamily, anomaly_id: uuid.UUID) -> Anchor:
        if family is AnomalyFamily.LOG_SOURCE:
            source_anomaly = await self.session.get(LogSourceAnomaly, anomaly_id)
            if source_anomaly is None:
                raise RelatedActivityNotFound("anomaly not found")
            source = await self.session.get(
                LogSource, source_anomaly.log_source_id
            )
            start, end = anomaly_span(
                detected_at=source_anomaly.detected_at,
                anomaly_start=source_anomaly.anomaly_start,
                anomaly_end=source_anomaly.anomaly_end,
            )
            return Anchor(family, anomaly_id, source, start, end)

        if family is AnomalyFamily.WAF:
            waf_anomaly = await self.session.get(WafAnomaly, anomaly_id)
            if waf_anomaly is None:
                raise RelatedActivityNotFound("anomaly not found")
            endpoint = await self.session.get(
                WafEndpoint, waf_anomaly.waf_endpoint_id
            )
            source = (
                await self.session.get(LogSource, endpoint.log_source_id)
                if endpoint is not None
                else None
            )
            start, end = anomaly_span(
                detected_at=waf_anomaly.detected_at,
                anomaly_start=waf_anomaly.anomaly_start,
                anomaly_end=waf_anomaly.anomaly_end,
            )
            return Anchor(family, anomaly_id, source, start, end)

        if family is AnomalyFamily.IPS:
            ips_anomaly = await self.session.get(IpsAnomaly, anomaly_id)
            if ips_anomaly is None:
                raise RelatedActivityNotFound("anomaly not found")
            entity = (
                await self.session.get(IpsEntity, ips_anomaly.ips_entity_id)
                if ips_anomaly.ips_entity_id is not None
                else None
            )
            # An entity-subject incident stores no source of its own; the entity does.
            source_id = ips_anomaly.log_source_id or (
                entity.log_source_id if entity is not None else None
            )
            source = (
                await self.session.get(LogSource, source_id)
                if source_id is not None
                else None
            )
            start, end = anomaly_span(
                detected_at=ips_anomaly.detected_at,
                anomaly_start=ips_anomaly.anomaly_start,
                anomaly_end=ips_anomaly.anomaly_end,
            )
            return Anchor(family, anomaly_id, source, start, end)

        pw_anomaly = await self.session.get(PwAnomaly, anomaly_id)
        if pw_anomaly is None:
            raise RelatedActivityNotFound("anomaly not found")
        watch = await self.session.get(PropertyWatch, pw_anomaly.watch_id)
        source = (
            await self.session.get(LogSource, watch.log_source_id)
            if watch is not None and watch.log_source_id is not None
            else None
        )
        start, end = anomaly_span(
            detected_at=pw_anomaly.detected_at,
            anomaly_start=pw_anomaly.anomaly_start,
            anomaly_end=pw_anomaly.anomaly_end,
        )
        return Anchor(family, anomaly_id, source, start, end)

    # -------------------------------------------------------------------- read
    async def read(
        self, family: AnomalyFamily, anomaly_id: uuid.UUID, pad: RelatedPad
    ) -> RelatedActivityOut:
        anchor = await self._anchor(family, anomaly_id)
        window_start, window_end = window_bounds(anchor.start, anchor.end, pad)
        anchor_out = RelatedAnchorOut(
            family=family,
            anomaly_id=anomaly_id,
            log_source_id=anchor.source.id if anchor.source is not None else None,
            qradar_log_source_id=(
                anchor.source.qradar_id if anchor.source is not None else None
            ),
            log_source_name=anchor.source.name if anchor.source is not None else None,
            window_start=window_start,
            window_end=window_end,
            pad=pad,
        )

        if anchor.source is None:
            empty = RelatedSectionState.NOT_APPLICABLE
            return RelatedActivityOut(
                state=empty,
                reason_code=REASON_NO_LOG_SOURCE,
                anchor=anchor_out,
                offenses=RelatedOffensesSection(
                    state=empty, reason_code=REASON_NO_LOG_SOURCE, total=0, items=[]
                ),
                anomalies=RelatedAnomaliesSection(
                    state=empty, reason_code=REASON_NO_LOG_SOURCE, total=0, items=[]
                ),
                rules=RelatedRulesSection(
                    state=empty, reason_code=REASON_NO_LOG_SOURCE, total=0, items=[]
                ),
            )

        source = anchor.source
        watermark = await self.repo.offense_watermark(source.instance_id)
        offense_base = offense_collection_state(
            last_run_at=watermark.last_run_at if watermark is not None else None,
            consecutive_failures=(
                watermark.consecutive_failures if watermark is not None else 0
            ),
            now=self._now,
            stale_after_seconds=self.settings.offense_stale_after_seconds,
        )

        offense_rows: list[dict[str, Any]] = []
        offense_total = 0
        rule_counts: dict[int, int] = {}
        if offense_base is not RelatedSectionState.NOT_COLLECTED:
            offense_rows, offense_total = await self.repo.offenses(
                instance_id=source.instance_id,
                qradar_log_source_id=source.qradar_id,
                window_start=window_start,
                window_end=window_end,
                limit=ITEMS_LIMIT,
            )
            rule_counts = await self.repo.offense_rule_counts(
                instance_id=source.instance_id,
                qradar_log_source_id=source.qradar_id,
                window_start=window_start,
                window_end=window_end,
            )

        offense_state = state_with_cap(offense_base, offense_total)
        offense_reason = _offense_reason(offense_state)
        offenses = RelatedOffensesSection(
            state=offense_state,
            reason_code=offense_reason,
            total=offense_total,
            items=[
                RelatedOffenseOut(
                    qradar_offense_id=row["qradar_offense_id"],
                    description=row["description"],
                    status=row["status"],
                    magnitude=row["magnitude"],
                    severity=row["severity"],
                    start_time=row["start_time"],
                    last_updated_time=row["last_updated_time"],
                    close_time=row["close_time"],
                    offense_type_name=row["offense_type_name"],
                    rule_count=len(row["rule_ids"] or []),
                    captured_at=row["captured_at"],
                )
                for row in offense_rows
            ],
        )

        anomaly_rows = await self._anomaly_rows(anchor, window_start, window_end)
        anomaly_rows = order_anomalies(anomaly_rows)
        anomaly_total = len(anomaly_rows)
        anomaly_state = state_with_cap(RelatedSectionState.OK, anomaly_total)
        anomalies = RelatedAnomaliesSection(
            state=anomaly_state,
            reason_code=_truncation_reason(anomaly_state),
            total=anomaly_total,
            items=[
                RelatedAnomalyOut(
                    family=str(row["family"]),
                    id=row["id"],
                    type=str(row["type"]),
                    lifecycle_state=row["lifecycle_state"],
                    severity=row["severity"],
                    start=row["start"],
                    end=row["end"],
                    triage_status=row["triage_status"] or TriageStatus.NEW,
                )
                for row in anomaly_rows[:ITEMS_LIMIT]
            ],
        )

        rules = await self._rules_section(
            source=source,
            offense_base=offense_base,
            rule_counts=rule_counts,
            window_start=window_start,
            window_end=window_end,
        )

        reason = None
        state = RelatedSectionState.OK
        return RelatedActivityOut(
            state=state,
            reason_code=reason,
            anchor=anchor_out,
            offenses=offenses,
            anomalies=anomalies,
            rules=rules,
        )

    async def _anomaly_rows(
        self, anchor: Anchor, window_start: datetime, window_end: datetime
    ) -> list[dict[str, Any]]:
        """One statement per family, plus one for custom-rule breaches."""
        assert anchor.source is not None
        rows: list[dict[str, Any]] = []
        for family in AnomalyFamily:
            rows.extend(
                await self.repo.anomalies(
                    family=family,
                    source_id=anchor.source.id,
                    anchor_id=anchor.anomaly_id,
                    window_start=window_start,
                    window_end=window_end,
                )
            )
        rows.extend(
            await self.repo.custom_rule_breaches(
                anchor.source.id, window_start, window_end
            )
        )
        return rows

    async def _rules_section(
        self,
        *,
        source: LogSource,
        offense_base: RelatedSectionState,
        rule_counts: dict[int, int],
        window_start: datetime,
        window_end: datetime,
    ) -> RelatedRulesSection:
        if offense_base is RelatedSectionState.NOT_COLLECTED:
            return RelatedRulesSection(
                state=RelatedSectionState.NOT_COLLECTED,
                reason_code=REASON_RULES_NOT_COLLECTED,
                total=0,
                items=[],
            )

        catalog = await self.repo.rules_by_qradar(source.instance_id, sorted(rule_counts))
        window_rows = await self.repo.rule_match_windows(
            source.instance_id, window_start, window_end
        )
        windows = [
            MatchWindowSpan(
                start=row.window_start,
                seconds=row.window_seconds,
                status=RuleMatchWindowStatus(str(row.status)),
            )
            for row in window_rows
        ]
        catalog_ids = [rule.id for rule in catalog.values()]
        metrics = await self.repo.rule_matched_events(
            catalog_ids, window_start, window_end
        )

        items: list[RelatedRuleOut] = []
        for qradar_id, related_offenses in rule_counts.items():
            rule = catalog.get(qradar_id)
            matched, measurement = measure_rule_matches(
                rule_type=rule.rule_type if rule is not None else None,
                windows=windows,
                window_start=window_start,
                window_end=window_end,
                matched_events=metrics.get(rule.id) if rule is not None else None,
            )
            items.append(
                RelatedRuleOut(
                    id=rule.id if rule is not None else None,
                    qradar_id=qradar_id,
                    name=rule.name if rule is not None else None,
                    type=rule.rule_type if rule is not None else None,
                    related_offenses=related_offenses,
                    matched_events=matched,
                    measurement=measurement,
                )
            )

        items = order_rules(items)
        total = len(items)
        state = state_with_cap(offense_base, total)
        reason = _rules_reason(state)
        if state is RelatedSectionState.OK and any(
            item.measurement is RelatedMeasurement.NOT_COLLECTED for item in items
        ):
            state = RelatedSectionState.PARTIAL
            reason = REASON_RULES_PARTIAL
        return RelatedRulesSection(
            state=state, reason_code=reason, total=total, items=items[:ITEMS_LIMIT]
        )


def _offense_reason(state: RelatedSectionState) -> str | None:
    if state is RelatedSectionState.NOT_COLLECTED:
        return REASON_OFFENSE_NOT_COLLECTED
    if state is RelatedSectionState.STALE:
        return REASON_OFFENSE_STALE
    return _truncation_reason(state)


def _rules_reason(state: RelatedSectionState) -> str | None:
    if state is RelatedSectionState.NOT_COLLECTED:
        return REASON_RULES_NOT_COLLECTED
    if state is RelatedSectionState.STALE:
        return REASON_RULES_STALE
    return _truncation_reason(state)


def _truncation_reason(state: RelatedSectionState) -> str | None:
    return REASON_ITEMS_TRUNCATED if state is RelatedSectionState.TRUNCATED else None
