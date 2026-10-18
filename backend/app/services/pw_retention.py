"""Bounded retention for property-watch buckets, metrics and retired values (design §G.7.2).

Bucket and metric rows go after `pw_metric_retention_days`. A value row goes when it is
retired, its retirement is older than that, and it has no metric left: the metrics are
deleted first, so in one run a value can leave right after its last metric aged out, and
never before. Every statement deletes at most `pw_retention_batch_size` rows
per batch and at most `pw_retention_max_batches` batches per run; a table that still has
more is reported `incomplete` and continues the next day.

Deleting a watch deletes its values by cascade; this task only ages what remains. It runs
whether or not collection is enabled: switching collection off must not freeze value text
in the database.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.enums import AnomalyState


@dataclass
class PwRetentionReport:
    deleted: dict[str, int] = field(default_factory=dict)
    incomplete: list[str] = field(default_factory=list)
    #: Rows kept back because an incident on them is still active (item 2 of PW-3c, §G.7).
    blocked: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"deleted": self.deleted, "incomplete": self.incomplete, "blocked": self.blocked}


#: The `pw_anomaly` states that protect a value's row from retention: an incident that has not
#: ended means the value it hangs on is still part of an investigation (design §G.7). The
#: statements below spell them out as SQL literals; `test_pw_retention` pins the two together
#: so the literal cannot drift from this tuple.
BLOCKING_INCIDENT_STATES = (AnomalyState.OPEN.value, AnomalyState.RECOVERING.value)


@dataclass(frozen=True)
class _RetentionStep:
    table: str
    delete: str
    remaining: str
    #: Counts the rows the `delete` would have taken but for an active incident; `None` for a
    #: table that no incident can protect.
    blocked: str | None = None


_STATEMENTS = (
    _RetentionStep(
        "pw_value_metric",
        "DELETE FROM pw_value_metric WHERE (pw_value_id, bucket_start) IN ("
        "SELECT pw_value_id, bucket_start FROM pw_value_metric "
        "WHERE bucket_start < :cutoff LIMIT :batch)",
        "SELECT EXISTS (SELECT 1 FROM pw_value_metric WHERE bucket_start < :cutoff)",
    ),
    _RetentionStep(
        "pw_bucket",
        "DELETE FROM pw_bucket WHERE (watch_id, bucket_start) IN ("
        "SELECT watch_id, bucket_start FROM pw_bucket "
        "WHERE bucket_start < :cutoff LIMIT :batch)",
        "SELECT EXISTS (SELECT 1 FROM pw_bucket WHERE bucket_start < :cutoff)",
    ),
    _RetentionStep(
        "pw_value",
        # A value with an incident still OPEN or RECOVERING is kept: deleting it would leave
        # the investigation without its subject (fail closed). Item 1 ends such incidents when
        # the value retires, so this normally protects nothing and the row leaves as before.
        # The state list is written as literals because this is raw SQL; the enum pin is in
        # `test_pw_retention`.
        "DELETE FROM pw_value WHERE id IN (SELECT v.id FROM pw_value v "
        "WHERE v.active = false AND v.retired_at IS NOT NULL AND v.retired_at < :cutoff "
        "AND NOT EXISTS (SELECT 1 FROM pw_value_metric m WHERE m.pw_value_id = v.id) "
        "AND NOT EXISTS (SELECT 1 FROM pw_anomaly a WHERE a.pw_value_id = v.id "
        "AND a.state IN ( 'OPEN', 'RECOVERING' )) "
        "LIMIT :batch)",
        "SELECT EXISTS (SELECT 1 FROM pw_value v "
        "WHERE v.active = false AND v.retired_at IS NOT NULL AND v.retired_at < :cutoff "
        "AND NOT EXISTS (SELECT 1 FROM pw_value_metric m WHERE m.pw_value_id = v.id) "
        "AND NOT EXISTS (SELECT 1 FROM pw_anomaly a WHERE a.pw_value_id = v.id "
        "AND a.state IN ( 'OPEN', 'RECOVERING' )))",
        "SELECT count(*) FROM pw_value v "
        "WHERE v.active = false AND v.retired_at IS NOT NULL AND v.retired_at < :cutoff "
        "AND NOT EXISTS (SELECT 1 FROM pw_value_metric m WHERE m.pw_value_id = v.id) "
        "AND EXISTS (SELECT 1 FROM pw_anomaly a WHERE a.pw_value_id = v.id "
        "AND a.state IN ( 'OPEN', 'RECOVERING' ))",
    ),
)


class PwRetention:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(self) -> PwRetentionReport:
        report = PwRetentionReport()
        cutoff = self._clock() - timedelta(days=self.settings.pw_metric_retention_days)
        batch = self.settings.pw_retention_batch_size
        for step in _STATEMENTS:
            deleted = 0
            for _ in range(self.settings.pw_retention_max_batches):
                result = await self.session.execute(
                    text(step.delete).bindparams(cutoff=cutoff, batch=batch)
                )
                await self.session.commit()
                count = int(getattr(result, "rowcount", 0) or 0)
                deleted += count
                if count < batch:
                    break
            else:
                remaining = await self.session.scalar(
                    text(step.remaining).bindparams(cutoff=cutoff)
                )
                if remaining:
                    report.incomplete.append(step.table)
            report.deleted[step.table] = deleted
            if step.blocked is not None:
                # One statement whatever the row count: values an active incident keeps back.
                report.blocked[step.table] = int(
                    await self.session.scalar(
                        text(step.blocked).bindparams(cutoff=cutoff)
                    )
                    or 0
                )
        return report
