"""A counting stand-in for ``ArielPermitBudget`` in executor, scheduler and route tests.

It has the budget's public surface that callers use (``permit()``, the caps and the
wait), holds a real ``asyncio.Semaphore`` per cap so concurrency is bounded the way
the permit bounds it, and records how many permits were held and at most how many at
once. ``refuse=True`` models a full budget: ``permit()`` yields ``None`` at once.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.services.ariel_permit import ArielPermit, ArielPermitCounts


class CountingBudget:
    def __init__(
        self,
        *,
        global_limit: int = 4,
        instance_limit: int = 2,
        refuse: bool = False,
        wait_seconds: float = 30.0,
        reported_held: int | None = None,
    ) -> None:
        self.global_limit = global_limit
        self.instance_limit = instance_limit
        self.wait_seconds = wait_seconds
        self.refuse = refuse
        self.reported_held = reported_held
        self._global = asyncio.Semaphore(global_limit)
        self.held = 0
        self.peak = 0
        self.acquired = 0
        self.refused = 0

    @asynccontextmanager
    async def permit(self) -> AsyncIterator[ArielPermit | None]:
        if self.refuse:
            self.refused += 1
            yield None
            return
        async with self._global:
            self.held += 1
            self.acquired += 1
            self.peak = max(self.peak, self.held)
            try:
                yield ArielPermit(global_slot=0, instance_slot=None, waited_seconds=0.0)
            finally:
                self.held -= 1

    async def active_permits(self, session) -> ArielPermitCounts | None:  # type: ignore[no-untyped-def]
        if self.reported_held is None:
            return None
        return ArielPermitCounts(
            global_held=self.reported_held,
            instance_held=0,
            global_cap=self.global_limit,
            instance_cap=self.instance_limit,
            reserved_live_slots=0,
        )
