"""Read the Ariel permit pool. Counts only; no query text."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.schemas.ariel import ArielBudgetOut
from app.services.ariel_permit import ArielPermitBudget


async def ariel_budget_status(
    session: AsyncSession, *, settings: Settings
) -> ArielBudgetOut:
    """Caps and permits held now, from one ``active_permits`` read.

    The budget is global-only: this process does not know which console a
    later search will name, and ``instance_held`` is every console-pool lock.
    """
    budget = ArielPermitBudget.for_session(session, settings, instance_id=None)
    counts = await budget.active_permits(session)
    return ArielBudgetOut(
        enforced=budget.enforced,
        global_cap=budget.global_limit,
        instance_cap=budget.instance_limit,
        reserved_live_slots=budget.reserved_live_slots,
        global_held=None if counts is None else counts.global_held,
        instance_held=None if counts is None else counts.instance_held,
        permit_wait_seconds=budget.wait_seconds,
    )
