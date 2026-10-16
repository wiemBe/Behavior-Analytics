"""Apply TimescaleDB retention and compression policies from configuration.

Kept out of migrations on purpose: retention *drops data*, and a destructive
action must be an explicit, configurable operation — never an implicit side
effect of running `alembic upgrade`. All policies default to disabled.

Reconciling, not just adding: each table's policy job is read back from
`timescaledb_information.jobs` and its interval compared with the setting. An
unchanged policy is left alone (stable job id), a different one is removed and
added again inside a savepoint, and an unset one is removed (`if_exists`).
`add_*_policy(..., if_not_exists => TRUE)` would keep an old interval, so it is
not relied on. Safe to repeat, including on hypertables with compressed chunks.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings

logger = logging.getLogger("app.timescale")


_METRIC_TABLES = ("log_source_metric", "search_result_metric", "rule_metric", "offense_snapshot")


async def _timescale_available(session: AsyncSession) -> bool:
    result = await session.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    )
    return result.scalar() is not None


async def _hypertables(session: AsyncSession) -> set[str]:
    """Which of our metric tables Timescale actually manages.

    The extension being installed does not imply the tables were converted: the
    initial migration creates hypertables only when the extension is present at
    migration time, so a schema migrated on vanilla Postgres and upgraded later
    has plain tables. Both add_retention_policy() and remove_retention_policy()
    raise on a non-hypertable even with if_exists/if_not_exists, so unmanaged
    tables must be filtered out rather than attempted.
    """
    result = await session.execute(
        text("SELECT hypertable_name FROM timescaledb_information.hypertables")
    )
    return {row[0] for row in result}


async def _policy_config(session: AsyncSession, table: str, proc: str) -> dict[str, str] | None:
    """The config of the table's policy job, or None when it has no such job."""
    result = await session.execute(
        text(
            "SELECT config::jsonb FROM timescaledb_information.jobs "
            "WHERE proc_name = :proc AND hypertable_name = :t"
        ).bindparams(proc=proc, t=table)
    )
    row = result.first()
    return None if row is None else dict(row[0])


async def _same_interval(session: AsyncSession, current: str | None, days: int) -> bool:
    if current is None:
        return False
    result = await session.execute(
        text("SELECT (:cur)::interval = (:want)::interval").bindparams(
            cur=current, want=f"{days} days"
        )
    )
    return bool(result.scalar())


async def _reconcile_retention(session: AsyncSession, table: str, days: int | None) -> str:
    """Make the table's retention policy match `days` (None = no policy).

    `add_retention_policy(if_not_exists => TRUE)` keeps an existing policy as it
    is when its interval differs, so a changed setting would never take effect.
    A policy with another interval is removed and added again.
    """
    if days is None:
        # Retention disabled or unset for this table: ensure no policy exists
        # so a previously-configured destructive policy can be turned off.
        await session.execute(
            text("SELECT remove_retention_policy(:t, if_exists => TRUE)").bindparams(t=table)
        )
        return "retention-disabled"
    if days < 1:
        raise ValueError(f"retention for {table} must be >= 1 day, got {days}")
    current = await _policy_config(session, table, "policy_retention")
    if current is not None:
        if await _same_interval(session, current.get("drop_after"), days):
            return f"retention={days}d"
        await session.execute(text("SELECT remove_retention_policy(:t)").bindparams(t=table))
    await session.execute(
        text("SELECT add_retention_policy(:t, drop_after => (:ival)::interval)").bindparams(
            t=table, ival=f"{days} days"
        )
    )
    return f"retention={days}d"


async def _reconcile_compression(session: AsyncSession, table: str, days: int | None) -> str:
    """Make the table's compression policy match `days` (None = no policy)."""
    if days is None:
        await session.execute(
            text("SELECT remove_compression_policy(:t, if_exists => TRUE)").bindparams(t=table)
        )
        return "compression-disabled"
    if days < 1:
        raise ValueError(f"compression for {table} must be >= 1 day, got {days}")
    # `table` comes from the module constant _METRIC_TABLES, never from input.
    await session.execute(text("ALTER TABLE " + table + " SET (timescaledb.compress = true)"))
    current = await _policy_config(session, table, "policy_compression")
    if current is not None:
        if await _same_interval(session, current.get("compress_after"), days):
            return f"compression={days}d"
        await session.execute(text("SELECT remove_compression_policy(:t)").bindparams(t=table))
    await session.execute(
        text(
            "SELECT add_compression_policy(:t, compress_after => (:ival)::interval)"
        ).bindparams(t=table, ival=f"{days} days")
    )
    return f"compression={days}d"


async def apply_policies(session: AsyncSession, settings: Settings | None = None) -> dict[str, str]:
    """Reconcile retention/compression policies with configuration.

    Returns a per-table description of the action taken, for logging/auditing.
    Compression results sit under `_compression:<table>` keys. Each table is
    reconciled inside its own savepoint: a failure is reported as
    `error:<ExceptionType>` for that table and the others still run.
    """
    settings = settings or get_settings()
    outcome: dict[str, str] = {}

    if not await _timescale_available(session):
        logger.info("timescaledb not present; skipping policy application")
        return {"_status": "timescaledb-absent"}

    retention = settings.retention_days()
    managed = await _hypertables(session)

    for table in _METRIC_TABLES:
        if table not in managed:
            outcome[table] = "not-a-hypertable"
            continue
        try:
            async with session.begin_nested():
                outcome[table] = await _reconcile_retention(session, table, retention.get(table))
        except Exception as exc:
            outcome[table] = f"error:{type(exc).__name__}"
            logger.error("retention policy failed", extra={"table": table}, exc_info=True)
        try:
            async with session.begin_nested():
                outcome[f"_compression:{table}"] = await _reconcile_compression(
                    session, table, settings.compression_after_days
                )
        except Exception as exc:
            outcome[f"_compression:{table}"] = f"error:{type(exc).__name__}"
            logger.error("compression policy failed", extra={"table": table}, exc_info=True)

    await session.commit()
    logger.info("timescale policies applied", extra={"outcome": outcome})
    return outcome
