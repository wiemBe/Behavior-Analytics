"""Pure contract tests for the platform-wide Ariel permit budget."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
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


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _Conn:
    def __init__(
        self, answers: list[bool] | None = None, *, always: bool | None = None
    ) -> None:
        self.answers = list(answers or [])
        self.always = always

    async def execution_options(self, **_kwargs: object) -> None:
        return None

    async def scalar(self, _statement: object) -> bool:
        if self.always is not None:
            return self.always
        return self.answers.pop(0)

    async def execute(self, _statement: object) -> None:
        return None

    async def invalidate(self) -> None:
        return None


class _Connect:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _Conn:
        return self.conn

    async def __aexit__(self, *_args: object) -> bool:
        return False


class _Engine:
    def __init__(self, conn: _Conn, *, name: str = "postgresql") -> None:
        self.dialect = SimpleNamespace(name=name)
        self._conn = conn

    def connect(self) -> _Connect:
        return _Connect(self._conn)


def _logging_budget(
    engine: _Engine,
    clock: _Clock,
    sleep: Callable[[float], Awaitable[None]],
    **overrides: object,
) -> ArielPermitBudget:
    return ArielPermitBudget(
        cast(AsyncEngine, engine),
        settings(
            ariel_global_max_concurrent_searches=overrides.pop("global_limit", 2),
            ariel_max_concurrent_searches=overrides.pop("instance_limit", 2),
            ariel_permit_reserved_live_slots=overrides.pop("reserved", 0),
            ariel_permit_wait_seconds=overrides.pop("wait", 5.0),
            ariel_permit_poll_seconds=overrides.pop("poll", 1.0),
        ),
        instance_id=None,
        sleep=sleep,
        monotonic=clock,
    )


def _advance(clock: _Clock) -> Callable[[float], Awaitable[None]]:
    async def sleep(seconds: float) -> None:
        clock.now += seconds

    return sleep


@pytest.mark.asyncio
async def test_a_grant_that_waited_at_least_one_second_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    clock = _Clock()
    # Two misses, then a grant on the next poll: waited_seconds is exactly 1.
    engine = _Engine(_Conn([False, False, True]))
    budget = _logging_budget(engine, clock, _advance(clock), global_limit=2)
    with caplog.at_level(logging.INFO, logger="app.services.ariel_permit"):
        async with budget.permit() as permit:
            assert permit is not None
            assert permit.waited_seconds == 1.0
            assert permit.instance_slot is None
    granted = [
        record
        for record in caplog.records
        if record.message == "Ariel permit granted after a wait"
    ]
    assert len(granted) == 1
    assert granted[0].levelname == "INFO"
    assert granted[0].waited_seconds == 1.0
    assert granted[0].priority == "live"
    assert granted[0].global_slot in (0, 1)
    assert granted[0].instance_slot is None
    assert not hasattr(granted[0], "family")


@pytest.mark.asyncio
async def test_a_fast_grant_and_a_short_wait_are_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    clock = _Clock()
    fast = _logging_budget(
        _Engine(_Conn([True])), clock, _advance(clock), global_limit=1
    )
    with caplog.at_level(logging.INFO, logger="app.services.ariel_permit"):
        async with fast.permit() as permit:
            assert permit is not None and permit.waited_seconds == 0.0
    clock.now = 0.0
    short = _logging_budget(
        _Engine(_Conn([False, True])),
        clock,
        _advance(clock),
        global_limit=1,
        poll=0.5,
    )
    with caplog.at_level(logging.INFO, logger="app.services.ariel_permit"):
        async with short.permit() as permit:
            assert permit is not None and permit.waited_seconds == 0.5
    assert [
        record.message
        for record in caplog.records
        if record.name == "app.services.ariel_permit"
    ] == []


@pytest.mark.asyncio
async def test_a_refusal_logs_the_class_the_wait_and_both_caps(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    clock = _Clock()
    budget = _logging_budget(
        _Engine(_Conn(always=False)),
        clock,
        _advance(clock),
        global_limit=4,
        instance_limit=2,
        wait=1.0,
        poll=1.0,
    )
    with (
        caplog.at_level(logging.INFO, logger="app.services.ariel_permit"),
        ariel_background_priority(),
    ):
        async with budget.permit() as permit:
            assert permit is None
    refused = [
        record for record in caplog.records if record.message == "Ariel permit refused"
    ]
    assert len(refused) == 1
    assert refused[0].levelname == "WARNING"
    assert refused[0].priority == "background"
    assert refused[0].waited_seconds == 1.0
    assert refused[0].instance_cap == 2
    assert refused[0].global_cap == 4
    assert not hasattr(refused[0], "family")
    assert _ARIEL_PRIORITY.get() == "live"


@pytest.mark.asyncio
async def test_an_unenforced_permit_and_an_acquire_error_are_not_refusals(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    clock = _Clock()

    class _Broken(_Conn):
        async def scalar(self, _statement: object) -> bool:
            raise RuntimeError("SELECT planted-query")

    with caplog.at_level(logging.INFO, logger="app.services.ariel_permit"):
        open_budget = _logging_budget(
            _Engine(_Conn(), name="sqlite"), clock, _advance(clock)
        )
        async with open_budget.permit() as permit:
            assert permit is not None and permit.waited_seconds == 0.0
        broken = _logging_budget(_Engine(_Broken()), clock, _advance(clock))
        with pytest.raises(RuntimeError, match="planted-query"):
            async with broken.permit():
                raise AssertionError("a failed acquire must not grant")
    assert [
        record.message
        for record in caplog.records
        if record.name == "app.services.ariel_permit"
    ] == []
    assert "planted-query" not in caplog.text

