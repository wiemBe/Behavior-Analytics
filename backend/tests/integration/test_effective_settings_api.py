"""The API routes that read a feature switch decide from the effective value (OPS-FLAGS S3b).

Real Postgres and the real `feature_switch` table. One route per family: the search-cadence
list shows `enabled` for every collection family and for explanations, the custom-rule and
property-watch catalogs show the alerting switches, and the QRadar health read its own.
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.rate_limit import limiter
from app.main import create_app
from app.models.base import utcnow
from app.models.feature_switch import FEATURE_KEYS, FeatureSwitch
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.feature_switches import EFFECTIVE_CACHE_SECONDS, effective_cache

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

CADENCE = "/api/v1/admin/search-cadence"
FEATURES = "/api/v1/admin/features"

#: family value -> its collection switch, as `search_cadence._FAMILY_SWITCHES` has it.
FAMILIES = {
    "WAF_COLLECTION": "waf_collection_enabled",
    "ANOMALY_EXPLANATION": "explanation_enabled",
    "RULE_MATCH": "rule_match_collection_enabled",
    "IPS_COLLECTION": "ips_collection_enabled",
    "PROPERTY_WATCH": "pw_collection_enabled",
    "CATCHALL_SENDERS": "catchall_collection_enabled",
    "GEO_COLLECTION": "geo_collection_enabled",
    "QRADAR_HEALTH": "qradar_health_collection_enabled",
    "IDENTITY_COLLECTION": "identity_collection_enabled",
}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Rig:
    def __init__(self, client: httpx.AsyncClient, maker, engine, clock: Clock) -> None:
        self.client, self.maker, self.engine, self.clock = client, maker, engine, clock
        self.statements: list[str] = []

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def record(conn, cursor, statement, *rest):  # type: ignore[no-untyped-def]
            self.statements.append(statement)

    def selects(self) -> int:
        return sum(1 for s in self.statements if "FROM feature_switch" in s)

    async def row(self, key: str, enabled: bool) -> None:
        """A row written behind this process's back, as another replica's PUT would."""
        async with self.maker() as session:
            session.add(
                FeatureSwitch(key=key, enabled=enabled, updated_at=utcnow(), updated_by="other")
            )
            await session.commit()

    async def enabled(self) -> dict[str, bool]:
        response = await self.client.get(CADENCE)
        assert response.status_code == 200, response.text
        return {row["family"]: row["enabled"] for row in response.json()}


def _rig_factory(env: dict[str, bool]):  # type: ignore[no-untyped-def]
    @pytest_asyncio.fixture
    async def rig(db_schema):  # type: ignore[no-untyped-def]
        engine = create_async_engine(db_schema)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        app = create_app()
        base = Settings(_env_file=None).model_copy(update=env)
        principal = Principal("operator", permissions=frozenset({"admin:*"}))

        async def sessions():  # type: ignore[no-untyped-def]
            async with maker() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        app.dependency_overrides[get_session] = sessions
        app.dependency_overrides[get_settings] = lambda: base
        app.dependency_overrides[get_principal] = lambda: principal
        limiter.reset()
        clock = Clock()
        effective_cache.invalidate()
        saved = effective_cache.clock
        effective_cache.clock = clock
        effective_cache.ttl = EFFECTIVE_CACHE_SECONDS
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test"
            ) as client:
                yield Rig(client, maker, engine, clock)
        finally:
            effective_cache.clock = saved
            effective_cache.invalidate()
            limiter.reset()
            await engine.dispose()

    return rig


rig_off = _rig_factory(dict.fromkeys(FEATURE_KEYS, False))
rig_on = _rig_factory(dict.fromkeys(FEATURE_KEYS, True))


@pytest.mark.parametrize(("family", "key"), FAMILIES.items())
async def test_a_row_turns_a_family_on_that_the_env_leaves_off(rig_off, family, key) -> None:
    assert (await rig_off.enabled())[family] is False
    await rig_off.row(key, True)
    rig_off.clock.now += EFFECTIVE_CACHE_SECONDS + 1
    assert (await rig_off.enabled())[family] is True


@pytest.mark.parametrize(("family", "key"), FAMILIES.items())
async def test_a_row_turns_a_family_off_that_the_env_turns_on(rig_on, family, key) -> None:
    assert (await rig_on.enabled())[family] is True
    await rig_on.row(key, False)
    rig_on.clock.now += EFFECTIVE_CACHE_SECONDS + 1
    assert (await rig_on.enabled())[family] is False


async def test_a_put_is_visible_to_the_next_get_on_the_same_replica(rig_off) -> None:
    assert (await rig_off.enabled())["GEO_COLLECTION"] is False  # warms the cache
    response = await rig_off.client.put(
        f"{FEATURES}/geo_collection_enabled", json={"enabled": True}
    )
    assert response.status_code == 204
    assert (await rig_off.enabled())["GEO_COLLECTION"] is True  # no clock advance
    assert (await rig_off.client.delete(f"{FEATURES}/geo_collection_enabled")).status_code == 204
    assert (await rig_off.enabled())["GEO_COLLECTION"] is False


async def test_another_replicas_write_shows_after_five_seconds_and_not_before(rig_off) -> None:
    assert (await rig_off.enabled())["GEO_COLLECTION"] is False
    await rig_off.row("geo_collection_enabled", True)
    rig_off.clock.now += EFFECTIVE_CACHE_SECONDS - 0.5
    assert (await rig_off.enabled())["GEO_COLLECTION"] is False
    rig_off.clock.now += 1.0
    assert (await rig_off.enabled())["GEO_COLLECTION"] is True


async def test_the_switch_table_is_read_once_per_window_not_once_per_request(rig_off) -> None:
    rig_off.statements.clear()
    for _ in range(5):
        await rig_off.enabled()
    assert rig_off.selects() == 1
    rig_off.clock.now += EFFECTIVE_CACHE_SECONDS + 1
    for _ in range(5):
        await rig_off.enabled()
    assert rig_off.selects() == 2


async def test_an_unreadable_table_answers_as_every_family_off(rig_on) -> None:
    async with rig_on.engine.begin() as conn:
        await conn.execute(text("DROP TABLE feature_switch"))
    enabled = await rig_on.enabled()  # env says on; the unread table must not
    assert set(enabled) >= set(FAMILIES) and not any(enabled[f] for f in FAMILIES)
    catalog = (await rig_on.client.get("/api/v1/custom-rules/catalog")).json()
    assert catalog["evaluation_enabled"] is False and catalog["alerting_mode"] == "shadow"


async def test_the_custom_rule_and_property_watch_catalogs_follow_the_row(rig_off) -> None:
    catalog = (await rig_off.client.get("/api/v1/custom-rules/catalog")).json()
    assert (catalog["evaluation_enabled"], catalog["alerting_mode"]) == (False, "shadow")
    pw = (await rig_off.client.get("/api/v1/property-watches/catalog")).json()
    assert pw["collection_enabled"] is False
    for key in ("custom_rules_enabled", "custom_rules_alerting_enabled", "pw_collection_enabled"):
        await rig_off.row(key, True)
    rig_off.clock.now += EFFECTIVE_CACHE_SECONDS + 1
    catalog = (await rig_off.client.get("/api/v1/custom-rules/catalog")).json()
    assert (catalog["evaluation_enabled"], catalog["alerting_mode"]) == (True, "alerting")
    pw = (await rig_off.client.get("/api/v1/property-watches/catalog")).json()
    assert pw["collection_enabled"] is True


async def test_the_health_read_follows_the_row(rig_off) -> None:
    before = (await rig_off.client.get("/api/v1/admin/qradar-health/latest")).json()
    await rig_off.row("qradar_health_collection_enabled", True)
    rig_off.clock.now += EFFECTIVE_CACHE_SECONDS + 1
    after = (await rig_off.client.get("/api/v1/admin/qradar-health/latest")).json()
    assert before != after
