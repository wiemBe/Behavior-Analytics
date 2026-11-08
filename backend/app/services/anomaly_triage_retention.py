"""Delete triage rows whose anomaly no longer exists.

`anomaly_triage` keys on `(family, anomaly_id)` and deliberately has no foreign key:
one column cannot point at four anomaly tables. Retention therefore owns the
consequence. Each anomaly retention task calls this after its own delete, so a triage
row lives exactly as long as the incident it describes and the triage table stays
bounded instead of accumulating orphans indefinitely.

The delete is bounded (one `LIMIT :batch` per run per family) and runs a single
statement. A row is orphaned only when its own family's table has no row with that id;
a triage row for a still-live incident is never touched. The activity events leave by
`ON DELETE CASCADE`.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AnomalyFamily

#: The one statement shape, one per family. Table names are literals: a family cannot
#: name a table, so no caller input reaches the SQL text.
DELETE_SQL: dict[AnomalyFamily, str] = {
    AnomalyFamily.LOG_SOURCE: (
        "DELETE FROM anomaly_triage WHERE id IN ("
        "SELECT t.id FROM anomaly_triage t "
        "WHERE t.family = 'log_source' "
        "AND NOT EXISTS (SELECT 1 FROM log_source_anomaly a WHERE a.id = t.anomaly_id) "
        "LIMIT :batch)"
    ),
    AnomalyFamily.WAF: (
        "DELETE FROM anomaly_triage WHERE id IN ("
        "SELECT t.id FROM anomaly_triage t "
        "WHERE t.family = 'waf' "
        "AND NOT EXISTS (SELECT 1 FROM waf_anomaly a WHERE a.id = t.anomaly_id) "
        "LIMIT :batch)"
    ),
    AnomalyFamily.IPS: (
        "DELETE FROM anomaly_triage WHERE id IN ("
        "SELECT t.id FROM anomaly_triage t "
        "WHERE t.family = 'ips' "
        "AND NOT EXISTS (SELECT 1 FROM ips_anomaly a WHERE a.id = t.anomaly_id) "
        "LIMIT :batch)"
    ),
    AnomalyFamily.PROPERTY_WATCH: (
        "DELETE FROM anomaly_triage WHERE id IN ("
        "SELECT t.id FROM anomaly_triage t "
        "WHERE t.family = 'property_watch' "
        "AND NOT EXISTS (SELECT 1 FROM pw_anomaly a WHERE a.id = t.anomaly_id) "
        "LIMIT :batch)"
    ),
    AnomalyFamily.RULE: (
        "DELETE FROM anomaly_triage WHERE id IN ("
        "SELECT t.id FROM anomaly_triage t "
        "WHERE t.family = 'rule' "
        "AND NOT EXISTS (SELECT 1 FROM rule_anomaly a WHERE a.id = t.anomaly_id) "
        "LIMIT :batch)"
    ),
}


async def delete_orphan_triage(
    session: AsyncSession, family: AnomalyFamily, *, batch_size: int
) -> int:
    """Delete at most `batch_size` orphaned rows of one family and commit."""
    result = await session.execute(
        text(DELETE_SQL[family]).bindparams(batch=batch_size)
    )
    await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)
