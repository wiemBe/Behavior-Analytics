"""Build identity. Open like health: an operator who cannot sign in still needs it."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.version import git_sha, migration_head, read_version

router = APIRouter(tags=["version"])


class VersionOut(BaseModel):
    version: str
    git_sha: str | None
    migration_head: str | None


@router.get("/version", response_model=VersionOut)
async def get_version() -> VersionOut:
    return VersionOut(version=read_version(), git_sha=git_sha(), migration_head=migration_head())
