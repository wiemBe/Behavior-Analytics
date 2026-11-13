"""Migration 0070: identity incidents, the identity family, and a clean round trip.

Runs the revision up, down and up again (``upgrade 0070``, ``downgrade 0069``,
``upgrade 0070``) without ``alembic.ini`` so process-wide logging stays
untouched. The version asserted is 0070, not head. ``alembic check`` runs only
after ``upgrade head``. A downgrade refuses an identity row in a shared table
and an ``identity_source`` template. Rows in the new incident tables do not
block the drop.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import Criticality
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from tests.integration.schema_reset import reset_public_schema
from tests.integration.test_migrations import _alembic_config, _sync_url, _table_exists

pytestmark = pytest.mark.integration

_NEW_TABLES = ("identity_anomaly", "identity_anomaly_transition", "identity_detector_state")
_FAMILY_CHECKS = (
    "ck_anomaly_triage_family_known",
    "ck_alert_policy_family_known",
    "ck_alert_suppression_family_known",
    "ck_alert_suppression_hit_family_known",
    "ck_baseline_training_exclusion_family_known",
)
_NOW = datetime(2026, 11, 13, tzinfo=UTC)

_TRIAGE = (
    "INSERT INTO anomaly_triage (id, family, anomaly_id, status, updated_by, version) "
    "VALUES (gen_random_uuid(), '{family}', gen_random_uuid(), 'NEW', 'seed', 1)"
)
_POLICY = (
    "INSERT INTO alert_policy (id, scope_kind, scope_value, family, detector_type, mode) "
    "VALUES (gen_random_uuid(), 'SOURCE', 'src', '{family}', NULL, 'ALERT')"
)
_SUPPRESSION = (
    "INSERT INTO alert_suppression (id, family, subject_id, detector_type, kind, reason, "
    "owner, starts_at, ends_at, reference, created_by) VALUES "
    "(gen_random_uuid(), '{family}', gen_random_uuid(), NULL, 'ANALYST', 'note', "
    "'analyst', '2026-11-01 00:00+00', '2026-11-08 00:00+00', NULL, 'analyst')"
)
_TEMPLATE = (
    "INSERT INTO email_template (id, alert_type, subject, html_body, enabled) "
    "VALUES (gen_random_uuid(), '{kind}', 's', '<p>x</p>', true)"
)
_EXCLUSION = (
    "INSERT INTO baseline_training_exclusion (id, origin_family, origin_anomaly_id, "
    "subject_kind, subject_id, scope_key, starts_at, ends_at, reason, created_by) "
    "VALUES (gen_random_uuid(), 'identity', gen_random_uuid(), 'log_source', "
    "gen_random_uuid(), 'identity:4625', '2026-11-01 00:00+00', '2026-11-02 00:00+00', "
    "'window', 'analyst')"
)


@pytest.fixture
def clean_db() -> str:
    sync_url = _sync_url()
    reset_public_schema(sync_url)
    return sync_url


def _version(engine) -> str:  # type: ignore[no-untyped-def]
    with engine.connect() as conn:
        return str(conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one())


def _check_sql(engine, name: str) -> str:
    with engine.connect() as conn:
        found = conn.execute(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name"),
            {"name": name},
        ).scalar_one()
    return str(found)


def _clear(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM anomaly_triage WHERE family = 'identity'"))
        conn.execute(text("DELETE FROM alert_policy WHERE family = 'identity'"))
        conn.execute(text("DELETE FROM alert_suppression_hit WHERE family = 'identity'"))
        conn.execute(text("DELETE FROM alert_suppression WHERE family = 'identity'"))
        conn.execute(
            text(
                "DELETE FROM baseline_training_exclusion WHERE origin_family = 'identity'"
            )
        )
        conn.execute(text("DELETE FROM email_template WHERE alert_type = 'identity_source'"))


def _seed(engine, blocker: str) -> None:
    with engine.begin() as conn:
        if blocker == "triage":
            conn.execute(text(_TRIAGE.format(family="identity")))
        elif blocker == "policy":
            conn.execute(text(_POLICY.format(family="identity")))
        elif blocker == "suppression":
            conn.execute(text(_SUPPRESSION.format(family="identity")))
        elif blocker == "suppression_hit":
            conn.execute(text(_SUPPRESSION.format(family="log_source")))
            conn.execute(
                text(
                    "INSERT INTO alert_suppression_hit (id, suppression_id, family, "
                    "anomaly_id, detector_type, bucket_start, occurred_at) "
                    "SELECT gen_random_uuid(), id, 'identity', gen_random_uuid(), "
                    "'IDENTITY_ACCOUNT_DELETED', '2026-11-02 00:00+00', "
                    "'2026-11-02 00:01+00' FROM alert_suppression"
                )
            )
        elif blocker == "exclusion":
            conn.execute(text(_EXCLUSION))
        else:
            conn.execute(text(_TEMPLATE.format(kind="identity_source")))


def test_0070_round_trip_widens_the_checks_and_leaves_no_drift(clean_db: str) -> None:
    from alembic.util.exc import AutogenerateDiffsDetected

    from alembic import command

    cfg = _alembic_config(clean_db)
    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(cfg, "0069")
        for table in _NEW_TABLES:
            assert not _table_exists(engine, table), table
        assert "'identity'" not in _check_sql(engine, "ck_anomaly_triage_family_known")

        command.upgrade(cfg, "0070")
        assert _version(engine) == "0070"
        for table in _NEW_TABLES:
            assert _table_exists(engine, table), table
        for name in _FAMILY_CHECKS:
            body = _check_sql(engine, name)
            assert "'identity'" in body, name
        template = _check_sql(engine, "ck_email_template_alert_type_known")
        assert "'identity_source'" in template
        assert template.index("'identity_source'") < template.index("'digest'")

        with engine.begin() as conn:
            conn.execute(text(_TRIAGE.format(family="identity")))
            conn.execute(text(_POLICY.format(family="identity")))
            conn.execute(text(_SUPPRESSION.format(family="identity")))
            conn.execute(text(_TEMPLATE.format(kind="identity_source")))
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(_TRIAGE.format(family="not_a_family")))
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(_TEMPLATE.format(kind="not_a_type")))

        _clear(engine)
        _seed_incident(engine)
        command.downgrade(cfg, "0069")
        for table in _NEW_TABLES:
            assert not _table_exists(engine, table), table
        assert "'identity'" not in _check_sql(engine, "ck_alert_policy_family_known")
        assert "'identity_source'" not in _check_sql(
            engine, "ck_email_template_alert_type_known"
        )
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(_TRIAGE.format(family="identity")))

        command.upgrade(cfg, "0070")
        assert _version(engine) == "0070"
        command.upgrade(cfg, "head")
        try:
            command.check(cfg)
        except AutogenerateDiffsDetected as exc:  # pragma: no cover - failure path
            pytest.fail(f"schema drift detected after the round trip:\n{exc}")
    finally:
        engine.dispose()


def _seed_incident(engine) -> None:
    with Session(engine) as session:
        instance = QRadarInstance(
            name="lab",
            console_host="lab.local",
            api_version="20.0",
            provider_kind="REST",
            mcp_enabled=False,
            enabled=True,
        )
        session.add(instance)
        session.flush()
        source = LogSource(
            instance_id=instance.id,
            qradar_id=1001,
            name="dc",
            criticality=Criticality.MEDIUM,
        )
        session.add(source)
        session.flush()
        session.execute(
            text(
                "INSERT INTO identity_anomaly (id, log_source_id, event_id, detector_type, "
                "fingerprint, severity, state, detected_at, consecutive_buckets, "
                "policy_version, profile_version, catalog_version, details, "
                "evidence_status, suppressed, observed_measurement) VALUES "
                "(:id, :source, 4726, 'IDENTITY_ACCOUNT_DELETED', 'fp', 'HIGH', "
                "'RESOLVED', :at, 1, 1, 1, 1, '{}'::jsonb, 'COMPLETE', false, 'EXACT')"
            ),
            {"id": uuid.uuid4(), "source": source.id, "at": _NOW},
        )
        session.commit()


def test_0070_rejects_an_unknown_detector(clean_db: str) -> None:
    from alembic import command

    cfg = _alembic_config(clean_db)
    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(cfg, "0070")
        assert _version(engine) == "0070"
        _seed_incident(engine)
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE identity_anomaly SET detector_type = 'NOT_A_DETECTOR'"
                )
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "blocker",
    ["triage", "policy", "suppression", "suppression_hit", "exclusion", "template"],
)
def test_0070_downgrade_refuses_while_an_identity_record_exists(
    clean_db: str, blocker: str
) -> None:
    from alembic import command

    cfg = _alembic_config(clean_db)
    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(cfg, "0070")
        _seed(engine, blocker)
        with pytest.raises(RuntimeError, match="cannot downgrade 0070"):
            command.downgrade(cfg, "0069")
        assert _version(engine) == "0070"
        for table in _NEW_TABLES:
            assert _table_exists(engine, table), table
        _clear(engine)
        command.downgrade(cfg, "0069")
        assert _version(engine) == "0069"
    finally:
        engine.dispose()
