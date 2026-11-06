"""Read model for the noisy-sources list (SOC-17a, design §2).

The list answers one question: which log sources keep firing, how much of that is
noise, and what should the analyst do about it. It writes nothing and changes no
existing query.

Cost is the contract (design §2.3, AGENTS.md rule 9): the list is **three**
statements whatever the fleet size -- sources (with classification, the current
baseline cell's reliability and the evaluation watermark), incidents grouped by
source and detector, and alerts grouped by source and detector. The detail route
uses the same three statements scoped to one source. Nothing here runs a query
per row.

Honesty is the other contract (design §2.4):

* a source that is not monitored is ``NOT_COLLECTED`` and every count is null,
  never zero;
* a monitored source with no reliable baseline cell is ``INSUFFICIENT_DATA`` --
  its counts are still real recorded counts, but a zero is not evidence of calm;
* a noise share is null, not zero, while fewer than
  ``MIN_NOISE_SHARE_DENOMINATOR`` incidents have a closed disposition;
* a window longer than the configured incident retention is ``PARTIAL`` and the
  payload carries the covered span.

The one PARTIAL signal the design names that is not computed here is "an incident
in the window was already pruned while its alert remains". The retention service
keeps every incident an alert still lists, so that state cannot be produced by
the platform's own retention; it would only appear after foreign deletion, and
detecting it would need a statement outside the three-statement budget. It is
left out rather than guessed.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import and_, cast, distinct, func, select, true
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n.messages import MessageRef, render
from app.models.alert import Alert
from app.models.enums import (
    AnomalyFamily,
    AnomalyType,
    Criticality,
    SourceClass,
    TriageDisposition,
    TriageStatus,
)
from app.models.fleet import LogSourceClassification
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource, LogSourceAnomaly, LogSourceBaseline
from app.models.monitoring import AnomalyEvaluationWatermark
from app.models.triage import AnomalyTriage

#: The two windows the row carries side by side. Anything else is refused by the route.
WINDOW_DAYS: tuple[int, ...] = (7, 28)
DEFAULT_WINDOW_DAYS = 28
#: The detail route always reports both windows; its coverage block names the longer one.
DETAIL_WINDOW_DAYS = 28
#: A share over fewer items is noise of its own (design §2.4).
MIN_NOISE_SHARE_DENOMINATOR = 5

REASON_NOT_MONITORED = "noise.reason.not_monitored"
REASON_NO_RELIABLE_BASELINE = "noise.reason.no_reliable_baseline"
REASON_PARTIAL_RETENTION = "noise.reason.partial_retention"
REASON_EMPTY_WINDOW = "noise.reason.empty_window"

#: Detector order for ties, from the persisted taxonomy rather than alphabetical order.
_TYPE_ORDER: dict[AnomalyType, int] = {kind: index for index, kind in enumerate(AnomalyType)}


class NoiseOrder(StrEnum):
    """The real count the operator sorts by. There is no composite score."""

    INCIDENTS = "incidents"
    ALERTS = "alerts"
    NOISE_SHARE = "noise_share"


class Judgement(StrEnum):
    """Whether the source's latest season could be judged at all."""

    JUDGED = "JUDGED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_COLLECTED = "NOT_COLLECTED"


class CoverageState(StrEnum):
    """Whether the stored window covers the requested one."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"


@dataclass(frozen=True)
class Reason:
    """A stable code, its raw parameters and the English sentence they render to."""

    code: str
    params: dict[str, Any]
    text: str

    def to_wire(self) -> dict[str, object]:
        return {
            "code": self.code,
            "params": dict(self.params),
            "text": self.text,
        }


def reason(code: str, **params: object) -> Reason:
    """Build a reason by rendering its registered sentence, never by hand."""
    ref = MessageRef(code, params)
    return Reason(code=code, params=dict(params), text=render(ref))


@dataclass(frozen=True)
class SourceSnapshot:
    """The source row of statement 1: identity, current cell and watermark."""

    log_source_id: uuid.UUID
    name: str
    instance_id: uuid.UUID
    instance_name: str | None
    criticality: Criticality
    source_class: SourceClass | None
    monitoring_enabled: bool
    custom_thresholds: dict[str, Any]
    maintenance_mode: bool
    maintenance_until: datetime | None
    business_hours_only: bool
    expected_interval_seconds: int | None
    baseline_reliable: bool | None
    baseline_computed_at: datetime | None
    evaluated_through: datetime | None


@dataclass(frozen=True)
class IncidentGroup:
    """One (source, detector) row of statement 2."""

    log_source_id: uuid.UUID
    anomaly_type: AnomalyType
    incidents_7d: int
    incidents_28d: int
    opened_7d: int
    opened_28d: int
    noise_closed: int
    noise_denominator: int
    worst_ratio: float | None
    worst_z: float | None


@dataclass(frozen=True)
class AlertGroup:
    """One (source, detector) row of statement 3."""

    log_source_id: uuid.UUID
    anomaly_type: AnomalyType
    alerts_7d: int
    alerts_28d: int


@dataclass(frozen=True)
class DetectorSummary:
    """Per-detector counts for the tuning page's breakdown."""

    anomaly_type: AnomalyType
    incidents_7d: int | None
    incidents_28d: int | None
    opened_7d: int | None
    opened_28d: int | None
    alerts_7d: int | None
    alerts_28d: int | None
    noise_closed: int | None
    noise_closed_total: int | None
    noise_share: float | None
    worst_ratio: float | None
    worst_z: float | None


