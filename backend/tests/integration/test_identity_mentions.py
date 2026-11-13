"""IDENTITY_TOP mentions, the timeline lane, and migration 0071."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.identity.collector import COLLECTOR_NAME, IdentityCollector
from app.models.entity import EntityMention
from app.models.enums import IdentityMeasurement
from app.models.identity_detection import (
    IdentityEventCount,
    IdentitySourceProfile,
    IdentityTopUsername,
)
from app.models.instance import QRadarInstance
from app.models.monitoring import CollectionWatermark
from app.providers.mock import MockQRadarProvider
from app.security.rbac import Principal
from tests.integration.factories import make_instance, make_log_source
from tests.integration.schema_reset import reset_public_schema
from tests.integration.test_fleet_api import Client, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="set TEST_DATABASE_URL"),
]

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
WIDTH = timedelta(seconds=300)
BUCKET = NOW - WIDTH
EXACT = IdentityMeasurement.EXACT.value
READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
CATALOG = [4625, 4740, 4720, 4726, 4728, 4732, 4756, 1102]


def _settings() -> Settings:
    return Settings(
        search_cadence_settle_seconds=0,
        collection_interval_seconds=300,
        collection_max_backfill_intervals=0,
        identity_max_sources_per_search=50,
        ariel_multi_bucket_max_rows=5000,
        search_cadence_max_searches_per_tick=16,
    )


def _sync_url() -> str:
    return os.environ["TEST_DATABASE_URL"].replace("+asyncpg", "+psycopg")


def _config():  # type: ignore[no-untyped-def]
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", _sync_url())
    return config


def _version(engine) -> str:  # type: ignore[no-untyped-def]
    with engine.connect() as conn:
        return str(conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one())


@contextmanager
def _counting(session: object) -> Iterator[list[str]]:
    statements: list[str] = []

    def record(_conn, _cursor, statement, _params, _context, _executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    engine = session.bind.sync_engine  # type: ignore[attr-defined]
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _mention_ops(statements: list[str]) -> tuple[int, int]:
    deletes = inserts = 0
    for sql in statements:
        compact = " ".join(sql.split()).lower()
        if "delete from entity_mention" in compact or 'delete from "entity_mention"' in compact:
            deletes += 1
        if "insert into entity_mention" in compact or 'insert into "entity_mention"' in compact:
            inserts += 1
    return deletes, inserts


async def _collect(session, instance: QRadarInstance, qradar_ids: list[int]) -> list[str]:
    for qradar_id in qradar_ids:
        source = await make_log_source(
            session, instance, qradar_id=qradar_id, name=f"dc-{qradar_id}"
        )
        session.add(
            IdentitySourceProfile(
                instance_id=instance.id,
                log_source_id=source.id,
                enabled=True,
                profile_version=1,
                event_id_property="EventID",
                username_property="username",
                enabled_event_ids=list(CATALOG),
                validation_status="VALID",
                validated_profile_version=1,
                validated_at=NOW,
                validation_report={},
            )
        )
    session.add(
        CollectionWatermark(
            instance_id=instance.id, collector=COLLECTOR_NAME, watermark_at=NOW - 2 * WIDTH
        )
    )
    await session.flush()
    provider = MockQRadarProvider(seed=1)
    with _counting(session) as statements:
        await IdentityCollector(
            session, provider, settings=_settings(), clock=lambda: NOW
        ).collect(instance)
    return list(statements)


async def test_only_stored_names_are_mentioned_in_one_delete_and_one_insert(db_session) -> None:
    one = await make_instance(db_session)
    one_sql = await _collect(db_session, one, [1003])
    names = set(
        (
            await db_session.scalars(
                select(EntityMention.value_norm).where(EntityMention.instance_id == one.id)
            )
        ).all()
    )
    assert "bob" not in names
    assert {"alice", "carol", "dave", "eve", "auditor"} <= names
    assert _mention_ops(one_sql) == (1, 1)

    many = await make_instance(db_session)
    many_sql = await _collect(db_session, many, [1003, 1004])
    assert _mention_ops(many_sql) == (1, 1)
    many_names = set(
        (
            await db_session.scalars(
                select(EntityMention.value_norm).where(EntityMention.instance_id == many.id)
            )
        ).all()
    )
    assert "bob" not in many_names
    assert "mallory" in many_names and "frank" in many_names


class _Timeline(Client):
    def __init__(self, *, enabled: bool) -> None:
        super().__init__(READER)
        settings = get_settings().model_copy(update={"identity_collection_enabled": enabled})
        self.http.app.dependency_overrides[get_settings] = lambda: settings


def _stamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _timeline(instance_id: uuid.UUID, value: str) -> str:
    start = _stamp(BUCKET - timedelta(hours=1))
    end = _stamp(BUCKET + timedelta(hours=1))
    return (
        f"/api/v1/entities/timeline?instance_id={instance_id}&kind=USER&value={value}"
        f"&start={start}&end={end}"
    )


def _lane(body: dict, source: str = "IDENTITY_TOP") -> dict:
    return next(lane for lane in body["lanes"] if lane["source"] == source)


async def _seed_lane(session, *, truncated: bool, with_mention: bool) -> uuid.UUID:
    instance = await make_instance(session)
    source = await make_log_source(session, instance, qradar_id=1003, name="dc01")
    measurement = IdentityMeasurement.TRUNCATED.value if truncated else EXACT
    session.add(
        IdentityEventCount(
            log_source_id=source.id,
            event_id=4625,
            bucket_start=BUCKET,
            event_count=5,
            measurement=EXACT,
            names_measurement=measurement,
            admission_truncated=truncated,
        )
    )
    session.add(
        IdentityTopUsername(
            log_source_id=source.id,
            event_id=4625,
            bucket_start=BUCKET,
            rank=1,
            username="alice",
            truncated=False,
            event_count=5,
        )
    )
    session.add(
        IdentityTopUsername(
            log_source_id=source.id,
            event_id=4625,
            bucket_start=BUCKET,
            rank=2,
            username="mallory",
            truncated=False,
            event_count=4,
        )
    )
    if with_mention:
        session.add(
            EntityMention(
                instance_id=instance.id,
                kind="USER",
                value_norm="alice",
                role="USER",
                source="IDENTITY_TOP",
                ref_table="identity_top_username",
                ref_id=f"{source.id}:4625",
                occurred_at=BUCKET,
                occurred_end=BUCKET + WIDTH,
            )
        )
    await session.commit()
    return instance.id


def test_a_user_missing_from_stored_names_is_not_observed(db_schema) -> None:  # type: ignore[no-untyped-def]
    instance_id = run(lambda session: _seed_lane(session, truncated=False, with_mention=False))
    response = _Timeline(enabled=True).get(_timeline(instance_id, "carol"))
    assert response.status_code == 200, response.text
    lane = _lane(response.json())
    assert lane["state"] == "PARTIAL"
    assert lane["total"] is None
    assert lane["reason_code"] == "entity_timeline.reason.not_observed"
    assert response.json()["items"] == []


def test_the_lane_does_not_say_not_observed_when_the_family_is_off(db_schema) -> None:  # type: ignore[no-untyped-def]
    instance_id = run(lambda session: _seed_lane(session, truncated=False, with_mention=False))
    response = _Timeline(enabled=False).get(_timeline(instance_id, "carol"))
    assert response.status_code == 200, response.text
    lane = _lane(response.json())
    assert lane["state"] == "NOT_COLLECTED"
    assert lane["reason_code"] == "entity_timeline.reason.family_off"
    assert lane["total"] is None


def test_a_stored_mention_is_ok_and_its_title_does_not_list_other_names(db_schema) -> None:  # type: ignore[no-untyped-def]
    instance_id = run(lambda session: _seed_lane(session, truncated=False, with_mention=True))
    response = _Timeline(enabled=True).get(_timeline(instance_id, "alice"))
    assert response.status_code == 200, response.text
    body = response.json()
    lane = _lane(body)
    assert lane["state"] == "OK"
    assert lane["total"] == 1
    assert lane["reason_code"] is None
    assert len(body["items"]) == 1
    title = body["items"][0]["title"]
    assert title == "Windows event 4625 on dc01"
    assert "mallory" not in title and "alice" not in title
    assert body["items"][0]["href"].startswith("/behavior/identity/sources/")


def test_a_truncated_name_list_is_not_a_complete_absence(db_schema) -> None:  # type: ignore[no-untyped-def]
    instance_id = run(lambda session: _seed_lane(session, truncated=True, with_mention=True))
    response = _Timeline(enabled=True).get(_timeline(instance_id, "alice"))
    assert response.status_code == 200, response.text
    body = response.json()
    lane = _lane(body)
    assert lane["state"] == "PARTIAL"
    assert lane["total"] == 1
    assert lane["reason_code"] != "entity_timeline.reason.not_observed"
    assert "absent" not in (lane["reason_code"] or "")
    assert body["items"][0]["title"] == "Windows event 4625 on dc01"

    empty = run(lambda session: _seed_lane(session, truncated=True, with_mention=False))
    missed = _Timeline(enabled=True).get(_timeline(empty, "carol"))
    assert missed.status_code == 200, missed.text
    missed_lane = _lane(missed.json())
    assert missed_lane["state"] == "PARTIAL"
    assert missed_lane["total"] is None
    assert missed_lane["reason_code"] == "entity_timeline.reason.not_observed"


def test_0071_round_trip_keeps_identity_top_and_checks_clean() -> None:
    from alembic.util.exc import AutogenerateDiffsDetected

    from alembic import command

    sync_url = _sync_url()
    reset_public_schema(sync_url)
    config = _config()
    engine = create_engine(sync_url, future=True)
    instance_id = str(uuid.uuid4())
    try:
        command.upgrade(config, "0071")
        assert _version(engine) == "0071"
        _insert_instance(engine, instance_id)
        _insert_mention(engine, instance_id)
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM entity_mention WHERE source = 'IDENTITY_TOP'")
            ).scalar() == 1

        command.downgrade(config, "0070")
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT count(*) FROM entity_mention WHERE source = 'IDENTITY_TOP'")
            ).scalar() == 0
            assert connection.execute(
                text(
                    "SELECT count(*) FROM entity_mention_backfill WHERE source = 'IDENTITY_TOP'"
                )
            ).scalar() == 0
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO entity_mention (instance_id, kind, value_norm, role, source, "
                    "ref_table, ref_id, occurred_at) VALUES (CAST(:instance AS uuid), 'USER', "
                    "'alice', 'USER', 'IDENTITY_TOP', 'identity_top_username', 'ref', "
                    "TIMESTAMPTZ '2026-11-13 11:55:00+00')"
                ),
                {"instance": instance_id},
            )

        command.upgrade(config, "0071")
        assert _version(engine) == "0071"
        _insert_mention(engine, instance_id)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO entity_mention (instance_id, kind, value_norm, role, source, "
                    "ref_table, ref_id, occurred_at) VALUES (CAST(:instance AS uuid), 'USER', "
                    "'alice', 'USER', 'NOT_A_SOURCE', 'identity_top_username', 'other', "
                    "TIMESTAMPTZ '2026-11-13 11:55:00+00')"
                ),
                {"instance": instance_id},
            )
        command.upgrade(config, "head")
        try:
            command.check(config)
        except AutogenerateDiffsDetected as exc:  # pragma: no cover - failure path
            pytest.fail(f"schema drift detected between models and migrations:\n{exc}")
    finally:
        engine.dispose()


def _insert_instance(engine, instance_id: str) -> None:  # type: ignore[no-untyped-def]
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO qradar_instance (id, name, console_host, api_version, verify_ssl, "
                "provider_kind, mcp_enabled, enabled, status, consecutive_failures) "
                "VALUES (CAST(:instance AS uuid), 'lab', 'qradar.lab.local', '20.0', true, "
                "'REST', false, true, 'HEALTHY', 0)"
            ),
            {"instance": instance_id},
        )


def _insert_mention(engine, instance_id: str) -> None:  # type: ignore[no-untyped-def]
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entity_mention (instance_id, kind, value_norm, role, source, "
                "ref_table, ref_id, occurred_at) VALUES (CAST(:instance AS uuid), 'USER', "
                "'alice', 'USER', 'IDENTITY_TOP', 'identity_top_username', 'ref', "
                "TIMESTAMPTZ '2026-11-13 11:55:00+00')"
            ),
            {"instance": instance_id},
        )
        connection.execute(
            text(
                "INSERT INTO entity_mention_backfill (instance_id, source, status, rows_written) "
                "VALUES (CAST(:instance AS uuid), 'IDENTITY_TOP', 'COMPLETE', 1)"
            ),
            {"instance": instance_id},
        )
