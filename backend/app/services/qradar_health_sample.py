"""The REST half of the stored QRadar health: one sample per console per run (D-42 section 3).

`sample_from_health` reduces the section the 30 s health service read to six figures; a figure
that was not read is None, never a zero. `store_rest_sample` writes it keyed on the cadence
slot the run claimed, so it lands at most once per run however often the service is polled.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.qradar_health import QRadarHealthRestSample
from app.schemas.qradar_health import FieldState, QRadarHealth

logger = logging.getLogger("app.services.qradar_health_sample")


@dataclass(frozen=True)
class RestSample:
    reachable: bool | None
    api_latency_ms: int | None
    hosts_active: int | None
    hosts_total: int | None
    ha_problem_count: int | None
    license_overallocated: bool | None


def _ha_problem_count(health: QRadarHealth) -> int | None:
    """HA pairs that are degraded, failed over or failed, when the health service says.

    The managed-host summary carries `ha_problem` once it knows about HA pairs (HLT-1); until
    then, and whenever the hosts were not read, there is no count, and None is not zero.
    """
    servers = health.servers.value
    value = getattr(servers, "ha_problem", None)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def sample_from_health(health: QRadarHealth) -> RestSample:
    reachable = health.reachable.value if health.reachable.state is FieldState.OK else None
    latency = health.api_latency_ms.value if health.api_latency_ms.state is FieldState.OK else None
    servers = health.servers.value
    over: bool | None = None
    pool = health.license.value
    if pool is not None:
        flags = [
            rate.overallocated
            for rate in (pool.eps, pool.fpm)
            if rate is not None and rate.overallocated is not None
        ]
        if flags:
            over = any(flags)
    return RestSample(
        reachable=reachable,
        api_latency_ms=latency,
        hosts_active=servers.active if servers is not None else None,
        hosts_total=servers.total if servers is not None else None,
        ha_problem_count=_ha_problem_count(health),
        license_overallocated=over,
    )


async def store_rest_sample(
    session: AsyncSession,
    instance_id: uuid.UUID,
    *,
    slot_start: datetime,
    sampled_at: datetime,
    sample: RestSample,
) -> bool:
    """Insert the run's sample unless this slot already has one. True when a row was written."""
    statement = (
        pg_insert(QRadarHealthRestSample)
        .values(
            instance_id=instance_id,
            slot_start=slot_start,
            sampled_at=sampled_at,
            reachable=sample.reachable,
            api_latency_ms=sample.api_latency_ms,
            hosts_active=sample.hosts_active,
            hosts_total=sample.hosts_total,
            ha_problem_count=sample.ha_problem_count,
            license_overallocated=sample.license_overallocated,
        )
        .on_conflict_do_nothing(index_elements=["instance_id", "slot_start"])
        .returning(QRadarHealthRestSample.slot_start)
    )
    return (await session.execute(statement)).first() is not None


def make_sample_sink(
    session: AsyncSession,
    instance_id: uuid.UUID,
    *,
    slot_start: datetime,
    clock: Callable[[], datetime] | None = None,
) -> Callable[[QRadarHealth], Awaitable[None]]:
    """The sink `read_qradar_health` calls with each fresh section."""
    now = clock or (lambda: datetime.now(UTC))

    async def sink(health: QRadarHealth) -> None:
        # A savepoint: a failed insert must not poison the run's own transaction.
        async with session.begin_nested():
            await store_rest_sample(
                session,
                instance_id,
                slot_start=slot_start,
                sampled_at=now(),
                sample=sample_from_health(health),
            )

    return sink
