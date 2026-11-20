"""The offense aggregate preserves the former Python counts, including SQL NULLs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import product
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, create_engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.schemas.log_source import OverviewOffenses
from app.services.overview import OverviewService

CAPTURED = datetime(2026, 11, 20, tzinfo=UTC)


@dataclass(frozen=True)
class CaptureRow:
    captured_at: datetime = CAPTURED
    status: str | None = "OPEN"
    magnitude: int | None = None
    is_assigned: bool | None = False
    age_seconds: int | None = None


def reference(rows: list[CaptureRow], critical_magnitude: int) -> OverviewOffenses:
    """The old Python algorithm, with its latest-capture and OPEN filters."""
    if not rows:
        return OverviewOffenses(
            active=None, critical=None, critical_unknown=None, unassigned=None,
            oldest_age_seconds=None,
        )
    latest = max(row.captured_at for row in rows)
    opened = [row for row in rows if row.captured_at == latest and row.status == "OPEN"]
    ages = [row.age_seconds for row in opened if row.age_seconds is not None]
    return OverviewOffenses(
        active=len(opened),
        critical=sum(
            1 for row in opened
            if row.magnitude is not None and row.magnitude >= critical_magnitude
        ),
        critical_unknown=sum(1 for row in opened if row.magnitude is None),
        unassigned=sum(1 for row in opened if not row.is_assigned),
        oldest_age_seconds=max(ages) if ages else None,
    )


SHAPES = [
    pytest.param([], id="no-snapshot"),
    pytest.param([CaptureRow(status="CLOSED", magnitude=10)], id="zero-open"),
    pytest.param([CaptureRow(status=None)], id="null-status"),
    pytest.param([
        CaptureRow(magnitude=7, age_seconds=20),
        CaptureRow(magnitude=None, is_assigned=True, age_seconds=None),
        CaptureRow(magnitude=9, is_assigned=None, age_seconds=100),
        CaptureRow(status="CLOSED", magnitude=10, age_seconds=1000),
    ], id="mixed-with-closed"),
    pytest.param([
        CaptureRow(captured_at=CAPTURED - timedelta(minutes=5), magnitude=10, age_seconds=999),
        CaptureRow(magnitude=6, is_assigned=True, age_seconds=10),
    ], id="latest-only"),
    pytest.param([
        CaptureRow(captured_at=CAPTURED - timedelta(minutes=5), magnitude=10, age_seconds=999),
        CaptureRow(status="CLOSED"),
    ], id="latest-closed"),
    *[
        pytest.param(
            [CaptureRow(magnitude=magnitude, is_assigned=assigned, age_seconds=age)],
            id=f"magnitude-{magnitude}-assigned-{assigned}-age-{age}",
        )
        for magnitude, assigned, age in product((None, 6, 7, 9), (None, False, True), (None, 0, 99))
    ],
]


@pytest.mark.parametrize("rows", SHAPES)
@pytest.mark.parametrize("critical_magnitude", [7, 9])
async def test_offense_aggregate_matches_python(
    rows: list[CaptureRow], critical_magnitude: int,
) -> None:
    # Only the aggregate's input columns are needed. The production Boolean is NOT NULL;
    # this isolated table also exercises Python's `not None` semantics without a migration.
    table = Table(
        "offense_snapshot", MetaData(),
        Column("captured_at", DateTime(timezone=True)), Column("status", String),
        Column("magnitude", Integer), Column("is_assigned", Boolean),
        Column("age_seconds", Integer),
    )
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            table.create(connection)
            if rows:
                connection.execute(table.insert(), [vars(row) for row in rows])
            session = AsyncMock(spec=AsyncSession)
            session.scalar.side_effect = connection.scalar
            session.execute.side_effect = connection.execute
            settings = get_settings().model_copy(
                update={"offense_critical_magnitude": critical_magnitude}
            )
            actual = await OverviewService(session, settings)._offense_overview()
            assert actual == reference(rows, critical_magnitude)
            assert session.scalar.await_count == 1
            assert session.execute.await_count == (1 if rows else 0)
            session.scalars.assert_not_awaited()
    finally:
        engine.dispose()
