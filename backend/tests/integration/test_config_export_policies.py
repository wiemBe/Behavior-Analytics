"""`python -m app.cli.config export`: policies, notifications, mail and LDAP.

The command runs as a subprocess, as the wrapper runs it. References are local names, a webhook's
HMAC secret and headers never enter the document, only open, unexpired suppressions reach the file,
and LDAP starts from the singleton row `alembic upgrade head` seeds (id 1).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config_transfer.document import SECTION_ORDER, secret_key_paths
from app.models.alert_policy import AlertPolicy
from app.models.alert_suppression import AlertSuppression
from app.models.email import EmailTemplate
from app.models.fleet import LogSourceGroup
from app.models.identity import LdapConfig
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.notification_destination import NotificationDestination
from app.models.notification_rule import NotificationRule
from app.models.property_watch import PropertyWatch

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[2]
NEW = ("alert_policies", "alert_suppressions", "notification_destinations",
       "notification_rules", "email_templates", "ldap_config")
WAF = {"name": "Edge WAF", "type_name": "F5 Networks BIG-IP ASM"}
HMAC, HEADER, BIND = "HMAC-SECRET-SENTINEL-9f3a", "HEADER-VALUE-SENTINEL-7c1b", "BIND-PASSWORD"
STAMP = "%Y-%m-%dT%H:%M:%SZ"


def _run(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    parsed = make_url(url)
    env = dict(os.environ) | {
        "POSTGRES_USER": parsed.username or "", "POSTGRES_PASSWORD": parsed.password or "",
        "POSTGRES_DB": parsed.database or "", "POSTGRES_HOST": parsed.host or "localhost",
        "POSTGRES_PORT": str(parsed.port or 5432), "PYTHONPATH": str(BACKEND)}
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "app.cli.config", *args], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=120, check=False)


async def _console(session: AsyncSession) -> QRadarInstance:
    console = QRadarInstance(name="preprod", console_host="https://qradar.lab.internal",
                             sec_token="S3CRET-TOKEN-h4rdc0ded")
    session.add(console)
    await session.flush()
    return console


async def _source(session: AsyncSession, console: QRadarInstance) -> LogSource:
    source = LogSource(instance_id=console.id, qradar_id=1, **WAF)
    session.add(source)
    await session.flush()
    return source


def _section(document: str, name: str) -> list[dict[str, object]]:
    return json.loads(document)["sections"][name]


def _suppression(now: datetime, **over: object) -> AlertSuppression:
    base: dict[str, object] = dict(
        family="waf", detector_type="VOLUME_SPIKE", kind="KNOWN_CHANGE",
        reason="change window CHG-100", owner="soc", created_by="old-operator",
        reference="CHG-100", starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1))
    return AlertSuppression(**(base | over))


async def test_alert_policies_export_scope_refs(
    db_schema: str, db_session: AsyncSession
) -> None:
    console = await _console(db_session)
    source = await _source(db_session, console)
    watch = PropertyWatch(instance_id=console.id, name="Denied actions", scope_kind="LOG_SOURCE",
                          log_source_id=source.id, detectors=["PW_NEW_VALUE"],
                          properties=[{"kind": "CUSTOM_PROPERTY", "name": "Action"}],
                          pinned_values=[])
    db_session.add(watch)
    await db_session.flush()
    db_session.add_all([
        AlertPolicy(instance_id=None, scope_kind="SOURCE", family="waf",
                    scope_value=str(source.id), mode="RECORD_ONLY"),
        AlertPolicy(instance_id=None, scope_kind="CLASS", family="log_source",
                    scope_value="WINDOWS", mode="DIGEST", detector_type="VOLUME_SPIKE"),
        AlertPolicy(instance_id=console.id, scope_kind="SOURCE", family="property_watch",
                    scope_value=str(watch.id), mode="ALERT", detector_type="PW_NEW_VALUE"),
    ])
    await db_session.commit()
    done = _run(db_schema, "export")
    assert (done.returncode, done.stderr) == (0, "")
    assert _section(done.stdout, "alert_policies") == [
        {"scope": "global", "scope_kind": "CLASS", "family": "log_source", "log_source": None,
         "source_class": "WINDOWS", "watch": None,
         "detector_type": "VOLUME_SPIKE", "mode": "DIGEST"},
        {"scope": "global", "scope_kind": "SOURCE", "family": "waf", "log_source": WAF,
         "source_class": None, "watch": None, "detector_type": None, "mode": "RECORD_ONLY"},
        {"scope": "instance", "scope_kind": "SOURCE", "family": "property_watch",
         "log_source": None, "source_class": None, "watch": {"name": "Denied actions"},
         "detector_type": "PW_NEW_VALUE", "mode": "ALERT"},
    ]


async def test_destination_drops_secrets_and_rule_refs_by_name(
    db_schema: str, db_session: AsyncSession
) -> None:
    console = await _console(db_session)
    destination = NotificationDestination(
        name="soc-hook", enabled=True, url="https://hooks.example.internal/soc",
        allow_private=True, hmac_secret=HMAC, headers=f'{{"X-Api-Key": "{HEADER}"}}',
        created_by="old-operator", updated_by="old-operator",
    )
    db_session.add(destination)
    await db_session.flush()
    synced = datetime.now(UTC)
    db_session.add_all([
        LogSourceGroup(instance_id=console.id, qradar_id=100, name="DMZ", assignable=True,
                       synced_at=synced),
        LogSourceGroup(instance_id=console.id, qradar_id=200, name="Web", parent_qradar_id=100,
                       assignable=True, synced_at=synced),
        NotificationRule(name="waf-high", enabled=True, priority=100, min_severity="HIGH",
                         families=["waf"], source_classes=["WAF"], group_ids=[200],
                         destination_ids=[destination.id], stop_default=False,
                         created_by="old-operator", updated_by="old-operator"),
    ])
    await db_session.commit()
    done = _run(db_schema, "export")
    assert (done.returncode, done.stderr) == (0, "")
    assert _section(done.stdout, "notification_destinations") == [{
        "name": "soc-hook", "enabled_on_source": True,
        "url": "https://hooks.example.internal/soc", "allow_http": False,
        "allow_private": True, "secrets_omitted": ["hmac_secret", "headers"]}]
    assert _section(done.stdout, "notification_rules") == [{
        "name": "waf-high", "enabled": True, "priority": 100, "min_severity": "HIGH",
        "families": ["waf"], "owners": None, "source_classes": ["WAF"],
        "groups": [{"name": "Web", "parent_name": "DMZ"}],
        "window_start_minute": None, "window_end_minute": None,
        "destinations": [{"name": "soc-hook"}], "stop_default": False}]
    assert secret_key_paths(json.loads(done.stdout)) == []
    assert HMAC not in done.stdout and HEADER not in done.stdout
    assert "old-operator" not in done.stdout

    again = _run(db_schema, "export")
    one, two = json.loads(done.stdout), json.loads(again.stdout)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", one.pop("exported_at"))
    two.pop("exported_at")
    assert one == two
    pairs = zip(done.stdout.splitlines(), again.stdout.splitlines(), strict=True)
    assert all(left == right for index, (left, right) in enumerate(pairs) if index != 3)


async def test_only_open_suppressions_are_exported(
    db_schema: str, db_session: AsyncSession
) -> None:
    console = await _console(db_session)
    source = await _source(db_session, console)
    now = datetime.now(UTC)
    db_session.add_all([
        _suppression(now, subject_id=source.id),
        _suppression(now, subject_id=source.id, detector_type="NEW_ENDPOINT",
                     starts_at=now - timedelta(days=2), ends_at=now - timedelta(days=1)),
        _suppression(now, subject_id=source.id, detector_type="RATE_ANOMALY",
                     ended_at=now - timedelta(hours=1)),
        _suppression(now, detector_type="PW_NEW_VALUE", family="property_watch",
                     subject_id=source.id),
    ])
    await db_session.commit()
    done = _run(db_schema, "export")
    assert (done.returncode, done.stderr) == (0, "")
    rows = _section(done.stdout, "alert_suppressions")
    assert len(rows) == 2
    assert rows[0]["family"] == "property_watch" and rows[0]["watch"] == {"name": None}
    assert rows[1]["detector_type"] == "VOLUME_SPIKE"
    assert rows[1]["log_source"] == WAF and rows[1]["watch"] is None and rows[1]["rule"] is None
    assert rows[1]["starts_at"] == (now - timedelta(hours=1)).strftime(STAMP)
    assert rows[1]["ends_at"] == (now + timedelta(hours=1)).strftime(STAMP)
    assert "RATE_ANOMALY" not in done.stdout and "ended_at" not in done.stdout


async def test_email_and_ldap_sections(db_schema: str, db_session: AsyncSession) -> None:
    await _console(db_session)
    await db_session.commit()
    empty = _run(db_schema, "export")
    assert (empty.returncode, empty.stderr) == (0, "")
    document = json.loads(empty.stdout)
    assert list(document["sections"]) == list(SECTION_ORDER)
    assert [document["sections"][name] for name in NEW] == [[] for _ in NEW]

    db_session.add_all([
        EmailTemplate(alert_type=None, subject="[site] severity", html_body="<p>title</p>",
                      text_body=None, enabled=True, updated_by="old-operator"),
        LdapConfig(id=1, enabled=False, urls=[], start_tls=False, bind_template="{username}",
                   timeout_seconds=5, version=1, updated_at=datetime.now(UTC)),
    ])
    await db_session.flush()
    await db_session.execute(
        LdapConfig.__table__.update().where(LdapConfig.id == 1).values(
            enabled=True, urls=["ldaps://directory.example.internal"],
            bind_template="uid={username},ou=people,dc=example,dc=internal",
            user_search_base="ou=people,dc=example,dc=internal", version=7, updated_by=BIND,
        )
    )
    await db_session.commit()
    done = _run(db_schema, "export")
    assert (done.returncode, done.stderr) == (0, "")
    assert _section(done.stdout, "email_templates") == [{
        "alert_type": None, "subject": "[site] severity", "html_body": "<p>title</p>",
        "text_body": None, "enabled": True}]
    assert _section(done.stdout, "ldap_config") == [{
        "enabled": True, "urls": ["ldaps://directory.example.internal"], "start_tls": False,
        "ca_pem": None, "bind_template": "uid={username},ou=people,dc=example,dc=internal",
        "timeout_seconds": 5, "user_search_base": "ou=people,dc=example,dc=internal"}]
    assert BIND not in done.stdout and '"version"' not in done.stdout
    assert secret_key_paths(json.loads(done.stdout)) == []
