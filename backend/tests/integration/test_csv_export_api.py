"""SOC-8a: the alert and anomaly lists as CSV, over HTTP.

Every test seeds committed rows and reads the attachment through the API, so the
assertions see the bytes an analyst's spreadsheet receives. The traffic export
keeps its own tests (`tests/integration/test_traffic_ranking.py`), which pin that
lifting its helpers into `app/api/csv_export.py` changed nothing.
"""

from __future__ import annotations

import csv
import io
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.models.alert import Alert
from app.models.enums import (
    AlertStatus,
    AnomalyFamily,
    AnomalyState,
    AnomalyType,
    EvidenceStatus,
    Severity,
    TriageDisposition,
    TriageStatus,
    WafAnomalyType,
)
from app.models.log_source import LogSourceAnomaly
from app.models.triage import AnomalyTriage
from app.models.waf import WafAnomaly, WafEndpoint
from app.security.rbac import Principal
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_fleet_api import Client, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the CSV export integration tests",
    ),
]

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
ALERTS_ONLY = Principal(subject="alerts", permissions=frozenset({"read:alerts"}))
NOBODY = Principal(subject="nobody", permissions=frozenset())

ALERTS_CSV = "/api/v1/alerts.csv"
ANOMALIES_CSV = "/api/v1/anomalies.csv"

BASE = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
#: The window every explicit-range request asks for: [BASE, BASE + 24h).
RANGE_END = BASE + timedelta(hours=24)
RANGE = {"since": BASE.isoformat().replace("+00:00", "Z"),
         "until": RANGE_END.isoformat().replace("+00:00", "Z")}
RANGE_FILENAME = "20261105T1200Z-20261106T1200Z"


def at(minutes: int) -> datetime:
    return BASE + timedelta(minutes=minutes)


# ------------------------------------------------------------------ parsing
def csv_rows(body: str) -> list[list[str]]:
    """The body parsed as RFC 4180, so quoting is checked rather than assumed."""
    return list(csv.reader(io.StringIO(body, newline=""), delimiter=",", quotechar='"'))


def records(body: str, columns: tuple[str, ...]) -> list[dict[str, str]]:
    """The export's data rows as cells keyed by column, header checked."""
    rows = csv_rows(body)
    assert rows[0] == list(columns)
    return [dict(zip(columns, row, strict=True)) for row in rows[1:]]


def cap_at(client: Client, rows: int) -> None:
    """The smallest export bound the settings allow, for the truncation tests."""
    client.http.app.dependency_overrides[get_settings] = (  # type: ignore[attr-defined]
        lambda: get_settings().model_copy(update={"api_max_export_rows": rows})
    )


# ------------------------------------------------------------------- seeds
def _alert(
    title: str,
    *,
    source_type: str = "log_source",
    source_id: uuid.UUID | None = None,
    severity: Severity = Severity.HIGH,
    status: AlertStatus = AlertStatus.OPEN,
    opened_at: datetime | None = None,
    **kw: object,
) -> Alert:
    fingerprint = f"csv:{uuid.uuid4()}"
    values: dict[str, object] = dict(
        dedup_key=fingerprint,
        fingerprint=fingerprint,
        title=title,
        severity=severity,
        status=status,
        source_type=source_type,
        source_id=source_id,
        context={},
        opened_at=opened_at or BASE,
        first_seen_at=opened_at or BASE,
        occurrence_count=1,
        evidence_snapshot={"raw": "never exported"},
        source_anomaly_ids=[],
    )
    values.update(kw)
    return Alert(**values)


async def alerts_world(session):  # type: ignore[no-untyped-def]
    """Three alerts: one acked, one resolved, and one unknown producer."""
    inst = await make_instance(session)
    src = await make_log_source(session, inst, qradar_id=1, name="edge-1")
    rows = [
        _alert("spike on edge", source_id=src.id, severity=Severity.CRITICAL,
               opened_at=at(10), occurrence_count=4),
        _alert("Şube Raporu — İstanbul", source_type="waf_endpoint",
               status=AlertStatus.ACKNOWLEDGED, opened_at=at(20),
               acknowledged_at=at(25), acknowledged_by="alice"),
        _alert("probes from 10.0.0.9", source_type="mystery_producer",
               status=AlertStatus.RESOLVED, severity=Severity.LOW, opened_at=at(30),
               resolved_at=at(40), resolved_by="bob"),
    ]
    session.add_all(rows)
    await session.flush()
    return {"source_id": src.id, "ids": [row.id for row in rows]}


