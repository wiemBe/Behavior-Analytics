"""PW-6b: pin users on success, the delete/create race as 409, the family cadence.

The races are forced by interleaving a second, committed session at the exact point between
the PW-6 check and the write (a wrapper around the check runs the real check, then lets the
other session commit). That is the order a concurrent request produces, without a sleep.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.api.routes import custom_rules as rules_routes
from app.api.routes import property_watches as watch_routes
from app.core.config import get_settings
from app.core.database import get_session
from app.main import create_app
from app.models.custom_rule import CustomRule
from app.models.enums import SearchFamily, default_search_cadence
from app.models.identity import AuditLog
from app.models.property_watch import PropertyWatch, PwValue
from app.models.search_cadence import SearchCadenceSetting
from app.security.auth import get_principal
from tests.integration.factories import make_instance, make_log_source, utc
from tests.integration.test_custom_rules_pw_value import (
    ACTION,
    KEY,
    PREFIX,
    USER,
    WATCH_PREFIX,
    _api_world,
    _principal,
    _rule,
    _rule_body,
    _value,
    _watch,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run PW-6b integration tests",
    ),
]

FK = "fk_custom_rule_pw_value_id_pw_value"
HOSTILE = '<img src=x onerror="alert(1)">'


def _counted_client(db_url: str, **overrides: object):  # type: ignore[no-untyped-def]
    """A client whose engine is exposed, so the test can count its statements."""
    engine = create_async_engine(db_url, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():  # type: ignore[no-untyped-def]
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    settings = get_settings().model_copy(
        update={"collection_interval_seconds": 300, **overrides}
    )
    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings
    principal = _principal("detection_engineer")
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app), engine


@contextmanager
def _statements(engine):  # type: ignore[no-untyped-def]
    from sqlalchemy import event

    seen: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


async def _other_session_commit(db_url: str, work):  # type: ignore[no-untyped-def]
    """Run `work(session)` in a second session and commit, as a concurrent request would."""
    engine = create_async_engine(db_url, future=True, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as other:
            await work(other)
            await other.commit()
    finally:
        await engine.dispose()


async def _audits(db_session) -> int:  # type: ignore[no-untyped-def]
    db_session.expire_all()
    return int(await db_session.scalar(select(func.count()).select_from(AuditLog)) or 0)


# ------------------------------------------------------------------- PinsOut.rules
async def test_pins_success_lists_the_rules_that_pin_a_value(db_session, db_schema: str) -> None:
    client, engine = _counted_client(db_schema)
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=21, name="fw")
    await db_session.commit()
    made = client.post(
        WATCH_PREFIX,
        json={"name": "actions", "scope_kind": "LOG_SOURCE", "log_source_id": str(source.id),
              "properties": [ACTION], "pinned_values": [[KEY]]},
    )
    assert made.status_code == 201, made.text
    watch = made.json()
    path = f"{WATCH_PREFIX}/{watch['id']}"
    row = await db_session.get(PropertyWatch, uuid.UUID(watch["id"]))
    pinned = await _value(db_session, row)
    await db_session.commit()

    # no rule yet: an empty list, not a missing field
    empty = client.put(
        f"{path}/pins", json={"expected_updated_at": watch["updated_at"],
                              "pinned_values": [[KEY], ["ALLOW"]]},
    )
    assert empty.status_code == 200, empty.text
    assert empty.json()["rules"] == []

    for name in ("b-rule", "a-rule"):
        assert client.post(PREFIX, json=_rule_body(pinned.id, name=name)).status_code == 201
    token = client.get(path).json()["updated_at"]
    with _statements(engine) as seen:
        grown = client.put(
            f"{path}/pins", json={"expected_updated_at": token,
                                  "pinned_values": [[KEY], ["ALLOW"], ["TCP"]]},
        )
    assert grown.status_code == 200, grown.text
    rules = grown.json()["rules"]
    assert [r["name"] for r in rules] == ["a-rule", "b-rule"]
    keys = {"id", "name", "pw_value_id", "value_key", "components", "enabled"}
    assert all(r["pw_value_id"] == str(pinned.id) and set(r) == keys for r in rules)
    assert all(r["value_key"] == KEY and r["components"] == [KEY] for r in rules)
    # one statement reads the rules; nothing is refused on a pure addition
    assert sum("custom_rule" in s and "pw_value" in s for s in seen) == 1

    # a no-op PUT reports them too
    token = grown.json()["watch"]["updated_at"]
    same = client.put(
        f"{path}/pins", json={"expected_updated_at": token,
                              "pinned_values": [[KEY], ["ALLOW"], ["TCP"]]},
    )
    assert [r["name"] for r in same.json()["rules"]] == ["a-rule", "b-rule"]

    # a removal of an unused pin succeeds and still lists the remaining dependency
    token = same.json()["watch"]["updated_at"]
    shrunk = client.put(
        f"{path}/pins", json={"expected_updated_at": token, "pinned_values": [[KEY]]}
    )
    assert shrunk.status_code == 200 and shrunk.json()["removed"] == 2
    assert [r["name"] for r in shrunk.json()["rules"]] == ["a-rule", "b-rule"]
    await engine.dispose()


# --------------------------------------------------------------- the watch-side race
async def test_a_rule_created_after_the_check_makes_the_delete_a_409(
    db_session, db_schema: str, monkeypatch
) -> None:
    client, watch, pinned, _auto, _instance = await _api_world(db_session, db_schema)
    path = f"{WATCH_PREFIX}/{watch['id']}"
    pinned_id, pinned_ref = pinned.id, SimpleNamespace(id=pinned.id)
    audits = await _audits(db_session)
    original = watch_routes._refuse_if_pinned_by_rules
    calls = {"n": 0}

    async def check_then_let_a_rule_commit(session, watch_id, action, **kw):  # type: ignore[no-untyped-def]
        await original(session, watch_id, action, **kw)  # the PW-6 check passes: no rule yet
        calls["n"] += 1

        async def create(other: AsyncSession) -> None:
            other.add(_rule("late-rule", pinned_ref))
            await other.flush()

        await _other_session_commit(db_schema, create)

    monkeypatch.setattr(watch_routes, "_refuse_if_pinned_by_rules", check_then_let_a_rule_commit)
    refused = client.delete(path)
    assert calls["n"] == 1
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert "late-rule" in detail["message"] and "delete the watch" in detail["message"]
    assert [r["name"] for r in detail["rules"]] == ["late-rule"]
    assert set(detail["rules"][0]) == {"id", "name"}

    db_session.expire_all()
    assert await db_session.get(PropertyWatch, uuid.UUID(watch["id"])) is not None
    assert await db_session.get(PwValue, pinned_id) is not None
    assert await _audits(db_session) == audits  # nothing audited for the refusal
    # the rule is the committed one; detaching it lets the delete through
    monkeypatch.undo()
    rule_id = (await db_session.scalars(select(CustomRule.id))).one()
    assert client.delete(f"{PREFIX}/{rule_id}").status_code == 204
    assert client.delete(path).status_code == 204


async def test_a_pin_removal_or_patch_that_fails_on_the_rule_fk_is_a_409(
    db_session, db_schema: str, monkeypatch
) -> None:
    """No PATCH or PUT write deletes a value today, so the key cannot fail for real there.

    The check is stubbed past and the flush is made to fail exactly as Postgres reports the
    RESTRICT key, so the handler of both routes is pinned: a 409 with the rules, not a 500.
    """
    client, watch, pinned, _auto, _instance = await _api_world(db_session, db_schema)
    assert client.post(PREFIX, json=_rule_body(pinned.id)).status_code == 201
    path = f"{WATCH_PREFIX}/{watch['id']}"
    token = client.get(path).json()["updated_at"]
    audits = await _audits(db_session)

    async def pass_the_check(*_a: object, **_k: object) -> None:
        return None

    real_flush = Session.flush

    def failing_flush(self, objects=None):  # type: ignore[no-untyped-def]
        if any(isinstance(o, PropertyWatch) for o in self.dirty):
            raise IntegrityError("UPDATE", {}, Exception(f'violates foreign key "{FK}"'))
        real_flush(self, objects)

    monkeypatch.setattr(watch_routes, "_refuse_if_pinned_by_rules", pass_the_check)
    monkeypatch.setattr(Session, "flush", failing_flush)
    put = client.put(f"{path}/pins", json={"expected_updated_at": token, "pinned_values": []})
    patch = client.patch(path, json={"expected_updated_at": token, "properties": [ACTION],
                                     "description": "x"})
    monkeypatch.undo()
    for response in (put, patch):
        assert response.status_code == 409, response.text
        assert [r["name"] for r in response.json()["detail"]["rules"]] == ["deny-flood"]
    after = client.get(path).json()
    assert after["pinned_values"] == [[KEY]] and after["updated_at"] == token
    assert await _audits(db_session) == audits


async def test_other_integrity_errors_on_the_watch_routes_still_raise(
    db_session, db_schema: str, monkeypatch
) -> None:
    client, watch, _pinned, _auto, _instance = await _api_world(db_session, db_schema)
    path = f"{WATCH_PREFIX}/{watch['id']}"
    token = client.get(path).json()["updated_at"]
    real_flush = Session.flush

    def failing_flush(self, objects=None):  # type: ignore[no-untyped-def]
        if any(isinstance(o, PropertyWatch) for o in self.dirty):
            raise IntegrityError("UPDATE", {}, Exception("some other constraint"))
        real_flush(self, objects)

    monkeypatch.setattr(Session, "flush", failing_flush)
    with pytest.raises(IntegrityError, match="some other constraint"):
        client.put(f"{path}/pins", json={"expected_updated_at": token,
                                         "pinned_values": [[KEY], ["X"]]})


# ----------------------------------------------------------------- the rule-side race
async def _delete_watch_after_the_check(monkeypatch, db_schema: str, watch_id: str):  # type: ignore[no-untyped-def]
    original = rules_routes._pw_value_reason

    async def check_then_delete(session, draft):  # type: ignore[no-untyped-def]
        reason = await original(session, draft)  # the value still exists and is pinned
        assert reason is None

        async def drop(other: AsyncSession) -> None:
            await other.execute(
                delete(PropertyWatch).where(PropertyWatch.id == uuid.UUID(watch_id))
            )

        await _other_session_commit(db_schema, drop)
        return reason

    monkeypatch.setattr(rules_routes, "_pw_value_reason", check_then_delete)


async def test_a_value_deleted_after_the_check_makes_the_rule_create_a_409(
    db_session, db_schema: str, monkeypatch
) -> None:
    client, watch, pinned, _auto, _instance = await _api_world(db_session, db_schema)
    pinned_id = pinned.id
    audits = await _audits(db_session)
    await _delete_watch_after_the_check(monkeypatch, db_schema, watch["id"])
    response = client.post(PREFIX, json=_rule_body(pinned_id))
    assert response.status_code == 409, response.text
    text = response.json()["detail"]
    assert "no longer exists" in text and "reload" in text
    db_session.expire_all()
    assert (await db_session.scalars(select(CustomRule))).all() == []
    assert await _audits(db_session) == audits


async def test_a_value_deleted_after_the_check_makes_the_rule_patch_a_409(
    db_session, db_schema: str, monkeypatch
) -> None:
    client, _w, pinned, _auto, instance = await _api_world(db_session, db_schema)
    source = await make_log_source(db_session, instance, qradar_id=31, name="fw2")
    second = await _watch(db_session, instance, source)
    target = await _value(db_session, second)
    await db_session.commit()
    pinned_id, target_id, second_id = pinned.id, target.id, second.id
    created = client.post(PREFIX, json=_rule_body(pinned_id))
    assert created.status_code == 201, created.text
    audits = await _audits(db_session)

    await _delete_watch_after_the_check(monkeypatch, db_schema, str(second_id))
    response = client.patch(
        f"{PREFIX}/{created.json()['id']}",
        json={"expected_updated_at": created.json()["updated_at"],
              "pw_value_id": str(target_id)},
    )
    assert response.status_code == 409, response.text
    assert "no longer exists" in response.json()["detail"]
    db_session.expire_all()
    stored = (await db_session.scalars(select(CustomRule))).one()
    assert stored.pw_value_id == pinned_id
    assert await _audits(db_session) == audits


async def test_the_rule_name_conflict_and_other_errors_keep_their_handling(
    db_session, db_schema: str, monkeypatch
) -> None:
    client, _watch_json, pinned, _auto, _instance = await _api_world(db_session, db_schema)
    assert client.post(PREFIX, json=_rule_body(pinned.id)).status_code == 201
    # the name pre-check is stubbed past so the database's own constraint decides
    async def never_taken(*_a: object, **_k: object) -> bool:
        return False

    monkeypatch.setattr(rules_routes, "_name_taken", never_taken)
    dup = client.post(PREFIX, json=_rule_body(pinned.id))
    assert dup.status_code == 409 and "already exists" in dup.json()["detail"]

    real_flush = Session.flush

    def failing_flush(self, objects=None):  # type: ignore[no-untyped-def]
        if any(isinstance(o, CustomRule) for o in self.new):
            raise IntegrityError("INSERT", {}, Exception("some other constraint"))
        real_flush(self, objects)

    monkeypatch.setattr(Session, "flush", failing_flush)
    with pytest.raises(IntegrityError, match="some other constraint"):
        client.post(PREFIX, json=_rule_body(pinned.id, name="other"))


# ----------------------------------------------------------------------- the cadence
async def test_cadence_seconds_is_read_once_per_request(db_session, db_schema: str) -> None:
    client, engine = _counted_client(db_schema)
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=41, name="fw")
    db_session.add(SearchCadenceSetting(
            family="PROPERTY_WATCH", cadence_seconds=1800, updated_at=utc(h=9)
        ))
    first = await _watch(db_session, instance, source)
    await db_session.commit()

    with _statements(engine) as one:
        listed = client.get(WATCH_PREFIX)
    assert listed.status_code == 200
    assert [w["cadence_seconds"] for w in listed.json()] == [1800]
    single = client.get(f"{WATCH_PREFIX}/{first.id}")
    assert single.json()["cadence_seconds"] == 1800

    for _ in range(19):
        await _watch(db_session, instance, source)
    await db_session.commit()
    with _statements(engine) as twenty:
        listed = client.get(WATCH_PREFIX)
    assert len(listed.json()) == 20
    assert {w["cadence_seconds"] for w in listed.json()} == {1800}
    assert len(one) == len(twenty)
    assert sum("search_cadence" in s for s in one) == 1
    assert sum("search_cadence" in s for s in twenty) == 1

    # a changed family setting is served on the next read
    await db_session.execute(
        SearchCadenceSetting.__table__.update()
        .where(SearchCadenceSetting.family == "PROPERTY_WATCH")
        .values(cadence_seconds=300)
    )
    await db_session.commit()
    assert client.get(f"{WATCH_PREFIX}/{first.id}").json()["cadence_seconds"] == 300
    await engine.dispose()


async def test_a_missing_family_row_serves_the_default_cadence(
    db_session, db_schema: str
) -> None:
    client, engine = _counted_client(db_schema)
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=42, name="fw")
    watch = await _watch(db_session, instance, source)
    await db_session.commit()
    # the same default the collector uses when the seed row is missing
    assert client.get(f"{WATCH_PREFIX}/{watch.id}").json()["cadence_seconds"] == 300
    await engine.dispose()


async def test_the_watch_routes_carry_the_family_timing(db_session, db_schema: str) -> None:
    """T-140: the figure's three inputs, from the cadence table and the settings, once a read."""
    client, engine = _counted_client(
        db_schema, collection_interval_seconds=900, search_cadence_settle_seconds=600
    )
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=43, name="fw")
    db_session.add(
        SearchCadenceSetting(family="PROPERTY_WATCH", cadence_seconds=1800, updated_at=utc(h=9))
    )
    first = await _watch(db_session, instance, source)
    await _watch(db_session, instance, source)
    await db_session.commit()
    timing = {
        "family": "PROPERTY_WATCH",
        "cadence_seconds": 1800,
        "bucket_seconds": 900,
        "settle_seconds": 600,
    }

    with _statements(engine) as listing:
        listed = client.get(WATCH_PREFIX)
    assert [w["cadence"] for w in listed.json()] == [timing, timing]
    assert [w["cadence_seconds"] for w in listed.json()] == [1800, 1800]
    assert client.get(f"{WATCH_PREFIX}/{first.id}").json()["cadence"] == timing
    with _statements(engine) as catalog:
        served = client.get(f"{WATCH_PREFIX}/catalog")
    assert served.status_code == 200, served.text
    assert served.json()["cadence"] == timing
    assert sum("search_cadence" in s for s in listing) == 1
    assert sum("search_cadence" in s for s in catalog) == 1
    await engine.dispose()


