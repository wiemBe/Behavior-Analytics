"""IPS metric retention must exceed the baseline lookback, the same way WAF does."""

from __future__ import annotations

import pytest

from app.core.config import Settings, get_settings
from app.services.ips_retention import IpsRetention


def _base(**overrides: object) -> dict:
    values = get_settings().model_dump()
    values.update(overrides)
    return values


def test_startup_refuses_equality_when_collection_and_retention_are_on() -> None:
    with pytest.raises(
        ValueError,
        match=(
            r"IPS_METRIC_RETENTION_DAYS must exceed IPS_BASELINE_LOOKBACK_DAYS "
            r"\(35 <= 35\)"
        ),
    ):
        Settings(
            **_base(
                ips_collection_enabled=True,
                ips_retention_enabled=True,
                ips_metric_retention_days=35,
                ips_baseline_lookback_days=35,
            )
        )
    ok = Settings(
        **_base(
            ips_collection_enabled=True,
            ips_retention_enabled=True,
            ips_metric_retention_days=36,
            ips_baseline_lookback_days=35,
        )
    )
    assert ok.ips_metric_retention_days == 36


def test_collection_off_is_not_refused() -> None:
    settings = Settings(
        **_base(
            ips_collection_enabled=False,
            ips_retention_enabled=True,
            ips_metric_retention_days=2,
            ips_baseline_lookback_days=28,
        )
    )
    assert settings.ips_collection_enabled is False


def test_retention_off_is_not_refused() -> None:
    settings = Settings(
        **_base(
            ips_collection_enabled=True,
            ips_retention_enabled=False,
            ips_metric_retention_days=2,
            ips_baseline_lookback_days=60,
        )
    )
    assert settings.ips_retention_enabled is False


@pytest.mark.asyncio
async def test_runtime_skips_even_when_collection_is_off() -> None:
    settings = Settings(
        **_base(
            ips_collection_enabled=False,
            ips_retention_enabled=True,
            ips_metric_retention_days=28,
            ips_baseline_lookback_days=28,
        )
    )
    report = await IpsRetention(None, settings=settings).run()  # type: ignore[arg-type]
    assert report.deleted == {}
    assert report.skipped_reason == (
        "IPS_METRIC_RETENTION_DAYS does not exceed IPS_BASELINE_LOOKBACK_DAYS; "
        "nothing deleted"
    )


@pytest.mark.asyncio
async def test_disabled_retention_wins_over_the_lookback_skip() -> None:
    settings = Settings(
        **_base(
            ips_collection_enabled=False,
            ips_retention_enabled=False,
            ips_metric_retention_days=2,
            ips_baseline_lookback_days=60,
        )
    )
    report = await IpsRetention(None, settings=settings).run()  # type: ignore[arg-type]
    assert report.deleted == {}
    assert report.skipped_reason == "IPS retention is disabled"
