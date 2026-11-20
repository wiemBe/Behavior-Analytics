"""Provider factory policy, startup validation and the configurable poll budget."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.providers.base import ProviderUnavailableError
from app.providers.factory import build_provider, build_provider_for_instance
from app.providers.qradar_rest import QRadarRestProvider
from app.services import ariel_search_log

FIELDS = {
    "qradar_request_timeout_seconds": (60.0, 600),
    "qradar_connect_timeout_seconds": (10.0, 120),
    "qradar_page_size": (500, 1000),
    "qradar_max_pages": (200, 2000),
    "ariel_provider_poll_seconds": (120, 3600),
}


@pytest.mark.parametrize("field,limits", FIELDS.items())
def test_defaults_and_startup_bounds_name_only_the_field(field, limits, monkeypatch):
    default, maximum = limits
    assert getattr(Settings(_env_file=None), field) == default
    for invalid in (0, -1, maximum + 1, "sensitive-invalid-value"):
        monkeypatch.setenv(field.upper(), str(invalid))
        with pytest.raises(ValidationError) as caught:
            Settings(_env_file=None)
        assert {error["loc"] for error in caught.value.errors()} == {(field,)}
        assert "input_value" not in str(caught.value)
        assert "sensitive-invalid-value" not in str(caught.value)
    monkeypatch.setenv(field.upper(), str(maximum))
    assert getattr(Settings(_env_file=None), field) == maximum


@pytest.mark.parametrize("per_instance", [False, True])
@pytest.mark.parametrize("custom", [False, True])
async def test_both_factories_apply_every_setting_and_retry_policy(per_instance, custom):
    overrides = dict(qradar_request_timeout_seconds=17, qradar_connect_timeout_seconds=4,
                     qradar_page_size=23, qradar_max_pages=7, ariel_provider_poll_seconds=5,
                     ariel_max_retries=2, ariel_retry_base_seconds=0.3,
                     ariel_retry_max_seconds=9) if custom else {}
    settings = Settings(_env_file=None, qradar_provider="rest", qradar_host="https://qradar.test",
                        qradar_sec_token="test", **overrides)
    instance = SimpleNamespace(id=uuid.uuid4(), name="test", provider_kind="rest",
                               console_host="https://qradar.test", sec_token="test",
                               api_version="20.0", verify_ssl=True, ca_bundle_path=None)
    provider = (build_provider_for_instance(instance, settings)
                if per_instance else build_provider(settings))
    try:
        assert isinstance(provider, QRadarRestProvider)
        assert provider._client.timeout.read == (17 if custom else 60)
        assert provider._client.timeout.connect == (4 if custom else 10)
        assert provider._page_size == (23 if custom else 500)
        assert provider._max_pages == (7 if custom else 200)
        assert provider._ariel_poll_seconds == (5 if custom else 120)
        assert provider._max_retries == (2 if custom else 3)
        assert provider._retry_base == (0.3 if custom else 2.0)
        assert provider._retry_max == (9 if custom else 60.0)
    finally:
        await provider.aclose()


async def test_direct_constructor_defaults_are_unchanged():
    provider = QRadarRestProvider(base_url="https://qradar.test", sec_token="test")
    try:
        assert provider._client.timeout == httpx.Timeout(60, connect=10)
        assert (provider._page_size, provider._max_pages) == (500, 200)
        assert provider._ariel_poll_seconds == 120
        assert (provider._max_retries, provider._retry_base, provider._retry_max) == (3, 2, 30)
    finally:
        await provider.aclose()


@pytest.mark.parametrize("limit", [3, 125])
@pytest.mark.parametrize("probe", [False, True])
async def test_poll_limit_cancels_the_search_for_both_lifecycles(limit, probe):
    calls = []
    sleeps = []

    def handler(request):
        calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(201, json={"search_id": "poll"})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"status": "EXECUTE", "progress": 10})

    async def sleep(seconds):
        sleeps.append(seconds)

    client = httpx.AsyncClient(base_url="https://qradar.test/api",
                               transport=httpx.MockTransport(handler))
    provider = QRadarRestProvider(base_url="https://qradar.test", sec_token="test", client=client,
                                  sleep=sleep, ariel_poll_seconds=limit)
    start = datetime(2026, 10, 12, tzinfo=UTC)
    try:
        if probe:
            result = await provider.observe_property_rejection(qradar_log_source_id=1,
                window_start=start, window_end=start + timedelta(minutes=5),
                property_names=["test"],
            )
            assert result[0].outcome == "TIMED_OUT"
        else:
            with pytest.raises(ProviderUnavailableError):
                await provider.get_log_source_metrics(start, start + timedelta(minutes=5))
        assert calls == ["POST", *(["GET"] * limit), "DELETE"]
        assert sleeps == [1.0] * limit
    finally:
        await provider.aclose()


def test_longer_provider_poll_budget_keeps_its_load_record_current(monkeypatch):
    monkeypatch.setattr("app.core.config.get_settings", lambda: Settings(
        _env_file=None, ariel_provider_poll_seconds=3600,
    ))
    assert ariel_search_log.stale_after_seconds() == 3630
