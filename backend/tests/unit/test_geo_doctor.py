"""The doctor's geography source line (GEO-3)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.cli import doctor as doctor_module
from app.cli.doctor import Doctor, Status, render_text
from app.core.config import Settings
from app.providers.base import ProviderError
from app.providers.mock import MockQRadarProvider

KEY = "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A="


class _Maker:
    def __call__(self) -> Any:  # pragma: no cover - the check reads consoles via the patch
        raise AssertionError("the session is not used")


def doctor(
    monkeypatch: pytest.MonkeyPatch,
    *,
    consoles: list[str] | BaseException,
    provider: Any = None,
    **overrides: Any,
) -> Doctor:
    settings = Settings(encryption_key=KEY, **overrides)
    built = Doctor(
        settings=settings,
        sessionmaker=cast(Any, _Maker()),
        provider_factory=lambda _instance: provider or MockQRadarProvider(seed=1),
    )
    if isinstance(consoles, BaseException):
        monkeypatch.setattr(built, "_enabled_instances", AsyncMock(side_effect=consoles))
    else:
        monkeypatch.setattr(
            built,
            "_enabled_instances",
            AsyncMock(return_value=[SimpleNamespace(name=name) for name in consoles]),
        )
    return built


def test_the_check_is_registered_with_its_runbook() -> None:
    assert ("geo_country_source", "OPS-R30") in doctor_module._CHECK_RUNBOOKS


@pytest.mark.asyncio
async def test_property_present_passes_and_names_the_property_and_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await doctor(monkeypatch, consoles=["lab"]).check_geo_country_source()
    assert result.status is Status.PASS
    assert render_text([result]) == (
        'PASS  geo_country_source property; SOURCE "Source Geographic Location" present on lab; '
        "DESTINATION not collected (no property)"
    )


@pytest.mark.asyncio
async def test_property_absent_on_a_console_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    result = await doctor(
        monkeypatch, consoles=["lab"], geo_source_country_property="Missing Country"
    ).check_geo_country_source()
    assert result.status is Status.WARN
    assert result.runbook == "OPS-R30"
    assert 'SOURCE "Missing Country" absent on lab' in result.message


@pytest.mark.asyncio
async def test_a_destination_property_is_checked_too(monkeypatch: pytest.MonkeyPatch) -> None:
    result = await doctor(
        monkeypatch, consoles=["lab"], geo_destination_country_property="Source Geographic Location"
    ).check_geo_country_source()
    assert result.status is Status.PASS
    assert 'DESTINATION "Source Geographic Location" present on lab' in result.message


@pytest.mark.asyncio
async def test_an_unreadable_property_list_fails_without_the_error_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = MockQRadarProvider(seed=1)
    provider.list_custom_properties = AsyncMock(  # type: ignore[method-assign]
        side_effect=ProviderError("token=planted-secret")
    )
    result = await doctor(
        monkeypatch, consoles=["lab"], provider=provider
    ).check_geo_country_source()
    assert result.status is Status.FAIL
    assert "could not be read on lab (ProviderError)" in result.message
    assert "planted-secret" not in result.message


@pytest.mark.asyncio
async def test_no_property_at_all_warns_that_nothing_is_collected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await doctor(
        monkeypatch, consoles=["lab"], geo_source_country_property=""
    ).check_geo_country_source()
    assert result.status is Status.WARN
    assert result.message == (
        "property; SOURCE not collected (no property); DESTINATION not collected (no property)"
    )


@pytest.mark.asyncio
async def test_no_enabled_console_fails_and_unreadable_consoles_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    none = await doctor(monkeypatch, consoles=[]).check_geo_country_source()
    assert none.status is Status.FAIL
    broken = await doctor(monkeypatch, consoles=RuntimeError("x")).check_geo_country_source()
    assert broken.status is Status.FAIL
    assert "RuntimeError" in broken.message


@pytest.mark.asyncio
async def test_lookup_mode_and_a_disabled_family(monkeypatch: pytest.MonkeyPatch) -> None:
    lookup = await doctor(
        monkeypatch, consoles=[], geo_country_source="lookup"
    ).check_geo_country_source()
    assert (lookup.status, lookup.message) == (
        Status.PASS,
        "lookup; SOURCE and DESTINATION use GEO::LOOKUP",
    )
    off = await doctor(
        monkeypatch, consoles=[], geo_collection_enabled=False
    ).check_geo_country_source()
    assert off.status is Status.SKIP


@pytest.mark.asyncio
async def test_only_enabled_directions_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    result = await doctor(
        monkeypatch, consoles=["lab"], geo_directions=["SOURCE"]
    ).check_geo_country_source()
    assert "DESTINATION" not in result.message
