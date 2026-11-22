"""Offline inventory absence control. Exit 0: success, 1: failure, 2: refused input."""

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from app.collectors.log_source_collector import (
    ABSENCE_ACCEPT_TTL,
    COLLECTOR_NAME,
    absence_accept_armed_at,
    stored_run,
)
from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.log_source_policy import source_pause_enabled
from app.models.instance import QRadarInstance
from app.models.monitoring import CollectionWatermark
from app.repositories.audit import record_audit
from app.services.feature_switches import effective_settings, resolve


class InventoryRefused(Exception):
    pass


async def run(args: argparse.Namespace) -> int:
    async with get_sessionmaker()() as session:
        base = get_settings()
        resolved = await resolve(session, base)
        if resolved.reason:
            raise RuntimeError("feature switch unreadable")
        enabled = source_pause_enabled(effective_settings(base, resolved))
        if args.command == "accept" and not enabled:
            raise InventoryRefused("Absence accept refused: QRadar source pauses are off.")
        instance = await session.scalar(select(QRadarInstance).where(
            QRadarInstance.name == args.instance,
        ))
        if instance is None:
            raise InventoryRefused("Inventory command refused: instance not found.")
        statement = select(CollectionWatermark).where(
            CollectionWatermark.instance_id == instance.id,
            CollectionWatermark.collector == COLLECTOR_NAME,
        )
        if args.command == "accept":
            statement = statement.with_for_update().execution_options(populate_existing=True)
        mark = await session.scalar(statement)
        metadata = (mark.collection_metadata or {}) if mark is not None else {}
        absence = (stored_run(mark) or {}).get("absence", {})
        if not isinstance(absence, dict):
            absence = {}
        if args.command == "accept":
            if mark is None or absence.get("state") != "held":
                raise InventoryRefused("Absence accept refused: last inventory run is not held.")
            count = absence.get("held_count")
            if type(count) is not int or count <= 0:
                raise InventoryRefused("Absence accept refused: held count is unavailable.")
            if isinstance(metadata.get("absence_accept"), dict):
                print("Absence accept is already armed; completeness and two passes still apply.")
                return 0
            actor = args.actor
            mark.collection_metadata = {
                **metadata,
                # Start a fresh two-pass observation after accepting either breaker stage.
                "absence_candidates": [],
                "absence_accept": {"armed_at": datetime.now(UTC).isoformat(),
                                   "actor": actor, "held_count": count},
            }
            await record_audit(
                session, action="inventory.absence.accept", actor=actor,
                object_type="qradar_instance", object_id=str(instance.id),
                detail={"held_count": count},
            )
            await session.commit()
            print(f"Absence accept armed for {count} held sources. The breaker is lifted until "
                  "a complete validated pass confirms absence or finds nothing missing, or until "
                  "the accept expires 24 hours after arming; two-pass evidence is required.")
        else:
            def count(key: str) -> int:
                value = absence.get(key)
                return value if type(value) is int and value >= 0 else 0

            state = absence.get("state")
            state = state if state in ("evaluated", "held", "skipped") else "not_collected"
            armed = isinstance(metadata.get("absence_accept"), dict)
            print(f"Absence state: {state}; candidates: {count('candidates')}; "
                  f"confirmed: {count('confirmed')}; held: {count('held_count')}; "
                  f"accept armed: {'yes' if armed else 'no'}; pauses: {'on' if enabled else 'off'}")
            if armed:
                armed_at = absence_accept_armed_at(metadata["absence_accept"])
                if armed_at is None:
                    print("Accept age: unavailable; expires at: unavailable (arm time invalid).")
                else:
                    age = max(0, int((datetime.now(UTC) - armed_at).total_seconds()))
                    expires = armed_at + ABSENCE_ACCEPT_TTL
                    print(f"Accept age: {age} seconds; expires at: {expires.isoformat()}; "
                          "cleared on the next complete validated nonempty pass.")
    return 0


async def _async_main(args: argparse.Namespace) -> int:
    try:
        return await run(args)
    except InventoryRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Inventory command failed ({type(exc).__name__})", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    families = parser.add_subparsers(dest="family", required=True)
    absence = families.add_parser("absence")
    commands = absence.add_subparsers(dest="command", required=True)
    for name in ("show", "accept"):
        command = commands.add_parser(name)
        command.add_argument("--instance", required=True)
        if name == "accept":
            command.add_argument("--actor", default="cli")
    return asyncio.run(_async_main(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