@dataclass(frozen=True)
class SourceSummary:
    """One noisy-source row: both windows, the noise share and the honesty marker."""

    log_source_id: uuid.UUID
    name: str
    instance_id: uuid.UUID
    instance_name: str | None
    criticality: Criticality
    source_class: SourceClass | None
    monitoring_enabled: bool
    judgement: Judgement
    judged: bool
    reason: Reason | None
    incidents_7d: int | None
    incidents_28d: int | None
    opened_7d: int | None
    opened_28d: int | None
    alerts_7d: int | None
    alerts_28d: int | None
    noise_closed: int | None
    noise_closed_total: int | None
    noise_share: float | None
    worst_ratio: float | None
    worst_z: float | None
    top_detector: AnomalyType | None
    evaluated_through: datetime | None
    detectors: tuple[DetectorSummary, ...] = ()


@dataclass(frozen=True)
class CurrentKnobs:
    """The source's current detection settings, shown beside the replay in 17b."""

    criticality: Criticality
    custom_thresholds: dict[str, Any]
    maintenance_mode: bool
    maintenance_until: datetime | None
    business_hours_only: bool
    expected_interval_seconds: int | None


@dataclass(frozen=True)
class Coverage:
    """How much of the requested window the stored incidents can cover."""

    state: CoverageState
    window_days: int
    covered_days: int
    retention_days: int
    reason: Reason | None


@dataclass(frozen=True)
class NoiseList:
    """The list response before it is converted to its wire schema."""

    window_days: int
    order: NoiseOrder
    limit: int
    offset: int
    total: int
    zero_fired: int
    zero_fired_judged: int
    zero_fired_insufficient_data: int
    last_collection: datetime | None
    coverage: Coverage
    empty_reason: Reason | None
    items: tuple[SourceSummary, ...]


@dataclass(frozen=True)
class SourceDetail:
    """One source with its per-detector breakdown and its current settings."""

    source: SourceSummary
    detectors: tuple[DetectorSummary, ...]
    current: CurrentKnobs
    coverage: Coverage
    #: 17d fills this with the active suppression, if any. Always null in 17a.
    suppression: dict[str, Any] | None = None
    #: 17b fills this with the replay coverage. Always null in 17a.
    replay: dict[str, Any] | None = None


# --------------------------------------------------------------- pure helpers
def window_start(now: datetime, window_days: int) -> datetime:
    """The inclusive lower bound of a window ending at ``now``."""
    return now - timedelta(days=window_days)


def noise_share(noise_closed: int, denominator: int) -> float | None:
    """The NOISE share, or null below the minimum denominator (design §2.4).

    ``0`` is a measured share (no closed item was noise); null is "not enough
    judgments to state one". The boundary is five: four closed items return null
    and five return a number.
    """
    if denominator < MIN_NOISE_SHARE_DENOMINATOR:
        return None
    return noise_closed / denominator


def coverage_for(window_days: int, retention_days: int) -> Coverage:
    """Derive PARTIAL from the configured retention against the requested window."""
    covered = max(1, min(window_days, retention_days))
    if covered >= window_days:
        return Coverage(
            state=CoverageState.COMPLETE,
            window_days=window_days,
            covered_days=covered,
            retention_days=retention_days,
            reason=None,
        )
    return Coverage(
        state=CoverageState.PARTIAL,
        window_days=window_days,
        covered_days=covered,
        retention_days=retention_days,
        reason=reason(REASON_PARTIAL_RETENTION, covered=covered, window=window_days),
    )


