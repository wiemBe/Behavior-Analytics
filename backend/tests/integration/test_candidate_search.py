"""The WAF and IPS add-source pickers search name, type, identifier and QRadar id."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.database import get_session
from app.ips.profile import IPS_PRESETS
from app.main import create_app
from app.models.enums import ClassificationReviewStatus, IpsPreset, SourceClass
from app.models.fleet import LogSourceClassification
from app.models.instance import QRadarInstance
from app.models.ips import IpsSourceProfile
from app.models.log_source import LogSource
from app.models.waf import WafSourceProfile
from app.security.auth import get_principal
from app.security.rbac import Principal

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
FAMILIES = [("waf", SourceClass.WAF), ("ips", SourceClass.IPS)]


def api_for(db_url: str, family: str) -> TestClient:
    engine = create_async_engine(db_url, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_override():  # type: ignore[no-untyped-def]
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="admin", permissions=frozenset({f"admin:{family}"})
    )
    app.dependency_overrides[get_settings] = get_settings
    return TestClient(app)


def source(instance, qradar_id: int, name: str, **extra) -> LogSource:  # type: ignore[no-untyped-def]
    return LogSource(
        instance_id=instance.id,
        qradar_id=qradar_id,
        name=name,
        monitoring_enabled=True,
        **extra,
    )


async def seed(db_session, family: str, klass: SourceClass):  # type: ignore[no-untyped-def]
    instance = QRadarInstance(
        name=f"i-{uuid.uuid4()}", console_host="q.example.test", provider_kind="mock"
    )
    other = QRadarInstance(
        name=f"o-{uuid.uuid4()}", console_host="r.example.test", provider_kind="mock"
    )
    db_session.add_all([instance, other])
    await db_session.flush()
    rows = {
        "plain": source(instance, 10, "Plain edge", type_name="Generic DSM"),
        "f5": source(instance, 11, "gateway-a", type_name="F5 Networks BIG-IP ASM"),
        "addr": source(instance, 12, "gateway-b", identifier="10.20.30.40"),
        # 4242 is this source's QRadar id and appears inside another source's name
        "id4242": source(instance, 4242, "zz-id-source"),
        "named4242": source(instance, 7, "Site 4242 firewall"),
        "pct": source(instance, 13, "Alpha%Name"),
        "pctx": source(instance, 14, "AlphaXName"),
        "under": source(instance, 15, "Under_score"),
        "underx": source(instance, 16, "UnderXscore"),
        "classed": source(instance, 99, "Zulu classed"),
        "profiled": source(instance, 50, "Has a profile"),
        "elsewhere": source(other, 10, "Plain edge elsewhere"),
    }
    db_session.add_all(rows.values())
    await db_session.flush()
    db_session.add(
        LogSourceClassification(
            log_source_id=rows["classed"].id,
            source_class=klass,
            basis="TYPE",
            conflicts=[],
            classified_at=NOW,
            review_status=ClassificationReviewStatus.NEW,
        )
    )
    if family == "waf":
        db_session.add(
            WafSourceProfile(
                instance_id=instance.id,
                log_source_id=rows["profiled"].id,
                url_property="URL",
                method_property="HTTP_Method",
            )
        )
    else:
        db_session.add(
            IpsSourceProfile(
                instance_id=instance.id,
                log_source_id=rows["profiled"].id,
                preset=IpsPreset.CISCO_FTD,
                mapping=IPS_PRESETS[IpsPreset.CISCO_FTD].mapping_dict(),
                action_value_map={},
            )
        )
    await db_session.commit()
    return instance, other, rows


def search(api: TestClient, family: str, instance, q: str | None = None, extra: str = ""):  # type: ignore[no-untyped-def]
    url = f"/api/v1/{family}/candidates?instance_id={instance.id}{extra}"
    if q is not None:
        url += f"&q={q}"
    response = api.get(url)
    assert response.status_code == 200, response.text
    return response.json()


def names(items: list[dict]) -> list[str]:
    return [item["name"] for item in items]


@pytest.mark.parametrize(("family", "klass"), FAMILIES)
async def test_each_match_path(db_session, db_schema, family, klass) -> None:
    instance, _other, _rows = await seed(db_session, family, klass)
    api = api_for(db_schema, family)
    assert names(search(api, family, instance, "plain EDGE")) == ["Plain edge"]
    # type, case-insensitively
    assert names(search(api, family, instance, "big-ip")) == ["gateway-a"]
    # identifier
    assert names(search(api, family, instance, "10.20.30")) == ["gateway-b"]
    # digits are a QRadar id match (exact) and still a contains match on the name
    assert names(search(api, family, instance, "4242")) == ["zz-id-source", "Site 4242 firewall"]
    # a partial id is not an id match
    assert names(search(api, family, instance, "424")) == ["Site 4242 firewall"]
    assert names(search(api, family, instance, "11")) == ["gateway-a"]
    # surrounding blanks are ignored
    assert names(search(api, family, instance, "%204242%20"))[0] == "zz-id-source"
    # an id beyond a 32-bit integer cannot match and must not error
    assert search(api, family, instance, "99999999999999999999") == []


@pytest.mark.parametrize(("family", "klass"), FAMILIES)
async def test_wildcards_are_literal(db_session, db_schema, family, klass) -> None:
    instance, _other, _rows = await seed(db_session, family, klass)
    api = api_for(db_schema, family)
    assert names(search(api, family, instance, "%25")) == ["Alpha%Name"]
    assert names(search(api, family, instance, "_")) == ["Under_score"]


@pytest.mark.parametrize(("family", "klass"), FAMILIES)
async def test_family_first_then_name_and_exclusions(
    db_session, db_schema, family, klass
) -> None:
    instance, _other, _rows = await seed(db_session, family, klass)
    api = api_for(db_schema, family)
    listed = names(search(api, family, instance))
    assert listed[0] == "Zulu classed"
    assert listed[1:] == sorted(listed[1:], key=str.lower)
    assert "Has a profile" not in listed
    assert "Plain edge elsewhere" not in listed
    first = search(api, family, instance)[0]
    assert first["source_class"] == klass.value
    assert {"type_name", "identifier"} <= first.keys()


@pytest.mark.parametrize(("family", "klass"), FAMILIES)
async def test_exact_id_match_precedes_name_order(db_session, db_schema, family, klass) -> None:
    instance, _other, _rows = await seed(db_session, family, klass)
    api = api_for(db_schema, family)
    # "zz-id-source" sorts after "Site 4242 firewall" by name; the id match comes first
    assert names(search(api, family, instance, "4242")) == ["zz-id-source", "Site 4242 firewall"]


@pytest.mark.parametrize(("family", "klass"), FAMILIES)
async def test_identifier_and_type_are_returned_and_limit_applies(
    db_session, db_schema, family, klass
) -> None:
    instance, _other, _rows = await seed(db_session, family, klass)
    api = api_for(db_schema, family)
    [addr] = search(api, family, instance, "10.20.30.40")
    assert addr["identifier"] == "10.20.30.40"
    [f5] = search(api, family, instance, "big-ip")
    assert f5["type_name"] == "F5 Networks BIG-IP ASM"
    assert len(search(api, family, instance, extra="&limit=3")) == 3
    too_many = api.get(f"/api/v1/{family}/candidates?instance_id={instance.id}&limit=201")
    assert too_many.status_code == 422
