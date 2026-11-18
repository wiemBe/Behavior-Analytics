"""`python -m app.cli.config export`: the three profile sections, and what they never carry.

The command runs as a subprocess, the way the wrapper runs it. A profile exports its mapping and
``enabled_on_source``; the validation state is the old console's evidence, so a ``VALID`` profile
with a report exports no validation field at all, and a row on another console stays out.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config_transfer.document import SECTION_ORDER, secret_key_paths
from app.models.identity import AuditLog
from app.models.identity_detection import IdentitySourceProfile
from app.models.instance import QRadarInstance
from app.models.ips import IpsSourceProfile
from app.models.log_source import LogSource
from app.models.waf import WafSourceProfile

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
NAME, HOST, TOKEN = "preprod", "https://qradar.lab.internal", "S3CRET-TOKEN-h4rdc0ded"
WAF = {"name": "Edge WAF", "type_name": "F5 Networks BIG-IP ASM"}
IPS = {"name": "Edge IPS", "type_name": "Cisco Firepower"}
WINDOWS = {"name": "DC Security", "type_name": "Microsoft Windows Security Event Log"}
PROFILES = ("waf_source_profiles", "ips_source_profiles", "identity_source_profiles")
#: Every column that records the old console's validation: none of these may reach the file.
VALIDATION = ("validation_status", "validated_profile_version", "validated_at",
              "validation_report", "validation_requested_at", "validation_requested_by", "VALID")
#: Text the file must never carry: the validation state, a report value, an actor or the console.
FORBIDDEN = (*VALIDATION, "REPORT-SENTINEL", "old-operator", TOKEN, HOST)
#: The documented `CISCO_FTD` default: `ACTION` unmapped and both action lists empty.
FTD_MAPPING = {
    "SOURCE_IP": {"kind": "NORMALIZED", "name": "sourceip"},
    "DESTINATION_IP": {"kind": "NORMALIZED", "name": "destinationip"},
    "DESTINATION_PORT": {"kind": "NORMALIZED", "name": "destinationport"},
    "PROTOCOL": {"kind": "NORMALIZED", "name": "protocolid"},
    "SIGNATURE": {"kind": "NORMALIZED", "name": "qid"},
    "ACTION": None,
    "SEVERITY": {"kind": "NORMALIZED", "name": "severity"},
}
WAF_ROW = {
    "log_source": WAF, "enabled_on_source": True, "product_label": "F5 ASM",
    "url_property": "URL", "method_property": "Method", "virtual_host_property": None,
    "action_property": "Action", "response_code_property": "ResponseCode",
    "rule_id_property": None, "user_agent_property": None,
    "action_value_map": {"BLOCKED": ["blocked"], "ALLOWED": ["allowed"]},
}


def _run(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    parsed = make_url(url)
    env = dict(os.environ) | {
        "POSTGRES_USER": parsed.username or "", "POSTGRES_PASSWORD": parsed.password or "",
        "POSTGRES_DB": parsed.database or "", "POSTGRES_HOST": parsed.host or "localhost",
        "POSTGRES_PORT": str(parsed.port or 5432), "PYTHONPATH": str(BACKEND),
    }
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "app.cli.config", *args], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=120, check=False,
    )


async def _console(session: AsyncSession, name: str = NAME) -> QRadarInstance:
    console = QRadarInstance(name=name, console_host=HOST, sec_token=TOKEN)
    session.add(console)
    await session.flush()
    return console


def _waf_type(name: str) -> dict[str, str]:
    """A WAF log source whose name sorts before the documented one."""
    return {"name": name, "type_name": WAF["type_name"]}


async def _source(
    session: AsyncSession, console: QRadarInstance, ref: dict[str, str], qradar_id: int,
    *, owner: str | None = None,
) -> LogSource:
    source = LogSource(instance_id=console.id, qradar_id=qradar_id, name=ref["name"],
                       type_name=ref["type_name"], owner=owner)
    session.add(source)
    await session.flush()
    return source


def _section(document: str, name: str) -> list[dict[str, object]]:
    sections: dict[str, list[dict[str, object]]] = json.loads(document)["sections"]
    return sections[name]


async def test_absent_profiles_are_present_as_empty_sections(
    db_schema: str, db_session: AsyncSession
) -> None:
    """Every section is in the file even when the console has no profile of that kind."""
    console = await _console(db_session)
    await _source(db_session, console, WAF, 1)
    await db_session.commit()
    done = _run(db_schema, "export")
    assert (done.returncode, done.stderr) == (0, "")
    document = json.loads(done.stdout)
    assert list(document["sections"]) == list(SECTION_ORDER)
    assert [len(document["sections"][name]) for name in PROFILES] == [0, 0, 0]


async def test_waf_profile_exports_the_mapping_and_not_the_validation_state(
    db_schema: str, db_session: AsyncSession
) -> None:
    console = await _console(db_session)
    source = await _source(db_session, console, WAF, 1)
    db_session.add(WafSourceProfile(
        instance_id=console.id, log_source_id=source.id, enabled=True, profile_version=7,
        product_label="F5 ASM", url_property="URL", method_property="Method",
        action_property="Action", response_code_property="ResponseCode",
        action_value_map={"BLOCKED": ["blocked"], "ALLOWED": ["allowed"]},
        validation_status="VALID", validated_profile_version=7,
        validated_at=datetime.now(UTC), validation_report={"checks": ["REPORT-SENTINEL"]},
        validation_requested_by="old-operator", updated_by="old-operator",
    ))
    await db_session.commit()
    done = _run(db_schema, "export", "--actor", "soc-lead")
    assert (done.returncode, done.stderr) == (0, "")
    rows = _section(done.stdout, "waf_source_profiles")
    assert rows == [WAF_ROW] and list(rows[0]) == list(WAF_ROW)
    assert not any(word in done.stdout for word in FORBIDDEN)
    audits = list((await db_session.scalars(select(AuditLog))).all())
    assert len(audits) == 1 and audits[0].action == "config.export"
    counts = audits[0].detail["counts"]
    assert {name: counts[name] for name in PROFILES} == {
        "waf_source_profiles": 1, "ips_source_profiles": 0, "identity_source_profiles": 0,
    } and set(counts) == set(SECTION_ORDER)


async def test_ips_profile_keeps_the_documented_empty_cisco_ftd_action_map(
    db_schema: str, db_session: AsyncSession
) -> None:
    """The default preset has an unmapped ACTION and `{}`, not two expanded empty lists."""
    console = await _console(db_session)
    source = await _source(db_session, console, IPS, 1)
    db_session.add(IpsSourceProfile(
        instance_id=console.id, log_source_id=source.id, enabled=True, profile_version=3,
        product_label="FTD", preset="CISCO_FTD", mapping=FTD_MAPPING, action_value_map={},
        validation_status="INVALID", validated_profile_version=2,
        validation_report={"reason": "REPORT-SENTINEL"},
    ))
    await db_session.commit()
    done = _run(db_schema, "export", "--instance", NAME)
    assert (done.returncode, done.stderr) == (0, "")
    assert _section(done.stdout, "ips_source_profiles") == [{
        "log_source": IPS, "enabled_on_source": True, "product_label": "FTD",
        "preset": "CISCO_FTD", "mapping": FTD_MAPPING, "action_value_map": {},
    }]
    assert not any(word in done.stdout for word in FORBIDDEN)


async def test_identity_profile_exports_its_enabled_event_ids(
    db_schema: str, db_session: AsyncSession
) -> None:
    """A disabled identity profile still reports the catalog ids it was told to collect."""
    console = await _console(db_session)
    source = await _source(db_session, console, WINDOWS, 1)
    db_session.add(IdentitySourceProfile(
        instance_id=console.id, log_source_id=source.id, enabled=False, profile_version=2,
        product_label="Windows", event_id_property="EventID", username_property="username",
        enabled_event_ids=[4625, 4740, 4720], validation_status="VALID",
        validated_profile_version=2, validation_report={"checks": ["REPORT-SENTINEL"]},
    ))
    await db_session.commit()
    done = _run(db_schema, "export")
    assert (done.returncode, done.stderr) == (0, "")
    assert _section(done.stdout, "identity_source_profiles") == [{
        "log_source": WINDOWS, "enabled_on_source": False, "product_label": "Windows",
        "event_id_property": "EventID", "username_property": "username",
        "logon_type_property": None, "enabled_event_ids": [4625, 4740, 4720],
    }]
    assert not any(word in done.stdout for word in FORBIDDEN)


async def test_a_profile_with_every_optional_field_null_exports_nulls(
    db_schema: str, db_session: AsyncSession
) -> None:
    """A null mapping is a null mapping: the file does not invent an empty string or a default."""
    console = await _console(db_session)
    waf = await _source(db_session, console, WAF, 1)
    ips = await _source(db_session, console, IPS, 2)
    windows = await _source(db_session, console, WINDOWS, 3)
    db_session.add_all([
        WafSourceProfile(instance_id=console.id, log_source_id=waf.id, url_property="URL",
                         method_property="Method", action_value_map={}),
        IpsSourceProfile(instance_id=console.id, log_source_id=ips.id, mapping={},
                         action_value_map={}),
        IdentitySourceProfile(instance_id=console.id, log_source_id=windows.id,
                              event_id_property="qid", username_property="username",
                              enabled_event_ids=[]),
    ])
    await db_session.commit()
    done = _run(db_schema, "export")
    assert (done.returncode, done.stderr) == (0, "")
    assert _section(done.stdout, "waf_source_profiles") == [{
        "log_source": WAF, "enabled_on_source": False, "product_label": None,
        "url_property": "URL", "method_property": "Method", "virtual_host_property": None,
        "action_property": None, "response_code_property": None, "rule_id_property": None,
        "user_agent_property": None, "action_value_map": {},
    }]
    assert _section(done.stdout, "ips_source_profiles") == [{
        "log_source": IPS, "enabled_on_source": False, "product_label": None, "preset": None,
        "mapping": {}, "action_value_map": {},
    }]
    assert _section(done.stdout, "identity_source_profiles") == [{
        "log_source": WINDOWS, "enabled_on_source": False, "product_label": None,
        "event_id_property": "qid", "username_property": "username",
        "logon_type_property": None, "enabled_event_ids": [],
    }]


def _tied_profile(
    family: str, console: QRadarInstance, source: LogSource, label: str
) -> WafSourceProfile | IpsSourceProfile | IdentitySourceProfile:
    """A minimal profile whose only distinguishing field is `product_label`."""
    if family == "waf_source_profiles":
        return WafSourceProfile(
            instance_id=console.id, log_source_id=source.id, product_label=label,
            url_property="URL", method_property="Method", action_value_map={},
        )
    if family == "ips_source_profiles":
        return IpsSourceProfile(
            instance_id=console.id, log_source_id=source.id, product_label=label,
            mapping={}, action_value_map={},
        )
    return IdentitySourceProfile(
        instance_id=console.id, log_source_id=source.id, product_label=label,
        event_id_property="qid", username_property="username", enabled_event_ids=[4625],
    )


def _tied_row(family: str, ref: dict[str, str], label: str) -> dict[str, object]:
    """The exact row `_tied_profile` exports, so the assertion pins its position, not its set."""
    common = {"log_source": ref, "enabled_on_source": False, "product_label": label}
    if family == "waf_source_profiles":
        return {**common, "url_property": "URL", "method_property": "Method",
                "virtual_host_property": None, "action_property": None,
                "response_code_property": None, "rule_id_property": None,
                "user_agent_property": None, "action_value_map": {}}
    if family == "ips_source_profiles":
        return {**common, "preset": None, "mapping": {}, "action_value_map": {}}
    return {**common, "event_id_property": "qid", "username_property": "username",
            "logon_type_property": None, "enabled_event_ids": [4625]}


@pytest.mark.parametrize("family", PROFILES)
async def test_tied_log_sources_are_ordered_by_qradar_id(
    family: str, db_schema: str, db_session: AsyncSession
) -> None:
    """Two sources sharing name and type keep a stable order, so two exports stay identical.

    The rows differ only by `product_label`, and they are inserted opposite the expected order:
    without the QRadar id (then UUID) tie-breaker the database returns them the other way round
    and the exact-row assertion fails.
    """
    console = await _console(db_session)
    tied = {"name": "Tied Edge", "type_name": WAF["type_name"]}
    later = await _source(db_session, console, tied, 20)
    earlier = await _source(db_session, console, tied, 10)
    db_session.add_all([
        _tied_profile(family, console, later, "created-later"),
        _tied_profile(family, console, earlier, "created-earlier"),
    ])
    await db_session.commit()
    done = _run(db_schema, "export")
    assert (done.returncode, done.stderr) == (0, "")
    assert _section(done.stdout, family) == [
        _tied_row(family, tied, "created-earlier"),
        _tied_row(family, tied, "created-later"),
    ]


async def test_tied_log_sources_are_ordered_by_qradar_id_in_log_source_settings(
    db_schema: str, db_session: AsyncSession
) -> None:
    """The pre-existing 1c section uses the same tie-breaker, not only the profile sections."""
    console = await _console(db_session)
    tied = {"name": "Tied Linux", "type_name": "Linux OS"}
    await _source(db_session, console, tied, 20, owner="created-later")
    await _source(db_session, console, tied, 10, owner="created-earlier")
    await db_session.commit()
    done = _run(db_schema, "export")
    assert (done.returncode, done.stderr) == (0, "")
    rows = _section(done.stdout, "log_source_settings")
    assert [row["log_source"] for row in rows] == [tied, tied]
    assert [row["owner"] for row in rows] == ["created-earlier", "created-later"]


async def test_only_the_selected_console_and_the_log_source_order_reach_the_file(
    db_schema: str, db_session: AsyncSession
) -> None:
    """Rows follow the log source's natural key, and the other console's profile is not read."""
    lab = await _console(db_session, "lab")
    prod = await _console(db_session, NAME)
    edge = await _source(db_session, prod, WAF, 1)
    alpha = await _source(db_session, prod, _waf_type("Alpha WAF"), 2)
    elsewhere = await _source(db_session, lab, _waf_type("Zulu WAF"), 3)
    for console, source in ((prod, edge), (prod, alpha), (lab, elsewhere)):
        db_session.add(WafSourceProfile(
            instance_id=console.id, log_source_id=source.id, url_property="URL",
            method_property="Method", action_value_map={},
        ))
    await db_session.commit()
    done = _run(db_schema, "export", "--instance", NAME)
    assert (done.returncode, done.stderr) == (0, "")
    rows = _section(done.stdout, "waf_source_profiles")
    assert [row["log_source"] for row in rows] == [
        {"name": "Alpha WAF", "type_name": WAF["type_name"]}, WAF,
    ]
    assert secret_key_paths(json.loads(done.stdout)) == []
    # Two exports of one unchanged database differ only in the exported_at line.
    again = _run(db_schema, "export", "--instance", NAME)
    first, second = json.loads(done.stdout), json.loads(again.stdout)
    first.pop("exported_at"), second.pop("exported_at")
    assert first == second
