"""AQL-1: `doctor --aql` sends each family's real query once and judges QRadar's answer."""

from __future__ import annotations

import json
import pathlib
import sys
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest.mock import AsyncMock

import pytest

from app.cli import doctor as doctor_module
from app.cli import doctor_aql
from app.cli.doctor import CheckResult, Doctor, Status, render_json, render_text
from app.core.config import Settings
from app.models.ariel_search import CALLERS
from app.models.enums import PwScopeKind
from app.providers.base import (
    CapabilityNotSupportedError,
    ProviderAuthError,
    ProviderError,
    ProviderQueryRejectedError,
    ProviderUnavailableError,
)
from app.services import ariel_search_log as ariel_log
from app.services.ariel_permit import ArielPermitUnavailable

#: A recognisable value no output may carry: it stands for a path, an IP or a property value.
RAW_VALUE = "RAW-VALUE-/admin/secret-10.9.8.7"
#: What a real 422 body can echo back (`_sanitize_upstream_error` exists for this).
UPSTREAM_BODY = "Authorization: SEC fake-header-token-0123456789 query was invalid"

NOW = datetime(2026, 11, 3, 12, 7, 31, tzinfo=UTC)

FAMILY_CALLS = {
    "log_source_volume": ["get_log_source_metrics_bucketed"],
    "rule_match": ["get_rule_matches_windowed"],
    "history": ["get_console_event_totals"],
    "waf_collection": ["get_waf_observations_bucketed"],
    "ips_collection": ["get_ips_keyed"],
    "property_watch": ["get_pw_keyed"],
    "catchall_senders": ["get_catchall_senders_keyed"],
    "geo_collection": ["get_geo_keyed"],
    "qradar_health": ["get_health_metrics", "get_health_notifications"],
}
#: The caller label each method declares with `@_loads`, as the real provider does.
METHOD_FAMILY = {
    "get_log_source_metrics_bucketed": ariel_log.CALLER_LOG_SOURCE_VOLUME,
    "get_rule_matches_windowed": ariel_log.CALLER_RULE_MATCH,
    "get_console_event_totals": ariel_log.CALLER_HISTORY,
    "get_waf_observations_bucketed": ariel_log.CALLER_WAF_COLLECTION,
    "get_ips_keyed": ariel_log.CALLER_IPS_COLLECTION,
    "get_pw_keyed": ariel_log.CALLER_PROPERTY_WATCH,
    "get_catchall_senders_keyed": ariel_log.CALLER_CATCHALL_SENDERS,
    "get_geo_keyed": ariel_log.CALLER_GEO_COLLECTION,
    "get_health_metrics": ariel_log.CALLER_QRADAR_HEALTH,
    "get_health_notifications": ariel_log.CALLER_QRADAR_HEALTH,
}


class FakeProvider:
    """Answers every search the way a console would, and records what was asked."""

    def __init__(
        self,
        *,
        error: BaseException | None = None,
        truncated: bool = False,
        properties: tuple[str, ...] = (
            "Metric ID",
            "Value",
            "Element",
            "Source Geographic Location",
        ),
    ) -> None:
        self.error = error
        self.truncated = truncated
        self.properties = properties
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.callers: list[str] = []
        self.closed = False
        self.running = 0
        self.max_running = 0

    def supports(self, _capability: object) -> bool:
        return True

    async def list_custom_properties(self) -> list[Any]:
        return [SimpleNamespace(name=name) for name in self.properties]

    async def aclose(self) -> None:
        self.closed = True

    async def _search(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            self.calls.append((name, {"args": args, **kwargs}))
            # The label the load record would carry: the override wins over the family.
            self.callers.append(ariel_log.effective_caller(METHOD_FAMILY[name]))
            if self.error is not None:
                raise self.error
            rows = [SimpleNamespace(value=RAW_VALUE)]
            return SimpleNamespace(
                rows=rows,
                rows_returned=1,
                truncated=self.truncated,
                batches={0: SimpleNamespace(rows=rows)},
                totals={0: 5},
            )
        finally:
            self.running -= 1

    def __getattr__(self, name: str) -> Any:
        if name not in METHOD_FAMILY:
            raise AttributeError(name)

        async def method(*args: Any, **kwargs: Any) -> Any:
            return await self._search(name, args, kwargs)

        return method


class FakeRows:
    def __init__(self, row: Any) -> None:
        self.row = row

    def first(self) -> Any:
        return self.row


class FakeSession:
    """The inputs a family reads: a WAF or IPS profile row, or nothing."""

    def __init__(self, row: Any = None) -> None:
        self.row = row

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, _query: object) -> FakeRows:
        return FakeRows(self.row)


