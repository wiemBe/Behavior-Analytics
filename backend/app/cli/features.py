"""Offline feature control. Exit 0: success, 1: database failure, 2: refused input."""
import argparse
import asyncio
import sys
from dataclasses import asdict

from app.core.config import Settings, get_settings, switch_dependent_problems
from app.core.database import get_sessionmaker
from app.services.feature_management import FeatureError, change
from app.services.feature_switches import (
    FEATURE_KEYS,
    ResolvedFeatures,
    effective_settings,
    resolve,
)

# Installer words map to collection keys, then the family's alerting key.
FAMILIES = {
    "waf": (("waf_collection_enabled", "waf_outcome_collection_enabled",
             "waf_client_collection_enabled"), "waf_alerting_enabled"),
    "ips": (("ips_collection_enabled",), "ips_alerting_enabled"),
    "rule-match": (("rule_match_collection_enabled",), "rule_alerting_enabled"),
    "property-watch": (("pw_collection_enabled",), "pw_alerting_enabled"),
    "identity": (("identity_collection_enabled",), "identity_alerting_enabled"),
    "custom-rules": (("custom_rules_enabled",), "custom_rules_alerting_enabled"),
}


def family_words(value: str) -> set[str]:
    words = value.replace(",", " ").split()
    if (not words or set(words) - (set(FAMILIES) | {"defaults", "none"})
            or (set(words) & {"defaults", "none"} and len(words) != 1)):
        raise FeatureError(422, "feature_switch_invalid_families")
    return set(words)


def family_changes(
    words: set[str], alerting: str, current: ResolvedFeatures,
) -> dict[str, bool]:
    changes = {}
    for name, (keys, alert) in FAMILIES.items():
        enabled = current.values[keys[0]].enabled if "defaults" in words else name in words
        if "defaults" not in words:
            changes.update(dict.fromkeys(keys, enabled))
        if not enabled and "defaults" not in words:
            changes[alert] = False
        elif enabled and alerting != "defaults":
            changes[alert] = alerting == "alerting"
    return changes


async def run(args: argparse.Namespace) -> int:
    changes: dict[str, bool | None] = {}
    families = getattr(args, "families", None)
    words = family_words(families) if families is not None else None
    if args.command == "defaults":
        enabled = [name for name, (keys, _) in FAMILIES.items()
                   if Settings.model_fields[keys[0]].default]
        print("Families on by code default: " + ", ".join(enabled))
        return 0
    if args.command == "set":
        changes[args.key] = args.value == "on"
    elif args.command == "reset":
        changes[args.key] = None
    elif args.command == "apply":
        if (families is not None and args.values) or (families is None and not args.values):
            raise FeatureError(422, "feature_switch_invalid_assignment")
        if families is None and getattr(args, "alerting", "defaults") != "defaults":
            raise FeatureError(422, "feature_switch_invalid_assignment")
        if words == {"defaults"} and args.alerting == "defaults":
            return 0
        for word in args.values:
            key, sep, value = word.partition("=")
            if not sep or value not in ("on", "off") or key in changes:
                raise FeatureError(422, "feature_switch_invalid_assignment")
            changes[key] = value == "on"
    if set(changes) - set(FEATURE_KEYS):
        raise FeatureError(404, "feature_switch_not_found")
    async with get_sessionmaker()() as session:
        base = get_settings()
        if args.command == "apply" and (words is not None or args.only_differing):
            current = await resolve(session, base)
            if current.reason:
                raise FeatureError(503, current.reason)
            if words is not None:
                changes.update(family_changes(words, args.alerting, current))
            if args.only_differing:
                changes = {key: value for key, value in changes.items()
                           if current.values[key].enabled != value}
        if changes:
            await change(session, base, changes, actor=args.source)
            await session.commit()
        elif args.command in ("show", "env"):
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
    for name in ("show", "env", "defaults", "set", "reset", "apply"):
        command = sub.add_parser(name)
        if name in ("set", "reset"):
            command.add_argument("key")
        if name == "set":
            command.add_argument("value", choices=("on", "off"))
        if name == "apply":
            command.add_argument("values", nargs="*")
            command.add_argument("--only-differing", action="store_true")
            command.add_argument("--families")
            command.add_argument("--alerting", choices=("defaults", "shadow", "alerting"),
                                 default="defaults")
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
