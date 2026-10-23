"""Query helpers for Phase A behavioral analytics.

Keeps the route handlers free of query construction, and keeps the pagination
and range guards in one place where they cannot be forgotten on a new endpoint.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    ColumnElement,
    Integer,
    Select,
    String,
    cast,
    exists,
    func,
    literal_column,
    null,
    or_,
    select,
    tuple_,
    union_all,
)
from sqlalchemy.dialects.postgresql import JSONB, distinct_on
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, selectinload

from app.models.enums import (
    ACTIVE_ANOMALY_STATES,
    AnomalyState,
    AnomalyType,
    EvidenceStatus,
    IpsAnomalyType,
    PwAnomalyType,
    Severity,
    WafAnomalyType,
)
from app.models.explanation import AnomalyExplanation
from app.models.ips import IpsAnomaly, IpsAnomalyTransition, IpsEntity
from app.models.log_source import (
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceMetric,
)
from app.models.property_watch import PropertyWatch, PwAnomaly, PwAnomalyTransition
from app.models.waf import (
    WafAnomaly,
    WafDetectorState,
    WafEndpoint,
    WafEndpointBaseline,
    WafEndpointMetric,
    WafSourceBucket,
    WafSourceProfile,
)
from app.schemas.anomaly import SubjectKind
from app.waf.baseline import WAF_BASELINE_METRIC
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION

_PHASE_A_TYPES = frozenset(t.value for t in AnomalyType)
_WAF_TYPES = frozenset(t.value for t in WafAnomalyType)
_IPS_TYPES = frozenset(t.value for t in IpsAnomalyType)
_PW_TYPES = frozenset(t.value for t in PwAnomalyType)


class AnomalyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------- unified list
    @staticmethod
    def _applicable_kinds(
        *,
        subject_kind: SubjectKind | None,
        waf_endpoint_id: uuid.UUID | None,
        ips_entity_id: uuid.UUID | None,
        anomaly_type: str | None,
    ) -> tuple[bool, bool, bool]:
        """Which anomaly families a filter set can match: (Phase A, WAF, IPS).

        A filter that cannot apply to a family excludes it. Ignoring it would
        return rows the caller asked not to see.
        """
        phase_a = subject_kind in (None, SubjectKind.LOG_SOURCE)
        waf = subject_kind in (None, SubjectKind.WAF_ENDPOINT)
        ips = subject_kind in (None, SubjectKind.IPS_ENTITY, SubjectKind.IPS_SOURCE)
        if waf_endpoint_id is not None:
            phase_a = False
            ips = False
        if ips_entity_id is not None:
            phase_a = False
            waf = False
            # An entity id names an entity incident; a source-incident filter
            # contradicts it.
            ips = ips and subject_kind is not SubjectKind.IPS_SOURCE
        if anomaly_type is not None:
            phase_a = phase_a and anomaly_type in _PHASE_A_TYPES
            waf = waf and anomaly_type in _WAF_TYPES
            ips = ips and anomaly_type in _IPS_TYPES
        return phase_a, waf, ips

    def _unified_phase_a(self, conditions: list[ColumnElement[bool]]) -> Select:
        a = LogSourceAnomaly
        return (
            select(
                a.id,
                cast(literal_column("'LOG_SOURCE'"), String).label("subject_kind"),
                a.log_source_id,
                LogSource.name.label("log_source_name"),
                cast(null(), PgUUID(as_uuid=True)).label("waf_endpoint_id"),
                cast(null(), String).label("endpoint_normalized"),
                cast(null(), String).label("endpoint_method"),
                cast(null(), String).label("endpoint_virtual_host"),
                cast(null(), Boolean).label("endpoint_virtual_host_available"),
                cast(null(), Integer).label("endpoint_normalizer_version"),
                cast(null(), PgUUID(as_uuid=True)).label("ips_entity_id"),
                cast(null(), String).label("entity_dimension"),
                cast(null(), String).label("entity_value_key"),
                cast(null(), String).label("entity_display_name"),
                cast(null(), PgUUID(as_uuid=True)).label("pw_watch_id"),
                cast(null(), String).label("pw_watch_name"),
                cast(null(), Integer).label("pw_watch_version"),
                cast(null(), String).label("pw_scope_kind"),
                cast(null(), JSONB).label("pw_properties"),
                cast(null(), String).label("pw_value_key"),
                cast(null(), PgUUID(as_uuid=True)).label("pw_value_id"),
                a.anomaly_type.label("anomaly_type"),
                a.state, a.severity, a.observed_value, a.expected_value, a.deviation_ratio,
                a.robust_z, a.absolute_delta, a.consecutive_buckets, a.confidence,
                a.detected_at, a.opened_at, a.anomaly_start, a.anomaly_end, a.resolved_at,
                a.evidence_status, a.suppressed,
                cast(null(), String).label("observed_measurement"),
                a.explanation,
            )
            .select_from(a)
            .outerjoin(LogSource, LogSource.id == a.log_source_id)
            .where(*conditions)
        )

    def _unified_waf(self, conditions: list[ColumnElement[bool]]) -> Select:
        w = WafAnomaly
        e = WafEndpoint
        host_available = or_(
            e.virtual_host.is_not(None),
            exists().where(
                WafSourceProfile.log_source_id == e.log_source_id,
                WafSourceProfile.virtual_host_property.is_not(None),
            ),
        )
        return (
            select(
                w.id,
                cast(literal_column("'WAF_ENDPOINT'"), String).label("subject_kind"),
                e.log_source_id,
                LogSource.name.label("log_source_name"),
                e.id.label("waf_endpoint_id"),
                e.normalized_endpoint.label("endpoint_normalized"),
                e.http_method.label("endpoint_method"),
                e.virtual_host.label("endpoint_virtual_host"),
                host_available.label("endpoint_virtual_host_available"),
                e.normalizer_version.label("endpoint_normalizer_version"),
                cast(null(), PgUUID(as_uuid=True)).label("ips_entity_id"),
                cast(null(), String).label("entity_dimension"),
                cast(null(), String).label("entity_value_key"),
                cast(null(), String).label("entity_display_name"),
                cast(null(), PgUUID(as_uuid=True)).label("pw_watch_id"),
                cast(null(), String).label("pw_watch_name"),
                cast(null(), Integer).label("pw_watch_version"),
                cast(null(), String).label("pw_scope_kind"),
                cast(null(), JSONB).label("pw_properties"),
                cast(null(), String).label("pw_value_key"),
                cast(null(), PgUUID(as_uuid=True)).label("pw_value_id"),
                w.detector_type.label("anomaly_type"),
                w.state, w.severity, w.observed_value, w.expected_value, w.deviation_ratio,
                w.robust_z, w.absolute_delta, w.consecutive_buckets, w.confidence,
                w.detected_at, w.opened_at, w.anomaly_start, w.anomaly_end, w.resolved_at,
                w.evidence_status, w.suppressed,
                w.observed_measurement.label("observed_measurement"),
                w.explanation,
            )
            .select_from(w)
            .join(e, e.id == w.waf_endpoint_id)
            .outerjoin(LogSource, LogSource.id == e.log_source_id)
            .where(*conditions)
        )

    def _unified_ips(self, conditions: list[ColumnElement[bool]]) -> Select:
        a = IpsAnomaly
        e = IpsEntity
        # An entity incident carries its source on the entity row; a source
        # incident on the anomaly row. Exactly one is set, so the coalesce is
        # the subject's log source either way, joined into the branch once.
        source_id = func.coalesce(a.log_source_id, e.log_source_id)
        return (
            select(
                a.id,
                a.subject_kind.label("subject_kind"),
                source_id.label("log_source_id"),
                LogSource.name.label("log_source_name"),
                cast(null(), PgUUID(as_uuid=True)).label("waf_endpoint_id"),
                cast(null(), String).label("endpoint_normalized"),
                cast(null(), String).label("endpoint_method"),
                cast(null(), String).label("endpoint_virtual_host"),
                cast(null(), Boolean).label("endpoint_virtual_host_available"),
                cast(null(), Integer).label("endpoint_normalizer_version"),
                e.id.label("ips_entity_id"),
                e.dimension.label("entity_dimension"),
                e.value_key.label("entity_value_key"),
                e.display_name.label("entity_display_name"),
                cast(null(), PgUUID(as_uuid=True)).label("pw_watch_id"),
                cast(null(), String).label("pw_watch_name"),
                cast(null(), Integer).label("pw_watch_version"),
                cast(null(), String).label("pw_scope_kind"),
                cast(null(), JSONB).label("pw_properties"),
                cast(null(), String).label("pw_value_key"),
                cast(null(), PgUUID(as_uuid=True)).label("pw_value_id"),
                a.detector_type.label("anomaly_type"),
                a.state, a.severity, a.observed_value, a.expected_value, a.deviation_ratio,
                a.robust_z, a.absolute_delta, a.consecutive_buckets, a.confidence,
                a.detected_at, a.opened_at, a.anomaly_start, a.anomaly_end, a.resolved_at,
                a.evidence_status, a.suppressed,
                a.observed_measurement.label("observed_measurement"),
                a.explanation,
            )
            .select_from(a)
            .outerjoin(e, e.id == a.ips_entity_id)
            .outerjoin(LogSource, LogSource.id == source_id)
            .where(*conditions)
        )

    def _unified_pw(self, conditions: list[ColumnElement[bool]]) -> Select:
        a = PwAnomaly
        w = PropertyWatch
        return (
            select(
                a.id,
                cast(literal_column("'PW_VALUE'"), String).label("subject_kind"),
                w.log_source_id.label("log_source_id"),
                LogSource.name.label("log_source_name"),
                cast(null(), PgUUID(as_uuid=True)).label("waf_endpoint_id"),
                cast(null(), String).label("endpoint_normalized"),
                cast(null(), String).label("endpoint_method"),
                cast(null(), String).label("endpoint_virtual_host"),
                cast(null(), Boolean).label("endpoint_virtual_host_available"),
                cast(null(), Integer).label("endpoint_normalizer_version"),
                cast(null(), PgUUID(as_uuid=True)).label("ips_entity_id"),
                cast(null(), String).label("entity_dimension"),
                cast(null(), String).label("entity_value_key"),
                cast(null(), String).label("entity_display_name"),
                w.id.label("pw_watch_id"),
                w.name.label("pw_watch_name"),
                a.watch_version.label("pw_watch_version"),
                w.scope_kind.label("pw_scope_kind"),
                w.properties.label("pw_properties"),
                a.value_key.label("pw_value_key"),
                a.pw_value_id.label("pw_value_id"),
                a.detector_type.label("anomaly_type"),
                a.state, a.severity, a.observed_value, a.expected_value, a.deviation_ratio,
                a.robust_z, a.absolute_delta, a.consecutive_buckets, a.confidence,
                a.detected_at, a.opened_at, a.anomaly_start, a.anomaly_end, a.resolved_at,
                a.evidence_status, a.suppressed,
                a.observed_measurement.label("observed_measurement"),
                a.explanation,
            )
            .select_from(a)
            .join(w, w.id == a.watch_id)
            .outerjoin(LogSource, LogSource.id == w.log_source_id)
            .where(*conditions)
        )

    async def list_unified(
        self,
        *,
        limit: int,
        offset: int,
        subject_kind: SubjectKind | None = None,
        waf_endpoint_id: uuid.UUID | None = None,
        ips_entity_id: uuid.UUID | None = None,
        pw_watch_id: uuid.UUID | None = None,
        log_source_id: uuid.UUID | None = None,
        instance_id: uuid.UUID | None = None,
        anomaly_type: str | None = None,
        state: AnomalyState | None = None,
        severity: Severity | None = None,
        evidence_status: EvidenceStatus | None = None,
        active_only: bool = False,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[list[dict], int]:
        """Phase A, WAF, IPS and property-watch anomalies in one ordering: two statements.

        One `UNION ALL` of the applicable families, counted once and paged once.
        Source names and subject identity (endpoint or IPS entity) are joined
        into the branches, so a page costs no resolution query and nothing is
        merged in Python. Ordered by detection time, newest first, with the id
        as a tiebreak so a page boundary inside a tie cannot repeat or drop a
        row.
        """
        phase_a, waf, ips = self._applicable_kinds(
            subject_kind=subject_kind,
            waf_endpoint_id=waf_endpoint_id,
            ips_entity_id=ips_entity_id,
            anomaly_type=anomaly_type,
        )
        # The property-watch family (Phase G): its own subject kind and detector types. A
        # filter that names another family's subject excludes it, as for the others.
        pw = subject_kind in (None, SubjectKind.PW_VALUE)
        if waf_endpoint_id is not None or ips_entity_id is not None:
            pw = False
        if anomaly_type is not None:
            pw = pw and anomaly_type in _PW_TYPES
        if pw_watch_id is not None:
            phase_a = waf = ips = False
        active = [s.value for s in ACTIVE_ANOMALY_STATES]

        def common(model: Any) -> list[ColumnElement[bool]]:
            out: list[ColumnElement[bool]] = []
            if state is not None:
                out.append(model.state == state)
            if severity is not None:
                out.append(model.severity == severity)
            if evidence_status is not None:
                out.append(model.evidence_status == evidence_status)
            if active_only:
                out.append(model.state.in_(active))
            if since is not None:
                out.append(model.detected_at >= since)
            if until is not None:
                out.append(model.detected_at < until)
            return out

        branches: list[Select] = []
        if phase_a:
            conditions = common(LogSourceAnomaly)
            if log_source_id is not None:
                conditions.append(LogSourceAnomaly.log_source_id == log_source_id)
            if instance_id is not None:
                conditions.append(
                    LogSourceAnomaly.log_source_id.in_(
                        select(LogSource.id).where(LogSource.instance_id == instance_id)
                    )
                )
            if anomaly_type is not None:
                conditions.append(LogSourceAnomaly.anomaly_type == anomaly_type)
            branches.append(self._unified_phase_a(conditions))
        if waf:
            conditions = common(WafAnomaly)
            if log_source_id is not None:
                conditions.append(WafEndpoint.log_source_id == log_source_id)
            if instance_id is not None:
                conditions.append(WafEndpoint.instance_id == instance_id)
            if waf_endpoint_id is not None:
                conditions.append(WafAnomaly.waf_endpoint_id == waf_endpoint_id)
            if anomaly_type is not None:
                conditions.append(WafAnomaly.detector_type == anomaly_type)
            branches.append(self._unified_waf(conditions))
        if ips:
            conditions = common(IpsAnomaly)
            source_id = func.coalesce(IpsAnomaly.log_source_id, IpsEntity.log_source_id)
            if log_source_id is not None:
                conditions.append(source_id == log_source_id)
            if instance_id is not None:
                # Source incidents inherit the instance from their source, as
                # Phase A rows do; entity incidents could read it off the entity
                # row, but one expression covers both without a second join.
                conditions.append(
                    source_id.in_(
                        select(LogSource.id).where(LogSource.instance_id == instance_id)
                    )
                )
            if ips_entity_id is not None:
                conditions.append(IpsAnomaly.ips_entity_id == ips_entity_id)
            if subject_kind is not None:
                # IPS_ENTITY or IPS_SOURCE: the row's own subject discriminator,
                # so one narrows to entity incidents and the other to source ones.
                conditions.append(IpsAnomaly.subject_kind == subject_kind.value)
            if anomaly_type is not None:
                conditions.append(IpsAnomaly.detector_type == anomaly_type)
            branches.append(self._unified_ips(conditions))
        if pw:
            conditions = common(PwAnomaly)
            if log_source_id is not None:
                conditions.append(PropertyWatch.log_source_id == log_source_id)
            if instance_id is not None:
                conditions.append(PropertyWatch.instance_id == instance_id)
            if pw_watch_id is not None:
                conditions.append(PwAnomaly.watch_id == pw_watch_id)
            if anomaly_type is not None:
                conditions.append(PwAnomaly.detector_type == anomaly_type)
            branches.append(self._unified_pw(conditions))
        if not branches:
            return [], 0

        union = (branches[0] if len(branches) == 1 else union_all(*branches)).subquery()
        total = await self.session.scalar(select(func.count()).select_from(union))
        rows = await self.session.execute(
            select(union)
            .order_by(union.c.detected_at.desc(), union.c.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [dict(r._mapping) for r in rows.all()], int(total or 0)

    async def get_detail(self, anomaly_id: uuid.UUID) -> LogSourceAnomaly | None:
        """One anomaly with its lifecycle history and evidence package.

        Eager-loaded: the investigation page needs all three, and lazy loading
        them would issue a query per contributor row on an async session that
        does not permit implicit IO.
        """
        return await self.session.scalar(
            select(LogSourceAnomaly)
            .where(LogSourceAnomaly.id == anomaly_id)
            .options(
                selectinload(LogSourceAnomaly.transitions),
                selectinload(LogSourceAnomaly.explanation_package).selectinload(
                    AnomalyExplanation.dimensions
                ),
                selectinload(LogSourceAnomaly.explanation_package).selectinload(
                    AnomalyExplanation.contributors
                ),
            )
        )

    async def source_names(
        self, anomalies: list[LogSourceAnomaly]
    ) -> dict[uuid.UUID, str]:
        """Resolve source names for a page of anomalies in one query.

        One statement for the whole page rather than a relationship load per
        row: the list view renders names for every row, and lazy loading them
        on an async session that forbids implicit IO would fail outright.
        """
        ids = {a.log_source_id for a in anomalies}
        if not ids:
            return {}
        rows = await self.session.execute(
            select(LogSource.id, LogSource.name).where(LogSource.id.in_(ids))
        )
        return {row[0]: row[1] for row in rows.all()}

    # --------------------------------------------------------------- summary
    async def evidence_counts(self) -> dict[str, int]:
        """Explanation-package counts keyed by evidence status."""
        rows = await self.session.execute(
            select(LogSourceAnomaly.evidence_status, func.count()).group_by(
                LogSourceAnomaly.evidence_status
            )
        )
        return {str(status): int(count) for status, count in rows.all()}

    async def state_counts(self) -> dict[tuple[str, str], int]:
        """Anomaly counts keyed by (state, anomaly_type)."""
        rows = await self.session.execute(
            select(
                LogSourceAnomaly.state,
                LogSourceAnomaly.anomaly_type,
                func.count(),
            ).group_by(LogSourceAnomaly.state, LogSourceAnomaly.anomaly_type)
        )
        return {(str(s), str(t)): int(c) for s, t, c in rows.all()}

    async def recently_resolved(self, *, limit: int = 10) -> list[LogSourceAnomaly]:
        return list(
            (
                await self.session.scalars(
                    select(LogSourceAnomaly)
                    .where(LogSourceAnomaly.state == AnomalyState.RESOLVED)
                    .where(LogSourceAnomaly.resolved_at.is_not(None))
                    .order_by(LogSourceAnomaly.resolved_at.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def highest_deviation(self, *, limit: int = 10) -> list[LogSourceAnomaly]:
        """Active anomalies ranked by how far they departed from expectation.

        Ordered on deviation_ratio with NULLs last: an anomaly whose expected
        value was zero has no ratio, and sorting it as if it were zero would put
        the least-quantified incidents at the top.
        """
        return list(
            (
                await self.session.scalars(
                    select(LogSourceAnomaly)
                    .where(
                        LogSourceAnomaly.state.in_(
                            [s.value for s in ACTIVE_ANOMALY_STATES]
                        )
                    )
                    .order_by(LogSourceAnomaly.deviation_ratio.desc().nullslast())
                    .limit(limit)
                )
            ).all()
        )

    # -------------------------------------------------------------- behavior
    async def latest_bucket(self, log_source_id: uuid.UUID) -> LogSourceMetric | None:
        return await self.session.scalar(
            select(LogSourceMetric)
            .where(LogSourceMetric.log_source_id == log_source_id)
            .order_by(LogSourceMetric.bucket_start.desc())
            .limit(1)
        )

    async def latest_buckets(
        self, log_source_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, LogSourceMetric]:
        """The newest bucket per source, one statement whatever the fleet size.

        A source with no bucket is absent from the dict, so a caller reads
        absence as "never collected" rather than as a bucket of zeros.
        """
        ids = list(log_source_ids)
        if not ids:
            return {}
        m = LogSourceMetric
        rows = await self.session.scalars(
            select(m)
            .where(m.log_source_id.in_(ids))
            .ext(distinct_on(m.log_source_id))
            .order_by(m.log_source_id, m.bucket_start.desc())
            .options(defer(m.query_provenance))
        )
        return {r.log_source_id: r for r in rows.all()}

    async def metric_history(
        self,
        log_source_id: uuid.UUID,
        *,
        since: datetime,
        until: datetime,
        limit: int,
    ) -> list[LogSourceMetric]:
        return list(
            (
                await self.session.scalars(
                    select(LogSourceMetric)
                    .where(
                        LogSourceMetric.log_source_id == log_source_id,
                        LogSourceMetric.bucket_start >= since,
                        LogSourceMetric.bucket_start < until,
                    )
                    .order_by(LogSourceMetric.bucket_start)
                    .limit(limit)
                )
            ).all()
        )

    async def baseline_cell(
        self, log_source_id: uuid.UUID, *, weekday: int, hour: int, metric: str
    ) -> LogSourceBaseline | None:
        return await self.session.scalar(
            select(LogSourceBaseline).where(
                LogSourceBaseline.log_source_id == log_source_id,
                LogSourceBaseline.weekday == weekday,
                LogSourceBaseline.hour == hour,
                LogSourceBaseline.metric_name == metric,
            )
        )

    async def baseline_cells(
        self,
        keys: Iterable[tuple[uuid.UUID, int, int]],
        *,
        metric: str,
    ) -> dict[tuple[uuid.UUID, int, int], LogSourceBaseline]:
        """The seasonal cells for the given (source, weekday, hour) keys.

        One row-value `IN` statement whatever the number of keys. A key with no
        stored cell is absent from the dict, exactly as `baseline_cell` returns
        None for it, so an unbuilt cell stays distinguishable from a built one.
        """
        pairs = list(keys)
        if not pairs:
            return {}
        b = LogSourceBaseline
        rows = await self.session.scalars(
            select(b).where(
                b.metric_name == metric,
                tuple_(b.log_source_id, b.weekday, b.hour).in_(pairs),
            )
        )
        return {(r.log_source_id, r.weekday, r.hour): r for r in rows.all()}

    async def baselines_for(
        self, log_source_id: uuid.UUID, *, metric: str
    ) -> list[LogSourceBaseline]:
        return list(
            (
                await self.session.scalars(
                    select(LogSourceBaseline)
                    .where(
                        LogSourceBaseline.log_source_id == log_source_id,
                        LogSourceBaseline.metric_name == metric,
                    )
                    .order_by(LogSourceBaseline.weekday, LogSourceBaseline.hour)
                )
            ).all()
        )

    async def monitored_sources(self) -> list[LogSource]:
        return list(
            (
                await self.session.scalars(
                    select(LogSource)
                    .where(LogSource.monitoring_enabled.is_(True))
                    .order_by(LogSource.name)
                )
            ).all()
        )

    async def active_counts_by_source(self) -> dict[uuid.UUID, int]:
        rows = await self.session.execute(
            select(LogSourceAnomaly.log_source_id, func.count())
            .where(
                LogSourceAnomaly.state.in_([s.value for s in ACTIVE_ANOMALY_STATES])
            )
            .group_by(LogSourceAnomaly.log_source_id)
        )
        return {sid: int(count) for sid, count in rows.all()}

    # ------------------------------------------------------------ WAF detail
    async def get_waf_detail(self, anomaly_id: uuid.UUID) -> WafAnomaly | None:
        """One WAF anomaly with its endpoint and lifecycle history, eager-loaded."""
        return await self.session.scalar(
            select(WafAnomaly)
            .where(WafAnomaly.id == anomaly_id)
            .options(selectinload(WafAnomaly.transitions), selectinload(WafAnomaly.endpoint))
        )

    # ------------------------------------------------------------ IPS detail
    async def get_ips_detail(
        self, anomaly_id: uuid.UUID
    ) -> tuple[IpsAnomaly, IpsEntity | None, list[IpsAnomalyTransition]] | None:
        """One IPS anomaly with its entity and lifecycle history, eager-loaded.

        The IPS models declare no relationships, so the entity is joined into
        the fetch and the transitions read in their own statement rather than
        selectin-loaded; the investigation page needs all three either way.
        """
        row = (
            await self.session.execute(
                select(IpsAnomaly, IpsEntity)
                .outerjoin(IpsEntity, IpsEntity.id == IpsAnomaly.ips_entity_id)
                .where(IpsAnomaly.id == anomaly_id)
            )
        ).first()
        if row is None:
            return None
        transitions = list(
            (
                await self.session.scalars(
                    select(IpsAnomalyTransition)
                    .where(IpsAnomalyTransition.ips_anomaly_id == anomaly_id)
                    .order_by(IpsAnomalyTransition.occurred_at)
                )
            ).all()
        )
        return row[0], row[1], transitions

    # ------------------------------------------------------------- PW detail
    async def get_pw_detail(
        self, anomaly_id: uuid.UUID
    ) -> tuple[PwAnomaly, PropertyWatch, LogSource | None, list[PwAnomalyTransition]] | None:
        """One property-watch incident, its watch, its source and its lifecycle: three statements.

        The incident joins its watch (the incident goes with the watch, so the join is inner)
        and the watch's source when it has one; the transitions are read in their own
        statement, oldest first. The PW models declare no relationships.
        """
        row = (
            await self.session.execute(
                select(PwAnomaly, PropertyWatch, LogSource)
                .join(PropertyWatch, PropertyWatch.id == PwAnomaly.watch_id)
                .outerjoin(LogSource, LogSource.id == PropertyWatch.log_source_id)
                .where(PwAnomaly.id == anomaly_id)
            )
        ).first()
        if row is None:
            return None
        transitions = list(
            (
                await self.session.scalars(
                    select(PwAnomalyTransition)
                    .where(PwAnomalyTransition.pw_anomaly_id == anomaly_id)
                    .order_by(PwAnomalyTransition.occurred_at, PwAnomalyTransition.created_at)
                )
            ).all()
        )
        return row[0], row[1], row[2], transitions

    async def virtual_host_available(self, log_source_id: uuid.UUID) -> bool:
        """Whether the source's profile maps a virtual host property."""
        mapped = await self.session.scalar(
            select(WafSourceProfile.virtual_host_property.is_not(None)).where(
                WafSourceProfile.log_source_id == log_source_id
            )
        )
        return bool(mapped)

    # ---------------------------------------------------------- WAF sources
    async def waf_sources_page(
        self, *, limit: int, offset: int
    ) -> tuple[list[tuple[WafSourceProfile, LogSource]], int]:
        total = await self.session.scalar(select(func.count()).select_from(WafSourceProfile))
        rows = await self.session.execute(
            select(WafSourceProfile, LogSource)
            .join(LogSource, LogSource.id == WafSourceProfile.log_source_id)
            .order_by(LogSource.name, WafSourceProfile.id)
            .limit(limit)
            .offset(offset)
        )
        return [(p, ls) for p, ls in rows.all()], int(total or 0)

    async def waf_source(
        self, log_source_id: uuid.UUID
    ) -> tuple[WafSourceProfile, LogSource] | None:
        row = (
            await self.session.execute(
                select(WafSourceProfile, LogSource)
                .join(LogSource, LogSource.id == WafSourceProfile.log_source_id)
                .where(WafSourceProfile.log_source_id == log_source_id)
            )
        ).first()
        return (row[0], row[1]) if row is not None else None

    async def waf_latest_buckets(
        self, log_source_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, WafSourceBucket]:
        """The newest collected bucket per source, in one statement."""
        if not log_source_ids:
            return {}
        b = WafSourceBucket
        rows = await self.session.scalars(
            select(b)
            .where(b.log_source_id.in_(log_source_ids))
            .ext(distinct_on(b.log_source_id))
            .order_by(b.log_source_id, b.bucket_start.desc())
            .options(defer(b.query_provenance))
        )
        return {r.log_source_id: r for r in rows.all()}

    async def waf_endpoint_counts(
        self, log_source_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[int, int]]:
        """(active under the current normalizer, all) endpoint counts per source."""
        if not log_source_ids:
            return {}
        e = WafEndpoint
        rows = await self.session.execute(
            select(
                e.log_source_id,
                func.count().filter(
                    e.is_active.is_(True), e.normalizer_version == ENDPOINT_NORMALIZER_VERSION
                ),
                func.count(),
            )
            .where(e.log_source_id.in_(log_source_ids))
            .group_by(e.log_source_id)
        )
        return {sid: (int(active), int(total)) for sid, active, total in rows.all()}

    async def waf_admission_overflow(
        self, log_source_ids: list[uuid.UUID], *, since: datetime, until: datetime
    ) -> dict[uuid.UUID, tuple[int, int, int, int, int]]:
        """Per source over the collected buckets in range: (buckets, truncated,
        rejected over quota, below threshold, untracked requests)."""
        if not log_source_ids:
            return {}
        b = WafSourceBucket
        rows = await self.session.execute(
            select(
                b.log_source_id,
                func.count(),
                func.count().filter(b.admission_truncated.is_(True)),
                func.coalesce(func.sum(b.rejected_over_quota), 0),
                func.coalesce(func.sum(b.below_admission_threshold), 0),
                func.coalesce(func.sum(b.untracked_requests), 0),
            )
            .where(
                b.log_source_id.in_(log_source_ids),
                b.bucket_start >= since,
                b.bucket_start < until,
            )
            .group_by(b.log_source_id)
        )
        return {
            sid: (int(n), int(trunc), int(rej), int(below), int(untracked))
            for sid, n, trunc, rej, below, untracked in rows.all()
        }

    async def waf_virtual_host_mapped(self, log_source_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        return set(
            (
                await self.session.scalars(
                    select(WafSourceProfile.log_source_id).where(
                        WafSourceProfile.log_source_id.in_(log_source_ids),
                        WafSourceProfile.virtual_host_property.is_not(None),
                    )
                )
            ).all()
        )

    # -------------------------------------------------------- WAF endpoints
    async def waf_endpoints_page(
        self,
        log_source_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        active_only: bool,
        seen_since: datetime | None,
        seen_until: datetime | None,
    ) -> tuple[list[WafEndpoint], int]:
        e = WafEndpoint
        conditions = [e.log_source_id == log_source_id]
        if active_only:
            conditions.append(e.is_active.is_(True))
        if seen_since is not None:
            conditions.append(e.last_seen_at >= seen_since)
        if seen_until is not None:
            conditions.append(e.last_seen_at < seen_until)
        total = await self.session.scalar(select(func.count()).select_from(e).where(*conditions))
        rows = await self.session.scalars(
            select(e)
            .where(*conditions)
            .order_by(e.normalized_endpoint, e.http_method, e.virtual_host, e.id)
            .limit(limit)
            .offset(offset)
        )
        return list(rows.all()), int(total or 0)

    async def waf_baseline_adequacy(
        self, endpoint_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[int, int]]:
        """(cells, reliable cells) of the request-count baseline per endpoint.

        Counted for the endpoint's own normalizer version only: a cell from another
        version is stale and proves nothing about this entity.
        """
        if not endpoint_ids:
            return {}
        b = WafEndpointBaseline
        rows = await self.session.execute(
            select(b.waf_endpoint_id, func.count(), func.count().filter(b.is_reliable.is_(True)))
            .join(WafEndpoint, WafEndpoint.id == b.waf_endpoint_id)
            .where(
                b.waf_endpoint_id.in_(endpoint_ids),
                b.metric_name == WAF_BASELINE_METRIC,
                b.normalizer_version == WafEndpoint.normalizer_version,
            )
            .group_by(b.waf_endpoint_id)
        )
        return {eid: (int(n), int(reliable)) for eid, n, reliable in rows.all()}

    async def waf_detector_states(
        self, endpoint_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[str]]:
        """Every detector's lifecycle state per endpoint."""
        if not endpoint_ids:
            return {}
        out: dict[uuid.UUID, list[str]] = {}
        rows = await self.session.execute(
            select(WafDetectorState.waf_endpoint_id, WafDetectorState.state).where(
                WafDetectorState.waf_endpoint_id.in_(endpoint_ids)
            )
        )
        for eid, state in rows.all():
            out.setdefault(eid, []).append(str(state))
        return out

    async def waf_active_anomaly_counts(
        self, endpoint_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        if not endpoint_ids:
            return {}
        rows = await self.session.execute(
            select(WafAnomaly.waf_endpoint_id, func.count())
            .where(
                WafAnomaly.waf_endpoint_id.in_(endpoint_ids),
                WafAnomaly.state.in_([s.value for s in ACTIVE_ANOMALY_STATES]),
            )
            .group_by(WafAnomaly.waf_endpoint_id)
        )
        return {eid: int(n) for eid, n in rows.all()}

    # ----------------------------------------------- WAF metrics, baselines
    async def waf_metrics_page(
        self,
        endpoint: WafEndpoint,
        *,
        since: datetime,
        until: datetime,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[WafEndpointMetric, WafSourceBucket | None]], int]:
        """Endpoint buckets in range with the source's bucket record, if any.

        The bucket record carries what the endpoint row cannot: whether the counts
        are exact, and each breakdown's own status. It is outer-joined, so a row
        whose bucket record is missing is reported with those fields unknown.
        """
        m = WafEndpointMetric
        b = WafSourceBucket
        window = (
            m.waf_endpoint_id == endpoint.id,
            m.bucket_start >= since,
            m.bucket_start < until,
        )
        total = await self.session.scalar(select(func.count()).select_from(m).where(*window))
        rows = await self.session.execute(
            select(m, b)
            .outerjoin(
                b,
                (b.log_source_id == endpoint.log_source_id) & (b.bucket_start == m.bucket_start),
            )
            .where(*window)
            .order_by(m.bucket_start)
            .limit(limit)
            .offset(offset)
            .options(defer(m.query_provenance), defer(m.top_clients), defer(b.query_provenance))
        )
        return [(row[0], row[1]) for row in rows.all()], int(total or 0)

    async def waf_baselines_page(
        self, endpoint_id: uuid.UUID, *, metric: str | None, limit: int, offset: int
    ) -> tuple[list[WafEndpointBaseline], int]:
        b = WafEndpointBaseline
        conditions = [b.waf_endpoint_id == endpoint_id]
        if metric is not None:
            conditions.append(b.metric_name == metric)
        total = await self.session.scalar(select(func.count()).select_from(b).where(*conditions))
        rows = await self.session.scalars(
            select(b)
            .where(*conditions)
            .order_by(b.metric_name, b.normalizer_version, b.weekday, b.hour)
            .limit(limit)
            .offset(offset)
            .options(defer(b.observations), defer(b.exclusion_counts))
        )
        return list(rows.all()), int(total or 0)