async def anomalies_world(session):  # type: ignore[no-untyped-def]
    """Three Phase A incidents: one judged, two with no triage row at all."""
    inst = await make_instance(session)
    src = await make_log_source(session, inst, qradar_id=2, name="Şube Ağ Geçidi")
    other = await make_log_source(session, inst, qradar_id=3, name="core")
    spike = phase_a(src.id, at(5))
    drop = phase_a(
        other.id, at(15), anomaly_type=AnomalyType.VOLUME_DROP, severity=Severity.MEDIUM,
        state=AnomalyState.RESOLVED, resolved_at=at(45),
    )
    silent = phase_a(
        src.id, at(25), anomaly_type=AnomalyType.NO_EVENTS, severity=Severity.INFO,
        evidence_status=EvidenceStatus.PARTIAL,
    )
    session.add_all([spike, drop, silent])
    await session.flush()
    session.add(
        AnomalyTriage(
            family=AnomalyFamily.LOG_SOURCE, anomaly_id=spike.id, owner="alice",
            status=TriageStatus.CLOSED, disposition=TriageDisposition.TRUE_POSITIVE,
            updated_by="alice", version=2,
        )
    )
    await session.flush()
    return {
        "source_id": src.id,
        "other_source_id": other.id,
        "spike": spike.id,
        "drop": drop.id,
        "silent": silent.id,
    }


def phase_a(source_id, when: datetime, **kw: object) -> LogSourceAnomaly:  # type: ignore[no-untyped-def]
    values: dict[str, object] = dict(
        log_source_id=source_id,
        anomaly_type=AnomalyType.VOLUME_SPIKE,
        severity=Severity.HIGH,
        state=AnomalyState.OPEN,
        detected_at=when,
        evidence_status=EvidenceStatus.NOT_REQUESTED,
        details={},
    )
    values.update(kw)
    return LogSourceAnomaly(**values)


# ------------------------------------------------------------- alert export
def test_alerts_csv_is_the_filtered_list_with_the_contract_columns(db_schema) -> None:  # type: ignore[no-untyped-def]
    world = run(alerts_world)
    response = Client(READER).get(ALERTS_CSV, params=RANGE)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="alerts-{RANGE_FILENAME}.csv"'
    )
    assert "x-export-truncated" not in response.headers
    # RFC 4180 records, CRLF-terminated, the header always first.
    assert response.text.count("\r\n") == 4
    assert response.text.endswith("\r\n")
    rows = records(response.text, (
        "id", "family", "source_type", "source_id", "title", "severity", "status",
        "opened_at", "acknowledged_at", "acknowledged_by", "resolved_at", "resolved_by",
        "occurrence_count", "fingerprint",
    ))
    # Newest first, the list's own default order.
    assert [row["id"] for row in rows] == [
        str(world["ids"][2]), str(world["ids"][1]), str(world["ids"][0])
    ]
    acked = rows[1]
    assert acked["family"] == "waf"
    assert acked["source_type"] == "waf_endpoint"
    assert acked["title"] == "Şube Raporu — İstanbul"
    assert acked["severity"] == "HIGH"
    assert acked["status"] == "ACKNOWLEDGED"
    assert acked["opened_at"] == "2026-11-05T12:20:00Z"
    assert acked["acknowledged_at"] == "2026-11-05T12:25:00Z"
    assert acked["acknowledged_by"] == "alice"
    assert acked["resolved_at"] == ""
    assert acked["resolved_by"] == ""
    spike = rows[2]
    assert spike["source_id"] == str(world["source_id"])
    assert spike["family"] == "log_source"
    assert spike["occurrence_count"] == "4"
    assert spike["severity"] == "CRITICAL"
    resolved = rows[0]
    assert resolved["resolved_at"] == "2026-11-05T12:40:00Z"
    assert resolved["resolved_by"] == "bob"
    # The snapshot is raw evidence and the export is a list.
    assert "evidence_snapshot" not in response.text
    assert "never exported" not in response.text


