"""Query helpers for Phase A behavioral analytics.

Keeps the route handlers free of query construction, and keeps the pagination
and range guards in one place where they cannot be forgotten on a new endpoint.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Iterable
from datetime import datetime
from typing import Any, NamedTuple

from sqlalchemy import (
    Boolean,
    ColumnElement,
    Float,
    Integer,
    Select,
    String,
    and_,
    case,
    cast,
    exists,
    func,
    literal_column,
    null,
    or_,
    select,
    true,
    tuple_,
    union_all,
)
from sqlalchemy.dialects.postgresql import JSONB, distinct_on
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, selectinload

from app.models.enums import (
    ACTIVE_ANOMALY_STATES,
    AnomalyFamily,
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    EvidenceStatus,
    IdentityAnomalyType,
    IpsAnomalyType,
    PwAnomalyType,
    RuleAnomalyType,
    Severity,
    TriageDisposition,
    TriageStatus,
    WafAnomalyType,
    WafMeasurement,
)
from app.models.explanation import AnomalyExplanation
from app.models.identity_anomaly import IdentityAnomaly, IdentityAnomalyTransition
from app.models.ips import IpsAnomaly, IpsAnomalyTransition, IpsEntity
from app.models.log_source import (
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceMetric,
)
from app.models.property_watch import PropertyWatch, PwAnomaly, PwAnomalyTransition
from app.models.rule import AnalyticsRule
from app.models.rule_anomaly import RuleAnomaly, RuleAnomalyTransition
from app.models.triage import AnomalyTriage
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
_RULE_TYPES = frozenset(t.value for t in RuleAnomalyType)

_WAF_TYPES = frozenset(t.value for t in WafAnomalyType)
_IPS_TYPES = frozenset(t.value for t in IpsAnomalyType)
_PW_TYPES = frozenset(t.value for t in PwAnomalyType)
_IDENTITY_TYPES = frozenset(t.value for t in IdentityAnomalyType)

SubjectKindFilter = SubjectKind | Collection[SubjectKind] | None


def _rule_measurement(a: type[RuleAnomaly]) -> ColumnElement[str]:
    """`LOWER_BOUND` when the incident's stored evidence says its window was truncated.

    The flag is `details.extra.truncated_window`, written by the rule engine for a storm
    judged on a partial count: the count can only understate the truth. Any other value, or
    no flag, reads as the exact count the other families report.
    """
    flag = a.details["extra"]["truncated_window"]
    # A JSON boolean true only: the string "true" is not the engine's flag.
    truncated = and_(func.jsonb_typeof(flag) == "boolean", flag.astext == "true")
    return case(
        (truncated, WafMeasurement.LOWER_BOUND.value),
        else_=WafMeasurement.EXACT.value,
    )


#: The measurement states in which a WAF bucket's endpoint counts are usable.
_WAF_MEASURED = (WafMeasurement.EXACT.value, WafMeasurement.LOWER_BOUND.value)


class WafTrafficRow(NamedTuple):
    """One ranked WAF endpoint in the traffic page."""

    id: uuid.UUID
    events: int
    buckets_with_data: int
    partial_buckets: int
    peak_rate_per_second: float
    peak_bucket_start: datetime
    virtual_host: str | None
    http_method: str
    normalized_endpoint: str
    active: bool


class WafTrafficPage(NamedTuple):
    """A page of ranked endpoints and the whole set's summary."""

    items: list[WafTrafficRow]
    total: int
    max_events: int | None
    attributed_events: int


def _null_identity_event() -> ColumnElement[Any]:
    """The identity event id of a union branch whose subject is not an identity source."""
    return cast(null(), Integer).label("identity_event_id")


def _null_rule_columns() -> list[ColumnElement[Any]]:
    """The rule-subject columns of a union branch whose subject is not a rule (SOC-6d1)."""
    return [
        cast(null(), PgUUID(as_uuid=True)).label("rule_id"),
        cast(null(), String).label("rule_name"),
        cast(null(), Integer).label("rule_qradar_id"),
        cast(null(), Boolean).label("rule_is_building_block"),
    ]


class AnomalyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _kind_set(subject_kind: SubjectKindFilter) -> frozenset[SubjectKind] | None:
        """The requested kinds, deduplicated; None keeps the unfiltered population."""
        if subject_kind is None:
            return None
        if isinstance(subject_kind, SubjectKind):
            return frozenset((subject_kind,))
        return frozenset(subject_kind)

    # ------------------------------------------------------- unified list
    @staticmethod
    def _applicable_kinds(
        *,
        subject_kind: SubjectKindFilter,
        waf_endpoint_id: uuid.UUID | None,
        ips_entity_id: uuid.UUID | None,
        anomaly_type: str | None,
    ) -> tuple[bool, bool, bool]:
        """Which anomaly families a filter set can match: (Phase A, WAF, IPS).

        A filter that cannot apply to a family excludes it. Ignoring it would
        return rows the caller asked not to see.
        """
        kinds = AnomalyRepository._kind_set(subject_kind)
        phase_a = kinds is None or SubjectKind.LOG_SOURCE in kinds
        waf = kinds is None or SubjectKind.WAF_ENDPOINT in kinds
        ips = kinds is None or bool(
            kinds.intersection((SubjectKind.IPS_ENTITY, SubjectKind.IPS_SOURCE))
        )
        if waf_endpoint_id is not None:
            phase_a = False
            ips = False
        if ips_entity_id is not None:
            phase_a = False
            waf = False
            # An entity id names an entity incident; a source-incident filter
            # contradicts it.
            ips = ips and (kinds is None or SubjectKind.IPS_ENTITY in kinds)
        if anomaly_type is not None:
            phase_a = phase_a and anomaly_type in _PHASE_A_TYPES
            waf = waf and anomaly_type in _WAF_TYPES
            ips = ips and anomaly_type in _IPS_TYPES
        return phase_a, waf, ips

    def _unified_phase_a(self, conditions: list[ColumnElement[bool]]) -> Select:
        a = LogSourceAnomaly
        t = AnomalyTriage
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
                *_null_rule_columns(),
                a.anomaly_type.label("anomaly_type"),
                a.state, a.severity, a.observed_value, a.expected_value, a.deviation_ratio,
                a.robust_z, a.absolute_delta, a.consecutive_buckets, a.confidence,
                a.detected_at, a.opened_at, a.anomaly_start, a.anomaly_end, a.resolved_at,
                a.evidence_status, a.suppressed,
                cast(null(), String).label("observed_measurement"),
                a.explanation,
                a.explanation_i18n.label("explanation_i18n"),
                t.status.label("triage_status"),
                t.owner.label("triage_owner"),
                t.disposition.label("triage_disposition"),
                t.updated_at.label("triage_updated_at"),
                _null_identity_event(),
            )
            .select_from(a)
            .outerjoin(LogSource, LogSource.id == a.log_source_id)
            .outerjoin(
                t,
                and_(t.family == AnomalyFamily.LOG_SOURCE, t.anomaly_id == a.id),
            )
            .where(*conditions)
        )

    def _unified_waf(self, conditions: list[ColumnElement[bool]]) -> Select:
        w = WafAnomaly
        e = WafEndpoint
        t = AnomalyTriage
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
                *_null_rule_columns(),
                w.detector_type.label("anomaly_type"),
                w.state, w.severity, w.observed_value, w.expected_value, w.deviation_ratio,
                w.robust_z, w.absolute_delta, w.consecutive_buckets, w.confidence,
                w.detected_at, w.opened_at, w.anomaly_start, w.anomaly_end, w.resolved_at,
                w.evidence_status, w.suppressed,
                w.observed_measurement.label("observed_measurement"),
                w.explanation,
                w.explanation_i18n.label("explanation_i18n"),
                t.status.label("triage_status"),
                t.owner.label("triage_owner"),
                t.disposition.label("triage_disposition"),
                t.updated_at.label("triage_updated_at"),
                _null_identity_event(),
            )
            .select_from(w)
            .join(e, e.id == w.waf_endpoint_id)
            .outerjoin(LogSource, LogSource.id == e.log_source_id)
            .outerjoin(
                t,
                and_(t.family == AnomalyFamily.WAF, t.anomaly_id == w.id),
            )
            .where(*conditions)
        )

    def _unified_ips(self, conditions: list[ColumnElement[bool]]) -> Select:
        a = IpsAnomaly
        e = IpsEntity
        t = AnomalyTriage
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
                *_null_rule_columns(),
                a.detector_type.label("anomaly_type"),
                a.state, a.severity, a.observed_value, a.expected_value, a.deviation_ratio,
                a.robust_z, a.absolute_delta, a.consecutive_buckets, a.confidence,
                a.detected_at, a.opened_at, a.anomaly_start, a.anomaly_end, a.resolved_at,
                a.evidence_status, a.suppressed,
                a.observed_measurement.label("observed_measurement"),
                a.explanation,
                a.explanation_i18n.label("explanation_i18n"),
                t.status.label("triage_status"),
                t.owner.label("triage_owner"),
                t.disposition.label("triage_disposition"),
                t.updated_at.label("triage_updated_at"),
                _null_identity_event(),
            )
            .select_from(a)
            .outerjoin(e, e.id == a.ips_entity_id)
            .outerjoin(LogSource, LogSource.id == source_id)
            .outerjoin(
                t,
                and_(t.family == AnomalyFamily.IPS, t.anomaly_id == a.id),
            )
            .where(*conditions)
        )

    def _unified_pw(self, conditions: list[ColumnElement[bool]]) -> Select:
        a = PwAnomaly
        w = PropertyWatch
        t = AnomalyTriage
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
                *_null_rule_columns(),
                a.detector_type.label("anomaly_type"),
                a.state, a.severity, a.observed_value, a.expected_value, a.deviation_ratio,
                a.robust_z, a.absolute_delta, a.consecutive_buckets, a.confidence,
                a.detected_at, a.opened_at, a.anomaly_start, a.anomaly_end, a.resolved_at,
                a.evidence_status, a.suppressed,
                a.observed_measurement.label("observed_measurement"),
                a.explanation,
                a.explanation_i18n.label("explanation_i18n"),
                t.status.label("triage_status"),
                t.owner.label("triage_owner"),
                t.disposition.label("triage_disposition"),
                t.updated_at.label("triage_updated_at"),
                _null_identity_event(),
            )
            .select_from(a)
            .join(w, w.id == a.watch_id)
            .outerjoin(LogSource, LogSource.id == w.log_source_id)
            .outerjoin(
                t,
                and_(t.family == AnomalyFamily.PROPERTY_WATCH, t.anomaly_id == a.id),
            )
            .where(*conditions)
        )

    def _unified_rule(self, conditions: list[ColumnElement[bool]]) -> Select:
        a = RuleAnomaly
        r = AnalyticsRule
        t = AnomalyTriage
        return (
            select(
                a.id,
                cast(literal_column("'RULE'"), String).label("subject_kind"),
                cast(null(), PgUUID(as_uuid=True)).label("log_source_id"),
                cast(null(), String).label("log_source_name"),
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
                a.rule_id.label("rule_id"),
                r.name.label("rule_name"),
                a.qradar_id.label("rule_qradar_id"),
                a.is_building_block.label("rule_is_building_block"),
                a.detector_type.label("anomaly_type"),
                a.state, a.severity, a.observed_value, a.expected_value, a.deviation_ratio,
                a.robust_z, a.absolute_delta, a.consecutive_buckets, a.confidence,
                a.detected_at, a.opened_at, a.anomaly_start, a.anomaly_end, a.resolved_at,
                a.evidence_status, a.suppressed,
                _rule_measurement(a).label("observed_measurement"),
                a.explanation,
                a.explanation_i18n.label("explanation_i18n"),
                t.status.label("triage_status"),
                t.owner.label("triage_owner"),
                t.disposition.label("triage_disposition"),
                t.updated_at.label("triage_updated_at"),
                _null_identity_event(),
            )
            .select_from(a)
            .join(r, r.id == a.rule_id)
            .outerjoin(t, and_(t.family == AnomalyFamily.RULE, t.anomaly_id == a.id))
            .where(*conditions)
        )

    def _unified_identity(self, conditions: list[ColumnElement[bool]]) -> Select:
        a = IdentityAnomaly
        t = AnomalyTriage
        return (
            select(
                a.id,
                cast(literal_column("'IDENTITY_SOURCE'"), String).label("subject_kind"),
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
                *_null_rule_columns(),
                a.detector_type.label("anomaly_type"),
                a.state, a.severity, a.observed_value, a.expected_value, a.deviation_ratio,
                a.robust_z, a.absolute_delta, a.consecutive_buckets, a.confidence,
                a.detected_at, a.opened_at, a.anomaly_start, a.anomaly_end, a.resolved_at,
                a.evidence_status, a.suppressed,
                a.observed_measurement.label("observed_measurement"),
                a.explanation,
                a.explanation_i18n.label("explanation_i18n"),
                t.status.label("triage_status"),
                t.owner.label("triage_owner"),
                t.disposition.label("triage_disposition"),
                t.updated_at.label("triage_updated_at"),
                a.event_id.label("identity_event_id"),
            )
            .select_from(a)
            .outerjoin(LogSource, LogSource.id == a.log_source_id)
            .outerjoin(
                t,
                and_(t.family == AnomalyFamily.IDENTITY, t.anomaly_id == a.id),
            )
            .where(*conditions)
        )

    async def list_unified(
        self,
        *,
        limit: int,
        offset: int,
        subject_kind: SubjectKindFilter = None,
        waf_endpoint_id: uuid.UUID | None = None,
        ips_entity_id: uuid.UUID | None = None,
        pw_watch_id: uuid.UUID | None = None,
        log_source_id: uuid.UUID | None = None,
        instance_id: uuid.UUID | None = None,
        anomaly_type: str | None = None,
        state: AnomalyState | None = None,
        severity: Severity | None = None,
        evidence_status: EvidenceStatus | None = None,
        triage_status: TriageStatus | None = None,
        triage_owner: str | None = None,
        triage_disposition: TriageDisposition | None = None,
        active_only: bool = False,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[list[dict], int]:
        """Phase A, WAF, IPS, property-watch, rule and identity anomalies in one ordering.

        One `UNION ALL` of the applicable families, counted once and paged once.
        Source names and subject identity (endpoint or IPS entity) are joined
        into the branches, so a page costs no resolution query and nothing is
        merged in Python. Ordered by detection time, newest first, with the id
        as a tiebreak so a page boundary inside a tie cannot repeat or drop a
        row.
        """
        kinds = self._kind_set(subject_kind)
        phase_a, waf, ips = self._applicable_kinds(
            subject_kind=kinds,
            waf_endpoint_id=waf_endpoint_id,
            ips_entity_id=ips_entity_id,
            anomaly_type=anomaly_type,
        )
        # The property-watch family (Phase G): its own subject kind and detector types. A
        # filter that names another family's subject excludes it, as for the others.
        pw = kinds is None or SubjectKind.PW_VALUE in kinds
        if waf_endpoint_id is not None or ips_entity_id is not None:
            pw = False
        if anomaly_type is not None:
            pw = pw and anomaly_type in _PW_TYPES
        if pw_watch_id is not None:
            phase_a = waf = ips = False
        # The rule family (SOC-6): a rule has no log source, endpoint, entity or watch, so a
        # filter naming one of those excludes it, as it excludes the other families.
        rule = kinds is None or SubjectKind.RULE in kinds
        if (
            waf_endpoint_id is not None
            or ips_entity_id is not None
            or pw_watch_id is not None
            or log_source_id is not None
        ):
            rule = False
        if anomaly_type is not None:
            rule = rule and anomaly_type in _RULE_TYPES
        # Identity matches a log source, unlike a rule. A filter that names another
        # family's subject, endpoint, entity or watch excludes it.
        identity = kinds is None or SubjectKind.IDENTITY_SOURCE in kinds
        if waf_endpoint_id is not None or ips_entity_id is not None or pw_watch_id is not None:
            identity = False
        if anomaly_type is not None:
            identity = identity and anomaly_type in _IDENTITY_TYPES
        active = [s.value for s in ACTIVE_ANOMALY_STATES]

        def triage_filters() -> list[ColumnElement[bool]]:
            """The analyst-state filters, applied inside every union branch.

            The triage table is left-joined per branch, so a condition on it filters
            before the union's limit and offset; a post-filter would return a short
            page and a total that disagree with it. `NEW` and owner `none` include an
            anomaly with no triage row, which is exactly what a missing row reads as.
            """
            out: list[ColumnElement[bool]] = []
            if triage_status is not None:
                if triage_status is TriageStatus.NEW:
                    out.append(
                        or_(
                            AnomalyTriage.status == TriageStatus.NEW,
                            AnomalyTriage.id.is_(None),
                        )
                    )
                else:
                    out.append(AnomalyTriage.status == triage_status)
            if triage_owner is not None:
                if triage_owner == "none":
                    out.append(AnomalyTriage.owner.is_(None))
                else:
                    out.append(AnomalyTriage.owner == triage_owner)
            if triage_disposition is not None:
                out.append(AnomalyTriage.disposition == triage_disposition)
            return out

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
            out.extend(triage_filters())
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
            if kinds is not None:
                # The row owns the IPS discriminator. A repeated query value therefore
                # stays one branch and one statement rather than one query per kind.
                ips_kinds = [
                    kind.value
                    for kind in (SubjectKind.IPS_ENTITY, SubjectKind.IPS_SOURCE)
                    if kind in kinds
                ]
                conditions.append(IpsAnomaly.subject_kind.in_(ips_kinds))
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
        if rule:
            conditions = common(RuleAnomaly)
            if instance_id is not None:
                conditions.append(AnalyticsRule.instance_id == instance_id)
            if anomaly_type is not None:
                conditions.append(RuleAnomaly.detector_type == anomaly_type)
            branches.append(self._unified_rule(conditions))
        if identity:
            conditions = common(IdentityAnomaly)
            if log_source_id is not None:
                conditions.append(IdentityAnomaly.log_source_id == log_source_id)
            if instance_id is not None:
                conditions.append(
                    IdentityAnomaly.log_source_id.in_(
                        select(LogSource.id).where(LogSource.instance_id == instance_id)
                    )
                )
            if anomaly_type is not None:
                conditions.append(IdentityAnomaly.detector_type == anomaly_type)
            branches.append(self._unified_identity(conditions))
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

    async def list_queue(
        self,
        *,
        limit: int,
        subject: str,
        scope: str,
        compare_from: datetime,
        families: Collection[AnomalyFamily] | None = None,
        severities: Collection[Severity] | None = None,
        new_only: bool = False,
    ) -> tuple[list[dict], int, int]:
        """The anomalies waiting on an analyst (SOC-4b): one statement, top `limit` rows.

        Returns `(rows, total, new_count)`. `total` and `new_count` are window counts over
        the whole filtered set, so the page cut costs no second statement.

        Waiting means: triage NEW (no row counts as NEW) or IN_PROGRESS, detector state OPEN,
        RECOVERING or RESOLVED (CANDIDATE was never promoted to an incident), and not
        suppressed. The conditions sit inside the union branches, as in `list_unified`.

        Ordered severity descending, then oldest first, then id; the caller merges these rows
        with the other sources' and cuts the page, so it asks for `offset + limit` rows here.

        `change` is NEW when the incident became real after `compare_from`, UPDATED when the
        detector resolved it after `compare_from` or someone other than `subject` changed its
        triage after it. A change by `subject` never flags the row.
        """
        wanted = set(families) if families is not None else None
        waiting_states = [
            AnomalyState.OPEN.value,
            AnomalyState.RECOVERING.value,
            AnomalyState.RESOLVED.value,
        ]
        severity_values = [s.value for s in severities] if severities is not None else None

        def common(model: Any) -> list[ColumnElement[bool]]:
            out: list[ColumnElement[bool]] = [
                model.state.in_(waiting_states),
                model.suppressed.is_(False),
                or_(
                    AnomalyTriage.id.is_(None),
                    AnomalyTriage.status.in_(
                        [TriageStatus.NEW.value, TriageStatus.IN_PROGRESS.value]
                    ),
                ),
            ]
            if severity_values is not None:
                out.append(model.severity.in_(severity_values))
            if scope == "mine":
                out.append(AnomalyTriage.owner == subject)
            elif scope == "unassigned":
                out.append(AnomalyTriage.owner.is_(None))
            return out

        branches: list[Select] = []
        if wanted is None or AnomalyFamily.LOG_SOURCE in wanted:
            branches.append(self._unified_phase_a(common(LogSourceAnomaly)))
        if wanted is None or AnomalyFamily.WAF in wanted:
            branches.append(self._unified_waf(common(WafAnomaly)))
        if wanted is None or AnomalyFamily.IPS in wanted:
            branches.append(self._unified_ips(common(IpsAnomaly)))
        if wanted is None or AnomalyFamily.PROPERTY_WATCH in wanted:
            branches.append(self._unified_pw(common(PwAnomaly)))
        if wanted is None or AnomalyFamily.RULE in wanted:
            branches.append(self._unified_rule(common(RuleAnomaly)))
        if wanted is None or AnomalyFamily.IDENTITY in wanted:
            branches.append(self._unified_identity(common(IdentityAnomaly)))
        if not branches:
            return [], 0, 0

        union = (branches[0] if len(branches) == 1 else union_all(*branches)).subquery()
        # The triage row again, outside the union, for the two columns the branches do not
        # carry (`version`, `updated_by`). The family is derived from the subject kind.
        family_of = case(
            (union.c.subject_kind == SubjectKind.LOG_SOURCE.value, AnomalyFamily.LOG_SOURCE.value),
            (union.c.subject_kind == SubjectKind.WAF_ENDPOINT.value, AnomalyFamily.WAF.value),
            (
                union.c.subject_kind.in_(
                    [SubjectKind.IPS_ENTITY.value, SubjectKind.IPS_SOURCE.value]
                ),
                AnomalyFamily.IPS.value,
            ),
            (union.c.subject_kind == SubjectKind.RULE.value, AnomalyFamily.RULE.value),
            (
                union.c.subject_kind == SubjectKind.IDENTITY_SOURCE.value,
                AnomalyFamily.IDENTITY.value,
            ),
            else_=AnomalyFamily.PROPERTY_WATCH.value,
        )
        triage = AnomalyTriage.__table__.alias("queue_triage")
        waiting_since = func.coalesce(union.c.opened_at, union.c.detected_at)
        is_new = waiting_since > compare_from
        is_updated = or_(
            union.c.resolved_at > compare_from,
            and_(triage.c.updated_at > compare_from, triage.c.updated_by != subject),
        )
        change = case(
            (is_new, "NEW"),
            (is_updated, "UPDATED"),
            else_=null(),
        )
        severity_rank = case(
            {"CRITICAL": 5, "HIGH": 4, "MEDIUM": 3, "LOW": 2, "INFO": 1},
            value=union.c.severity,
            else_=0,
        )
        stmt = (
            select(
                union,
                triage.c.version.label("triage_version"),
                waiting_since.label("waiting_since"),
                change.label("change"),
                severity_rank.label("severity_rank"),
                func.count().over().label("queue_total"),
                func.count().filter(change.is_not(None)).over().label("queue_new"),
            )
            .select_from(union)
            .outerjoin(
                triage,
                and_(triage.c.anomaly_id == union.c.id, triage.c.family == family_of),
            )
            .order_by(severity_rank.desc(), waiting_since.asc(), union.c.id.asc())
            .limit(limit)
        )
        if new_only:
            stmt = stmt.where(change.is_not(None))
        rows = [dict(r._mapping) for r in (await self.session.execute(stmt)).all()]
        if not rows:
            return [], 0, 0
        return rows, int(rows[0]["queue_total"]), int(rows[0]["queue_new"])

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

    async def active_family_counts(
        self, *, since: datetime | None, until: datetime
    ) -> dict[tuple[str, str], int]:
        """Active anomalies keyed by (family, state): one statement over the six families.

        The population is `list_unified(active_only=True)` for the same window --
        the same states, the same `detected_at` bounds and the same joins -- so the
        counts add up to that list's `total`. Without a `since` there is no lower
        bound, as on that list: an active incident older than the range ceiling is
        still active. The family key is `log_source`, `waf`, `ips`,
        `property_watch`, `rule` or `identity`.
        """
        active = [s.value for s in ACTIVE_ANOMALY_STATES]

        def branch(family: str, model: Any, *joins: Any) -> Select:
            stmt = select(
                literal_column(f"'{family}'").label("family"),
                model.state.label("state"),
                func.count().label("n"),
            ).select_from(model)
            for target, on in joins:
                stmt = stmt.join(target, on)
            conditions: list[ColumnElement[bool]] = [
                model.state.in_(active),
                model.detected_at < until,
            ]
            if since is not None:
                conditions.append(model.detected_at >= since)
            return stmt.where(*conditions).group_by(model.state)

        union = union_all(
            branch("log_source", LogSourceAnomaly),
            branch(
                "waf", WafAnomaly, (WafEndpoint, WafEndpoint.id == WafAnomaly.waf_endpoint_id)
            ),
            branch("ips", IpsAnomaly),
            branch(
                "property_watch",
                PwAnomaly,
                (PropertyWatch, PropertyWatch.id == PwAnomaly.watch_id),
            ),
            branch("rule", RuleAnomaly, (AnalyticsRule, AnalyticsRule.id == RuleAnomaly.rule_id)),
            branch("identity", IdentityAnomaly),
        ).subquery()
        rows = await self.session.execute(select(union.c.family, union.c.state, union.c.n))
        return {(str(f), str(s)): int(n) for f, s, n in rows.all()}

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

    async def resolved_count(self, *, since: datetime, until: datetime) -> int:
        """Count resolution timestamps in one half-open window."""
        count = await self.session.scalar(
            select(func.count())
            .select_from(LogSourceAnomaly)
            .where(LogSourceAnomaly.resolved_at >= since)
            .where(LogSourceAnomaly.resolved_at < until)
        )
        return int(count or 0)

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

    async def get_rule_detail(
        self, anomaly_id: uuid.UUID
    ) -> tuple[RuleAnomaly, AnalyticsRule, str, list[RuleAnomalyTransition]] | None:
        """One rule-match incident, its rule and its lifecycle: two statements.

        The incident goes with its rule (cascade), so the join is inner. The third item is
        the incident's observed-count claim, from the same expression the list uses. The
        rule models declare no relationships, so the transitions, oldest first, are read in
        their own statement.
        """
        row = (
            await self.session.execute(
                select(RuleAnomaly, AnalyticsRule, _rule_measurement(RuleAnomaly))
                .join(AnalyticsRule, AnalyticsRule.id == RuleAnomaly.rule_id)
                .where(RuleAnomaly.id == anomaly_id)
            )
        ).first()
        if row is None:
            return None
        transitions = list(
            (
                await self.session.scalars(
                    select(RuleAnomalyTransition)
                    .where(RuleAnomalyTransition.rule_anomaly_id == anomaly_id)
                    .order_by(RuleAnomalyTransition.occurred_at, RuleAnomalyTransition.created_at)
                )
            ).all()
        )
        return row[0], row[1], str(row[2]), transitions

    async def get_identity_detail(
        self, anomaly_id: uuid.UUID
    ) -> tuple[IdentityAnomaly, LogSource | None, list[IdentityAnomalyTransition]] | None:
        """One identity incident, its log source and its lifecycle: two statements."""
        row = (
            await self.session.execute(
                select(IdentityAnomaly, LogSource)
                .outerjoin(LogSource, LogSource.id == IdentityAnomaly.log_source_id)
                .where(IdentityAnomaly.id == anomaly_id)
            )
        ).first()
        if row is None:
            return None
        transitions = list(
            (
                await self.session.scalars(
                    select(IdentityAnomalyTransition)
                    .where(IdentityAnomalyTransition.identity_anomaly_id == anomaly_id)
                    .order_by(
                        IdentityAnomalyTransition.occurred_at,
                        IdentityAnomalyTransition.created_at,
                    )
                )
            ).all()
        )
        return row[0], row[1], transitions

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

    # ------------------------------------------------------- WAF traffic (TOP-1)
    async def waf_source_and_profile(
        self, log_source_id: uuid.UUID
    ) -> tuple[LogSource, WafSourceProfile | None] | None:
        """The log source and its WAF profile, if it has one.

        None only when the log source itself does not exist. An existing source
        without a profile was never collected, which the route answers as
        NOT_COLLECTED rather than as an unknown id.
        """
        row = (
            await self.session.execute(
                select(LogSource, WafSourceProfile)
                .outerjoin(WafSourceProfile, WafSourceProfile.log_source_id == LogSource.id)
                .where(LogSource.id == log_source_id)
            )
        ).first()
        return (row[0], row[1]) if row is not None else None

    async def waf_latest_complete_bucket(
        self, log_source_id: uuid.UUID
    ) -> tuple[datetime, int] | None:
        """The newest bucket whose endpoint counts were a complete measurement.

        A PARTIAL bucket is the newest *collected* bucket but not a complete
        one, so it cannot anchor a "5m" window that claims to be whole.
        """
        b = WafSourceBucket
        row = (
            await self.session.execute(
                select(b.bucket_start, b.bucket_seconds)
                .where(
                    b.log_source_id == log_source_id,
                    b.completeness == BucketCompleteness.COMPLETE.value,
                )
                .order_by(b.bucket_start.desc())
                .limit(1)
            )
        ).first()
        return (row[0], int(row[1])) if row is not None else None

    async def waf_traffic_bucket_stats(
        self, log_source_id: uuid.UUID, *, since: datetime, until: datetime
    ) -> tuple[int, int, bool, int | None, int | None]:
        """(measured, partial, any truncated, source events, untracked requests).

        Measured means the bucket states that its endpoint counts were taken;
        only those buckets' rows are ranked. Source events and untracked
        requests are summed over the same buckets, so a missing bucket can
        neither shrink the total nor invent a zero. Truncation is checked on
        every bucket of the range, measured or not, because it is what makes a
        missing bucket's absence uninformative.
        """
        b = WafSourceBucket
        measured = b.request_count_measurement.in_(_WAF_MEASURED)
        row = (
            await self.session.execute(
                select(
                    func.count().filter(measured).label("measured"),
                    func.count()
                    .filter(
                        measured,
                        b.completeness == BucketCompleteness.PARTIAL.value,
                    )
                    .label("partial"),
                    func.coalesce(
                        func.bool_or(or_(b.groups_truncated, b.admission_truncated)),
                        False,
                    ).label("truncated"),
                    func.sum(b.total_events).filter(measured).label("source_events"),
                    func.sum(b.untracked_requests).filter(measured).label("untracked"),
                ).where(
                    b.log_source_id == log_source_id,
                    b.bucket_start >= since,
                    b.bucket_start < until,
                )
            )
        ).one()
        return (
            int(row.measured),
            int(row.partial),
            bool(row.truncated),
            int(row.source_events) if row.source_events is not None else None,
            int(row.untracked) if row.untracked is not None else None,
        )

    async def waf_traffic_page(
        self,
        log_source_id: uuid.UUID,
        *,
        since: datetime,
        until: datetime,
        sort: str,
        descending: bool,
        limit: int,
        offset: int,
    ) -> WafTrafficPage:
        """One page of endpoints ranked over the whole measured set.

        One statement: the per-endpoint aggregate, the peak bucket, the sort,
        the page and the whole set's count/max/sum. The summary is LEFT JOINed
        so an offset past the end still reports the true total, and no query is
        issued per endpoint.
        """
        b = WafSourceBucket
        measured_buckets = select(b.bucket_start).where(
            b.log_source_id == log_source_id,
            b.bucket_start >= since,
            b.bucket_start < until,
            b.request_count_measurement.in_(_WAF_MEASURED),
        )
        m = WafEndpointMetric
        e = WafEndpoint
        endpoints = select(WafEndpoint.id).where(
            WafEndpoint.log_source_id == log_source_id
        )
        agg = (
            select(
                m.waf_endpoint_id.label("entity_id"),
                func.sum(m.request_count).label("events"),
                func.count().label("buckets_with_data"),
                func.count()
                .filter(m.completeness == BucketCompleteness.PARTIAL.value)
                .label("partial_buckets"),
            )
            .where(
                m.bucket_start.in_(measured_buckets),
                m.waf_endpoint_id.in_(endpoints),
            )
            .group_by(m.waf_endpoint_id)
            .subquery()
        )
        peak_rate = cast(m.request_count, Float) / m.bucket_seconds
        peak = (
            select(
                m.waf_endpoint_id.label("entity_id"),
                m.bucket_start.label("peak_bucket_start"),
                peak_rate.label("peak_rate"),
            )
            .where(
                m.bucket_start.in_(measured_buckets),
                m.waf_endpoint_id.in_(endpoints),
            )
            .ext(distinct_on(m.waf_endpoint_id))
            .order_by(m.waf_endpoint_id, peak_rate.desc(), m.bucket_start)
            .subquery()
        )
        summary = (
            select(
                func.count().label("total"),
                func.max(agg.c.events).label("max_events"),
                func.coalesce(func.sum(agg.c.events), 0).label("attributed_events"),
            )
            .select_from(agg)
            .subquery()
        )
        if sort == "host":
            ordered: ColumnElement[Any] = (
                e.virtual_host.desc().nulls_last()
                if descending
                else e.virtual_host.asc().nulls_last()
            )
        elif sort == "endpoint":
            ordered = (
                e.normalized_endpoint.desc() if descending else e.normalized_endpoint.asc()
            )
        elif sort == "peak":
            ordered = peak.c.peak_rate.desc() if descending else peak.c.peak_rate.asc()
        else:
            ordered = agg.c.events.desc() if descending else agg.c.events.asc()
        page = (
            select(
                agg.c.entity_id,
                agg.c.events,
                agg.c.buckets_with_data,
                agg.c.partial_buckets,
                peak.c.peak_rate,
                peak.c.peak_bucket_start,
                e.virtual_host,
                e.http_method,
                e.normalized_endpoint,
                e.is_active,
            )
            .join(e, e.id == agg.c.entity_id)
            .join(peak, peak.c.entity_id == agg.c.entity_id)
            .order_by(ordered, e.id.asc())
            .limit(limit)
            .offset(offset)
            .subquery()
        )
        statement = select(
            summary.c.total,
            summary.c.max_events,
            summary.c.attributed_events,
            page.c.entity_id,
            page.c.events,
            page.c.buckets_with_data,
            page.c.partial_buckets,
            page.c.peak_rate,
            page.c.peak_bucket_start,
            page.c.virtual_host,
            page.c.http_method,
            page.c.normalized_endpoint,
            page.c.is_active,
        ).select_from(summary.outerjoin(page, true()))
        rows = (await self.session.execute(statement)).all()
        items = [
            WafTrafficRow(
                id=row.entity_id,
                events=int(row.events),
                buckets_with_data=int(row.buckets_with_data),
                partial_buckets=int(row.partial_buckets),
                peak_rate_per_second=float(row.peak_rate),
                peak_bucket_start=row.peak_bucket_start,
                virtual_host=row.virtual_host,
                http_method=row.http_method,
                normalized_endpoint=row.normalized_endpoint,
                active=bool(row.is_active),
            )
            for row in rows
            if row.entity_id is not None
        ]
        summary_row = rows[0]
        return WafTrafficPage(
            items=items,
            total=int(summary_row.total or 0),
            max_events=(
                int(summary_row.max_events) if summary_row.max_events is not None else None
            ),
            attributed_events=int(summary_row.attributed_events or 0),
        )

    async def waf_traffic_series(
        self,
        log_source_id: uuid.UUID,
        *,
        since: datetime,
        until: datetime,
        step_seconds: int,
        entity_ids: Collection[uuid.UUID],
    ) -> tuple[dict[int, int], dict[tuple[int, uuid.UUID], int]]:
        """(measured buckets per step, events per entity and step), one statement.

        Only the page's entities are read. A step's measured count comes from the
        source buckets themselves, so a step without a measured bucket is absent
        from the dict and the route draws it as a gap, never as zero. An entity
        with no row in a measured step is absent from `events`, which the route
        reads as zero events over that step's measured seconds -- the same rule
        `rate_per_second` applies to the whole window.
        """
        if not entity_ids:
            return {}, {}
        b = WafSourceBucket
        m = WafEndpointMetric
        measured_buckets = select(b.bucket_start).where(
            b.log_source_id == log_source_id,
            b.bucket_start >= since,
            b.bucket_start < until,
            b.request_count_measurement.in_(_WAF_MEASURED),
        )
        step = func.floor(
            func.extract("epoch", b.bucket_start - since) / step_seconds
        ).cast(Integer)
        steps = (
            select(step.label("step"), func.count().label("measured"))
            .where(
                b.log_source_id == log_source_id,
                b.bucket_start >= since,
                b.bucket_start < until,
                b.request_count_measurement.in_(_WAF_MEASURED),
            )
            .group_by(step)
            .subquery()
        )
        entity_step = func.floor(
            func.extract("epoch", m.bucket_start - since) / step_seconds
        ).cast(Integer)
        events = (
            select(
                m.waf_endpoint_id.label("entity_id"),
                entity_step.label("step"),
                func.sum(m.request_count).label("events"),
            )
            .where(
                m.waf_endpoint_id.in_(entity_ids),
                m.bucket_start.in_(measured_buckets),
            )
            .group_by(m.waf_endpoint_id, entity_step)
            .subquery()
        )
        rows = (
            await self.session.execute(
                select(steps.c.step, steps.c.measured, events.c.entity_id, events.c.events)
                .select_from(steps.outerjoin(events, events.c.step == steps.c.step))
            )
        ).all()
        measured: dict[int, int] = {}
        per_entity: dict[tuple[int, uuid.UUID], int] = {}
        for row in rows:
            measured[int(row.step)] = int(row.measured)
            if row.entity_id is not None:
                per_entity[(int(row.step), row.entity_id)] = int(row.events)
        return measured, per_entity