async def test_a_missing_family_row_serves_the_default_timing(db_session, db_schema: str) -> None:
    client, engine = _counted_client(db_schema)
    served = client.get(f"{WATCH_PREFIX}/catalog").json()["cadence"]
    settings = get_settings()
    assert served == {
        "family": "PROPERTY_WATCH",
        "cadence_seconds": 300,
        "bucket_seconds": 300,
        "settle_seconds": settings.search_cadence_settle_seconds,
    }
    await engine.dispose()


async def test_the_rule_catalog_carries_one_timing_per_family_with_one_read(
    db_session, db_schema: str
) -> None:
    client, engine = _counted_client(
        db_schema, collection_interval_seconds=900, search_cadence_settle_seconds=600
    )
    db_session.add_all(
        [
            SearchCadenceSetting(
                family="PHASE_A_VOLUME", cadence_seconds=3600, updated_at=utc(h=9)
            ),
            SearchCadenceSetting(
                family="PROPERTY_WATCH", cadence_seconds=1800, updated_at=utc(h=9)
            ),
        ]
    )
    await db_session.commit()

    with _statements(engine) as seen:
        response = client.get(f"{PREFIX}/catalog")
    assert response.status_code == 200, response.text
    body = response.json()
    by_family = {c["family"]: c for c in body["cadences"]}
    assert set(by_family) == {
        "PHASE_A_VOLUME",
        "WAF_COLLECTION",
        "IPS_COLLECTION",
        "PROPERTY_WATCH",
    }
    assert len(body["cadences"]) == 4
    assert by_family["PHASE_A_VOLUME"] == {
        "family": "PHASE_A_VOLUME",
        "cadence_seconds": 3600,
        "bucket_seconds": 900,
        "settle_seconds": 600,
    }
    assert by_family["PROPERTY_WATCH"]["cadence_seconds"] == 1800
    # a family with no row serves the default, as the collector does
    assert by_family["WAF_COLLECTION"]["cadence_seconds"] == int(
        default_search_cadence(SearchFamily.WAF_COLLECTION)
    )
    families = {row["subject_kind"]: row["search_family"] for row in body["subject_kinds"]}
    assert families == {
        "LOG_SOURCE": "PHASE_A_VOLUME",
        "FLEET_CLASS": "PHASE_A_VOLUME",
        "WAF_ENDPOINT": "WAF_COLLECTION",
        "IPS_ENTITY": "IPS_COLLECTION",
        "IPS_SOURCE": "IPS_COLLECTION",
        "PW_VALUE": "PROPERTY_WATCH",
    }
    assert {c["family"] for c in body["cadences"]} == set(families.values())
    assert sum("search_cadence" in s for s in seen) == 1
    await engine.dispose()


