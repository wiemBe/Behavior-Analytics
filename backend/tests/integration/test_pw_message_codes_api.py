"""MSG-4b: what the property-watch API serves beside each English sentence.

A refusal carries `reason_codes` parallel to `reasons` (or `detail_code` and `detail_params`);
a read carries `<field>_code` and `<field>_params`. The English never changes, and a row stored
before codes serves its text with no code.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import update

from app.history.planner import Gap
from app.models.enums import PwHistoryState
from app.models.fleet import LogSourceGroup
from app.models.property_watch import PropertyWatch
from app.property_watch.bootstrap import history_columns
from tests.integration.pw_engine_support import (
    SPIKE,
    T0,
    add_cells,
    make_rig,
    plan_range,
)
from tests.integration.test_property_watches_api import (
    ACTION,
    GROUP_ID,
    PREFIX,
    USER,
    _body,
    _client,
    _create,
    _principal,
    _seed,
)
from tests.integration.test_pw_history_api import (
    ENGINEER,
    _engineer,
    _progress,
    _row,
    _set,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the property-watch message code tests",
    ),
]


def _codes(response: Any) -> list[tuple[str, dict[str, Any]]]:
    detail = response.json()["detail"]
    return [(item["code"], item["params"]) for item in detail["reason_codes"]]


# ------------------------------------------------------------------- the 422 and 409
async def test_a_refused_draft_names_a_code_for_each_reason_in_the_same_order(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    refused = _engineer(db_schema).post(
        PREFIX,
        json=_body(
            "bad",
            properties=[ACTION, USER],
            pinned_values=[["only-one"], [""]],
            detectors=[],
            history_days=99,
        ),
    )
    assert refused.status_code == 422, refused.text
    detail = refused.json()["detail"]
    assert detail["reasons"] == [
        "pinned value 1: needs 2 string(s), one per property",
        "pinned value 2: needs 2 string(s), one per property",
        "pinned value 2, part 1: a pinned value cannot be empty",
        "a watch evaluates at least one detector",
        "history_days must be 0 (no history) or between 7 and 28, not 99",
    ]
    assert detail["message"] == "; ".join(detail["reasons"])
    assert _codes(refused) == [
        ("pw.draft.pin_parts", {"index": 1, "count": 2}),
        ("pw.draft.pin_parts", {"index": 2, "count": 2}),
        (
            "pw.draft.pin_invalid",
            {
                "index": 2,
                "part": 1,
                "reason": {"code": "pw.reference.pin_empty", "params": {}},
            },
        ),
        ("pw.draft.detector_required", {}),
        ("pw.draft.history_days_range", {"low": 7, "high": 28, "days": 99}),
    ]


async def test_the_scope_and_instance_reasons_carry_codes(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    missing = uuid.uuid4()
    refused = _engineer(db_schema).post(
        PREFIX,
        json=_body(
            "ghost", scope_kind="LOG_SOURCE", scope_type_id=None, log_source_id=str(missing)
        ),
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["reasons"] == [f"log source {missing} does not exist"]
    assert _codes(refused) == [("pw.draft.log_source_missing", {"id": str(missing)})]
    gone = _engineer(db_schema).post(
        PREFIX, json=_body("no-type", scope_type_id=999_999)
    )
    assert gone.status_code == 422
    assert _codes(gone) == [("pw.draft.type_missing", {"id": 999_999})]
    group = _engineer(db_schema).post(
        PREFIX, json=_body("no-group", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                           scope_group_id=424_242)
    )
    assert _codes(group) == [("pw.draft.group_missing", {"id": 424_242})]


async def test_an_explicit_null_field_is_refused_with_its_name_as_a_parameter(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "nulled", history_days=0)
    refused = client.patch(
        f"{PREFIX}/{made['id']}",
        json={"expected_updated_at": made["updated_at"], "severity": None},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["reasons"] == ["severity must not be null"]
    assert _codes(refused) == [("pw.draft.field_null", {"name": "severity"})]


async def test_the_history_request_refuses_with_codes(db_session, db_schema: str) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "asked", history_days=0)
    refused = client.post(f"{PREFIX}/{made['id']}/history", json={"history_days": 3})
    assert refused.status_code == 422
    assert _codes(refused) == [("pw.draft.history_days_range", {"low": 7, "high": 28, "days": 3})]

    await _set(db_session, made["id"], history_state=PwHistoryState.READING.value)
    blocked = client.post(f"{PREFIX}/{made['id']}/history", json={"history_days": 14})
    assert blocked.status_code == 409, blocked.text
    body = blocked.json()
    assert body["detail"] == (
        "history is READING on this watch and cannot be requested again; "
        "create a new watch to read history"
    )
    assert body["detail_code"] == "pw.history_change.already_requested"
    assert body["detail_params"] == {"state": "READING"}


async def test_a_watch_that_already_collected_names_which_marks_are_set(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "collected", history_days=0)
    await _set(db_session, made["id"], collected_through=T0, evaluated_through=T0)
    blocked = client.post(f"{PREFIX}/{made['id']}/history", json={"history_days": 14})
    assert blocked.status_code == 409
    body = blocked.json()
    assert body["detail"] == (
        "this watch has already collected (collected_through and evaluated_through set); "
        "create a new watch to read history"
    )
    assert body["detail_code"] == "pw.history_change.already_collected"
    assert body["detail_params"] == {
        "marks": {"kind": "list", "items": ["collected_through", "evaluated_through"]}
    }


# --------------------------------------------------------------- reads with a stored code
async def _stale_group_watch(db_session, db_schema: str):  # type: ignore[no-untyped-def]
    seeded = await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(
        client, "stale", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None, scope_group_id=GROUP_ID
    )
    synced = seeded.now - timedelta(hours=30)
    await db_session.execute(update(LogSourceGroup).values(synced_at=synced))
    await db_session.commit()
    return client, made, synced


async def test_the_scope_read_and_the_picker_carry_the_stale_inventory_code(
    db_session, db_schema: str
) -> None:
    client, made, synced = await _stale_group_watch(db_session, db_schema)
    scope = client.get(f"{PREFIX}/{made['id']}/scope").json()
    assert scope["state"] == "SCOPE_STALE"
    assert scope["reason"] == (
        f"the group inventory was last synced at {synced.isoformat()}, older than 1 day, 0:00:00"
    )
    assert scope["reason_code"] == "pw.scope.group_stale"
    assert scope["reason_params"]["limit"] == {"kind": "duration", "seconds": 86400.0}
    assert scope["reason_params"]["synced"] == synced.isoformat()

    options = client.get(f"{PREFIX}/scope-options", params={"kind": "LOG_SOURCE_GROUP"}).json()
    edge = next(item for item in options if item["qradar_id"] == GROUP_ID)
    assert edge["scope_state"] == "SCOPE_STALE"
    assert edge["scope_reason"] == scope["reason"]
    assert edge["scope_reason_code"] == "pw.scope.group_stale"


async def test_the_preview_and_the_aql_say_why_with_a_code(db_session, db_schema: str) -> None:
    client, made, _ = await _stale_group_watch(db_session, db_schema)
    refused = client.post(f"{PREFIX}/{made['id']}/preview")
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["detail"].startswith("SCOPE_STALE: the group inventory was last synced at ")
    assert body["detail_code"] == "pw.preview.scope_unresolved"
    assert body["detail_params"]["status"] == "SCOPE_STALE"
    assert body["detail_params"]["reason"]["code"] == "pw.scope.group_stale"

    aql = client.get(f"{PREFIX}/{made['id']}/aql").json()
    assert aql["current"] == [] and aql["current_unavailable"] == body["detail"]
    assert aql["current_unavailable_code"] == "pw.preview.scope_unresolved"
    assert aql["current_unavailable_params"] == body["detail_params"]


async def test_the_history_block_carries_codes_for_its_reason_and_for_a_coded_gap(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "gaps", history_days=7)
    await _set(db_session, made["id"], history_state=PwHistoryState.READING.value)
    started = client.get(f"{PREFIX}/{made['id']}").json()["history"]
    assert started["reason"] == "the reading starts at the next collection"
    assert started["reason_code"] == "pw.history.starts_next"
    assert started["reason_params"] == {}

    progress = _progress(576)
    floor = progress.floor
    assert floor is not None
    progress = replace(
        progress,
        gaps=(
            Gap(
                floor + timedelta(minutes=50),
                floor + timedelta(minutes=70),
                "search failed 3 times at the minimum width",
                "history.gap.search_failed",
                {"failures": 3},
            ),
            Gap(
                floor + timedelta(hours=4),
                floor + timedelta(hours=5),
                "could not be read after 3 attempts",
            ),
        ),
    )
    await _set(
        db_session,
        made["id"],
        **history_columns(progress, state=PwHistoryState.READING, reason="reading 576 buckets"),
    )
    history = client.get(f"{PREFIX}/{made['id']}").json()["history"]
    coded, old = history["gaps"]
    assert coded["reason"] == "search failed 3 times at the minimum width"
    assert coded["reason_code"] == "history.gap.search_failed"
    assert coded["reason_params"] == {"failures": 3}
    assert old["reason"] == "could not be read after 3 attempts"
    assert old["reason_code"] is None and old["reason_params"] is None
    # the planner's own reading reason is not this task's: it stays English with no code
    assert history["reason"] == "reading 576 buckets"
    assert history["reason_code"] is None


async def test_switched_off_and_unreadable_histories_carry_codes(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "off", history_days=7)
    await _set(db_session, made["id"], history_state=PwHistoryState.PARTIAL.value)
    off = client.get(f"{PREFIX}/{made['id']}").json()["history"]
    assert off["reason"] == "history reading is switched off"
    assert off["reason_code"] == "pw.history.switched_off"

    await _set(
        db_session,
        made["id"],
        history_state=PwHistoryState.READING.value,
        history_progress={"version": 99},
    )
    broken = client.get(f"{PREFIX}/{made['id']}").json()["history"]
    assert broken["progress_unreadable"] is True
    assert broken["reason"] == "the stored history progress could not be read back"
    assert broken["reason_code"] == "pw.history.unreadable"


async def test_a_disabled_watch_serves_its_status_with_the_actor_as_a_parameter(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "toggle", history_days=0)
    row = await _row(db_session, made["id"])
    row.enabled = True
    row.collected_through = T0
    row.evaluated_through = T0
    await db_session.commit()
    token = (await _row(db_session, made["id"])).updated_at.isoformat()
    off = client.patch(
        f"{PREFIX}/{made['id']}", json={"expected_updated_at": token, "enabled": False}
    )
    assert off.status_code == 200, off.text
    body = off.json()
    assert body["status"] == "DISABLED"
    assert body["status_reason"] == f"disabled by {ENGINEER}"
    assert body["status_reason_code"] == "pw.status.disabled_by"
    assert body["status_reason_params"] == {"actor": ENGINEER}

    back = client.patch(
        f"{PREFIX}/{made['id']}", json={"expected_updated_at": body["updated_at"], "enabled": True}
    )
    assert back.status_code == 200, back.text
    assert back.json()["status_reason"] is None
    assert back.json()["status_reason_code"] is None
    assert (await _row(db_session, made["id"])).last_status_reason_i18n is None


async def test_an_old_row_serves_its_text_with_no_code(db_session, db_schema: str) -> None:
    await _seed(db_session)
    client = _engineer(db_schema)
    made = _create(client, "old", history_days=0)
    await db_session.execute(
        update(PropertyWatch)
        .where(PropertyWatch.id == uuid.UUID(made["id"]))
        .values(
            last_status="STALE",
            last_status_reason="written before codes",
            last_status_reason_i18n={"code": 5, "params": []},
        )
    )
    await db_session.commit()
    read = client.get(f"{PREFIX}/{made['id']}").json()
    assert read["status_reason"] == "written before codes"
    # a stored value that is not a well-formed reference is never served as one
    assert read["status_reason_code"] is None and read["status_reason_params"] is None


# ---------------------------------------------------------------- the anomaly reads
async def test_the_anomaly_reads_carry_the_code_of_the_explanation_and_the_transitions(
    db_session, db_schema: str
) -> None:
    rig = await make_rig(db_session)
    await add_cells(db_session, await rig.value("alice"))
    plan_range(rig.plan, 1, 3, {"alice": 500, "bob": 400})
    await rig.ticks(0, 3)
    [anomaly] = await rig.anomalies(SPIKE)
    viewer = _client(db_schema, _principal("viewer"))

    listed = viewer.get(
        "/api/v1/anomalies",
        params={"since": (T0 - timedelta(days=1)).isoformat(),
                "until": (T0 + timedelta(days=1)).isoformat(),
                "subject_kind": "PW_VALUE"},
    ).json()["items"]
    [item] = [row for row in listed if row["id"] == str(anomaly.id)]
    assert item["explanation"] == anomaly.explanation
    assert item["explanation_code"] == "pw.detector.spike_fired"
    assert item["explanation_params"]["observed"] == 500.0
    assert item["explanation_params"]["basis"]["code"] == "pw.detector.basis_z_high"

    detail = viewer.get(f"/api/v1/anomalies/{anomaly.id}").json()
    assert detail["explanation_code"] == "pw.detector.spike_fired"
    assert detail["detection"]["reason"] == anomaly.explanation
    assert detail["detection"]["reason_code"] == "pw.detector.spike_fired"
    assert detail["detection"]["reason_params"] == detail["explanation_params"]
    # the lifecycle's own transition reasons carry their code (Family 2, MSG-5)
    assert detail["transitions"]
    assert all(
        t["reason_code"] is not None and t["reason_code"].startswith("anomaly.lifecycle.")
        for t in detail["transitions"]
    )


async def test_a_resolved_transition_is_served_with_its_code(db_session, db_schema: str) -> None:
    rig = await make_rig(db_session)
    await add_cells(db_session, await rig.value("alice"))
    plan_range(rig.plan, 1, 3, {"alice": 500, "bob": 400})
    await rig.ticks(0, 3)
    [anomaly] = await rig.anomalies(SPIKE)
    await db_session.execute(
        update(PropertyWatch).where(PropertyWatch.id == rig.watch.id).values(watch_version=2)
    )
    await db_session.commit()
    plan_range(rig.plan, 3, 6, {"alice": 500, "bob": 400})
    await rig.tick(3)
    viewer = _client(db_schema, _principal("viewer"))
    detail = viewer.get(f"/api/v1/anomalies/{anomaly.id}").json()
    last = detail["transitions"][-1]
    assert last["to_state"] == "RESOLVED"
    assert last["reason"] == "watch definition changed"
    assert last["reason_code"] == "pw.transition.definition_changed"
    assert last["reason_params"] == {}
