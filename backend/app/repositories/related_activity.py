"""Reads behind `GET /anomalies/related/{family}/{anomaly_id}`.

Every read is bulk or keyed by the anchor. The statement count is fixed: one for the
offense watermark, one page plus its total for offenses, one aggregation for the rule
union, one catalog resolution, one read of the measured rule-match windows, one of
their metrics, and one per anomaly family (the family set is fixed). No statement is
issued per offense, per anomaly or per rule.

Offenses are append-on-change history, so "the offense in this window" means its
newest snapshot, then the overlap test on that snapshot. Filtering before the
`DISTINCT ON` would let an older snapshot that overlapped represent an offense whose
newest snapshot has since closed or moved.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Integer, and_, func, literal, literal_column, select
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Subquery

from app.models.custom_rule import CustomRule, CustomRuleBreach
from app.models.enums import AnomalyFamily, TriageStatus
from app.models.ips import IpsAnomaly, IpsEntity
from app.models.log_source import LogSourceAnomaly
from app.models.monitoring import CollectionWatermark
from app.models.offense import OffenseSnapshot
from app.models.property_watch import PropertyWatch, PwAnomaly
from app.models.rule import AnalyticsRule, RuleMatchMetric, RuleMatchWindow
from app.models.triage import AnomalyTriage
from app.models.waf import WafAnomaly, WafEndpoint

#: The collector that writes `offense_snapshot`. Kept local rather than imported from
#: the collector: this read must not pull the collector's provider imports into the API.
OFFENSE_COLLECTOR = "offense_snapshot"

#: A custom-rule breach's own type value in the `anomalies` section. It has no detector
#: enum, so the response uses one stable name.
CUSTOM_RULE_BREACH_TYPE = "CUSTOM_RULE_BREACH"


#: One second as a SQL interval, for `bucket_start + bucket_seconds * interval`.
_SECOND: Any = literal_column("INTERVAL '1 second'")


class RelatedActivityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --------------------------------------------------------------- freshness
    async def offense_watermark(self, instance_id: uuid.UUID) -> CollectionWatermark | None:
        """The offense collector's progress row, if it has ever run for this console."""
        return await self.session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.instance_id == instance_id,
                CollectionWatermark.collector == OFFENSE_COLLECTOR,
            )
        )

    # ----------------------------------------------------------------- offenses
    def _latest_offenses(self, instance_id: uuid.UUID) -> Subquery:
        """One row per offense: its newest snapshot, before any window filter."""
        return (
            select(OffenseSnapshot)
            .ext(distinct_on(OffenseSnapshot.instance_id, OffenseSnapshot.qradar_offense_id))
            .where(OffenseSnapshot.instance_id == instance_id)
            .order_by(
                OffenseSnapshot.instance_id,
                OffenseSnapshot.qradar_offense_id,
                OffenseSnapshot.captured_at.desc(),
            )
            .subquery()
        )

    @staticmethod
    def _offense_filters(
        sub: Any,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
    ) -> list[Any]:
        """The newest snapshot matches the source and its span overlaps the window.

        An open offense ends at `last_updated_time`; a closed one at `close_time`. A
        row missing both cannot prove an overlap and is excluded rather than guessed.
        """
        span_end = func.coalesce(sub.c.close_time, sub.c.last_updated_time)
        return [
            sub.c.log_source_ids.contains([qradar_log_source_id]),
            sub.c.start_time <= window_end,
            span_end >= window_start,
        ]

    async def offenses(
        self,
        *,
        instance_id: uuid.UUID,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """The first `limit` matching offenses and the full matching count.

        `count(*) over ()` rides in the same statement, so the page and its total
        cannot disagree and the total costs no extra round trip.
        """
        sub = self._latest_offenses(instance_id)
        stmt = (
            select(*sub.c, func.count().over().label("total"))
            .where(
                *self._offense_filters(
                    sub, qradar_log_source_id, window_start, window_end
                )
            )
            .order_by(
                sub.c.magnitude.desc().nullslast(),
                sub.c.start_time.desc().nullslast(),
                sub.c.qradar_offense_id.asc(),
            )
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).mappings().all()
        total = int(rows[0]["total"]) if rows else 0
        return [dict(row) for row in rows], total

    async def offense_rule_counts(
        self,
        *,
        instance_id: uuid.UUID,
        qradar_log_source_id: int,
        window_start: datetime,
        window_end: datetime,
    ) -> dict[int, int]:
        """How many matching offenses name each rule id, over the whole match.

        The union is taken server-side with `jsonb_array_elements_text`, so the count
        does not grow with the number of offenses or rule ids the window holds.
        """
        sub = self._latest_offenses(instance_id)
        element = (
            func.jsonb_array_elements_text(sub.c.rule_ids)
            .table_valued("value")
            .render_derived()
        )
        stmt = (
            select(
                element.c.value.cast(Integer).label("qradar_id"),
                func.count(func.distinct(sub.c.qradar_offense_id)),
            )
            .select_from(sub.join(element, sa_true()))
            .where(
                *self._offense_filters(
                    sub, qradar_log_source_id, window_start, window_end
                )
            )
            .group_by(element.c.value)
        )
        rows = (await self.session.execute(stmt)).all()
        return {int(qradar_id): int(count) for qradar_id, count in rows}

    async def rules_by_qradar(
        self, instance_id: uuid.UUID, qradar_ids: list[int]
    ) -> dict[int, AnalyticsRule]:
        """The catalog rows for a set of QRadar rule ids, in one statement."""
        if not qradar_ids:
            return {}
        rows = await self.session.scalars(
            select(AnalyticsRule).where(
                AnalyticsRule.instance_id == instance_id,
                AnalyticsRule.qradar_id.in_(qradar_ids),
            )
        )
        return {rule.qradar_id: rule for rule in rows.all()}

    async def rule_match_windows(
        self, instance_id: uuid.UUID, window_start: datetime, window_end: datetime
    ) -> list[RuleMatchWindow]:
        """Every measured (or attempted) rule-match window overlapping the read."""
        rows = await self.session.scalars(
            select(RuleMatchWindow).where(
                RuleMatchWindow.instance_id == instance_id,
                RuleMatchWindow.window_start < window_end,
                RuleMatchWindow.window_start
                + RuleMatchWindow.window_seconds * _SECOND
                > window_start,
            )
        )
        return list(rows.all())

    async def rule_matched_events(
        self,
        rule_ids: list[uuid.UUID],
        window_start: datetime,
        window_end: datetime,
    ) -> dict[uuid.UUID, int]:
        """Matched events per rule over the metric buckets overlapping the window."""
        if not rule_ids:
            return {}
        rows = (
            await self.session.execute(
                select(
                    RuleMatchMetric.rule_id,
                    func.sum(RuleMatchMetric.matched_events),
                )
                .where(
                    RuleMatchMetric.rule_id.in_(rule_ids),
                    RuleMatchMetric.bucket_start < window_end,
                    RuleMatchMetric.bucket_start
                    + RuleMatchMetric.bucket_seconds * _SECOND
                    > window_start,
                )
                .group_by(RuleMatchMetric.rule_id)
            )
        ).all()
        return {rule_id: int(total or 0) for rule_id, total in rows}

    # ---------------------------------------------------------------- anomalies
    async def anomalies(
        self,
        *,
        family: AnomalyFamily,
        source_id: uuid.UUID,
        anchor_id: uuid.UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> list[dict[str, Any]]:
        """Other anomalies on `source_id` whose own interval overlaps the window."""
        if family is AnomalyFamily.LOG_SOURCE:
            return await self._log_source_anomalies(
                source_id, anchor_id, window_start, window_end
            )
        if family is AnomalyFamily.WAF:
            return await self._waf_anomalies(source_id, anchor_id, window_start, window_end)
        if family is AnomalyFamily.IPS:
            return await self._ips_anomalies(source_id, anchor_id, window_start, window_end)
        if family is AnomalyFamily.RULE:
            # A rule incident has no log-source subject, so it is not part of a
            # source's related activity. The fall-through below is property watch.
            return []
        return await self._pw_anomalies(source_id, anchor_id, window_start, window_end)

    async def _log_source_anomalies(
        self,
        source_id: uuid.UUID,
        anchor_id: uuid.UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> list[dict[str, Any]]:
        start = func.coalesce(LogSourceAnomaly.anomaly_start, LogSourceAnomaly.detected_at)
        end = func.coalesce(
            LogSourceAnomaly.anomaly_end,
            LogSourceAnomaly.anomaly_start,
            LogSourceAnomaly.detected_at,
        )
        stmt = (
            select(
                _family_label(AnomalyFamily.LOG_SOURCE),
                LogSourceAnomaly.id,
                LogSourceAnomaly.anomaly_type.label("type"),
                LogSourceAnomaly.state.label("lifecycle_state"),
                LogSourceAnomaly.severity,
                start.label("start"),
                end.label("end"),
                func.coalesce(AnomalyTriage.status, TriageStatus.NEW).label(
                    "triage_status"
                ),
            )
            .outerjoin(
                AnomalyTriage,
                and_(
                    AnomalyTriage.family == AnomalyFamily.LOG_SOURCE,
                    AnomalyTriage.anomaly_id == LogSourceAnomaly.id,
                ),
            )
            .where(
                LogSourceAnomaly.log_source_id == source_id,
                LogSourceAnomaly.id != anchor_id,
                start <= window_end,
                end >= window_start,
            )
        )
        return _mappings(await self.session.execute(stmt))

    async def _waf_anomalies(
        self,
        source_id: uuid.UUID,
        anchor_id: uuid.UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> list[dict[str, Any]]:
        start = func.coalesce(WafAnomaly.anomaly_start, WafAnomaly.detected_at)
        end = func.coalesce(
            WafAnomaly.anomaly_end,
            WafAnomaly.anomaly_start,
            WafAnomaly.detected_at,
        )
        stmt = (
            select(
                _family_label(AnomalyFamily.WAF),
                WafAnomaly.id,
                WafAnomaly.detector_type.label("type"),
                WafAnomaly.state.label("lifecycle_state"),
                WafAnomaly.severity,
                start.label("start"),
                end.label("end"),
                func.coalesce(AnomalyTriage.status, TriageStatus.NEW).label(
                    "triage_status"
                ),
            )
            .join(WafEndpoint, WafAnomaly.waf_endpoint_id == WafEndpoint.id)
            .outerjoin(
                AnomalyTriage,
                and_(
                    AnomalyTriage.family == AnomalyFamily.WAF,
                    AnomalyTriage.anomaly_id == WafAnomaly.id,
                ),
            )
            .where(
                WafEndpoint.log_source_id == source_id,
                WafAnomaly.id != anchor_id,
                start <= window_end,
                end >= window_start,
            )
        )
        return _mappings(await self.session.execute(stmt))

    async def _ips_anomalies(
        self,
        source_id: uuid.UUID,
        anchor_id: uuid.UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> list[dict[str, Any]]:
        start = func.coalesce(IpsAnomaly.anomaly_start, IpsAnomaly.detected_at)
        end = func.coalesce(
            IpsAnomaly.anomaly_end,
            IpsAnomaly.anomaly_start,
            IpsAnomaly.detected_at,
        )
        source = func.coalesce(IpsAnomaly.log_source_id, IpsEntity.log_source_id)
        stmt = (
            select(
                _family_label(AnomalyFamily.IPS),
                IpsAnomaly.id,
                IpsAnomaly.detector_type.label("type"),
                IpsAnomaly.state.label("lifecycle_state"),
                IpsAnomaly.severity,
                start.label("start"),
                end.label("end"),
                func.coalesce(AnomalyTriage.status, TriageStatus.NEW).label(
                    "triage_status"
                ),
            )
            .outerjoin(IpsEntity, IpsAnomaly.ips_entity_id == IpsEntity.id)
            .outerjoin(
                AnomalyTriage,
                and_(
                    AnomalyTriage.family == AnomalyFamily.IPS,
                    AnomalyTriage.anomaly_id == IpsAnomaly.id,
                ),
            )
            .where(
                source == source_id,
                IpsAnomaly.id != anchor_id,
                start <= window_end,
                end >= window_start,
            )
        )
        return _mappings(await self.session.execute(stmt))

    async def _pw_anomalies(
        self,
        source_id: uuid.UUID,
        anchor_id: uuid.UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> list[dict[str, Any]]:
        start = func.coalesce(PwAnomaly.anomaly_start, PwAnomaly.detected_at)
        end = func.coalesce(
            PwAnomaly.anomaly_end,
            PwAnomaly.anomaly_start,
            PwAnomaly.detected_at,
        )
        stmt = (
            select(
                _family_label(AnomalyFamily.PROPERTY_WATCH),
                PwAnomaly.id,
                PwAnomaly.detector_type.label("type"),
                PwAnomaly.state.label("lifecycle_state"),
                PwAnomaly.severity,
                start.label("start"),
                end.label("end"),
                func.coalesce(AnomalyTriage.status, TriageStatus.NEW).label(
                    "triage_status"
                ),
            )
            .join(PropertyWatch, PwAnomaly.watch_id == PropertyWatch.id)
            .outerjoin(
                AnomalyTriage,
                and_(
                    AnomalyTriage.family == AnomalyFamily.PROPERTY_WATCH,
                    AnomalyTriage.anomaly_id == PwAnomaly.id,
                ),
            )
            .where(
                PropertyWatch.log_source_id == source_id,
                PwAnomaly.id != anchor_id,
                start <= window_end,
                end >= window_start,
            )
        )
        return _mappings(await self.session.execute(stmt))

    async def custom_rule_breaches(
        self,
        source_id: uuid.UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> list[dict[str, Any]]:
        """Breaches whose rule is tied to this log source.

        `FLEET_CLASS` and `PW_VALUE` rules name no log source, so their breaches are
        deliberately absent: attaching them to a source would invent an anchor.
        """
        source = func.coalesce(
            CustomRule.log_source_id,
            WafEndpoint.log_source_id,
            IpsEntity.log_source_id,
        )
        stmt = (
            select(
                literal("custom_rule").label("family"),
                CustomRuleBreach.id,
                literal(CUSTOM_RULE_BREACH_TYPE).label("type"),
                CustomRuleBreach.state.label("lifecycle_state"),
                CustomRule.severity,
                CustomRuleBreach.first_bucket_start.label("start"),
                CustomRuleBreach.last_bucket_start.label("end"),
                literal(TriageStatus.NEW.value).label("triage_status"),
            )
            .join(CustomRule, CustomRuleBreach.rule_id == CustomRule.id)
            .outerjoin(WafEndpoint, CustomRule.waf_endpoint_id == WafEndpoint.id)
            .outerjoin(IpsEntity, CustomRule.ips_entity_id == IpsEntity.id)
            .where(
                source == source_id,
                CustomRuleBreach.first_bucket_start <= window_end,
                CustomRuleBreach.last_bucket_start >= window_start,
            )
        )
        return _mappings(await self.session.execute(stmt))


def _family_label(family: AnomalyFamily) -> Any:
    return literal(family.value).label("family")


def _mappings(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]
