"""The seeded roles' dashboard grants: an analyst saves dashboards, a viewer only reads."""

from __future__ import annotations

from app.security.rbac import (
    PERM_DASHBOARD_READ,
    PERM_DASHBOARD_SHARE,
    PERM_DASHBOARD_WRITE,
    Principal,
)
from app.services.seed import _ROLES


def _principal(role: str) -> Principal:
    return Principal(subject=role, permissions=frozenset(_ROLES[role]))


def test_the_analyst_saves_dashboards_but_cannot_share_them() -> None:
    analyst = _principal("analyst")
    assert analyst.has(PERM_DASHBOARD_READ) and analyst.has(PERM_DASHBOARD_WRITE)
    assert not analyst.has(PERM_DASHBOARD_SHARE)


def test_the_viewer_only_reads_dashboards() -> None:
    viewer = _principal("viewer")
    assert viewer.has(PERM_DASHBOARD_READ)
    assert not viewer.has(PERM_DASHBOARD_WRITE) and not viewer.has(PERM_DASHBOARD_SHARE)