def _waf_row(virtual_host: str | None = None) -> Any:
    profile = SimpleNamespace(
        url_property="Request URL",
        method_property="Request Method",
        virtual_host_property=virtual_host,
    )
    return profile, SimpleNamespace(qradar_id=77, name="waf-edge-1")


def _ips_row() -> Any:
    mapping = {
        "SOURCE_IP": {"kind": "NORMALIZED", "name": "sourceip"},
        "DESTINATION_IP": {"kind": "NORMALIZED", "name": "destinationip"},
        "DESTINATION_PORT": {"kind": "NORMALIZED", "name": "destinationport"},
        "SIGNATURE": {"kind": "CUSTOM_PROPERTY", "name": "Threat Signature"},
    }
    profile = SimpleNamespace(mapping=mapping, action_value_map={})
    return profile, SimpleNamespace(qradar_id=88, name="ips-core-1")


class FakeWatchRepository:
    watches: ClassVar[list[Any]] = []

    def __init__(self, _session: object) -> None:
        pass

    async def enabled_watches(self, _instance_id: object) -> list[Any]:
        return list(self.watches)

    async def sources(self, *_args: object, **_kwargs: object) -> list[Any]:
        return [SimpleNamespace(id=uuid.UUID(int=1), qradar_id=99, type_id=1, group_ids=[])]

    async def groups(self, _instance_id: object) -> dict[int, Any]:
        return {}


def _watch() -> Any:
    return SimpleNamespace(
        id=uuid.UUID(int=1),
        name="login-country",
        scope_kind=PwScopeKind.LOG_SOURCE.value,
        log_source_id=uuid.UUID(int=1),
        scope_type_id=None,
        scope_group_id=None,
        properties=[{"kind": "CUSTOM_PROPERTY", "name": "Login Country"}],
    )


#: Several collections ship off; the checks need them on.
ALL_ON = {
    "rule_match_collection_enabled": True,
    "waf_collection_enabled": True,
    "ips_collection_enabled": True,
    "pw_collection_enabled": True,
    "catchall_collection_enabled": True,
    "geo_collection_enabled": True,
    "qradar_health_collection_enabled": True,
}


def _settings(tmp_path, **overrides: Any) -> Settings:
    token = tmp_path / "qradar.sec"
    ca = tmp_path / "qradar-ca.pem"
    token.write_text("fixture-token", encoding="utf-8")
    ca.write_text("fixture-ca", encoding="utf-8")
    return Settings(
        qradar_host="https://qradar.example.test",
        qradar_api_token_file=str(token),
        qradar_ca_bundle=str(ca),
        public_origin="https://soc.example.test",
        encryption_key="sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A=",
        **{**ALL_ON, **overrides},
    )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return _settings(tmp_path)


@pytest.fixture(autouse=True)
def watch_repository(monkeypatch: pytest.MonkeyPatch) -> type[FakeWatchRepository]:
    FakeWatchRepository.watches = [_watch()]
    monkeypatch.setattr(doctor_aql, "PropertyWatchRepository", FakeWatchRepository)
    return FakeWatchRepository


def _console(kind: str = "rest", name: str = "lab-console") -> Any:
    return SimpleNamespace(id=uuid.uuid4(), name=name, provider_kind=kind)


class RoutedSession(FakeSession):
    """WAF and IPS rows by the table the query selects from."""

    def __init__(self, waf: Any = None, ips: Any = None) -> None:
        super().__init__()
        self.waf, self.ips = waf, ips

    async def execute(self, query: object) -> FakeRows:
        text = str(query)
        return FakeRows(self.waf if "waf_source_profile" in text else self.ips)


def _sessionmaker(waf: Any = None, ips: Any = None) -> Any:
    return lambda: RoutedSession(waf, ips)


async def _run(
    settings: Settings,
    provider: FakeProvider,
    *,
    families: tuple[str, ...] = doctor_aql.FAMILIES,
    instances: list[Any] | None = None,
    waf: Any = None,
    ips: Any = None,
) -> dict[str, doctor_aql.AqlLine]:
    lines = await doctor_aql.run_aql_checks(
        settings=settings,
        sessionmaker=_sessionmaker(waf, ips),
        instances=[_console()] if instances is None else instances,
        provider_factory=lambda _instance: cast(Any, provider),
        now=NOW,
        families=families,
    )
    return {line.family: line for line in lines}