def test_alerts_csv_an_unknown_producer_has_no_family(db_schema) -> None:  # type: ignore[no-untyped-def]
    run(alerts_world)
    rows = records(Client(READER).get(ALERTS_CSV, params=RANGE).text, (
        "id", "family", "source_type", "source_id", "title", "severity", "status",
        "opened_at", "acknowledged_at", "acknowledged_by", "resolved_at", "resolved_by",
        "occurrence_count", "fingerprint",
    ))
    unknown = next(row for row in rows if row["source_type"] == "mystery_producer")
    # Not forced into a family: an empty cell is the honest answer.
    assert unknown["family"] == ""


def test_alerts_csv_without_a_range_says_no_range(db_schema) -> None:  # type: ignore[no-untyped-def]
    run(alerts_world)
    response = Client(READER).get(ALERTS_CSV)
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == (
        'attachment; filename="alerts-no-range.csv"'
    )
    assert len(csv_rows(response.text)) == 4


@pytest.mark.parametrize(
    "params",
    [
        {"severity": "HIGH"},
        {"family": "waf"},
        {"status": "RESOLVED"},
        {"q": "spike"},
        {"sort": "severity", "order": "asc"},
        {"log_source_id": "world"},
        {"since": RANGE["since"], "until": RANGE["until"]},
    ],
)
def test_alerts_csv_returns_the_same_ids_as_the_list_for_the_same_query(  # type: ignore[no-untyped-def]
    db_schema, params: dict[str, str]
) -> None:
    world = run(alerts_world)
    query = dict(params)
    if query.pop("log_source_id", None) == "world":
        query["log_source_id"] = str(world["source_id"])
    client = Client(READER)
    listed = client.get("/api/v1/alerts", params={**query, "limit": 500})
    exported = client.get(ALERTS_CSV, params=query)
    assert listed.status_code == 200, listed.text
    assert exported.status_code == 200, exported.text
    assert [item["id"] for item in listed.json()["items"]] == [
        row[0] for row in csv_rows(exported.text)[1:]
    ]


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_alerts_csv_guards_a_title_that_starts_with_a_formula_character(  # type: ignore[no-untyped-def]
    db_schema, prefix: str
) -> None:
    async def seed_one(s):  # type: ignore[no-untyped-def]
        s.add(_alert(f"{prefix}cmd|calc", opened_at=at(5)))
        await s.flush()

    run(seed_one)
    response = Client(READER).get(ALERTS_CSV, params=RANGE)
    rows = csv_rows(response.text)
    assert rows[1][4] == "'" + f"{prefix}cmd|calc"


def test_alerts_csv_carries_a_non_ascii_title_and_quotes_it_rfc4180(db_schema) -> None:  # type: ignore[no-untyped-def]
    title = 'Şube "Ağ", Raporu — İstanbul'

    async def seed(s):  # type: ignore[no-untyped-def]
        s.add(_alert(title, opened_at=at(5)))
        await s.flush()

    run(seed)
    response = Client(READER).get(ALERTS_CSV, params=RANGE)
    assert "utf-8" in response.headers["content-type"]
    assert response.content.decode("utf-8").encode("utf-8") == response.content
    assert csv_rows(response.text)[1][4] == title


def test_alerts_csv_truncates_at_the_configured_bound_and_says_so(db_schema) -> None:  # type: ignore[no-untyped-def]
    world = run(alerts_world)
    client = Client(READER)
    cap_at(client, 2)
    response = client.get(ALERTS_CSV, params=RANGE)
    assert response.status_code == 200, response.text
    assert response.headers["x-export-truncated"] == "true"
    rows = csv_rows(response.text)
    assert len(rows) == 3  # the header and the cap, not the whole set
    assert [row[0] for row in rows[1:]] == [
        str(world["ids"][2]), str(world["ids"][1])
    ]


def test_alerts_csv_needs_read_alerts_and_names_the_permission(db_schema) -> None:  # type: ignore[no-untyped-def]
    run(alerts_world)
    denied = Client(NOBODY).get(ALERTS_CSV)
    assert denied.status_code == 403
    assert denied.json()["detail_code"] == "auth.permission.missing"
    assert denied.json()["detail_params"] == {"permission": "read:alerts"}
    assert Client(ALERTS_ONLY).get(ALERTS_CSV).status_code == 200


