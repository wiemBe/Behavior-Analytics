"""Offline feature control. Exit 0: success, 1: database failure, 2: refused input."""
import argparse
import asyncio
import sys
from dataclasses import asdict

from app.core.config import get_settings, switch_dependent_problems
from app.core.database import get_sessionmaker
from app.services.feature_management import FeatureError, change
from app.services.feature_switches import effective_settings, resolve


async def run(args: argparse.Namespace) -> int:
    changes: dict[str, bool | None] = {}
    if args.command == "set":
        changes[args.key] = args.value == "on"
    elif args.command == "reset":
        changes[args.key] = None
    elif args.command == "apply":
        for word in args.values:
            key, sep, value = word.partition("=")
            if not sep or value not in ("on", "off") or key in changes:
                raise FeatureError(422, "feature_switch_invalid_assignment")
            changes[key] = value == "on"
    async with get_sessionmaker()() as session:
        base = get_settings()
        if changes:
            await change(session, base, changes, actor=args.source)
            await session.commit()
        else:
            resolved = await resolve(session, base)
            if resolved.reason:
                raise FeatureError(503, resolved.reason)
            for key, value in resolved.values.items():
                if args.command == "env":
                    print(f"{key.upper()}={str(value.enabled).lower()}")
                else:
                    print(f"{key}={'on' if value.enabled else 'off'} {asdict(value)}")
            if args.command == "show":
                for problem in switch_dependent_problems(effective_settings(base, resolved)):
                    print(problem)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("show", "env", "set", "reset", "apply"):
        command = sub.add_parser(name)
        if name in ("set", "reset"):
            command.add_argument("key")
        if name == "set":
            command.add_argument("value", choices=("on", "off"))
        if name == "apply":
            command.add_argument("values", nargs="+")
        command.add_argument("--source", choices=("cli", "installer"), default="cli")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except FeatureError as exc:
        print(f"{exc.code}: {'; '.join(exc.problems)}", file=sys.stderr)
        return 1 if exc.status == 503 else 2
    except Exception as exc:
        print(f"Feature command failed ({type(exc).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
