"""PW-5c: the incident detail, `value_id`, the histogram, the AQL view and the values read.

Seeded through committed sessions and read back over HTTP, with the helpers of
`test_property_watches_api`. The value texts below are canaries: they must reach a `read:*`
caller (D-22) and never a log line.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from app.models.enums import AnomalyState, EvidenceStatus, Severity
from app.models.fleet import LogSourceGroup
from app.models.property_watch import (
    PwAnomaly,
    PwAnomalyTransition,
    PwBucket,
    PwValue,
    PwValueMetric,
)
from tests.integration.test_property_watches_api import (
    GROUP_ID,
    NIL,
    NOBODY,
    PREFIX,
    _bucket,
    _bucket_start,
    _client,
    _create,
    _principal,
    _seed,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the PW-5c integration tests",
    ),
]

ANOMALIES = "/api/v1/anomalies"
CANARY = "CanaryAlice-7f3a"
PIN_CANARY = "PinnedCanary-91c2"
DETAILS = {
    "reason": "events above the expected band",
    "baseline_low": 10.0,
    "baseline_high": 40.0,
    "threshold": 3.5,
    "sample_count": 12,
    "extra": {
        "observed_events": 120.0,
        "median": 20.0,
        "metric_semantics": "events per bucket",
        "watch_version": 1,
        "ratio": 6.0,
        "robust_z": 9.1,
        "robust_score_status": "OK",
        "internal_secret": "must-not-leave",
    },
}


def _incident(watch_id: str, value_id: uuid.UUID | None, **kw: object) -> PwAnomaly:
    start = datetime(2026, 10, 13, 8, 5, tzinfo=UTC)
    values: dict[str, object] = dict(
        watch_id=uuid.UUID(watch_id), pw_value_id=value_id, value_key=CANARY,
        detector_type="PW_VALUE_SPIKE", fingerprint=f"fp-{uuid.uuid4()}",
        severity=Severity.HIGH, state=AnomalyState.OPEN, detected_at=start,
        opened_at=start + timedelta(minutes=10), anomaly_start=start, watch_version=1,
        observed_value=120.0, expected_value=20.0, deviation_ratio=6.0, robust_z=9.1,
        absolute_delta=100.0, confidence=0.9, consecutive_buckets=3, policy_version=1,
        explanation="above the band", details=DETAILS,
        evidence_status=EvidenceStatus.NOT_REQUESTED, observed_measurement="EXACT",
    )
    values.update(kw)
    return PwAnomaly(**values)  # type: ignore[arg-type]


async def _world(db_session, db_schema):  # type: ignore[no-untyped-def]
    await _seed(db_session)
    writer = _client(db_schema, _principal("detection_engineer"))
    made = _create(writer, "gaps")
    watch_id = uuid.UUID(made["id"])
    value = PwValue(watch_id=watch_id, watch_version=1, value_key=CANARY,
                    tracked_since=_bucket_start(7))
    db_session.add(value)
    await db_session.flush()
    incident = _incident(made["id"], value.id)
    db_session.add(incident)
    await db_session.flush()
    t0 = incident.detected_at
    # Inserted out of order: the detail must read them oldest first.
    db_session.add_all([
        PwAnomalyTransition(
            pw_anomaly_id=incident.id, from_state="CANDIDATE", to_state="OPEN",
            occurred_at=t0 + timedelta(minutes=10), reason="three buckets above", actor="pw-engine",
            observed_value=120.0, expected_value=20.0),
        PwAnomalyTransition(
            pw_anomaly_id=incident.id, from_state=None, to_state="CANDIDATE", occurred_at=t0,
            bucket_start=t0, reason="above the band", actor="pw-engine",
            observed_value=90.0, expected_value=20.0),
    ])
    await db_session.commit()
    return made, value, incident


# ------------------------------------------------------------------ item 1: the detail
async def test_the_pw_incident_detail_serves_subject_reading_and_lifecycle(
    db_session, db_schema: str
) -> None:
    made, value, incident = await _world(db_session, db_schema)
    body = _client(db_schema, _principal("viewer")).get(f"{ANOMALIES}/{incident.id}")
    assert body.status_code == 200, body.text
    out = body.json()
    assert out["subject_kind"] == "PW_VALUE" and out["anomaly_type"] == "PW_VALUE_SPIKE"
    ref = out["pw_value"]
    assert ref["watch_id"] == made["id"] and ref["watch_name"] == "gaps"
    assert ref["value_key"] == CANARY and ref["components"] == [CANARY]
    assert ref["value_id"] == str(value.id)
    assert ref["scope_kind"] == "LOG_SOURCE_TYPE" and ref["watch_version"] == 1
    assert out["state"] == "OPEN" and out["observed_measurement"] == "EXACT"
    assert out["observed_value"] == 120.0 and out["expected_value"] == 20.0
    assert out["explanation_package"] is None and out["waf_endpoint"] is None
    assert [t["to_state"] for t in out["transitions"]] == ["CANDIDATE", "OPEN"]
    assert out["transitions"][0]["from_state"] is None
    det = out["detection"]
    assert (det["expected_low"], det["expected_high"]) == (10.0, 40.0)
    assert det["observed_events"] == 120.0 and det["expected_median"] == 20.0
    assert det["baseline_sample_count"] == 12 and det["robust_score_status"] == "OK"
    assert "internal_secret" not in str(out), "the projection reads named keys only"


async def test_a_pw_incident_whose_value_was_deleted_keeps_its_detail(
    db_session, db_schema: str
) -> None:
    made, _value, _incident_row = await _world(db_session, db_schema)
    orphan = _incident(made["id"], None, details={})
    db_session.add(orphan)
    await db_session.commit()
    out = _client(db_schema, _principal("viewer")).get(f"{ANOMALIES}/{orphan.id}").json()
    assert out["pw_value"]["value_id"] is None and out["pw_value"]["value_key"] == CANARY
    assert out["detection"] is None, "no stored evidence is None, never a block of zeros"
    assert out["transitions"] == []


async def test_an_unknown_incident_is_404_and_the_guard_holds(db_session, db_schema: str) -> None:
    _made, _value, incident = await _world(db_session, db_schema)
    viewer = _client(db_schema, _principal("viewer"))
    assert viewer.get(f"{ANOMALIES}/{NIL}").status_code == 404
    assert _client(db_schema, NOBODY).get(f"{ANOMALIES}/{incident.id}").status_code == 403


async def test_the_detail_costs_a_fixed_number_of_statements(db_session, db_schema: str) -> None:
    made, value, incident = await _world(db_session, db_schema)
    viewer = _client(db_schema, _principal("viewer"))
    viewer.statements.clear()  # type: ignore[attr-defined]
    assert viewer.get(f"{ANOMALIES}/{incident.id}").status_code == 200
    one = len(viewer.statements)  # type: ignore[attr-defined]
    for _ in range(20):
        db_session.add(_incident(made["id"], value.id))
    await db_session.flush()
    for stamp in range(15):
        db_session.add(PwAnomalyTransition(
            pw_anomaly_id=incident.id, from_state="OPEN", to_state="OPEN",
            occurred_at=incident.detected_at + timedelta(hours=1, minutes=stamp),
            actor="pw-engine"))
    await db_session.commit()
    viewer.statements.clear()  # type: ignore[attr-defined]
    assert viewer.get(f"{ANOMALIES}/{incident.id}").status_code == 200
    assert len(viewer.statements) == one  # type: ignore[attr-defined]


# ------------------------------------------------------------------ item 2: value_id
async def test_the_list_carries_value_id(db_session, db_schema: str) -> None:
    _made, value, incident = await _world(db_session, db_schema)
    rows = _client(db_schema, _principal("viewer")).get(
        ANOMALIES,
        params={"subject_kind": "PW_VALUE", "since": "2026-10-13T00:00:00Z",
                "until": "2026-10-14T00:00:00Z"},
    ).json()["items"]
    assert [r["id"] for r in rows] == [str(incident.id)]
    assert rows[0]["pw_value"]["value_id"] == str(value.id)


# ------------------------------------------------------------------ item 3: the histogram
async def test_the_histogram_counts_pw_incidents_and_filters_by_kind(
    db_session, db_schema: str
) -> None:
    made, value, _incident_row = await _world(db_session, db_schema)
    db_session.add(_incident(
        made["id"], value.id, severity=Severity.LOW, state=AnomalyState.RESOLVED,
        detected_at=datetime(2026, 10, 13, 9, 30, tzinfo=UTC)))
    await db_session.commit()
    viewer = _client(db_schema, _principal("viewer"))
    window = {"since": "2026-10-13T08:00:00Z", "until": "2026-10-13T11:00:00Z"}
    everything = viewer.get(f"{ANOMALIES}/histogram", params=window).json()
    listed = viewer.get(ANOMALIES, params={**window, "limit": 100}).json()
    assert everything["total"] == listed["total"] == 2
    assert "PW_VALUE" in everything["families"]
    assert [h["counts"] for h in everything["hours"]] == [{"HIGH": 1}, {"LOW": 1}, {}]
    only = viewer.get(f"{ANOMALIES}/histogram", params={**window, "subject_kind": "PW_VALUE"})
    assert only.json()["families"] == ["PW_VALUE"] and only.json()["total"] == 2
    other = viewer.get(f"{ANOMALIES}/histogram", params={**window, "subject_kind": "LOG_SOURCE"})
    assert other.json()["total"] == 0 and "PW_VALUE" not in other.json()["families"]


# ------------------------------------------------------------------ item 4: the AQL view
async def test_the_aql_view_is_read_only_text_with_the_last_buckets_provenance(
    db_session, db_schema: str
) -> None:
    made, _value, _incident_row = await _world(db_session, db_schema)
    provenance = {
        "grouped": {"query": "SELECT grouped-text", "state": "COMPLETE", "internal": "x"},
        "totals": {"query": "SELECT totals-text"},
        "pinned": {"query": f"SELECT pinned IN ('{PIN_CANARY}')"},
        "noise": {"query": "never"},
    }
    db_session.add(_bucket(made["id"], 3, query_provenance={"grouped": {"query": "older"}}))
    db_session.add(_bucket(made["id"], 1, query_provenance=provenance))
    await db_session.commit()
    viewer = _client(db_schema, _principal("viewer"))
    answer = viewer.get(f"{PREFIX}/{made['id']}/aql")
    assert answer.status_code == 200, answer.text
    out = answer.json()
    assert [q["search"] for q in out["current"]] == ["GROUPED", "TOTALS"]
    assert all(q["text"].startswith("SELECT") and " FROM events " in q["text"]
               for q in out["current"])
    assert "WAF_Action" in out["current"][0]["text"] and out["current_unavailable"] is None
    assert out["bucket_seconds"] == 300 and out["watch_version"] == 1
    last = out["last_bucket"]
    assert [(q["search"], q["text"]) for q in last["queries"]] == [
        ("GROUPED", "SELECT grouped-text"), ("TOTALS", "SELECT totals-text"),
        ("PINNED", f"SELECT pinned IN ('{PIN_CANARY}')")]
    assert "internal" not in str(out) and "noise" not in str(out)
    # Nothing is accepted: no write verb exists on the path, and a query string changes nothing.
    for method in ("post", "put", "patch", "delete"):
        answer = viewer.request(method.upper(), f"{PREFIX}/{made['id']}/aql", json={"aql": "x"})
        assert answer.status_code == 405, method
    hostile = viewer.get(f"{PREFIX}/{made['id']}/aql", params={"aql": "DROP", "text": "DROP"})
    assert hostile.json()["current"] == out["current"]


async def test_the_aql_view_before_any_collection_and_for_an_unresolvable_scope(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    writer = _client(db_schema, _principal("detection_engineer"))
    viewer = _client(db_schema, _principal("viewer"))
    fresh = _create(writer, "fresh")
    out = viewer.get(f"{PREFIX}/{fresh['id']}/aql").json()
    assert out["last_bucket"] is None and len(out["current"]) == 2
    empty = _create(writer, "empty-group", scope_kind="LOG_SOURCE_GROUP", scope_type_id=None,
                    scope_group_id=GROUP_ID)
    await db_session.execute(delete(LogSourceGroup))
    await db_session.commit()
    out = viewer.get(f"{PREFIX}/{empty['id']}/aql").json()
    assert out["current"] == [] and "SCOPE_EMPTY" in out["current_unavailable"]
    assert out["window_start"] is None, "no window is claimed when no query was built"


async def test_the_aql_route_guard_and_404(db_session, db_schema: str) -> None:
    await _seed(db_session)
    made = _create(_client(db_schema, _principal("detection_engineer")), "guarded")
    assert _client(db_schema, NOBODY).get(f"{PREFIX}/{made['id']}/aql").status_code == 403
    for role in ("admin", "analyst", "viewer", "detection_engineer"):
        assert _client(db_schema, _principal(role)).get(
            f"{PREFIX}/{made['id']}/aql").status_code == 200, role
    assert _client(db_schema, _principal("viewer")).get(f"{PREFIX}/{NIL}/aql").status_code == 404


# ------------------------------------------------------------------ item 5: list_values
async def _watch_with_values(db_session, writer, name: str, count: int) -> str:  # type: ignore[no-untyped-def]
    made = _create(writer, name)
    watch_id = uuid.UUID(made["id"])
    db_session.add(_bucket(made["id"], 1))
    values = [
        PwValue(watch_id=watch_id, watch_version=1, value_key=f"{name}-v{n}",
                tracked_since=_bucket_start(7))
        for n in range(count)
    ]
    db_session.add_all(values)
    await db_session.flush()
    db_session.add_all([
        PwValueMetric(pw_value_id=v.id, bucket_start=_bucket_start(1), events=n + 1,
                      bucket_seconds=300)
        for n, v in enumerate(values)
    ])
    await db_session.commit()
    return made["id"]


async def test_list_values_reads_only_its_watchs_metric_rows(db_session, db_schema: str) -> None:
    await _seed(db_session)
    writer = _client(db_schema, _principal("detection_engineer"))
    mine = await _watch_with_values(db_session, writer, "mine", 3)
    await _watch_with_values(db_session, writer, "theirs", 3)
    viewer = _client(db_schema, _principal("viewer"))
    viewer.statements.clear()  # type: ignore[attr-defined]
    page = viewer.get(f"{PREFIX}/{mine}/values", params={"limit": 50}).json()
    assert [(v["value_key"], v["events"]) for v in page["items"]] == [
        ("mine-v2", 3), ("mine-v1", 2), ("mine-v0", 1)]
    metric_reads = [s for s in viewer.statements  # type: ignore[attr-defined]
                    if "pw_value_metric" in s]
    # The page filters its values by watch, and the metric subquery must filter by it too.
    assert metric_reads and all(s.count("pw_value.watch_id = ") >= 2 for s in metric_reads)


async def test_list_values_costs_the_same_statements_at_50_and_200_values_and_1_and_10_watches(
    db_session, db_schema: str
) -> None:
    await _seed(db_session)
    writer = _client(db_schema, _principal("detection_engineer"))
    viewer = _client(db_schema, _principal("viewer"))
    first = await _watch_with_values(db_session, writer, "w0", 50)
    viewer.statements.clear()  # type: ignore[attr-defined]
    small = viewer.get(f"{PREFIX}/{first}/values", params={"limit": 200}).json()
    assert len(small["items"]) == 50
    baseline = len(viewer.statements)  # type: ignore[attr-defined]
    second = await _watch_with_values(db_session, writer, "w1", 200)
    viewer.statements.clear()  # type: ignore[attr-defined]
    page = viewer.get(f"{PREFIX}/{second}/values", params={"limit": 200}).json()
    assert len(page["items"]) == 200
    assert len(viewer.statements) == baseline  # type: ignore[attr-defined]
    for n in range(2, 10):
        await _watch_with_values(db_session, writer, f"w{n}", 5)
    viewer.statements.clear()  # type: ignore[attr-defined]
    again = viewer.get(f"{PREFIX}/{second}/values", params={"limit": 200}).json()
    assert len(again["items"]) == 200
    assert len(viewer.statements) == baseline  # type: ignore[attr-defined]
    assert baseline <= 6


# ------------------------------------------------------------------ privacy
async def test_neither_the_detail_nor_the_aql_view_logs_a_value_text(
    db_session, db_schema: str, caplog: pytest.LogCaptureFixture
) -> None:
    made, _value, incident = await _world(db_session, db_schema)
    db_session.add(_bucket(made["id"], 1, query_provenance={
        "pinned": {"query": f"SELECT pinned IN ('{PIN_CANARY}')"}}))
    await db_session.commit()
    viewer = _client(db_schema, _principal("viewer"))
    with caplog.at_level(logging.DEBUG):
        detail = viewer.get(f"{ANOMALIES}/{incident.id}")
        aql = viewer.get(f"{PREFIX}/{made['id']}/aql")
        viewer.get(f"{ANOMALIES}/{NIL}")
    assert CANARY in detail.text and PIN_CANARY in aql.text, "read:* callers see the text (D-22)"
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert CANARY not in logged and PIN_CANARY not in logged
    assert PwBucket.__tablename__  # the model import is the schema this test seeds
