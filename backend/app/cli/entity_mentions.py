"""Operator commands for the entity-mention backfill (SOC-16f4, D-64).

    python -m app.cli.entity_mentions status [--instance NAME] [--json]
    python -m app.cli.entity_mentions run [--instance NAME] [--max-batches N]

The backfill also runs by itself from a beat task; this command exists for a manual run and
to see where each (console, source) stands. It reads only stored aggregates, makes no QRadar
call, and never deletes a mention. `run` ignores `ENTITY_MENTION_BACKFILL_ENABLED` (that
switch governs the beat task) but holds the same advisory lock as the task.

Exit codes:
  0  done, or nothing to do (`run`); the report was printed (`status`)
  1  the database failed
  2  the console is not registered, or the arguments are invalid
  3  another backfill run holds the lock; nothing was done
  4  at least one source FAILED in this run (`run` only); the next run retries it
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_sessionmaker
from app.models.entity import EntityMentionBackfill
from app.models.instance import QRadarInstance
from app.services.entity_mention_backfill import EntityMentionBackfillService
from app.services.instance_registry import InstanceNotFound, InstanceRegistry

EXIT_OK = 0
EXIT_DB_FAILED = 1
EXIT_BAD_ARGUMENTS = 2
EXIT_BUSY = 3
EXIT_FAILED_SOURCE = 4

_EPILOG = (
    "Exit codes:\n"
    "  0  done or nothing to do\n"
    "  1  the database failed\n"
    "  2  the console is not registered, or the arguments are invalid\n"
    "  3  another backfill run holds the lock; nothing was done\n"
    "  4  at least one source FAILED in this run (run only)"
)


def _utc(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ") if value else "-"


async def _check_instance(session: AsyncSession, name: str | None) -> None:
    if name is not None:
        await InstanceRegistry(session).get_by_name(name)


async def _status(args: argparse.Namespace) -> int:
    async with get_sessionmaker()() as session:
        await _check_instance(session, args.instance)
        query = (
            select(
                QRadarInstance.name,
                EntityMentionBackfill.source,
                EntityMentionBackfill.status,
                EntityMentionBackfill.rows_written,
                EntityMentionBackfill.started_at,
                EntityMentionBackfill.completed_at,
                EntityMentionBackfill.last_error,
            )
            .join(QRadarInstance, QRadarInstance.id == EntityMentionBackfill.instance_id)
            .order_by(QRadarInstance.name, EntityMentionBackfill.source)
        )
        if args.instance is not None:
            query = query.where(QRadarInstance.name == args.instance)
        rows = (await session.execute(query)).all()
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "instance": r[0],
                        "source": r[1],
                        "status": r[2],
                        "rows_written": r[3],
                        "started_at": r[4].isoformat() if r[4] else None,
                        "completed_at": r[5].isoformat() if r[5] else None,
                        "last_error": r[6],
                    }
                    for r in rows
                ]
            )
        )
        return EXIT_OK
    if not rows:
        print("the entity-mention backfill has not started: no state rows exist yet")
        return EXIT_OK
    for name, source, status, written, started, completed, error in rows:
        print(
            f"{name} {source} {status} rows_written={written} "
            f"started={_utc(started)} completed={_utc(completed)} last_error={error or '-'}"
        )
    return EXIT_OK


async def _run(args: argparse.Namespace) -> int:
    async with get_sessionmaker()() as session:
        await _check_instance(session, args.instance)
        report = await EntityMentionBackfillService(session).run(
            instance_name=args.instance,
            max_batches=args.max_batches,
            unbounded=args.max_batches is None,
        )
    if report.busy:
        print("another entity-mention backfill run holds the lock; nothing was done")
        return EXIT_BUSY
    if not report.instances:
        print("no enabled console to backfill")
        return EXIT_OK
    for name, sources in report.instances.items():
        for source, result in sources.items():
            print(
                f"{name} {source} {result.status} batches={result.batches} "
                f"rows_written={result.rows_written}"
            )
    return EXIT_FAILED_SOURCE if report.failed else EXIT_OK


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.entity_mentions",
        description="Inspect and run the entity-mention backfill.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser(
        "status",
        help="show the state of each (console, source)",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    status.add_argument("--instance", help="name of a registered console")
    status.add_argument("--json", action="store_true", help="print JSON instead of lines")
    status.set_defaults(func=_status)

    run = sub.add_parser(
        "run",
        help="run the backfill now, under the task's lock",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    run.add_argument("--instance", help="name of a registered console (default: all enabled)")
    run.add_argument(
        "--max-batches",
        type=_positive,
        help="stop after this many batches (default: until every source is COMPLETE)",
    )
    run.set_defaults(func=_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_BAD_ARGUMENTS if exc.code not in (0, None) else EXIT_OK
    try:
        return asyncio.run(args.func(args))
    except InstanceNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_BAD_ARGUMENTS
    except Exception as exc:
        print(f"error: the database failed ({type(exc).__name__})", file=sys.stderr)
        return EXIT_DB_FAILED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
