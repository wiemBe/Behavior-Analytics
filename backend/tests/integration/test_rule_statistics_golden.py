"""`RuleStatisticsService.compute` against a response captured before SOC-6d1.

SOC-6d1 adds a read beside `/rules/statistics` and must not change that page's payload
(design §8). The fixture was captured from the unchanged tree; the ids are replaced by
stable labels so it does not depend on the uuids a run generates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.integration.factories import make_instance
from tests.integration.test_rule_statistics import (
    add_match,
    add_rule,
    add_windows,
    stats,
)

pytestmark = pytest.mark.integration

GOLDEN = Path(__file__).parent / "golden" / "rule_statistics_pre_6d1.json"


def _labelled(value, labels: dict[str, str]):
    """`value` with every known id replaced by its label (recursively)."""
    if isinstance(value, dict):
        return {k: _labelled(v, labels) for k, v in value.items()}
    if isinstance(value, list):
        return [_labelled(v, labels) for v in value]
    if isinstance(value, str):
        return labels.get(value, value)
    return value


@pytest.mark.asyncio
async def test_rule_statistics_response_equals_the_pre_change_fixture(db_session) -> None:
    inst = await make_instance(db_session)
    quiet = await add_rule(db_session, inst, 1, name="quiet", capacity=4.0)
    loud = await add_rule(db_session, inst, 2, name="loud", capacity=2.0)
    steady = await add_rule(db_session, inst, 3, name="steady", rule_type="COMMON")
    await add_rule(db_session, inst, 4, name="off", enabled=False, bb=True, origin="SYSTEM")
    await add_windows(db_session, inst)
    await add_match(db_session, loud, hours_back=1, records=3, events=7200)
    await add_match(db_session, loud, hours_back=5, records=2, events=100)
    await add_match(db_session, steady, hours_back=2, records=1, events=9)

    result = await stats(db_session, inst, days=1, top=5)

    labels = {
        str(inst.id): "instance",
        str(quiet.id): "rule-1",
        str(loud.id): "rule-2",
        str(steady.id): "rule-3",
    }
    payload = _labelled(json.loads(result.model_dump_json()), labels)
    assert payload == json.loads(GOLDEN.read_text())
