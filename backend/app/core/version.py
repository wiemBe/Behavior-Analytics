"""The application version, the build SHA and the shipped migration head.

``VERSION`` is the one source of truth (docs/OPERATIONS-DESIGN.md section 1). Lookup order:

1. ``/app/VERSION``, i.e. ``VERSION`` one level above the ``app`` package. This is where the
   image carries it.
2. ``VERSION`` at the repository root, two levels above the ``app`` package, in a checkout.

The first file that exists wins. A missing or malformed file raises ``VersionError`` at
startup. There is no fallback string: a made-up version is worse than a refused start.
"""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from pathlib import Path

from alembic.script import ScriptDirectory

logger = logging.getLogger("app")

_APP_PACKAGE = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _APP_PACKAGE.parent
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
_SHA = re.compile(r"^[0-9a-f]{7,64}$")


class VersionError(RuntimeError):
    """``VERSION`` is missing or is not one SemVer line."""


def candidate_paths() -> list[Path]:
    return [_BACKEND_DIR / "VERSION", _BACKEND_DIR.parent / "VERSION"]


def parse_version(text: str, source: str = "VERSION") -> str:
    lines = text.splitlines()
    if len(lines) != 1 or not _SEMVER.match(lines[0]):
        raise VersionError(f"{source} must hold exactly one SemVer line such as 0.1.0")
    return lines[0]


def read_version(paths: list[Path] | None = None) -> str:
    for path in paths if paths is not None else candidate_paths():
        if path.is_file():
            return parse_version(path.read_text(encoding="utf-8"), str(path))
    searched = ", ".join(str(p) for p in (paths if paths is not None else candidate_paths()))
    raise VersionError(f"VERSION file not found; looked in: {searched}")


def git_sha() -> str | None:
    """The build's commit from ``GIT_SHA``; ``None`` when unset or not a hex SHA."""
    raw = os.environ.get("GIT_SHA", "").strip().lower()
    return raw if _SHA.match(raw) else None


@lru_cache(maxsize=1)
def migration_head() -> str | None:
    """Head of ``alembic/versions`` as shipped. Not a database query."""
    try:
        directory = ScriptDirectory(str(_BACKEND_DIR / "alembic"))
        heads = directory.get_heads()
    except Exception:  # reported as unknown, never guessed
        logger.exception("could not read the alembic script directory")
        return None
    return heads[0] if len(heads) == 1 else None