def judgement_for(monitoring_enabled: bool, baseline_reliable: bool | None) -> Judgement:
    """Every state rule of design §2.4, in one place."""
    if not monitoring_enabled:
        return Judgement.NOT_COLLECTED
    if baseline_reliable is True:
        return Judgement.JUDGED
    return Judgement.INSUFFICIENT_DATA


def _reason_for(judgement: Judgement) -> Reason | None:
    if judgement is Judgement.NOT_COLLECTED:
        return reason(REASON_NOT_MONITORED)
    if judgement is Judgement.INSUFFICIENT_DATA:
        return reason(REASON_NO_RELIABLE_BASELINE)
    return None


def _detector_summary(
    anomaly_type: AnomalyType,
    incident: IncidentGroup | None,
    alert: AlertGroup | None,
) -> DetectorSummary:
    noise_closed = incident.noise_closed if incident is not None else 0
    denominator = incident.noise_denominator if incident is not None else 0
    return DetectorSummary(
        anomaly_type=anomaly_type,
        incidents_7d=incident.incidents_7d if incident is not None else 0,
        incidents_28d=incident.incidents_28d if incident is not None else 0,
        opened_7d=incident.opened_7d if incident is not None else 0,
        opened_28d=incident.opened_28d if incident is not None else 0,
        alerts_7d=alert.alerts_7d if alert is not None else 0,
        alerts_28d=alert.alerts_28d if alert is not None else 0,
        noise_closed=noise_closed,
        noise_closed_total=denominator,
        noise_share=noise_share(noise_closed, denominator),
        worst_ratio=incident.worst_ratio if incident is not None else None,
        worst_z=incident.worst_z if incident is not None else None,
    )


def summarise_source(
    snapshot: SourceSnapshot,
    incidents: Sequence[IncidentGroup],
    alerts: Sequence[AlertGroup],
) -> SourceSummary:
    """Fold the grouped rows of statements 2 and 3 into one source row."""
    judgement = judgement_for(snapshot.monitoring_enabled, snapshot.baseline_reliable)
    if judgement is Judgement.NOT_COLLECTED:
        # Every count is null: "not monitored" is not a measured zero.
        return SourceSummary(
            log_source_id=snapshot.log_source_id,
            name=snapshot.name,
            instance_id=snapshot.instance_id,
            instance_name=snapshot.instance_name,
            criticality=snapshot.criticality,
            source_class=snapshot.source_class,
            monitoring_enabled=snapshot.monitoring_enabled,
            judgement=judgement,
            judged=False,
            reason=_reason_for(judgement),
            incidents_7d=None,
            incidents_28d=None,
            opened_7d=None,
            opened_28d=None,
            alerts_7d=None,
            alerts_28d=None,
            noise_closed=None,
            noise_closed_total=None,
            noise_share=None,
            worst_ratio=None,
            worst_z=None,
            top_detector=None,
            evaluated_through=snapshot.evaluated_through,
            detectors=(),
        )

    incident_by_type = {group.anomaly_type: group for group in incidents}
    alert_by_type = {group.anomaly_type: group for group in alerts}
    detector_types = sorted(
        set(incident_by_type) | set(alert_by_type), key=lambda kind: _TYPE_ORDER[kind]
    )
    detectors = tuple(
        _detector_summary(
            kind, incident_by_type.get(kind), alert_by_type.get(kind)
        )
        for kind in detector_types
    )

    incidents_7d = sum(item.incidents_7d or 0 for item in detectors)
    incidents_28d = sum(item.incidents_28d or 0 for item in detectors)
    opened_7d = sum(item.opened_7d or 0 for item in detectors)
    opened_28d = sum(item.opened_28d or 0 for item in detectors)
    alerts_7d = sum(item.alerts_7d or 0 for item in detectors)
    alerts_28d = sum(item.alerts_28d or 0 for item in detectors)
    noise_closed = sum(item.noise_closed or 0 for item in detectors)
    noise_closed_total = sum(item.noise_closed_total or 0 for item in detectors)
    ratios = [item.worst_ratio for item in detectors if item.worst_ratio is not None]
    zs = [item.worst_z for item in detectors if item.worst_z is not None]

    # The detector with the most incidents in 28 days; ties keep the taxonomy order.
    top_detector: AnomalyType | None = None
    best = 0
    for item in detectors:
        count = item.incidents_28d or 0
        if count > best:
            best = count
            top_detector = item.anomaly_type

    return SourceSummary(
        log_source_id=snapshot.log_source_id,
        name=snapshot.name,
        instance_id=snapshot.instance_id,
        instance_name=snapshot.instance_name,
        criticality=snapshot.criticality,
        source_class=snapshot.source_class,
        monitoring_enabled=snapshot.monitoring_enabled,
        judgement=judgement,
        judged=judgement is Judgement.JUDGED,
        reason=_reason_for(judgement),
        incidents_7d=incidents_7d,
        incidents_28d=incidents_28d,
        opened_7d=opened_7d,
        opened_28d=opened_28d,
        alerts_7d=alerts_7d,
        alerts_28d=alerts_28d,
        noise_closed=noise_closed,
        noise_closed_total=noise_closed_total,
        noise_share=noise_share(noise_closed, noise_closed_total),
        worst_ratio=max(ratios) if ratios else None,
        worst_z=max(zs) if zs else None,
        top_detector=top_detector,
        evaluated_through=snapshot.evaluated_through,
        detectors=detectors,
    )


