"""History-backfill permissions: `read:*` reads, only `admin:*` administers."""

from __future__ import annotations

from app.security.rbac import (
    PERM_HISTORY_BACKFILL_ADMIN,
    PERM_HISTORY_BACKFILL_READ,
    Principal,
)
from app.services.seed import _ROLES


def test_constants_name_the_capabilities() -> None:
    assert PERM_HISTORY_BACKFILL_READ == "read:history_backfill"
    assert PERM_HISTORY_BACKFILL_ADMIN == "admin:history_backfill"


def test_read_star_grants_the_read_and_not_the_admin_permission() -> None:
    reader = Principal(subject="reader", permissions=frozenset({"read:*"}))
    assert reader.has(PERM_HISTORY_BACKFILL_READ)
    assert not reader.has(PERM_HISTORY_BACKFILL_ADMIN)


def test_admin_star_grants_both() -> None:
    admin = Principal(subject="admin", permissions=frozenset({"admin:*"}))
    assert admin.has(PERM_HISTORY_BACKFILL_READ)
    assert admin.has(PERM_HISTORY_BACKFILL_ADMIN)


def test_the_seeded_admin_role_has_both_without_listing_the_strings() -> None:
    assert PERM_HISTORY_BACKFILL_ADMIN not in _ROLES["admin"]
    admin = Principal(subject="admin", permissions=frozenset(_ROLES["admin"]))
    assert admin.has(PERM_HISTORY_BACKFILL_READ) and admin.has(PERM_HISTORY_BACKFILL_ADMIN)


def test_a_role_with_neither_has_neither() -> None:
    other = Principal(subject="x", permissions=frozenset({"read:offenses", "rule:write"}))
    assert not other.has(PERM_HISTORY_BACKFILL_READ)
    assert not other.has(PERM_HISTORY_BACKFILL_ADMIN)
    assert not Principal(subject="nobody").has(PERM_HISTORY_BACKFILL_READ)


def test_the_specific_grants_do_not_cross() -> None:
    reader = Principal(subject="r", permissions=frozenset({PERM_HISTORY_BACKFILL_READ}))
    assert reader.has(PERM_HISTORY_BACKFILL_READ) and not reader.has(PERM_HISTORY_BACKFILL_ADMIN)
    admin_only = Principal(subject="a", permissions=frozenset({PERM_HISTORY_BACKFILL_ADMIN}))
    assert admin_only.has(PERM_HISTORY_BACKFILL_ADMIN)
    assert not admin_only.has(PERM_HISTORY_BACKFILL_READ)
