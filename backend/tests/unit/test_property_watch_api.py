"""Property-watch API, the parts that need no database (design §G.11, §G.12).

Permissions and the mount guard, schema bounds, the draft validator (every reason at once, no
reason echoing a pinned value), the audit digest, the capacity arithmetic, the group walk and
the many-types catalog filter.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.main import create_app
from app.models.enums import PwAnomalyType, PwScopeKind
from app.models.fleet import LogSourceGroup
from app.property_watch.reference import PW_NORMALIZED_FIELD_ALLOWLIST
from app.providers.dto import CustomPropertyDTO
from app.schemas.property_watch import (
    PinsIn,
    PropertyWatchCreate,
    PropertyWatchUpdate,
)
from app.security.rbac import (
    PERM_PROPERTY_WATCH_READ,
    PERM_PROPERTY_WATCH_WRITE,
    Principal,
)
from app.services import property_watches as svc
from app.services.property_catalog import PropertyCatalogService
from app.services.seed import _ROLES

PREFIX = "/api/v1/property-watches"
_NORMALIZED = {"kind": "NORMALIZED", "name": "username"}
_CUSTOM = {"kind": "CUSTOM_PROPERTY", "name": "WAF_Action"}


# ------------------------------------------------------------------------ permissions
def test_constants_name_the_capabilities() -> None:
    assert PERM_PROPERTY_WATCH_READ == "read:property_watches"
    assert PERM_PROPERTY_WATCH_WRITE == "property_watch:write"


def test_read_star_grants_the_read_and_not_the_write() -> None:
    reader = Principal(subject="reader", permissions=frozenset({"read:*"}))
    assert reader.has(PERM_PROPERTY_WATCH_READ)
    assert not reader.has(PERM_PROPERTY_WATCH_WRITE)


def test_admin_star_grants_the_write_without_the_string() -> None:
    assert "property_watch:write" not in _ROLES["admin"]
    admin = Principal(subject="admin", permissions=frozenset(_ROLES["admin"]))
    assert admin.has(PERM_PROPERTY_WATCH_WRITE)
    assert admin.has(PERM_PROPERTY_WATCH_READ)


def test_only_the_detection_engineer_role_gains_the_write() -> None:
    assert "property_watch:write" in _ROLES["detection_engineer"]
    for role in ("analyst", "viewer", "admin"):
        assert "property_watch:write" not in _ROLES[role]
    analyst = set(_ROLES["analyst"])
    assert analyst <= set(_ROLES["detection_engineer"])
    # Additive: the engineer still holds the Phase F write beside the new one.
    assert "custom_rule:write" in _ROLES["detection_engineer"]


def test_the_write_does_not_imply_the_read() -> None:
    writer = Principal(subject="w", permissions=frozenset({PERM_PROPERTY_WATCH_WRITE}))
    assert not writer.has(PERM_PROPERTY_WATCH_READ)


# ---------------------------------------------------------------------- the mount guard
def _walk(items):  # type: ignore[no-untyped-def]
    for route in items:
        if hasattr(route, "effective_candidates"):
            yield from _walk(route.effective_candidates())
            yield from _walk(route.effective_low_priority_routes())
        else:
            yield route


def _watch_routes() -> list:
    app = create_app()
    return [r for r in _walk(app.routes) if getattr(r, "path", "").startswith(PREFIX)]


def test_every_route_on_the_router_carries_the_mount_guard() -> None:
    """The guard is a property of the mount: an endpoint added later cannot ship without it."""
    routes = _watch_routes()
    assert len(routes) >= 13
    for route in routes:
        guards = [
            [cell.cell_contents for cell in (dep.dependency.__closure__ or ())]
            for dep in route.dependencies
            if dep.dependency.__qualname__.startswith("requires.")
        ]
        assert guards == [[PERM_PROPERTY_WATCH_READ]], f"{route.methods} {route.path}"


def test_the_expected_routes_exist() -> None:
    found = {(m, r.path) for r in _watch_routes() for m in r.methods}
    expected = {
        ("GET", f"{PREFIX}/catalog"),
        ("GET", f"{PREFIX}/scope-options"),
        ("GET", f"{PREFIX}/property-options"),
        ("GET", PREFIX),
        ("POST", PREFIX),
        ("GET", f"{PREFIX}/{{watch_id}}"),
        ("PATCH", f"{PREFIX}/{{watch_id}}"),
        ("DELETE", f"{PREFIX}/{{watch_id}}"),
        ("GET", f"{PREFIX}/{{watch_id}}/buckets"),
        ("GET", f"{PREFIX}/{{watch_id}}/values"),
        ("GET", f"{PREFIX}/{{watch_id}}/series"),
        ("POST", f"{PREFIX}/{{watch_id}}/preview"),
        ("PUT", f"{PREFIX}/{{watch_id}}/pins"),
    }
    assert expected <= found
    # The two routes that need the scope resolver are PW-5b's, not placeholders here.
    assert ("GET", f"{PREFIX}/{{watch_id}}/scope") not in found


def test_static_routes_precede_the_watch_id_route() -> None:
    """`catalog` is not a watch id: it must be registered before `/{watch_id}`."""
    paths = [r.path for r in _watch_routes() if "GET" in r.methods]
    for static in ("catalog", "scope-options", "property-options"):
        assert paths.index(f"{PREFIX}/{static}") < paths.index(f"{PREFIX}/{{watch_id}}")


# ------------------------------------------------------------------------------ schemas
def _create(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": "w",
        "scope_kind": "LOG_SOURCE_TYPE",
        "scope_type_id": 1,
        "properties": [_NORMALIZED],
    }
    body.update(overrides)
    return body


def test_a_watch_cannot_be_created_enabled() -> None:
    with pytest.raises(ValidationError):
        PropertyWatchCreate.model_validate(_create(enabled=True))


def test_create_defaults_are_disabled_shape() -> None:
    model = PropertyWatchCreate.model_validate(_create())
    assert model.pinned_values == []
    assert model.detectors is None
    assert model.severity.value == "MEDIUM"


def test_an_unknown_detector_or_reference_kind_is_a_schema_error() -> None:
    with pytest.raises(ValidationError):
        PropertyWatchCreate.model_validate(_create(detectors=["PW_NOPE"]))
    with pytest.raises(ValidationError):
        PropertyWatchCreate.model_validate(
            _create(properties=[{"kind": "SOMETHING", "name": "x"}])
        )


def test_the_tokens_need_a_timezone() -> None:
    with pytest.raises(ValidationError):
        PropertyWatchUpdate.model_validate({"expected_updated_at": "2026-01-01T00:00:00"})
    with pytest.raises(ValidationError):
        PinsIn.model_validate(
            {"expected_updated_at": "2026-01-01T00:00:00", "pinned_values": []}
        )
    ok = PinsIn.model_validate(
        {"expected_updated_at": "2026-01-01T02:00:00+02:00", "pinned_values": []}
    )
    assert ok.expected_updated_at == datetime(2026, 1, 1, tzinfo=UTC)


def test_update_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PropertyWatchUpdate.model_validate(
            {"expected_updated_at": "2026-01-01T00:00:00Z", "watch_version": 9}
        )


def test_hours_bounds_are_the_f1_preview_bounds() -> None:
    assert (svc.HOURS_MIN, svc.HOURS_MAX, svc.HOURS_DEFAULT) == (1, 168, 24)


# --------------------------------------------------------------------------- validation
def _draft(**overrides: object) -> svc.WatchDraft:
    values: dict[str, object] = {
        "scope_kind": PwScopeKind.LOG_SOURCE_TYPE,
        "log_source_id": None,
        "scope_type_id": 1,
        "scope_group_id": None,
        "properties": [_NORMALIZED],
        "pinned_values": [],
        "detectors": ["PW_VALUE_SPIKE"],
    }
    values.update(overrides)
    return svc.WatchDraft(**values)  # type: ignore[arg-type]


def test_a_valid_draft_has_no_reasons() -> None:
    assert svc.validate_draft(_draft(), max_pinned=10) == []
    assert (
        svc.validate_draft(
            _draft(properties=[_NORMALIZED, _CUSTOM], pinned_values=[["a", "b"]]), max_pinned=10
        )
        == []
    )


def test_every_reason_comes_back_at_once() -> None:
    draft = _draft(
        scope_kind=PwScopeKind.LOG_SOURCE,
        log_source_id=None,
        scope_type_id=5,
        properties=[
            {"kind": "NORMALIZED", "name": "sourceip"},
            {"kind": "CUSTOM_PROPERTY", "name": "bad;name"},
            _CUSTOM,
        ],
        pinned_values=[["ok", "x"], ["it's"], ["ok", "x"]],
        detectors=[],
    )
    reasons = svc.validate_draft(draft, max_pinned=2)
    joined = " | ".join(reasons)
    assert "log_source_id is required" in joined
    assert "scope_type_id must be empty" in joined
    assert "one or two properties, not 3" in joined
    assert "property 1" in joined and "property 2" in joined
    assert "pins at most 2" in joined
    assert "pinned value 2" in joined
    assert "pinned twice" in joined
    assert "at least one detector" in joined
    assert len(reasons) >= 8


def test_no_reason_echoes_a_pinned_value() -> None:
    reasons = svc.validate_draft(
        _draft(pinned_values=[["Secret'Quote"], ["Other", "Extra"]]), max_pinned=10
    )
    assert reasons
    assert all("Secret" not in r and "Other" not in r and "Extra" not in r for r in reasons)


def test_pin_width_follows_the_property_count() -> None:
    one = svc.validate_draft(_draft(pinned_values=[["a", "b"]]), max_pinned=10)
    assert any("needs 1 string" in r for r in one)
    two = svc.validate_draft(
        _draft(properties=[_NORMALIZED, _CUSTOM], pinned_values=[["a"]]), max_pinned=10
    )
    assert any("needs 2 string" in r for r in two)


def test_a_new_combination_needs_two_properties() -> None:
    reasons = svc.validate_draft(_draft(detectors=["PW_NEW_COMBINATION"]), max_pinned=10)
    assert any("PW_NEW_COMBINATION needs a watch with two properties" in r for r in reasons)
    assert not svc.validate_draft(
        _draft(properties=[_NORMALIZED, _CUSTOM], detectors=["PW_NEW_COMBINATION"]),
        max_pinned=10,
    )


def test_a_normalized_field_outside_the_allowlist_is_refused() -> None:
    reasons = svc.validate_draft(
        _draft(properties=[{"kind": "NORMALIZED", "name": "category"}]), max_pinned=10
    )
    assert any("allowlist" in r for r in reasons)
    assert PW_NORMALIZED_FIELD_ALLOWLIST == ("username",)


def test_the_scope_columns_must_match_the_kind() -> None:
    both = svc.validate_draft(_draft(scope_group_id=3), max_pinned=10)
    assert any("scope_group_id must be empty" in r for r in both)
    negative = svc.validate_draft(_draft(scope_type_id=-1), max_pinned=10)
    assert any("must not be negative" in r for r in negative)


def test_default_detectors_follow_the_property_count() -> None:
    one = svc.default_detectors(1)
    two = svc.default_detectors(2)
    assert PwAnomalyType.PW_NEW_COMBINATION not in one
    assert set(two) == set(PwAnomalyType)
    assert set(one) | {PwAnomalyType.PW_NEW_COMBINATION} == set(PwAnomalyType)


# ------------------------------------------------------------------------------ digest
def test_the_digest_is_order_free_prefixed_and_value_free() -> None:
    a = svc.pinned_digest([["alice"], ["bob"]])
    b = svc.pinned_digest([["bob"], ["alice"]])
    assert a == b
    assert a.startswith("sha256:") and len(a) == len("sha256:") + 64
    assert "alice" not in a and "bob" not in a
    assert svc.pinned_digest([["alice"]]) != a
    assert svc.pinned_digest([]) != a


# ----------------------------------------------------------------------------- capacity
def test_search_cost_is_chunks_times_two_or_three() -> None:
    cost = svc.search_cost
    assert cost(PwScopeKind.LOG_SOURCE, 1, pinned=False, max_sources=50) == 2
    assert cost(PwScopeKind.LOG_SOURCE_TYPE, 9999, pinned=True, max_sources=50) == 3
    assert cost(PwScopeKind.LOG_SOURCE_GROUP, 37, pinned=True, max_sources=50) == 3
    assert cost(PwScopeKind.LOG_SOURCE_GROUP, 120, pinned=True, max_sources=50) == 9
    assert cost(PwScopeKind.LOG_SOURCE_GROUP, 200, pinned=True, max_sources=50) == 12
    assert cost(PwScopeKind.LOG_SOURCE_GROUP, 0, pinned=False, max_sources=50) == 2


def _group(gid: int, parent: int | None) -> LogSourceGroup:
    return LogSourceGroup(
        instance_id=uuid.uuid4(), qradar_id=gid, name=f"g{gid}", parent_qradar_id=parent,
        assignable=True, synced_at=datetime.now(UTC),
    )


def test_the_group_walk_includes_descendants_and_survives_a_cycle() -> None:
    children = svc._children([_group(1, None), _group(2, 1), _group(3, 2), _group(4, None)])
    assert svc._descendants(1, children) == (1, 2, 3)
    # The lab's own two groups name each other as parent.
    cyclic = svc._children([_group(0, 1), _group(1, 0)])
    assert set(svc._descendants(0, cyclic)) == {0, 1}
    assert set(svc._descendants(1, cyclic)) == {0, 1}


def test_group_path_is_cycle_safe() -> None:
    groups = {g.qradar_id: g for g in (_group(0, 1), _group(1, 0))}
    assert svc.group_path(0, groups) in {"g0 / g1", "g1 / g0"}
    tree = {g.qradar_id: g for g in (_group(1, None), _group(2, 1))}
    assert svc.group_path(2, tree) == "g1 / g2"


# ------------------------------------------------------------------------------ preview
def test_the_preview_window_is_the_last_closed_hour_on_a_bucket_edge() -> None:
    settings = get_settings().model_copy(
        update={"collection_interval_seconds": 300, "search_cadence_settle_seconds": 120}
    )
    now = datetime(2026, 10, 17, 10, 7, 30, tzinfo=UTC)
    start, end, bucket = svc.preview_window(settings, now)
    assert bucket == 300
    assert end == datetime(2026, 10, 17, 10, 5, tzinfo=UTC)
    assert end - start == timedelta(hours=1)
    assert start.timestamp() % 300 == 0
    # The settle delay holds the newest bucket back until it has had time to land.
    just_closed = datetime(2026, 10, 17, 10, 5, 30, tzinfo=UTC)
    assert svc.preview_window(settings, just_closed)[1] == datetime(
        2026, 10, 17, 10, 0, tzinfo=UTC
    )


# -------------------------------------------------------------------- many-types filter
def _props() -> list[CustomPropertyDTO]:
    def prop(name: str, types: tuple[int, ...], allowed: bool = True) -> CustomPropertyDTO:
        return CustomPropertyDTO(
            qradar_id=abs(hash(name)) % 10_000,
            identifier=f"id-{name}",
            name=name,
            property_type="string",
            use_for_rule_engine=False,
            log_source_type_ids=types,
            name_allowed=allowed,
        )

    return [
        prop("Zeta", (1, 2)),
        prop("Alpha", (2,)),
        prop("Nowhere", ()),
        prop("Bad.Name(1)", (1, 2), allowed=False),
    ]


class _Provider:
    def __init__(self, props: list[CustomPropertyDTO], *, fail: bool = False) -> None:
        self.props, self.fail, self.closed = props, fail, 0

    async def list_custom_properties(self) -> list[CustomPropertyDTO]:
        if self.fail:
            raise RuntimeError("https://qradar.internal/api token=abc")
        return self.props

    async def aclose(self) -> None:
        self.closed += 1


async def test_the_many_types_filter_lists_the_defining_types() -> None:
    service = PropertyCatalogService(ttl_seconds=60)
    provider = _Provider(_props())
    out = await service.catalog_for_types(
        instance_id="i", provider_factory=lambda: provider, source_type_ids=[2, 1, 2],  # type: ignore[arg-type,return-value]
    )
    assert out.status == "AVAILABLE"
    by_name = {p.name: p.defined_on_type_ids for p in out.properties}
    assert by_name["Zeta"] == (1, 2)
    assert by_name["Alpha"] == (2,)
    assert by_name["Nowhere"] == ()
    # Defined on at least one scope type first, then by name; a property no scope type
    # defines is still listed (the catalog is a suggestion, never a gate).
    names = [p.name for p in out.properties]
    assert names[:3] == ["Alpha", "Bad.Name(1)", "Zeta"] and names[-1] == "Nowhere"
    assert [p.name_allowed for p in out.properties if p.name == "Bad.Name(1)"] == [False]


async def test_the_many_types_filter_shares_the_cache_with_the_one_type_filter() -> None:
    service = PropertyCatalogService(ttl_seconds=60)
    built = 0

    def factory() -> _Provider:
        nonlocal built
        built += 1
        return _Provider(_props())

    await service.catalog_for_types(
        instance_id="i", provider_factory=factory, source_type_ids=[1]  # type: ignore[arg-type]
    )
    one = await service.catalog(
        instance_id="i", provider_factory=factory, source_type_id=1  # type: ignore[arg-type]
    )
    assert built == 1
    assert one.status == "AVAILABLE"


async def test_the_many_types_filter_fails_with_the_fixed_sentence() -> None:
    service = PropertyCatalogService(ttl_seconds=60)
    out = await service.catalog_for_types(
        instance_id="i",
        provider_factory=lambda: _Provider([], fail=True),  # type: ignore[arg-type,return-value]
        source_type_ids=[1],
    )
    assert out.status == "UNAVAILABLE" and out.properties == []
    assert out.detail is not None
    assert out.detail.startswith("RuntimeError: the property list could not be read")
    assert "qradar.internal" not in out.detail and "token" not in out.detail


async def test_an_empty_type_set_lists_every_property_with_none_defined() -> None:
    service = PropertyCatalogService(ttl_seconds=60)
    out = await service.catalog_for_types(
        instance_id="i", provider_factory=lambda: _Provider(_props()),  # type: ignore[arg-type,return-value]
        source_type_ids=[],
    )
    assert all(p.defined_on_type_ids == () for p in out.properties)
    assert len(out.properties) == 4
