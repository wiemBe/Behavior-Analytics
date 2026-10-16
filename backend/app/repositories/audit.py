"""Append-only audit logging for administrative and search actions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import AuditLog
from app.security.redaction import redact


async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    actor: str | None,
    object_type: str | None = None,
    object_id: str | None = None,
    outcome: str = "SUCCESS",
    detail: dict | None = None,
    source_ip: str | None = None,
    actor_id: uuid.UUID | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        created_at=datetime.now(UTC),
        actor=actor[:255] if actor is not None else None,
        actor_id=actor_id,
        action=action,
        object_type=object_type,
        object_id=str(object_id) if object_id is not None else None,
        outcome=outcome,
        source_ip=source_ip[:64] if source_ip is not None else None,
        user_agent=user_agent[:512] if user_agent is not None else None,
        detail=redact(detail or {}),
    )
    session.add(entry)
    await session.flush()
    return entry


def new_correlation_id() -> str:
    return uuid.uuid4().hex
