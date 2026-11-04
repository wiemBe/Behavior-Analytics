"""Reads of the stored QRadar health windows: the latest window and the history (D-42).

Database only: nothing here asks QRadar. Both reads run a constant number of statements however
many consoles and hosts there are (AGENTS.md rule 9): the consoles, then one statement per
table for all of them at once.

A console with no window, or whose newest run did not measure a part, answers NOT_COLLECTED or
FAILED with the stored reason and no value. A window older than the cadence allows answers
STALE and keeps its values with the window's time, so a reader never takes a stale figure for a
current one.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.health_detail import spec
from app.i18n.messages import MessageRef, render
from app.models.enums import SearchFamily
from app.models.instance import QRadarInstance
from app.models.qradar_health import (
    QRadarHealthMetricBucket,
    QRadarHealthNotificationBucket,
    QRadarHealthRestSample,
    QRadarHealthRun,
)
from app.schemas.qradar_health_detail import (
    ConsoleHistory,
    ConsoleLatest,
    HistoryOut,
    HistoryPoint,
    HistorySeries,
    HostDisk,
    HostHealth,
    HostHistory,
    HostPipeline,
    HostSystem,
    LatestOut,
    MetricCell,
    MountValue,
    NotificationEntry,
    NotificationRemainder,
    NotificationsValue,
    PartState,
    PartStatus,
    ReachabilityPoint,
    ReachabilitySeries,
    RestSampleOut,
    StageValue,
    WindowOut,
)
from app.services.search_cadence import SearchCadenceService

_REASON = "qradar_health_detail.reason."
#: The history answers one of these ranges, in hours.
HISTORY_HOURS: tuple[int, ...] = (24, 168)
#: A series longer than this is folded into windows of equal time, keeping each one's maximum.
MAX_SERIES_POINTS = 400
_MEASURED = ("OK", "PARTIAL")
#: A notification range is 1 to this many hours (one week, as the history).
NOTIFICATION_HOURS_MAX = 168
#: Groups a notification roll-up lists; the rest is the counted remainder.
NOTIFICATION_ROLLUP_LIMIT = 50
_STAGE_SUFFIX = "ThreadInfo"


def reason_for(code: str) -> str:
    """The English sentence of a `qradar_health_detail.reason.<code>` message."""
    return render(MessageRef(_REASON + code))


def _stored_reason(code: str | None, params: Mapping[str, Any] | None) -> tuple[str, str]:
    """The stored reason of a part as (code, sentence); an unknown code never raises."""
    if not code:
        return "unknown", "The reason was not recorded."
    try:
        return code, render(MessageRef(code, dict(params or {})))
    except Exception:  # a code from a newer release: say so rather than fail the read
        return code, "The reason is not known to this release."


def disk_level(fraction: float | None, settings: Settings) -> str | None:
    """OK, WARN or FAIL for a disk share of 0 to 1, by the configured percentages."""
    if fraction is None:
        return None
    percent = fraction * 100
    if percent >= settings.qradar_health_disk_fail_percent:
        return "FAIL"
    if percent >= settings.qradar_health_disk_warn_percent:
        return "WARN"
    return "OK"


def is_stale(
    window_end: datetime, cadence_seconds: int, now: datetime, settings: Settings
) -> bool:
    """The newest window ended longer ago than `QRADAR_HEALTH_STALE_CADENCES` cadences allow."""
    allowed = (
        settings.qradar_health_stale_cadences * cadence_seconds
        + settings.search_cadence_settle_seconds
    )
    return (now - window_end).total_seconds() > allowed


def _missing_cell(code: str = "metric_not_reported") -> MetricCell:
    return MetricCell(state=PartState.NOT_COLLECTED, reason_code=code, reason=reason_for(code))


# ------------------------------------------------------------------------------ latest
def _cell(
    rows: Mapping[tuple[str, str], QRadarHealthMetricBucket], metric: str, state: PartState
) -> MetricCell:
    row = rows.get((metric, ""))
    if row is None:
        return _missing_cell()
    return MetricCell(
        state=state, value=row.max_value, average=row.avg_value, samples=int(row.samples)
    )


def _mount_values(
    rows: Mapping[tuple[str, str], QRadarHealthMetricBucket],
    settings: Settings,
    carried: Mapping[str, tuple[float, datetime]] | None = None,
) -> list[MountValue]:
    mounts = sorted({element for (metric, element) in rows if metric in spec.DISK_METRIC_SET})
    out: list[MountValue] = []
    for mount in mounts:
        usage = rows.get(("DiskUsage", mount))
        used = rows.get(("DiskSpaceUsed", mount))
        total = rows.get(("DiskSpaceTotal", mount))
        fraction = usage.max_value if usage is not None else None
        total_bytes = total.max_value if total is not None else None
        total_from: datetime | None = None
        if total is None and carried is not None and mount in carried:
            total_bytes, total_from = carried[mount]
        free: float | None = None
        if used is not None and total_bytes is not None and total_bytes >= used.max_value:
            free = total_bytes - used.max_value
        out.append(
            MountValue(
                mount=mount,
                used_fraction=fraction,
                used_percent=fraction * 100 if fraction is not None else None,
                used_bytes=used.max_value if used is not None else None,
                total_bytes=total_bytes,
                total_from=total_from,
                free_bytes=free,
                level=disk_level(fraction, settings),
            )
        )
    return out


def host_health(
    rows: Mapping[tuple[str, str], QRadarHealthMetricBucket],
    host: str,
    state: PartState,
    settings: Settings,
    carried: Mapping[str, tuple[float, datetime]] | None = None,
) -> HostHealth:
    """One host's cells from its stored aggregates of a window. `state` is OK or STALE.

    `carried` maps a mount to (total bytes, end of its window) from an earlier window, used only
    for a mount whose own window holds no `DiskSpaceTotal`.
    """
    mounts = _mount_values(rows, settings, carried)
    measured = [m for m in mounts if m.used_fraction is not None]
    fullest = max(measured, key=lambda m: (m.used_fraction or 0.0, m.mount), default=None)
    if mounts:
        disk = HostDisk(state=state, fullest=fullest, mounts=mounts)
    else:
        disk = HostDisk(
            state=PartState.NOT_COLLECTED,
            reason_code="metric_not_reported",
            reason=reason_for("metric_not_reported"),
        )
    # D-43: a stage's RunnableRatio is 0 or 100 per sample, so its maximum only says "busy at
    # least once"; the busiest stage is the highest window average (the share of the window it was
    # busy). A row without an average is left out, never read as 0. `max` keeps the first of equal
    # averages, so a tie goes to the earlier stage in `spec.STAGE_METRICS`.
    stages = [
        (metric, average)
        for metric in spec.STAGE_METRICS
        if (row := rows.get((metric, spec.STAGE_BUSY_ELEMENT))) is not None
        and (average := row.avg_value) is not None
    ]
    busiest = max(stages, key=lambda item: item[1], default=None)
    spill = _cell(rows, spec.METRIC_THROTTLE_SPILL, state)
    return HostHealth(
        host=host,
        disk=disk,
        system=HostSystem(
            cpu_percent=_cell(rows, "SystemCPU", state),
            memory_percent=_cell(rows, "SystemMemoryUsed", state),
            load_avg_5=_cell(rows, "LoadAvg5", state),
            swap_percent=_cell(rows, "SystemSwapUtil", state),
            io_wait=_cell(rows, "IoWait", state),
            running_queries=_cell(rows, "RunningQueries", state),
            host_running_queries=_cell(rows, "HostRunningQueries", state),
        ),
        pipeline=HostPipeline(
            cre_queue=_cell(rows, spec.METRIC_CRE_QUEUE, state),
            throttle_spill_bytes=spill,
            throttle_spilling=(spill.value > 0) if spill.value is not None else None,
            ect_tcp_to_ep_disk_bytes=_cell(rows, "ECTCPTOEPDiskSize", state),
            flow_governor_queue_disk_bytes=_cell(rows, "FlowGovernerQueueDiskSize", state),
            busiest_stage=(
                StageValue(
                    stage=busiest[0].removesuffix(_STAGE_SUFFIX) or busiest[0],
                    busy_percent=busiest[1],
                )
                if busiest is not None
                else None
            ),
        ),
    )


def _part_status(
    stored_state: str, code: str | None, params: Mapping[str, Any] | None, *, stale: bool
) -> PartStatus:
    if stored_state in _MEASURED and stale:
        return PartStatus(
            state=PartState.STALE, reason_code="stale", reason=reason_for("stale")
        )
    if stored_state == "OK":
        return PartStatus(state=PartState.OK)
    reason_code, reason = _stored_reason(code, params)
    if stored_state == "PARTIAL":
        return PartStatus(state=PartState.PARTIAL, reason_code=reason_code, reason=reason)
    state = PartState.FAILED if stored_state == "FAILED" else PartState.NOT_COLLECTED
    return PartStatus(state=state, reason_code=reason_code, reason=reason)


def _no_window() -> PartStatus:
    return PartStatus(
        state=PartState.NOT_COLLECTED, reason_code="no_window", reason=reason_for("no_window")
    )


def rollup_notifications(
    rows: Sequence[Any], coverage: tuple[int, datetime, datetime] | None, hours: int
) -> NotificationsValue | None:
    """One console's notifications over a range, from the summed groups of its windows.

    `rows` are (kind, host, qid_name, events, groups, max_severity, last_seen) summed over the
    range; `coverage` is (windows, first start, last end) of the measured windows, or None. No
    measured window in the range is no list at all, never an empty one: "nothing happened" is
    only said of a range that was looked at.
    """
    if coverage is None or coverage[0] < 1:
        return None
    groups = sorted((r for r in rows if r[0] == "GROUP"), key=lambda r: (-r[3], r[1], r[2]))
    kept, dropped = groups[:NOTIFICATION_ROLLUP_LIMIT], groups[NOTIFICATION_ROLLUP_LIMIT:]
    rest_events = sum(int(r[3]) for r in rows if r[0] == "REMAINDER") + sum(
        int(r[3]) for r in dropped
    )
    rest_groups = sum(int(r[4]) for r in rows if r[0] == "REMAINDER") + len(dropped)
    return NotificationsValue(
        entries=[
            NotificationEntry(
                host=r[1],
                qid_name=r[2],
                events=int(r[3]),
                max_severity=r[5],
                last_seen=r[6],
            )
            for r in kept
        ],
        remainder=(
            NotificationRemainder(events=rest_events, groups=rest_groups)
            if rest_groups > 0
            else None
        ),
        hours=hours,
        windows=int(coverage[0]),
        covered_from=coverage[1],
        covered_until=coverage[2],
    )


async def read_latest(
    session: AsyncSession,
    settings: Settings,
    *,
    now: datetime | None = None,
    notifications_hours: int | None = None,
) -> LatestOut:
    """The newest stored window of every enabled console, with a constant number of statements.

    With `notifications_hours` the notification list is the roll-up of the measured windows of
    that many hours instead of the newest window alone; a fixed number of statements, however many
    consoles there are. Everything else is the newest window either way.
    """
    generated = now or datetime.now(UTC)
    instances = list(
        (
            await session.scalars(
                select(QRadarInstance)
                .where(QRadarInstance.enabled.is_(True))
                .order_by(QRadarInstance.name, QRadarInstance.id)
            )
        ).all()
    )
    enabled = settings.qradar_health_collection_enabled
    if not instances:
        return LatestOut(generated_at=generated, collection_enabled=enabled, consoles=[])
    ids = [instance.id for instance in instances]

    runs = {
        run.instance_id: run
        for run in (
            await session.scalars(
                select(QRadarHealthRun)
                .where(QRadarHealthRun.instance_id.in_(ids))
                .ext(distinct_on(QRadarHealthRun.instance_id))
                .order_by(QRadarHealthRun.instance_id, QRadarHealthRun.window_start.desc())
            )
        ).all()
    }
    metric_pairs = [
        (run.instance_id, run.window_start)
        for run in runs.values()
        if run.metrics_state in _MEASURED
    ]
    notice_pairs = [
        (run.instance_id, run.window_start)
        for run in runs.values()
        if run.notifications_state in _MEASURED
    ]
    by_host: dict[UUID, dict[str, dict[tuple[str, str], QRadarHealthMetricBucket]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    carried_totals: dict[UUID, dict[str, dict[str, tuple[float, datetime]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    if metric_pairs:
        for bucket in (
            await session.scalars(
                select(QRadarHealthMetricBucket).where(
                    tuple_(
                        QRadarHealthMetricBucket.instance_id,
                        QRadarHealthMetricBucket.bucket_start,
                    ).in_(metric_pairs)
                )
            )
        ).all():
            by_host[bucket.instance_id][bucket.host][(bucket.metric, bucket.element)] = bucket
        # One statement for every console, host and mount: the newest stored total in the
        # carry bound before each console's latest window (QRadar sends it once an hour).
        nb_total = QRadarHealthMetricBucket
        in_bound = [
            and_(
                nb_total.instance_id == run.instance_id,
                nb_total.bucket_start < run.window_start,
                nb_total.bucket_end >= run.window_end - spec.DISK_TOTAL_MAX_AGE,
            )
            for run in runs.values()
            if run.metrics_state in _MEASURED
        ]
        for total in (
            await session.scalars(
                select(nb_total)
                .where(nb_total.metric == spec.DISK_TOTAL_METRIC, or_(*in_bound))
                .ext(distinct_on(nb_total.instance_id, nb_total.host, nb_total.element))
                .order_by(
                    nb_total.instance_id,
                    nb_total.host,
                    nb_total.element,
                    nb_total.bucket_start.desc(),
                )
            )
        ).all():
            carried_totals[total.instance_id][total.host][total.element] = (
                total.max_value,
                total.bucket_end,
            )
    notices: dict[UUID, list[QRadarHealthNotificationBucket]] = defaultdict(list)
    rollups: dict[UUID, NotificationsValue | None] = {}
    if notifications_hours is None:
        if notice_pairs:
            for notice in (
                await session.scalars(
                    select(QRadarHealthNotificationBucket).where(
                        tuple_(
                            QRadarHealthNotificationBucket.instance_id,
                            QRadarHealthNotificationBucket.bucket_start,
                        ).in_(notice_pairs)
                    )
                )
            ).all():
                notices[notice.instance_id].append(notice)
    else:
        since = generated - timedelta(hours=notifications_hours)
        measured_run = and_(
            QRadarHealthRun.instance_id.in_(ids),
            QRadarHealthRun.window_start >= since,
            QRadarHealthRun.window_start <= generated,
            QRadarHealthRun.notifications_state.in_(_MEASURED),
        )
        coverage = {
            row[0]: (row[1], row[2], row[3])
            for row in (
                await session.execute(
                    select(
                        QRadarHealthRun.instance_id,
                        func.count(),
                        func.min(QRadarHealthRun.window_start),
                        func.max(QRadarHealthRun.window_end),
                    )
                    .where(measured_run)
                    .group_by(QRadarHealthRun.instance_id)
                )
            ).all()
        }
        summed: dict[UUID, list[Any]] = defaultdict(list)
        nb = QRadarHealthNotificationBucket
        for row in (
            await session.execute(
                select(
                    nb.instance_id,
                    nb.kind,
                    nb.host,
                    nb.qid_name,
                    func.sum(nb.events),
                    func.sum(nb.groups),
                    func.max(nb.max_severity),
                    func.max(nb.last_seen),
                )
                .join(
                    QRadarHealthRun,
                    and_(
                        QRadarHealthRun.instance_id == nb.instance_id,
                        QRadarHealthRun.window_start == nb.bucket_start,
                    ),
                )
                .where(measured_run)
                .group_by(nb.instance_id, nb.kind, nb.host, nb.qid_name)
            )
        ).all():
            summed[row[0]].append(row[1:])
        for instance_id in ids:
            rollups[instance_id] = rollup_notifications(
                summed.get(instance_id, []), coverage.get(instance_id), notifications_hours
            )
    last_good = {
        row[0]: row[1]
        for row in (
            await session.execute(
                select(QRadarHealthRun.instance_id, func.max(QRadarHealthRun.window_end))
                .where(
                    QRadarHealthRun.instance_id.in_(ids),
                    QRadarHealthRun.metrics_state.in_(_MEASURED),
                )
                .group_by(QRadarHealthRun.instance_id)
            )
        ).all()
    }
    samples = {
        sample.instance_id: sample
        for sample in (
            await session.scalars(
                select(QRadarHealthRestSample)
                .where(QRadarHealthRestSample.instance_id.in_(ids))
                .ext(distinct_on(QRadarHealthRestSample.instance_id))
                .order_by(
                    QRadarHealthRestSample.instance_id, QRadarHealthRestSample.slot_start.desc()
                )
            )
        ).all()
    }

    consoles: list[ConsoleLatest] = []
    for instance in instances:
        run = runs.get(instance.id)
        sample = samples.get(instance.id)
        rest = (
            RestSampleOut(
                sampled_at=sample.sampled_at,
                reachable=sample.reachable,
                api_latency_ms=sample.api_latency_ms,
                hosts_active=sample.hosts_active,
                hosts_total=sample.hosts_total,
                ha_problem_count=sample.ha_problem_count,
                license_overallocated=sample.license_overallocated,
            )
            if sample is not None
            else None
        )
        if run is None:
            consoles.append(
                ConsoleLatest(
                    instance_id=instance.id,
                    instance_name=instance.name,
                    metrics=_no_window(),
                    notifications=_no_window(),
                    rest_sample=rest,
                )
            )
            continue
        stale = is_stale(run.window_end, run.cadence_seconds, generated, settings)
        metrics = _part_status(
            run.metrics_state, run.metrics_reason_code, run.metrics_reason_params, stale=stale
        )
        notifications = _part_status(
            run.notifications_state,
            run.notifications_reason_code,
            run.notifications_reason_params,
            stale=stale,
        )
        cell_state = PartState.STALE if stale else PartState.OK
        hosts = [
            host_health(
                rows, host, cell_state, settings, carried_totals.get(instance.id, {}).get(host)
            )
            for host, rows in sorted(by_host.get(instance.id, {}).items())
        ]
        listing: NotificationsValue | None = None
        if notifications_hours is not None:
            listing = rollups.get(instance.id)
        elif run.notifications_state in _MEASURED:
            groups = sorted(
                (n for n in notices.get(instance.id, []) if n.kind == "GROUP"),
                key=lambda n: (-n.events, n.host, n.qid_name),
            )
            rest_rows = [n for n in notices.get(instance.id, []) if n.kind == "REMAINDER"]
            listing = NotificationsValue(
                entries=[
                    NotificationEntry(
                        host=n.host,
                        qid_name=n.qid_name,
                        events=int(n.events),
                        max_severity=n.max_severity,
                        last_seen=n.last_seen,
                    )
                    for n in groups
                ],
                remainder=(
                    NotificationRemainder(
                        events=int(rest_rows[0].events), groups=rest_rows[0].groups
                    )
                    if rest_rows
                    else None
                ),
            )
        consoles.append(
            ConsoleLatest(
                instance_id=instance.id,
                instance_name=instance.name,
                window=WindowOut(
                    start=run.window_start,
                    end=run.window_end,
                    collected_at=run.collected_at,
                    cadence_seconds=run.cadence_seconds,
                ),
                metrics=metrics,
                notifications=notifications,
                hosts=hosts,
                notification_list=listing,
                last_metrics_window_end=last_good.get(instance.id),
                unparseable_hosts=run.unparseable_hosts,
                dropped_mounts=run.dropped_mounts,
                dropped_metrics=run.dropped_metrics,
                null_values=run.null_values,
                invalid_values=run.invalid_values,
                dropped_events=int(run.dropped_events),
                rest_sample=rest,
            )
        )
    return LatestOut(generated_at=generated, collection_enabled=enabled, consoles=consoles)


# ---------------------------------------------------------------------------- history
def thin_series(
    points: Sequence[tuple[datetime, float, str | None]],
    *,
    since: datetime,
    until: datetime,
    limit: int = MAX_SERIES_POINTS,
) -> list[HistoryPoint]:
    """Fold a series longer than `limit` into equal time slices, keeping each slice's maximum.

    Time-based, so a gap stays a gap. A series within the limit is returned as it is.
    """
    if len(points) <= limit:
        return [HistoryPoint(at=at, value=value, mount=mount) for at, value, mount in points]
    span = max(1.0, (until - since).total_seconds())
    step = math.ceil(span / limit)
    slices: dict[int, list[tuple[datetime, float, str | None]]] = defaultdict(list)
    for item in points:
        slices[int((item[0] - since).total_seconds() // step)].append(item)
    out: list[HistoryPoint] = []
    for key in sorted(slices):
        group = slices[key]
        peak = max(group, key=lambda item: item[1])
        out.append(
            HistoryPoint(
                at=group[0][0], value=peak[1], mount=peak[2], windows=len(group)
            )
        )
    return out


def thin_reachability(
    points: Sequence[ReachabilityPoint], *, since: datetime, until: datetime
) -> list[ReachabilityPoint]:
    """Fold a long reachability series into equal time slices, keeping the worst of each.

    A slice that holds an unreachable sample keeps that one (an outage is never averaged
    away); otherwise it keeps the slowest answer.
    """
    if len(points) <= MAX_SERIES_POINTS:
        return list(points)
    step = math.ceil(max(1.0, (until - since).total_seconds()) / MAX_SERIES_POINTS)
    slices: dict[int, list[ReachabilityPoint]] = defaultdict(list)
    for point in points:
        slices[int((point.at - since).total_seconds() // step)].append(point)

    def worst(group: list[ReachabilityPoint]) -> ReachabilityPoint:
        down = [p for p in group if p.reachable is False]
        if down:
            return down[0]
        return max(group, key=lambda p: -1 if p.api_latency_ms is None else p.api_latency_ms)

    return [worst(slices[key]) for key in sorted(slices)]


def _series(points: list[HistoryPoint]) -> HistorySeries:
    if points:
        return HistorySeries(state=PartState.OK, points=points)
    return HistorySeries(
        state=PartState.NOT_COLLECTED,
        reason_code="metric_not_reported",
        reason=reason_for("metric_not_reported"),
    )


async def read_history(
    session: AsyncSession, settings: Settings, hours: int, *, now: datetime | None = None
) -> HistoryOut:
    """Bounded series per console and host over the last `hours` (24 or 168)."""
    if hours not in HISTORY_HOURS:
        raise ValueError("hours must be 24 or 168")
    until = now or datetime.now(UTC)
    since = until - timedelta(hours=hours)
    cadence = (
        await SearchCadenceService(session, settings).get(SearchFamily.QRADAR_HEALTH)
    ).cadence_seconds
    instances = list(
        (
            await session.scalars(
                select(QRadarInstance)
                .where(QRadarInstance.enabled.is_(True))
                .order_by(QRadarInstance.name, QRadarInstance.id)
            )
        ).all()
    )
    if not instances:
        return HistoryOut(
            generated_at=until,
            hours=hours,
            since=since,
            until=until,
            expected_interval_seconds=cadence,
            consoles=[],
        )
    ids = [instance.id for instance in instances]
    bucket = QRadarHealthMetricBucket
    in_range = (
        bucket.instance_id.in_(ids),
        bucket.bucket_start >= since,
        bucket.bucket_start < until,
    )

    fullest = (
        await session.execute(
            select(
                bucket.instance_id,
                bucket.host,
                bucket.bucket_start,
                bucket.element,
                bucket.max_value,
            )
            .where(bucket.metric == spec.METRIC_DISK_USAGE, *in_range)
            .ext(distinct_on(bucket.instance_id, bucket.host, bucket.bucket_start))
            .order_by(
                bucket.instance_id,
                bucket.host,
                bucket.bucket_start,
                bucket.max_value.desc(),
                bucket.element,
            )
        )
    ).all()
    pipeline = (
        await session.execute(
            select(
                bucket.instance_id,
                bucket.host,
                bucket.bucket_start,
                bucket.metric,
                bucket.max_value,
            )
            .where(
                bucket.metric.in_((spec.METRIC_CRE_QUEUE, spec.METRIC_THROTTLE_SPILL)),
                *in_range,
            )
            .order_by(bucket.instance_id, bucket.host, bucket.bucket_start)
        )
    ).all()
    rest_rows = (
        await session.execute(
            select(
                QRadarHealthRestSample.instance_id,
                QRadarHealthRestSample.slot_start,
                QRadarHealthRestSample.reachable,
                QRadarHealthRestSample.api_latency_ms,
            )
            .where(
                QRadarHealthRestSample.instance_id.in_(ids),
                QRadarHealthRestSample.slot_start >= since,
                QRadarHealthRestSample.slot_start < until,
            )
            .order_by(QRadarHealthRestSample.instance_id, QRadarHealthRestSample.slot_start)
        )
    ).all()

    disk: dict[tuple[UUID, str], list[tuple[datetime, float, str | None]]] = defaultdict(list)
    for instance_id, host, start, element, value in fullest:
        disk[(instance_id, host)].append((start, value, element))
    queue: dict[tuple[UUID, str], list[tuple[datetime, float, str | None]]] = defaultdict(list)
    spill: dict[tuple[UUID, str], list[tuple[datetime, float, str | None]]] = defaultdict(list)
    for instance_id, host, start, metric, value in pipeline:
        target = queue if metric == spec.METRIC_CRE_QUEUE else spill
        target[(instance_id, host)].append((start, value, None))
    reach: dict[UUID, list[ReachabilityPoint]] = defaultdict(list)
    for instance_id, start, reachable, latency in rest_rows:
        reach[instance_id].append(
            ReachabilityPoint(at=start, reachable=reachable, api_latency_ms=latency)
        )

    consoles: list[ConsoleHistory] = []
    for instance in instances:
        names = sorted(
            {host for (iid, host) in (*disk, *queue, *spill) if iid == instance.id}
        )
        hosts = [
            HostHistory(
                host=host,
                disk_fullest_fraction=_series(
                    thin_series(disk.get((instance.id, host), []), since=since, until=until)
                ),
                cre_queue=_series(
                    thin_series(queue.get((instance.id, host), []), since=since, until=until)
                ),
                throttle_spill_bytes=_series(
                    thin_series(spill.get((instance.id, host), []), since=since, until=until)
                ),
            )
            for host in names
        ]
        points = thin_reachability(reach.get(instance.id, []), since=since, until=until)
        reachability = (
            ReachabilitySeries(state=PartState.OK, points=points)
            if points
            else ReachabilitySeries(
                state=PartState.NOT_COLLECTED,
                reason_code="no_sample",
                reason=reason_for("no_sample"),
            )
        )
        metrics = (
            PartStatus(state=PartState.OK)
            if hosts
            else PartStatus(
                state=PartState.NOT_COLLECTED,
                reason_code="no_window_in_range",
                reason=reason_for("no_window_in_range"),
            )
        )
        consoles.append(
            ConsoleHistory(
                instance_id=instance.id,
                instance_name=instance.name,
                metrics=metrics,
                hosts=hosts,
                reachability=reachability,
            )
        )
    return HistoryOut(
        generated_at=until,
        hours=hours,
        since=since,
        until=until,
        expected_interval_seconds=cadence,
        consoles=consoles,
    )
