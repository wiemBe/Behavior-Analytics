"""Pure contract tests for the platform-wide Ariel permit budget."""

from __future__ import annotations

import asyncio
import uuid
from itertools import combinations
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.services.ariel_permit import (
    _ARIEL_PRIORITY,
    ArielPermitBudget,
    ArielPermitUnavailable,
    _instance_slot_base,
    ariel_background_priority,
)


def settings(**overrides: object) -> Settings:
    values = Settings(_env_file=None).model_dump()
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_non_postgres_is_unbounded_without_waiting_or_slots() -> None:
    engine = cast(
        AsyncEngine,
        SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
    )
    budget = ArielPermitBudget(engine, settings(), instance_id=uuid.uuid4())

    assert not budget.enforced
    async with budget.permit() as permit:
        assert permit is not None
        assert permit.global_slot is None
        assert permit.instance_slot is None
        assert permit.waited_seconds == 0.0


@pytest.mark.asyncio
async def test_background_priority_is_task_local_and_restored() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def background_task() -> tuple[str, str]:
        with ariel_background_priority():
            entered.set()
            await release.wait()
            inside = _ARIEL_PRIORITY.get()
        return inside, _ARIEL_PRIORITY.get()

    task = asyncio.create_task(background_task())
    await entered.wait()
    assert _ARIEL_PRIORITY.get() == "live"
    release.set()
    assert await task == ("background", "live")
    assert _ARIEL_PRIORITY.get() == "live"


def test_background_priority_is_restored_after_an_exception() -> None:
    assert _ARIEL_PRIORITY.get() == "live"
    with pytest.raises(RuntimeError, match="stop"), ariel_background_priority():
        assert _ARIEL_PRIORITY.get() == "background"
        raise RuntimeError("stop")
    assert _ARIEL_PRIORITY.get() == "live"


@pytest.mark.parametrize("limit", [1, 2, 3, 7, 20])
def test_instance_range_is_contiguous_and_inside_the_positive_key_space(limit: int) -> None:
    for value in (0, 1, 2**64, 2**128 - 1):
        base = _instance_slot_base(uuid.UUID(int=value), limit)
        slots = list(range(base, base + limit))
        assert slots == [base + offset for offset in range(limit)]
        assert base % limit == 0
        assert 0 <= slots[0] <= slots[-1] <= 2**31 - 1


NAMESPACE_FIELDS = (
    "collection_advisory_lock_namespace",
    "waf_ariel_permit_lock_namespace",
    "ariel_permit_lock_namespace",
    "ariel_permit_instance_lock_namespace",
)


@pytest.mark.parametrize(("left", "right"), list(combinations(NAMESPACE_FIELDS, 2)))
def test_every_advisory_lock_namespace_collision_is_refused(left: str, right: str) -> None:
    values = settings().model_dump()
    values[left] = 9182
    values[right] = 9182
    with pytest.raises(ValidationError, match="pairwise distinct"):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize("reserved", [6, 7, 50])
def test_live_reservation_must_leave_a_global_slot_for_background(reserved: int) -> None:
    with pytest.raises(ValidationError, match="ARIEL_PERMIT_RESERVED_LIVE_SLOTS"):
        settings(
            ariel_global_max_concurrent_searches=6,
            ariel_permit_reserved_live_slots=reserved,
        )


def test_permit_unavailable_names_wait_and_both_caps_but_no_query() -> None:
    error = ArielPermitUnavailable(waited_seconds=30, instance_cap=3, global_cap=6)
    message = str(error)
    assert "30 seconds" in message
    assert "per-console cap 3" in message
    assert "global cap 6" in message
    assert "SELECT" not in message
    assert "AQL" not in message

