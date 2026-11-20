"""Real import commands: refusals, transaction boundaries, and fail-closed fleet merges."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import make_url

from app.config_transfer.document import SECTION_ORDER, build_document
from app.config_transfer.import_bundle import SOC_DEFAULTS, plan
from app.config_transfer.report import ImportReport
from app.fleet.service import ensure_seed_rules
from app.models.fleet import ClassificationRule, LogSourceClassification
from app.models.identity import AuditLog
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.search_cadence import SearchCadenceSetting

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[2]
WINDOWS = r"\bMicrosoft Windows Security Event Log\b"
REF = {"name": "Edge WAF", "type_name": "F5 Networks BIG-IP ASM"}
BUILTIN = {"built_in": True, "kind": "TYPE_NAME_REGEX", "pattern": WINDOWS, "enabled": False}
OPERATOR = {"built_in": False, "scope": "global", "kind": "NAME_REGEX", "pattern": "edge-waf",
            "target_class": "WAF", "priority": 10, "enabled": True, "description": "Site rule"}


def _document(**sections):
    return build_document(product_version="test", exported_at=datetime.now(UTC),
                          source_label="preprod", sections=sections)


def _soc(**values):
    return {"log_source": REF, **SOC_DEFAULTS, **values}


def _run(url, tmp_path, *args, document=None, raw=None, hook="", port=None):
    """A child-only startup hook injects failures without application testing switches."""
    parsed = make_url(url)
    startup = tmp_path / "sitecustomize.py"
    startup.write_text(hook)
    env = dict(os.environ) | {
        "POSTGRES_USER": parsed.username or "", "POSTGRES_PASSWORD": parsed.password or "",
        "POSTGRES_DB": parsed.database or "", "POSTGRES_HOST": parsed.host or "localhost",
        "POSTGRES_PORT": str(port or parsed.port or 5432),
        "PYTHONPATH": os.pathsep.join((str(tmp_path), str(BACKEND))),
    }
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "app.cli.config", "import", *args], cwd=BACKEND, env=env,
        input=raw if raw is not None else json.dumps(document or _document()),
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert "Error in sitecustomize" not in result.stderr, result.stderr
    return result


def _report(done, code=0):
    assert done.returncode == code, done.stderr
    assert "Traceback" not in done.stderr
    report = json.loads(done.stdout)
    assert list(report["sections"]) == list(SECTION_ORDER)
    return report


def _lines(report, section, outcome):
    return report["sections"][section][outcome]


async def _fleet(session, *, classified=False, seeded=False):
    console = QRadarInstance(name="prod", console_host="https://lab.invalid", sec_token="secret")
    session.add(console)
    await session.flush()
    source = LogSource(instance_id=console.id, qradar_id=1, name=REF["name"],
                       type_name=REF["type_name"])
    session.add(source)
    await session.flush()
    if classified:
        session.add(LogSourceClassification(
            log_source_id=source.id, source_class="OTHER", basis="DEFAULT", conflicts=[],
            classified_at=datetime.now(UTC), review_status="NEW"))
    if seeded:
        await ensure_seed_rules(session)
    session.add(SearchCadenceSetting(family="WAF_COLLECTION", cadence_seconds=900,
                                    updated_at=datetime.now(UTC), last_run_at=datetime.now(UTC)))
    await session.commit()
    return console, source


async def _audits(session):
    return list(await session.scalars(select(AuditLog).order_by(AuditLog.created_at)))


async def test_argument_and_document_refusals(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    for args in ((), ("--apply",), ("--dry-run", "--apply", "--yes")):
        refused = _run(db_schema, tmp_path, *args)
        assert (refused.returncode, refused.stdout) == (2, "")
    for raw, message in (("{", "not JSON"),
                         (json.dumps({"schema_version": 2}), "not supported"),
                         (" " * 5_000_001, "cap"),
                         (json.dumps({"schema_version": 1, "token": "secret"}), "unknown"),
                         (json.dumps(_document(log_source_settings=[{"password": "secret"}])),
                          "secret key")):
        refused = _run(db_schema, tmp_path, "--dry-run", raw=raw)
        assert (refused.returncode, refused.stdout) == (2, "")
        assert message in refused.stderr
    assert await _audits(db_session) == []


async def test_console_preflight_and_database_failure(db_schema, db_session, tmp_path):
    empty = _run(db_schema, tmp_path, "--dry-run")
    assert empty.returncode == 2 and "no console is registered" in empty.stderr
    await _fleet(db_session)
    db_session.add(QRadarInstance(name="other", console_host="https://other.invalid",
                                 sec_token="secret"))
    await db_session.commit()
    many = _run(db_schema, tmp_path, "--dry-run")
    assert many.returncode == 2 and "import resolves names on one console; 2" in many.stderr
    missing = _run(db_schema, tmp_path, "--dry-run", "--instance", "missing")
    assert missing.returncode == 2 and "no console is registered under the name" in missing.stderr
    assert await _audits(db_session) == []
    chosen = _report(_run(db_schema, tmp_path, "--dry-run", "--instance", "prod"))
    assert chosen["instance"] == "prod"
    failure = _run(db_schema, tmp_path, "--apply", "--yes", port=1)
    assert _report(failure, 1)["committed"] is False
    assert "test:test" not in failure.stderr


async def test_dry_run_rolls_back_before_second_session_audit(db_schema, db_session, tmp_path):
    _, source = await _fleet(db_session)
    trace = tmp_path / "sessions.jsonl"
    hook = f'''
import json
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories import audit
original_rollback, original_audit = AsyncSession.rollback, audit.record_audit
async def rollback(self):
    with open({str(trace)!r}, "a") as out:
        out.write(json.dumps(["rollback", id(self)]) + "\\n")
    await original_rollback(self)
async def record(session, **kwargs):
    with open({str(trace)!r}, "a") as out:
        out.write(json.dumps(["audit", id(session)]) + "\\n")
    return await original_audit(session, **kwargs)
AsyncSession.rollback, audit.record_audit = rollback, record
'''
    done = _run(db_schema, tmp_path, "--dry-run", "--actor", "soc",
                document=_document(log_source_settings=[_soc(owner="soc")]), hook=hook)
    report = _report(done)
    assert report["mode"] == "dry-run" and report["committed"] is False
    assert len(_lines(report, "log_source_settings", "created")) == 1
    assert _lines(report, "log_source_settings", "created")[0]["local_id"] == str(source.id)
    await db_session.refresh(source)
    assert source.owner is None
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    assert events[0][0] == "rollback" and events[1][0] == "audit"
    assert events[0][1] != events[1][1]
    audits = await _audits(db_session)
    assert len(audits) == 1 and audits[0].action == "config.import.dry_run"
    assert audits[0].actor == "soc" and audits[0].detail["created"] == 1
    hook = '''
from app.repositories import audit
async def fail(*args, **kwargs):
    raise RuntimeError("sensitive audit error")
audit.record_audit = fail
'''
    failed = _run(db_schema, tmp_path, "--dry-run", hook=hook)
    assert _report(failed)["committed"] is False
    assert "sensitive audit error" not in failed.stderr
    assert len(await _audits(db_session)) == 1


async def test_apply_commits_and_reapply_is_unchanged_without_provider_or_secrets(
    db_schema, db_session, tmp_path
):
    _, source = await _fleet(db_session)
    cadence = await db_session.get(SearchCadenceSetting, "WAF_COLLECTION")
    last_run = cadence.last_run_at
    hook = '''
from app.providers import factory
from app.models.base import EncryptedString
def fail(*args, **kwargs):
    raise AssertionError("provider or secret was read")
factory.build_provider = factory.build_provider_for_instance = fail
EncryptedString.process_result_value = fail
'''
    document = _document(log_source_settings=[_soc(owner="soc")],
                         search_cadence=[{"family": "WAF_COLLECTION", "cadence_seconds": 1800}])
    first = _report(_run(db_schema, tmp_path, "--apply", "--yes", "--actor", "soc",
                         document=document, hook=hook))
    assert first["committed"] is True
    assert all(len(_lines(first, section, "created")) == 1 for section in
               ("log_source_settings", "search_cadence"))
    await db_session.refresh(source)
    await db_session.refresh(cadence)
    assert source.owner == "soc"
    assert (cadence.cadence_seconds, cadence.updated_by) == (1800, "soc")
    assert cadence.last_run_at == last_run
    updated = cadence.updated_at
    second = _report(_run(db_schema, tmp_path, "--apply", "--yes", document=document, hook=hook))
    assert all(len(_lines(second, section, "unchanged")) == 1 for section in
               ("log_source_settings", "search_cadence"))
    await db_session.refresh(cadence)
    assert cadence.updated_at == updated
    audits = await _audits(db_session)
    assert len(audits) == 2 and audits[0].action == "config.import.apply"
    assert audits[0].detail["instance"] == "prod"


async def test_apply_exception_rolls_back_every_write_and_reports_failure(db_schema, db_session,
                                                                       tmp_path):
    await _fleet(db_session)
    hook = '''
from app.repositories import audit
original = audit.record_audit
async def fail(session, **kwargs):
    if kwargs.get("outcome", "SUCCESS") == "SUCCESS":
        raise RuntimeError("secret exception text")
    return await original(session, **kwargs)
audit.record_audit = fail
'''
    done = _run(db_schema, tmp_path, "--apply", "--yes", hook=hook,
                document=_document(classification_rules=[OPERATOR],
                                   log_source_settings=[_soc(owner="soc")]))
    assert _report(done, 1)["committed"] is False
    assert "secret exception text" not in done.stderr
    assert list(await db_session.scalars(select(ClassificationRule))) == []
    source = await db_session.scalar(select(LogSource))
    await db_session.refresh(source)
    assert source.owner is None
    audits = await _audits(db_session)
    assert len(audits) == 1 and audits[0].outcome == "FAILURE"
    # A failed best-effort failure audit must preserve exit 1 as well.
    done = _run(db_schema, tmp_path, "--apply", "--yes",
                hook=hook.replace('if kwargs.get("outcome", "SUCCESS") == "SUCCESS":', 'if True:'))
    assert _report(done, 1)["committed"] is False


async def test_conflicts_ambiguity_missing_seed_and_invalid_values(db_schema, db_session, tmp_path):
    console, source = await _fleet(db_session)
    source.owner = "production-team"
    cadence = await db_session.get(SearchCadenceSetting, "WAF_COLLECTION")
    cadence.updated_by = "operator"
    await db_session.commit()
    document = _document(log_source_settings=[_soc(owner="soc")], search_cadence=[
        {"family": "WAF_COLLECTION", "cadence_seconds": 1800},
        {"family": "IPS_COLLECTION", "cadence_seconds": 900},
        {"family": "NO_FAMILY", "cadence_seconds": 900}])
    report = _report(_run(db_schema, tmp_path, "--apply", "--yes", document=document))
    assert len(_lines(report, "log_source_settings", "conflict")) == 1
    assert len(_lines(report, "search_cadence", "conflict")) == 1
    assert len(_lines(report, "search_cadence", "unresolved")) == 2
    assert "cadence family is not seeded" in json.dumps(report)
    db_session.add(LogSource(instance_id=console.id, qradar_id=2, name=source.name,
                             type_name=source.type_name))
    await db_session.commit()
    report = _report(_run(db_schema, tmp_path, "--apply", "--yes", document=document))
    line = _lines(report, "log_source_settings", "unresolved")[0]
    assert line["reason"] == "2 log sources share this name and type" and "local_id" not in line
    for bad in (42, None, "wrong"):
        report = _report(_run(db_schema, tmp_path, "--apply", "--yes", document=_document(
            search_cadence=[{"family": "WAF_COLLECTION", "cadence_seconds": bad}])))
        assert len(_lines(report, "search_cadence", "unresolved")) == 1
    await db_session.refresh(cadence)
    assert cadence.cadence_seconds == 900 and cadence.updated_by == "operator"
    await db_session.refresh(source)
    assert source.owner == "production-team"


async def test_builtin_classification_updates_enabled_without_create(db_schema, db_session,
                                                                   tmp_path):
    await _fleet(db_session, seeded=True)
    hook = '''
from app.config_transfer import import_bundle
def fail(*args, **kwargs):
    raise AssertionError("built-in passed through create validator")
import_bundle.ClassificationRuleCreate = fail
'''
    report = _report(_run(db_schema, tmp_path, "--apply", "--yes", hook=hook,
                         document=_document(classification_rules=[BUILTIN])))
    assert len(_lines(report, "classification_rules", "created")) == 1
    rule = await db_session.scalar(select(ClassificationRule).where(
        ClassificationRule.pattern == WINDOWS))
    assert rule.built_in and not rule.enabled and rule.target_class == "WINDOWS"
    assert rule.description is not None
    report = _report(_run(db_schema, tmp_path, "--apply", "--yes", hook=hook,
                         document=_document(classification_rules=[BUILTIN])))
    assert len(_lines(report, "classification_rules", "unchanged")) == 1


async def test_builtin_classification_without_seed_is_unresolved(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    report = _report(_run(db_schema, tmp_path, "--apply", "--yes",
                         document=_document(classification_rules=[BUILTIN])))
    assert _lines(report, "classification_rules", "unresolved")[0]["reason"] == (
        "built-in classification rule is not seeded")
    assert list(await db_session.scalars(select(ClassificationRule))) == []


async def test_operator_classification_rule_maps_scope(db_schema, db_session, tmp_path):
    console, _ = await _fleet(db_session)
    document = _document(classification_rules=[OPERATOR, {**OPERATOR, "scope": "instance"}])
    report = _report(_run(db_schema, tmp_path, "--apply", "--yes", "--actor", "soc",
                         document=document))
    assert len(_lines(report, "classification_rules", "created")) == 2
    rules = list(await db_session.scalars(select(ClassificationRule)))
    assert {rule.instance_id for rule in rules} == {None, console.id}
    assert all(not rule.built_in and rule.created_by == "soc" for rule in rules)
    report = _report(_run(db_schema, tmp_path, "--apply", "--yes", document=document))
    assert len(_lines(report, "classification_rules", "unchanged")) == 2
    report = _report(_run(db_schema, tmp_path, "--apply", "--yes", document=_document(
        classification_rules=[{**OPERATOR, "priority": 20}])))
    assert len(_lines(report, "classification_rules", "conflict")) == 1


async def test_classification_override_without_row_is_unresolved(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    report = _report(_run(db_schema, tmp_path, "--apply", "--yes", document=_document(
        classification_overrides=[{"log_source": REF, "override_class": "WAF"}])))
    assert _lines(report, "classification_overrides", "unresolved")[0]["reason"] == (
        "log source has not been classified")
    assert list(await db_session.scalars(select(LogSourceClassification))) == []


async def test_classification_override_sets_actor_and_time(db_schema, db_session, tmp_path):
    _, source = await _fleet(db_session, classified=True)
    document = _document(classification_overrides=[{"log_source": REF, "override_class": "WAF"}])
    report = _report(_run(db_schema, tmp_path, "--apply", "--yes", "--actor", "soc",
                         document=document))
    assert len(_lines(report, "classification_overrides", "created")) == 1
    row = await db_session.get(LogSourceClassification, source.id)
    assert row.override_class == "WAF" and row.override_by == "soc" and row.override_at is not None
    assert row.source_class == "OTHER" and row.basis == "DEFAULT" and row.review_status == "NEW"
    timestamp = row.override_at
    report = _report(_run(db_schema, tmp_path, "--apply", "--yes", document=document))
    assert len(_lines(report, "classification_overrides", "unchanged")) == 1
    await db_session.refresh(row)
    assert row.override_at == timestamp and row.override_by == "soc"
    for value, outcome in (("IPS", "conflict"), (None, "conflict"), ("INVALID", "unresolved")):
        report = _report(_run(db_schema, tmp_path, "--apply", "--yes", document=_document(
            classification_overrides=[{"log_source": REF, "override_class": value}])))
        assert len(_lines(report, "classification_overrides", outcome)) == 1
    await db_session.refresh(row)
    assert row.override_class == "WAF"


@pytest.mark.parametrize("section,row", (
        ("log_source_settings", _soc(custom_thresholds={"not_a_threshold": 1})),
        ("log_source_settings", _soc(criticality=None)),
        ("log_source_settings", _soc(timezone_name="not/a/timezone")),
        ("log_source_settings", {**_soc(), "maintenance_mode": True}),
        ("log_source_settings", _soc(log_source={"name": "missing", "type_name": None})),
        ("log_source_settings", _soc(log_source="00000000-0000-4000-8000-000000000001")),
        ("log_source_settings", {**_soc(), "warnings": ["unresolved source"]}),
        ("classification_rules", {**OPERATOR, "scope": "unknown"}),
        ("classification_rules", {**OPERATOR, "kind": "INVALID"}),
        ("classification_rules", {**OPERATOR, "target_class": "INVALID"}),
        ("classification_rules", {**OPERATOR, "pattern": "["}),
        ("classification_rules", {**BUILTIN, "pattern": "["}),
        ("classification_rules", {**BUILTIN, "enabled": None}),
        ("classification_rules", {**BUILTIN, "pattern": None}),
        ("classification_overrides", {"log_source": REF, "override_class": "INVALID"}),
        ("search_cadence", {"family": "WAF_COLLECTION", "cadence_seconds": 42}),
    ), ids=["thresholds", "null-soc", "timezone", "extra-field", "missing-source", "uuid-source",
            "warnings", "scope", "kind", "target-class", "operator-pattern", "builtin-pattern",
            "flags", "pattern-type", "override-class", "cadence-value"])
async def test_invalid_rows_are_unresolved_and_other_sections_still_commit(
    db_schema, db_session, tmp_path, section, row
):
    _, source = await _fleet(db_session, classified=True, seeded=True)
    sections = {section: [row]}
    companion = "log_source_settings" if section == "search_cadence" else "search_cadence"
    sections[companion] = ([_soc(owner="accepted")] if companion == "log_source_settings" else
                           [{"family": "WAF_COLLECTION", "cadence_seconds": 1800}])
    report = _report(_run(db_schema, tmp_path, "--apply", "--yes", document=_document(**sections)))
    assert len(_lines(report, companion, "created")) == 1
    assert len(_lines(report, section, "unresolved")) == 1, row
    if row.get("warnings"):
        assert _lines(report, section, "unresolved")[0]["reason"] == (
            "export reported an unresolved reference")
    assert report["committed"] is True
    await db_session.refresh(source)
    assert source.owner == ("accepted" if section == "search_cadence" else None)
    assert source.criticality == "MEDIUM"
    assert list(await db_session.scalars(select(ClassificationRule).where(
        ClassificationRule.built_in.is_(False)))) == []


async def test_duplicate_keys_and_later_sections_are_reported(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    document = _document(log_source_settings=[_soc(owner="first"), _soc(owner="second")],
                         technique_mappings=[{"name": "x"}],
                         alert_policies=[{"name": "policy"}], dashboards=[{"name": "SOC"}])
    report = _report(_run(db_schema, tmp_path, "--apply", "--yes", document=document))
    assert len(_lines(report, "log_source_settings", "unresolved")) == 2
    for section in ("technique_mappings", "alert_policies", "dashboards"):
        assert _lines(report, section, "unresolved")[0]["reason"] == "not applied in this release"
    source = await db_session.scalar(select(LogSource))
    await db_session.refresh(source)
    assert source.owner is None


async def test_rule_ambiguity_and_builtin_collision_are_not_guessed(
    db_schema, db_session, tmp_path
):
    console, _ = await _fleet(db_session, seeded=True)
    db_session.add(ClassificationRule(
        instance_id=console.id, kind="TYPE_NAME_REGEX", pattern=WINDOWS,
        target_class="OTHER", priority=10, enabled=True, built_in=True))
    await db_session.commit()
    report = _report(_run(db_schema, tmp_path, "--apply", "--yes", document=_document(
        classification_rules=[BUILTIN])))
    assert _lines(report, "classification_rules", "unresolved")[0]["reason"] == (
        "classification natural key is ambiguous")
    report = _report(_run(db_schema, tmp_path, "--apply", "--yes", document=_document(
        classification_rules=[{**OPERATOR, "kind": "TYPE_NAME_REGEX", "pattern": WINDOWS}])))
    assert len(_lines(report, "classification_rules", "conflict")) == 1
    rules = list(await db_session.scalars(select(ClassificationRule).where(
        ClassificationRule.pattern == WINDOWS)))
    assert len(rules) == 2 and all(rule.enabled for rule in rules)


async def test_null_type_matches_and_unattributed_nondefault_cadence_conflicts(
    db_schema, db_session, tmp_path
):
    _, source = await _fleet(db_session)
    source.type_name = None
    cadence = await db_session.get(SearchCadenceSetting, "WAF_COLLECTION")
    cadence.cadence_seconds = 1800
    await db_session.commit()
    report = _report(_run(db_schema, tmp_path, "--apply", "--yes", document=_document(
        log_source_settings=[_soc(log_source={**REF, "type_name": None}, owner="soc")],
        search_cadence=[{"family": "WAF_COLLECTION", "cadence_seconds": 3600}])))
    assert _lines(report, "log_source_settings", "created")[0]["local_id"] == str(source.id)
    assert len(_lines(report, "search_cadence", "conflict")) == 1
    await db_session.refresh(cadence)
    assert cadence.cadence_seconds == 1800


async def test_invalid_seed_pattern_is_still_validated(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    rule = ClassificationRule(instance_id=None, kind="NAME_REGEX", pattern="[",
                              target_class="OTHER", priority=10, enabled=True, built_in=True)
    db_session.add(rule)
    await db_session.commit()
    report = _report(_run(db_schema, tmp_path, "--apply", "--yes", document=_document(
        classification_rules=[{**BUILTIN, "kind": "NAME_REGEX", "pattern": "["}])))
    assert _lines(report, "classification_rules", "unresolved")[0]["reason"] == (
        "row validation failed")
    await db_session.refresh(rule)
    assert rule.enabled is True


async def test_created_reason_uses_mode_specific_wording(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    document = _document(log_source_settings=[_soc(owner="soc")])
    dry = _report(_run(db_schema, tmp_path, "--dry-run", document=document))
    assert dry["mode"] == "dry-run" and dry["committed"] is False
    assert _lines(dry, "log_source_settings", "created")[0]["reason"] == (
        "would apply configuration")
    applied = _report(_run(db_schema, tmp_path, "--apply", "--yes", document=document))
    assert applied["mode"] == "apply" and applied["committed"] is True
    assert _lines(applied, "log_source_settings", "created")[0]["reason"] == (
        "applied configuration")


async def test_null_file_override_conflicts_with_set_local_override(db_schema, db_session,
                                                                  tmp_path):
    _, source = await _fleet(db_session, classified=True)
    first = _report(_run(db_schema, tmp_path, "--apply", "--yes", "--actor", "soc",
                         document=_document(classification_overrides=[
                             {"log_source": REF, "override_class": "WAF"}])))
    assert len(_lines(first, "classification_overrides", "created")) == 1
    row = await db_session.get(LogSourceClassification, source.id)
    assert (row.override_class, row.override_by) == ("WAF", "soc")
    stamp, actor = row.override_at, row.override_by

    cleared = _report(_run(db_schema, tmp_path, "--apply", "--yes", document=_document(
        classification_overrides=[{"log_source": REF, "override_class": None}])))
    line = _lines(cleared, "classification_overrides", "conflict")[0]
    assert line["reason"] == "file has no override; import never clears a local override"
    assert "configuration already matches" not in json.dumps(cleared)
    await db_session.refresh(row)
    assert (row.override_class, row.override_by, row.override_at) == ("WAF", actor, stamp)


async def test_classification_query_is_one_join_loading_every_classified_source(db_schema,
                                                                             db_session):
    console, source = await _fleet(db_session, classified=True)
    second = LogSource(instance_id=console.id, qradar_id=2, name="Second WAF",
                       type_name=REF["type_name"])
    db_session.add(second)
    await db_session.flush()
    db_session.add(LogSourceClassification(
        log_source_id=second.id, source_class="OTHER", basis="DEFAULT", conflicts=[],
        classified_at=datetime.now(UTC), review_status="NEW"))
    await db_session.commit()

    statements: list[tuple[str, object]] = []

    @event.listens_for(db_session.get_bind(), "before_cursor_execute")
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    report = ImportReport("apply")
    await plan(db_session, _document(classification_overrides=[
        {"log_source": REF, "override_class": "WAF"},
        {"log_source": {"name": "Second WAF", "type_name": REF["type_name"]},
         "override_class": "IPS"},
    ]), console, report, "soc", "apply")
    created = report.sections["classification_overrides"]["created"]
    assert [line["local_id"] for line in created] == [str(source.id), str(second.id)]
    queries = [sql for sql, _ in statements
               if "log_source_classification" in sql and sql.lstrip().lower().startswith("select")]
    assert len(queries) == 1
    assert "join log_source" in queries[0].lower()
    await db_session.rollback()
