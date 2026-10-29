"""Property-watch API (design §G.11): guards, validation, caps, version, pins, reads, preview.

Driven off the OpenAPI document so a new `/property-watches` route cannot ship without the
mount guard. The preview runs on the mock provider, which counts the searches it is asked for.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.routes.property_watches import get_provider_factory
from app.core.config import get_settings
from app.core.database import get_session
from app.core.rate_limit import limiter
from app.main import create_app
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    EvidenceStatus,
    PwAnomalyType,
    Severity,
)
from app.models.fleet import LogSourceGroup, LogSourceType
from app.models.identity import AuditLog
from app.models.log_source import LogSource, LogSourceAnomaly
from app.models.property_watch import (
    PropertyWatch,
    PwAnomaly,
    PwBucket,
    PwValue,
    PwValueBaseline,
    PwValueMetric,
)
from app.property_watch.facts import TUPLE_SEPARATOR
from app.property_watch.inputs import PW_VALUE_EVENTS_METRIC
from app.providers.base import ProviderUnavailableError
from app.providers.dto import PwSearchKind
from app.providers.mock import MockQRadarProvider
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.ariel_permit import _ARIEL_PRIORITY
from app.services.property_catalog import PropertyCatalogService, get_property_catalog_service
from app.services.property_watches import pinned_digest
from app.services.seed import _ROLES
from tests.integration.factories import make_instance, make_log_source

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run property-watch API integration tests",
    ),
]

NIL = "00000000-0000-0000-0000-000000000000"
PREFIX = "/api/v1/property-watches"
NOBODY = Principal(subject="nobody", permissions=frozenset())
TYPE_ID = 212
GROUP_ID = 10
ACTION = {"kind": "CUSTOM_PROPERTY", "name": "WAF_Action"}
USER = {"kind": "NORMALIZED", "name": "username"}
SEEDED = ("SeededDave", "SeededErin", "PinnedBob", "PinnedCarol", "PinnedAmy")


@pytest.fixture(autouse=True)
def _reset_rate_limit():  # type: ignore[no-untyped-def]
    limiter.reset()
    yield
    limiter.reset()


def _principal(role: str) -> Principal:
    return Principal(subject=role, permissions=frozenset(_ROLES[role]))


def _client(
    db_url: str,
    principal: Principal | None,
    *,
    provider: MockQRadarProvider | None = None,
    **overrides: object,
) -> TestClient:
    engine = create_async_engine(db_url, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    statements: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)

    async def _session():  # type: ignore[no-untyped-def]
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    updates: dict[str, object] = {"collection_interval_seconds": 300}
    updates.update(overrides)
    settings = get_settings().model_copy(update=updates)
    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings
    catalog = PropertyCatalogService(ttl_seconds=60)
    app.dependency_overrides[get_property_catalog_service] = lambda: catalog
    if principal is not None:
        app.dependency_overrides[get_principal] = lambda: principal
    if provider is not None:
        app.dependency_overrides[get_provider_factory] = lambda: lambda _instance: provider
    client = TestClient(app)
    client.statements = statements  # type: ignore[attr-defined]
    return client


def _anonymous(db_url: str) -> TestClient:
    async def _no_session():  # type: ignore[no-untyped-def]
        yield None

    settings = get_settings().model_copy(update={"auth_provider": "oidc"})
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = _no_session
    return TestClient(app)


def _operations(client: TestClient) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path, operations in client.app.openapi()["paths"].items():  # type: ignore[attr-defined]
        if not path.startswith(PREFIX):
            continue
        concrete = path.replace("{watch_id}", NIL)
        assert "{" not in concrete, path
        for method in operations:
            if method in ("get", "post", "patch", "put", "delete"):
                found.append((method.upper(), concrete))
    return found


async def _seed(session) -> SimpleNamespace:  # type: ignore[no-untyped-def]
    """One instance, a source of type 212, a type row, and a two-level group with members."""
    instance = await make_instance(session)
    now = datetime.now(UTC)
    source = await make_log_source(
        session, instance, qradar_id=701, name="fw-1", type_id=TYPE_ID, type_name="WAF",
        group_ids=[GROUP_ID],
    )
    other = await make_log_source(
        session, instance, qradar_id=702, name="fw-2", type_id=TYPE_ID, type_name="WAF",
        group_ids=[GROUP_ID + 1],
    )
    session.add(
        LogSourceType(
            instance_id=instance.id, type_id=TYPE_ID, name="Web Application Firewall",
            internal=False, custom=False, synced_at=now,
        )
    )
    session.add_all(
        [
            LogSourceGroup(
                instance_id=instance.id, qradar_id=GROUP_ID, name="Edge",
                parent_qradar_id=None, assignable=True, synced_at=now,
            ),
            LogSourceGroup(
                instance_id=instance.id, qradar_id=GROUP_ID + 1, name="Edge DMZ",
                parent_qradar_id=GROUP_ID, assignable=True, synced_at=now,
            ),
        ]
    )
    await session.commit()
    return SimpleNamespace(instance=instance, source=source, other=other, now=now)


def _body(name: str, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": name,
        "scope_kind": "LOG_SOURCE_TYPE",
        "scope_type_id": TYPE_ID,
        "properties": [ACTION],
    }
    body.update(overrides)
    return body


def _create(client: TestClient, name: str, **overrides: object) -> dict:
    response = client.post(PREFIX, json=_body(name, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


async def _audits(session, action: str | None = None) -> list[AuditLog]:  # type: ignore[no-untyped-def]
    session.expire_all()
    query = select(AuditLog).order_by(AuditLog.created_at)
    if action is not None:
        query = query.where(AuditLog.action == action)
    return list((await session.scalars(query)).all())


async def _row(session, watch_id: str) -> PropertyWatch:  # type: ignore[no-untyped-def]
    """The stored row, read fresh: what the route's transaction left behind."""
    session.expire_all()
    return await session.get(PropertyWatch, uuid.UUID(watch_id))


async def _count(session, model) -> int:  # type: ignore[no-untyped-def]
    session.expire_all()
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


def _same_instant(a: str, b: str) -> bool:
    return datetime.fromisoformat(a) == datetime.fromisoformat(b)


def _bucket_start(back: int) -> datetime:
    edge = int(datetime.now(UTC).timestamp()) // 300 * 300
    return datetime.fromtimestamp(edge - 300 * back, UTC)


def _bucket(watch_id: str, back: int, **kw: object) -> PwBucket:
    values: dict[str, object] = dict(
        watch_id=uuid.UUID(watch_id), bucket_start=_bucket_start(back), bucket_seconds=300,
        watch_version=1, total_events=100, parsed_events=90, unparsed_events=10,
        untracked_events=0, values_measurement="EXACT", pinned_measurement="NOT_REQUESTED",
        totals_complete=True, observed_values=2, tracked_values_observed=2, admitted_values=0,
        below_admission_threshold=0, rejected_over_quota=0, admission_truncated=False,
        scope_member_count=None, scope_changed=False, in_maintenance=False,
        collected_at=datetime.now(UTC),
    )
    values.update(kw)
    return PwBucket(**values)  # type: ignore[arg-type]


async def _bulk_tree(session, *, groups: int, sources: int, base: int):  # type: ignore[no-untyped-def]
    """A fresh instance with a `groups`-group chain under `base`, holding `sources` members.

    Written in bulk (one `INSERT` each) so a 200-source scope is cheap to build. The type row
    lets the same tree resolve a type scope too.
    """
    instance = await make_instance(session)
    now = datetime.now(UTC)
    session.add(
        LogSourceType(
            instance_id=instance.id, type_id=TYPE_ID, name="Web Application Firewall",
            internal=False, custom=False, synced_at=now,
        )
    )
    session.add_all(
        [
            LogSourceGroup(
                instance_id=instance.id, qradar_id=base + index, name=f"g{index}",
                parent_qradar_id=None if index == 0 else base + index - 1, assignable=True,
                synced_at=now,
            )
            for index in range(groups)
        ]
    )
    session.add_all(
        [
            LogSource(
                instance_id=instance.id, qradar_id=base + 1000 + index, name=f"src-{index:04d}",
                type_id=TYPE_ID, type_name="WAF", group_ids=[base + index % groups],
            )
            for index in range(sources)
        ]
    )
    await session.commit()
    return instance


#: What a `/scope` read costs, pinned: the watch, the inventory (the group walk and its
#: sources), the walked group names, the member names and the stored changes. Flat at any
#: number of groups or sources (AGENTS.md rule 9).
SCOPE_STATEMENTS = {"group": 6, "type": 4, "source": 4}
#: What `scope-options` costs, pinned: the inventory and the resolver's one bulk read. Flat
#: at any size. With an explicit `instance_id` there is no `_default_instance` statement.
OPTIONS_STATEMENTS = {"group": 3, "type": 2, "source": 1}
#: What `property-options` costs, pinned: for a group scope, the group's existence check, the
#: resolver's two bulk reads (the walk and its sources), and the instance row; for a type
#: scope, the instance row alone. Flat at any number of groups or sources. With an explicit
#: `instance_id` there is no `_default_instance` statement; the editor's call (no
#: `instance_id`) adds one (PW-5b §5 point 4, PW-3c item 6).
PROPERTY_OPTIONS_STATEMENTS = {"group": 4, "type": 1}


# ----------------------------------------------------------------------------- guards
def test_every_property_watch_route_without_a_token_is_401(db_schema: str) -> None:
    client = _anonymous(db_schema)
    operations = _operations(client)
    assert len(operations) >= 13
    for method, path in operations:
        response = client.request(method, path, json={})
        assert response.status_code == 401, f"{method} {path} -> {response.status_code}"
        assert response.json()["detail"] == "missing bearer token"


def test_every_property_watch_route_without_read_is_403(db_schema: str) -> None:
    client = _client(db_schema, NOBODY)
    for method, path in _operations(client):
        response = client.request(method, path, json={})
        assert response.status_code == 403, f"{method} {path} -> {response.status_code}"
        assert response.json()["detail"] == "missing required permission: read:property_watches"


async def test_each_role_reads_and_only_writers_write(db_session, db_schema: str) -> None:
    """Readers read everything. Only the detection engineer and admin write.

    The admin grant is `admin:*` alone: the role does not list `property_watch:write`.
    """
    await _seed(db_session)
    provider = MockQRadarProvider(seed=3)
    admin = _client(db_schema, _principal("admin"), provider=provider)
    shared = _create(admin, "shared")
    path = f"{PREFIX}/{shared['id']}"
    token = shared["updated_at"]
    reads = [
        PREFIX,
        f"{PREFIX}/catalog",
        f"{PREFIX}/scope-options?kind=LOG_SOURCE",
        f"{PREFIX}/property-options?scope_kind=LOG_SOURCE_TYPE&scope_id={TYPE_ID}",
        path,
        f"{path}/scope",
        f"{path}/buckets",
        f"{path}/values",
        f"{path}/series?value_ids={NIL}",
    ]
    for role in ("admin", "analyst", "viewer", "detection_engineer"):
        client = _client(db_schema, _principal(role), provider=provider)
        for url in reads:
            status = client.get(url).status_code
            # The series of an unknown value is the one honest 422 in the list.
            assert status == (422 if "series" in url else 200), f"{role} {url} {status}"
    denied = "missing required permission: property_watch:write"
    for role in ("analyst", "viewer"):
        client = _client(db_schema, _principal(role), provider=provider)
        answers = [
            client.post(PREFIX, json=_body(f"{role}-w")),
            client.patch(path, json={"expected_updated_at": token, "description": "x"}),
            client.delete(path),
            client.put(path + "/pins", json={"expected_updated_at": token, "pinned_values": []}),
            client.post(path + "/preview"),
        ]
        for answer in answers:
            assert answer.status_code == 403, f"{role}: {answer.text}"
            assert answer.json()["detail"] == denied
    assert provider.pw_searches == [], "a refused preview must not spend a search"
    for role in ("detection_engineer", "admin"):
        client = _client(db_schema, _principal(role), provider=provider)
        made = client.post(PREFIX, json=_body(f"{role}-w"))
        assert made.status_code == 201, made.text
        assert client.post(path + "/preview").status_code == 200


