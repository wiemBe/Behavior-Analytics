"""Transfer operator configuration as secret-free JSON (CFG-1, D-78).

    python -m app.cli.config export [--label LABEL] [--instance NAME] [--actor ACTOR]
    python -m app.cli.config import (--dry-run | --apply --yes) [--instance NAME] [--actor ACTOR]

Reads only this product's database: no provider call, no secret column. The document is all of
stdout; the ``config.export`` audit row commits in one transaction, and stdout is printed only
after it. Exit codes: 0 written; 1 the database failed, nothing committed; 2 the arguments, the
label or the console preflight were refused, nothing written.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.config_transfer.document import (
    MAX_DOCUMENT_BYTES,
    DocumentError,
    check_label,
    dumps,
    parse,
)
from app.config_transfer.export_bundle import export_document
from app.config_transfer.import_bundle import plan
from app.config_transfer.report import ImportReport
from app.core.database import dispose_engine, get_sessionmaker
from app.models.instance import QRadarInstance
from app.repositories.audit import record_audit

logger = logging.getLogger("app.cli.config")

EXPORT_ACTION = "config.export"
NO_CONSOLE = "no console is registered; run qradar add and sync log sources first"
MANY_CONSOLES = "export resolves names on one console; {count} are registered"
NO_SUCH_CONSOLE = "no console is registered under the name {name!r}"
NAME_IS_NOT_LABEL = "instance name is not a legal source label; pass --label"
EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2


class PreflightError(ValueError):
    """Export cannot pick one console, or the name it would use as a label is illegal."""


async def _target(session: AsyncSession, name: str | None) -> QRadarInstance:
    """The one console export reads: the named row, or the only registered one."""
    rows = list(await session.scalars(select(QRadarInstance).options(
        load_only(QRadarInstance.id, QRadarInstance.name))))
    if not rows:
        raise PreflightError(NO_CONSOLE)
    if name is not None:
        rows = [row for row in rows if row.name == name]
        if not rows:
            raise PreflightError(NO_SUCH_CONSOLE.format(name=name))
    elif len(rows) > 1:
        raise PreflightError(MANY_CONSOLES.format(count=len(rows)))
    return rows[0]


def _name_label(instance: QRadarInstance) -> str:
    """The console's name as the label; a name that is not a label is refused, not fixed."""
    try:
        return check_label(instance.name)
    except DocumentError:
        raise PreflightError(NAME_IS_NOT_LABEL) from None


async def _export(args: argparse.Namespace) -> int:
    # A bad argument is refused before the database is touched, as the exit table promises.
    requested = check_label(args.label) if args.label is not None else None
    maker = get_sessionmaker()
    try:
        async with maker() as session:
            instance = await _target(session, args.instance)
            label = requested if requested is not None else _name_label(instance)
            document = await export_document(
                session, instance, source_label=label, exported_at=datetime.now(UTC)
            )
            # Serialized before the audit row: a secret-shaped key must refuse the whole run.
            rendered = dumps(document)
            counts = {name: len(rows) for name, rows in document["sections"].items()}
            await record_audit(
                session, action=EXPORT_ACTION, actor=args.actor, object_type="config_bundle",
                object_id=label,
                # Not "sections": redaction masks any audit detail key containing "sec".
                detail={"schema_version": document["schema_version"], "source_label": label,
                        "counts": counts},
            )
            await session.commit()
    finally:
        await dispose_engine()
    # Printed only after commit, as `users.py` prints the one-time password.
    print(rendered, end="")
    logger.info("%s %s %s", EXPORT_ACTION, label, counts)
    return EXIT_OK


async def _import(args: argparse.Namespace) -> int:
    document = parse(sys.stdin.buffer.read(MAX_DOCUMENT_BYTES + 1))
    maker = get_sessionmaker()
    report = ImportReport("apply" if args.apply else "dry-run")
    action = "config.import.apply" if args.apply else "config.import.dry_run"

    async def audit(outcome: str = "SUCCESS") -> None:
        try:
            async with maker() as audit_session:
                await record_audit(audit_session, action=action, actor=args.actor,
                                   object_type="config_bundle", object_id=report.instance,
                                   outcome=outcome, detail=report.counts())
                await audit_session.commit()
        except Exception as exc:
            logger.error("%s audit failed: %s", action, type(exc).__name__)

    try:
        async with maker() as session:
            try:
                instance = await _target(session, args.instance)
            except PreflightError as exc:
                message = str(exc).replace("export resolves", "import resolves")
                raise PreflightError(message) from None
            report.instance = instance.name
            try:
                await plan(session, document, instance, report, args.actor)
                if args.apply:
                    await record_audit(session, action=action, actor=args.actor,
                                       object_type="config_bundle", object_id=instance.name,
                                       detail=report.counts() | {"instance": instance.name})
                    await session.commit()
                    report.committed = True
                else:
                    await session.rollback()
            except Exception:
                await session.rollback()
                raise
        if not args.apply:
            await audit()
    except PreflightError:
        raise
    except Exception as exc:
        await audit("FAILURE")
        logger.error("%s failed: %s", action, type(exc).__name__)
        print(report.render(), end="")
        return EXIT_FAILED
    finally:
        await dispose_engine()
    print(report.render(), end="")
    logger.info("%s %s %s", action, report.instance, report.counts())
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli.config", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="print the configuration document")
    export.add_argument("--label", default=None, help="source label recorded in the file")
    export.add_argument("--instance", default=None, help="name of one registered console")
    export.add_argument("--actor", default=getpass.getuser())
    importer = sub.add_parser("import", help="merge configuration from stdin")
    mode = importer.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    importer.add_argument("--yes", action="store_true")
    importer.add_argument("--instance", default=None)
    importer.add_argument("--actor", default=getpass.getuser())
    args = parser.parse_args(argv)
    if args.command == "import" and args.apply and not args.yes:
        parser.error("--apply requires --yes")
    try:
        return asyncio.run(_export(args) if args.command == "export" else _import(args))
    except (PreflightError, DocumentError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except Exception as exc:
        # The class only: a message can carry a DSN or a URL.
        logger.error("config.%s failed: %s", args.command, type(exc).__name__)
        print(f"error: the {args.command} was not written ({type(exc).__name__})", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
