"""`python -m app.cli.config export`: outage policies, mail recipients and unresolved refs.

Slice 1d2 (the CFG-1d follow-up): the two sections 1d left out, and the audit rule that a
reference which no longer resolves on this console is recorded on its row as a `warnings`
entry instead of passing as a silent null name. The command runs as a subprocess, as the
wrapper runs it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config_transfer.document import SECTION_ORDER, parse, secret_key_paths
from app.models.alert_policy import AlertPolicy
from app.models.alert_suppression import AlertSuppression
from app.models.email import EmailRecipient
from app.models.fleet import LogSourceGroup
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.notification_destination import NotificationDestination
from app.models.notification_rule import NotificationRule
from app.models.outage import OutageAlertPolicy
from app.models.property_watch import PropertyWatch
from app.models.rule import AnalyticsRule

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[2]
NEW = ("outage_alert_policies", "email_recipients")
WAF = {"name": "Edge WAF", "type_name": "F5 Networks BIG-IP ASM"}


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
        family="rule", detector_type=None, kind="KNOWN_CHANGE",
        reason="change window CHG-100", owner="soc", created_by="old-operator",
        reference="CHG-100", starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=1))
    return AlertSuppression(**(base | over))


async def test_outage_policies_and_recipients_export(
    db_schema: str, db_session: AsyncSession
) -> None:
    console = await _console(db_session)
    await db_session.commit()
    empty = _run(db_schema, "export")
    assert (empty.returncode, empty.stderr) == (0, "")
    document = json.loads(empty.stdout)
    assert list(document["sections"]) == list(SECTION_ORDER)
    assert [document["sections"][name] for name in NEW] == [[] for _ in NEW]

    source = await _source(db_session, console)
    db_session.add_all([
        OutageAlertPolicy(instance_id=None, scope_kind="CLASS", scope_value="WAF",
                          mode="ALERT", silence_grace_buckets=None,
                          updated_by="old-operator"),
        OutageAlertPolicy(instance_id=console.id, scope_kind="SOURCE",
                          scope_value=str(source.id), mode="RECORD_ONLY",
                          silence_grace_buckets=5, updated_by="old-operator"),
        EmailRecipient(address="soc@example.internal", min_severity="HIGH",
                       alert_types=["waf"], enabled=True, updated_by="old-operator"),
    ])
    await db_session.commit()
    done = _run(db_schema, "export")
    assert (done.returncode, done.stderr) == (0, "")
    assert _section(done.stdout, "outage_alert_policies") == [
        {"scope": "global", "scope_kind": "CLASS", "source_class": "WAF",
         "log_source": None, "mode": "ALERT", "silence_grace_buckets": None},
        {"scope": "instance", "scope_kind": "SOURCE", "source_class": None,
         "log_source": WAF, "mode": "RECORD_ONLY", "silence_grace_buckets": 5},
    ]
    assert _section(done.stdout, "email_recipients") == [
        {"address": "soc@example.internal", "min_severity": "HIGH",
         "alert_types": ["waf"], "enabled": True}]
    assert "old-operator" not in done.stdout
    assert secret_key_paths(json.loads(done.stdout)) == []

    again = _run(db_schema, "export")
    assert (again.returncode, again.stderr) == (0, "")
    one, two = json.loads(done.stdout), json.loads(again.stdout)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", one.pop("exported_at"))
    two.pop("exported_at")
    assert one == two
    pairs = zip(done.stdout.splitlines(), again.stdout.splitlines(), strict=True)
    assert all(left == right for index, (left, right) in enumerate(pairs) if index != 3)


async def test_suppression_refs_resolve_to_real_names(
    db_schema: str, db_session: AsyncSession
) -> None:
    console = await _console(db_session)
    source = await _source(db_session, console)
    watch = PropertyWatch(instance_id=console.id, name="Denied actions",
                          scope_kind="LOG_SOURCE", log_source_id=source.id,
                          detectors=["PW_NEW_VALUE"],
                          properties=[{"kind": "CUSTOM_PROPERTY", "name": "Action"}],
                          pinned_values=[])
    rule = AnalyticsRule(instance_id=console.id, qradar_id=7,
                         name="Excessive Firewall Denies", rule_type="EVENT",
                         is_building_block=False)
    db_session.add_all([watch, rule])
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add_all([
        _suppression(now, family="property_watch", subject_id=watch.id,
                     detector_type="PW_NEW_VALUE"),
        _suppression(now, family="rule", subject_id=rule.id),
    ])
    await db_session.commit()
    done = _run(db_schema, "export")
    assert (done.returncode, done.stderr) == (0, "")
    rows = _section(done.stdout, "alert_suppressions")
    assert len(rows) == 2
    assert rows[0]["family"] == "property_watch"
    assert rows[0]["watch"] == {"name": "Denied actions"}
    assert rows[0]["rule"] is None and rows[0]["log_source"] is None
    assert rows[1]["family"] == "rule"
    assert rows[1]["rule"] == {"name": "Excessive Firewall Denies", "rule_type": "EVENT",
                               "is_building_block": False}
    assert rows[1]["watch"] is None and rows[1]["log_source"] is None
    assert "warnings" not in done.stdout
    assert "old-operator" not in done.stdout


async def test_unresolved_refs_are_recorded_with_warnings(
    db_schema: str, db_session: AsyncSession
) -> None:
    console = await _console(db_session)
    await _source(db_session, console)
    destination = NotificationDestination(
        name="soc-hook", enabled=True, url="https://hooks.example.internal/soc",
        headers="{}", created_by="old-operator", updated_by="old-operator")
    orphan = LogSourceGroup(instance_id=console.id, qradar_id=500, name="Web",
                            parent_qradar_id=999, assignable=True,
                            synced_at=datetime.now(UTC))
    db_session.add_all([destination, orphan])
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add_all([
        AlertPolicy(instance_id=None, scope_kind="SOURCE", family="waf",
                    scope_value=str(uuid.uuid4()), mode="ALERT"),
        AlertPolicy(instance_id=console.id, scope_kind="SOURCE", family="property_watch",
                    scope_value=str(uuid.uuid4()), mode="ALERT",
                    detector_type="PW_NEW_VALUE"),
        _suppression(now, subject_id=uuid.uuid4()),
        OutageAlertPolicy(instance_id=console.id, scope_kind="SOURCE",
                          scope_value=str(uuid.uuid4()), mode="ALERT"),
        NotificationRule(name="orphan-dest", enabled=True, priority=100,
                         min_severity="HIGH", families=["waf"], group_ids=[424242],
                         destination_ids=[uuid.uuid4()], stop_default=False,
                         created_by="old-operator", updated_by="old-operator"),
        NotificationRule(name="orphan-parent", enabled=True, priority=100,
                         min_severity="HIGH", families=["waf"], group_ids=[500],
                         destination_ids=[destination.id], stop_default=False,
                         created_by="old-operator", updated_by="old-operator"),
    ])
    await db_session.commit()
    done = _run(db_schema, "export")
    assert (done.returncode, done.stderr) == (0, "")

    policies = _section(done.stdout, "alert_policies")
    assert len(policies) == 2
    dangling_source = next(row for row in policies if row["family"] == "waf")
    assert dangling_source["log_source"] is None
    assert dangling_source["warnings"] == ["log source ref does not resolve"]
    dangling_watch = next(row for row in policies if row["family"] == "property_watch")
    assert dangling_watch["watch"] == {"name": None}
    assert dangling_watch["warnings"] == ["watch ref does not resolve"]

    (suppression,) = _section(done.stdout, "alert_suppressions")
    assert suppression["rule"] == {"name": None}
    assert suppression["warnings"] == ["rule ref does not resolve"]

    (outage,) = _section(done.stdout, "outage_alert_policies")
    assert outage["log_source"] is None
    assert outage["warnings"] == ["log source ref does not resolve"]

    rules = {row["name"]: row for row in _section(done.stdout, "notification_rules")}
    orphan_dest = rules["orphan-dest"]
    assert orphan_dest["destinations"] == [{"name": None}]
    assert orphan_dest["groups"] == [{"name": None, "parent_name": None}]
    assert orphan_dest["warnings"] == ["destination ref does not resolve",
                                       "group ref does not resolve"]
    orphan_parent = rules["orphan-parent"]
    assert orphan_parent["destinations"] == [{"name": "soc-hook"}]
    assert orphan_parent["groups"] == [{"name": "Web", "parent_name": None}]
    assert orphan_parent["warnings"] == ["group parent ref does not resolve"]
    assert "old-operator" not in done.stdout
    assert secret_key_paths(json.loads(done.stdout)) == []
    # The rows carry a key the design's example does not spell; the document must still be
    # one the shared reader accepts, or import could never read an export that warned.
    assert list(parse(done.stdout)["sections"]) == list(SECTION_ORDER)