def _full(settings: Settings, provider: FakeProvider, **kwargs: Any):
    return _run(settings, provider, waf=_waf_row(), ips=_ips_row(), **kwargs)


# ---------------------------------------------------------------------- verdicts
@pytest.mark.asyncio
async def test_every_family_passes_with_row_count_and_no_raw_value(settings: Settings) -> None:
    provider = FakeProvider()
    lines = await _full(settings, provider)
    assert list(lines) == list(doctor_aql.FAMILIES)
    for family, line in lines.items():
        assert line.verdict == doctor_aql.PASS, (family, line.message)
        assert "row(s) returned" in line.message
        assert "ms" in line.message
        assert "truncated" not in line.message
    # The entity each family used is named by name and id, never by a value.
    assert "waf-edge-1" in lines["waf_collection"].message
    assert "QRadar id 77" in lines["waf_collection"].message
    assert "ips-core-1" in lines["ips_collection"].message
    assert "login-country" in lines["property_watch"].message
    assert provider.closed
    rendered = [
        CheckResult(f"aql_{line.family}", Status(line.verdict), line.message, "OPS-R72")
        for line in lines.values()
    ]
    assert RAW_VALUE not in render_text(rendered)
    assert RAW_VALUE not in render_json(rendered)
    assert "10.9.8.7" not in render_json(rendered)


@pytest.mark.asyncio
async def test_each_family_calls_its_real_method_once_with_the_smallest_cap(
    settings: Settings,
) -> None:
    provider = FakeProvider()
    await _full(settings, provider)
    asked = [name for name, _ in provider.calls]
    assert asked == [name for names in FAMILY_CALLS.values() for name in names]
    assert len(asked) == 10
    start, end = doctor_aql.window_for(NOW)
    assert (end - start).total_seconds() == 300
    assert end == datetime(2026, 11, 3, 12, 5, tzinfo=UTC)
    for name, kwargs in provider.calls:
        if "max_rows" in kwargs:
            assert kwargs["max_rows"] == 1, name
        if name == "get_waf_observations_bucketed":
            assert kwargs["max_groups"] == 1
        values = (*kwargs["args"], *kwargs.values())
        window = [item for item in values if isinstance(item, datetime)]
        assert window[:2] == [start, end], name
    # The geo search names the configured property; the entity is the first configured one.
    geo = dict(provider.calls)["get_geo_keyed"]
    assert geo["country_property"] == "Source Geographic Location"


@pytest.mark.asyncio
async def test_a_truncated_result_is_still_a_pass_and_says_so(settings: Settings) -> None:
    lines = await _full(settings, FakeProvider(truncated=True))
    for line in lines.values():
        if line.family == "history":
            continue  # a totals search has no row cap to truncate at
        assert line.verdict == doctor_aql.PASS, line
        assert "truncated" in line.message, line


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("family", "version", "names"),
    [
        ("rule_match", "rule_match/v2", "none"),
        ("history", "console_event_totals/v1", "none"),
        ("waf_collection", "unversioned", '"Request URL", "Request Method"'),
        ("ips_collection", "unversioned", 'SIGNATURE="CUSTOM_PROPERTY:Threat Signature"'),
        ("property_watch", "unversioned", '"CUSTOM_PROPERTY:Login Country"'),
        ("geo_collection", "geo-country-property-v1", '"Source Geographic Location"'),
        ("qradar_health", "qradar_health/v1", '"Metric ID", "Value", "Element"'),
    ],
)
async def test_a_422_fails_with_status_version_and_names_and_no_upstream_body(
    settings: Settings, family: str, version: str, names: str
) -> None:
    provider = FakeProvider(error=ProviderQueryRejectedError(UPSTREAM_BODY, status_code=422))
    line = (await _full(settings, provider, families=(family,)))[family]
    assert line.verdict == doctor_aql.FAIL
    assert "HTTP 422" in line.message
    assert version in line.message
    assert names in line.message
    assert "fake-header-token" not in line.message
    assert "Authorization" not in line.message
    rendered = render_json([CheckResult(f"aql_{family}", Status.FAIL, line.message, "OPS-R72")])
    assert "fake-header-token" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [401, 403])
