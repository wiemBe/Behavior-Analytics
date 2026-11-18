"""Which collection and alerting families run: the database over the environment.

A row in ``feature_switch`` wins over the ``.env`` value; an absent row leaves
``.env``, then the code default, in force. A read that fails switches every
family off for that run (AGENTS rule 8): a run that cannot read its own switches
cannot be trusted to speak for what it collects.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.feature_switch import FEATURE_KEYS, FeatureSwitch

logger = logging.getLogger("app.feature_switches")

#: An alerting or modifier switch, and the collection switch it needs (design
#: section 2.5 rules 1 and 2): inert while its prerequisite is off.
ALERTING_REQUIRES: dict[str, str] = {
    "waf_alerting_enabled": "waf_collection_enabled",
    "waf_outcome_collection_enabled": "waf_collection_enabled",
    "waf_client_collection_enabled": "waf_collection_enabled",
    "ips_alerting_enabled": "ips_collection_enabled",
    "rule_alerting_enabled": "rule_match_collection_enabled",
    "pw_alerting_enabled": "pw_collection_enabled",
    "custom_rules_alerting_enabled": "custom_rules_enabled",
    "identity_alerting_enabled": "identity_collection_enabled",
    "catchall_new_sender_alerts_enabled": "catchall_collection_enabled",
}

#: The reason code a failed read carries; a run reports it as its status reason.
UNREADABLE_REASON = "feature_switch_unreadable"

#: ``unreadable`` is not stored: it is what every key reads during a failed read.
FeatureSource = Literal["db", "env", "default", "unreadable"]


@dataclass(frozen=True)
class ResolvedSwitch:
    """The effective value of one switch and where it came from."""

    enabled: bool
    source: FeatureSource
    updated_at: datetime | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class ResolvedFeatures:
    """Every switch of ``FEATURE_KEYS``, plus what the read learned beyond that."""

    values: Mapping[str, ResolvedSwitch]
    #: Rows whose key this release does not know (a newer release wrote them).
    unknown_keys: tuple[str, ...] = ()
    #: ``UNREADABLE_REASON`` when the table could not be read and all keys are off.
    reason: str | None = None


def _from_environment(base: Settings) -> dict[str, ResolvedSwitch]:
    """The env file wins over the code default, exactly as before the table existed."""
    return {
        key: ResolvedSwitch(
            enabled=bool(getattr(base, key)),
            source="env" if key in base.model_fields_set else "default",
        )
        for key in FEATURE_KEYS
    }


async def resolve(session: AsyncSession, base: Settings) -> ResolvedFeatures:
    """Read the switches once: one statement of at most 20 rows, and never raise.

    A failed read returns every key off with ``UNREADABLE_REASON`` and source
    ``unreadable``, never the ``.env`` value: a family that cannot be shown to be
    on must not run. ``feature_switches_db`` false sends no query at all and
    returns the ``.env``/default values, so the table can be ignored without a
    downgrade.
    """
    values = _from_environment(base)
    if not base.feature_switches_db:
        logger.debug("feature switches read from the environment: the table is switched off")
        return ResolvedFeatures(values=values)

    try:
        rows = (await session.scalars(select(FeatureSwitch))).all()
    except Exception:
        logger.warning(
            "feature switches unreadable; every family is off for this run",
            extra={"reason": UNREADABLE_REASON},
            exc_info=True,
        )
        return ResolvedFeatures(
            values={key: ResolvedSwitch(False, "unreadable") for key in FEATURE_KEYS},
            reason=UNREADABLE_REASON,
        )

    unknown = sorted(row.key for row in rows if row.key not in values)
    for row in rows:
        if row.key in values:
            values[row.key] = ResolvedSwitch(
                row.enabled, "db", updated_at=row.updated_at, updated_by=row.updated_by
            )
    if unknown:
        logger.warning(
            "feature switch rows with a key this release does not know are ignored",
            extra={"keys": unknown},
        )
    return ResolvedFeatures(values=values, unknown_keys=tuple(unknown))


def effective_settings(base: Settings, resolved: ResolvedFeatures) -> Settings:
    """``base`` with only the ``FEATURE_KEYS`` values replaced, nothing else touched."""
    update = {
        key: resolved.values[key].enabled for key in FEATURE_KEYS if key in resolved.values
    }
    return base.model_copy(update=update)
