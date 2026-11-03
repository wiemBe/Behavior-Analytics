"""Stored alert e-mail templates and recipients (MAIL-1)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email import EmailRecipient, EmailTemplate


async def list_templates(session: AsyncSession) -> Sequence[EmailTemplate]:
    """Every stored template row: the default (alert_type NULL) first."""
    return (
        await session.scalars(
            select(EmailTemplate).order_by(
                EmailTemplate.alert_type.is_not(None), EmailTemplate.alert_type
            )
        )
    ).all()


async def get_template(session: AsyncSession, alert_type: str | None) -> EmailTemplate | None:
    """The row for one type; ``None`` selects the default row."""
    condition = (
        EmailTemplate.alert_type.is_(None)
        if alert_type is None
        else EmailTemplate.alert_type == alert_type
    )
    return await session.scalar(select(EmailTemplate).where(condition))


async def list_recipients(session: AsyncSession) -> Sequence[EmailRecipient]:
    return (await session.scalars(select(EmailRecipient).order_by(EmailRecipient.address))).all()


async def get_recipient(session: AsyncSession, recipient_id: uuid.UUID) -> EmailRecipient | None:
    return await session.get(EmailRecipient, recipient_id)


async def get_recipient_by_address(session: AsyncSession, address: str) -> EmailRecipient | None:
    return await session.scalar(select(EmailRecipient).where(EmailRecipient.address == address))
