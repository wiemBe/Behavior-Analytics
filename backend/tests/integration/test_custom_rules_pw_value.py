"""The PW_VALUE rule kind end to end (design §G.10.2): engine, loader cost, pin-only, 409s.

The engine half runs on seeded `pw_bucket` / `pw_value_metric` rows. The API half drives the
real routes: a rule on an unpinned value is a 422, and a pinned value's watch cannot be
deleted, re-versioned or unpinned while a rule holds it.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.custom_rules.engine import CustomRuleEngine
from app.main import create_app
from app.models.custom_rule import CustomRule, CustomRuleBreach, CustomRuleWatermark
from app.models.enums import CustomRuleMetric, CustomRuleSubjectKind, Severity
from app.models.identity import AuditLog
from app.models.property_watch import PropertyWatch, PwBucket, PwValue, PwValueMetric
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.seed import _ROLES
from tests.integration.factories import make_instance, make_log_source, utc
from tests.integration.test_waf_scale import counting

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run PW_VALUE rule integration tests",
    ),
]

PREFIX = "/api/v1/custom-rules"
WATCH_PREFIX = "/api/v1/property-watches"
ACTION = {"kind": "CUSTOM_PROPERTY", "name": "WAF_Action"}
USER = {"kind": "NORMALIZED", "name": "username"}
KEY = "DENY"


def _settings(**overrides: object) -> Settings:
    values = get_settings().model_dump()
    values.update(
        custom_rules_enabled=True,
        pw_collection_enabled=True,
        collection_interval_seconds=300,
        custom_rules_max_buckets_per_run=288,
    )
    values.update(overrides)
    return Settings(**values)


async def _watch(session, instance, source, **kw) -> PropertyWatch:  # type: ignore[no-untyped-def]
    watch = PropertyWatch(
        instance_id=instance.id,
        name=f"w-{uuid.uuid4().hex[:10]}",
        scope_kind="LOG_SOURCE",
        log_source_id=source.id,
        properties=[ACTION],
        pinned_values=[[KEY]],
        detectors=[],
        watch_version=1,
        enabled=True,
        **kw,
    )
    session.add(watch)
    await session.flush()
    return watch


async def _value(session, watch, key: str = KEY, **kw) -> PwValue:  # type: ignore[no-untyped-def]
    values = {
        "watch_id": watch.id,
        "watch_version": 1,
        "value_key": key,
        "pinned": True,
        "tracked_since": utc(h=9, mi=0),
    }
    values.update(kw)
    value = PwValue(**values)
    session.add(value)
    await session.flush()
    return value


def _bucket(watch, minute: int, **kw) -> PwBucket:  # type: ignore[no-untyped-def]
    values = {
        "watch_id": watch.id,
        "bucket_start": utc(h=10, mi=minute),
        "bucket_seconds": 300,
        "watch_version": 1,
        "total_events": 100,
        "parsed_events": 100,
        "unparsed_events": 0,
        "untracked_events": 0,
        "values_measurement": "EXACT",
        "pinned_measurement": "EXACT",
        "totals_complete": True,
        "observed_values": 1,
        "tracked_values_observed": 1,
        "admitted_values": 0,
        "below_admission_threshold": 0,
        "rejected_over_quota": 0,
        "admission_truncated": False,
        "scope_changed": False,
        "in_maintenance": False,
        "collected_at": utc(h=10, mi=minute),
    }
    values.update(kw)
    return PwBucket(**values)


def _rule(name: str, value: PwValue, **kw) -> CustomRule:  # type: ignore[no-untyped-def]
    values = {
        "name": name,
        "subject_kind": CustomRuleSubjectKind.PW_VALUE,
        "pw_value_id": value.id,
        "metric": CustomRuleMetric.PW_VALUE_EVENTS.value,
        "operator": "GT",
        "threshold_value": 100.0,
        "sustain_buckets": 1,
        "recovery_buckets": 1,
        "severity": Severity.HIGH,
        "enabled": True,
        "consecutive_breaching": 0,
        "consecutive_under": 0,
    }
    values.update(kw)
    return CustomRule(**values)


async def _arm(session) -> None:  # type: ignore[no-untyped-def]
    """A watermark just before the data, so the first run judges instead of seeding."""
    await session.execute(delete(CustomRuleWatermark))
    session.add(
        CustomRuleWatermark(
            subject_kind=CustomRuleSubjectKind.PW_VALUE,
            evaluated_through=utc(h=10, mi=0),
            updated_at=utc(h=10, mi=0),
        )
    )
    await session.flush()


async def _run(session, **overrides):  # type: ignore[no-untyped-def]
    return await CustomRuleEngine(
        session, _settings(**overrides), clock=lambda: utc(h=10, mi=22)
    ).run()


async def _breaches(session) -> list[CustomRuleBreach]:  # type: ignore[no-untyped-def]
    return list((await session.scalars(select(CustomRuleBreach))).all())


# ---------------------------------------------------------------------------- the engine
async def test_a_measured_zero_opens_an_lt_rule_for_a_pinned_value(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1, name="fw")
    watch = await _watch(db_session, instance, source)
    value = await _value(db_session, watch)
    db_session.add_all([_bucket(watch, 5), _bucket(watch, 10), _bucket(watch, 15)])
    rule = _rule("went-silent", value, operator="LT", threshold_value=5.0, sustain_buckets=2)
    db_session.add(rule)
    await _arm(db_session)

    report = await _run(db_session)
    assert report.judged_buckets == 3 and report.opened == 1
    breach = (await _breaches(db_session))[0]
    assert breach.worst_value == 0.0 and breach.worst_measurement == "EXACT"
    assert breach.subject_label == f"{watch.name}: {KEY}"
    assert rule.last_status == "OPEN"


async def test_an_unmeasured_bucket_is_never_a_zero(db_session) -> None:
    """The pinned search was cut: no row proves nothing, and an LT rule must not open."""
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=2, name="fw")
    watch = await _watch(db_session, instance, source)
    value = await _value(db_session, watch)
    db_session.add_all(
        [_bucket(watch, m, pinned_measurement="LOWER_BOUND") for m in (5, 10, 15)]
    )
    rule = _rule("went-silent", value, operator="LT", threshold_value=5.0)
    db_session.add(rule)
    await _arm(db_session)

    report = await _run(db_session)
    assert report.opened == 0 and await _breaches(db_session) == []
    assert rule.last_status != "OPEN"
    assert rule.consecutive_breaching == 0 and rule.consecutive_under == 0


async def test_an_unpinned_flag_or_a_foreign_version_gives_no_zero_either(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=3, name="fw")
    watch = await _watch(db_session, instance, source)
    value = await _value(db_session, watch, tracked_since=utc(h=10, mi=40))
    other = await _watch(db_session, instance, source)
    other_value = await _value(db_session, other)
    db_session.add_all([_bucket(watch, 10), _bucket(other, 10, watch_version=2)])
    db_session.add_all(
        [
            _rule("not-tracked-yet", value, operator="LT", threshold_value=5.0),
            _rule("other-version", other_value, operator="LT", threshold_value=5.0),
        ]
    )
    await _arm(db_session)
    report = await _run(db_session)
    assert report.opened == 0 and await _breaches(db_session) == []


async def test_a_lower_bound_row_opens_a_gt_rule_and_is_labelled_so(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=4, name="fw")
    watch = await _watch(db_session, instance, source)
    value = await _value(db_session, watch)
    db_session.add_all(
        [
            _bucket(
                watch, m, values_measurement="LOWER_BOUND", pinned_measurement="LOWER_BOUND"
            )
            for m in (5, 10)
        ]
    )
    db_session.add(PwValueMetric(pw_value_id=value.id, bucket_start=utc(h=10, mi=10),
                                 events=150, bucket_seconds=300))
    db_session.add(_rule("deny-above-100", value))
    await _arm(db_session)
    report = await _run(db_session)
    assert report.opened == 1
    breach = (await _breaches(db_session))[0]
    assert breach.worst_value == 150.0 and breach.worst_measurement == "LOWER_BOUND"


async def test_the_family_gate_holds_a_pw_rule_while_collection_is_off(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=5, name="fw")
    watch = await _watch(db_session, instance, source)
    value = await _value(db_session, watch)
    db_session.add_all([_bucket(watch, 10)])
    db_session.add(PwValueMetric(pw_value_id=value.id, bucket_start=utc(h=10, mi=10),
                                 events=500, bucket_seconds=300))
    rule = _rule("deny-above-100", value)
    db_session.add(rule)
    await _arm(db_session)

    report = await _run(db_session, pw_collection_enabled=False)
    assert report.judged_buckets == 0 and await _breaches(db_session) == []
    assert rule.last_status == "NOT_COLLECTED"
    assert rule.last_status_reason == "pw_collection_enabled is off"
    mark = (await db_session.execute(select(CustomRuleWatermark))).scalar_one()
    assert mark.evaluated_through == utc(h=10, mi=0)

    await _run(db_session)
    assert len(await _breaches(db_session)) == 1


async def test_an_unpinned_value_retires_its_rule(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=6, name="fw")
    watch = await _watch(db_session, instance, source)
    value = await _value(db_session, watch, pinned=False)
    db_session.add(_bucket(watch, 10))
    db_session.add(PwValueMetric(pw_value_id=value.id, bucket_start=utc(h=10, mi=10),
                                 events=500, bucket_seconds=300))
    rule = _rule("deny-above-100", value)
    db_session.add(rule)
    await _arm(db_session)
    await _run(db_session)
    assert rule.last_status == "SUBJECT_RETIRED"
    assert rule.last_status_reason == "the value is no longer pinned"
    assert await _breaches(db_session) == []


async def _plant(db_session, count: int) -> list[str]:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=100 + count, name=f"fw{count}")
    watch = await _watch(db_session, instance, source)
    watch.pinned_values = [[f"v{i}"] for i in range(count)]
    db_session.add(_bucket(watch, 10))
    for index in range(count):
        value = await _value(db_session, watch, key=f"v{index}")
        db_session.add(PwValueMetric(pw_value_id=value.id, bucket_start=utc(h=10, mi=10),
                                     events=5, bucket_seconds=300))
        db_session.add(_rule(f"r-{count}-{index}", value, threshold_value=1_000_000.0))
    await _arm(db_session)
    with counting(db_session) as statements:
        report = await _run(db_session)
    assert report.judged_buckets >= 1 and report.opened == 0
    return list(statements)


async def test_the_loader_is_one_statement_at_10_and_at_100_rules(db_session) -> None:
    small = await _plant(db_session, 10)
    await db_session.execute(delete(CustomRule))
    large = await _plant(db_session, 100)
    assert len(small) == len(large)
    assert sum("pw_value_metric" in s for s in small) == 1
    assert sum("pw_value_metric" in s for s in large) == 1
    print(f"PW_VALUE tick statements: {len(small)} at 10 rules, {len(large)} at 100 rules")


async def test_restrict_holds_for_the_orm_too(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=7, name="fw")
    watch = await _watch(db_session, instance, source)
    value = await _value(db_session, watch)
    db_session.add(_rule("holder", value))
    await db_session.flush()
    with pytest.raises(IntegrityError, match="fk_custom_rule_pw_value_id_pw_value"):
        await db_session.delete(watch)
        await db_session.flush()
    await db_session.rollback()


# ------------------------------------------------------------------------------- the API
def _principal(role: str) -> Principal:
    return Principal(subject=role, permissions=frozenset(_ROLES[role]))


def _client(db_url: str) -> TestClient:
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

    settings = get_settings().model_copy(update={"collection_interval_seconds": 300})
    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings
    principal = _principal("detection_engineer")
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


def _rule_body(value_id: uuid.UUID | str, name: str = "deny-flood", **overrides: object) -> dict:
    body: dict[str, object] = {
        "name": name,
        "subject_kind": "PW_VALUE",
        "pw_value_id": str(value_id),
        "metric": "pw_value_events",
        "operator": "GT",
        "threshold_value": 500,
        "sustain_buckets": 3,
        "recovery_buckets": 2,
    }
    body.update(overrides)
    return body


async def _api_world(db_session, db_schema: str):  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=9, name="fw")
    await db_session.commit()
    client = _client(db_schema)
    made = client.post(
        WATCH_PREFIX,
        json={
            "name": "actions",
            "scope_kind": "LOG_SOURCE",
            "log_source_id": str(source.id),
            "properties": [ACTION],
            "pinned_values": [[KEY]],
        },
    )
    assert made.status_code == 201, made.text
    watch = made.json()
    pinned = await _value(db_session, await db_session.get(PropertyWatch, uuid.UUID(watch["id"])))
    automatic = await _value(
        db_session, await db_session.get(PropertyWatch, uuid.UUID(watch["id"])),
        key="ALLOW", pinned=False,
    )
    await db_session.commit()
    return client, watch, pinned, automatic, instance


async def test_a_rule_pins_a_pinned_value_only(db_session, db_schema: str) -> None:
    client, watch, pinned, automatic, _instance = await _api_world(db_session, db_schema)

    refused = client.post(PREFIX, json=_rule_body(automatic.id))
    assert refused.status_code == 422
    reason = " ".join(refused.json()["detail"]["reasons"])
    assert "is not pinned" in reason and "pin it on its property watch first" in reason
    assert "ALLOW" not in refused.text

    missing = client.post(PREFIX, json=_rule_body(uuid.uuid4()))
    assert missing.status_code == 422 and "does not exist" in missing.text

    # The flag alone is not enough: the watch must still hold the pin.
    await db_session.execute(
        PropertyWatch.__table__.update()
        .where(PropertyWatch.id == uuid.UUID(watch["id"]))
        .values(pinned_values=[])
    )
    await db_session.commit()
    assert client.post(PREFIX, json=_rule_body(pinned.id)).status_code == 422
    await db_session.execute(
        PropertyWatch.__table__.update()
        .where(PropertyWatch.id == uuid.UUID(watch["id"]))
        .values(pinned_values=[[KEY]])
    )
    await db_session.commit()

    wrong_pairing = client.post(PREFIX, json=_rule_body(pinned.id, log_source_id=str(uuid.uuid4())))
    assert wrong_pairing.status_code == 422
    assert "log_source_id must stay empty" in wrong_pairing.text

    ok = client.post(PREFIX, json=_rule_body(pinned.id))
    assert ok.status_code == 201, ok.text
    assert ok.json()["pw_value_id"] == str(pinned.id)
    assert ok.json()["threshold_unit"] == "events/bucket"
    again = client.get(f"{PREFIX}/{ok.json()['id']}")
    assert again.json()["subject_kind"] == "PW_VALUE"
    catalog = client.get(f"{PREFIX}/catalog").json()
    assert {
        "subject_kind": "PW_VALUE",
        "column": "pw_value_id",
        "search_family": "PROPERTY_WATCH",
    } in catalog["subject_kinds"]
    assert any(m["metric"] == "pw_value_events" for m in catalog["metrics"])


async def test_the_three_409s_name_the_rules_and_change_nothing(
    db_session, db_schema: str
) -> None:
    client, watch, pinned, _automatic, instance = await _api_world(db_session, db_schema)
    rule = client.post(PREFIX, json=_rule_body(pinned.id))
    assert rule.status_code == 201, rule.text
    path = f"{WATCH_PREFIX}/{watch['id']}"
    audits_before = int(await db_session.scalar(select(func.count()).select_from(AuditLog)) or 0)

    # 1. DELETE
    deleted = client.delete(path)
    assert deleted.status_code == 409
    detail = deleted.json()["detail"]
    assert "deny-flood" in detail["message"] and detail["rules"][0]["name"] == "deny-flood"
    assert client.get(path).status_code == 200

    # 2. a PATCH that bumps watch_version, by properties and by scope; a pin removal too
    token = client.get(path).json()["updated_at"]
    bump = client.patch(path, json={"expected_updated_at": token, "properties": [USER]})
    assert bump.status_code == 409 and "deny-flood" in bump.json()["detail"]["message"]
    other = await make_log_source(db_session, instance, qradar_id=10)
    await db_session.commit()
    scoped = client.patch(
        path,
        json={"expected_updated_at": token, "scope_kind": "LOG_SOURCE",
              "log_source_id": str(other.id)},
    )
    assert scoped.status_code == 409
    unpin_patch = client.patch(path, json={"expected_updated_at": token, "pinned_values": []})
    assert unpin_patch.status_code == 409
    after = client.get(path).json()
    assert after["watch_version"] == 1 and after["updated_at"] == token

    # a patch that bumps nothing still works
    fine = client.patch(path, json={"expected_updated_at": token, "description": "kept"})
    assert fine.status_code == 200, fine.text
    token = fine.json()["updated_at"]

    # 3. PUT /pins: unpinning the used value is refused, keeping it and adding is not
    unpin = client.put(f"{path}/pins", json={"expected_updated_at": token, "pinned_values": []})
    assert unpin.status_code == 409
    assert unpin.json()["detail"]["rules"][0]["name"] == "deny-flood"
    grow = client.put(
        f"{path}/pins", json={"expected_updated_at": token, "pinned_values": [[KEY], ["ALLOW"]]}
    )
    assert grow.status_code == 200 and grow.json()["added"] == 1
    token = grow.json()["watch"]["updated_at"]
    shrink = client.put(
        f"{path}/pins", json={"expected_updated_at": token, "pinned_values": [[KEY]]}
    )
    assert shrink.status_code == 200 and shrink.json()["removed"] == 1

    # the refusals audited nothing
    db_session.expire_all()
    audits = int(await db_session.scalar(select(func.count()).select_from(AuditLog)) or 0)
    assert audits == audits_before + 3  # the patch and two pin writes; no refusal is audited
    assert (await db_session.get(PropertyWatch, uuid.UUID(watch["id"]))) is not None

    # Detach the rule and the delete goes through.
    assert client.delete(f"{PREFIX}/{rule.json()['id']}").status_code == 204
    assert client.delete(path).status_code == 204