async def test_unknown_ids_are_404_for_a_writer(db_session, db_schema: str) -> None:
    await _seed(db_session)
    client = _client(db_schema, _principal("detection_engineer"), provider=MockQRadarProvider())
    stale = "2026-01-01T00:00:00Z"
    answers = [
        client.get(f"{PREFIX}/{NIL}"),
        client.patch(f"{PREFIX}/{NIL}", json={"expected_updated_at": stale}),
        client.delete(f"{PREFIX}/{NIL}"),
        client.get(f"{PREFIX}/{NIL}/scope"),
        client.get(f"{PREFIX}/{NIL}/buckets"),
        client.get(f"{PREFIX}/{NIL}/values"),
        client.get(f"{PREFIX}/{NIL}/series?value_ids={NIL}"),
        client.post(f"{PREFIX}/{NIL}/preview"),
        client.put(
            f"{PREFIX}/{NIL}/pins", json={"expected_updated_at": stale, "pinned_values": []}
        ),
    ]
    for answer in answers:
        assert answer.status_code == 404, answer.text
    assert client.get(f"{PREFIX}/not-a-uuid").status_code == 422


# ------------------------------------------------------------------------- validation
async def test_create_is_disabled_and_every_422_reason_comes_at_once(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _client(db_schema, _principal("detection_engineer"))
    made = _create(client, "first", pinned_values=[["DENY"]])
    assert made["enabled"] is False
    assert made["watch_version"] == 1
    assert made["status"] is None, "a never-run watch is NULL, which is not OK"
    assert made["pinned_values"] == [["DENY"]]
    assert PwAnomalyType.PW_NEW_COMBINATION.value not in made["detectors"]
    assert client.post(PREFIX, json=_body("sneaky", enabled=True)).status_code == 422

    bad = client.post(
        PREFIX,
        json={
            "name": "bad",
            "scope_kind": "LOG_SOURCE",
            "scope_type_id": 5,
            "properties": [
                {"kind": "NORMALIZED", "name": "sourceip"},
                {"kind": "CUSTOM_PROPERTY", "name": "bad;name"},
                ACTION,
            ],
            "pinned_values": [["it's"], ["x", "y"]],
            "detectors": ["PW_NEW_COMBINATION"],
        },
    )
    assert bad.status_code == 422
    detail = bad.json()["detail"]
    assert set(detail) == {"message", "reasons", "reason_codes"}
    assert len(detail["reason_codes"]) == len(detail["reasons"])
    joined = " | ".join(detail["reasons"])
    for needle in (
        "log_source_id is required",
        "scope_type_id must be empty",
        "one or two properties, not 3",
        "property 1",
        "property 2",
        "pinned value 1",
        "PW_NEW_COMBINATION",
    ):
        assert needle in joined, needle
    assert "it's" not in bad.text
    assert detail["message"] == "; ".join(detail["reasons"])

    missing = client.post(PREFIX, json=_body("ghost", scope_type_id=999))
    assert missing.status_code == 422
    assert "log source type 999 is not in the synced inventory" in missing.text
    ghost_group = client.post(
        PREFIX, json=_body("ghost-group", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                           scope_group_id=424242),
    )
    assert ghost_group.status_code == 422
    assert client.post(PREFIX, json=_body("ghost-src", scope_kind="LOG_SOURCE",
                                          scope_type_id=None, log_source_id=NIL)).status_code == 422
    assert client.post(PREFIX, json=_body("range", scope_type_id="x")).status_code == 422
    assert (
        client.post(PREFIX, json=_body("kind", properties=[{"kind": "X", "name": "a"}])).status_code
        == 422
    )


async def test_the_source_scope_takes_its_instance_and_a_duplicate_name_is_409(
    db_session, db_schema: str
) -> None:
    world = await _seed(db_session)
    client = _client(db_schema, _principal("detection_engineer"))
    made = _create(
        client, "by-source", scope_kind="LOG_SOURCE", scope_type_id=None,
        log_source_id=str(world.source.id), properties=[USER],
    )
    assert made["instance_id"] == str(world.instance.id)
    assert made["scope"] == {
        "kind": "LOG_SOURCE", "name": "fw-1", "detail": "WAF", "qradar_id": 701,
        "member_count": 1,
    }
    dup = client.post(PREFIX, json=_body("by-source"))
    assert dup.status_code == 409
    assert "already exists" in dup.json()["detail"]
    assert dup.json()["detail_code"] == "propertywatch.name_exists"
    assert dup.json()["detail_params"] == {"name": "by-source"}


async def test_the_patch_carries_a_version_and_a_stale_one_is_409(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _client(db_schema, _principal("detection_engineer"))
    made = _create(client, "versioned")
    path = f"{PREFIX}/{made['id']}"
    first = client.patch(path, json={"expected_updated_at": made["updated_at"], "description": "a"})
    assert first.status_code == 200, first.text
    assert first.json()["updated_at"] > made["updated_at"]
    stale = client.patch(path, json={"expected_updated_at": made["updated_at"], "description": "b"})
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert _same_instant(detail["current_version"], first.json()["updated_at"])
    assert detail["updated_by"] == "detection_engineer"
    assert client.get(path).json()["description"] == "a"
    nulls = client.patch(
        path, json={"expected_updated_at": first.json()["updated_at"], "name": None}
    )
    assert nulls.status_code == 422
    assert "name must not be null" in nulls.text
    no_tz = client.patch(path, json={"expected_updated_at": "2026-01-01T00:00:00"})
    assert no_tz.status_code == 422


async def test_watch_version_bumps_on_scope_or_properties_only(
    db_session, db_schema: str
) -> None:
    world = await _seed(db_session)
    client = _client(db_schema, _principal("detection_engineer"))
    made = _create(client, "semantics")
    path = f"{PREFIX}/{made['id']}"
    token = made["updated_at"]

    def patch(**body: object) -> dict:
        nonlocal token
        response = client.patch(path, json={"expected_updated_at": token, **body})
        assert response.status_code == 200, response.text
        token = response.json()["updated_at"]
        return response.json()

    assert patch(pinned_values=[["DENY"]])["watch_version"] == 1
    assert patch(severity="HIGH")["watch_version"] == 1
    assert patch(name="semantics-2", description="d")["watch_version"] == 1
    assert patch(enabled=True)["watch_version"] == 1
    assert patch(enabled=False)["watch_version"] == 1
    assert patch(detectors=["PW_VALUE_SPIKE"])["watch_version"] == 1
    renamed = patch(properties=[{"kind": "CUSTOM_PROPERTY", "name": "WAF_URL"}])
    assert renamed["watch_version"] == 2
    moved = patch(
        scope_kind="LOG_SOURCE", log_source_id=str(world.source.id),
        properties=[{"kind": "CUSTOM_PROPERTY", "name": "WAF_URL"}],
    )
    assert moved["watch_version"] == 3
    assert moved["scope_type_id"] is None and moved["log_source_id"] == str(world.source.id)
    # A patch that changes nothing is not a change: no bump, no audit, the token holds.
    same = client.patch(path, json={"expected_updated_at": token, "severity": "HIGH"})
    assert same.status_code == 200 and same.json()["updated_at"] == token
    # A scope that names two columns is a reason, not a silent pick.
    both = client.patch(
        path, json={"expected_updated_at": token, "scope_kind": "LOG_SOURCE_TYPE",
                    "scope_type_id": TYPE_ID, "log_source_id": str(world.source.id)},
    )
    assert both.status_code == 422 and "log_source_id must be empty" in both.text


# ------------------------------------------------------------------------- the caps
async def test_enable_409s_name_the_cap_the_budget_and_the_scope(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    # The enabled-watch cap.
    client = _client(db_schema, _principal("detection_engineer"), pw_max_enabled_watches=1)
    one, two = _create(client, "cap-1"), _create(client, "cap-2")
    on = client.patch(f"{PREFIX}/{one['id']}",
                      json={"expected_updated_at": one["updated_at"], "enabled": True})
    assert on.status_code == 200 and on.json()["enabled"] is True
    refused = client.patch(f"{PREFIX}/{two['id']}",
                           json={"expected_updated_at": two["updated_at"], "enabled": True})
    assert refused.status_code == 409
    assert "at most 1 property watches may be enabled at once" in refused.json()["detail"]
    assert client.get(f"{PREFIX}/{two['id']}").json()["enabled"] is False, "a 409 changes nothing"
    catalog = client.get(f"{PREFIX}/catalog").json()
    assert catalog["enabled_count"] == 1 and catalog["budget_used"] == 2

    # The tick's search budget: two watches at 2 searches each against a budget of 3.
    client = _client(
        db_schema, _principal("detection_engineer"),
        pw_max_searches_per_tick=3, pw_max_searches_per_watch_per_tick=3,
    )
    budget = client.patch(f"{PREFIX}/{two['id']}",
                          json={"expected_updated_at": two["updated_at"], "enabled": True})
    assert budget.status_code == 409
    assert "4 searches per tick against a budget of 3" in budget.json()["detail"]
    # Pinning a value on the enabled watch makes it cost 3 and the pair 5: refused too.
    client = _client(
        db_schema, _principal("detection_engineer"),
        pw_max_searches_per_tick=2, pw_max_searches_per_watch_per_tick=2,
    )
    token = client.get(f"{PREFIX}/{one['id']}").json()["updated_at"]
    pinned = client.put(f"{PREFIX}/{one['id']}/pins",
                        json={"expected_updated_at": token, "pinned_values": [["DENY"]]})
    assert pinned.status_code == 409 and "3 searches" in pinned.json()["detail"]
    assert client.get(f"{PREFIX}/{one['id']}").json()["pinned_values"] == []


async def test_a_group_larger_than_the_chunk_limit_cannot_be_enabled(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _client(
        db_schema, _principal("detection_engineer"),
        pw_max_sources_per_search=1, pw_max_chunks_per_watch=1,
    )
    group = _create(client, "big-group", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                    scope_group_id=GROUP_ID)
    # Group 10 holds fw-1 and, through its child group 11, fw-2: two sources, one chunk of 1.
    refused = client.patch(f"{PREFIX}/{group['id']}",
                           json={"expected_updated_at": group["updated_at"], "enabled": True})
    assert refused.status_code == 409
    assert "resolves to 2 sources, above the limit of 1" in refused.json()["detail"]
    assert "split the group" in refused.json()["detail"]
    roomy = _client(db_schema, _principal("detection_engineer"))
    ok = roomy.patch(f"{PREFIX}/{group['id']}",
                     json={"expected_updated_at": group["updated_at"], "enabled": True})
    assert ok.status_code == 200 and ok.json()["enabled"] is True
    assert ok.json()["scope"]["detail"] == "Edge"


# ------------------------------------------------------------------- the codes of a 409
async def test_the_enabled_cap_refusal_carries_its_code(
    db_session, db_schema: str
) -> None:
    """The 409 names its reason and codes it, so the browser never reads the wording (T-145)."""
    await _seed(db_session)
    client = _client(db_schema, _principal("detection_engineer"), pw_max_enabled_watches=1)
    one, two = _create(client, "coded-cap-1"), _create(client, "coded-cap-2")
    on = client.patch(f"{PREFIX}/{one['id']}",
                      json={"expected_updated_at": one["updated_at"], "enabled": True})
    assert on.status_code == 200, on.text
    refused = client.patch(f"{PREFIX}/{two['id']}",
                           json={"expected_updated_at": two["updated_at"], "enabled": True})
    assert refused.status_code == 409
    assert refused.json()["codes"] == ["ENABLED_CAP"]
    assert "at most 1 property watches may be enabled at once" in refused.json()["detail"]
    assert client.get(f"{PREFIX}/{two['id']}").json()["enabled"] is False, "a 409 changes nothing"


async def test_the_group_too_large_refusal_carries_its_code(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _client(
        db_schema, _principal("detection_engineer"),
        pw_max_sources_per_search=1, pw_max_chunks_per_watch=1,
    )
    group = _create(client, "coded-group", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                    scope_group_id=GROUP_ID)
    refused = client.patch(f"{PREFIX}/{group['id']}",
                           json={"expected_updated_at": group["updated_at"], "enabled": True})
    assert refused.status_code == 409
    assert refused.json()["codes"] == ["GROUP_TOO_LARGE"]
    assert "resolves to 2 sources, above the limit of 1" in refused.json()["detail"]


async def test_the_per_watch_search_limit_refusal_carries_its_code(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    # Two sources in one search of one: two chunks, four searches per tick, above three.
    client = _client(
        db_schema, _principal("detection_engineer"),
        pw_max_sources_per_search=1, pw_max_chunks_per_watch=2,
        pw_max_searches_per_watch_per_tick=3,
    )
    group = _create(client, "coded-limit", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                    scope_group_id=GROUP_ID)
    refused = client.patch(f"{PREFIX}/{group['id']}",
                           json={"expected_updated_at": group["updated_at"], "enabled": True})
    assert refused.status_code == 409
    assert refused.json()["codes"] == ["WATCH_SEARCH_LIMIT"]
    assert "4 searches per tick, above the per-watch limit of 3" in refused.json()["detail"]


async def test_the_tick_budget_refusal_carries_its_code(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _client(
        db_schema, _principal("detection_engineer"),
        pw_max_searches_per_tick=3, pw_max_searches_per_watch_per_tick=3,
    )
    # Neither watch reads history, so the budget is the only reason.
    one = _create(client, "coded-budget-1", history_days=0)
    two = _create(client, "coded-budget-2", history_days=0)
    on = client.patch(f"{PREFIX}/{one['id']}",
                      json={"expected_updated_at": one["updated_at"], "enabled": True})
    assert on.status_code == 200, on.text
    refused = client.patch(f"{PREFIX}/{two['id']}",
                           json={"expected_updated_at": two["updated_at"], "enabled": True})
    assert refused.status_code == 409
    assert refused.json()["codes"] == ["TICK_BUDGET"]
    assert "4 searches per tick against a budget of 3" in refused.json()["detail"]
    assert client.get(f"{PREFIX}/{two['id']}").json()["enabled"] is False, "a 409 changes nothing"


# --------------------------------------------------------------- the refusal changes nothing
async def test_a_capacity_refusal_leaves_the_stored_row_unchanged(
    db_session, db_schema: str
) -> None:
    """A refusal is an exception, so the session dependency rolls the route's write back.

    The route writes the new name, the enabled flag and the optimistic token, and flushes
    them, before it asks the caps; a refusal must leave the stored row exactly as it was and
    write no audit row. The rollback is the dependency's, on the exception: the route does
    not roll back itself (T-145, round 1). The other refusals that change nothing are
    `test_enable_409s_name_the_cap_the_budget_and_the_scope`,
    `test_the_enabled_cap_refusal_carries_its_code` and
    `test_the_tick_budget_refusal_carries_its_code`.
    """
    await _seed(db_session)
    client = _client(db_schema, _principal("detection_engineer"), pw_max_enabled_watches=1)
    one, two = _create(client, "rollback-cap-1"), _create(client, "rollback-cap-2")
    on = client.patch(f"{PREFIX}/{one['id']}",
                      json={"expected_updated_at": one["updated_at"], "enabled": True})
    assert on.status_code == 200, on.text
    token = two["updated_at"]
    refused = client.patch(
        f"{PREFIX}/{two['id']}",
        json={"expected_updated_at": token, "enabled": True, "name": "rollback-renamed"},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["codes"] == ["ENABLED_CAP"]
    row = await _row(db_session, two["id"])
    assert row.name == "rollback-cap-2", "the route's write must not survive the refusal"
    assert row.enabled is False
    assert _same_instant(row.updated_at.isoformat(), token), "the token must not move"
    assert await _audits(db_session, "property_watch.update") == []


# --------------------------------------------------------------------------- the audit
async def test_audit_actions_snapshot_the_count_and_digest_never_the_pins(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _client(db_schema, _principal("detection_engineer"))
    made = _create(client, "audited", pinned_values=[["PinnedBob"], ["PinnedCarol"]])
    path = f"{PREFIX}/{made['id']}"
    token = made["updated_at"]

    def patch(**body: object) -> dict:
        nonlocal token
        response = client.patch(path, json={"expected_updated_at": token, **body})
        assert response.status_code == 200, response.text
        token = response.json()["updated_at"]
        return response.json()

    patch(enabled=True)
    patch(enabled=False)
    patch(severity="LOW", enabled=True)
    patch(pinned_values=[["PinnedAmy"]])
    assert client.delete(path).status_code == 204

    rows = await _audits(db_session)
    assert [r.action for r in rows] == [
        "property_watch.create",
        "property_watch.enable",
        "property_watch.disable",
        "property_watch.update",
        "property_watch.update",
        "property_watch.delete",
    ]
    assert all(r.object_id == made["id"] and r.actor == "detection_engineer" for r in rows)
    created = rows[0].detail["after"]
    assert created["pinned_count"] == 2
    assert created["pinned_digest"] == pinned_digest([["PinnedBob"], ["PinnedCarol"]])
    assert created["pinned_digest"].startswith("sha256:")
    assert rows[0].detail["before"] is None
    changed = rows[4].detail
    assert changed["before"]["pinned_count"] == 2 and changed["after"]["pinned_count"] == 1
    assert changed["before"]["pinned_digest"] != changed["after"]["pinned_digest"]
    assert rows[5].detail["after"] is None
    assert created["scope"] == f"LOG_SOURCE_TYPE:{TYPE_ID}"
    dump = json.dumps([r.detail for r in rows])
    for text in SEEDED:
        assert text not in dump, f"{text} reached an audit row"


async def test_a_no_op_patch_writes_no_audit_row(db_session, db_schema: str) -> None:
    await _seed(db_session)
    client = _client(db_schema, _principal("detection_engineer"))
    made = _create(client, "quiet")
    same = client.patch(f"{PREFIX}/{made['id']}",
                        json={"expected_updated_at": made["updated_at"], "name": "quiet"})
    assert same.status_code == 200
    assert [r.action for r in await _audits(db_session)] == ["property_watch.create"]


# ---------------------------------------------------------------------------- the pins
async def test_pins_are_replaced_whole_and_checked_against_the_pattern(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _client(db_schema, _principal("detection_engineer"), pw_max_pinned_values=3)
    made = _create(client, "pins", properties=[USER, ACTION])
    path = f"{PREFIX}/{made['id']}/pins"
    token = made["updated_at"]
    good = client.put(path, json={"expected_updated_at": token,
                                  "pinned_values": [["PinnedBob", "DENY"], ["PinnedAmy", "ALLOW"]]})
    assert good.status_code == 200, good.text
    assert good.json()["added"] == 2 and good.json()["removed"] == 0
    assert good.json()["watch"]["watch_version"] == 1
    token = good.json()["watch"]["updated_at"]
    kept = [["PinnedBob", "DENY"], ["PinnedCarol", "DENY"]]
    swapped = client.put(path, json={"expected_updated_at": token, "pinned_values": kept})
    assert swapped.json()["added"] == 1 and swapped.json()["removed"] == 1
    token = swapped.json()["watch"]["updated_at"]
    same = client.put(path, json={"expected_updated_at": token, "pinned_values": kept})
    assert same.json()["added"] == 0 and same.json()["watch"]["updated_at"] == token

    bad = client.put(path, json={"expected_updated_at": token, "pinned_values": [
        ["O'Brien", "DENY"], ["a\\b", "x"], ["only-one"], [" lead", "x"], ["PinnedBob", "DENY"],
        ["PinnedBob", "DENY"],
    ]})
    assert bad.status_code == 422
    reasons = bad.json()["detail"]["reasons"]
    assert any("pins at most 3" in r for r in reasons)
    assert any("pinned value 1, part 1" in r for r in reasons)
    assert any("pinned value 2, part 1" in r for r in reasons)
    assert any("pinned value 3: needs 2 string" in r for r in reasons)
    assert any("pinned value 4, part 1" in r for r in reasons)
    assert any("pinned twice" in r for r in reasons)
    assert "Brien" not in bad.text
    stale = client.put(path, json={"expected_updated_at": made["updated_at"], "pinned_values": []})
    assert stale.status_code == 409
    assert _same_instant(stale.json()["detail"]["current_version"], token)


# ------------------------------------------------------------------------------ privacy
async def test_logs_and_audit_hold_no_pinned_or_seeded_value_text(
    db_session, db_schema: str, caplog: pytest.LogCaptureFixture
) -> None:
    await _seed(db_session)
    caplog.set_level(logging.DEBUG)
    provider = MockQRadarProvider(seed=5)
    provider.pw_script = lambda key, names: [
        {"values": dict.fromkeys(names, "SeededDave"), "events": 40},
        {"values": dict.fromkeys(names, "SeededErin"), "events": 10},
        {"values": dict.fromkeys(names, "PinnedBob"), "events": 5},
    ]
    client = _client(db_schema, _principal("detection_engineer"), provider=provider)
    made = _create(client, "private", pinned_values=[["PinnedBob"]])
    token = made["updated_at"]
    updated = client.patch(f"{PREFIX}/{made['id']}",
                           json={"expected_updated_at": token,
                                 "pinned_values": [["PinnedCarol"], ["PinnedAmy"]]})
    assert updated.status_code == 200
    previewed = client.post(f"{PREFIX}/{made['id']}/preview")
    assert previewed.status_code == 200
    # Positive control: the response does carry the value text (D-22), so the log and audit
    # checks below are not vacuous.
    assert {r["value_key"] for r in previewed.json()["rows"]} >= {"SeededDave", "SeededErin"}

    dump = json.dumps([r.detail for r in await _audits(db_session)])
    logged = "\n".join(
        record.getMessage() + " " + json.dumps(getattr(record, "__dict__", {}), default=str)
        for record in caplog.records
    )
    for text in SEEDED:
        assert text not in dump, f"{text} reached an audit row"
        assert text not in logged, f"{text} reached a log record"


# --------------------------------------------------------------------------- the reads
async def test_catalog_scope_options_and_property_options(db_session, db_schema: str) -> None:
    world = await _seed(db_session)
    client = _client(db_schema, _principal("viewer"), provider=MockQRadarProvider())
    catalog = client.get(f"{PREFIX}/catalog").json()
    assert catalog["collection_enabled"] is False
    assert catalog["alerting_mode"] == "shadow"
    assert catalog["normalized_fields"] == ["username"]
    assert catalog["max_pinned_values"] == 10 and catalog["max_properties"] == 2
    assert catalog["preview_searches"] == 2 and catalog["hours_max"] == 168
    assert catalog["enabled_count"] == 0 and catalog["budget_used"] == 0
    assert catalog["search_budget"] == 40

    sources = client.get(
        f"{PREFIX}/scope-options", params={"kind": "LOG_SOURCE", "q": "FW-"}
    ).json()
    assert [s["name"] for s in sources] == ["fw-1", "fw-2"]
    assert sources[0]["id"] == str(world.source.id) and sources[0]["qradar_id"] == 701
    types = client.get(f"{PREFIX}/scope-options", params={"kind": "LOG_SOURCE_TYPE"}).json()
    assert types == [{
        "kind": "LOG_SOURCE_TYPE", "id": str(TYPE_ID), "qradar_id": TYPE_ID,
        "name": "Web Application Firewall", "detail": None, "synced_source_count": 2,
        "scope_state": None, "scope_reason": None,
        "scope_reason_code": None, "scope_reason_params": None,
    }]
    groups = client.get(f"{PREFIX}/scope-options", params={"kind": "LOG_SOURCE_GROUP"}).json()
    assert [(g["name"], g["detail"]) for g in groups] == [
        ("Edge", "Edge"), ("Edge DMZ", "Edge / Edge DMZ"),
    ]
    # PW-5b: the resolved member count of a group, through the shared resolver. Edge holds
    # fw-1 and, through its child group Edge DMZ, fw-2; Edge DMZ holds fw-2.
    assert {g["name"]: g["synced_source_count"] for g in groups} == {"Edge": 2, "Edge DMZ": 1}
    assert all(g["scope_state"] is None for g in groups)
    assert len(client.get(f"{PREFIX}/scope-options",
                          params={"kind": "LOG_SOURCE", "limit": 1}).json()) == 1
    assert client.get(f"{PREFIX}/scope-options").status_code == 422
    assert client.get(f"{PREFIX}/scope-options", params={"kind": "NOPE"}).status_code == 422

    by_type = client.get(f"{PREFIX}/property-options",
                         params={"scope_kind": "LOG_SOURCE_TYPE", "scope_id": TYPE_ID}).json()
    assert by_type["status"] == "AVAILABLE" and by_type["scope_type_ids"] == [TYPE_ID]
    names = [p["name"] for p in by_type["properties"]]
    assert names.index("WAF_Action") < names.index("Unused Property"), "defining types first"
    action = next(p for p in by_type["properties"] if p["name"] == "WAF_Action")
    assert action["defined_on_type_ids"] == [TYPE_ID] and action["covers_scope"] is True
    unused = next(p for p in by_type["properties"] if p["name"] == "Unused Property")
    assert unused["defined_on_type_ids"] == [] and unused["covers_scope"] is False
    bad = next(p for p in by_type["properties"] if p["name"] == "Bad.Name(1)")
    assert bad["name_allowed"] is False
    assert by_type["normalized_fields"] == ["username"]
    by_source = client.get(f"{PREFIX}/property-options",
                           params={"scope_kind": "LOG_SOURCE", "scope_id": str(world.source.id),
                                   "q": "waf_"}).json()
    assert {p["name"] for p in by_source["properties"]} == {
        "WAF_Action", "WAF_ResponseCode", "WAF_USERAGENT", "WAF_URL",
    }
    by_group = client.get(f"{PREFIX}/property-options",
                          params={"scope_kind": "LOG_SOURCE_GROUP", "scope_id": GROUP_ID}).json()
    assert by_group["scope_type_ids"] == [TYPE_ID]
    assert client.get(f"{PREFIX}/property-options",
                      params={"scope_kind": "LOG_SOURCE", "scope_id": "x"}).status_code == 422
    assert client.get(f"{PREFIX}/property-options",
                      params={"scope_kind": "LOG_SOURCE_TYPE", "scope_id": "x"}).status_code == 422
    assert client.get(f"{PREFIX}/property-options",
                      params={"scope_kind": "LOG_SOURCE", "scope_id": NIL}).status_code == 404
    ghost = client.get(f"{PREFIX}/property-options",
                       params={"scope_kind": "LOG_SOURCE_GROUP", "scope_id": 99999})
    assert ghost.status_code == 404
    # The editor's own call names no instance: `_default_instance` adds one statement, and the
    # read is still flat in the number of groups and sources (AGENTS.md rule 9).
    for kind, expected in (("LOG_SOURCE_GROUP", OPTIONS_STATEMENTS["group"]),
                           ("LOG_SOURCE_TYPE", OPTIONS_STATEMENTS["type"])):
        client.statements.clear()  # type: ignore[attr-defined]
        assert client.get(f"{PREFIX}/scope-options", params={"kind": kind}).status_code == 200
        assert len(client.statements) == expected + 1, kind  # type: ignore[attr-defined]


async def test_property_options_degrade_with_a_fixed_sentence(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _client(db_schema, _principal("viewer"))

    def broken(_instance):  # type: ignore[no-untyped-def]
        raise RuntimeError("https://qradar.internal/api SEC=abcdef0123456789abcdef01")

    client.app.dependency_overrides[get_provider_factory] = lambda: broken  # type: ignore[attr-defined]
    out = client.get(f"{PREFIX}/property-options",
                     params={"scope_kind": "LOG_SOURCE_TYPE", "scope_id": TYPE_ID})
    assert out.status_code == 200
    body = out.json()
    assert body["status"] == "UNAVAILABLE" and body["properties"] == []
    assert body["normalized_fields"] == ["username"]
    assert "qradar.internal" not in out.text and "abcdef" not in out.text


async def test_list_and_detail_with_status_coverage_and_incidents(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _client(db_schema, _principal("viewer"))
    writer = _client(db_schema, _principal("detection_engineer"))
    made = _create(writer, "reads", detectors=["PW_VALUE_SPIKE", "PW_VALUE_DROP"])
    watch_id = uuid.UUID(made["id"])
    db_session.add(_bucket(made["id"], 1, total_events=200, parsed_events=150))
    db_session.add(_bucket(made["id"], 2, total_events=100, parsed_events=100))
    db_session.add(_bucket(made["id"], 3, watch_version=0 + 7))  # another version: ignored
    value = PwValue(watch_id=watch_id, watch_version=1, value_key="SeededDave",
                    component_0="SeededDave", tracked_since=_bucket_start(5))
    db_session.add(value)
    await db_session.flush()
    db_session.add_all([
        PwAnomaly(watch_id=watch_id, pw_value_id=value.id, value_key="SeededDave",
                  detector_type="PW_VALUE_SPIKE", fingerprint="f1", severity="HIGH",
                  state="OPEN", detected_at=datetime.now(UTC), watch_version=1),
        PwAnomaly(watch_id=watch_id, pw_value_id=value.id, value_key="SeededDave",
                  detector_type="PW_VALUE_SPIKE", fingerprint="f2", severity="HIGH",
                  state="RESOLVED", detected_at=datetime.now(UTC), watch_version=1),
    ])
    await db_session.commit()

    rows = client.get(PREFIX).json()
    assert [r["name"] for r in rows] == ["reads"]
    assert rows[0]["coverage"]["total_events"] == 200
    assert rows[0]["coverage"]["ratio"] == 0.75
    assert rows[0]["coverage"]["measurement"] == "EXACT"
    assert rows[0]["open_incidents"] == 1
    assert rows[0]["detector_states"] is None
    assert rows[0]["alerting_mode"] == "shadow"
    one = client.get(f"{PREFIX}/{made['id']}").json()
    assert one["detector_states"] == [
        {"detector_type": "PW_VALUE_SPIKE", "active_incidents": 1},
        {"detector_type": "PW_VALUE_DROP", "active_incidents": 0},
    ]
    assert one["scope"]["name"] == "Web Application Firewall"


async def test_the_list_costs_the_same_statements_for_one_watch_or_six(
    db_session, db_schema: str
) -> None:
    world = await _seed(db_session)
    writer = _client(db_schema, _principal("detection_engineer"))
    reader = _client(db_schema, _principal("viewer"))
    _create(writer, "s-0")
    reader.statements.clear()  # type: ignore[attr-defined]
    assert len(reader.get(PREFIX).json()) == 1
    one = len(reader.statements)  # type: ignore[attr-defined]
    _create(writer, "s-1", scope_kind="LOG_SOURCE", scope_type_id=None,
            log_source_id=str(world.source.id))
    for index in range(2, 4):
        _create(writer, f"s-{index}", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                scope_group_id=GROUP_ID, pinned_values=[["a"]])
    for index in range(4, 6):
        _create(writer, f"s-{index}")
    reader.statements.clear()  # type: ignore[attr-defined]
    assert len(reader.get(PREFIX).json()) == 6
    # Never a statement per watch: six watches of three scope kinds add the name reads of
    # their scopes (a source, a type, the groups), a fixed three, and nothing more.
    assert len(reader.statements) <= one + 3  # type: ignore[attr-defined]


async def test_buckets_values_and_series_keep_gaps_and_zeros_honest(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    writer = _client(db_schema, _principal("detection_engineer"))
    reader = _client(db_schema, _principal("viewer"))
    made = _create(writer, "history", pinned_values=[["PinnedBob"]])
    watch_id = uuid.UUID(made["id"])
    # Collected at 6, 5 (a lower bound) and 4 buckets ago; nothing at 3; then 2 and 1.
    for back, kw in ((6, {}), (5, {"values_measurement": "LOWER_BOUND", "untracked_events": None}),
                     (4, {}), (2, {}), (1, {})):
        db_session.add(_bucket(made["id"], back, **kw))
    counted = PwValue(watch_id=watch_id, watch_version=1, value_key="SeededDave",
                      tracked_since=_bucket_start(7), first_seen_at=_bucket_start(7))
    silent = PwValue(watch_id=watch_id, watch_version=1, value_key="SeededErin",
                     tracked_since=_bucket_start(7))
    late = PwValue(watch_id=watch_id, watch_version=1, value_key="Late",
                   tracked_since=_bucket_start(2))
    pinned = PwValue(watch_id=watch_id, watch_version=1, value_key="PinnedBob", pinned=True,
                     tracked_since=_bucket_start(7))
    retired = PwValue(watch_id=watch_id, watch_version=1, value_key="Gone", active=False,
                      tracked_since=_bucket_start(900), tracked_until=_bucket_start(800))
    other_version = PwValue(watch_id=watch_id, watch_version=9, value_key="Old")
    db_session.add_all([counted, silent, late, pinned, retired, other_version])
    await db_session.flush()
    for back, events in ((6, 30), (4, 50), (1, 70)):
        db_session.add(PwValueMetric(pw_value_id=counted.id, bucket_start=_bucket_start(back),
                                     events=events, bucket_seconds=300))
    latest = _bucket_start(1)
    db_session.add(PwValueBaseline(
        pw_value_id=counted.id, metric_name=PW_VALUE_EVENTS_METRIC, weekday=latest.isoweekday(),
        hour=latest.hour, median=60.0, p05=40.0, p95=90.0, sample_count=12, is_reliable=True,
        watch_version=1))
    db_session.add(PwValueBaseline(
        pw_value_id=counted.id, metric_name=PW_VALUE_EVENTS_METRIC,
        weekday=(latest.isoweekday() % 7) + 1, hour=latest.hour, median=1.0, sample_count=2,
        is_reliable=False, watch_version=1))
    db_session.add(PwValueBaseline(
        pw_value_id=silent.id, metric_name=PW_VALUE_EVENTS_METRIC, weekday=latest.isoweekday(),
        hour=latest.hour, median=1.0, sample_count=2, is_reliable=False, watch_version=1))
    await db_session.commit()

    buckets = reader.get(f"{PREFIX}/{made['id']}/buckets", params={"hours": 2}).json()
    assert len(buckets) == 5, "a gap is an absent bucket, never a zero row"
    assert [b["values_measurement"] for b in buckets] == [
        "EXACT", "LOWER_BOUND", "EXACT", "EXACT", "EXACT"]
    assert buckets[1]["untracked_events"] is None
    starts = [b["bucket_start"] for b in buckets]
    assert starts == sorted(starts)
    assert reader.get(f"{PREFIX}/{made['id']}/buckets", params={"hours": 0}).status_code == 422
    assert reader.get(f"{PREFIX}/{made['id']}/buckets", params={"hours": 169}).status_code == 422
    assert reader.get(f"{PREFIX}/{made['id']}/buckets", params={"hours": 168}).status_code == 200

    page = reader.get(f"{PREFIX}/{made['id']}/values", params={"hours": 24}).json()
    assert page["total"] == 4, "retired long ago and other-version values are not listed"
    keys = [v["value_key"] for v in page["items"]]
    assert keys[0] == "PinnedBob", "pinned values come first"
    assert keys[1] == "SeededDave", "then by events at the newest bucket"
    by_key = {v["value_key"]: v for v in page["items"]}
    dave = by_key["SeededDave"]
    assert dave["reading"] == "COUNT" and dave["events"] == 70 and dave["measurement"] == "EXACT"
    assert dave["baseline_state"] == "RELIABLE"
    assert (dave["expected_low"], dave["expected_median"], dave["expected_high"]) == (40, 60, 90)
    assert dave["reliable_cells"] == 1 and dave["cells_total"] == 168
    erin = by_key["SeededErin"]
    assert erin["reading"] == "MEASURED_ZERO" and erin["events"] == 0
    assert erin["baseline_state"] == "LEARNING"
    assert erin["expected_low"] is None, "a cell below the sample floor draws no band"
    assert by_key["Late"]["reading"] == "MEASURED_ZERO"
    assert by_key["PinnedBob"]["baseline_state"] == "NONE"
    assert [v["value_key"] for v in reader.get(
        f"{PREFIX}/{made['id']}/values", params={"sort": "value", "limit": 2}).json()["items"]
    ] == ["PinnedBob", "Late"]
    assert reader.get(f"{PREFIX}/{made['id']}/values", params={"sort": "bogus"}).status_code == 422

    series = reader.get(
        f"{PREFIX}/{made['id']}/series",
        params=[("value_ids", str(counted.id)), ("value_ids", str(late.id)), ("hours", 2)],
    ).json()
    by_id = {s["value_id"]: s for s in series["series"]}
    dave_points = by_id[str(counted.id)]["points"]
    assert len(dave_points) == 5, "one point per collected bucket"
    assert [p["reading"] for p in dave_points] == [
        "COUNT", "UNKNOWN", "COUNT", "MEASURED_ZERO", "COUNT"]
    assert dave_points[1]["events"] is None, "a lower-bound bucket cannot say the value was absent"
    assert dave_points[3]["events"] == 0
    assert dave_points[4]["expected_median"] == 60
    late_points = by_id[str(late.id)]["points"]
    assert late_points[0]["reading"] == "NOT_AN_OBSERVATION", "before it was tracked"
    assert late_points[3]["reading"] == "MEASURED_ZERO"

    too_many = [("value_ids", str(uuid.uuid4())) for _ in range(13)]
    refused = reader.get(f"{PREFIX}/{made['id']}/series", params=too_many)
    assert refused.status_code == 422 and "at most 12" in refused.text
    stranger = reader.get(f"{PREFIX}/{made['id']}/series",
                          params=[("value_ids", str(other_version.id))])
    assert stranger.status_code == 422 and "not a tracked value" in stranger.text
    assert reader.get(f"{PREFIX}/{made['id']}/series").status_code == 422


# ------------------------------------------------------------------------------ delete
async def test_delete_cascades_every_value(db_session, db_schema: str) -> None:
    await _seed(db_session)
    writer = _client(db_schema, _principal("detection_engineer"))
    made = _create(writer, "doomed")
    keep = _create(writer, "survivor")
    for owner in (made, keep):
        watch_id = uuid.UUID(owner["id"])
        db_session.add(_bucket(owner["id"], 1))
        value = PwValue(watch_id=watch_id, watch_version=1, value_key=f"v-{owner['name']}",
                        tracked_since=_bucket_start(3))
        db_session.add(value)
        await db_session.flush()
        db_session.add(PwValueMetric(pw_value_id=value.id, bucket_start=_bucket_start(1),
                                     events=3, bucket_seconds=300))
        db_session.add(PwValueBaseline(pw_value_id=value.id, metric_name=PW_VALUE_EVENTS_METRIC,
                                       weekday=1, hour=1, median=1.0, watch_version=1))
        db_session.add(PwAnomaly(watch_id=watch_id, pw_value_id=value.id, value_key="x",
                                 detector_type="PW_VALUE_SPIKE", fingerprint=f"f-{owner['name']}",
                                 severity="LOW", state="OPEN", detected_at=datetime.now(UTC),
                                 watch_version=1))
    await db_session.commit()
    tables = (PwBucket, PwValue, PwValueMetric, PwValueBaseline, PwAnomaly)
    assert [await _count(db_session, t) for t in tables] == [2, 2, 2, 2, 2]
    assert writer.delete(f"{PREFIX}/{made['id']}").status_code == 204
    assert [await _count(db_session, t) for t in tables] == [1, 1, 1, 1, 1]
    assert await _count(db_session, PropertyWatch) == 1
    assert writer.get(f"{PREFIX}/{made['id']}").status_code == 404
    assert writer.delete(f"{PREFIX}/{made['id']}").status_code == 404


# ------------------------------------------------------------------------------ preview
def _scripted(provider: MockQRadarProvider) -> MockQRadarProvider:
    provider.pw_script = lambda key, names: [
        {"values": dict.fromkeys(names, "SeededDave"), "events": 40},
        {"values": dict.fromkeys(names, "SeededErin"), "events": 10},
        {"values": dict.fromkeys(names), "events": 5},
    ]
    return provider


async def test_the_preview_sends_exactly_two_searches_and_persists_nothing(
    db_session, db_schema: str
) -> None:
    world = await _seed(db_session)
    provider = _scripted(MockQRadarProvider(seed=1))
    priorities: list[str] = []

    def record_priority(_search):  # type: ignore[no-untyped-def]
        priorities.append(_ARIEL_PRIORITY.get())
        return None

    provider.pw_fault = record_priority
    client = _client(db_schema, _principal("detection_engineer"), provider=provider)
    made = _create(client, "previewed", pinned_values=[["SeededDave"]])
    before = [await _count(db_session, m) for m in (PwBucket, PwValue, PwValueMetric, PwAnomaly)]
    out = client.post(f"{PREFIX}/{made['id']}/preview")
    assert out.status_code == 200, out.text
    body = out.json()
    assert [(s.search, s.scope_ids) for s in provider.pw_searches] == [
        (PwSearchKind.TOTALS, (TYPE_ID,)), (PwSearchKind.GROUPED, (TYPE_ID,))
    ], "totals and grouped: two searches, never a pinned one, never a split"
    assert priorities == ["background", "background"]
    assert _ARIEL_PRIORITY.get() == "live"
    assert body["mode"] == "PREVIEW" and body["searches_sent"] == 2
    assert body["outcome"] == "MEASURED" and body["reason"] is None
    assert body["bucket_seconds"] == 300 and body["buckets_measured"] == 12
    assert body["total_events"] == 660 and body["parsed_events"] == 600
    assert body["unparsed_events"] == 60 and body["unparsed_bound"] == "EXACT"
    assert body["coverage"] == pytest.approx(600 / 660)
    assert body["property_parses"] is True and body["rows_measurement"] == "EXACT"
    assert [(r["value_key"], r["events"]) for r in body["rows"]] == [
        ("SeededDave", 480), ("SeededErin", 120)]
    assert body["rows"][0]["share"] == pytest.approx(0.8)
    assert {q["search"] for q in body["queries"]} == {"GROUPED", "TOTALS"}
    grouped = next(q for q in body["queries"] if q["search"] == "GROUPED")["text"]
    assert grouped.startswith("SELECT") and '"WAF_Action"' in grouped
    start = datetime.fromisoformat(body["window_start"])
    end = datetime.fromisoformat(body["window_end"])
    assert end - start == timedelta(hours=1) and end <= datetime.now(UTC)
    after = [await _count(db_session, m) for m in (PwBucket, PwValue, PwValueMetric, PwAnomaly)]
    assert before == after == [0, 0, 0, 0], "a preview persists and admits nothing"

    audit = (await _audits(db_session, "property_watch.preview"))[0]
    assert audit.outcome == "SUCCESS" and audit.object_id == made["id"]
    assert set(audit.detail) == {
        "watch_version", "searches", "outcome", "buckets_measured", "buckets_lower_bound",
        "buckets_unmeasured", "rows", "total_events", "parsed_events", "property_parses",
    }
    assert audit.detail["searches"] == 2 and audit.detail["rows"] == 2
    assert "SeededDave" not in json.dumps(audit.detail)
    assert world.instance is not None


async def test_the_preview_resolves_a_source_and_a_group_scope(
    db_session, db_schema: str
) -> None:
    world = await _seed(db_session)
    provider = _scripted(MockQRadarProvider(seed=1))
    client = _client(db_schema, _principal("detection_engineer"), provider=provider)
    by_source = _create(client, "p-source", scope_kind="LOG_SOURCE", scope_type_id=None,
                        log_source_id=str(world.other.id), properties=[USER, ACTION])
    assert client.post(f"{PREFIX}/{by_source['id']}/preview").status_code == 200
    assert [s.scope_ids for s in provider.pw_searches] == [(702,), (702,)]
    assert {s.scope_kind.value for s in provider.pw_searches} == {"LOG_SOURCE"}
    provider.pw_searches.clear()
    group = _create(client, "p-group", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                    scope_group_id=GROUP_ID)
    out = client.post(f"{PREFIX}/{group['id']}/preview")
    assert out.status_code == 200
    assert [s.scope_ids for s in provider.pw_searches] == [(701, 702), (701, 702)]
    # The audit holds counts only, one row per preview that spent searches.
    assert len(await _audits(db_session, "property_watch.preview")) == 2


async def test_a_preview_that_cannot_run_is_409_and_spends_nothing(
    db_session, db_schema: str
) -> None:
    world = await _seed(db_session)
    provider = _scripted(MockQRadarProvider(seed=1))
    client = _client(db_schema, _principal("detection_engineer"), provider=provider,
                     pw_max_sources_per_search=1)
    group = _create(client, "wide", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                    scope_group_id=GROUP_ID)
    wide = client.post(f"{PREFIX}/{group['id']}/preview")
    assert wide.status_code == 409 and "covers one search chunk of at most 1" in wide.text
    empty = _create(client, "empty", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                    scope_group_id=GROUP_ID + 1)
    # Group 11 holds fw-2 only; empty it by pointing the source elsewhere.
    world.other.group_ids = []
    await db_session.commit()
    nobody_home = client.post(f"{PREFIX}/{empty['id']}/preview")
    assert nobody_home.status_code == 409 and "SCOPE_EMPTY" in nobody_home.text
    stale = await db_session.get(LogSourceGroup, (world.instance.id, GROUP_ID))
    stale.synced_at = datetime.now(UTC) - timedelta(hours=48)
    await db_session.commit()
    old = client.post(f"{PREFIX}/{group['id']}/preview")
    assert old.status_code == 409 and "SCOPE_STALE" in old.text
    assert provider.pw_searches == []
    assert await _audits(db_session, "property_watch.preview") == []


async def test_a_cut_search_is_not_split_and_never_a_zero(db_session, db_schema: str) -> None:
    await _seed(db_session)
    provider = _scripted(MockQRadarProvider(seed=1))
    provider.pw_force_truncated = lambda s: s.search is PwSearchKind.GROUPED
    client = _client(db_schema, _principal("detection_engineer"), provider=provider)
    made = _create(client, "cut")
    out = client.post(f"{PREFIX}/{made['id']}/preview").json()
    assert len(provider.pw_searches) == 2, "the budget refuses the split: two searches, no more"
    assert out["outcome"] == "NOT_MEASURED"
    assert "does not split it" in out["reason"]
    assert out["rows"] == [] and out["parsed_events"] is None and out["unparsed_events"] is None
    assert out["buckets_measured"] == 0 and out["buckets_unmeasured"] == 12
    assert out["property_parses"] is None, "nothing parsed was measured: unknown, not False"
    assert out["rows_measurement"] is None
    assert out["total_events"] == 660, "the totals search was whole"
    audit = (await _audits(db_session, "property_watch.preview"))[0]
    assert audit.outcome == "FAILURE" and audit.detail["outcome"] == "NOT_MEASURED"


async def test_a_failed_search_is_unmeasured_and_leaks_no_upstream_text(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    provider = _scripted(MockQRadarProvider(seed=1))
    provider.pw_fault = lambda s: ProviderUnavailableError(
        "ECONNREFUSED https://qradar.internal:443 SEC=0123456789abcdef0123456789abcdef"
    )
    client = _client(db_schema, _principal("detection_engineer"), provider=provider)
    made = _create(client, "failing")
    out = client.post(f"{PREFIX}/{made['id']}/preview")
    assert out.status_code == 200
    body = out.json()
    assert body["outcome"] == "NOT_MEASURED" and "search failed" in body["reason"]
    assert body["total_events"] is None and body["rows"] == []
    assert body["property_parses"] is None
    assert "qradar.internal" not in out.text and "SEC=" not in out.text
    assert len(provider.pw_searches) == 2


async def test_a_property_that_never_parses_says_so(db_session, db_schema: str) -> None:
    await _seed(db_session)
    provider = MockQRadarProvider(seed=1)
    provider.pw_script = lambda key, names: [
        {"values": dict.fromkeys(names), "events": 25},
    ]
    client = _client(db_schema, _principal("detection_engineer"), provider=provider)
    made = _create(client, "unparsed")
    body = client.post(f"{PREFIX}/{made['id']}/preview").json()
    assert body["property_parses"] is False
    assert body["total_events"] == 300 and body["parsed_events"] == 0
    assert body["unparsed_events"] == 300 and body["coverage"] == 0
    assert body["rows"] == [] and body["outcome"] == "MEASURED"


async def test_the_preview_is_rate_limited(
    db_session, db_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(get_settings(), "rate_limit_search_execution", "2/minute")
    provider = _scripted(MockQRadarProvider(seed=1))
    client = _client(db_schema, _principal("detection_engineer"), provider=provider)
    made = _create(client, "limited")
    url = f"{PREFIX}/{made['id']}/preview"
    assert client.post(url).status_code == 200
    assert client.post(url).status_code == 200
    third = client.post(url)
    assert third.status_code == 429, third.text
    assert len(provider.pw_searches) == 4, "the refused call sent nothing"


# ----------------------------------------------------------------------------- anomalies
async def test_anomalies_list_a_pw_row_as_pw_value_with_its_text(
    db_session, db_schema: str
) -> None:
    world = await _seed(db_session)
    writer = _client(db_schema, _principal("detection_engineer"))
    by_source = _create(writer, "pw-src", scope_kind="LOG_SOURCE", scope_type_id=None,
                        log_source_id=str(world.source.id), properties=[USER, ACTION])
    by_type = _create(writer, "pw-type")
    now = datetime.now(UTC)
    combo = f"SeededDave{TUPLE_SEPARATOR}DENY"
    db_session.add_all([
        PwAnomaly(watch_id=uuid.UUID(by_source["id"]), value_key=combo,
                  detector_type="PW_NEW_COMBINATION", fingerprint="a", severity="HIGH",
                  state="OPEN", detected_at=now, watch_version=1, observed_value=42.0,
                  explanation="a combination never seen before"),
        PwAnomaly(watch_id=uuid.UUID(by_type["id"]), value_key="SeededErin",
                  detector_type="PW_VALUE_SPIKE", fingerprint="b", severity="MEDIUM",
                  state="RESOLVED", detected_at=now - timedelta(minutes=5), watch_version=1),
        LogSourceAnomaly(log_source_id=world.source.id, anomaly_type=AnomalyType.VOLUME_SPIKE,
                         severity=Severity.LOW, state=AnomalyState.OPEN,
                         detected_at=now - timedelta(minutes=10),
                         evidence_status=EvidenceStatus.NOT_REQUESTED),
    ])
    await db_session.commit()
    viewer = _client(db_schema, _principal("viewer"))
    everything = viewer.get("/api/v1/anomalies").json()
    assert everything["total"] == 3, "the Phase A family still unions with the new one"
    by_kind = {i["subject_kind"]: i for i in everything["items"] if i["subject_kind"] != "PW_VALUE"}
    assert set(by_kind) == {"LOG_SOURCE"}
    pw = viewer.get("/api/v1/anomalies", params={"subject_kind": "PW_VALUE"}).json()
    assert pw["total"] == 2
    first, second = pw["items"]
    assert first["subject_kind"] == "PW_VALUE" and first["anomaly_type"] == "PW_NEW_COMBINATION"
    assert first["pw_value"]["value_key"] == combo
    assert first["pw_value"]["components"] == ["SeededDave", "DENY"]
    assert first["pw_value"]["watch_name"] == "pw-src"
    assert first["pw_value"]["properties"] == [USER, ACTION]
    assert first["pw_value"]["scope_kind"] == "LOG_SOURCE"
    assert first["log_source_id"] == str(world.source.id) and first["log_source_name"] == "fw-1"
    assert first["observed_value"] == 42.0 and first["observed_measurement"] == "EXACT"
    assert first["waf_endpoint"] is None and first["ips_entity"] is None
    assert second["pw_value"]["components"] == ["SeededErin"]
    assert second["log_source_id"] is None, "a type-scoped watch is about no one source"
    assert second["state"] == "RESOLVED"
    typed = viewer.get("/api/v1/anomalies", params={"anomaly_type": "PW_VALUE_SPIKE"}).json()
    assert [i["pw_value"]["value_key"] for i in typed["items"]] == ["SeededErin"]
    scoped = viewer.get("/api/v1/anomalies", params={"property_watch_id": by_source["id"]}).json()
    assert scoped["total"] == 1 and scoped["items"][0]["anomaly_type"] == "PW_NEW_COMBINATION"
    active = viewer.get("/api/v1/anomalies", params={"active_only": "true"}).json()
    assert active["total"] == 2
    other = viewer.get("/api/v1/anomalies", params={"subject_kind": "LOG_SOURCE"}).json()
    assert other["total"] == 1 and other["items"][0]["pw_value"] is None
    by_source_id = viewer.get("/api/v1/anomalies",
                              params={"log_source_id": str(world.source.id)}).json()
    assert by_source_id["total"] == 2, "the Phase A row and the source-scoped watch's row"
    waf_only = viewer.get("/api/v1/anomalies", params={"subject_kind": "WAF_ENDPOINT"}).json()
    assert waf_only["total"] == 0
    assert viewer.get("/api/v1/anomalies", params={"anomaly_type": "PW_NOPE"}).status_code == 422
    # Any caller who can read /anomalies reads the text (D-22); one who cannot, does not.
    assert _client(db_schema, NOBODY).get("/api/v1/anomalies").status_code == 403


# --------------------------------------------------------------------------- the scope read
async def test_the_scope_read_answers_a_source_a_type_and_a_group(
    db_session, db_schema: str
) -> None:
    world = await _seed(db_session)
    writer = _client(db_schema, _principal("detection_engineer"))
    reader = _client(db_schema, _principal("viewer"))

    by_source = _create(writer, "scope-src", scope_kind="LOG_SOURCE", scope_type_id=None,
                        log_source_id=str(world.source.id))
    source = reader.get(f"{PREFIX}/{by_source['id']}/scope")
    assert source.status_code == 200, source.text
    body = source.json()
    assert body["watch_id"] == by_source["id"] and body["watch_version"] == 1
    assert body["kind"] == "LOG_SOURCE" and body["resolved"] is True and body["state"] is None
    assert body["reason"] is None
    assert body["member_count"] == 1 and body["members"] == ["fw-1"]
    assert body["members_total"] == 1
    assert body["groups_walked"] == [] and body["changes"] == []
    # A source scope has no group inventory to age.
    assert body["inventory_synced_at"] is None and body["inventory_age_seconds"] is None

    by_type = _create(writer, "scope-type")
    typed = reader.get(f"{PREFIX}/{by_type['id']}/scope").json()
    assert typed["kind"] == "LOG_SOURCE_TYPE" and typed["resolved"] is True
    assert typed["member_count"] == 2 and typed["members"] == ["fw-1", "fw-2"]
    assert typed["members_total"] == 2 and typed["groups_walked"] == []

    # Edge (10) holds fw-1 and, through its child Edge DMZ (11), fw-2.
    by_group = _create(writer, "scope-group", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                       scope_group_id=GROUP_ID)
    grouped = reader.get(f"{PREFIX}/{by_group['id']}/scope").json()
    assert grouped["kind"] == "LOG_SOURCE_GROUP" and grouped["resolved"] is True
    assert grouped["member_count"] == 2 and grouped["members"] == ["fw-1", "fw-2"]
    assert grouped["members_total"] == 2
    assert [(g["qradar_id"], g["name"]) for g in grouped["groups_walked"]] == [
        (GROUP_ID, "Edge"), (GROUP_ID + 1, "Edge DMZ")]
    assert grouped["inventory_synced_at"] is not None
    assert grouped["inventory_age_seconds"] is not None and grouped["inventory_age_seconds"] >= 0
    assert grouped["changes"] == []


async def test_a_two_group_parent_cycle_terminates_and_lists_each_group_once(
    db_session, db_schema: str
) -> None:
    world = await _seed(db_session)
    now = datetime.now(UTC)
    # The lab's own inventory shape: group 20 names 21 as parent and 21 names 20.
    db_session.add_all([
        LogSourceGroup(instance_id=world.instance.id, qradar_id=20, name="Cyc-A",
                       parent_qradar_id=21, assignable=True, synced_at=now),
        LogSourceGroup(instance_id=world.instance.id, qradar_id=21, name="Cyc-B",
                       parent_qradar_id=20, assignable=True, synced_at=now),
        LogSource(instance_id=world.instance.id, qradar_id=801, name="cyc-1", type_id=TYPE_ID,
                  type_name="WAF", group_ids=[20]),
        LogSource(instance_id=world.instance.id, qradar_id=802, name="cyc-2", type_id=TYPE_ID,
                  type_name="WAF", group_ids=[21]),
    ])
    await db_session.commit()
    writer = _client(db_schema, _principal("detection_engineer"))
    reader = _client(db_schema, _principal("viewer"))
    made = _create(writer, "cyclic", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                   scope_group_id=20)
    body = reader.get(f"{PREFIX}/{made['id']}/scope").json()
    assert body["resolved"] is True and body["member_count"] == 2
    assert [g["qradar_id"] for g in body["groups_walked"]] == [20, 21], "each group once"
    assert body["members"] == ["cyc-1", "cyc-2"]


async def test_a_stale_group_inventory_is_marked_and_carries_no_zero(
    db_session, db_schema: str
) -> None:
    world = await _seed(db_session)
    writer = _client(db_schema, _principal("detection_engineer"))
    reader = _client(db_schema, _principal("viewer"))
    made = _create(writer, "stale", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                   scope_group_id=GROUP_ID)
    row = await db_session.get(LogSourceGroup, (world.instance.id, GROUP_ID))
    row.synced_at = datetime.now(UTC) - timedelta(hours=48)
    await db_session.commit()

    body = reader.get(f"{PREFIX}/{made['id']}/scope").json()
    assert body["resolved"] is False and body["state"] == "SCOPE_STALE"
    assert body["reason"] and "older than" in body["reason"]
    # The last sync's members are shown, marked stale: never a zero drawn from nothing.
    assert body["member_count"] == 2
    assert body["members"] == ["fw-1", "fw-2"] and body["members_total"] == 2
    assert body["inventory_age_seconds"] >= 47 * 3600
    assert body["changes"] == []


async def test_a_stale_descendant_marks_the_scope_read_stale(db_session, db_schema: str) -> None:
    """PW-3c item 4: the `/scope` read agrees with the resolver on the oldest walked row."""
    world = await _seed(db_session)
    writer = _client(db_schema, _principal("detection_engineer"))
    reader = _client(db_schema, _principal("viewer"))
    made = _create(writer, "stale-child", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                   scope_group_id=GROUP_ID)
    now = datetime.now(UTC)
    child = await db_session.get(LogSourceGroup, (world.instance.id, GROUP_ID + 1))
    child.synced_at = now - timedelta(hours=48)
    await db_session.commit()

    body = reader.get(f"{PREFIX}/{made['id']}/scope").json()
    assert body["resolved"] is False and body["state"] == "SCOPE_STALE"
    # The named group (Edge) is fresh; the read still reports the oldest walked row's age.
    assert body["inventory_age_seconds"] >= 47 * 3600
    assert [g["qradar_id"] for g in body["groups_walked"]] == [GROUP_ID, GROUP_ID + 1]
    assert body["reason"] and child.synced_at.isoformat() in body["reason"]


async def test_a_group_above_the_chunk_limit_is_too_large_and_keeps_its_members(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    writer = _client(db_schema, _principal("detection_engineer"))
    made = _create(writer, "too-big", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                   scope_group_id=GROUP_ID)
    client = _client(db_schema, _principal("viewer"),
                     pw_max_sources_per_search=1, pw_max_chunks_per_watch=1)
    body = client.get(f"{PREFIX}/{made['id']}/scope").json()
    assert body["resolved"] is False and body["state"] == "SCOPE_TOO_LARGE"
    assert body["member_count"] == 2 and body["members_total"] == 2
    assert body["members"] == ["fw-1", "fw-2"]
    assert "2 sources need 2 searches; the limit is 1" in body["reason"]
    assert body["changes"] == []


async def test_the_scope_read_names_at_most_200_members_and_totals_the_rest(
    db_session, db_schema: str
) -> None:
    await _bulk_tree(db_session, groups=1, sources=205, base=900)
    writer = _client(db_schema, _principal("detection_engineer"), pw_max_chunks_per_watch=10)
    reader = _client(db_schema, _principal("viewer"), pw_max_chunks_per_watch=10)
    made = _create(writer, "many", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                   scope_group_id=900)
    body = reader.get(f"{PREFIX}/{made['id']}/scope").json()
    assert body["resolved"] is True and body["member_count"] == 205
    assert body["members_total"] == 205
    assert len(body["members"]) == 200, "the names are capped, the total is not"
    assert body["members"] == sorted(body["members"])
    assert body["members"][0] == "src-0000" and body["members"][-1] == "src-0199"


async def test_the_scope_read_reports_the_seeded_membership_changes(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    writer = _client(db_schema, _principal("detection_engineer"))
    made = _create(writer, "history")
    # Oldest to newest: a change z->a, a bucket that measured nothing (with its own fingerprint,
    # so a read that kept it would invent a change), then a change a->b. The last row is another
    # version, whose bucket is not this watch's history.
    db_session.add_all([
        _bucket(made["id"], 7, scope_fingerprint="z", scope_member_count=8),
        _bucket(made["id"], 6, scope_fingerprint="a", scope_member_count=10, scope_changed=True),
        _bucket(made["id"], 5, scope_fingerprint="a", scope_member_count=10),
        _bucket(made["id"], 3, values_measurement="NOT_COLLECTED", scope_fingerprint="x",
                scope_member_count=99),
        _bucket(made["id"], 2, scope_fingerprint="b", scope_member_count=12, scope_changed=True),
        _bucket(made["id"], 1, scope_fingerprint="b", scope_member_count=12),
        _bucket(made["id"], 0, watch_version=2, scope_fingerprint="c", scope_member_count=1),
    ])
    await db_session.commit()

    changes = _client(db_schema, _principal("viewer")).get(
        f"{PREFIX}/{made['id']}/scope").json()["changes"]
    assert len(changes) == 2, "a NOT_COLLECTED bucket says nothing; another version is not ours"
    newest, oldest = changes
    assert _same_instant(newest["bucket_start"], _bucket_start(2).isoformat())
    assert newest["member_count_before"] == 10 and newest["member_count_after"] == 12
    assert newest["in_grace_window"] is True
    assert _same_instant(oldest["bucket_start"], _bucket_start(6).isoformat())
    assert oldest["member_count_before"] == 8 and oldest["member_count_after"] == 10
    assert oldest["in_grace_window"] is True


async def test_scope_options_show_a_stale_group_without_a_count(
    db_session, db_schema: str
) -> None:
    world = await _seed(db_session)
    now = datetime.now(UTC)
    db_session.add(LogSourceGroup(instance_id=world.instance.id, qradar_id=12, name="Empty",
                                  parent_qradar_id=None, assignable=True, synced_at=now))
    stale = await db_session.get(LogSourceGroup, (world.instance.id, GROUP_ID))
    stale.synced_at = now - timedelta(hours=48)
    await db_session.commit()

    client = _client(db_schema, _principal("viewer"))
    groups = client.get(f"{PREFIX}/scope-options", params={"kind": "LOG_SOURCE_GROUP"}).json()
    by_name = {g["name"]: g for g in groups}
    # Edge's own row is stale: no count, and the reason says why.
    assert by_name["Edge"]["synced_source_count"] is None
    assert by_name["Edge"]["scope_state"] == "SCOPE_STALE"
    assert by_name["Edge"]["scope_reason"]
    # Edge DMZ's own row is fresh: it still resolves and counts its one member.
    assert by_name["Edge DMZ"]["synced_source_count"] == 1
    assert by_name["Edge DMZ"]["scope_state"] is None
    # An empty group is a measured zero, not missing data.
    assert by_name["Empty"]["synced_source_count"] == 0
    assert by_name["Empty"]["scope_state"] == "SCOPE_EMPTY"


async def test_the_cap_the_preview_and_the_scope_read_agree_on_the_member_count(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    provider = MockQRadarProvider(seed=1)
    writer = _client(db_schema, _principal("detection_engineer"), provider=provider)
    made = _create(writer, "agree", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                   scope_group_id=GROUP_ID)
    reader = _client(db_schema, _principal("viewer"))
    count = reader.get(f"{PREFIX}/{made['id']}/scope").json()["member_count"]
    assert count == 2

    # The enable cap names the same number.
    strict = _client(db_schema, _principal("detection_engineer"),
                     pw_max_sources_per_search=1, pw_max_chunks_per_watch=1)
    refused = strict.patch(f"{PREFIX}/{made['id']}",
                           json={"expected_updated_at": made["updated_at"], "enabled": True})
    assert refused.status_code == 409
    assert f"resolves to {count} sources" in refused.json()["detail"]

    # A preview of the same group is refused and names the same number, spending nothing.
    narrow = _client(db_schema, _principal("detection_engineer"), provider=provider,
                     pw_max_sources_per_search=1)
    preview = narrow.post(f"{PREFIX}/{made['id']}/preview")
    assert preview.status_code == 409
    assert f"the group resolves to {count} sources" in preview.text
    assert provider.pw_searches == []

    # A roomy preview searches exactly the members the /scope read names.
    roomy = _client(db_schema, _principal("detection_engineer"), provider=provider)
    assert roomy.post(f"{PREFIX}/{made['id']}/preview").status_code == 200
    assert {i for s in provider.pw_searches for i in s.scope_ids} == {701, 702}
    assert reader.get(f"{PREFIX}/{made['id']}/scope").json()["members"] == ["fw-1", "fw-2"]


async def test_the_scope_read_costs_the_same_statements_at_every_size(
    db_session, db_schema: str
) -> None:
    reader = _client(db_schema, _principal("viewer"), pw_max_chunks_per_watch=10)
    writer = _client(db_schema, _principal("detection_engineer"), pw_max_chunks_per_watch=10)
    counts: dict[str, int] = {}
    for groups, sources in ((2, 10), (20, 200)):
        base = 1000 + groups
        instance = await _bulk_tree(db_session, groups=groups, sources=sources, base=base)
        made = _create(writer, f"scale-{groups}", scope_kind="LOG_SOURCE_GROUP",
                       scope_type_id=None, scope_group_id=base, instance_id=str(instance.id))
        reader.statements.clear()  # type: ignore[attr-defined]
        body = reader.get(f"{PREFIX}/{made['id']}/scope").json()
        assert body["member_count"] == sources and len(body["groups_walked"]) == groups
        counts[f"group-{groups}"] = len(reader.statements)  # type: ignore[attr-defined]
    assert len(set(counts.values())) == 1, counts
    assert counts["group-2"] == SCOPE_STATEMENTS["group"], counts

    for sources in (10, 200):
        base = 2000 + sources
        instance = await _bulk_tree(db_session, groups=1, sources=sources, base=base)
        made = _create(writer, f"type-{sources}", scope_group_id=None, scope_kind="LOG_SOURCE_TYPE",
                       scope_type_id=TYPE_ID, instance_id=str(instance.id))
        reader.statements.clear()  # type: ignore[attr-defined]
        body = reader.get(f"{PREFIX}/{made['id']}/scope").json()
        assert body["member_count"] == sources
        counts[f"type-{sources}"] = len(reader.statements)  # type: ignore[attr-defined]
        # A single-source read costs the same at either size too.
        source_id = await db_session.scalar(
            select(LogSource.id).where(LogSource.instance_id == instance.id).limit(1)
        )
        one = _create(writer, f"source-{sources}", scope_kind="LOG_SOURCE", scope_type_id=None,
                      log_source_id=str(source_id), instance_id=str(instance.id))
        reader.statements.clear()  # type: ignore[attr-defined]
        assert reader.get(f"{PREFIX}/{one['id']}/scope").json()["member_count"] == 1
        counts[f"source-{sources}"] = len(reader.statements)  # type: ignore[attr-defined]
    assert counts["type-10"] == counts["type-200"] == SCOPE_STATEMENTS["type"], counts
    assert counts["source-10"] == counts["source-200"] == SCOPE_STATEMENTS["source"], counts


async def test_scope_options_costs_the_same_statements_at_every_size(
    db_session, db_schema: str
) -> None:
    reader = _client(db_schema, _principal("viewer"))
    counts: dict[str, int] = {}
    for groups, sources in ((2, 10), (20, 200)):
        instance = await _bulk_tree(db_session, groups=groups, sources=sources, base=3000 + groups)
        reader.statements.clear()  # type: ignore[attr-defined]
        options = reader.get(f"{PREFIX}/scope-options",
                             params={"kind": "LOG_SOURCE_GROUP",
                                     "instance_id": str(instance.id)}).json()
        assert len(options) == groups
        assert any(o["synced_source_count"] == sources for o in options)
        counts[f"group-{groups}"] = len(reader.statements)  # type: ignore[attr-defined]
        reader.statements.clear()  # type: ignore[attr-defined]
        page = reader.get(f"{PREFIX}/scope-options",
                          params={"kind": "LOG_SOURCE", "instance_id": str(instance.id),
                                  "limit": 5}).json()
        assert len(page) == 5
        counts[f"sources-{groups}"] = len(reader.statements)  # type: ignore[attr-defined]
    assert counts["group-2"] == counts["group-20"] == OPTIONS_STATEMENTS["group"], counts
    assert counts["sources-2"] == counts["sources-20"] == OPTIONS_STATEMENTS["source"], counts

    for sources in (10, 200):
        instance = await _bulk_tree(db_session, groups=1, sources=sources, base=4000 + sources)
        reader.statements.clear()  # type: ignore[attr-defined]
        options = reader.get(f"{PREFIX}/scope-options",
                             params={"kind": "LOG_SOURCE_TYPE",
                                     "instance_id": str(instance.id)}).json()
        assert [o["synced_source_count"] for o in options] == [sources]
        counts[f"type-{sources}"] = len(reader.statements)  # type: ignore[attr-defined]
    assert counts["type-10"] == counts["type-200"] == OPTIONS_STATEMENTS["type"], counts


async def test_property_options_costs_the_same_statements_at_every_size(
    db_session, db_schema: str
) -> None:
    """PW-3c item 6: `/property-options` is flat in the number of groups and sources.

    The editor's picker resolves the scope (the walk and its sources, one bulk read each) and
    loads the instance row; it never issues a query per group or per member. Pinned so a
    per-group query cannot creep in unnoticed.
    """
    reader = _client(db_schema, _principal("viewer"), provider=MockQRadarProvider())
    counts: dict[str, int] = {}
    for groups, sources in ((2, 10), (20, 200)):
        base = 5000 + groups
        instance = await _bulk_tree(db_session, groups=groups, sources=sources, base=base)
        reader.statements.clear()  # type: ignore[attr-defined]
        body = reader.get(
            f"{PREFIX}/property-options",
            params={
                "scope_kind": "LOG_SOURCE_GROUP",
                "scope_id": base,
                "instance_id": str(instance.id),
            },
        ).json()
        assert body["status"] == "AVAILABLE" and body["scope_type_ids"] == [TYPE_ID]
        counts[f"group-{groups}"] = len(reader.statements)  # type: ignore[attr-defined]
    assert counts["group-2"] == counts["group-20"] == PROPERTY_OPTIONS_STATEMENTS["group"], counts

    for sources in (10, 200):
        base = 6000 + sources
        instance = await _bulk_tree(db_session, groups=1, sources=sources, base=base)
        reader.statements.clear()  # type: ignore[attr-defined]
        body = reader.get(
            f"{PREFIX}/property-options",
            params={
                "scope_kind": "LOG_SOURCE_TYPE",
                "scope_id": TYPE_ID,
                "instance_id": str(instance.id),
            },
        ).json()
        assert body["status"] == "AVAILABLE" and body["scope_type_ids"] == [TYPE_ID]
        counts[f"type-{sources}"] = len(reader.statements)  # type: ignore[attr-defined]
    assert counts["type-10"] == counts["type-200"] == PROPERTY_OPTIONS_STATEMENTS["type"], counts


async def test_the_scope_read_of_an_empty_group_is_a_measured_zero(
    db_session, db_schema: str
) -> None:
    """An empty group resolved: the inventory held no member, so the zero is a measured fact.

    The collection side of §G.4 stays fail-closed (no bucket, no zero); a membership count is
    a statement about the synced inventory, not a measurement, so an empty group states 0.
    """
    world = await _seed(db_session)
    now = datetime.now(UTC)
    db_session.add(LogSourceGroup(instance_id=world.instance.id, qradar_id=12, name="Empty",
                                  parent_qradar_id=None, assignable=True, synced_at=now))
    await db_session.commit()
    writer = _client(db_schema, _principal("detection_engineer"))
    reader = _client(db_schema, _principal("viewer"))
    made = _create(writer, "empty", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                   scope_group_id=12)
    reader.statements.clear()  # type: ignore[attr-defined]
    body = reader.get(f"{PREFIX}/{made['id']}/scope").json()
    assert body["resolved"] is False and body["state"] == "SCOPE_EMPTY"
    assert body["member_count"] == 0 and body["members_total"] == 0
    assert body["members"] == [] and body["changes"] == []
    assert [(g["qradar_id"], g["name"]) for g in body["groups_walked"]] == [(12, "Empty")]
    assert body["reason"] and "hold no synced log source" in body["reason"]
    assert body["inventory_synced_at"] is not None
    # The walk found nothing to name, but the read is the same size as any other group's.
    assert len(reader.statements) == SCOPE_STATEMENTS["group"]  # type: ignore[attr-defined]


async def test_a_group_that_vanished_reads_as_empty_with_no_count(
    db_session, db_schema: str
) -> None:
    """A vanished group is `SCOPE_EMPTY`: no count is drawn from nothing (rule 8)."""
    world = await _seed(db_session)
    writer = _client(db_schema, _principal("detection_engineer"))
    reader = _client(db_schema, _principal("viewer"))
    made = _create(writer, "vanished", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                   scope_group_id=GROUP_ID)
    gone = await db_session.get(LogSourceGroup, (world.instance.id, GROUP_ID))
    await db_session.delete(gone)
    await db_session.commit()

    body = reader.get(f"{PREFIX}/{made['id']}/scope").json()
    assert body["resolved"] is False and body["state"] == "SCOPE_EMPTY"
    # The group row is gone, so there is no walk and no count: never a zero invented for it.
    assert body["member_count"] is None and body["members_total"] is None
    assert body["members"] == [] and body["groups_walked"] == []
    assert body["inventory_synced_at"] is None and body["inventory_age_seconds"] is None
    assert body["reason"] and "not in the inventory" in body["reason"]


async def test_scope_options_show_no_count_for_a_group_that_is_too_large(
    db_session, db_schema: str
) -> None:
    """A group over the chunk cap shows a reason, not a count, as a stale one does."""
    await _seed(db_session)
    client = _client(db_schema, _principal("viewer"),
                     pw_max_sources_per_search=1, pw_max_chunks_per_watch=1)
    groups = client.get(f"{PREFIX}/scope-options", params={"kind": "LOG_SOURCE_GROUP"}).json()
    by_name = {g["name"]: g for g in groups}
    assert by_name["Edge"]["synced_source_count"] is None
    assert by_name["Edge"]["scope_state"] == "SCOPE_TOO_LARGE"
    assert by_name["Edge"]["scope_reason"] and "limit is 1" in by_name["Edge"]["scope_reason"]
    # One member is one chunk even at these settings, so it still counts.
    assert by_name["Edge DMZ"]["synced_source_count"] == 1
    assert by_name["Edge DMZ"]["scope_state"] is None


async def test_property_options_refuse_a_group_that_does_not_resolve(
    db_session, db_schema: str
) -> None:
    """A stale group has no trustworthy in-scope types: 409 with the resolver's reason."""
    world = await _seed(db_session)
    row = await db_session.get(LogSourceGroup, (world.instance.id, GROUP_ID))
    row.synced_at = datetime.now(UTC) - timedelta(hours=48)
    await db_session.commit()

    client = _client(db_schema, _principal("viewer"), provider=MockQRadarProvider())
    refused = client.get(f"{PREFIX}/property-options",
                         params={"scope_kind": "LOG_SOURCE_GROUP", "scope_id": GROUP_ID})
    assert refused.status_code == 409
    assert "older than" in refused.text
    assert "http" not in refused.text, "no console address in an error (rule 5)"
    # A fresh group still offers its members' types.
    fresh = client.get(f"{PREFIX}/property-options",
                       params={"scope_kind": "LOG_SOURCE_GROUP", "scope_id": GROUP_ID + 1})
    assert fresh.status_code == 200 and fresh.json()["scope_type_ids"] == [TYPE_ID]