async def test_an_auth_error_fails(settings: Settings, code: int) -> None:
    provider = FakeProvider(error=ProviderAuthError(UPSTREAM_BODY, status_code=code))
    line = (await _full(settings, provider, families=("rule_match",)))["rule_match"]
    assert line.verdict == doctor_aql.FAIL
    assert f"HTTP {code}" in line.message
    assert "fake-header-token" not in line.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "fragment"),
    [
        (TimeoutError(), "no answer within 60 s"),
        (ProviderUnavailableError("QRadar request timed out"), "did not finish the search"),
        (ProviderUnavailableError(UPSTREAM_BODY), "did not answer"),
        (ProviderError("QRadar Ariel metric search did not complete"), "without completing"),
        (ArielPermitUnavailable(5.0, 3, 6), "no free Ariel permit"),
    ],
)
async def test_warn_proves_nothing_about_the_query(
    settings: Settings, error: BaseException, fragment: str
) -> None:
    provider = FakeProvider(error=error)
    line = (await _full(settings, provider, families=("rule_match",)))["rule_match"]
    assert line.verdict == doctor_aql.WARN
    assert fragment in line.message
    assert "proves nothing about the query" in line.message
    assert "fake-header-token" not in line.message


@pytest.mark.asyncio
async def test_a_query_that_cannot_be_built_from_the_configuration_fails(
    settings: Settings,
) -> None:
    provider = FakeProvider(error=ValueError("unsafe QRadar property name: 'x\"y'"))
    line = (await _full(settings, provider, families=("waf_collection",)))["waf_collection"]
    assert line.verdict == doctor_aql.FAIL
    assert "no search was sent" in line.message
    assert "unsafe" not in line.message


@pytest.mark.asyncio
async def test_a_provider_without_aql_is_skipped(settings: Settings) -> None:
    provider = FakeProvider(error=CapabilityNotSupportedError("no AQL"))
    line = (await _full(settings, provider, families=("rule_match",)))["rule_match"]
    assert line.verdict == doctor_aql.SKIP


# ------------------------------------------------------------------------- skips
@pytest.mark.asyncio
async def test_a_family_that_is_off_is_skipped_and_sends_nothing(tmp_path) -> None:
    settings = _settings(
        tmp_path,
        rule_match_collection_enabled=False,
        waf_collection_enabled=False,
        ips_collection_enabled=False,
        pw_collection_enabled=False,
        catchall_collection_enabled=False,
        geo_collection_enabled=False,
        qradar_health_collection_enabled=False,
    )
    provider = FakeProvider()
    lines = await _full(settings, provider)
    off = set(doctor_aql.FAMILIES) - {"log_source_volume", "history"}
    for family in off:
        assert lines[family].verdict == doctor_aql.SKIP, family
        assert "is off" in lines[family].message
    assert lines["log_source_volume"].verdict == doctor_aql.PASS
    assert lines["history"].verdict == doctor_aql.PASS
    assert [name for name, _ in provider.calls] == [
        "get_log_source_metrics_bucketed",
        "get_console_event_totals",
    ]


@pytest.mark.asyncio
async def test_nothing_configured_is_a_skip_never_a_pass(
    tmp_path, watch_repository: type[FakeWatchRepository]
) -> None:
    # The settings validator refuses an empty list; `model_copy` skips it, as a guard test needs.
    settings = _settings(tmp_path, geo_source_country_property="").model_copy(
        update={"catchall_type_ids": []}
    )
    watch_repository.watches = []
    provider = FakeProvider()
    lines = await _run(settings, provider)  # no WAF profile, no IPS profile, no watch
    for family in ("waf_collection", "ips_collection", "property_watch", "catchall_senders"):
        assert lines[family].verdict == doctor_aql.SKIP, family
    assert lines["geo_collection"].verdict == doctor_aql.SKIP
    assert "GEO_SOURCE_COUNTRY_PROPERTY" in lines["geo_collection"].message
    called = {name for name, _ in provider.calls}
    assert not called & {
        "get_waf_observations_bucketed",
        "get_ips_keyed",
        "get_pw_keyed",
        "get_catchall_senders_keyed",
        "get_geo_keyed",
    }


