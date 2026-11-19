"""Shared, transactional feature writes for the API and offline CLI."""
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, switch_dependent_problems
from app.models.feature_switch import FeatureSwitch
from app.repositories.audit import record_audit
from app.services.feature_switches import (
    ALERTING_REQUIRES,
    FEATURE_KEYS,
    ResolvedSwitch,
    effective_settings,
    resolve,
)


class FeatureError(Exception):
    def __init__(self, status: int, code: str, problems: list[str] | None = None):
        self.status, self.code, self.problems = status, code, problems or []
        super().__init__(code)


async def change(
    session: AsyncSession, base: Settings, changes: dict[str, bool | None], *,
    actor: str, source_ip: str | None = None,
) -> None:
    if set(changes) - set(FEATURE_KEYS):
        raise FeatureError(404, "feature_switch_not_found")
    # Serialize even first inserts: independent keys can invalidate one another's dependencies.
    try:
        await session.execute(text("SELECT pg_advisory_xact_lock(72461204)"))
    except SQLAlchemyError as exc:
        raise FeatureError(503, "feature_switch_unreadable") from exc
    current = await resolve(session, base)
    if current.reason:
        raise FeatureError(503, current.reason)
    if not base.feature_switches_db:
        raise FeatureError(409, "feature_switch_database_disabled")
    rows = {key: await session.get(FeatureSwitch, key) for key in changes}
    if all(value is None and rows[key] is None for key, value in changes.items()):
        return
    proposed = dict(current.values)
    fallback = await resolve(session, base.model_copy(update={"feature_switches_db": False}))
    for key, enabled in changes.items():
        proposed[key] = fallback.values[key] if enabled is None else ResolvedSwitch(enabled, "db")
    for key in changes:
        required = ALERTING_REQUIRES.get(key)
        if (proposed[key].enabled and required and not proposed[required].enabled
                and (changes[key] is not None or rows[key] is not None)):
            raise FeatureError(422, "feature_switch_dependency", [key, required])
    problems = switch_dependent_problems(
        effective_settings(base, replace(current, values=proposed)))
    if problems:
        raise FeatureError(422, "feature_switch_settings_invalid", problems)
    for key, enabled in changes.items():
        old, new = current.values[key], proposed[key]
        if enabled is None:
            if rows[key] is None:
                continue
            await session.delete(rows[key])
        elif old.enabled == enabled:
            continue
        else:
            values = {"key": key, "enabled": enabled,
                      "updated_at": datetime.now(UTC), "updated_by": actor}
            await session.execute(insert(FeatureSwitch).values(**values).on_conflict_do_update(
                index_elements=[FeatureSwitch.key], set_=values,
            ))
        await record_audit(
            session, action="feature_switch.reset" if enabled is None else "feature_switch.update",
            actor=actor, object_type="feature_switch", object_id=key, source_ip=source_ip,
            detail={"old": old.enabled, "new": new.enabled, "source": old.source},
        )
