"""No `app/` source may use Starlette's deprecated 422 status name.

Starlette renamed `HTTP_422_UNPROCESSABLE_ENTITY` to
`HTTP_422_UNPROCESSABLE_CONTENT`; the old alias now emits a
`StarletteDeprecationWarning` on every use, and when it is removed the name
stops resolving at import time — so a route that still uses it takes the whole
application down, not just one endpoint. The status code itself (422) is
unchanged.

This walks the source rather than the seven route files that were fixed,
because the point is to catch the next one. Only `app/` is scanned: Starlette's
own `status.py` keeps the alias on purpose, in its deprecation map.
"""

from __future__ import annotations

import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[2] / "app"

#: The deprecated spelling. A substring test is enough: the replacement name
#: `HTTP_422_UNPROCESSABLE_CONTENT` does not contain it.
DEPRECATED = "HTTP_422_UNPROCESSABLE_ENTITY"

#: The replacement spelling this rename must have produced instead.
REPLACEMENT = "HTTP_422_UNPROCESSABLE_CONTENT"

ALL_SOURCES = sorted(APP.rglob("*.py"))


def _deprecated_uses(path: pathlib.Path) -> list[int]:
    """The line numbers in `path` that mention the deprecated name."""
    return [
        number
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if DEPRECATED in line
    ]


@pytest.mark.parametrize("path", ALL_SOURCES, ids=lambda p: str(p.relative_to(APP)))
def test_app_does_not_use_the_deprecated_422_name(path: pathlib.Path) -> None:
    hits = _deprecated_uses(path)
    assert not hits, (
        f"{path.relative_to(APP)} uses Starlette's deprecated {DEPRECATED} on "
        f"line(s) {hits}; use {REPLACEMENT} instead, which is the same 422"
    )


def test_the_app_really_is_on_the_replacement_name() -> None:
    """A rename cannot pass by deleting the code: the new name must be in use."""
    users = [p for p in ALL_SOURCES if REPLACEMENT in p.read_text()]
    assert users, (
        f"no source under {APP} uses {REPLACEMENT}; the rename has been undone or "
        "the scan is looking in the wrong place"
    )


def test_the_scan_detects_a_planted_use(tmp_path: pathlib.Path) -> None:
    """The scanner must actually find something, or it proves nothing."""
    sample = tmp_path / "sample.py"
    sample.write_text(f"from fastapi import status\nCODE = status.{DEPRECATED}\n")
    assert _deprecated_uses(sample) == [2]
    assert DEPRECATED not in REPLACEMENT