@pytest.mark.asyncio
async def test_mcp_consoles_and_no_console_are_skipped(settings: Settings) -> None:
    provider = FakeProvider()
    mcp = await _full(settings, provider, instances=[_console("mcp", "mcp-console")])
    assert {line.verdict for line in mcp.values()} == {doctor_aql.SKIP}
    assert all("AQL runs only on the REST provider" in line.message for line in mcp.values())
    none = await _full(settings, provider, instances=[])
    assert {line.verdict for line in none.values()} == {doctor_aql.SKIP}
    assert all("no enabled QRadar console" in line.message for line in none.values())
    assert provider.calls == []


@pytest.mark.asyncio
async def test_an_mcp_console_next_to_a_rest_one_is_named_and_not_searched(
    settings: Settings,
) -> None:
    provider = FakeProvider()
    consoles = [_console("rest", "rest-console"), _console("mcp", "mcp-console")]
    line = (await _full(settings, provider, instances=consoles, families=("rule_match",)))[
        "rule_match"
    ]
    assert line.verdict == doctor_aql.PASS
    assert "mcp-console: AQL runs only on the REST provider" in line.message
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_a_missing_property_fails_without_a_search(settings: Settings) -> None:
    provider = FakeProvider(properties=())
    lines = await _full(settings, provider, families=("geo_collection", "qradar_health"))
    geo, health = lines["geo_collection"], lines["qradar_health"]
    assert geo.verdict == doctor_aql.FAIL
    assert "property absent" in geo.message
    assert "Source Geographic Location" in geo.message
    assert "GEO_SOURCE_COUNTRY_PROPERTY" in geo.message
    assert health.verdict == doctor_aql.FAIL
    assert "Metric ID, Value, Element" in health.message
    assert provider.calls == []


@pytest.mark.asyncio
async def test_an_unreadable_property_catalog_warns_without_a_search(settings: Settings) -> None:
    provider = FakeProvider()
    provider.list_custom_properties = AsyncMock(side_effect=ProviderError("boom"))  # type: ignore[method-assign]
    lines = await _full(settings, provider, families=("geo_collection", "qradar_health"))
    assert {line.verdict for line in lines.values()} == {doctor_aql.WARN}
    assert provider.calls == []


# ------------------------------------------------------------------ cost and safety
@pytest.mark.asyncio
async def test_searches_are_sequential_and_bounded_by_families_times_consoles(
    settings: Settings,
) -> None:
    provider = FakeProvider()
    consoles = [_console(name="a"), _console(name="b")]
    await _full(settings, provider, instances=consoles)
    assert provider.max_running == 1
    assert len(provider.calls) == 20
    assert len(provider.calls) <= len(doctor_aql.FAMILIES) * 2 * 2


@pytest.mark.asyncio
async def test_the_caller_recorded_is_doctor_aql_never_the_family(settings: Settings) -> None:
    provider = FakeProvider()
    await _full(settings, provider)
    assert provider.callers
    assert set(provider.callers) == {"DOCTOR_AQL"}
    assert "DOCTOR_AQL" in CALLERS
    # Outside the doctor the same method keeps its family.
    assert ariel_log.effective_caller("RULE_MATCH") == "RULE_MATCH"


def test_every_aql_check_points_to_the_runbook_and_the_ordinary_map_is_untouched() -> None:
    expected = tuple(f"aql_{family}" for family in doctor_aql.FAMILIES)
    assert expected == doctor_aql.CHECK_IDS
    assert len(doctor_aql.CHECK_IDS) == 9
    assert {runbook for _id, runbook in doctor_module._AQL_CHECK_RUNBOOKS} == {"OPS-R72"}
    assert [i for i, _ in doctor_module._AQL_CHECK_RUNBOOKS] == list(doctor_aql.CHECK_IDS)
    assert doctor_module._CHECK_RUNBOOKS[-1] == ("qradar_health", "OPS-R67")
    assert not any(i.startswith("aql_") for i, _ in doctor_module._CHECK_RUNBOOKS)


# ----------------------------------------------------------- the doctor and main()
def _doctor(settings: Settings, provider: FakeProvider, **kwargs: Any) -> Doctor:
    doctor = Doctor(
        settings=settings,
        sessionmaker=cast(Any, _sessionmaker(_waf_row(), _ips_row())),
        provider_factory=lambda _instance: cast(Any, provider),
        now=lambda: NOW,
        **kwargs,
    )
    for check_id, _runbook in doctor_module._CHECK_RUNBOOKS:
        setattr(doctor, f"check_{check_id}", AsyncMock(
            return_value=CheckResult(check_id, Status.PASS, "healthy")
        ))
    doctor._enabled_instances = AsyncMock(return_value=[_console()])  # type: ignore[method-assign]
    return doctor


