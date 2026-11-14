"""`python -m app.cli.config export`: every exit path, and the four fleet sections it writes.

The command runs as a subprocess, the way the wrapper runs it: the document is stdout and
nothing else, one audit row is the only write, and no secret, console host or collected table
reaches the file.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config_transfer.document import SECTION_ORDER, secret_key_paths
from app.fleet.seed_rules import SEED_RULES
from app.fleet.service import ensure_seed_rules
from app.models.fleet import ClassificationRule, LogSourceClassification
from app.models.identity import AuditLog
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.search_cadence import SearchCadenceSetting

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
NAME, HOST, TOKEN = "preprod", "https://qradar.lab.internal", "S3CRET-TOKEN-h4rdc0ded"
WAF = {"name": "Edge WAF", "type_name": "F5 Networks BIG-IP ASM"}
IPS = {"name": "Edge IPS", "type_name": "Cisco Firepower"}
WINDOWS = r"\bMicrosoft Windows Security Event Log\b"
FLEET = ("log_source_settings", "classification_overrides", "classification_rules",
         "search_cadence")


def _run(url: str, *args: str, port: str | None = None) -> subprocess.CompletedProcess[str]:
    parsed = make_url(url)
    env = dict(os.environ) | {
        "POSTGRES_USER": parsed.username or "", "POSTGRES_PASSWORD": parsed.password or "",
        "POSTGRES_DB": parsed.database or "", "POSTGRES_HOST": parsed.host or "localhost",
        "POSTGRES_PORT": port or str(parsed.port or 5432), "PYTHONPATH": str(BACKEND),
    }
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "app.cli.config", *args], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=120, check=False,
    )


async def _consoles(session: AsyncSession, *names: str) -> None:
    """One console per name, each with a log source named after it."""
    for qradar_id, name in enumerate(names, start=1):
        console = QRadarInstance(name=name, console_host=HOST, sec_token=TOKEN)
        session.add(console)
        await session.flush()
        session.add(LogSource(instance_id=console.id, qradar_id=qradar_id, name=f"{name}-waf"))
    await session.commit()


async def _fleet(session: AsyncSession) -> QRadarInstance:
    """The console holding every value the four exported sections carry."""
    console = QRadarInstance(name=NAME, console_host=HOST, sec_token=TOKEN)
    session.add(console)
    await session.flush()
    waf = LogSource(instance_id=console.id, qradar_id=1, name=WAF["name"],
                    type_name=WAF["type_name"], criticality="HIGH", owner="soc")
    ips = LogSource(instance_id=console.id, qradar_id=2, name=IPS["name"],
                    type_name=IPS["type_name"])
    session.add_all([waf, ips])
    await session.flush()
    session.add_all([
        LogSourceClassification(log_source_id=waf.id, source_class="WAF", basis="TYPE_NAME",
                                conflicts=[], classified_at=datetime.now(UTC),
                                review_status="NEW", override_class="WAF"),
        LogSourceClassification(log_source_id=ips.id, source_class="IPS", basis="TYPE_NAME",
                                conflicts=[], classified_at=datetime.now(UTC),
                                review_status="NEW"),
        ClassificationRule(instance_id=None, kind="NAME_REGEX", pattern="edge-waf",
                           target_class="WAF", priority=10, enabled=True, built_in=False,
                           description="Site naming convention"),
        SearchCadenceSetting(family="WAF_COLLECTION", cadence_seconds=900,
                             updated_at=datetime.now(UTC), updated_by="old-operator",
                             last_slot_start=datetime.now(UTC), last_run_at=datetime.now(UTC)),
    ])
    await ensure_seed_rules(session)
    windows = next(rule for rule in SEED_RULES if rule.pattern == WINDOWS)
    await session.execute(update(ClassificationRule)
                          .where(ClassificationRule.id == windows.id).values(enabled=False))
    await session.commit()
    return console


async def _audits(session: AsyncSession) -> list[AuditLog]:
    return list((await session.scalars(select(AuditLog))).all())


async def test_the_refusals_write_nothing_and_one_console_is_selected(
    db_schema: str, db_session: AsyncSession
) -> None:
    unreachable = _run(db_schema, "export", port="1")
    assert (unreachable.returncode, unreachable.stdout) == (1, "")
    assert "the export was not written" in unreachable.stderr
    assert "Traceback" not in unreachable.stderr and "password" not in unreachable.stderr
    empty = _run(db_schema, "export")
    assert (empty.returncode, empty.stdout) == (2, "")
    assert "no console is registered" in empty.stderr
    # A bad argument is refused first: it never waits on, or reads, the database.
    bad = _run(db_schema, "export", "--label", "pre prod")
    assert bad.returncode == 2 and "does not match" in bad.stderr
    assert "no console is registered" not in bad.stderr
    assert await _audits(db_session) == []

    await _consoles(db_session, "lab", "prod")
    ambiguous = _run(db_schema, "export")
    assert ambiguous.returncode == 2 and "2 are registered" in ambiguous.stderr
    chosen = _run(db_schema, "export", "--instance", "prod")
    assert chosen.returncode == 0, chosen.stderr
    document = json.loads(chosen.stdout)
    assert document["source_label"] == "prod"
    exported = [row["log_source"]["name"] for row in document["sections"]["log_source_settings"]]
    assert exported == ["prod-waf"]
    unknown = _run(db_schema, "export", "--instance", "ghost")
    assert unknown.returncode == 2 and "ghost" in unknown.stderr
    assert len(await _audits(db_session)) == 1  # only the successful run


async def test_one_console_prints_the_document_and_writes_one_audit_row(
    db_schema: str, db_session: AsyncSession
) -> None:
    await _fleet(db_session)
    done = _run(db_schema, "export", "--actor", "soc-lead")
    assert (done.returncode, done.stderr) == (0, "")
    document = json.loads(done.stdout)  # stdout is the document, and nothing else
    assert done.stdout.endswith("}\n") and document["schema_version"] == 1
    assert document["source_label"] == NAME and list(document["sections"]) == list(SECTION_ORDER)
    sections = document["sections"]
    assert [row["log_source"] for row in sections["log_source_settings"]] == [IPS, WAF]
    waf_row = sections["log_source_settings"][1]
    assert (waf_row["criticality"], waf_row["owner"]) == ("HIGH", "soc")
    assert set(waf_row) == {
        "log_source", "criticality", "owner", "owner_email", "monitoring_enabled",
        "expected_interval_seconds", "business_hours_only", "business_hours_start",
        "business_hours_end", "business_days", "timezone_name", "custom_thresholds",
    }
    # A null override is classifier state, not operator configuration, so it is not exported.
    assert sections["classification_overrides"] == [
        {"log_source": WAF, "override_class": "WAF"}
    ]
    assert sections["classification_rules"] == [
        {"built_in": False, "scope": "global", "kind": "NAME_REGEX", "pattern": "edge-waf",
         "target_class": "WAF", "priority": 10, "enabled": True,
         "description": "Site naming convention"},
        {"built_in": True, "kind": "TYPE_NAME_REGEX", "pattern": WINDOWS, "enabled": False},
    ]
    audits = await _audits(db_session)
    assert len(audits) == 1
    assert (audits[0].action, audits[0].actor, audits[0].object_type, audits[0].object_id) == (
        "config.export", "soc-lead", "config_bundle", NAME
    )
    assert audits[0].outcome == "SUCCESS" and audits[0].detail["source_label"] == NAME
    counts = audits[0].detail["counts"]
    assert {name: counts[name] for name in FLEET} == {
        "log_source_settings": 2, "classification_overrides": 1,
        "classification_rules": 2, "search_cadence": 1,
    } and set(counts) == set(SECTION_ORDER)


async def test_the_document_carries_no_secret_and_no_runtime_column(
    db_schema: str, db_session: AsyncSession
) -> None:
    await _fleet(db_session)
    first, second = _run(db_schema, "export"), _run(db_schema, "export")
    document = json.loads(first.stdout)
    assert secret_key_paths(document) == []
    absent = (TOKEN, TOKEN.lower(), HOST, "qradar.lab.internal", "console_host")
    runtime = ("last_run_at", "last_slot_start", "updated_by", "history_days")
    assert not any(word in first.stdout for word in absent + runtime)
    sections = document["sections"]
    assert [name for name in SECTION_ORDER if name not in FLEET and sections[name]] == []
    assert not any(name.endswith("_bucket") for name in sections)
    assert sections["search_cadence"] == [{"family": "WAF_COLLECTION", "cadence_seconds": 900}]
    # Two runs of one unchanged database differ only in the exported_at line.
    one, two = json.loads(first.stdout), json.loads(second.stdout)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", one.pop("exported_at"))
    two.pop("exported_at")
    assert one == two
    pairs = zip(first.stdout.splitlines(), second.stdout.splitlines(), strict=True)
    assert all(left == right for index, (left, right) in enumerate(pairs) if index != 3)


async def test_an_instance_name_that_is_not_a_label_is_refused(
    db_schema: str, db_session: AsyncSession
) -> None:
    await _consoles(db_session, "pre prod")
    defaulted = _run(db_schema, "export")
    assert defaulted.returncode == 2 and defaulted.stdout == ""
    assert "instance name is not a legal source label; pass --label" in defaulted.stderr
    labelled = _run(db_schema, "export", "--label", "preprod")
    assert labelled.returncode == 0, labelled.stderr
    assert json.loads(labelled.stdout)["source_label"] == "preprod"
    for bad in (HOST, "pre prod", "a" * 65):
        refused = _run(db_schema, "export", "--label", bad)
        assert refused.returncode == 2 and "does not match" in refused.stderr
    assert len(await _audits(db_session)) == 1
