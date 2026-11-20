"""Which collection and alerting families run: the database over the environment.

A row in ``feature_switch`` wins over the ``.env`` value; an absent row leaves
``.env``, then the code default, in force. A read that fails switches every
family off for that run (AGENTS rule 8): a run that cannot read its own switches
cannot be trusted to speak for what it collects.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.feature_switch import FEATURE_KEYS, FeatureSwitch

logger = logging.getLogger("app.feature_switches")

#: English family names used by backend messages and the doctor.
FAMILY_LABELS: dict[str, str] = {
    "waf_collection_enabled": "WAF collection",
    "waf_alerting_enabled": "WAF alerting",
    "waf_outcome_collection_enabled": "WAF outcome collection",
    "waf_client_collection_enabled": "WAF client collection",
    "ips_collection_enabled": "IPS collection",
    "ips_alerting_enabled": "IPS alerting",
    "rule_match_collection_enabled": "Rule-match collection",
    "rule_alerting_enabled": "Rule alerting",
    "pw_collection_enabled": "Property watch collection",
    "pw_alerting_enabled": "Property watch alerting",
    "custom_rules_enabled": "Custom rule evaluation",
    "custom_rules_alerting_enabled": "Custom rule alerting",
    "identity_collection_enabled": "Identity collection",
    "identity_alerting_enabled": "Identity alerting",
    "catchall_collection_enabled": "Catch-all sender collection",
    "catchall_new_sender_alerts_enabled": "Catch-all new sender alerts",
    "geo_collection_enabled": "Geography collection",
    "qradar_health_collection_enabled": "QRadar health collection",
    "explanation_enabled": "Anomaly explanations",
    "inventory_new_source_alerts_enabled": "Inventory new source alerts",
    "jdbc_collection_enabled": "JDBC collection",
    "qradar_source_pause_enabled": "QRadar source pauses",
}

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


def unreadable_features() -> ResolvedFeatures:
    """Every switch off with ``UNREADABLE_REASON``: what a read that failed resolves to."""
    return ResolvedFeatures(
        values={key: ResolvedSwitch(False, "unreadable") for key in FEATURE_KEYS},
        reason=UNREADABLE_REASON,
    )


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
        return unreadable_features()

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


#: How long the API keeps one resolved read (design section 2.3). A write on this
#: replica drops it at once; another replica sees the change within this many seconds.
EFFECTIVE_CACHE_SECONDS = 5.0


class EffectiveSettingsCache:
    """One resolved read per ``ttl`` seconds for the API, however many requests arrive.

    Keyed on the identity of ``base`` so a test (or a reload) that supplies other
    settings never sees another's switches. A failed read is cached like any other:
    it is every switch off, and the next read is at most ``ttl`` seconds away.
    """

    def __init__(
        self, ttl: float = EFFECTIVE_CACHE_SECONDS, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.ttl = ttl
        self.clock = clock
        self._entry: tuple[float, Settings, Settings] | None = None
        self._generation = 0

    def invalidate(self) -> None:
        """Forget the cached read; called by the writer after its commit."""
        self._generation += 1
        self._entry = None

    async def get(self, session: AsyncSession, base: Settings) -> Settings:
        entry = self._entry
        if entry is not None and entry[1] is base and self.clock() < entry[0]:
            return entry[2]
        generation = self._generation
        resolved = await resolve(session, base)
        effective = effective_settings(base, resolved)
        # A write that committed while this read was in flight made it stale.
        if generation == self._generation:
            self._entry = (self.clock() + self.ttl, base, effective)
        return effective


effective_cache = EffectiveSettingsCache()


def invalidate_effective_settings() -> None:
    effective_cache.invalidate()