def test_alerts_csv_is_not_matched_as_an_alert_id(db_schema) -> None:  # type: ignore[no-untyped-def]
    world = run(alerts_world)
    client = Client(READER)
    export = client.get(ALERTS_CSV, params=RANGE)
    assert export.status_code == 200
    assert export.headers["content-type"] == "text/csv; charset=utf-8"
    assert export.text.startswith("id,family,source_type,")
    # The id route is still the id route: a real id resolves, a non-id is refused.
    detail = client.get(f"/api/v1/alerts/{world['ids'][0]}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == str(world["ids"][0])
    assert client.get("/api/v1/alerts/not-a-uuid").status_code == 422


def test_alerts_csv_statement_count_does_not_grow_with_the_rows(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed_one(s):  # type: ignore[no-untyped-def]
        s.add(_alert("first", opened_at=at(5)))
        await s.flush()

    run(seed_one)
    client = Client(READER)
    assert client.get(ALERTS_CSV, params=RANGE).status_code == 200
    small = client.statements

    async def grow(s):  # type: ignore[no-untyped-def]
        for index in range(50):
            s.add(_alert(f"bulk-{index}", opened_at=at(6)))
        await s.flush()

    run(grow)
    assert client.get(ALERTS_CSV, params=RANGE).status_code == 200
    assert client.statements == small, (small, client.statements)
    # One statement over the rows: the extra row that proves truncation is read
    # with them, never as a second count.
    assert small == 1


# ----------------------------------------------------------- anomaly export
def test_anomalies_csv_is_the_filtered_list_with_the_contract_columns(db_schema) -> None:  # type: ignore[no-untyped-def]
    world = run(anomalies_world)
    response = Client(READER).get(ANOMALIES_CSV, params=RANGE)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="anomalies-{RANGE_FILENAME}.csv"'
    )
    assert "x-export-truncated" not in response.headers
    assert response.text.count("\r\n") == 4
    rows = records(response.text, (
        "id", "family", "anomaly_type", "log_source_id", "log_source_name", "severity",
        "state", "detected_at", "opened_at", "resolved_at", "triage_status",
        "triage_owner", "triage_disposition",
    ))
    # Newest detection first, the list's own order.
    assert [row["id"] for row in rows] == [
        str(world["silent"]), str(world["drop"]), str(world["spike"])
    ]
    judged = rows[2]
    assert judged["family"] == "log_source"
    assert judged["anomaly_type"] == "VOLUME_SPIKE"
    assert judged["log_source_id"] == str(world["source_id"])
    assert judged["log_source_name"] == "Şube Ağ Geçidi"
    assert judged["severity"] == "HIGH"
    assert judged["state"] == "OPEN"
    assert judged["detected_at"] == "2026-11-05T12:05:00Z"
    assert judged["triage_status"] == "CLOSED"
    assert judged["triage_owner"] == "alice"
    assert judged["triage_disposition"] == "TRUE_POSITIVE"
    # A resolved incident carries its clock; the open ones have no empty zero.
    assert rows[1]["resolved_at"] == "2026-11-05T12:45:00Z"
    untriaged = rows[0]
    assert untriaged["triage_status"] == "NEW"
    assert untriaged["triage_owner"] == ""
    assert untriaged["triage_disposition"] == ""
    assert untriaged["opened_at"] == ""
    assert untriaged["resolved_at"] == ""
    # Reasoning and contributor evidence are not list columns.
    assert "explanation" not in response.text


def test_anomalies_csv_exports_a_waf_incident_under_its_family(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=4, name="waf-1")
        endpoint = WafEndpoint(
            instance_id=inst.id, log_source_id=src.id, virtual_host="api.example.com",
            normalized_endpoint="/login", http_method="POST",
            normalizer_version=ENDPOINT_NORMALIZER_VERSION,
            first_seen_at=at(0), last_seen_at=at(30), tracked_since=at(0), is_active=True,
        )
        s.add(endpoint)
        await s.flush()
        s.add(
            WafAnomaly(
                waf_endpoint_id=endpoint.id,
                detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
                severity=Severity.MEDIUM, state=AnomalyState.OPEN, detected_at=at(10),
                evidence_status=EvidenceStatus.NOT_REQUESTED, details={},
            )
        )
        await s.flush()

    run(seed)
    rows = records(Client(READER).get(ANOMALIES_CSV, params=RANGE).text, (
        "id", "family", "anomaly_type", "log_source_id", "log_source_name", "severity",
        "state", "detected_at", "opened_at", "resolved_at", "triage_status",
        "triage_owner", "triage_disposition",
    ))
    assert [row["family"] for row in rows] == ["waf"]
    assert rows[0]["anomaly_type"] == "WAF_REQUEST_RATE_SPIKE"
    assert rows[0]["log_source_name"] == "waf-1"


@pytest.mark.parametrize(
    "params",
    [
        {"state": "OPEN"},
        {"severity": "HIGH"},
        {"triage_status": "CLOSED"},
        {"owner": "alice"},
        {"disposition": "TRUE_POSITIVE"},
        {"anomaly_type": "VOLUME_DROP"},
        {"active_only": "true"},
        {"log_source_id": "world"},
    ],
)
def test_anomalies_csv_returns_the_same_ids_as_the_list_for_the_same_query(  # type: ignore[no-untyped-def]
    db_schema, params: dict[str, str]
) -> None:
    world = run(anomalies_world)
    query = dict(params)
    if query.pop("log_source_id", None) == "world":
        query["log_source_id"] = str(world["source_id"])
    request = {**query, **RANGE}
    client = Client(READER)
    listed = client.get("/api/v1/anomalies", params={**request, "limit": 200})
    exported = client.get(ANOMALIES_CSV, params=request)
    assert listed.status_code == 200, listed.text
    assert exported.status_code == 200, exported.text
    assert [item["id"] for item in listed.json()["items"]] == [
        row[0] for row in csv_rows(exported.text)[1:]
    ]


def test_anomalies_csv_active_only_without_a_range_says_no_range(db_schema) -> None:  # type: ignore[no-untyped-def]
    """An active-only export has no lower bound, so it covers no named window."""
    run(anomalies_world)
    response = Client(READER).get(ANOMALIES_CSV, params={"active_only": "true"})
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == (
        'attachment; filename="anomalies-no-range.csv"'
    )
    assert len(csv_rows(response.text)) == 3  # silent and spike are open


def test_anomalies_csv_truncates_at_the_configured_bound_and_says_so(db_schema) -> None:  # type: ignore[no-untyped-def]
    run(anomalies_world)
    client = Client(READER)
    cap_at(client, 1)
    response = client.get(ANOMALIES_CSV, params=RANGE)
    assert response.status_code == 200, response.text
    assert response.headers["x-export-truncated"] == "true"
    assert len(csv_rows(response.text)) == 2
    assert "x-export-truncated" not in Client(READER).get(
        ANOMALIES_CSV, params=RANGE
    ).headers


def test_anomalies_csv_needs_read_anomalies_and_names_the_permission(db_schema) -> None:  # type: ignore[no-untyped-def]
    run(anomalies_world)
    denied = Client(NOBODY).get(ANOMALIES_CSV)
    assert denied.status_code == 403
    assert denied.json()["detail_code"] == "auth.permission.missing"
    assert denied.json()["detail_params"] == {"permission": "read:anomalies"}
    # Reading the alert list does not grant the anomaly surface.
    assert Client(ALERTS_ONLY).get(ANOMALIES_CSV).status_code == 403


def test_anomalies_csv_is_not_matched_as_an_anomaly_id(db_schema) -> None:  # type: ignore[no-untyped-def]
    world = run(anomalies_world)
    client = Client(READER)
    export = client.get(ANOMALIES_CSV, params=RANGE)
    assert export.status_code == 200
    assert export.headers["content-type"] == "text/csv; charset=utf-8"
    assert export.text.startswith("id,family,anomaly_type,")
    detail = client.get(f"/api/v1/anomalies/{world['spike']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == str(world["spike"])
    assert client.get("/api/v1/anomalies/not-a-uuid").status_code == 422


def test_anomalies_csv_statement_count_does_not_grow_with_the_rows(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed_one(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=9, name="edge-9")
        s.add(phase_a(src.id, at(5)))

    run(seed_one)
    client = Client(READER)
    assert client.get(ANOMALIES_CSV, params=RANGE).status_code == 200
    small = client.statements

    async def grow(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=10, name="edge-10")
        for index in range(30):
            s.add(phase_a(src.id, at(5 + index // 4)))

    run(grow)
    assert client.get(ANOMALIES_CSV, params=RANGE).status_code == 200
    assert client.statements == small, (small, client.statements)
    # The union's count and its page: the same two statements the JSON list makes.
    assert small == 2
