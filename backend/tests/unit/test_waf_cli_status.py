"""The WAF status line says unknown when consecutive failures were not collected."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.cli.waf import main
from app.schemas.waf import WafArielBudgetOut, WafSourceStatusOut


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _status(failures: int | None) -> WafSourceStatusOut:
    return WafSourceStatusOut.model_construct(
        qradar_log_source_id=312,
        log_source_name="Portal WAF",
        profile_version=2,
        validation_status="VALID",
        collection_eligible=True,
        lag_seconds=30,
        consecutive_failures=failures,
        active_endpoints=4,
        ariel_searches_last_run=1,
        last_collection_duration_ms=12,
        previous_run_skipped=False,
        last_skip_reason=None,
        ineligible_reason=None,
    )


def _print_status(monkeypatch: pytest.MonkeyPatch, failures: int | None) -> None:
    async def statuses(*_args: object, **_kwargs: object) -> list[WafSourceStatusOut]:
        return [_status(failures)]

    async def budget(*_args: object, **_kwargs: object) -> WafArielBudgetOut:
        return WafArielBudgetOut(
            enforced=False,
            max_concurrent_sources=2,
            active_permits=None,
            permit_wait_seconds=5,
        )

    monkeypatch.setattr("app.cli.waf.get_sessionmaker", lambda: lambda: _Session())
    monkeypatch.setattr("app.cli.waf.waf_source_statuses", statuses)
    monkeypatch.setattr("app.cli.waf.waf_ariel_budget_status", budget)
    monkeypatch.setattr("app.cli.waf.get_settings", lambda: Mock())
    assert main(["status"]) == 0


@pytest.mark.parametrize(
    ("failures", "printed"),
    [(None, "failures=unknown"), (0, "failures=0"), (3, "failures=3")],
)
def test_status_prints_unknown_only_when_failures_were_not_collected(
    failures: int | None,
    printed: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _print_status(monkeypatch, failures)
    out = capsys.readouterr().out
    assert printed in out
    assert "failures=None" not in out
    if failures is None:
        assert "failures=0" not in out
