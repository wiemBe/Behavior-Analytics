"""What the QRadar health collection asks for, in one place (D-42 section 1 and 3).

The metric list is fixed: a metric that is not named here is never asked for and, if QRadar
sends it anyway, is dropped by the fold. Metric ids are QRadar's own, from the console's
`/health/metrics/system_metrics` and `/health/metrics/qradar_metrics` catalogs.
"""

from __future__ import annotations

import re

#: Disk metrics are per mount: the `Element` of the event names the mount.
DISK_METRICS: tuple[str, ...] = ("DiskUsage", "DiskSpaceUsed", "DiskSpaceTotal")
SYSTEM_METRICS: tuple[str, ...] = (
    "SystemCPU",
    "SystemMemoryUsed",
    "LoadAvg5",
    "SystemSwapUtil",
    "IoWait",
)
#: Events waiting for the rule engine, and bytes of pipeline queue spilled to disk.
#: `QueuedEventThrottleFilterDiskSize` is the license throttle: events spilled because the
#: host is over its EPS allocation.
PIPELINE_METRICS: tuple[str, ...] = (
    "CREQueueSize",
    "QueuedEventThrottleFilterDiskSize",
    "ECTCPTOEPDiskSize",
    "FlowGovernerQueueDiskSize",
)
#: Busy share (percent, 0-100) of each pipeline stage; the page shows the busiest. A stage
#: metric holds one event per thread state in `Element`; the busy share is the RUNNABLE one.
STAGE_METRICS: tuple[str, ...] = (
    "AggOutThreadInfo",
    "AggregationThreadInfo",
    "ArielWriterThreadInfo",
    "CREPRocessorThreadInfo",
    "EventParserThreadInfo",
    "EventsIndexerThreadInfo",
    "PreprocessorThreadInfo",
    "QueuedEventThrottleFilterThreadInfo",
    "WorkerThreadInfo",
)
#: The `Element` of a stage metric that is the busy share: threads in the RUNNABLE state.
STAGE_BUSY_ELEMENT = "RunnableRatio"
#: The other thread states a stage metric reports. They are not stored and not an error.
STAGE_OTHER_STATES: frozenset[str] = frozenset(
    {
        "BlockedRatio",
        "CpuUtil",
        "NewRatio",
        "TerminatingRatio",
        "TimedWaitRatio",
        "WaitingRatio",
    }
)
ARIEL_METRICS: tuple[str, ...] = ("RunningQueries", "HostRunningQueries")

#: Every metric id a search may name, in a fixed order.
METRIC_IDS: tuple[str, ...] = (
    *DISK_METRICS,
    *SYSTEM_METRICS,
    *PIPELINE_METRICS,
    *STAGE_METRICS,
    *ARIEL_METRICS,
)
METRIC_ID_SET: frozenset[str] = frozenset(METRIC_IDS)
DISK_METRIC_SET: frozenset[str] = frozenset(DISK_METRICS)

#: The metric ids are interpolated into AQL, so each one must be a plain identifier.
METRIC_ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]{0,63}")

#: The shipped custom properties the metric search reads. A console without them cannot be
#: asked: the metric part is NOT_COLLECTED, not guessed.
PROPERTY_METRIC_ID = "Metric ID"
PROPERTY_VALUE = "Value"
PROPERTY_ELEMENT = "Element"
REQUIRED_PROPERTIES: tuple[str, ...] = (PROPERTY_METRIC_ID, PROPERTY_VALUE, PROPERTY_ELEMENT)

LOG_SOURCE_TYPE_METRICS = "Health Metrics"
LOG_SOURCE_TYPE_NOTIFICATIONS = "System Notification"
#: The one notification QID name that is noise; excluded in the AQL.
EXCLUDED_QID_NAME = "Information Message"

#: Notifications kept per run (busiest first); the rest is a counted remainder.
NOTIFICATION_TOP = 50

#: Stored on the run row; bump it when a query or the fold changes meaning.
#: 2: a stage metric is stored per thread state, only `RunnableRatio` (before, `''` held the
#: max over all seven states, which is not a busy share).
QUERY_VERSION = 2

#: What the API calls the metrics. The fullest-mount, queue and spill series use these.
METRIC_DISK_USAGE = "DiskUsage"
METRIC_CRE_QUEUE = "CREQueueSize"
METRIC_THROTTLE_SPILL = "QueuedEventThrottleFilterDiskSize"

_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,253}")
_NAME_SEPARATOR = " :: "


def parse_host(source_name: object) -> str | None:
    """The hostname of a `<log source name> :: <hostname>` log-source name, else None.

    `Health Metrics-<n> :: <hostname>` is the shipped name; the same shape names the system
    notification sources. A name that does not fit is not guessed at: the caller counts it.
    """
    if not isinstance(source_name, str):
        return None
    prefix, separator, host = source_name.rpartition(_NAME_SEPARATOR)
    if not separator or not prefix.strip():
        return None
    host = host.strip()
    return host if _HOST.fullmatch(host) else None


for _metric in METRIC_IDS:
    if METRIC_ID_PATTERN.fullmatch(_metric) is None:  # pragma: no cover - import-time guard
        raise ValueError("a health metric id is not a plain identifier")
if len(set(METRIC_IDS)) != len(METRIC_IDS):  # pragma: no cover - import-time guard
    raise ValueError("a health metric id is listed twice")
