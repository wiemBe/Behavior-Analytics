"""Shared schema reset for the migration integration tests.

Every migration test drops and recreates `public` before each test; this
module holds that logic once, including the session-clearing guard that
keeps the drop from deadlocking against TimescaleDB background workers
(docs/tasks/T-083.report.md).
"""

from __future__ import annotations

from sqlalchemy import create_engine, text


def reset_public_schema(sync_url: str) -> None:
    """Drop and recreate `public`, clearing competing sessions on this database."""
    engine = create_engine(sync_url, future=True)
    with engine.connect() as conn:
        # The TimescaleDB Telemetry Reporter reads `public` relations while the
        # drop below takes AccessExclusiveLock on them, and PostgreSQL resolves
        # the cycle as a deadlock, failing this fixture's setup. Signalled
        # sessions are cleared first and only then waited for; pg_stat_activity
        # does not update inside an open transaction, so every step commits
        # before the next (docs/tasks/T-083.report.md).
        victims = ",".join(
            str(pid)
            for pid in conn.execute(
                text(
                    "SELECT pid FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                )
            ).scalars()
        )
        conn.commit()
        if victims:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                )
            )
            conn.commit()
            for _ in range(6):
                remaining = conn.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE pid::text = ANY(string_to_array(:victims, ','))"
                    ).bindparams(victims=victims)
                ).scalar()
                if not remaining:
                    break
                conn.execute(text("SELECT pg_sleep(0.05)"))
                conn.commit()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
