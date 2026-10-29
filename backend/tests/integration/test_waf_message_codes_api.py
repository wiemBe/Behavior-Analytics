"""MSG-6: what the WAF anomaly API serves beside each English sentence.

The detail carries `explanation_code` / `explanation_params` and, inside the typed detection
block, `reason_*` and `caveat_*`; the unified list carries `explanation_*` per item. The English
never changes, and a row stored before codes serves its text with no code.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.models.enums import WafAnomalyType
from tests.integration.test_fleet_api import Client, run
from tests.integration.test_waf_analyst_api import READER, waf, world
from tests.unit.waf_message_scenarios import SCENARIOS

pytestmark = pytest.mark.integration


def coded_row(name: str = "ratio.block.fired") -> dict[str, Any]:
    """The columns a row written by the engine holds for one scenario's verdict."""
    evidence = SCENARIOS[name]().evidence
    assert evidence.reason_message is not None
    return {
        "detector_type": WafAnomalyType.WAF_BLOCK_RATIO_SPIKE,
        "details": evidence.to_dict(),
        "explanation": evidence.reason,
        "explanation_i18n": evidence.reason_message.to_wire(),
    }


def seed(rows: list[dict[str, Any]]):  # type: ignore[no-untyped-def]
    async def setup(s):  # type: ignore[no-untyped-def]
        _inst, _fw, _ws, ep = await world(s, a_rows=1, w_rows=0)
        made = [waf(ep.id, 2 * i + 1, **row) for i, row in enumerate(rows)]
        s.add_all(made)
        await s.flush()
        return [row.id for row in made]

    return run(setup)


def test_the_detail_serves_the_explanation_and_detection_codes(db_schema) -> None:
    row = coded_row()
    (anomaly_id,) = seed([row])
    body = Client(READER).get(f"/api/v1/anomalies/{anomaly_id}").json()
    assert body["explanation"] == row["explanation"]
    assert body["explanation_code"] == "waf.ratio.fired"
    params = body["explanation_params"]
    assert params["count"] == 40 and params["total"] == 100
    assert params["numerator"] == {"code": "waf.noun.blocked_requests", "params": {}}
    assert params["share"] == {"kind": "percent", "ratio": 0.4, "precision": 1}
    detection = body["detection"]
    assert detection["reason"] == row["explanation"]
    assert detection["reason_code"] == "waf.ratio.fired"
    assert detection["reason_params"] == params
    assert detection["caveat_code"] == "waf.caveat.block_ratio"
    assert detection["caveat_params"] == {}
    assert detection["caveat"].startswith("Blocking is judged through the profile's action map")


def test_the_lower_bound_wrap_reaches_the_client_nested(db_schema) -> None:
    row = coded_row("rate.lower_z_not_reached")
    row["detector_type"] = WafAnomalyType.WAF_REQUEST_RATE_SPIKE
    (anomaly_id,) = seed([row])
    detection = Client(READER).get(f"/api/v1/anomalies/{anomaly_id}").json()["detection"]
    assert detection["reason_code"] == "waf.rate.not_proven_lower_bound"
    inner = detection["reason_params"]["reason"]
    assert inner["code"] == "waf.rate.z_not_reached"
    assert inner["params"]["score"] == pytest.approx(2.53, abs=0.01)
    assert inner["params"]["threshold"] == 3.5
    assert detection["reason"].startswith("at least 250 requests were attributed")


def test_the_response_forwards_no_contributor_list_or_raw_value(db_schema) -> None:
    (anomaly_id,) = seed([coded_row("ratio.block.fired")])
    response = Client(READER).get(f"/api/v1/anomalies/{anomaly_id}")
    for forbidden in ("contributors", "source_ip", "203.0.113", "numerator_label"):
        assert forbidden not in response.text, forbidden


def test_the_list_serves_each_item_its_own_code(db_schema) -> None:
    ids = seed([coded_row("ratio.block.fired"), coded_row("rate.fired")])
    items = Client(READER).get("/api/v1/anomalies?limit=50").json()["items"]
    by_id = {item["id"]: item for item in items}
    first, second = (by_id[str(i)] for i in ids)
    assert first["explanation_code"] == "waf.ratio.fired"
    assert second["explanation_code"] == "waf.rate.fired"
    assert first["explanation_params"]["count"] == 40
    assert first["explanation"].startswith("40 of 100 requests carrying a WAF action")
    phase_a = next(item for item in items if item["subject_kind"] == "LOG_SOURCE")
    assert phase_a["explanation_code"] is None and phase_a["explanation_params"] is None


def test_a_row_stored_before_codes_serves_its_text_and_no_code(db_schema) -> None:
    row = coded_row()
    row["explanation_i18n"] = None
    row["details"] = copy.deepcopy(row["details"])
    for key in ("reason_code", "reason_params"):
        del row["details"][key]
    for key in ("caveat_code", "caveat_params"):
        del row["details"]["extra"][key]
    (anomaly_id,) = seed([row])
    client = Client(READER)
    body = client.get(f"/api/v1/anomalies/{anomaly_id}").json()
    assert body["explanation"] == row["explanation"]
    assert body["explanation_code"] is None and body["explanation_params"] is None
    detection = body["detection"]
    assert detection["reason"] == row["explanation"] and detection["reason_code"] is None
    assert detection["caveat"] and detection["caveat_code"] is None
    items = client.get("/api/v1/anomalies?limit=50").json()["items"]
    item = next(i for i in items if i["id"] == str(anomaly_id))
    assert item["explanation_code"] is None and item["explanation_params"] is None


@pytest.mark.parametrize(
    "broken",
    [{"code": 5, "params": {}}, {"code": "waf.rate.fired"}, "waf.rate.fired", []],
    ids=("code-not-text", "no-params", "bare-text", "list"),
)
def test_a_malformed_stored_reference_is_dropped_never_served(db_schema, broken) -> None:
    row = coded_row()
    row["explanation_i18n"] = broken
    (anomaly_id,) = seed([row])
    client = Client(READER)
    body = client.get(f"/api/v1/anomalies/{anomaly_id}").json()
    assert body["explanation"] == row["explanation"]
    assert body["explanation_code"] is None and body["explanation_params"] is None
    items = client.get("/api/v1/anomalies?limit=50").json()["items"]
    listed = next(i for i in items if i["id"] == str(anomaly_id))
    assert listed["explanation_code"] is None


def test_the_list_statement_count_does_not_grow_with_the_coded_rows(db_schema) -> None:
    seed([coded_row() for _ in range(2)])
    small = Client(READER)
    small.get("/api/v1/anomalies?limit=50")
    run(lambda s: _more(s))
    large = Client(READER)
    large.get("/api/v1/anomalies?limit=50")
    assert large.statements == small.statements


async def _more(s):  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from app.models.waf import WafEndpoint

    endpoint = await s.scalar(select(WafEndpoint))
    row = coded_row()
    s.add_all([waf(endpoint.id, 40 + i, **row) for i in range(6)])
    await s.flush()
