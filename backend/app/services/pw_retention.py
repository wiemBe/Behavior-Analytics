"""Bounded retention for property-watch buckets, metrics, incidents and retired values
(design §G.7.2, §G.14.3).

Bucket and metric rows go after `pw_metric_retention_days`. An incident row goes when its
state is terminal and it ended more than `pw_anomaly_retention_days` ago; its transitions
leave by cascade. The age is `COALESCE(resolved_at, anomaly_end, detected_at)`, as for WAF:
a RESOLVED incident carries `resolved_at`, a CANDIDATE that closed back to NORMAL carries
the `anomaly_end` of its last bucket. A value row goes when it is retired, its retirement
is older than `pw_metric_retention_days`, and it has no metric left: the metrics are
deleted first, so in one run a value can leave right after its last metric aged out, and
never before. Every statement deletes at most `pw_retention_batch_size` rows per batch and
at most `pw_retention_max_batches` batches per run; a table that still has more is reported
`incomplete` and continues the next day.

The incident step runs before the value step, so a value whose only incident aged out in
the same run still leaves. An incident is kept when a `pw_detector_state` still points at
it (a live investigation) or when an unresolved alert depends on it (the engine resolves an
incident's alert with the incident only while alerting is on, so a shadow-mode flip can
leave an open alert on a resolved incident); the rows kept back are counted in `blocked`.

Deleting a watch deletes its values and incidents by cascade; this task only ages what
remains. It runs whether or not collection is enabled: switching collection off must not
freeze value text in the database.
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
    #: Rows kept back because an incident on them is still active (item 2 of PW-3c, §G.7), a
    #: detector state points at them, or an unresolved alert depends on them.
    blocked: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"deleted": self.deleted, "incomplete": self.incomplete, "blocked": self.blocked}


#: The `pw_anomaly` states that protect a value's row from retention: an incident that has not
#: ended means the value it hangs on is still part of an investigation (design §G.7). The
#: statements below spell them out as SQL literals; `test_pw_retention` pins the two together
#: so the literal cannot drift from this tuple.
BLOCKING_INCIDENT_STATES = (AnomalyState.OPEN.value, AnomalyState.RECOVERING.value)

#: The states a `pw_anomaly` incident can end in: RESOLVED (the recovery, retirement and
#: watch-version resolves) and NORMAL (a CANDIDATE closed back before it opened). CANDIDATE,
#: OPEN and RECOVERING are live, and a `pw_detector_state` points at them, so they never age
#: out. `test_pw_retention` pins this tuple to the SQL literals in the `pw_anomaly` step.
TERMINAL_INCIDENT_STATES = (AnomalyState.RESOLVED.value, AnomalyState.NORMAL.value)


@dataclass(frozen=True)
class _RetentionStep:
    table: str
    #: Which entry of the run's `cutoffs` this step ages against.
    cutoff: str
    delete: str
    remaining: str
    #: Counts the rows the `delete` would have taken but for a guard (an active incident on a
    #: value, a detector state on an incident, or an unresolved alert); `None` for a table
    #: that no guard can protect.
    blocked: str | None = None


_STATEMENTS = (
    _RetentionStep(
        "pw_value_metric",
        "metric_cutoff",
        "DELETE FROM pw_value_metric WHERE (pw_value_id, bucket_start) IN ("
        "SELECT pw_value_id, bucket_start FROM pw_value_metric "
        "WHERE bucket_start < :cutoff LIMIT :batch)",
        "SELECT EXISTS (SELECT 1 FROM pw_value_metric WHERE bucket_start < :cutoff)",
    ),
    _RetentionStep(
        "pw_bucket",
        "metric_cutoff",
        "DELETE FROM pw_bucket WHERE (watch_id, bucket_start) IN ("
        "SELECT watch_id, bucket_start FROM pw_bucket "
        "WHERE bucket_start < :cutoff LIMIT :batch)",
        "SELECT EXISTS (SELECT 1 FROM pw_bucket WHERE bucket_start < :cutoff)",
    ),
    _RetentionStep(
        "pw_anomaly",
        "anomaly_cutoff",
        # Only a terminal incident ages. It is kept while a detector state points at it, and
        # while an unresolved alert depends on it: the engine resolves an incident's alert
        # together with the incident only while alerting is on, so an alert can outlive its
        # incident. The alert names the incident in `source_anomaly_ids` (the alert's
        # `source_id` is the value), so the JSONB membership test is the link. Transitions
        # leave by cascade.
        "DELETE FROM pw_anomaly WHERE id IN ("
        "SELECT a.id FROM pw_anomaly a "
        "WHERE a.state IN ( 'RESOLVED', 'NORMAL' ) "
        "AND COALESCE(a.resolved_at, a.anomaly_end, a.detected_at) < :cutoff "
        "AND NOT EXISTS (SELECT 1 FROM pw_detector_state s "
        "WHERE s.active_anomaly_id = a.id OR s.open_anomaly_id = a.id) "
        "AND NOT EXISTS (SELECT 1 FROM alert al WHERE al.status <> 'RESOLVED' "
        "AND jsonb_exists(al.source_anomaly_ids, a.id::text)) "
        "LIMIT :batch)",
        "SELECT EXISTS (SELECT 1 FROM pw_anomaly a "
        "WHERE a.state IN ( 'RESOLVED', 'NORMAL' ) "
        "AND COALESCE(a.resolved_at, a.anomaly_end, a.detected_at) < :cutoff "
        "AND NOT EXISTS (SELECT 1 FROM pw_detector_state s "
        "WHERE s.active_anomaly_id = a.id OR s.open_anomaly_id = a.id) "
        "AND NOT EXISTS (SELECT 1 FROM alert al WHERE al.status <> 'RESOLVED' "
        "AND jsonb_exists(al.source_anomaly_ids, a.id::text)))",
        "SELECT count(*) FROM pw_anomaly a "
        "WHERE a.state IN ( 'RESOLVED', 'NORMAL' ) "
        "AND COALESCE(a.resolved_at, a.anomaly_end, a.detected_at) < :cutoff "
        "AND (EXISTS (SELECT 1 FROM pw_detector_state s "
        "WHERE s.active_anomaly_id = a.id OR s.open_anomaly_id = a.id) "
        "OR EXISTS (SELECT 1 FROM alert al WHERE al.status <> 'RESOLVED' "
        "AND jsonb_exists(al.source_anomaly_ids, a.id::text)))",
    ),
    _RetentionStep(
        "pw_value",
        "metric_cutoff",
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
        now = self._clock()
        cutoffs = {
            "metric_cutoff": now - timedelta(days=self.settings.pw_metric_retention_days),
            "anomaly_cutoff": now - timedelta(days=self.settings.pw_anomaly_retention_days),
        }
        batch = self.settings.pw_retention_batch_size
        for step in _STATEMENTS:
            cutoff = cutoffs[step.cutoff]
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
                # One statement whatever the row count: the rows a guard keeps back.
                report.blocked[step.table] = int(
                    await self.session.scalar(
                        text(step.blocked).bindparams(cutoff=cutoff)
                    )
                    or 0
                )
        return report
