"""Liveness and readiness endpoints for container health checks."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.api.deps import SessionDep
from app.core.version import migration_head

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    """Process is up. No dependencies checked — used as the container liveness
    probe so a slow DB does not trigger a restart loop."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(session: SessionDep) -> dict[str, str]:
    """Ready to serve traffic: database reachable and at the image's migration head."""
    expected = migration_head()
    try:
        await session.execute(text("SELECT 1"))
        result = await session.execute(text("SELECT version_num FROM alembic_version"))
        rows = result.all()
        if not rows:
            raise HTTPException(
                status_code=503,
                detail=f"database revision missing; image head is {expected or 'unknown'}",
            )
        db_revision = rows[0][0] if rows[0] else ""
        if expected is None:
            raise HTTPException(
                status_code=503,
                detail="image has no Alembic head; cannot verify readiness",
            )
        if db_revision != expected:
            raise HTTPException(
                status_code=503,
                detail=f"database revision {db_revision} does not match image head {expected}",
            )
        return {"status": "ready", "database": "ok", "revision": expected}
    except HTTPException:
        raise
    except Exception:
        # Never leak the query error text.
        raise HTTPException(
            status_code=503, detail="database readiness check failed"
        ) from None
