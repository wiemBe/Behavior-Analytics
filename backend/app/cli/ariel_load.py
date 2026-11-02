"""Print the Ariel load for an operator.

``--hours N`` summarises a window per caller. ``--current`` reads the load now:
searches in flight, the permit slots, the last 5, 15 and 60 minutes, and how QRadar
itself is doing (a short block per enabled console; this process asks QRadar afresh
every time, the API reuses its answers for a few seconds). Plain
table by default; ``--json`` prints the same document the admin route returns.
A window with no rows says so, and a part that cannot be read says so. Nothing
here is AQL or a secret.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from app.i18n.messages import MessageRef, render
from app.services.ariel_load_current import load_current_with_health
from app.services.ariel_search_log import (
    DEFAULT_HOURS,
    HOURS_CODE,
    empty_message,
    load_summary,
    parse_hours,
)
from app.services.qradar_health import ARIEL_RECENT_WINDOW_MINUTES

EXIT_OK = 0
EXIT_REFUSED = 2


def _cell(value: object) -> str:
    if value is None:
        return "null"
    return str(value)


def render_table(summary: dict[str, Any]) -> str:
    """The operator table. An empty window is the sentence, not a row of zeros."""
    if summary.get("empty"):
        message = summary.get("message") or empty_message()
        return (
            f"{message}\n"
            f"Window: {summary['start']} to {summary['end']} ({summary['hours']} h)."
        )
    headers = (
        "caller",
        "searches",
        "outcomes",
        "wall_p50_ms",
        "wall_p95_ms",
        "wall_max_ms",
        "wall_sum_ms",
        "qradar_p50_ms",
        "qradar_p95_ms",
        "qradar_sum_ms",
        "qradar_unknown",
        "records",
        "records_unknown",
        "data_bytes",
        "data_unknown",
    )
    lines = [
        f"Ariel load, last {summary['hours']} h ({summary['start']} to {summary['end']})",
        "\t".join(headers),
    ]
    for row in summary["callers"]:
        outcomes = ",".join(f"{name}:{count}" for name, count in row["outcomes"].items())
        lines.append(
            "\t".join(
                (
                    _cell(row["caller"]),
                    _cell(row["searches"]),
                    outcomes or "none",
                    _cell(row["wall_ms_p50"]),
                    _cell(row["wall_ms_p95"]),
                    _cell(row["wall_ms_max"]),
                    _cell(row["wall_ms_sum"]),
                    _cell(row["qradar_execution_ms_p50"]),
                    _cell(row["qradar_execution_ms_p95"]),
                    _cell(row["qradar_execution_ms_sum"]),
                    _cell(row["qradar_execution_ms_unknown"]),
                    _cell(row["records_returned_sum"]),
                    _cell(row["records_returned_unknown"]),
                    _cell(row["data_total_size_sum"]),
                    _cell(row["data_total_size_unknown"]),
                )
            )
        )
    lines.append("")
    lines.append(
        "percentiles are over COMPLETED searches; sums cover every finished search"
    )
    lines.append("")
    lines.append("hour\tsearches\tqradar_sum_ms\tqradar_unknown")
    for point in summary["series"]:
        lines.append(
            "\t".join(
                (
                    _cell(point["hour"]),
                    _cell(point["searches"]),
                    _cell(point["qradar_execution_ms_sum"]),
                    _cell(point["qradar_execution_ms_unknown"]),
                )
            )
        )
    return "\n".join(lines)


def _part_line(name: str, part: dict[str, Any]) -> str | None:
    """The sentence for a part that could not be read, else ``None``."""
    if part.get("available"):
        return None
    return f"{name}: not available ({part.get('reason', 'could not be read')})"


def _not_read(name: str, field: dict[str, Any]) -> str:
    return f"  {name}: not read ({field['state']}: {field.get('reason', 'no reason given')})"


def _percent(share: float | None) -> str:
    return "allocation is 0" if share is None else f"{share * 100:.1f} % of the allocation"


def _console_lines(entry: dict[str, Any]) -> list[str]:
    """One console's health as a short block. A field that was not read says why."""
    reachable = entry["reachable"]
    if reachable["state"] != "OK":
        head = f"not read ({reachable['state']}: {reachable.get('reason', 'no reason given')})"
    elif reachable["value"] is False:
        head = f"UNREACHABLE ({reachable.get('reason', 'no answer')})"
    else:
        latency = entry["api_latency_ms"]
        version = entry["version"]
        head = "reachable"
        head += (
            f", API {latency['value']} ms" if latency["state"] == "OK" else ", API latency not read"
        )
        head += f", QRadar {version['value']}" if version["state"] == "OK" else ""
    cached = f"reads {entry['age_seconds']:g} s old" if entry["cached"] else "read now"
    lines = [f"console {entry['instance_name']}: {head} ({cached}, as of {entry['as_of']})"]
    servers = entry["servers"]
    if servers["state"] != "OK":
        lines.append(_not_read("managed hosts", servers))
    else:
        v = servers["value"]
        text = f"{v['active']} of {v['total']} ACTIVE"
        if v["unreported"]:
            text += f", {v['unreported']} without a status"
        others = [f"{h['hostname'] or '?'} {h['status'] or 'no status'}" for h in v["not_active"]]
        lines.append(f"  managed hosts: {text}" + (f" ({', '.join(others)})" if others else ""))
    eps = entry["eps"]
    if eps["state"] != "OK":
        lines.append(_not_read("EPS", eps))
    else:
        v = eps["value"]
        lines.append(
            f"  EPS: average {v['average_eps']}, peak {v['peak_eps']}, allocated "
            f"{v['allocated_eps']} ({_percent(v['average_share'])})"
        )
    lic = entry["license"]
    if lic["state"] != "OK":
        lines.append(_not_read("license", lic))
    else:
        v = lic["value"]["eps"]
        over = "over-allocated" if v["overallocated"] else (
            "not over-allocated" if v["overallocated"] is False else "allocation not reported"
        )
        lines.append(f"  license EPS: total {v['total']}, allocated {v['allocated']} ({over})")
    ariel = entry["ariel"]
    held = ariel["list_probe"]
    if held["state"] != "OK":
        lines.append(_not_read("Ariel searches held by QRadar", held))
    else:
        v = held["value"]
        lines.append(
            f"  Ariel searches held by QRadar (all users, finished and active): "
            f"{'at least ' if v['truncated'] else ''}{v['held']}"
        )
    ours = ariel["ours_in_flight"]
    lines.append(
        _not_read("our searches in flight", ours)
        if ours["state"] != "OK"
        else f"  our searches in flight: {ours['value']}"
    )
    available = ariel["available"]
    recent = ariel["recent"]
    if available["state"] != "OK":
        lines.append(_not_read("Ariel answering our latest search", available))
    else:
        lines.append(
            "  Ariel answering our latest search: "
            + ("yes" if available["value"] else f"NO ({available.get('reason')})")
        )
    if recent["state"] != "OK":
        lines.append(_not_read(f"our searches, last {ARIEL_RECENT_WINDOW_MINUTES} min", recent))
    else:
        v = recent["value"]
        lines.append(
            f"  our searches, last {v['window_minutes']} min: {v['searches']}, "
            f"{v['unavailable']} found Ariel unavailable"
        )
    return lines