@pytest.mark.asyncio
async def test_plain_doctor_sends_no_search(settings: Settings) -> None:
    provider = FakeProvider()
    results = await _doctor(settings, provider).run()
    assert provider.calls == []
    assert provider.callers == []
    assert not any(result.id.startswith("aql_") for result in results)


@pytest.mark.asyncio
async def test_aql_checks_follow_the_ordinary_ones_in_text_and_json(settings: Settings) -> None:
    provider = FakeProvider()
    results = await _doctor(settings, provider, check_aql=True).run()
    ids = [result.id for result in results]
    ordinary = [i for i, _ in doctor_module._CHECK_RUNBOOKS]
    assert ids == [*ordinary, *doctor_aql.CHECK_IDS]
    assert {r.runbook for r in results if r.id.startswith("aql_")} == {"OPS-R72"}
    payload = json.loads(render_json(results))
    assert [item["id"] for item in payload][-9:] == list(doctor_aql.CHECK_IDS)
    assert "aql_log_source_volume" in render_text(results)
    assert len(provider.calls) == 10


@pytest.mark.asyncio
async def test_an_unreadable_console_list_skips_every_aql_check(settings: Settings) -> None:
    provider = FakeProvider()
    doctor = _doctor(settings, provider, check_aql=True)
    doctor._enabled_instances = AsyncMock(side_effect=RuntimeError("db"))  # type: ignore[method-assign]
    results = (await doctor.run())[-9:]
    assert {r.status for r in results} == {Status.SKIP}
    assert provider.calls == []


@pytest.mark.parametrize(
    ("error", "code", "verdict"),
    [
        (None, 0, "PASS"),
        (TimeoutError(), 1, "WARN"),
        (ProviderQueryRejectedError(UPSTREAM_BODY, status_code=422), 2, "FAIL"),
    ],
)
def test_main_with_aql_and_json_exits_with_the_worst_verdict(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: BaseException | None,
    code: int,
    verdict: str,
) -> None:
    provider = FakeProvider(error=error)
    seen: list[dict[str, Any]] = []

    def build(**kwargs: Any) -> Doctor:
        seen.append(kwargs)
        return _doctor(settings, provider, **kwargs)

    async def no_dispose() -> None:
        return None

    monkeypatch.setattr(doctor_module, "Doctor", build)
    monkeypatch.setattr(doctor_module, "dispose_engine", no_dispose)
    monkeypatch.setattr(sys, "argv", ["doctor", "--aql", "--json"])
    assert doctor_module.main() == code
    assert seen == [{"check_aql": True}]
    payload = json.loads(capsys.readouterr().out)
    aql = [item for item in payload if item["id"].startswith("aql_")]
    assert len(aql) == 9
    if verdict == "PASS":
        assert {item["status"] for item in aql} == {"PASS"}
    else:
        assert any(item["status"] == verdict for item in aql)
    assert "fake-header-token" not in json.dumps(payload)


def test_the_docs_and_env_example_describe_the_checks_and_the_setting() -> None:
    root = pathlib.Path(__file__).resolve().parents[3]
    operations = (root / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
    assert "| `aql_<family>`" in operations
    assert "### OPS-R72." in operations
    entry = operations[operations.index("### OPS-R72.") :]
    for fragment in ("GEO_SOURCE_COUNTRY_PROPERTY", "doctor --aql --json", "WAF", "IPS", "watch"):
        assert fragment in entry, fragment
    for family in doctor_aql.FAMILIES:
        assert f"`aql_{family}`" in operations, family
    manual = (root / "docs" / "MANUAL-TEST.md").read_text(encoding="utf-8")
    results = (root / "docs" / "MANUAL-TEST-RESULTS.md").read_text(encoding="utf-8")
    assert "#### MT-136" in manual and "doctor --aql" in manual
    assert "| MT-136 | yes |" in results
    assert "DOCTOR_AQL_TIMEOUT_SECONDS=60" in (root / ".env.example").read_text(encoding="utf-8")
    assert "`DOCTOR_AQL_TIMEOUT_SECONDS`" in (root / "docs" / "CONFIGURATION.md").read_text(
        encoding="utf-8"
    )