def window_incidents(row: SourceSummary, window_days: int) -> int:
    """The row's incident count for the selected window; null counts as zero here."""
    value = row.incidents_7d if window_days == 7 else row.incidents_28d
    return value or 0


def window_alerts(row: SourceSummary, window_days: int) -> int:
    """The row's alert count for the selected window; null counts as zero here."""
    value = row.alerts_7d if window_days == 7 else row.alerts_28d
    return value or 0


def order_rows(
    rows: Sequence[SourceSummary], order: NoiseOrder, window_days: int
) -> list[SourceSummary]:
    """Design §2.1's order, with a deterministic tail.

    ``incidents`` orders by the selected window's incidents then alerts;
    ``alerts`` by alerts then incidents; ``noise_share`` puts the rows whose
    share is null last. The project has no composite score, so no invented one
    is used as a tie-break: name then id keep the page stable.
    """

    def name_key(row: SourceSummary) -> tuple[str, str]:
        return row.name.casefold(), str(row.log_source_id)

    if order is NoiseOrder.ALERTS:
        return sorted(
            rows,
            key=lambda row: (
                -window_alerts(row, window_days),
                -window_incidents(row, window_days),
                *name_key(row),
            ),
        )
    if order is NoiseOrder.NOISE_SHARE:
        return sorted(
            rows,
            key=lambda row: (
                row.noise_share is None,
                -(row.noise_share or 0.0),
                -window_incidents(row, window_days),
                *name_key(row),
            ),
        )
    return sorted(
        rows,
        key=lambda row: (
            -window_incidents(row, window_days),
            -window_alerts(row, window_days),
            *name_key(row),
        ),
    )


