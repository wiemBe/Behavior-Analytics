"""The two `doctor` lines of the stored QRadar health (D-42): `qradar_disk`, `qradar_pipeline`.

Pure functions over `LatestOut` (the latest stored window of every enabled console). They return
a status word and a message; `Doctor` wraps them. Nothing here asks QRadar.

A missing, failed, partial or stale window is a WARN that says since when nothing was collected.
It is never a PASS: a disk that was not read is not a disk that is fine (rule 8).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import Settings
from app.schemas.qradar_health_detail import (
    ConsoleLatest,
    LatestOut,
    MetricCell,
    PartState,
)

_UNREAD_STATES = (PartState.NOT_COLLECTED, PartState.FAILED, PartState.STALE, PartState.PARTIAL)


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _bytes(value: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    number = float(value)
    for unit in units[:-1]:
        if abs(number) < 1024:
            return f"{number:.0f} {unit}" if unit == "B" else f"{number:.1f} {unit}"
        number /= 1024
    return f"{number:.1f} {units[-1]}"


def _not_collected(console: ConsoleLatest) -> str | None:
    """The reason a console's metrics cannot be judged, or None when the window is usable."""
    metrics = console.metrics
    if metrics.state is PartState.OK:
        return None
    since = (
        f"not collected since {_utc(console.last_metrics_window_end)}"
        if console.last_metrics_window_end is not None
        else "not collected yet"
    )
    detail = {
        PartState.STALE: "the newest window is older than the cadence allows",
        PartState.PARTIAL: "the newest window is partial (a search hit its row cap)",
    }.get(metrics.state, metrics.reason or metrics.state.value)
    return f"{console.instance_name}: {since} ({detail})"


def disk_line(latest: LatestOut, settings: Settings) -> tuple[str, str]:
    """`qradar_disk`: FAIL at the fail share, WARN at the warn share, naming host and mount."""
    if not latest.consoles:
        return "SKIP", "no enabled QRadar instance is registered; see qradar_auth"
    fails: list[str] = []
    warns: list[str] = []
    unread: list[str] = []
    facts: list[str] = []
    for console in latest.consoles:
        problem = _not_collected(console)
        if problem is not None:
            unread.append(problem)
            if console.metrics.state is not PartState.PARTIAL:
                continue
        for host in console.hosts:
            fullest = host.disk.fullest
            if fullest is None or fullest.used_fraction is None:
                unread.append(f"{console.instance_name}: {host.host} reported no allowlisted mount")
                continue
            text = f"{host.host} {fullest.mount} {fullest.used_percent:.1f} %"
            if fullest.free_bytes is not None:
                text += f" ({_bytes(fullest.free_bytes)} free)"
            if fullest.level == "FAIL":
                fails.append(f"{text} (at or over {settings.qradar_health_disk_fail_percent} %)")
            elif fullest.level == "WARN":
                warns.append(f"{text} (at or over {settings.qradar_health_disk_warn_percent} %)")
            else:
                facts.append(text)
    parts = [*fails, *warns, *unread]
    if fails:
        return "FAIL", "; ".join(parts + ([f"fullest mounts: {', '.join(facts)}"] if facts else []))
    if warns or unread:
        return "WARN", "; ".join(parts + ([f"fullest mounts: {', '.join(facts)}"] if facts else []))
    return "PASS", "fullest mount per host: " + ", ".join(facts)


def _cell_value(cell: MetricCell) -> float | None:
    return cell.value if cell.state in (PartState.OK, PartState.STALE, PartState.PARTIAL) else None


def pipeline_line(latest: LatestOut, settings: Settings) -> tuple[str, str]:
    """`qradar_pipeline`: WARN on a throttle spill above zero or a rule-engine queue too long."""
    if not latest.consoles:
        return "SKIP", "no enabled QRadar instance is registered; see qradar_auth"
    warns: list[str] = []
    unread: list[str] = []
    facts: list[str] = []
    for console in latest.consoles:
        problem = _not_collected(console)
        if problem is not None:
            unread.append(problem)
            if console.metrics.state is not PartState.PARTIAL:
                continue
        for host in console.hosts:
            spill = _cell_value(host.pipeline.throttle_spill_bytes)
            queue = _cell_value(host.pipeline.cre_queue)
            if spill is None:
                unread.append(f"{console.instance_name}: {host.host} throttle spill not read")
            elif spill > 0:
                warns.append(
                    f"{host.host} spilled {_bytes(spill)} to disk (events over the license EPS)"
                )
            if queue is None:
                unread.append(f"{console.instance_name}: {host.host} CRE queue not read")
            elif queue > settings.qradar_health_cre_queue_warn:
                warns.append(
                    f"{host.host} CRE queue {queue:.0f} events "
                    f"(over {settings.qradar_health_cre_queue_warn})"
                )
            elif queue is not None and spill is not None and spill == 0:
                facts.append(f"{host.host} CRE queue {queue:.0f}, no throttle spill")
    parts = [*warns, *unread]
    if warns or unread:
        return "WARN", "; ".join(parts + ([", ".join(facts)] if facts else []))
    return "PASS", "; ".join(facts)
