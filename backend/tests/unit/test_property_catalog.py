"""The custom-property catalog: REST join, mock shapes, cache, and the route.

The REST provider is driven through a mocked httpx transport. Nothing here
reaches a QRadar console.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes import waf_catalog
from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.main import create_app
from app.providers.base import (
    CapabilityNotSupportedError,
    ProviderCapability,
    ProviderUnavailableError,
)
from app.providers.dto import CustomPropertyDTO
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_mcp import QRadarMCPProvider
from app.providers.qradar_rest import QRadarRestProvider
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.property_catalog import PropertyCatalogService, get_property_catalog_service
from tests.unit.test_qradar_rest_provider import build_provider

PROPS = "/api/config/event_sources/custom_properties/regex_properties"
EXPRS = "/api/config/event_sources/custom_properties/property_expressions"


def _prop(i: int, name: str, **extra: object) -> dict:
    return {
        "id": i,
        "identifier": f"ID{i}",
        "name": name,
        "property_type": "string",
        "use_for_rule_engine": False,
        "auto_discovered": False,
        **extra,
    }


def _expr(ident: str, type_id: int, *, enabled: bool = True, source: int = -1, **extra) -> dict:
    return {
        "regex_property_identifier": ident,
        "log_source_type_id": type_id,
        "log_source_id": source,
        "enabled": enabled,
        **extra,
    }


# ------------------------------------------------------------------ REST
class TestRestCatalog:
    def _provider(self, props: list[dict], exprs: list[dict], seen: list[httpx.Request]):
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            rows = props if request.url.path == PROPS else exprs
            if request.url.path not in (PROPS, EXPRS):
                raise AssertionError(f"unexpected request: {request.method} {request.url}")
            return httpx.Response(200, json=rows)

        provider, _ = build_provider(handler, page_size=100, max_pages=3)
        return provider

    @pytest.mark.asyncio
    async def test_paths_fields_and_join(self) -> None:
        seen: list[httpx.Request] = []
        provider = self._provider(
            [_prop(1, "URL"), _prop(2, "Orphaned"), _prop(3, "Bad.Name(1)")],
            [_expr("ID1", 212), _expr("ID1", 2), _expr("ID1", 212), _expr("ID3", 5)],
            seen,
        )
        result = await provider.list_custom_properties()
        assert [r.method for r in seen] == ["GET", "GET"]
        assert [r.url.path for r in seen] == [PROPS, EXPRS]
        assert seen[0].url.params["fields"] == (
            "id,identifier,name,property_type,use_for_rule_engine,auto_discovered"
        )
        assert seen[1].url.params["fields"] == (
            "regex_property_identifier,log_source_type_id,log_source_id,enabled"
        )
        assert "filter" not in seen[1].url.params
        by_name = {p.name: p for p in result}
        assert by_name["URL"].log_source_type_ids == (2, 212)  # sorted, de-duplicated
        assert by_name["Orphaned"].log_source_type_ids == ()  # no expression
        assert by_name["Bad.Name(1)"].name_allowed is False
        assert by_name["URL"].name_allowed is True
        assert len(result) == 3  # one DTO per property

    @pytest.mark.asyncio
    async def test_type_filter_is_sent(self) -> None:
        seen: list[httpx.Request] = []
        provider = self._provider([_prop(1, "URL")], [_expr("ID1", 212)], seen)
        await provider.list_custom_properties(log_source_type_id=212)
        assert seen[1].url.params["filter"] == "log_source_type_id=212"
        assert "filter" not in seen[0].url.params

    @pytest.mark.asyncio
    async def test_only_enabled_whole_type_known_expressions_count(self) -> None:
        provider = self._provider(
            [_prop(1, "A"), _prop(2, "B"), _prop(3, "C")],
            [
                _expr("ID1", 10, enabled=False),
                _expr("ID1", 11, source=77),
                _expr("ID1", 12),
                _expr("NOPE", 13),  # orphan: no such property
                _expr("ID3", 14, enabled="true"),  # not a real boolean
            ],
            [],
        )
        by_name = {p.name: p for p in await provider.list_custom_properties()}
        assert by_name["A"].log_source_type_ids == (12,)
        assert by_name["B"].log_source_type_ids == ()
        assert by_name["C"].log_source_type_ids == ()

    @pytest.mark.asyncio
    async def test_bodies_never_reach_the_dto(self) -> None:
        secret = "SECRET-REGEX-BODY"
        provider = self._provider(
            [_prop(1, "URL", regex=secret, payload=secret, description=secret)],
            [_expr("ID1", 212, regex=secret, payload=secret, format_string=secret)],
            [],
        )
        (dto,) = await provider.list_custom_properties()
        dumped = json.dumps(dto.model_dump())
        assert secret not in dumped
        assert set(dto.model_dump()) == {
            "qradar_id", "identifier", "name", "property_type", "use_for_rule_engine",
            "auto_discovered", "log_source_type_ids", "name_allowed",
        }

    @pytest.mark.asyncio
    async def test_paginates_across_two_pages(self) -> None:
        props = [_prop(i, f"P{i}") for i in range(1, 4)]
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path == EXPRS:
                return httpx.Response(200, json=[_expr("ID3", 9)])
            start = int(request.headers["Range"].split("=")[1].split("-")[0])
            return httpx.Response(200, json=props[start : start + 2])

        provider, _ = build_provider(handler, page_size=2, max_pages=3)
        result = await provider.list_custom_properties()
        assert [p.name for p in result] == ["P1", "P2", "P3"]
        assert result[2].log_source_type_ids == (9,)
        assert [r.headers["Range"] for r in seen if r.url.path == PROPS] == [
            "items=0-1",
            "items=2-3",
        ]

    def test_capability_declared_on_rest_and_mock_not_mcp(self) -> None:
        cap = ProviderCapability.CUSTOM_PROPERTY_CATALOG
        assert cap in QRadarRestProvider.capabilities
        assert cap in MockQRadarProvider.capabilities
        assert cap not in QRadarMCPProvider.capabilities

    @pytest.mark.asyncio
    async def test_mcp_refuses(self) -> None:
        mcp = QRadarMCPProvider(base_url="http://qradar-mcp:5000")
        with pytest.raises(CapabilityNotSupportedError):
            await mcp.list_custom_properties()


# ------------------------------------------------------------------ mock
class TestMockCatalog:
    @pytest.mark.asyncio
    async def test_lab_shape(self) -> None:
        mock = MockQRadarProvider()
        by_name = {p.name: p for p in await mock.list_custom_properties()}
        for name in ("HTTP_Method", "URL", "WAF_Action", "WAF_ResponseCode", "WAF_USERAGENT"):
            assert 212 in by_name[name].log_source_type_ids, name
        assert by_name["WAF_URL"].log_source_type_ids == (232,)
        assert by_name["IPS_Action"].log_source_type_ids == (2,)
        assert any(not p.log_source_type_ids for p in by_name.values())
        assert by_name["Bad.Name(1)"].name_allowed is False
        assert all(p.name_allowed for n, p in by_name.items() if n != "Bad.Name(1)")

    @pytest.mark.asyncio
    async def test_type_filter(self) -> None:
        names = {p.name for p in await MockQRadarProvider().list_custom_properties(
            log_source_type_id=232
        )}
        assert names == {"WAF_URL"}


# --------------------------------------------------------------- service
T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def _dto(name: str, types: tuple[int, ...], *, allowed: bool = True) -> CustomPropertyDTO:
    return CustomPropertyDTO(
        qradar_id=abs(hash(name)) % 1000,
        identifier=name,
        name=name,
        property_type="string",
        log_source_type_ids=types,
        name_allowed=allowed,
    )


class FakeProvider:
    def __init__(self, calls: list[str], *, error: Exception | None = None, props=None) -> None:
        self.calls = calls
        self.error = error
        self.props = props if props is not None else [_dto("URL", (212,))]
        self.closed = False

    async def list_custom_properties(self, *, log_source_type_id=None):
        self.calls.append("list")
        if self.error:
            raise self.error
        return self.props

    async def aclose(self) -> None:
        self.closed = True


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class TestService:
    def _service(self, ttl: int = 600) -> tuple[PropertyCatalogService, Clock]:
        clock = Clock()
        return PropertyCatalogService(ttl_seconds=ttl, clock=clock), clock

    @pytest.mark.asyncio
    async def test_ttl_hit_and_miss_and_refresh(self) -> None:
        service, clock = self._service(600)
        calls: list[str] = []
        providers: list[FakeProvider] = []

        def factory() -> FakeProvider:
            providers.append(FakeProvider(calls))
            return providers[-1]

        async def get(refresh: bool = False):
            return await service.catalog(
                instance_id="i1", provider_factory=factory, source_type_id=212, refresh=refresh
            )

        first = await get()
        assert first.status == "AVAILABLE" and first.fetched_at == T0
        clock.now = T0 + timedelta(seconds=599)
        await get()
        assert len(calls) == 1  # hit
        clock.now = T0 + timedelta(seconds=600)
        again = await get()
        assert len(calls) == 2 and again.fetched_at == clock.now  # expired: miss
        await get(refresh=True)
        assert len(calls) == 3  # refresh bypasses
        assert all(p.closed for p in providers)
        # another instance has its own entry
        await service.catalog(instance_id="i2", provider_factory=factory, source_type_id=None)
        assert len(calls) == 4

    @pytest.mark.asyncio
    async def test_hit_builds_no_provider(self) -> None:
        service, _ = self._service()
        calls: list[str] = []
        await service.catalog(
            instance_id="i", provider_factory=lambda: FakeProvider(calls), source_type_id=1
        )

        def boom() -> FakeProvider:
            raise AssertionError("provider built on a cache hit")

        out = await service.catalog(instance_id="i", provider_factory=boom, source_type_id=1)
        assert out.status == "AVAILABLE"

    @pytest.mark.asyncio
    async def test_failure_is_unavailable_safe_and_not_cached(self) -> None:
        service, _ = self._service()
        calls: list[str] = []
        leak = "https://console.internal/api SEC=tok-123 body"
        bad = FakeProvider(calls, error=ProviderUnavailableError(leak))
        out = await service.catalog(
            instance_id="i", provider_factory=lambda: bad, source_type_id=212
        )
        assert out.status == "UNAVAILABLE" and out.properties == [] and out.fetched_at is None
        assert out.detail is not None and "ProviderUnavailableError" in out.detail
        assert "console.internal" not in out.detail and "tok-123" not in out.detail
        assert bad.closed
        good = FakeProvider(calls)
        recovered = await service.catalog(
            instance_id="i", provider_factory=lambda: good, source_type_id=212
        )
        assert recovered.status == "AVAILABLE"  # the failure was not cached
        assert calls == ["list", "list"]

    @pytest.mark.asyncio
    async def test_factory_failure_and_unsupported_capability(self) -> None:
        service, _ = self._service()

        def no_token() -> FakeProvider:
            raise ValueError("has no stored SEC token")

        out = await service.catalog(instance_id="a", provider_factory=no_token, source_type_id=1)
        assert out.status == "UNAVAILABLE" and "ValueError" in (out.detail or "")
        mcp = await service.catalog(
            instance_id="b",
            provider_factory=lambda: QRadarMCPProvider(base_url="http://qradar-mcp:5000"),
            source_type_id=1,
        )
        assert mcp.status == "UNAVAILABLE"
        assert "CapabilityNotSupportedError" in (mcp.detail or "")

    @pytest.mark.asyncio
    async def test_applicability_and_ordering(self) -> None:
        service, _ = self._service()
        props = [
            _dto("zeta", (212,)),
            _dto("Alpha", ()),
            _dto("beta", (212, 5)),
            _dto("Bad.Name(1)", (212,), allowed=False),
            _dto("gamma", (7,)),
        ]
        out = await service.catalog(
            instance_id="i",
            provider_factory=lambda: FakeProvider([], props=props),
            source_type_id=212,
        )
        assert out.filtered_by_source_type is True
        assert [(p.name, p.applies_to_source_type) for p in out.properties] == [
            ("Bad.Name(1)", True),
            ("beta", True),
            ("zeta", True),
            ("Alpha", False),
            ("gamma", False),
        ]
        assert next(p for p in out.properties if p.name == "Bad.Name(1)").name_allowed is False

        unknown = await service.catalog(
            instance_id="i", provider_factory=lambda: FakeProvider([]), source_type_id=None
        )
        assert unknown.filtered_by_source_type is False
        assert {p.applies_to_source_type for p in unknown.properties} == {None}
        assert [p.name for p in unknown.properties] == [
            "Alpha", "Bad.Name(1)", "beta", "gamma", "zeta",
        ]

    def test_ttl_setting_bounds(self) -> None:
        assert Settings().qradar_property_catalog_ttl_seconds == 600
        for bad in (59, 86401):
            with pytest.raises(ValueError):
                Settings(qradar_property_catalog_ttl_seconds=bad)


# ------------------------------------------------------------------ route
class FakeSession:
    def __init__(self, objects: dict) -> None:
        self.objects = objects

    async def get(self, model, key):
        return self.objects.get((model.__name__, key))


def _app_client(session, service, *, auth_provider: str = "disabled"):
    app = create_app()
    settings = get_settings().model_copy(update={"auth_provider": auth_provider})
    app.dependency_overrides[get_settings] = lambda: settings

    async def _session():
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_property_catalog_service] = lambda: service
    if auth_provider != "oidc":
        app.dependency_overrides[get_principal] = lambda: Principal(
            subject="waf-admin", permissions=frozenset({"admin:waf"})
        )
    return TestClient(app, raise_server_exceptions=False)


def test_route_returns_catalog_without_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    instance = SimpleNamespace(id=uuid.uuid4(), provider_kind="mock")
    ls_id = uuid.uuid4()
    source = SimpleNamespace(id=ls_id, instance_id=instance.id, type_id=212)
    session = FakeSession({("LogSource", ls_id): source, ("QRadarInstance", instance.id): instance})
    monkeypatch.setattr(waf_catalog, "build_provider_for_instance", lambda i: MockQRadarProvider())
    service = PropertyCatalogService(ttl_seconds=600)
    client = _app_client(session, service)

    resp = client.get(f"/api/v1/waf/log-sources/{ls_id}/property-catalog")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "AVAILABLE" and body["filtered_by_source_type"] is True
    applies = {p["name"]: p["applies_to_source_type"] for p in body["properties"]}
    assert applies["URL"] is True and applies["WAF_URL"] is False
    assert [p["applies_to_source_type"] for p in body["properties"]][:5] == [True] * 5
    text = resp.text.lower()
    for forbidden in ("regex", "payload", "expression", "identifier"):
        assert forbidden not in text

    assert client.get(
        f"/api/v1/waf/log-sources/{ls_id}/property-catalog?refresh=true"
    ).status_code == 200


def test_route_404_unknown_log_source() -> None:
    client = _app_client(FakeSession({}), PropertyCatalogService(ttl_seconds=600))
    resp = client.get(f"/api/v1/waf/log-sources/{uuid.uuid4()}/property-catalog")
    assert resp.status_code == 404


def test_route_401_without_token_and_403_for_read_wildcard() -> None:
    url = f"/api/v1/waf/log-sources/{uuid.uuid4()}/property-catalog"
    service = PropertyCatalogService(ttl_seconds=600)
    assert _app_client(FakeSession({}), service, auth_provider="oidc").get(url).status_code == 401

    app_client = _app_client(FakeSession({}), service)
    app_client.app.dependency_overrides[get_principal] = lambda: Principal(
        subject="viewer", permissions=frozenset({"read:*"})
    )
    assert app_client.get(url).status_code == 403
