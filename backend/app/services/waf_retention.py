"""Bounded retention for WAF behavioral telemetry.

Each kind of data has its own rule, because they age differently:

  * `waf_endpoint_metric`, `waf_source_bucket` -- rows whose bucket started more
    than `WAF_METRIC_RETENTION_DAYS` ago. That setting must exceed the baseline
    lookback, so no rebuild ever loses a sample it relies on.
  * `waf_endpoint_baseline` -- cells not recomputed within
    `WAF_METRIC_RETENTION_DAYS`: every sample that trained them is already gone.
  * `waf_anomaly` and its transitions -- only terminal incidents (RESOLVED, or a
    CANDIDATE closed back to NORMAL / INSUFFICIENT_DATA) that ended more than
    `WAF_ANOMALY_RETENTION_DAYS` ago, and never one a detector state points at.
  * `waf_endpoint`, with its detector state and any remaining cells -- retired
    more than `WAF_METRIC_RETENTION_DAYS` ago and referenced by no incident.
  * `waf_source_profile` -- never; configuration goes only with its log source.

Active incidents -- CANDIDATE, OPEN, RECOVERING, SUPPRESSED -- are never deleted,
whatever their age or the age of the metrics that raised them. Evidence
provenance lives on the bucket rows and incidents and ages with them; there is no
separate WAF evidence table yet.

Deletes run in bounded batches (`WAF_RETENTION_BATCH_SIZE` rows, at most
`WAF_RETENTION_MAX_BATCHES` per table per run), each committed on its own, so a
large backlog is worked down across runs instead of in one long transaction.
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

TERMINAL_STATES = (
    AnomalyState.RESOLVED.value,
    AnomalyState.NORMAL.value,
    AnomalyState.INSUFFICIENT_DATA.value,
)


@dataclass
class WafRetentionReport:
    deleted: dict[str, int] = field(default_factory=dict)
    #: Tables whose backlog was not cleared within the batch limit.
    incomplete: list[str] = field(default_factory=list)
    skipped_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "deleted": self.deleted,
            "incomplete": self.incomplete,
            "skipped_reason": self.skipped_reason,
        }


_STATEMENTS: tuple[tuple[str, str, str], ...] = (
    (
        "waf_endpoint_metric",
        "metric_cutoff",
        "DELETE FROM waf_endpoint_metric WHERE (waf_endpoint_id, bucket_start) IN ("
        "SELECT waf_endpoint_id, bucket_start FROM waf_endpoint_metric "
        "WHERE bucket_start < :cutoff LIMIT :batch)",
    ),
    (
        "waf_source_bucket",
        "metric_cutoff",
        "DELETE FROM waf_source_bucket WHERE (log_source_id, bucket_start) IN ("
        "SELECT log_source_id, bucket_start FROM waf_source_bucket "
        "WHERE bucket_start < :cutoff LIMIT :batch)",
    ),
    (
        "waf_endpoint_baseline",
        "metric_cutoff",
        "DELETE FROM waf_endpoint_baseline WHERE id IN ("
        "SELECT id FROM waf_endpoint_baseline "
        "WHERE computed_at IS NOT NULL AND computed_at < :cutoff LIMIT :batch)",
    ),
    (
        "waf_anomaly",
        "anomaly_cutoff",
        "DELETE FROM waf_anomaly WHERE id IN ("
        "SELECT a.id FROM waf_anomaly a "
        "WHERE a.state IN ('RESOLVED', 'NORMAL', 'INSUFFICIENT_DATA') "
        "AND COALESCE(a.resolved_at, a.anomaly_end, a.detected_at) < :cutoff "
        "AND NOT EXISTS (SELECT 1 FROM waf_detector_state s "
        "WHERE s.active_anomaly_id = a.id OR s.open_anomaly_id = a.id) "
        "LIMIT :batch)",
    ),
    (
        "waf_endpoint",
        "metric_cutoff",
        "DELETE FROM waf_endpoint WHERE id IN ("
        "SELECT e.id FROM waf_endpoint e "
        "WHERE e.is_active = false AND e.retired_at IS NOT NULL AND e.retired_at < :cutoff "
        "AND NOT EXISTS (SELECT 1 FROM waf_anomaly a WHERE a.waf_endpoint_id = e.id) "
        "AND NOT EXISTS (SELECT 1 FROM waf_endpoint_metric m WHERE m.waf_endpoint_id = e.id) "
        "LIMIT :batch)",
    ),
)


class WafRetention:
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

    async def run(self) -> WafRetentionReport:
        report = WafRetentionReport()
        if not self.settings.waf_retention_enabled:
            report.skipped_reason = "WAF retention is disabled"
            return report
        if self.settings.waf_metric_retention_days <= self.settings.baseline_lookback_days:
            # Startup refuses this combination only when WAF collection is on,
            # so it can still reach a run. Deleting would remove samples every
            # baseline rebuild relies on; do nothing and say why.
            report.skipped_reason = (
                "WAF_METRIC_RETENTION_DAYS does not exceed BASELINE_LOOKBACK_DAYS; "
                "nothing deleted"
            )
            return report
        now = self._clock()
        cutoffs = {
            "metric_cutoff": now - timedelta(days=self.settings.waf_metric_retention_days),
            "anomaly_cutoff": now - timedelta(days=self.settings.waf_anomaly_retention_days),
        }
        batch = self.settings.waf_retention_batch_size
        for table, cutoff_name, statement in _STATEMENTS:
            deleted = 0
            for _ in range(self.settings.waf_retention_max_batches):
                result = await self.session.execute(
                    text(statement).bindparams(cutoff=cutoffs[cutoff_name], batch=batch)
                )
                await self.session.commit()
                count = int(getattr(result, "rowcount", 0) or 0)
                deleted += count
                if count < batch:
                    break
            else:
                report.incomplete.append(table)
            report.deleted[table] = deleted
        return report
