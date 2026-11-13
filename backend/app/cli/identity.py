"""Administer identity source profiles.

    python -m app.cli.identity set --instance lab --log-source 1003 \\
        --event-id-property EventID --username-property username
    python -m app.cli.identity validate --instance lab --log-source 1003
    python -m app.cli.identity enable --instance lab --log-source 1003

``set`` states the mapping. A field that fails the reference check is refused
and its text is not printed. ``validate`` runs a bounded read-only probe.
``enable`` collects only after this profile version is VALID. Nothing here
writes to QRadar.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_sessionmaker
from app.identity.profiles import IdentityProfileError, IdentityProfileService
from app.identity.reference import IdentityReferenceError, validate_event_ids
from app.models.enums import IDENTITY_EVENT_CATALOG, IdentityProfileValidationStatus
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.providers.base import ProviderError
from app.providers.factory import build_provider_for_instance


def _event_ids(value: str | None) -> list[int]:
    """Catalog ids. A bad token raises a fixed message that does not quote it."""
    if value is None or not value.strip():
        return [event_id for event_id, _kind in IDENTITY_EVENT_CATALOG]
    numbers: list[int] = []
    for part in value.split(","):
        token = part.strip()
        if not token or not token.isascii() or not token.isdigit():
            raise IdentityReferenceError("event ids must be catalog integers")
        numbers.append(int(token))
    return list(validate_event_ids(numbers))


async def _resolve(
    session: AsyncSession, instance_name: str, qradar_id: int
) -> tuple[QRadarInstance, LogSource]:
    instance = await session.scalar(
        select(QRadarInstance).where(QRadarInstance.name == instance_name)
    )
    if instance is None:
        raise IdentityProfileError("no QRadar instance with that name")
    log_source = await session.scalar(
        select(LogSource).where(
            LogSource.instance_id == instance.id, LogSource.qradar_id == qradar_id
        )
    )
    if log_source is None:
        raise IdentityProfileError("that log source is not in the inventory")
    return instance, log_source


async def _run(args: argparse.Namespace) -> int:
    maker = get_sessionmaker()
    async with maker() as session:
        service = IdentityProfileService(session)
        instance, log_source = await _resolve(session, args.instance, args.log_source)
        if args.command == "set":
            profile, changed = await service.set_mapping(
                log_source=log_source,
                event_id_property=args.event_id_property,
                username_property=args.username_property,
                logon_type_property=args.logon_type_property,
                enabled_event_ids=_event_ids(args.event_ids),
                actor=args.actor,
                product_label=args.product_label,
            )
            await session.commit()
            print(
                f"profile v{profile.profile_version} "
                f"{'updated' if changed else 'unchanged'}; "
                f"validation {profile.validation_status}"
            )
            return 0

        existing = await service.for_log_source(log_source)
        if existing is None:
            raise IdentityProfileError("no identity profile exists for this log source")
        if args.command == "validate":
            provider = build_provider_for_instance(instance)
            try:
                result = await service.validate(existing, provider, actor=args.actor)
            finally:
                await provider.aclose()
            await session.commit()
            if args.json:
                print(json.dumps(result.report, indent=2))
            else:
                print(f"validation: {result.status.value}")
            if result.status is IdentityProfileValidationStatus.VALID:
                return 0
            return 2
        changed = await service.enable(existing, actor=args.actor)
        await session.commit()
        print("profile enabled" if changed else "profile already enabled")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli.identity", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def source_args(command: argparse.ArgumentParser) -> None:
        command.add_argument("--instance", required=True)
        command.add_argument("--log-source", type=int, required=True)
        command.add_argument("--actor", default=getpass.getuser())

    set_parser = sub.add_parser("set", help="create or change a profile's field mapping")
    source_args(set_parser)
    set_parser.add_argument("--event-id-property", required=True)
    set_parser.add_argument("--username-property", required=True)
    set_parser.add_argument("--logon-type-property", default=None)
    set_parser.add_argument("--event-ids", default=None)
    set_parser.add_argument("--product-label", default=None)

    validate_parser = sub.add_parser("validate")
    source_args(validate_parser)
    validate_parser.add_argument("--json", action="store_true")

    enable_parser = sub.add_parser("enable")
    source_args(enable_parser)

    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except (IdentityProfileError, IdentityReferenceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ProviderError as exc:
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
