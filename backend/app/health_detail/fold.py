"""The pure fold of the health searches (D-42 section 3, AGENTS.md rules 3 and 8).

Raw values live only here: log-source names, mount names, metric ids and QID names go in,
and only the bounded aggregates leave. Nothing is stored that the allowlists did not pass.

  * Disk metrics are kept only for the allowlisted mounts. An overlay, `/run/user/...` or
    `/dev/shm` is dropped and counted, never shown.
  * Only the fixed metric list is kept. Anything else is dropped and counted.
  * A host is the hostname parsed from the log-source name. A name that does not parse is
    counted (distinct names) and its rows are dropped; a host is never guessed.
  * A stage metric (`*ThreadInfo`) holds one row per thread state in `Element`. Only
    `RunnableRatio`, the share of the stage's threads that are RUNNABLE, is kept, stored with
    that element. The six other known states are not a lost measurement: they are skipped and
    not counted. A stage row with no element, or one that names no known state, is counted in
    `dropped_stage_states`.
  * A value that is missing, negative, or outside the metric's range is not a measurement:
    the row is dropped and counted. A missing value is never turned into a zero.
  * Notifications keep the busiest `NOTIFICATION_TOP` (host, QID name) groups; the rest is
    one counted remainder, so the totals still add up.

No database, no clock and no provider call.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.health_detail.rows import HealthMetricRow, HealthNotificationRow
from app.health_detail.spec import (
    DISK_METRIC_SET,
    EXCLUDED_QID_NAME,
    METRIC_ID_SET,
    NOTIFICATION_TOP,
    STAGE_BUSY_ELEMENT,
    STAGE_METRICS,
    STAGE_OTHER_STATES,
    parse_host,
)

#: Largest value a metric can honestly hold. A metric above its bound is not a measurement.
_UPPER_BOUND: dict[str, float] = {
    "DiskUsage": 1.0,
    "SystemCPU": 100.0,
    "SystemMemoryUsed": 100.0,
    **dict.fromkeys(STAGE_METRICS, 100.0),
}
_STAGE_METRIC_SET = frozenset(STAGE_METRICS)
#: Float noise on a bound (a share of 1.0000001 is 1).
_TOLERANCE = 1e-6


@dataclass(frozen=True)
class MetricBucketFact:
    """One stored (host, metric, element) aggregate of a window.

    `element` is the mount for a disk metric, `RunnableRatio` for a stage metric and the empty
    string for every other metric.
    """

    host: str
    metric: str
    element: str
    max_value: float
    avg_value: float | None
    samples: int


@dataclass(frozen=True)
class MetricFold:
    buckets: tuple[MetricBucketFact, ...]
    #: Distinct log-source names that held no hostname.
    unparseable_hosts: int
    #: Disk rows on a mount that is not in the allowlist (or on no mount).
    dropped_mounts: int
    #: Rows of a metric that is not in the fixed list.
    dropped_metrics: int
    #: Rows with no usable value: NULL, not a number.
    null_values: int
    #: Rows with a negative value, or one above the metric's range.
    invalid_values: int
    #: Stage rows whose `Element` is missing or names no known thread state. The six known
    #: states other than `RunnableRatio` are skipped without being counted.
    dropped_stage_states: int


def _merge(a: MetricBucketFact, b: MetricBucketFact) -> MetricBucketFact:
    samples = a.samples + b.samples
    avg: float | None = None
    if a.avg_value is not None and b.avg_value is not None and samples > 0:
        avg = (a.avg_value * a.samples + b.avg_value * b.samples) / samples
    return MetricBucketFact(
        host=a.host,
        metric=a.metric,
        element=a.element,
        max_value=max(a.max_value, b.max_value),
        avg_value=avg,
        samples=samples,
    )


def fold_metrics(rows: Sequence[HealthMetricRow], *, mounts: Collection[str]) -> MetricFold:
    """Metric rows to the stored aggregates, with every drop counted."""
    allowed = frozenset(mounts)
    merged: dict[tuple[str, str, str], MetricBucketFact] = {}
    bad_names: set[str] = set()
    dropped_mounts = dropped_metrics = null_values = invalid_values = 0
    dropped_stage_states = 0
    for row in rows:
        if row.metric not in METRIC_ID_SET:
            dropped_metrics += 1
            continue
        host = parse_host(row.source)
        if host is None:
            bad_names.add(row.source or "")
            continue
        metric = row.metric
        assert metric is not None  # in METRIC_ID_SET
        element = ""
        if metric in DISK_METRIC_SET:
            if row.element is None or row.element not in allowed:
                dropped_mounts += 1
                continue
            element = row.element
        elif metric in _STAGE_METRIC_SET:
            if row.element != STAGE_BUSY_ELEMENT:
                if row.element is None or row.element not in STAGE_OTHER_STATES:
                    dropped_stage_states += 1
                continue
            element = STAGE_BUSY_ELEMENT
        if row.max_value is None:
            null_values += 1
            continue
        bound = _UPPER_BOUND.get(metric)
        if row.max_value < 0 or (bound is not None and row.max_value > bound + _TOLERANCE):
            invalid_values += 1
            continue
        fact = MetricBucketFact(
            host=host,
            metric=metric,
            element=element,
            max_value=row.max_value,
            avg_value=row.avg_value,
            samples=row.samples,
        )
        key = (host, metric, element)
        merged[key] = _merge(merged[key], fact) if key in merged else fact
    return MetricFold(
        buckets=tuple(merged[key] for key in sorted(merged)),
        unparseable_hosts=len(bad_names),
        dropped_mounts=dropped_mounts,
        dropped_metrics=dropped_metrics,
        null_values=null_values,
        invalid_values=invalid_values,
        dropped_stage_states=dropped_stage_states,
    )


@dataclass(frozen=True)
class NotificationFact:
    host: str
    qid_name: str
    events: int
    max_severity: int | None
    last_seen: datetime | None


@dataclass(frozen=True)
class NotificationFold:
    #: The busiest groups, at most `NOTIFICATION_TOP`, busiest first.
    entries: tuple[NotificationFact, ...]
    #: Events and groups below the top, summed. Zero groups means no remainder.
    remainder_events: int
    remainder_groups: int
    unparseable_hosts: int
    #: Events in groups dropped for an unparseable host or no QID name.
    dropped_events: int


def fold_notifications(rows: Sequence[HealthNotificationRow]) -> NotificationFold:
    """Notification rows to the top groups plus one counted remainder."""
    merged: dict[tuple[str, str], NotificationFact] = {}
    bad_names: set[str] = set()
    dropped_events = 0
    for row in rows:
        if row.qid_name == EXCLUDED_QID_NAME:
            continue
        host = parse_host(row.source)
        if host is None:
            bad_names.add(row.source or "")
            dropped_events += row.events
            continue
        if row.qid_name is None:
            dropped_events += row.events
            continue
        key = (host, row.qid_name)
        previous = merged.get(key)
        if previous is None:
            merged[key] = NotificationFact(
                host, row.qid_name, row.events, row.max_severity, row.last_seen
            )
            continue
        severities = [s for s in (previous.max_severity, row.max_severity) if s is not None]
        seen = [t for t in (previous.last_seen, row.last_seen) if t is not None]
        merged[key] = NotificationFact(
            host,
            row.qid_name,
            previous.events + row.events,
            max(severities) if severities else None,
            max(seen) if seen else None,
        )
    ordered = sorted(merged.values(), key=lambda f: (-f.events, f.host, f.qid_name))
    top, rest = ordered[:NOTIFICATION_TOP], ordered[NOTIFICATION_TOP:]
    return NotificationFold(
        entries=tuple(top),
        remainder_events=sum(f.events for f in rest),
        remainder_groups=len(rest),
        unparseable_hosts=len(bad_names),
        dropped_events=dropped_events,
    )
