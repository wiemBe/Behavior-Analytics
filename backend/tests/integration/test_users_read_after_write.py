"""T-132 item 7: `GET /api/v1/users` straight after a sign-in, a create and a password change.

OPS-10 and OPS-11 each saw one `GET /api/v1/users` right after a sign-in return a body that was
not the user list. These tests hammer the same sequences and record every response that is not a
200 with a list holding the expected users. The rate limiter is reset on every iteration so a
429 (a non-list body by design, 120 requests a minute per address) cannot hide another cause.
"""

from __future__ import annotations

import pytest

from app.core.rate_limit import limiter
from tests.integration.test_auth_api import (  # noqa: F401  (fixtures and helpers)
    ORIGIN,
    PASSWORD,
    _add_user,
    _login,
    _reset_rate_limit,
    api,
)

pytestmark = pytest.mark.integration

ITERATIONS = 2


def _bad(response, expected: set[str]) -> str | None:  # type: ignore[no-untyped-def]
    if response.status_code != 200:
        return f"{response.status_code} {response.text[:120]}"
    body = response.json()
    if not isinstance(body, list):
        return f"non-list {str(body)[:120]}"
    names = {row.get("username") for row in body}
    if not expected <= names:
        return f"missing {sorted(expected - names)}"
    return None


async def test_users_list_right_after_sign_in_create_and_password_change(api) -> None:  # noqa: F811
    client, maker = api
    await _add_user(maker, "ada", roles=["admin"])
    problems: list[str] = []
    expected = {"ada"}

    for i in range(ITERATIONS):
        limiter.reset()
        client.cookies.clear()
        login = _login(client, "ada", PASSWORD)
        assert login.status_code == 200, login.text
        if (found := _bad(client.get("/api/v1/users"), expected)) is not None:
            problems.append(f"after sign-in #{i}: {found}")

        name = f"user{i}"
        created = client.post(
            "/api/v1/users",
            json={"username": name, "auth_source": "LOCAL", "password": PASSWORD,
                  "roles": ["viewer"]},
        )
        assert created.status_code == 201, created.text
        expected.add(name)
        if (found := _bad(client.get("/api/v1/users"), expected)) is not None:
            problems.append(f"after create #{i}: {found}")

    new_password = PASSWORD + "-changed"
    for i, (current, new) in enumerate(
        [(PASSWORD, new_password), (new_password, PASSWORD)] * (ITERATIONS // 2)
    ):
        limiter.reset()
        changed = client.post(
            "/api/v1/auth/password", json={"current_password": current, "new_password": new}
        )
        assert changed.status_code == 200, changed.text
        if (found := _bad(client.get("/api/v1/users"), expected)) is not None:
            problems.append(f"after password change #{i}: {found}")

    assert problems == []
