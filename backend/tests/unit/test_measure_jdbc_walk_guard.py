"""Guard for the destructive apply step of measure_jdbc_walk (unit, no DB work)."""

from __future__ import annotations

import sys

import pytest

from scripts.measure_jdbc_walk import DROP_FLAG, GuardResult, check_apply_guard

TEST_DB = "postgresql+asyncpg://test:test@localhost:5433/qradar_obs_test"
NON_TEST_DB = "postgresql+asyncpg://app:pw@db.internal:5432/qradar_obs_prod"


@pytest.mark.parametrize(
    ("url", "flag", "expected"),
    [
        (NON_TEST_DB, True, GuardResult.REFUSE),   # non-test name: flag never overrides it
        (NON_TEST_DB, False, GuardResult.REFUSE),
        (TEST_DB, False, GuardResult.SKIP),        # test name, no flag: skipped with a reason
        (TEST_DB, True, GuardResult.ALLOW),
        (None, True, GuardResult.SKIP),            # unset URL: skipped, never DATABASE_URL
    ],
)
def test_apply_guard(
    url: str | None, flag: bool, expected: GuardResult, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard reads TEST_DATABASE_URL and the flag from argvs; it touches no DB."""
    monkeypatch.setattr(sys, "argv", ["measure_jdbc_walk.py"] + ([DROP_FLAG] if flag else []))
    if url is None:
        monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("TEST_DATABASE_URL", url)

    verdict, message = check_apply_guard()

    assert verdict is expected
    assert bool(message) is (expected is not GuardResult.ALLOW)
