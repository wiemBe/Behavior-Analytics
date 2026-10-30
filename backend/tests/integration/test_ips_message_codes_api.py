"""MSG-6b: the IPS anomaly reads expose stored sentence references additively."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from sqlalchemy import select

from app.i18n.messages import MessageRef, render
from app.models.enums import IpsAnomalyType
from app.models.ips import IpsAnomaly, IpsEntity
from tests.integration.test_fleet_api import Client, run
from tests.integration.test_ips_analyst_api import READER, ips_world
from tests.unit.ips_message_scenarios import RAW

pytestmark = pytest.mark.integration

#: What the engine writes when it suppresses an anomalous verdict in a maintenance window.
#: The IPS engine stores the WAF sentence (MSG-6b).
_MAINTENANCE = MessageRef("waf.suppression.maintenance_window")


def coded_row(name: str) -> dict[str, Any]:
    verdict = RAW[name]()
    evidence = verdict.evidence  # type: ignore[union-attr]
    assert evidence.reason_message is not None
    return {
        "details": evidence.to_dict(),
        "explanation": evidence.reason,
        "explanation_i18n": evidence.reason_message.to_wire(),
    }


def seed(*, entity_row: dict[str, Any], source_row: dict[str, Any] | None = None):  # type: ignore[no-untyped-def]
    async def setup(session):  # type: ignore[no-untyped-def]
        made = await ips_world(session)
        entity = await session.get(IpsAnomaly, made["a_open"])
        source = await session.get(IpsAnomaly, made["a_source"])
        assert entity is not None and source is not None
        for key, value in entity_row.items():
            setattr(entity, key, value)
        if source_row is not None:
            for key, value in source_row.items():
                setattr(source, key, value)
        await session.flush()
        return entity.id, source.id

    return run(setup)


def test_the_detail_serves_the_explanation_reason_and_caveat_codes(db_schema) -> None:
    row = coded_row("spike.destination_port.lower_fired")
    entity_id, _source_id = seed(entity_row=row)

    body = Client(READER).get(f"/api/v1/anomalies/{entity_id}").json()
    assert body["explanation"] == row["explanation"]
    assert body["explanation_code"] == "ips.spike.fired_lower"
    params = body["explanation_params"]
    assert params["observed"] == 100.0
    assert params["scope"]["code"] == "ips.scope.role"
    assert params["scope"]["params"]["reason"]["code"] == "waf.scope.lower_bound_increase"
    detection = body["detection"]
    assert detection["reason"] == row["explanation"]
    assert detection["reason_code"] == "ips.spike.fired_lower"
    assert detection["reason_params"] == params
    assert detection["caveat_code"] == "ips.caveat.port_rate"
    assert detection["caveat_params"] == {}


def test_the_list_serves_each_ips_item_its_own_code(db_schema) -> None:
    entity_row = coded_row("spike.destination_port.fired")
    source_row = coded_row("conc.fired")
    entity_id, source_id = seed(entity_row=entity_row, source_row=source_row)

    items = Client(READER).get("/api/v1/anomalies?limit=200").json()["items"]
    by_id = {item["id"]: item for item in items}
    assert by_id[str(entity_id)]["explanation_code"] == "ips.spike.fired"
    assert by_id[str(source_id)]["explanation_code"] == "ips.concentration.fired"
    assert by_id[str(entity_id)]["explanation_params"]["observed"] == 100.0
    assert by_id[str(source_id)]["explanation_params"]["top"] == 70


def test_a_row_stored_before_codes_serves_its_text_and_no_code(db_schema) -> None:
    row = coded_row("spike.destination_port.fired")
    row["explanation_i18n"] = None
    row["details"] = copy.deepcopy(row["details"])
    for key in ("reason_code", "reason_params"):
        del row["details"][key]
    for key in ("caveat_code", "caveat_params"):
        del row["details"]["extra"][key]
    entity_id, _source_id = seed(entity_row=row)

    client = Client(READER)
    body = client.get(f"/api/v1/anomalies/{entity_id}").json()
    assert body["explanation"] == row["explanation"]
    assert body["explanation_code"] is None and body["explanation_params"] is None
    assert body["detection"]["reason_code"] is None
    assert body["detection"]["caveat_code"] is None
    items = client.get("/api/v1/anomalies?limit=200").json()["items"]
    listed = next(item for item in items if item["id"] == str(entity_id))
    assert listed["explanation_code"] is None and listed["explanation_params"] is None


@pytest.mark.parametrize(
    "broken",
    ({"code": 5, "params": {}}, {"code": "ips.spike.fired"}, "ips.spike.fired", []),
    ids=("code-not-text", "no-params", "bare-text", "list"),
)
def test_a_malformed_stored_reference_is_dropped_never_served(db_schema, broken) -> None:
    row = coded_row("spike.destination_port.fired")
    row["explanation_i18n"] = broken
    entity_id, _source_id = seed(entity_row=row)

    client = Client(READER)
    body = client.get(f"/api/v1/anomalies/{entity_id}").json()
    assert body["explanation"] == row["explanation"]
    assert body["explanation_code"] is None and body["explanation_params"] is None
    items = client.get("/api/v1/anomalies?limit=200").json()["items"]
    listed = next(item for item in items if item["id"] == str(entity_id))
    assert listed["explanation_code"] is None


def test_the_response_forwards_no_contributor_block_or_new_port_claim(db_schema) -> None:
    row = coded_row("port.fired")
    row["detector_type"] = IpsAnomalyType.IPS_NEW_DESTINATION_PORT
    entity_id, _source_id = seed(entity_row=row)

    response = Client(READER).get(f"/api/v1/anomalies/{entity_id}")
    for forbidden in (
        '"contributors"',
        '"source_ip"',
        "192.0.2.4",
        "below 3 events in every bucket of the history window",
    ):
        assert forbidden not in response.text, forbidden


def suppressed_row(name: str) -> dict[str, Any]:
    """The columns a row the engine suppressed in a maintenance window holds."""
    return {
        **coded_row(name),
        "suppressed": True,
        "suppression_reason": render(_MAINTENANCE),
        "suppression_reason_i18n": _MAINTENANCE.to_wire(),
    }


def test_the_detail_serves_the_suppression_reason_and_its_code(db_schema) -> None:
    entity_row = suppressed_row("spike.destination_port.fired")
    source_row = suppressed_row("conc.fired")
    entity_id, source_id = seed(entity_row=entity_row, source_row=source_row)

    client = Client(READER)
    for anomaly_id, row in ((entity_id, entity_row), (source_id, source_row)):
        body = client.get(f"/api/v1/anomalies/{anomaly_id}").json()
        assert body["suppressed"] is True
        assert body["suppression_reason"] == row["suppression_reason"]
        assert body["suppression_reason_code"] == "waf.suppression.maintenance_window"
        assert body["suppression_reason_params"] == {}


def test_a_never_suppressed_row_serves_no_suppression_reason(db_schema) -> None:
    entity_id, _source_id = seed(entity_row=coded_row("spike.destination_port.fired"))

    body = Client(READER).get(f"/api/v1/anomalies/{entity_id}").json()
    assert body["suppressed"] is False
    assert body["suppression_reason"] is None
    assert body["suppression_reason_code"] is None
    assert body["suppression_reason_params"] is None


def test_an_old_suppressed_row_serves_its_text_and_no_code(db_schema) -> None:
    row = suppressed_row("spike.destination_port.fired")
    row["suppression_reason_i18n"] = None
    entity_id, _source_id = seed(entity_row=row)

    body = Client(READER).get(f"/api/v1/anomalies/{entity_id}").json()
    assert body["suppression_reason"] == row["suppression_reason"]
    assert body["suppression_reason_code"] is None
    assert body["suppression_reason_params"] is None


def test_the_list_serves_no_suppression_reason(db_schema) -> None:
    entity_id, _source_id = seed(entity_row=suppressed_row("spike.destination_port.fired"))

    listed = Client(READER).get("/api/v1/anomalies?limit=200").json()["items"]
    item = next(i for i in listed if i["id"] == str(entity_id))
    assert item["suppressed"] is True
    for field in ("suppression_reason", "suppression_reason_code", "suppression_reason_params"):
        assert field not in item


def test_the_list_statement_count_does_not_grow_with_coded_ips_rows(db_schema) -> None:
    seed(
        entity_row=coded_row("spike.destination_port.fired"),
        source_row=coded_row("conc.fired"),
    )
    small = Client(READER)
    small.get("/api/v1/anomalies?limit=200")
    run(_add_more)
    large = Client(READER)
    large.get("/api/v1/anomalies?limit=200")
    assert large.statements == small.statements


async def _add_more(session) -> None:  # type: ignore[no-untyped-def]
    row = coded_row("spike.destination_port.fired")
    original = await session.scalar(select(IpsAnomaly).where(IpsAnomaly.ips_entity_id.is_not(None)))
    entity = await session.scalar(select(IpsEntity))
    assert original is not None and entity is not None
    for index in range(6):
        clone = IpsAnomaly(
            subject_kind="IPS_ENTITY",
            ips_entity_id=entity.id,
            log_source_id=None,
            detector_type=original.detector_type,
            fingerprint=f"msg6b-extra-{index}",
            severity=original.severity,
            state=original.state,
            detected_at=original.detected_at,
            policy_version=original.policy_version,
            profile_version=original.profile_version,
            normalizer_version=original.normalizer_version,
            evidence_status=original.evidence_status,
            observed_measurement=original.observed_measurement,
            **row,
        )
        session.add(clone)
    await session.flush()
