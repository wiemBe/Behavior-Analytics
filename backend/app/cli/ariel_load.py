"""Print the Ariel load for an operator.

``--hours N`` summarises a window per caller. ``--current`` reads the load now:
searches in flight, the permit slots, and the last 5, 15 and 60 minutes. Plain
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
from app.services.ariel_search_log import (
    DEFAULT_HOURS,
    HOURS_CODE,
    empty_message,
    load_current,
    load_summary,
    parse_hours,
)

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
        return "\n".join(lines)
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
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli.ariel_load")
    parser.add_argument("--hours", default=str(DEFAULT_HOURS))
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--current",
        action="store_true",
        help="the load now (in flight, permit slots, last 5/15/60 minutes); ignores --hours",
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
            return await load_current(session)
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
