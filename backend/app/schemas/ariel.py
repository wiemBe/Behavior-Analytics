"""Ariel permit-pool responses. Counts only; no query text."""

from pydantic import BaseModel


class ArielBudgetOut(BaseModel):
    """Global Ariel permit pool. Counts only; no query text.

    ``global_held`` and ``instance_held`` are null when the budget is not
    enforced. ``instance_held`` is this console's count when the budget has a
    console, and every console-pool lock otherwise. The status route uses a
    global-only budget, so ``instance_held`` is that aggregate and can exceed
    ``instance_cap``.
    """

    enforced: bool
    global_cap: int
    instance_cap: int
    reserved_live_slots: int
    global_held: int | None = None
    instance_held: int | None = None
    permit_wait_seconds: float
