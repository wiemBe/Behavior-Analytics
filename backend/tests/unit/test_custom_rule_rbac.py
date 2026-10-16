"""Custom-rule permissions and the detection-engineer role (design §F.11, D-17)."""

from __future__ import annotations

from app.security.rbac import (
    PERM_CUSTOM_RULE_READ,
    PERM_CUSTOM_RULE_WRITE,
    PERM_SEARCH_WRITE,
    Principal,
)
from app.services.seed import _ROLES


def test_constants_name_the_capabilities() -> None:
    assert PERM_CUSTOM_RULE_READ == "read:custom_rules"
    assert PERM_CUSTOM_RULE_WRITE == "custom_rule:write"


def test_read_star_grants_read_and_not_write() -> None:
    reader = Principal(subject="reader", permissions=frozenset({"read:*"}))
    assert reader.has(PERM_CUSTOM_RULE_READ)
    assert not reader.has(PERM_CUSTOM_RULE_WRITE)


def test_admin_star_grants_the_write_without_the_string() -> None:
    """``admin:*`` satisfies every permission, so the admin role does not list it."""
    assert "custom_rule:write" not in _ROLES["admin"]
    admin = Principal(subject="admin", permissions=frozenset(_ROLES["admin"]))
    assert admin.has(PERM_CUSTOM_RULE_WRITE)
    assert admin.has(PERM_CUSTOM_RULE_READ)


def test_the_write_does_not_imply_the_read() -> None:
    writer = Principal(subject="writer", permissions=frozenset({PERM_CUSTOM_RULE_WRITE}))
    assert writer.has(PERM_CUSTOM_RULE_WRITE)
    assert not writer.has(PERM_CUSTOM_RULE_READ)


def test_detection_engineer_is_an_analyst_plus_the_two_writes() -> None:
    analyst = set(_ROLES["analyst"])
    engineer = set(_ROLES["detection_engineer"])
    assert analyst <= engineer
    assert PERM_SEARCH_WRITE in engineer
    assert PERM_CUSTOM_RULE_WRITE in engineer
    assert "admin:*" not in engineer
    held = Principal(subject="detection_engineer", permissions=frozenset(engineer))
    assert held.has(PERM_CUSTOM_RULE_READ)
    assert held.has(PERM_CUSTOM_RULE_WRITE)
    assert held.has(PERM_SEARCH_WRITE)
    assert not held.has("admin:*")
    assert not held.has("admin:outage")
    assert not held.has("admin:waf")
    assert not held.has("admin:dashboards")
    assert not held.has("admin:users")
    assert not held.has("admin:auth")
    plain = Principal(subject="analyst", permissions=frozenset(analyst))
    assert not plain.has(PERM_CUSTOM_RULE_WRITE)
    assert not plain.has(PERM_SEARCH_WRITE)


def test_the_viewer_holds_neither_write() -> None:
    viewer = Principal(subject="viewer", permissions=frozenset(_ROLES["viewer"]))
    assert viewer.has(PERM_CUSTOM_RULE_READ)
    assert not viewer.has(PERM_CUSTOM_RULE_WRITE)
    assert not viewer.has(PERM_SEARCH_WRITE)
