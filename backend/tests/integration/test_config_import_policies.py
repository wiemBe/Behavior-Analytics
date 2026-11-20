"""Real import commands for the policies, notifications, mail and LDAP sections (CFG-1h).

Every case runs ``python -m app.cli.config import`` as a subprocess with a sitecustomize tripwire
for the provider, encrypted-column reads and the webhook transport. Created destinations are
disabled and secret-free; references, validators and the migration seed decide every outcome
without a provider call or an HTTP request.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config_transfer.document import SECTION_ORDER, build_document, format_timestamp
from app.models.alert_policy import AlertPolicy
from app.models.alert_suppression import AlertSuppression
from app.models.email import EmailRecipient, EmailTemplate
from app.models.fleet import LogSourceGroup
from app.models.identity import LdapConfig
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.notification_destination import NotificationDestination
from app.models.notification_rule import NotificationRule
from app.models.outage import OutageAlertPolicy
from app.models.property_watch import PropertyWatch
from app.models.rule import AnalyticsRule

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
WAF = {"name": "Edge WAF", "type_name": "F5 Networks BIG-IP ASM"}
GROUP = {"name": "DMZ", "parent_name": None}
RULE = {"name": "Excessive Firewall Denies", "rule_type": "EVENT", "is_building_block": False}
URL = "https://93.184.216.34/soc"
HOST_URL = "https://soc.example.invalid/soc"
HOOK = '''
import os
from app.providers import factory
from app.models.base import EncryptedString
from app.models.notification_destination import NotificationDestination
from app.alerts.notifiers import destination_sender, url_guard
import httpx
def fail(*args, **kwargs):
    raise AssertionError("provider, secret or webhook transport was used")
factory.build_provider = factory.build_provider_for_instance = fail
# Block every encrypted read, then allow only the URL (export writes it as plaintext).
_decrypt = EncryptedString.process_result_value
EncryptedString.process_result_value = fail
class ConfigDecrypt(EncryptedString):
    def process_result_value(self, value, dialect):
        return _decrypt(self, value, dialect)
NotificationDestination.__table__.c.url.type = ConfigDecrypt(4096)
# The guard's default resolver is patched in-process: a working one returns a fixed
# public address, CFG1H_DNS_FAIL makes it raise so the resolver path is exercised.
def resolver(host, port):
    if os.environ.get("CFG1H_DNS_FAIL"):
        raise OSError("DNS unavailable in test")
    return ("93.184.216.34",)
url_guard.guard_url.__kwdefaults__["resolver"] = resolver
destination_sender.http_transport = fail
httpx.AsyncHTTPTransport.handle_async_request = fail
'''


def _document(**sections: list[dict[str, Any]]) -> dict[str, Any]:
    return build_document(product_version="test", exported_at=datetime.now(UTC),
                          source_label="preprod", sections=sections)


def _cli(url: str, tmp_path: Path, command: str, *args: str,
         document: dict[str, Any] | None = None,
         dns_fail: bool = False) -> subprocess.CompletedProcess[str]:
    parsed = make_url(url)
    (tmp_path / "sitecustomize.py").write_text(HOOK)
    env = dict(os.environ) | {
        "POSTGRES_USER": parsed.username or "", "POSTGRES_PASSWORD": parsed.password or "",
        "POSTGRES_DB": parsed.database or "", "POSTGRES_HOST": parsed.host or "localhost",
        "POSTGRES_PORT": str(parsed.port or 5432),
        "PYTHONPATH": os.pathsep.join((str(tmp_path), str(BACKEND))),
    }
    if dns_fail:
        env["CFG1H_DNS_FAIL"] = "1"
    else:
        env.pop("CFG1H_DNS_FAIL", None)
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "app.cli.config", command, *args], cwd=BACKEND, env=env,
        input=json.dumps(document) if document is not None else None,
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert "Error in sitecustomize" not in done.stderr, done.stderr
    return done


def _import(url: str, tmp_path: Path, document: dict[str, Any], mode: str = "--apply",
            *, dns_fail: bool = False) -> Any:
    args = (mode, "--yes", "--actor", "soc") if mode == "--apply" else (mode,)
    done = _cli(url, tmp_path, "import", *args, document=document, dns_fail=dns_fail)
    assert done.returncode == 0, done.stderr
    assert "Traceback" not in done.stderr
    report = json.loads(done.stdout)
    assert list(report["sections"]) == list(SECTION_ORDER)
    return report


def _lines(report: Any, section: str, outcome: str) -> list[dict[str, str]]:
    return report["sections"][section][outcome]


SOC = {"log_source": WAF, "criticality": "MEDIUM", "owner": "soc", "owner_email": None,
       "monitoring_enabled": True, "expected_interval_seconds": None,
       "business_hours_only": False, "business_hours_start": 8, "business_hours_end": 18,
       "business_days": [1, 2, 3, 4, 5], "timezone_name": "UTC", "custom_thresholds": {}}
DEST = {"name": "soc-hook", "enabled_on_source": True, "url": URL, "allow_http": False,
        "allow_private": False, "secrets_omitted": ["hmac_secret", "headers"]}
RULE_ROW = {"name": "waf-high", "enabled": True, "priority": 100, "min_severity": "HIGH",
            "families": ["waf"], "owners": None, "source_classes": ["WAF"], "groups": [GROUP],
            "window_start_minute": None, "window_end_minute": None,
            "destinations": [{"name": "soc-hook"}], "stop_default": False}
_now = datetime.now(UTC)
SUP = {"family": "log_source", "log_source": WAF, "watch": None, "rule": None,
       "detector_type": "VOLUME_SPIKE", "kind": "KNOWN_CHANGE", "reason": "change window CHG-100",
       "owner": "soc", "starts_at": format_timestamp(_now - timedelta(hours=1)),
       "ends_at": format_timestamp(_now + timedelta(hours=23)), "reference": "CHG-100"}
TEMPLATE = {"alert_type": None, "subject": "[{{ site.name }}] {{ alert.severity }}",
            "html_body": "<p>{{ alert.title }}</p>", "text_body": None, "enabled": True}
RECIPIENT = {"address": "SOC@Example.Internal", "min_severity": "LOW",
             "alert_types": ["waf_endpoint", "log_source"], "enabled": True}
OUTAGE = {"scope": "global", "scope_kind": "CLASS", "source_class": "WAF", "log_source": None,
          "mode": "ALERT", "silence_grace_buckets": None}
POLICY = {"scope": "global", "scope_kind": "CLASS", "family": "waf", "log_source": None,
          "source_class": "WAF", "watch": None, "detector_type": None, "mode": "RECORD_ONLY"}
LDAP = {"enabled": True, "urls": ["ldaps://directory.example.internal"], "start_tls": False,
        "ca_pem": None, "bind_template": "uid={username},ou=people,dc=example,dc=internal",
        "timeout_seconds": 5, "user_search_base": None}


async def _fleet(session: AsyncSession, *, ldap: bool = False,
                 no_group: bool = False, no_watch: bool = False) -> dict[str, Any]:
    console = QRadarInstance(name="prod", console_host="https://lab.invalid", sec_token="secret")
    session.add(console)
    await session.flush()
    source = LogSource(instance_id=console.id, qradar_id=10, name=WAF["name"],
                       type_name=WAF["type_name"])
    session.add(source)
    await session.flush()  # the Python-side UUID default is assigned on flush
    result: dict[str, Any] = {"console": console, "source": source}
    if not no_group:
        group = LogSourceGroup(instance_id=console.id, qradar_id=7, name="DMZ",
                               parent_qradar_id=None, assignable=True, synced_at=datetime.now(UTC))
        session.add(group)
    rule = AnalyticsRule(instance_id=console.id, qradar_id=11, name=RULE["name"],
                         rule_type=RULE["rule_type"], is_building_block=False)
    session.add(rule)
    if not no_watch:
        watch = PropertyWatch(instance_id=console.id, name="Denied actions",
                              scope_kind="LOG_SOURCE", log_source_id=source.id,
                              properties=[{"kind": "CUSTOM_PROPERTY", "name": "Action"}],
                              detectors=["PW_NEW_VALUE"])
        session.add(watch)
        result["watch"] = watch
    if ldap:
        result["ldap"] = LdapConfig(
            id=1, enabled=False, urls=[], start_tls=False, ca_pem=None,
            bind_template="{username}", timeout_seconds=5, user_search_base=None,
            version=1, updated_by=None, updated_at=datetime.now(UTC))
        session.add(result["ldap"])
    await session.commit()
    return result


async def _count(session: AsyncSession, *models: Any) -> list[int]:
    return [await session.scalar(select(func.count()).select_from(model)) or 0 for model in models]


async def test_destinations_are_created_disabled_and_secret_free(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(
        notification_destinations=[DEST], log_source_settings=[SOC]))
    assert _lines(report, "notification_destinations", "created") == [{
        "key": "soc-hook", "reason": "applied configuration"}]
    row = await db_session.scalar(select(NotificationDestination))
    assert (row.name, row.url, row.allow_http, row.allow_private) == (
        "soc-hook", URL, False, False)
    assert row.enabled is False and row.hmac_secret is None and row.headers == "{}"
    assert (row.created_by, row.updated_by) == ("soc", "soc")
    assert row.last_test_at is None and row.last_test_status is None
    assert len(_lines(report, "log_source_settings", "created")) == 1
    again = _import(db_schema, tmp_path, _document(notification_destinations=[DEST]))
    assert len(_lines(again, "notification_destinations", "unchanged")) == 1
    assert await _count(db_session, NotificationDestination) == [1]


async def test_guard_failure_is_unresolved_and_sends_nothing(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    bad = {**DEST, "name": "bad-hook", "url": "https://127.0.0.1/soc"}
    report = _import(db_schema, tmp_path, _document(
        notification_destinations=[bad], log_source_settings=[SOC]))
    assert _lines(report, "notification_destinations", "unresolved") == [{
        "key": "bad-hook", "reason": "destination URL failed the egress guard"}]
    assert await _count(db_session, NotificationDestination) == [0]
    assert len(_lines(report, "log_source_settings", "created")) == 1
    assert report["committed"] is True


async def test_second_apply_with_failing_resolver_is_unchanged(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    host_row = {**DEST, "url": HOST_URL}
    first = _import(db_schema, tmp_path, _document(notification_destinations=[host_row]))
    assert len(_lines(first, "notification_destinations", "created")) == 1
    # A hostname URL needs DNS on create; once the row exists the guard must not
    # run, so a now-failing resolver cannot flip unchanged to unresolved.
    again = _import(db_schema, tmp_path, _document(notification_destinations=[host_row]),
                    dns_fail=True)
    assert _lines(again, "notification_destinations", "unchanged") == [{
        "key": "soc-hook", "reason": "configuration already matches"}]
    assert await _count(db_session, NotificationDestination) == [1]


async def test_same_name_different_url_conflicts_without_writing(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    first = _import(db_schema, tmp_path, _document(notification_destinations=[DEST]))
    assert len(_lines(first, "notification_destinations", "created")) == 1
    changed = {**DEST, "url": "https://93.184.216.34/other"}
    second = _import(db_schema, tmp_path, _document(notification_destinations=[changed]))
    assert _lines(second, "notification_destinations", "conflict") == [{
        "key": "soc-hook",
        "reason": "stored configuration differs from the file and is not default"}]
    row = await db_session.scalar(select(NotificationDestination))
    await db_session.refresh(row)
    assert row.url == URL
    assert await _count(db_session, NotificationDestination) == [1]


async def test_rules_point_at_new_destination_ids_and_set_actor(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(
        notification_destinations=[DEST], notification_rules=[RULE_ROW]))
    assert len(_lines(report, "notification_rules", "created")) == 1
    destination = await db_session.scalar(select(NotificationDestination))
    rule = await db_session.scalar(select(NotificationRule))
    assert rule.destination_ids == [destination.id] and rule.group_ids == [7]
    assert (rule.created_by, rule.updated_by) == ("soc", "soc")
    again = _import(db_schema, tmp_path, _document(
        notification_destinations=[DEST], notification_rules=[RULE_ROW]))
    assert len(_lines(again, "notification_rules", "unchanged")) == 1
    assert await _count(db_session, NotificationRule) == [1]


async def test_unresolved_group_skips_the_whole_rule(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    row = {**RULE_ROW, "groups": [{"name": "missing", "parent_name": None}]}
    report = _import(db_schema, tmp_path, _document(
        notification_destinations=[DEST], notification_rules=[row], log_source_settings=[SOC]))
    assert _lines(report, "notification_rules", "unresolved")[0]["reason"] == (
        "no log source group has this name and parent")
    assert await _count(db_session, NotificationRule) == [0]
    assert await _count(db_session, NotificationDestination) == [1]
    assert len(_lines(report, "log_source_settings", "created")) == 1


async def test_suppression_created_by_and_expired_window_unresolved(db_schema, db_session,
                                                                    tmp_path):
    fleet = await _fleet(db_session)
    expired = {**SUP, "detector_type": "VOLUME_DROP",
               "ends_at": format_timestamp(_now - timedelta(minutes=5))}
    report = _import(db_schema, tmp_path, _document(
        alert_suppressions=[SUP, expired], log_source_settings=[SOC]))
    assert len(_lines(report, "alert_suppressions", "created")) == 1
    assert _lines(report, "alert_suppressions", "unresolved")[0]["reason"] == (
        "window expired before import")
    row = await db_session.scalar(select(AlertSuppression))
    assert row.created_by == "soc" and row.owner == "soc" and row.reference == "CHG-100"
    assert row.subject_id == fleet["source"].id and row.ended_at is None
    assert await _count(db_session, AlertSuppression) == [1]


async def test_email_html_is_sanitized_and_empty_body_is_unresolved(db_schema, db_session,
                                                                    tmp_path):
    await _fleet(db_session)
    good = {**TEMPLATE, "alert_type": "waf_endpoint",
            "html_body": '<p onclick="x()">Hello</p><script>bad()</script>'}
    empty = {**TEMPLATE, "alert_type": "log_source", "html_body": "<script>bad()</script>"}
    report = _import(db_schema, tmp_path, _document(
        email_templates=[good, empty], log_source_settings=[SOC]))
    assert len(_lines(report, "email_templates", "created")) == 1
    assert _lines(report, "email_templates", "unresolved")[0]["reason"] == (
        "email HTML body is empty after sanitizing")
    row = await db_session.scalar(select(EmailTemplate))
    assert "Hello" in row.html_body and "onclick" not in row.html_body
    assert "script" not in row.html_body
    assert len(_lines(report, "log_source_settings", "created")) == 1


async def test_unknown_email_alert_type_is_unresolved(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    bad = {**TEMPLATE, "alert_type": "not_a_real_type"}
    report = _import(db_schema, tmp_path, _document(
        email_templates=[bad], log_source_settings=[SOC]))
    assert _lines(report, "email_templates", "unresolved") == [{
        "key": "not_a_real_type", "reason": "email alert_type is not a known template type"}]
    assert await _count(db_session, EmailTemplate) == [0]
    assert len(_lines(report, "log_source_settings", "created")) == 1
    assert report["committed"] is True


async def test_null_email_alert_type_is_the_default_template(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(email_templates=[TEMPLATE]))
    assert len(_lines(report, "email_templates", "created")) == 1
    assert (await db_session.scalar(select(EmailTemplate))).alert_type is None
    again = _import(db_schema, tmp_path, _document(email_templates=[TEMPLATE]))
    assert len(_lines(again, "email_templates", "unchanged")) == 1


async def test_unknown_outage_scope_kind_is_unresolved(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    bad = {**OUTAGE, "scope_kind": "WATCH"}
    report = _import(db_schema, tmp_path, _document(
        outage_alert_policies=[bad], log_source_settings=[SOC]))
    assert _lines(report, "outage_alert_policies", "unresolved") == [{
        "key": "WATCH|WAF", "reason": "outage scope_kind is not CLASS or SOURCE"}]
    assert await _count(db_session, OutageAlertPolicy) == [0]
    assert len(_lines(report, "log_source_settings", "created")) == 1
    assert report["committed"] is True


async def test_outage_scope_kind_must_match_its_reference(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    source_with_class = {**OUTAGE, "scope_kind": "SOURCE"}  # class ref, SOURCE kind
    class_with_source = {**OUTAGE, "scope_kind": "CLASS", "source_class": None,
                         "log_source": WAF}  # source ref, CLASS kind
    report = _import(db_schema, tmp_path, _document(
        outage_alert_policies=[source_with_class, class_with_source],
        log_source_settings=[SOC]))
    unresolved = _lines(report, "outage_alert_policies", "unresolved")
    assert [line["reason"] for line in unresolved] == [
        "outage scope does not match scope_kind", "outage scope does not match scope_kind"]
    assert {line["key"] for line in unresolved} == {"SOURCE|WAF", "CLASS|Edge WAF"}
    assert await _count(db_session, OutageAlertPolicy) == [0]
    assert len(_lines(report, "log_source_settings", "created")) == 1
    assert report["committed"] is True


async def test_ldap_seed_is_updated_once_and_version_becomes_two(db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session, ldap=True)
    report = _import(db_schema, tmp_path, _document(ldap_config=[LDAP]))
    assert len(_lines(report, "ldap_config", "created")) == 1
    row = await db_session.get(LdapConfig, 1)
    await db_session.refresh(row)  # the seed object predates the child process's commit
    assert (row.version, row.updated_by) == (2, "soc")
    assert row.enabled is True and row.urls == LDAP["urls"]
    assert row.bind_template == LDAP["bind_template"] and row.user_search_base is None
    assert row.ca_pem is None and row.start_tls is False and row.timeout_seconds == 5
    again = _import(db_schema, tmp_path, _document(ldap_config=[LDAP]))
    assert len(_lines(again, "ldap_config", "unchanged")) == 1
    await db_session.refresh(row)
    assert row.version == 2
    assert fleet["ldap"].version == 2


async def test_ldap_non_seed_difference_conflicts_without_bumping_version(
        db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session, ldap=True)
    fleet["ldap"].enabled, fleet["ldap"].updated_by = True, "operator"
    await db_session.commit()
    report = _import(db_schema, tmp_path, _document(ldap_config=[LDAP]))
    assert len(_lines(report, "ldap_config", "conflict")) == 1
    row = await db_session.get(LdapConfig, 1)
    await db_session.refresh(row)
    assert row.version == 1 and row.enabled is True
    assert row.bind_template == "{username}"  # the operator's value is preserved


async def test_ldap_validation_failure_is_unresolved(db_schema, db_session, tmp_path):
    await _fleet(db_session, ldap=True)
    bad = {**LDAP, "timeout_seconds": 99}
    report = _import(db_schema, tmp_path, _document(
        ldap_config=[bad], log_source_settings=[SOC]))
    assert len(_lines(report, "ldap_config", "unresolved")) == 1
    assert _lines(report, "ldap_config", "unresolved")[0]["reason"] == (
        "auth.directory.timeout_range")
    assert (await db_session.get(LdapConfig, 1)).version == 1
    assert len(_lines(report, "log_source_settings", "created")) == 1


async def test_policy_and_outage_resolve_and_merge(db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    source_policy = {**POLICY, "scope_kind": "SOURCE", "family": "waf", "source_class": None,
                     "log_source": WAF}
    watch_policy = {**POLICY, "family": "property_watch", "source_class": None,
                    "watch": {"name": "Denied actions"}, "detector_type": "PW_NEW_VALUE"}
    outage_source = {**OUTAGE, "scope_kind": "SOURCE", "source_class": None, "log_source": WAF}
    report = _import(db_schema, tmp_path, _document(
        alert_policies=[POLICY, source_policy, watch_policy],
        outage_alert_policies=[OUTAGE, outage_source]))
    assert len(_lines(report, "alert_policies", "created")) == 3
    assert len(_lines(report, "outage_alert_policies", "created")) == 2
    policies = list(await db_session.scalars(select(AlertPolicy)))
    assert any(p.scope_value == str(fleet["source"].id) for p in policies)
    assert any(p.scope_value == str(fleet["watch"].id) for p in policies)
    assert all(p.updated_by == "soc" for p in policies)
    changed = {**POLICY, "mode": "ALERT"}
    second = _import(db_schema, tmp_path, _document(alert_policies=[changed]))
    assert len(_lines(second, "alert_policies", "conflict")) == 1
    assert _lines(second, "alert_policies", "created") == []


async def test_recipient_is_normalized_and_bad_values_are_unresolved(db_schema, db_session,
                                                                    tmp_path):
    await _fleet(db_session)
    bad = {**RECIPIENT, "address": "not-an-address"}
    bad_type = {**RECIPIENT, "address": "other@example.internal", "alert_types": ["digest"]}
    report = _import(db_schema, tmp_path, _document(
        email_recipients=[RECIPIENT, bad, bad_type], log_source_settings=[SOC]))
    assert len(_lines(report, "email_recipients", "created")) == 1
    assert len(_lines(report, "email_recipients", "unresolved")) == 2
    row = await db_session.scalar(select(EmailRecipient))
    assert row.address == "soc@example.internal"
    assert row.alert_types == ["log_source", "waf_endpoint"]
    again = _import(db_schema, tmp_path, _document(email_recipients=[RECIPIENT]))
    assert len(_lines(again, "email_recipients", "unchanged")) == 1


async def test_dry_run_reports_writes_but_makes_none(db_schema, db_session, tmp_path):
    await _fleet(db_session, ldap=True)
    document = _document(notification_destinations=[DEST], notification_rules=[RULE_ROW],
                        alert_suppressions=[SUP], email_templates=[TEMPLATE],
                        email_recipients=[RECIPIENT], outage_alert_policies=[OUTAGE],
                        alert_policies=[POLICY], ldap_config=[LDAP])
    report = _import(db_schema, tmp_path, document, mode="--dry-run")
    assert report["mode"] == "dry-run" and report["committed"] is False
    for section, count in (("notification_destinations", 1), ("notification_rules", 1),
                           ("alert_suppressions", 1), ("email_templates", 1),
                           ("email_recipients", 1), ("outage_alert_policies", 1),
                           ("alert_policies", 1), ("ldap_config", 1)):
        assert len(_lines(report, section, "created")) == count, section
    assert await _count(db_session, NotificationDestination, NotificationRule, AlertSuppression,
                        EmailTemplate, EmailRecipient, OutageAlertPolicy, AlertPolicy) == [0] * 7
    assert (await db_session.get(LdapConfig, 1)).version == 1


BAD_ROWS = [
    ("alert_policies", {**POLICY, "mode": "INVALID"}, "row validation failed"),
    ("outage_alert_policies", {**OUTAGE, "mode": "INVALID"}, "row validation failed"),
    ("alert_suppressions", {**SUP, "detector_type": "NOPE"},
     "unknown detector for suppression family"),
    ("alert_suppressions", {**SUP, "reason": "x"}, "row validation failed"),
    ("notification_destinations", {**DEST, "name": ""}, "row validation failed"),
    ("notification_rules", {**RULE_ROW, "min_severity": "NOPE"}, "row validation failed"),
    ("email_recipients", {**RECIPIENT, "address": "bad"},
     "email recipient address is not a mailbox"),
]


@pytest.mark.parametrize(("section", "row", "reason"), BAD_ROWS,
                         ids=[row[0] for row in BAD_ROWS])
async def test_invalid_rows_are_unresolved_and_other_sections_still_commit(
        db_schema, db_session, tmp_path, section, row, reason):
    await _fleet(db_session)
    sections = {section: [row], "log_source_settings": [SOC]}
    if section == "notification_rules":
        sections["notification_destinations"] = [DEST]
    report = _import(db_schema, tmp_path, _document(**sections))
    assert _lines(report, section, "unresolved")[0]["reason"] == reason
    assert len(_lines(report, "log_source_settings", "created")) == 1
    assert report["committed"] is True
    assert "Traceback" not in json.dumps(report)


async def test_exported_row_with_warnings_is_unresolved(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    row = {**RULE_ROW, "warnings": ["group ref does not resolve"]}
    report = _import(db_schema, tmp_path, _document(notification_rules=[row]))
    assert _lines(report, "notification_rules", "unresolved")[0]["reason"] == (
        "export reported an unresolved reference")
    assert await _count(db_session, NotificationRule) == [0]