async def test_the_single_read_lists_the_threshold_rules_and_the_list_does_not(
    db_session, db_schema: str
) -> None:
    client, engine = _counted_client(db_schema)
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=51, name="fw")
    mine = await _watch(db_session, instance, source)
    mine.properties = [USER, ACTION]
    mine.pinned_values = [["alice", HOSTILE]]
    other = await _watch(db_session, instance, source)
    own_value = await _value(db_session, mine, key=f"alice\x1f{HOSTILE}")
    foreign_value = await _value(db_session, other)
    db_session.add_all(
        [
            _rule("b-rule", own_value, enabled=False),
            _rule("a-rule", own_value),
            _rule("elsewhere", foreign_value),
        ]
    )
    await db_session.commit()

    with _statements(engine) as seen:
        single = client.get(f"{WATCH_PREFIX}/{mine.id}")
    assert single.status_code == 200, single.text
    rules = single.json()["threshold_rules"]
    assert [(r["name"], r["enabled"]) for r in rules] == [("a-rule", True), ("b-rule", False)]
    assert all(r["pw_value_id"] == str(own_value.id) for r in rules)
    assert all(r["value_key"] == f"alice\x1f{HOSTILE}" for r in rules)
    assert all(r["components"] == ["alice", HOSTILE] for r in rules)
    assert sum("custom_rule" in s and "pw_value" in s for s in seen) == 1

    with _statements(engine) as listed_seen:
        listed = client.get(WATCH_PREFIX)
    assert listed.status_code == 200
    assert all(w.get("threshold_rules") is None for w in listed.json())
    assert not any("custom_rule" in s for s in listed_seen)
    await engine.dispose()
