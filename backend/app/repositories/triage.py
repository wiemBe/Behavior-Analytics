"""Query helpers for analyst triage state.

The triage key is polymorphic `(family, anomaly_id)`, so every lookup takes the family
and asks the matching anomaly table whether the id exists. The list endpoint never
uses `summaries_for` for its main page: it joins `anomaly_triage` into the unified
union, so one request costs the same statements whatever the page holds. The helper is
for the two small Phase A lists inside `GET /anomalies/summary`, where a single bulk
lookup is cheaper than widening each of those queries with a join.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AnomalyFamily
from app.models.identity_anomaly import IdentityAnomaly
from app.models.ips import IpsAnomaly
from app.models.log_source import LogSourceAnomaly
from app.models.property_watch import PwAnomaly
from app.models.rule_anomaly import RuleAnomaly
from app.models.triage import AnomalyTriage, AnomalyTriageEvent
from app.models.waf import WafAnomaly
from app.schemas.triage import TriageSummaryOut

#: The table an `AnomalyFamily` names. The only place the mapping exists.
FAMILY_MODELS: dict[AnomalyFamily, Any] = {
    AnomalyFamily.LOG_SOURCE: LogSourceAnomaly,
    AnomalyFamily.WAF: WafAnomaly,
    AnomalyFamily.IPS: IpsAnomaly,
    AnomalyFamily.PROPERTY_WATCH: PwAnomaly,
    AnomalyFamily.RULE: RuleAnomaly,
    AnomalyFamily.IDENTITY: IdentityAnomaly,
}


def summary_of(row: AnomalyTriage | None) -> TriageSummaryOut:
    """The list/detail triage block; a missing row is NEW and unowned."""
    if row is None:
        return TriageSummaryOut()
    return TriageSummaryOut(
        status=row.status,
        owner=row.owner,
        disposition=row.disposition,
        updated_at=row.updated_at,
    )


class TriageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def anomaly_exists(
        self, family: AnomalyFamily, anomaly_id: uuid.UUID
    ) -> bool:
        """Whether the named anomaly table holds this id."""
        model = FAMILY_MODELS[family]
        found = await self.session.scalar(select(model.id).where(model.id == anomaly_id))
        return found is not None

    async def row(
        self,
        family: AnomalyFamily,
        anomaly_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> AnomalyTriage | None:
        """The triage row, optionally locked for the rest of the transaction.

        `FOR UPDATE` is what makes the version check race-free: a second PATCH waits
        on the lock and then reads the version the first one committed.
        """
        stmt = select(AnomalyTriage).where(
            AnomalyTriage.family == family,
            AnomalyTriage.anomaly_id == anomaly_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)

    async def summary(
        self, family: AnomalyFamily, anomaly_id: uuid.UUID
    ) -> TriageSummaryOut:
        """The list/detail block for one anomaly, in one statement."""
        return summary_of(await self.row(family, anomaly_id))

    async def events_page(
        self, triage_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[AnomalyTriageEvent], int]:
        """One page of activity, newest first.

        The tiebreak on `id` keeps a page boundary deterministic when one PATCH wrote
        several events at the same instant.
        """
        where = (AnomalyTriageEvent.triage_id == triage_id,)
        total = await self.session.scalar(
            select(func.count()).select_from(AnomalyTriageEvent).where(*where)
        )
        rows = await self.session.scalars(
            select(AnomalyTriageEvent)
            .where(*where)
            .order_by(
                AnomalyTriageEvent.created_at.desc(), AnomalyTriageEvent.id.desc()
            )
            .limit(limit)
            .offset(offset)
        )
        return list(rows.all()), int(total or 0)

    async def summaries_for(
        self, family: AnomalyFamily, anomaly_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, TriageSummaryOut]:
        """The triage block for a set of ids, in one statement."""
        ids = list(anomaly_ids)
        if not ids:
            return {}
        rows = await self.session.scalars(
            select(AnomalyTriage).where(
                AnomalyTriage.family == family,
                AnomalyTriage.anomaly_id.in_(ids),
            )
        )
        found = {row.anomaly_id: summary_of(row) for row in rows.all()}
        return {
            anomaly_id: found.get(anomaly_id, TriageSummaryOut()) for anomaly_id in ids
        }
