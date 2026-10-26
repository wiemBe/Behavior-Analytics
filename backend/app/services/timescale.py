"""Apply TimescaleDB retention and compression policies from configuration.

Kept out of migrations on purpose: retention *drops data*, and a destructive
action must be an explicit, configurable operation — never an implicit side
effect of running `alembic upgrade`. All policies default to disabled.

Reconciling, not just adding: each table's policy job is read back from
`timescaledb_information.jobs` and its interval compared with the setting. An
unchanged policy is left alone (stable job id), a different one is removed and
added again, and an unset one is removed (`if_exists`).
`add_*_policy(..., if_not_exists => TRUE)` would keep an old interval, so it is
not relied on. Safe to repeat, including on hypertables with compressed chunks.

Each reconcile commits its own transaction. Removing a policy job deletes
Timescale's catalog rows and cancels a running job, and the locks that takes on
the job catalog are held until commit — a savepoint release does not release
them. One transaction across the whole loop would therefore make the next
table's reconcile wait on, and deadlock with, the very policy job an earlier
table just scheduled (a new job starts at once), so no statement of one
reconcile runs inside another's transaction.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings

logger = logging.getLogger("app.timescale")


_METRIC_TABLES = ("log_source_metric", "search_result_metric", "rule_metric", "offense_snapshot")

# Postgres SQLSTATE for a deadlock victim ("deadlock detected").
_DEADLOCK_SQLSTATE = "40P01"
# A deadlocked reconcile is retried: the policy job it ran into finishes or is
# cancelled on its own. At most one + len(_DEADLOCK_RETRY_PAUSES) attempts, in
# total, with _DEADLOCK_RETRY_PAUSES[i] seconds before retry i + 1. A test sets
# the pauses to 0.
_DEADLOCK_RETRY_PAUSES: tuple[float, ...] = (0.5, 1.0)

# A reconcile callable: one table's retention or compression reconcile.
_Reconcile = Callable[[AsyncSession, str, int | None], Awaitable[str]]


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


async def _compression_enabled(session: AsyncSession, table: str) -> bool:
    """Whether the hypertable already has `timescaledb.compress` turned on."""
    result = await session.execute(
        text(
            "SELECT compression_enabled FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = :t"
        ).bindparams(t=table)
    )
    return bool(result.scalar())


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
    # The SET holds an ACCESS EXCLUSIVE lock until the reconcile's commit, so it
    # runs only when compression is not on yet — re-issuing it on an already
    # enabled hypertable buys nothing and locks the table for nothing.
    if not await _compression_enabled(session, table):
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


def _is_deadlock(exc: Exception) -> bool:
    """Whether exc is a Postgres deadlock (SQLSTATE 40P01).

    SQLAlchemy wraps the driver's error in a DBAPIError whose `.orig` is its
    asyncpg adapter error, which carries the server's SQLSTATE on `.sqlstate`.
    """
    return getattr(getattr(exc, "orig", None), "sqlstate", None) == _DEADLOCK_SQLSTATE


async def _reconcile_in_own_transaction(
    session: AsyncSession, label: str, table: str, reconcile: _Reconcile, days: int | None
) -> str:
    """Run one reconcile alone in its own transaction, retrying a deadlock.

    Committed when it succeeds, rolled back when it raises, so no statement of
    one reconcile runs in another's transaction (see the module docstring for
    why). A deadlock is retried after a pause — the policy job that caused it
    ends on its own; any other error, and a deadlock that survives every
    attempt, is reported as `error:<ExceptionType>` without raising.
    """
    attempts = 1 + len(_DEADLOCK_RETRY_PAUSES)
    for attempt in range(1, attempts + 1):
        try:
            outcome = await reconcile(session, table, days)
            await session.commit()
            return outcome
        except Exception as exc:
            await session.rollback()
            if attempt < attempts and _is_deadlock(exc):
                pause = _DEADLOCK_RETRY_PAUSES[attempt - 1]
                if pause:
                    await asyncio.sleep(pause)
                continue
            logger.error(
                "%s policy failed",
                label,
                extra={"table": table, "attempts": attempt},
                exc_info=True,
            )
            return f"error:{type(exc).__name__}"
    raise AssertionError("unreachable: the loop returns on every path")  # pragma: no cover


async def apply_policies(session: AsyncSession, settings: Settings | None = None) -> dict[str, str]:
    """Reconcile retention/compression policies with configuration.

    Returns a per-table description of the action taken, for logging/auditing.
    Compression results sit under `_compression:<table>` keys. Each table's
    retention and each table's compression reconcile commits its own
    transaction, so a failure is reported as `error:<ExceptionType>` for that
    table and the others still run.
    """
    settings = settings or get_settings()
    outcome: dict[str, str] = {}

    if not await _timescale_available(session):
        logger.info("timescaledb not present; skipping policy application")
        return {"_status": "timescaledb-absent"}

    retention = settings.retention_days()
    managed = await _hypertables(session)
    # End the reads' transaction: the reconciles below each run in their own.
    await session.commit()

    for table in _METRIC_TABLES:
        if table not in managed:
            outcome[table] = "not-a-hypertable"
            continue
        outcome[table] = await _reconcile_in_own_transaction(
            session, "retention", table, _reconcile_retention, retention.get(table)
        )
        outcome[f"_compression:{table}"] = await _reconcile_in_own_transaction(
            session, "compression", table, _reconcile_compression, settings.compression_after_days
        )

    logger.info("timescale policies applied", extra={"outcome": outcome})
    return outcome