def _qradar_lines(current: dict[str, Any]) -> list[str]:
    qradar = current.get("qradar")
    if qradar is None:
        return []
    unreadable = _part_line("QRadar health", qradar)
    if unreadable is not None:
        return ["", unreadable]
    consoles = qradar["consoles"]
    if not consoles:
        return ["", "QRadar health: no enabled console is registered"]
    lines = ["", "QRadar health"]
    for entry in consoles:
        lines.extend(_console_lines(entry))
    return lines


def render_current_table(current: dict[str, Any]) -> str:
    """The operator table for the current load. An unreadable part is a sentence."""
    lines = [f"Ariel load as of {current['as_of']}"]
    flight = current["in_flight"]
    unreadable = _part_line("in flight", flight)
    if unreadable is not None:
        lines.append(unreadable)
    else:
        lines.append(
            f"in flight: {len(flight['active'])}"
            f" (stale, worker gone: {len(flight['stale'])}, older than"
            f" {flight['stale_after_seconds']} s)"
        )
        if flight["active"] or flight["stale"]:
            lines.append("state\tcaller\tinstance\tage_s\tpolls\trange_s")
        for state, entries in (("active", flight["active"]), ("stale", flight["stale"])):
            for entry in entries:
                lines.append(
                    "\t".join(
                        (
                            state,
                            _cell(entry["caller"]),
                            _cell(entry["instance_id"]),
                            _cell(entry["age_seconds"]),
                            _cell(entry["polls"]),
                            _cell(entry["range_seconds"]),
                        )
                    )
                )
    slots = current["slots"]
    unreadable = _part_line("slots", slots)
    if unreadable is not None:
        lines.append(unreadable)
    elif not slots["enforced"]:
        lines.append("slots: permits are not enforced")
    else:
        lines.append(
            f"slots: global {slots['global_held']}/{slots['global_cap']}, "
            f"console-pool locks {slots['console_pool_locks']} "
            f"(cap {slots['console_cap']} per console), "
            f"reserved {slots['reserved_slots']}"
        )
    windows = current["windows"]
    unreadable = _part_line("windows", windows)
    if unreadable is not None:
        lines.append(unreadable)
        return "\n".join([*lines, *_qradar_lines(current)])
    lines.append("")
    lines.append(
        "\t".join(
            (
                "window",
                "submitted",
                "completed",
                "failed",
                "refused",
                "in_flight",
                "wall_p50_ms",
                "wall_p95_ms",
                "wall_sum_ms",
                "qradar_p50_ms",
                "qradar_p95_ms",
                "qradar_sum_ms",
                "qradar_unknown",
                "records",
                "records_unknown",
            )
        )
    )
    for minutes in (5, 15, 60):
        window = windows[f"minutes_{minutes}"]
        lines.append(
            "\t".join(
                (
                    f"{minutes} min",
                    _cell(window["submitted"]),
                    _cell(window["completed"]),
                    _cell(window["failed"]),
                    _cell(window["refused"]),
                    _cell(window["in_flight"]),
                    _cell(window["wall_ms_p50"]),
                    _cell(window["wall_ms_p95"]),
                    _cell(window["wall_ms_sum"]),
                    _cell(window["qradar_execution_ms_p50"]),
                    _cell(window["qradar_execution_ms_p95"]),
                    _cell(window["qradar_execution_ms_sum"]),
                    _cell(window["qradar_execution_ms_unknown"]),
                    _cell(window["records_returned_sum"]),
                    _cell(window["records_returned_unknown"]),
                )
            )
        )
    lines.append("percentiles are over COMPLETED searches; sums cover every finished search")
    return "\n".join([*lines, *_qradar_lines(current)])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli.ariel_load")
    parser.add_argument("--hours", default=str(DEFAULT_HOURS))
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--current",
        action="store_true",
        help=(
            "the load now (in flight, permit slots, last 5/15/60 minutes) and QRadar's own "
            "health per console, read afresh by this process; ignores --hours"
        ),
    )
    return parser


async def _load(hours: int) -> dict[str, Any]:
    from app.core.database import dispose_engine, get_sessionmaker

    maker = get_sessionmaker()
    try:
        async with maker() as session:
            return await load_summary(session, hours=hours)
    finally:
        await dispose_engine()


async def _load_now() -> dict[str, Any]:
    from app.core.database import dispose_engine, get_sessionmaker

    maker = get_sessionmaker()
    try:
        async with maker() as session:
            return await load_current_with_health(session)
    finally:
        await dispose_engine()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.current:
        try:
            current = asyncio.run(_load_now())
        except Exception:
            print("ariel load could not be read", file=sys.stderr)
            return EXIT_REFUSED
        print(json.dumps(current) if args.json else render_current_table(current))
        return EXIT_OK
    try:
        hours = parse_hours(str(args.hours))
    except ValueError:
        print(render(MessageRef(HOURS_CODE)), file=sys.stderr)
        return EXIT_REFUSED
    try:
        summary = asyncio.run(_load(hours))
    except Exception:
        print("ariel load summary could not be read", file=sys.stderr)
        return EXIT_REFUSED
    if args.json:
        print(json.dumps(summary))
    else:
        print(render_table(summary))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
