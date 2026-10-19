"""Record the operator's answer to "read QRadar's past?" (HISTORY-BACKFILL §B.9.4).

    python -m app.cli.history_backfill decide --instance qradarce2 --answer yes \
        --source installer

The installer runs this once, right after `qradar add`. The answer is write-once: a
decision that already exists is never changed here (a change is made by an authorised
person in the admin UI, and is audited). The command makes no QRadar call and does not
depend on `HISTORY_BACKFILL_ENABLED`: that switch governs reading, not the answer.

Exit codes:
  0  the answer was recorded, or a decision already existed (it is left as it is)
  1  the database failed; nothing was recorded
  2  the console is not registered, or the arguments are invalid; nothing was recorded
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.models.enums import HistoryDecisionAnswer, HistoryDecisionSource
from app.models.history_backfill import HistoryBackfillDecision
from app.repositories.audit import record_audit
from app.repositories.history_backfill import HistoryBackfillRepository
from app.services.instance_registry import InstanceNotFound, InstanceRegistry

DECIDE_ACTION = "history_backfill.decide"
INSTALLER_ACTOR = "installer"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2


async def _existing_decision(
    session: AsyncSession, instance_id: uuid.UUID
) -> HistoryBackfillDecision | None:
    return (await HistoryBackfillRepository(session).decisions([instance_id])).get(instance_id)


def _already_decided(existing: HistoryBackfillDecision) -> str:
    when = existing.decided_at.astimezone(UTC).date().isoformat()
    return f"already decided on {when}: {existing.answer}"


async def _decide(args: argparse.Namespace) -> int:
    answer = HistoryDecisionAnswer(args.answer.upper())
    source = HistoryDecisionSource(args.source.upper())
    days = get_settings().history_backfill_days if answer is HistoryDecisionAnswer.YES else None

    maker = get_sessionmaker()
    async with maker() as session:
        # An unknown console is refused before anything is written; there is no global
        # decision.
        instance = await InstanceRegistry(session).get_by_name(args.instance)
        # Plain values: a rollback below expires the ORM object.
        name, instance_id = instance.name, instance.id

        existing = await _existing_decision(session, instance_id)
        if existing is not None:
            print(f"{name}: {_already_decided(existing)}")
            return EXIT_OK

        # ON CONFLICT DO NOTHING inside the repository: two concurrent runs cannot both
        # write, and a row that appears between the read above and the insert is never
        # overwritten.
        _, inserted = await HistoryBackfillRepository(session).record_decision(
            instance_id,
            answer=answer.value,
            days=days,
            source=source.value,
            decided_by=INSTALLER_ACTOR,
            at=datetime.now(UTC),
        )
        if not inserted:
            await session.rollback()
            current = await _existing_decision(session, instance_id)
            if current is None:  # pragma: no cover - deleted between the two statements
                raise RuntimeError("the decision vanished while it was being recorded")
            print(f"{name}: {_already_decided(current)}")
            return EXIT_OK

        await record_audit(
            session,
            action=DECIDE_ACTION,
            actor=INSTALLER_ACTOR,
            object_type="qradar_instance",
            object_id=str(instance_id),
            detail={"answer": answer.value, "days": days, "source": source.value},
        )
        await session.commit()

    window = f"{days} days" if days is not None else "no history"
    print(f"{name}: recorded {answer.value} ({window}) from the {source.value.lower()}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.history_backfill",
        description="Record the operator's answer to reading QRadar's past.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    decide = sub.add_parser(
        "decide",
        help="record the answer for a console (write-once)",
        description=(
            "Record the operator's answer for one console. A decision that already exists "
            "is never changed."
        ),
        epilog=(
            "Exit codes:\n"
            "  0  recorded, or a decision already existed and was left as it is\n"
            "  1  the database failed; nothing was recorded\n"
            "  2  the console is not registered, or the arguments are invalid"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    decide.add_argument("--instance", required=True, help="name of a registered console")
    decide.add_argument("--answer", required=True, choices=["yes", "no"])
    decide.add_argument(
        "--source",
        required=True,
        choices=["installer"],
        help="only 'installer'; decisions from the UI come through the API",
    )
    decide.set_defaults(func=_decide)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(args.func(args))
    except (InstanceNotFound, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except Exception as exc:
        print(f"error: the decision was not recorded ({type(exc).__name__})", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
