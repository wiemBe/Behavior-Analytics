"""The alert seam F-2 plugs into. F-1 records breaches and calls this; it does not
open alerts.

`NoOpCustomRuleAlertSink` is the default. F-2 replaces it with an `AlertService`
adapter (the `AlertEnqueuer` pattern in `app.anomaly.engine`). The evaluator
stays free of the alert tables until that adapter is wired.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class CustomRuleAlertEvent:
    """One breach transition the sink should reflect as an alert.

    Counts, shares, labels and bucket bounds only (AGENTS.md rule 3). `action`
    is ``open`` when the breach starts, ``refresh`` while it is still breaching,
    and ``resolve`` after recovery.
    """

    action: str
    rule_id: uuid.UUID
    breach_id: uuid.UUID
    rule_name: str
    severity: str
    subject_kind: str
    subject_label: str
    metric: str
    unit: str
    operator: str
    threshold_value: float
    observed_value: float | None
    measurement: str | None
    first_bucket_start: datetime
    last_bucket_start: datetime
    unmeasured_buckets: int
    reason: str | None = None


class CustomRuleAlertSink(Protocol):
    """What F-2 implements. Both methods run inside the evaluator's transaction."""

    async def open_or_update(self, event: CustomRuleAlertEvent) -> None:
        """Open the breach's alert, or refresh it while the condition still holds."""

    async def resolve(self, event: CustomRuleAlertEvent) -> None:
        """Resolve the breach's alert. `event.reason` says why."""


class NoOpCustomRuleAlertSink:
    """F-1's sink: the breach row is the whole record, and nothing is paged."""

    async def open_or_update(self, event: CustomRuleAlertEvent) -> None:
        return None

    async def resolve(self, event: CustomRuleAlertEvent) -> None:
        return None


__all__ = [
    "CustomRuleAlertEvent",
    "CustomRuleAlertSink",
    "NoOpCustomRuleAlertSink",
]
