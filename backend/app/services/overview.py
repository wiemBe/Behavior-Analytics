"""SOC overview aggregation service."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.enums import ACTIVE_ANOMALY_STATES, AlertStatus, AnomalyType
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource, LogSourceAnomaly
from app.models.offense import OffenseSnapshot
from app.repositories.log_source import LogSourceRepository
from app.schemas.log_source import (
    OverviewAlerts,
    OverviewCounts,
    OverviewOffenses,
    SocOverview,
)


class OverviewService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.ls_repo = LogSourceRepository(session)

    async def build(self) -> SocOverview:
        instance = await self.session.scalar(select(QRadarInstance).limit(1))

        counts = await self.ls_repo.overview_counts()
        anomalous = await self.ls_repo.anomalous_source_count()
        silent = await self._silent_count()

        return SocOverview(
            instance_status=str(instance.status) if instance else "UNKNOWN",
            instance_version=instance.qradar_version if instance else None,
            instance_last_health_check_at=(
                instance.last_health_check_at if instance else None
            ),
            generated_at=datetime.now(UTC),
            log_sources=OverviewCounts(
                total_log_sources=counts["total"],
                monitored_log_sources=counts["monitored"],
                silent_log_sources=silent,
                anomalous_log_sources=anomalous,
                in_maintenance=counts["maintenance"],
            ),
            offenses=await self._offense_overview(),
            alerts=await self._alert_overview(),
        )

    async def _silent_count(self) -> int:
        """Monitored sources with an open, unsuppressed NO_EVENTS finding.

        Silence is the anomaly engine's verdict: a complete empty bucket, past
        the silence grace, where that source is expected to be active. A null
        `last_event_time` means inventory has not reported one. That source is
        unknown, not silent. An old timestamp is not a second definition of
        silence; the engine already applies the grace, the baseline and the
        business-hours gate, and this count reads that result.
        """
        stmt = (
            select(func.count(func.distinct(LogSource.id)))
            .select_from(LogSource)
            .join(LogSourceAnomaly, LogSourceAnomaly.log_source_id == LogSource.id)
            .where(
                LogSource.monitoring_enabled.is_(True),
                LogSource.maintenance_mode.is_(False),
                LogSource.enabled.is_(True),
                LogSourceAnomaly.anomaly_type == AnomalyType.NO_EVENTS,
                LogSourceAnomaly.state.in_([state.value for state in ACTIVE_ANOMALY_STATES]),
                LogSourceAnomaly.suppressed.is_(False),
            )
        )
        return int(await self.session.scalar(stmt) or 0)

    async def _offense_overview(self) -> OverviewOffenses:
        """Open offenses in the most recent capture.

        No snapshot at all is unknown, not zero open offenses. Zero is what a
        capture that contains no OPEN row measures.
        """
        latest_captured = await self.session.scalar(
            select(func.max(OffenseSnapshot.captured_at))
        )
        if latest_captured is None:
            return OverviewOffenses(active=None, critical=None, unassigned=None)

        base = select(OffenseSnapshot).where(
            OffenseSnapshot.captured_at == latest_captured,
            OffenseSnapshot.status == "OPEN",
        )
        rows = list((await self.session.scalars(base)).all())
        active = len(rows)
        critical = sum(1 for r in rows if (r.magnitude or 0) >= 7)
        unassigned = sum(1 for r in rows if not r.is_assigned)
        oldest = max((r.age_seconds or 0 for r in rows), default=None)
        return OverviewOffenses(
            active=active, critical=critical, unassigned=unassigned, oldest_age_seconds=oldest
        )

    async def _alert_overview(self) -> OverviewAlerts:
        open_count = (
            await self.session.scalar(
                select(func.count()).select_from(Alert).where(
                    Alert.status == AlertStatus.OPEN
                )
            )
            or 0
        )
        ack_count = (
            await self.session.scalar(
                select(func.count()).select_from(Alert).where(
                    Alert.status == AlertStatus.ACKNOWLEDGED
                )
            )
            or 0
        )
        sev_rows = await self.session.execute(
            select(Alert.severity, func.count())
            .where(Alert.status != AlertStatus.RESOLVED)
            .group_by(Alert.severity)
        )
        by_sev = {str(sev): cnt for sev, cnt in sev_rows}
        return OverviewAlerts(open=open_count, acknowledged=ack_count, by_severity=by_sev)
