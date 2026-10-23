"""Administer WAF source profiles.

    python -m app.cli.waf set --instance qradarce2 --log-source 312 \\
        --url-property URL --method-property HTTP_Method \\
        --action-property WAF_Action --response-code-property WAF_ResponseCode \\
        --blocked-actions DENY --allowed-actions ALLOW
    python -m app.cli.waf validate --instance qradarce2 --log-source 312
    python -m app.cli.waf enable   --instance qradarce2 --log-source 312
    python -m app.cli.waf status [--json]
    python -m app.cli.waf probe-rejection --instance qradarce2 --log-source 312 [--json]

`set` states the complete mapping: a field not given is recorded as unavailable
on this source. `--blocked-actions` / `--allowed-actions` author the action map
from the values `validate` discovered; blocking is never inferred from a response
code, and a value in neither list is counted as unmapped. Every change is
audited with the acting operator (`--actor`, default the OS user). `validate` runs
bounded read-only queries against QRadar; nothing here writes to QRadar.

`probe-rejection` is the operator procedure for docs/PHASE-B-WAF-BEHAVIOR.md
section 29.9. Validation assumes QRadar refuses an Ariel query naming a
nonexistent property with HTTP 422. This issues two bounded, read-only searches
against one log source over the last 15 minutes -- one naming a property that
cannot exist, one naming the profile's URL property as a control -- and records
exactly what QRadar did with each: HTTP status, whether a search was created,
how it ended. No result row is fetched. The observation is printed and written
to the audit log, so the assumption is verified by evidence rather than
restated.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import secrets
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.waf import WafSourceProfile
from app.providers.base import ProviderError
from app.providers.dto import WafRejectionObservation
from app.providers.factory import build_provider_for_instance
from app.repositories.audit import record_audit
from app.services.ariel_permit import ariel_background_priority
from app.services.waf_profiles import WafProfileService
from app.services.waf_status import waf_ariel_budget_status, waf_source_statuses
from app.waf.profile import WafFieldMapping, WafProfileError


async def _resolve(
    session: AsyncSession, instance_name: str, qradar_id: int
) -> tuple[QRadarInstance, LogSource]:
    instance = await session.scalar(
        select(QRadarInstance).where(QRadarInstance.name == instance_name)
    )
    if instance is None:
        raise WafProfileError(f"no QRadar instance named {instance_name!r}")
    log_source = await session.scalar(
        select(LogSource).where(
            LogSource.instance_id == instance.id, LogSource.qradar_id == qradar_id
        )
    )
    if log_source is None:
        raise WafProfileError(
            f"log source {qradar_id} is not in the inventory of {instance_name!r}; "
            "run `python -m app.cli.sync log-sources` first"
        )
    return instance, log_source


def _csv(value: str | None) -> list[str]:
    return [v for v in (value or "").split(",") if v.strip()]


def _failures(consecutive_failures: int | None) -> str:
    """Failure count for the status line. A missing watermark is unknown."""
    if consecutive_failures is None:
        return "unknown"
    return str(consecutive_failures)


async def _run(args: argparse.Namespace) -> int:
    maker = get_sessionmaker()
    async with maker() as session:
        service = WafProfileService(session)
        if args.command == "status":
            statuses = await waf_source_statuses(
                session, settings=get_settings(), now=datetime.now(UTC)
            )
            budget = await waf_ariel_budget_status(session, settings=get_settings())
            if args.json:
                print(json.dumps({
                    "ariel_budget": budget.model_dump(mode="json"),
                    "sources": [s.model_dump(mode="json") for s in statuses],
                }, indent=2))
            else:
                print(
                    f"WAF Ariel permits: {budget.active_permits if budget.enforced else '-'}"
                    f"/{budget.max_concurrent_sources} in use"
                    + ("" if budget.enforced else " (not enforced on this database)")
                )
                for s in statuses:
                    print(
                        f"{s.qradar_log_source_id:>6} {s.log_source_name[:32]:32} "
                        f"v{s.profile_version} {s.validation_status:12} "
                        f"eligible={s.collection_eligible} lag={s.lag_seconds} "
                        f"failures={_failures(s.consecutive_failures)} "
                        f"active={s.active_endpoints} "
                        f"searches={s.ariel_searches_last_run} "
                        f"duration_ms={s.last_collection_duration_ms}"
                        + (f" skipped={s.last_skip_reason}" if s.previous_run_skipped else "")
                        + (f"  ({s.ineligible_reason})" if s.ineligible_reason else "")
                    )
            return 0

        instance, log_source = await _resolve(session, args.instance, args.log_source)
        if args.command == "set":
            mapping = WafFieldMapping(
                url=args.url_property,
                method=args.method_property,
                virtual_host=args.virtual_host_property,
                action=args.action_property,
                response_code=args.response_code_property,
                rule_id=args.rule_id_property,
                user_agent=args.user_agent_property,
                blocked_actions=tuple(_csv(args.blocked_actions)),
                allowed_actions=tuple(_csv(args.allowed_actions)),
            )
            profile, changed = await service.upsert(
                log_source=log_source,
                mapping=mapping,
                actor=args.actor,
                product_label=args.product_label,
            )
            await session.commit()
            print(
                f"profile v{profile.profile_version} "
                f"{'updated' if changed else 'unchanged'}; validation {profile.validation_status}"
            )
            return 0

        existing = await service.for_log_source(log_source)
        if existing is None:
            raise WafProfileError("no WAF profile exists for this log source; run `set` first")

        if args.command == "validate":
            provider = build_provider_for_instance(instance)
            try:
                result = await service.validate(existing, provider, actor=args.actor)
            finally:
                await provider.aclose()
            await session.commit()
            if args.json:
                print(json.dumps(result.as_report(), indent=2))
            else:
                print(f"validation: {result.status.value}")
                for reason in result.reasons:
                    print(f"  - {reason}")
                for f in result.fields:
                    print(f"  {f.logical.value:14} {f.status.value:22} {f.property_name or ''}")
            return 0 if result.status.value == "VALID" else 2

        if args.command == "probe-rejection":
            report = await probe_rejection(session, instance, log_source, existing, args.actor)
            await session.commit()
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                print(f"conclusion: {report['conclusion']}")
                for o in report["observations"]:
                    print(
                        f"  {o['role']:12} {o['outcome']:18} http={o['create_http_status']} "
                        f"search={o['search_status']}"
                    )
            return 0

        if args.command in ("enable", "disable"):
            changed = await service.set_enabled(
                existing, args.command == "enable", actor=args.actor
            )
            await session.commit()
            print(f"profile {args.command}d" if changed else f"profile already {args.command}d")
            return 0
    return 1


def interpret_rejection(
    missing: WafRejectionObservation, control: WafRejectionObservation
) -> tuple[str, str]:
    """(code, sentence) for what the observations mean for profile validation."""
    if control.outcome != "SEARCH_COMPLETED":
        return (
            "INCONCLUSIVE_CONTROL_FAILED",
            f"the control query naming a mapped property did not complete ({control.outcome}); "
            "nothing can be concluded about how a nonexistent property is refused",
        )
    if missing.outcome == "REJECTED_AT_CREATE" and missing.create_http_status == 422:
        return (
            "CONFIRMED_422",
            "a nonexistent property is refused at search creation with HTTP 422, as "
            "validation assumes: it is reported PROPERTY_REJECTED (INVALID)",
        )
    if missing.outcome == "REJECTED_AT_CREATE":
        return (
            "REJECTED_WITH_OTHER_STATUS",
            f"a nonexistent property is refused with HTTP {missing.create_http_status}, not "
            "422: validation reports it QUERY_FAILED (INCONCLUSIVE), which still blocks "
            "collection but names the cause less precisely",
        )
    if missing.outcome == "SEARCH_FAILED":
        return (
            "REJECTED_ASYNCHRONOUSLY",
            "the search is created and then fails: validation reports QUERY_FAILED "
            "(INCONCLUSIVE), which still blocks collection",
        )
    if missing.outcome == "SEARCH_COMPLETED":
        return (
            "ACCEPTED_SILENTLY",
            "QRadar accepted a nonexistent property and completed the search: validation "
            "would see no populated values and report UNPOPULATED (INVALID) for a required "
            "field; the 422 assumption does not hold on this appliance",
        )
    return (
        "INCONCLUSIVE",
        f"the probe for a nonexistent property ended {missing.outcome}; repeat the procedure",
    )


async def probe_rejection(
    session: AsyncSession,
    instance: QRadarInstance,
    log_source: LogSource,
    profile: WafSourceProfile,
    actor: str,
) -> dict:
    # Letters and digits only, so it passes the property-name allowlist; random,
    # so it cannot be a property an operator authored.
    missing_name = f"QRDASH_NONEXISTENT_{secrets.token_hex(6).upper()}"
    end = datetime.now(UTC).replace(microsecond=0)
    provider = build_provider_for_instance(instance)
    try:
        with ariel_background_priority():
            observed = await provider.observe_property_rejection(
                qradar_log_source_id=log_source.qradar_id,
                window_start=end - timedelta(minutes=15),
                window_end=end,
                property_names=[missing_name, profile.url_property],
            )
    finally:
        await provider.aclose()
    by_name = {o.property_name: o for o in observed}
    missing, control = by_name[missing_name], by_name[profile.url_property]
    code, sentence = interpret_rejection(missing, control)
    report = {
        "conclusion_code": code,
        "conclusion": sentence,
        "observed_at": end.isoformat(),
        "qradar_log_source_id": log_source.qradar_id,
        "observations": [
            {"role": "nonexistent", **missing.model_dump(mode="json")},
            {"role": "control", **control.model_dump(mode="json")},
        ],
    }
    await record_audit(
        session,
        action="waf.qradar.rejection_semantics_observed",
        actor=actor,
        object_type="waf_source_profile",
        object_id=str(profile.id),
        outcome="SUCCESS",
        detail=report,
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli.waf", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def source_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--instance", required=True)
        p.add_argument("--log-source", type=int, required=True, help="QRadar log source id")
        p.add_argument("--actor", default=getpass.getuser())

    set_p = sub.add_parser("set", help="create or change a profile's field mapping")
    source_args(set_p)
    set_p.add_argument("--url-property", required=True)
    set_p.add_argument("--method-property", required=True)
    for name in ("virtual-host", "action", "response-code", "rule-id", "user-agent"):
        set_p.add_argument(f"--{name}-property", default=None)
    set_p.add_argument("--product-label", default=None)
    set_p.add_argument("--blocked-actions", default="", help="comma-separated action values")
    set_p.add_argument("--allowed-actions", default="", help="comma-separated action values")

    for name in ("validate", "enable", "disable", "probe-rejection"):
        p = sub.add_parser(name)
        source_args(p)
        if name in ("validate", "probe-rejection"):
            p.add_argument("--json", action="store_true")

    status_p = sub.add_parser("status")
    status_p.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except (ProviderError, WafProfileError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