# ------------------------------------------------------------------- database
class NoiseReadModel:
    """The three grouped reads behind ``/noise/sources`` and its detail route."""

    def __init__(self, session: AsyncSession, *, now: datetime | None = None) -> None:
        self.session = session
        self.now = now or datetime.now(UTC)

    async def source_snapshots(
        self, *, source_id: uuid.UUID | None = None, monitored_only: bool = True
    ) -> list[SourceSnapshot]:
        """Statement 1: the fleet, its classification, its current cell and watermark."""
        baseline_join = and_(
            LogSourceBaseline.log_source_id == LogSource.id,
            LogSourceBaseline.metric_name == "average_eps",
            LogSourceBaseline.weekday == self.now.isoweekday(),
            LogSourceBaseline.hour == self.now.hour,
        )
        statement = (
            select(
                LogSource.id.label("log_source_id"),
                LogSource.name.label("source_name"),
                LogSource.instance_id.label("instance_id"),
                QRadarInstance.name.label("instance_name"),
                LogSource.criticality.label("criticality"),
                LogSourceClassification.source_class.label("source_class"),
                LogSource.monitoring_enabled.label("monitoring_enabled"),
                LogSource.custom_thresholds.label("custom_thresholds"),
                LogSource.maintenance_mode.label("maintenance_mode"),
                LogSource.maintenance_until.label("maintenance_until"),
                LogSource.business_hours_only.label("business_hours_only"),
                LogSource.expected_interval_seconds.label("expected_interval_seconds"),
                LogSourceBaseline.is_reliable.label("baseline_reliable"),
                LogSourceBaseline.computed_at.label("baseline_computed_at"),
                AnomalyEvaluationWatermark.evaluated_through.label("evaluated_through"),
            )
            .join(QRadarInstance, QRadarInstance.id == LogSource.instance_id)
            .outerjoin(
                LogSourceClassification,
                LogSourceClassification.log_source_id == LogSource.id,
            )
            .outerjoin(LogSourceBaseline, baseline_join)
            .outerjoin(
                AnomalyEvaluationWatermark,
                AnomalyEvaluationWatermark.log_source_id == LogSource.id,
            )
        )
        if source_id is not None:
            statement = statement.where(LogSource.id == source_id)
        if monitored_only:
            statement = statement.where(LogSource.monitoring_enabled.is_(True))
        statement = statement.order_by(LogSource.name, LogSource.id)
        rows = (await self.session.execute(statement)).all()
        return [
            SourceSnapshot(
                log_source_id=row.log_source_id,
                name=row.source_name,
                instance_id=row.instance_id,
                instance_name=row.instance_name,
                criticality=Criticality(row.criticality),
                source_class=SourceClass(row.source_class) if row.source_class else None,
                monitoring_enabled=row.monitoring_enabled,
                custom_thresholds=dict(row.custom_thresholds or {}),
                maintenance_mode=row.maintenance_mode,
                maintenance_until=row.maintenance_until,
                business_hours_only=row.business_hours_only,
                expected_interval_seconds=row.expected_interval_seconds,
                baseline_reliable=row.baseline_reliable,
                baseline_computed_at=row.baseline_computed_at,
                evaluated_through=row.evaluated_through,
            )
            for row in rows
        ]

    async def incident_groups(
        self, source_ids: Sequence[uuid.UUID]
    ) -> list[IncidentGroup]:
        """Statement 2: incidents per (source, detector) with both windows and triage."""
        if not source_ids:
            return []
        seven = window_start(self.now, 7)
        twenty_eight = window_start(self.now, 28)
        detected = LogSourceAnomaly.detected_at
        closed = AnomalyTriage.status == TriageStatus.CLOSED.value
        has_disposition = AnomalyTriage.disposition.is_not(None)
        noise = AnomalyTriage.disposition == TriageDisposition.NOISE.value
        statement = (
            select(
                LogSourceAnomaly.log_source_id.label("log_source_id"),
                LogSourceAnomaly.anomaly_type.label("anomaly_type"),
                func.count(LogSourceAnomaly.id)
                .filter(detected >= seven)
                .label("incidents_7d"),
                func.count(LogSourceAnomaly.id).label("incidents_28d"),
                func.count(LogSourceAnomaly.id)
                .filter(and_(detected >= seven, LogSourceAnomaly.opened_at.is_not(None)))
                .label("opened_7d"),
                func.count(LogSourceAnomaly.id)
                .filter(LogSourceAnomaly.opened_at.is_not(None))
                .label("opened_28d"),
                func.count(AnomalyTriage.id)
                .filter(and_(closed, has_disposition, noise))
                .label("noise_closed"),
                func.count(AnomalyTriage.id)
                .filter(and_(closed, has_disposition))
                .label("noise_denominator"),
                func.max(LogSourceAnomaly.deviation_ratio).label("worst_ratio"),
                func.max(func.abs(LogSourceAnomaly.robust_z)).label("worst_z"),
            )
            .select_from(LogSourceAnomaly)
            .outerjoin(
                AnomalyTriage,
                and_(
                    AnomalyTriage.family == AnomalyFamily.LOG_SOURCE.value,
                    AnomalyTriage.anomaly_id == LogSourceAnomaly.id,
                ),
            )
            .where(detected >= twenty_eight, LogSourceAnomaly.log_source_id.in_(source_ids))
            .group_by(LogSourceAnomaly.log_source_id, LogSourceAnomaly.anomaly_type)
        )
        rows = (await self.session.execute(statement)).all()
        return [
            IncidentGroup(
                log_source_id=row.log_source_id,
                anomaly_type=AnomalyType(row.anomaly_type),
                incidents_7d=int(row.incidents_7d),
                incidents_28d=int(row.incidents_28d),
                opened_7d=int(row.opened_7d),
                opened_28d=int(row.opened_28d),
                noise_closed=int(row.noise_closed),
                noise_denominator=int(row.noise_denominator),
                worst_ratio=row.worst_ratio,
                worst_z=row.worst_z,
            )
            for row in rows
        ]

    async def alert_groups(self, source_ids: Sequence[uuid.UUID]) -> list[AlertGroup]:
        """Statement 3: anomaly alerts per (source, detector) through ``source_anomaly_ids``.

        Inventory alerts carry no anomaly id and therefore join nothing; they are
        not noise signal and are excluded by construction rather than by a second
        predicate about their fingerprint.
        """
        if not source_ids:
            return []
        seven = window_start(self.now, 7)
        twenty_eight = window_start(self.now, 28)
        references = (
            func.jsonb_array_elements_text(Alert.source_anomaly_ids)
            .table_valued("value")
            .alias("anomaly_ref")
        )
        statement = (
            select(
                Alert.source_id.label("log_source_id"),
                LogSourceAnomaly.anomaly_type.label("anomaly_type"),
                func.count(distinct(Alert.id))
                .filter(Alert.opened_at >= seven)
                .label("alerts_7d"),
                func.count(distinct(Alert.id)).label("alerts_28d"),
            )
            .select_from(Alert)
            .join(references, true())
            .join(
                LogSourceAnomaly,
                LogSourceAnomaly.id == cast(references.c.value, PgUUID(as_uuid=True)),
            )
            .where(
                Alert.source_type == "log_source",
                Alert.source_id.in_(source_ids),
                Alert.opened_at >= twenty_eight,
            )
            .group_by(Alert.source_id, LogSourceAnomaly.anomaly_type)
        )
        rows = (await self.session.execute(statement)).all()
        return [
            AlertGroup(
                log_source_id=row.log_source_id,
                anomaly_type=AnomalyType(row.anomaly_type),
                alerts_7d=int(row.alerts_7d),
                alerts_28d=int(row.alerts_28d),
            )
            for row in rows
        ]

    async def list_sources(
        self,
        *,
        window_days: int,
        order: NoiseOrder,
        limit: int,
        offset: int,
        retention_days: int,
    ) -> NoiseList:
        """The list: three statements, then the fold and the page in Python."""
        snapshots = await self.source_snapshots(monitored_only=True)
        source_ids = [snapshot.log_source_id for snapshot in snapshots]
        incidents = await self.incident_groups(source_ids)
        alerts = await self.alert_groups(source_ids)

        incidents_by_source: dict[uuid.UUID, list[IncidentGroup]] = {}
        for incident in incidents:
            incidents_by_source.setdefault(incident.log_source_id, []).append(incident)
        alerts_by_source: dict[uuid.UUID, list[AlertGroup]] = {}
        for alert in alerts:
            alerts_by_source.setdefault(alert.log_source_id, []).append(alert)

        summaries = [
            summarise_source(
                snapshot,
                incidents_by_source.get(snapshot.log_source_id, []),
                alerts_by_source.get(snapshot.log_source_id, []),
            )
            for snapshot in snapshots
        ]
        fired = [row for row in summaries if window_incidents(row, window_days) > 0]
        quiet = [row for row in summaries if window_incidents(row, window_days) == 0]
        ordered = order_rows(fired, order, window_days)
        marks = [
            snapshot.evaluated_through
            for snapshot in snapshots
            if snapshot.evaluated_through is not None
        ]
        return NoiseList(
            window_days=window_days,
            order=order,
            limit=limit,
            offset=offset,
            total=len(ordered),
            zero_fired=len(quiet),
            zero_fired_judged=sum(1 for row in quiet if row.judged),
            zero_fired_insufficient_data=sum(1 for row in quiet if not row.judged),
            last_collection=max(marks) if marks else None,
            coverage=coverage_for(window_days, retention_days),
            empty_reason=(
                reason(REASON_EMPTY_WINDOW, window=window_days) if not ordered else None
            ),
            items=tuple(ordered[offset : offset + limit]),
        )

    async def source_detail(
        self, log_source_id: uuid.UUID, *, retention_days: int
    ) -> SourceDetail | None:
        """One source: the same three statements, scoped, or ``None`` when unknown."""
        snapshots = await self.source_snapshots(
            source_id=log_source_id, monitored_only=False
        )
        if not snapshots:
            return None
        snapshot = snapshots[0]
        incidents = await self.incident_groups([log_source_id])
        alerts = await self.alert_groups([log_source_id])
        summary = summarise_source(snapshot, incidents, alerts)
        return SourceDetail(
            source=summary,
            detectors=summary.detectors,
            current=CurrentKnobs(
                criticality=snapshot.criticality,
                custom_thresholds=snapshot.custom_thresholds,
                maintenance_mode=snapshot.maintenance_mode,
                maintenance_until=snapshot.maintenance_until,
                business_hours_only=snapshot.business_hours_only,
                expected_interval_seconds=snapshot.expected_interval_seconds,
            ),
            coverage=coverage_for(DETAIL_WINDOW_DAYS, retention_days),
        )
