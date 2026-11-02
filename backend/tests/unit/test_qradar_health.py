"""ARL-3: the `qradar` health section and the GET-only reads behind it.

Two layers. The REST provider is driven through a mocked httpx transport, so the verbs, the
single attempt and the parsing are asserted on real requests. The service is driven through the
mock provider with scripted answers and faults, so every field state, the cache and the
no-secret guarantee are asserted on the model a caller receives.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import ProviderKind, Settings, get_settings
from app.models.instance import QRadarInstance
from app.providers.base import (
    CapabilityNotSupportedError,
    ProviderAuthError,
    ProviderCapability,
    ProviderError,
    ProviderUnavailableError,
    QRadarProvider,
)
from app.providers.dto import (
    ArielSearchListDTO,
    DeploymentHostDTO,
    LicensePoolDTO,
    LicenseRateDTO,
    ManagedServerDTO,
)
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_mcp import QRadarMCPProvider
from app.providers.qradar_rest import QRadarRestProvider
from app.schemas.qradar_health import FieldState, HealthField
from app.services.qradar_health import (
    REASONS,
    ArielRunsSnapshot,
    HealthReadCache,
    read_qradar_health,
)

SEC_TOKEN = "s3cr3t-qradar-token-do-not-leak"
BASE_URL = "https://qradar.example.internal"
T0 = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)


# ---------------------------------------------------------------- REST provider


class _Sleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _rest(handler) -> tuple[QRadarRestProvider, _Sleep]:
    sleep = _Sleep()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=BASE_URL + "/api",
        headers={"SEC": SEC_TOKEN, "Version": "20.0", "Accept": "application/json"},
    )
    return (
        QRadarRestProvider(
            base_url=BASE_URL,
            sec_token=SEC_TOKEN,
            client=client,
            rng=random.Random(1),
            sleep=sleep,
            max_retries=3,
        ),
        sleep,
    )


_BODIES: dict[str, object] = {
    "/api/system/about": {"external_version": "7.6.0.0", "release_name": "7.6.0 FP1"},
    "/api/system/servers": [
        {"hostname": "qradarce2.local", "managed_host_id": 53, "server_id": 51, "status": "ACTIVE"}
    ],
    "/api/config/deployment/hosts": [
        {
            "hostname": "qradarce2",
            "status": "Active",
            "average_eps": 118,
            "peak_eps": 1916,
            "eps_allocation": 5000,
            "average_fpm": 0,
            "peak_fpm": 0,
            "fpm_allocation": 200000,
            "components": ["magistrate"],
            "private_ip": "192.168.122.50",
        }
    ],
    "/api/config/deployment/license_pool": {
        "fpm": {"total": 5000, "overallocated": True, "allocated": 200000},
        "eps": {"total": 100, "overallocated": True, "allocated": 5000},
    },
    "/api/ariel/searches": ["id-1", "id-2", "id-3"],
}


async def test_the_health_reads_are_get_only_and_each_is_tried_once() -> None:
    seen: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("Range")))
        return httpx.Response(200, json=_BODIES[request.url.path])

    provider, sleep = _rest(handler)
    try:
        assert provider.supports(ProviderCapability.HEALTH_READS)
        about = await provider.probe_about()
        servers = await provider.list_managed_servers()
        hosts = await provider.list_deployment_hosts()
        pool = await provider.get_license_pool()
        listed = await provider.count_ariel_searches()
    finally:
        await provider.aclose()

    assert {method for method, _path, _range in seen} == {"GET"}
    assert [path for _m, path, _r in seen] == list(_BODIES)
    assert about.version == "7.6.0.0"
    assert [(s.hostname, s.status) for s in servers] == [("qradarce2.local", "ACTIVE")]
    assert (hosts[0].average_eps, hosts[0].peak_eps, hosts[0].eps_allocation) == (118, 1916, 5000)
    assert pool.eps is not None and (pool.eps.total, pool.eps.overallocated) == (100, True)
    assert listed.held == 3 and not listed.truncated
    assert sleep.delays == []


@pytest.mark.parametrize(
    "call",
    ["probe_about", "list_managed_servers", "list_deployment_hosts", "get_license_pool",
     "count_ariel_searches"],
)
async def test_a_failing_health_read_is_not_retried(call: str) -> None:
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(503, json={"message": f"echo {SEC_TOKEN}"})

    provider, sleep = _rest(handler)
    try:
        with pytest.raises(ProviderUnavailableError) as raised:
            await getattr(provider, call)()
    finally:
        await provider.aclose()

    assert requests == 1
    assert sleep.delays == []
    assert raised.value.status_code == 503
    assert SEC_TOKEN not in str(raised.value)


@pytest.mark.parametrize("status", [401, 403])
async def test_an_auth_refusal_keeps_its_status(status: int) -> None:
    provider, _ = _rest(lambda _r: httpx.Response(status, json={}))
    try:
        with pytest.raises(ProviderAuthError) as raised:
            await provider.list_managed_servers()
    finally:
        await provider.aclose()
    assert raised.value.status_code == status


async def test_a_full_search_list_page_is_reported_truncated() -> None:
    provider, _ = _rest(lambda _r: httpx.Response(200, json=[f"id-{i}" for i in range(1000)]))
    try:
        listed = await provider.count_ariel_searches()
    finally:
        await provider.aclose()
    assert listed.held == 1000 and listed.truncated


async def test_a_non_list_search_list_is_malformed_not_zero() -> None:
    provider, _ = _rest(lambda _r: httpx.Response(200, json={"searches": []}))
    try:
        with pytest.raises(ProviderError):
            await provider.count_ariel_searches()
    finally:
        await provider.aclose()


async def test_mcp_does_not_offer_the_health_reads() -> None:
    mcp = QRadarMCPProvider(base_url="http://mcp.invalid", sec_token=SEC_TOKEN)
    try:
        assert not mcp.supports(ProviderCapability.HEALTH_READS)
        for call in (
            mcp.list_managed_servers,
            mcp.list_deployment_hosts,
            mcp.get_license_pool,
            mcp.count_ariel_searches,
        ):
            with pytest.raises(CapabilityNotSupportedError):
                await call()
    finally:
        await mcp.aclose()


def test_the_interface_gained_reads_only() -> None:
    added = {
        "probe_about",
        "list_managed_servers",
        "list_deployment_hosts",
        "get_license_pool",
        "count_ariel_searches",
    }
    for name in added:
        assert name.startswith(("probe_", "list_", "get_", "count_"))
    # No health read can even express a write: none takes a body, an id or a verb.
    for name in added:
        params = set(inspect.signature(getattr(QRadarProvider, name)).parameters) - {"self"}
        assert params == set(), name
    # The names the interface exposes beyond the pre-existing Ariel pair stay read-shaped.
    writes = {
        name
        for name, member in inspect.getmembers(QRadarProvider, inspect.isfunction)
        if name.startswith(("create_", "update_", "delete_", "put_", "post_", "patch_"))
    }
    assert writes == {"create_ariel_search"}


# -------------------------------------------------------------------- the service


def _instance(host: str = BASE_URL, ident: str = "00000000-0000-0000-0000-0000000003a1"):
    return QRadarInstance(
        id=uuid.UUID(ident),
        name="lab",
        console_host=host,
        api_version="20.0",
        sec_token=SEC_TOKEN,
        verify_ssl=True,
        provider_kind=ProviderKind.MOCK.value,
    )


def _settings(ttl: int = 30) -> Settings:
    return get_settings().model_copy(update={"qradar_health_cache_ttl_seconds": ttl})


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class _Run:
    """One `read_qradar_health` call with its own cache and clock."""

    def __init__(self, provider: MockQRadarProvider | None = None, ttl: int = 30) -> None:
        self.provider = provider or MockQRadarProvider()
        self.cache = HealthReadCache()
        self.clock = _Clock()
        self.ttl = ttl
        self.built = 0
        self.closed = 0
        self.runs: ArielRunsSnapshot | None = None

    def _factory(self, _instance: QRadarInstance) -> QRadarProvider:
        self.built += 1
        run = self
        provider = self.provider
        original = provider.aclose

        async def aclose() -> None:
            run.closed += 1
            await original()

        provider.aclose = aclose  # type: ignore[method-assign]
        return provider

    async def read(self, instance: QRadarInstance | None = None, **kw):
        return await read_qradar_health(
            instance or _instance(),
            settings=_settings(self.ttl),
            provider_factory=self._factory,
            ariel_runs=kw.pop("ariel_runs", self.runs),
            cache=self.cache,
            now=lambda: T0,
            monotonic=self.clock,
            **kw,
        )


def _unavailable(status: int | None = None) -> ProviderUnavailableError:
    return ProviderUnavailableError("upstream said " + SEC_TOKEN, status_code=status)


async def test_every_field_ok_and_a_sample_of_the_section(capsys) -> None:
    run = _Run()
    health = await run.read(
        ariel_runs=ArielRunsSnapshot(
            window_minutes=15,
            searches=40,
            unavailable=0,
            last_success_at=T0 - timedelta(seconds=20),
            in_flight=2,
        )
    )
    for name in ("reachable", "api_latency_ms", "version", "servers", "eps", "license"):
        assert getattr(health, name).state is FieldState.OK, name
    assert health.ariel.list_probe.state is FieldState.OK
    assert health.ariel.list_probe.value is not None and health.ariel.list_probe.value.held == 4
    assert health.ariel.available.value is True
    assert health.ariel.ours_in_flight.value == 2
    assert health.cache_ttl_seconds == 30 and health.cached is False
    assert health.as_of == T0
    assert health.servers.value is not None and health.servers.value.all_active
    assert health.eps.value is not None and health.eps.value.average_share == round(120 / 5000, 4)
    with capsys.disabled():
        print("\nSAMPLE " + json.dumps(json.loads(health.model_dump_json()), indent=2))


async def test_forbidden_is_a_state_of_the_field_not_of_the_section() -> None:
    provider = MockQRadarProvider()
    provider.health_faults["list_managed_servers"] = ProviderAuthError("no", status_code=403)
    health = await _Run(provider).read()
    assert health.servers.state is FieldState.FORBIDDEN
    assert health.servers.value is None and health.servers.reason_code == "forbidden"
    assert health.eps.state is FieldState.OK
    assert health.reachable.value is True


async def test_a_rejected_token_is_failed_not_forbidden() -> None:
    provider = MockQRadarProvider()
    provider.health_faults["probe_about"] = ProviderAuthError("no", status_code=401)
    health = await _Run(provider).read()
    assert health.api_latency_ms.state is FieldState.FAILED
    assert health.api_latency_ms.reason_code == "unauthorized"
    # An HTTP answer came back, so the console is reachable even though the read failed.
    assert health.reachable.value is True


async def test_an_unreachable_console_skips_the_other_reads() -> None:
    provider = MockQRadarProvider()
    provider.health_faults["probe_about"] = _unavailable()
    health = await _Run(provider).read()
    assert health.reachable.state is FieldState.OK and health.reachable.value is False
    assert health.reachable.reason_code == "unreachable"
    assert health.api_latency_ms.state is FieldState.FAILED
    for field in (health.servers, health.eps, health.license, health.ariel.list_probe):
        assert field.state is FieldState.NOT_COLLECTED
        assert field.reason_code == "console_unreachable"
        assert field.value is None
    assert provider.health_calls == {"probe_about": 1}


async def test_ariel_503_is_the_search_list_only() -> None:
    provider = MockQRadarProvider()
    provider.health_faults["count_ariel_searches"] = _unavailable(503)
    health = await _Run(provider).read()
    assert health.ariel.list_probe.state is FieldState.FAILED
    assert health.ariel.list_probe.reason_code == "ariel_unavailable"
    assert health.servers.state is FieldState.OK and health.eps.state is FieldState.OK
    assert health.reachable.value is True


async def test_a_503_elsewhere_is_an_upstream_error() -> None:
    provider = MockQRadarProvider()
    provider.health_faults["get_license_pool"] = _unavailable(503)
    health = await _Run(provider).read()
    assert health.license.reason_code == "upstream_error"


async def test_a_read_that_exceeds_the_timeout_fails_with_its_own_code() -> None:
    provider = MockQRadarProvider()

    async def slow():
        await asyncio.sleep(5)

    provider.list_deployment_hosts = slow  # type: ignore[method-assign]
    health = await _Run(provider).read(read_timeout=0.05)
    assert health.eps.state is FieldState.FAILED and health.eps.reason_code == "timeout"
    assert health.servers.state is FieldState.OK


async def test_a_missing_endpoint_and_an_unsupported_provider_are_not_collected() -> None:
    provider = MockQRadarProvider()
    provider.health_faults["list_managed_servers"] = ProviderError("gone", status_code=404)
    provider.health_faults["get_license_pool"] = CapabilityNotSupportedError("mcp")
    health = await _Run(provider).read()
    assert health.servers.state is FieldState.NOT_COLLECTED
    assert health.servers.reason_code == "endpoint_not_found"
    assert health.license.state is FieldState.NOT_COLLECTED
    assert health.license.reason_code == "provider_not_supported"


async def test_a_422_and_a_malformed_answer_are_failed() -> None:
    provider = MockQRadarProvider()
    provider.health_faults["list_managed_servers"] = ProviderError("bad", status_code=422)
    provider.health_faults["list_deployment_hosts"] = ProviderError("not a list")
    health = await _Run(provider).read()
    assert health.servers.reason_code == "rejected"
    assert health.eps.reason_code == "malformed_response"


async def test_fields_missing_in_the_answer_are_never_summed_as_zero() -> None:
    provider = MockQRadarProvider()
    provider.health_hosts = [
        DeploymentHostDTO(hostname="a", average_eps=100, peak_eps=200, eps_allocation=500),
        DeploymentHostDTO(hostname="b", average_eps=None, peak_eps=10, eps_allocation=500),
    ]
    provider.health_license = LicensePoolDTO(eps=None, fpm=LicenseRateDTO(total=1))
    provider.health_servers = [ManagedServerDTO(hostname="a", status=None)]
    health = await _Run(provider).read()
    assert health.eps.state is FieldState.NOT_COLLECTED
    assert health.eps.reason_code == "field_missing" and health.eps.value is None
    assert health.license.reason_code == "field_missing"
    assert health.servers.reason_code == "field_missing"


async def test_an_empty_server_list_is_not_healthy() -> None:
    provider = MockQRadarProvider()
    provider.health_servers = []
    provider.health_hosts = []
    health = await _Run(provider).read()
    assert health.servers.state is FieldState.NOT_COLLECTED
    assert health.servers.reason_code == "empty_response"
    assert health.eps.reason_code == "empty_response"


async def test_a_host_without_status_is_counted_apart_and_never_active() -> None:
    provider = MockQRadarProvider()
    provider.health_servers = [
        ManagedServerDTO(hostname="a", status="ACTIVE"),
        ManagedServerDTO(hostname="b", status=None),
        ManagedServerDTO(hostname="c", status="down"),
    ]
    health = await _Run(provider).read()
    value = health.servers.value
    assert value is not None
    assert (value.total, value.active, value.unreported) == (3, 1, 1)
    assert value.by_status == {"ACTIVE": 1, "DOWN": 1}
    assert value.all_active is False
    assert [(e.hostname, e.status) for e in value.not_active] == [("b", None), ("c", "DOWN")]


async def test_a_zero_allocation_has_no_share() -> None:
    provider = MockQRadarProvider()
    provider.health_hosts = [
        DeploymentHostDTO(hostname="a", average_eps=0, peak_eps=0, eps_allocation=0)
    ]
    health = await _Run(provider).read()
    assert health.eps.value is not None
    assert health.eps.value.average_share is None


async def test_a_console_with_no_token_has_no_provider() -> None:
    run = _Run()

    def refuse(_instance: QRadarInstance) -> QRadarProvider:
        raise ValueError("no stored SEC token " + SEC_TOKEN)

    health = await read_qradar_health(
        _instance(),
        settings=_settings(),
        provider_factory=refuse,
        cache=run.cache,
        now=lambda: T0,
        monotonic=run.clock,
    )
    for field in (health.reachable, health.servers, health.eps, health.license):
        assert field.state is FieldState.NOT_COLLECTED
        assert field.reason_code == "provider_not_configured"
    assert SEC_TOKEN not in health.model_dump_json()


# ------------------------------------------------------------------------ cache


async def test_two_calls_inside_the_ttl_make_one_qradar_read_each() -> None:
    run = _Run(ttl=30)
    first = await run.read()
    run.clock.t += 29
    second = await run.read()
    assert run.provider.health_calls == {
        "probe_about": 1,
        "list_managed_servers": 1,
        "list_deployment_hosts": 1,
        "get_license_pool": 1,
        "count_ariel_searches": 1,
    }
    assert run.built == 1 and run.closed == 1
    assert first.cached is False and second.cached is True
    assert second.age_seconds == 29.0 and second.cache_ttl_seconds == 30
    assert second.as_of == first.as_of


async def test_the_next_call_after_the_ttl_asks_qradar_again() -> None:
    run = _Run(ttl=30)
    await run.read()
    run.clock.t += 30
    again = await run.read()
    assert again.cached is False
    assert run.provider.health_calls["probe_about"] == 2
    assert run.built == 2


async def test_a_failure_is_cached_too() -> None:
    provider = MockQRadarProvider()
    provider.health_faults["probe_about"] = _unavailable()
    run = _Run(provider)
    await run.read()
    run.clock.t += 5
    again = await run.read()
    assert again.cached is True and again.reachable.value is False
    assert provider.health_calls == {"probe_about": 1}


async def test_concurrent_callers_share_one_read() -> None:
    run = _Run()
    results = await asyncio.gather(*(run.read() for _ in range(6)))
    assert run.provider.health_calls["probe_about"] == 1
    assert sorted(r.cached for r in results) == [False] + [True] * 5


async def test_the_ariel_record_is_merged_fresh_on_a_cache_hit() -> None:
    run = _Run()
    first = await run.read()
    assert first.ariel.available.reason_code == "ariel_search_run_not_wired"
    second = await run.read(
        ariel_runs=ArielRunsSnapshot(
            window_minutes=15, searches=3, unavailable=0, last_success_at=T0, in_flight=0
        )
    )
    assert second.cached is True
    assert second.ariel.available.value is True
    assert second.ariel.ours_in_flight.value == 0


async def test_a_changed_console_host_is_read_afresh() -> None:
    run = _Run()
    await run.read(_instance("https://one.example"))
    await run.read(_instance("https://two.example"))
    assert run.provider.health_calls["probe_about"] == 2


# ---------------------------------------------------------- the Ariel record fields


async def test_without_the_search_record_the_ariel_fields_say_not_wired() -> None:
    health = await _Run().read()
    for field in (health.ariel.available, health.ariel.recent, health.ariel.ours_in_flight):
        assert field.state is FieldState.NOT_COLLECTED
        assert field.reason_code == "ariel_search_run_not_wired"
        assert field.value is None
    # QRadar's own list is still read.
    assert health.ariel.list_probe.state is FieldState.OK


@pytest.mark.parametrize(
    ("runs", "state", "value", "code"),
    [
        (ArielRunsSnapshot(15, 0, 0), FieldState.NOT_COLLECTED, None, "no_search_in_window"),
        (ArielRunsSnapshot(15, 5, 0), FieldState.NOT_COLLECTED, None, "no_ariel_outcome_in_window"),
        (ArielRunsSnapshot(15, 5, 6), FieldState.NOT_COLLECTED, None, "snapshot_inconsistent"),
        (ArielRunsSnapshot(15, 5, 0, T0), FieldState.OK, True, None),
        (
            ArielRunsSnapshot(15, 5, 2, T0, T0 - timedelta(minutes=1)),
            FieldState.OK, True, None,
        ),
        (
            ArielRunsSnapshot(15, 5, 2, T0 - timedelta(minutes=1), T0),
            FieldState.OK, False, "ariel_unavailable",
        ),
        (ArielRunsSnapshot(15, 5, 5, None, T0), FieldState.OK, False, "ariel_unavailable"),
    ],
)
async def test_ariel_available_follows_the_latest_outcome(runs, state, value, code) -> None:
    health = await _Run().read(ariel_runs=runs)
    assert health.ariel.available.state is state
    assert health.ariel.available.value is value
    assert health.ariel.available.reason_code == code


async def test_the_unavailable_share_and_unread_in_flight() -> None:
    health = await _Run().read(ariel_runs=ArielRunsSnapshot(15, 8, 2, T0))
    recent = health.ariel.recent.value
    assert recent is not None and recent.unavailable_share == 0.25
    assert health.ariel.ours_in_flight.state is FieldState.NOT_COLLECTED
    assert health.ariel.ours_in_flight.reason_code == "in_flight_not_read"


# ------------------------------------------------------------ the model's own guard


def test_a_field_without_a_value_cannot_claim_ok() -> None:
    with pytest.raises(ValidationError):
        HealthField[int](state=FieldState.OK)


def test_a_field_that_was_not_read_cannot_carry_a_value_or_lack_a_reason() -> None:
    with pytest.raises(ValidationError):
        HealthField[int](state=FieldState.FAILED, value=0, reason_code="x", reason="y")
    with pytest.raises(ValidationError):
        HealthField[int](state=FieldState.NOT_COLLECTED)


def test_every_reason_is_a_fixed_sentence_without_upstream_text() -> None:
    for code, sentence in REASONS.items():
        assert code and sentence.endswith(".")
        assert "{" not in sentence


# ------------------------------------------------------------------- no secrets


async def test_no_token_in_the_response_or_any_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    provider = MockQRadarProvider()
    for name in (
        "probe_about",
        "list_managed_servers",
        "list_deployment_hosts",
        "get_license_pool",
        "count_ariel_searches",
    ):
        provider.health_faults[name] = _unavailable(500)
    health = await _Run(provider).read()
    assert SEC_TOKEN not in health.model_dump_json()
    assert SEC_TOKEN not in caplog.text
    for field in (health.servers, health.eps, health.license, health.ariel.list_probe):
        assert field.state is FieldState.FAILED
        assert field.reason in REASONS.values()


def test_the_cache_ttl_setting_is_bounded() -> None:
    assert get_settings().qradar_health_cache_ttl_seconds == 30
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {**get_settings().model_dump(), "qradar_health_cache_ttl_seconds": 1}
        )
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {**get_settings().model_dump(), "qradar_health_cache_ttl_seconds": 601}
        )


def test_a_dto_exists_for_each_read() -> None:
    assert ArielSearchListDTO(held=0).truncated is False
