"""Write one tick of identity facts. One bulk statement per table.

The number of statements does not grow with the number of sources or names
(AGENTS.md rule 9, by analogy with the WAF and IPS bucket writers). The fold
has already dropped every username the allow-list refuses; this module stores
only the lists it was given and never reads a clock.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import Table, delete, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.fold import IdentityFold
from app.models.enums import IDENTITY_BUCKET_SECONDS, IdentityMeasurement
from app.models.identity_detection import IdentityBucket, IdentityEventCount, IdentityTopUsername

_BUCKET_PK = ("log_source_id", "bucket_start")
_COUNT_PK = ("log_source_id", "event_id", "bucket_start")
_NAME_PK = ("log_source_id", "event_id", "bucket_start", "rank")


def _conflict_update(statement: Any, rows: list[dict[str, Any]], pk: tuple[str, ...]) -> Any:
    set_ = {key: statement.excluded[key] for key in rows[0] if key not in pk}
    return statement.on_conflict_do_update(index_elements=list(pk), set_=set_)


async def write_tick(
    session: AsyncSession,
    folded: IdentityFold,
    *,
    collected_at: datetime,
    bucket_seconds: int = IDENTITY_BUCKET_SECONDS,
    provenance: dict[str, Any] | None = None,
) -> None:
    """Upsert the tick. Pass `collected_at`; the writer does not read the clock.

    Buckets, event counts and usernames are each one statement, however many
    sources the tick holds. Usernames are deleted and inserted once for the
    whole tick, so a name the fold no longer returns does not stay behind.
    """
    if not folded.buckets:
        return
    recorded = provenance if provenance is not None else {}
    bucket_rows: list[dict[str, Any]] = []
    count_rows: list[dict[str, Any]] = []
    name_rows: list[dict[str, Any]] = []
    # A name search that did not run must not wipe the names already stored.
    name_keys: list[tuple[Any, int, datetime]] = []
    for bucket in folded.buckets:
        bucket_rows.append(
            {
                "log_source_id": bucket.log_source_id,
                "bucket_start": bucket.bucket_start,
                "bucket_seconds": bucket_seconds,
                "catalog_version": bucket.catalog_version,
                "measurement": bucket.measurement.value,
                "population": bucket.population,
                "admission_truncated": bucket.admission_truncated,
                "query_provenance": recorded,
                "collected_at": collected_at,
            }
        )
        for series in bucket.series:
            count_rows.append(
                {
                    "log_source_id": series.log_source_id,
                    "event_id": series.event_id,
                    "bucket_start": series.bucket_start,
                    "event_count": series.count,
                    "measurement": series.measurement.value,
                    "names_measurement": series.names_measurement.value,
                    "admission_truncated": series.admission_truncated,
                }
            )
            if series.names_measurement is IdentityMeasurement.NOT_COLLECTED:
                continue
            name_keys.append((series.log_source_id, series.event_id, series.bucket_start))
            for name in series.usernames:
                name_rows.append(
                    {
                        "log_source_id": series.log_source_id,
                        "event_id": series.event_id,
                        "bucket_start": series.bucket_start,
                        "rank": name.rank,
                        "username": name.username,
                        "truncated": name.truncated,
                        "event_count": name.count,
                    }
                )

    bucket_insert = pg_insert(cast(Table, IdentityBucket.__table__))
    await session.execute(_conflict_update(bucket_insert, bucket_rows, _BUCKET_PK), bucket_rows)
    count_insert = pg_insert(cast(Table, IdentityEventCount.__table__))
    await session.execute(_conflict_update(count_insert, count_rows, _COUNT_PK), count_rows)
    if name_keys:
        await session.execute(
            delete(IdentityTopUsername).where(
                tuple_(
                    IdentityTopUsername.log_source_id,
                    IdentityTopUsername.event_id,
                    IdentityTopUsername.bucket_start,
                ).in_(name_keys)
            )
        )
    if name_rows:
        name_insert = pg_insert(cast(Table, IdentityTopUsername.__table__))
        await session.execute(_conflict_update(name_insert, name_rows, _NAME_PK), name_rows)
