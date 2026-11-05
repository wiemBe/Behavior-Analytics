"""SOC-1a: analyst triage over HTTP, on all four anomaly families.

Everything is seeded through a committed session and read back over HTTP, as the other
API tests do. The four families share one triage table, so every list assertion covers
the union's four branches: a filter that works on Phase A but not on WAF would pass a
single-family test.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.enums import (
    AnomalyState,
    AnomalyType,
    EvidenceStatus,
    IpsAnomalyType,
    PwAnomalyType,
    PwScopeKind,
    Severity,
    WafAnomalyType,
)
from app.models.identity import AuditLog
from app.models.ips import IpsAnomaly
from app.models.log_source import LogSourceAnomaly
from app.models.property_watch import PropertyWatch, PwAnomaly
from app.models.waf import WafAnomaly, WafEndpoint
from app.security.rbac import Principal
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_fleet_api import Client, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the anomaly triage API tests",
    ),
]

NOW = (datetime.now(UTC) - timedelta(hours=1)).replace(microsecond=0)

# `read:*` satisfies the router's read:anomalies guard; `anomaly:triage` is the write.
READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
TRIAGER = Principal(
    subject="me@example.com",
    permissions=frozenset({"read:*", "anomaly:triage"}),
)
ANOMALY_READER = Principal(subject="ar", permissions=frozenset({"read:anomalies"}))
TRIAGE_ONLY = Principal(subject="t", permissions=frozenset({"anomaly:triage"}))
NOBODY = Principal(subject="nobody", permissions=frozenset())


def phase_a(source_id, **kw):  # type: ignore[no-untyped-def]
    values = dict(
        log_source_id=source_id,
        anomaly_type=AnomalyType.VOLUME_SPIKE,
        severity=Severity.HIGH,
        state=AnomalyState.OPEN,
        detected_at=NOW,
        evidence_status=EvidenceStatus.NOT_REQUESTED,
        details={},
    )
    values.update(kw)
    return LogSourceAnomaly(**values)


def waf(endpoint_id, **kw):  # type: ignore[no-untyped-def]
    values = dict(
        waf_endpoint_id=endpoint_id,
        detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
        severity=Severity.MEDIUM,
        state=AnomalyState.OPEN,
        detected_at=NOW,
        evidence_status=EvidenceStatus.NOT_REQUESTED,
        details={},
    )
    values.update(kw)
    return WafAnomaly(**values)


def ips(source_id, **kw):  # type: ignore[no-untyped-def]
    values = dict(
        subject_kind="IPS_SOURCE",
        log_source_id=source_id,
        detector_type=IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE,
        fingerprint=f"ips:{uuid.uuid4().hex}",
        severity=Severity.MEDIUM,
        state=AnomalyState.OPEN,
        detected_at=NOW,
        profile_version=1,
        normalizer_version=1,
        evidence_status=EvidenceStatus.NOT_REQUESTED,
        details={},
    )
    values.update(kw)
    return IpsAnomaly(**values)


def pw(watch_id, **kw):  # type: ignore[no-untyped-def]
    values = dict(
        watch_id=watch_id,
        value_key="/api/login",
        detector_type=PwAnomalyType.PW_VALUE_SPIKE,
        fingerprint=f"pw:{uuid.uuid4().hex}",
        severity=Severity.LOW,
        state=AnomalyState.OPEN,
        detected_at=NOW,
        watch_version=1,
        evidence_status=EvidenceStatus.NOT_REQUESTED,
        details={},
    )
    values.update(kw)
    return PwAnomaly(**values)


async def _seed(session) -> dict[str, uuid.UUID]:  # type: ignore[no-untyped-def]
    inst = await make_instance(session)
    source = await make_log_source(
        session, inst, qradar_id=9001, name=f"triage-{uuid.uuid4().hex[:6]}"
    )
    a = phase_a(source.id)
    endpoint = WafEndpoint(
        instance_id=inst.id,
        log_source_id=source.id,
        normalized_endpoint="/login",
        http_method="POST",
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        first_seen_at=NOW,
        last_seen_at=NOW,
        tracked_since=NOW,
        is_active=True,
    )
    session.add_all([a, endpoint])
    await session.flush()
    w = waf(endpoint.id)
    i = ips(source.id)
    watch = PropertyWatch(
        instance_id=inst.id,
        name=f"watch-{uuid.uuid4().hex[:8]}",
        scope_kind=PwScopeKind.LOG_SOURCE,
        log_source_id=source.id,
        properties=[{"kind": "NORMALIZED", "name": "URL"}],
        detectors=[PwAnomalyType.PW_VALUE_SPIKE.value],
    )
    session.add_all([w, i, watch])
    await session.flush()
    p = pw(watch.id)
    session.add(p)
    await session.flush()
    return {
        "phase_a": a.id,
        "waf": w.id,
        "ips": i.id,
        "pw": p.id,
        "source": source.id,
        "instance": inst.id,
    }


@pytest.fixture
def world(db_schema):  # type: ignore[no-untyped-def]
    return run(_seed)


def url(family: str, anomaly_id: uuid.UUID) -> str:
    return f"/api/v1/anomalies/triage/{family}/{anomaly_id}"


TRIAGE_PATHS = {
    "phase_a": "log_source",
    "waf": "waf",
    "ips": "ips",
    "pw": "property_watch",
}


# ------------------------------------------------------------------ authorization
class TestAuthorization:
    def test_reading_triage_needs_read_anomalies(self, world) -> None:
        path = url("log_source", world["phase_a"])
        assert Client(NOBODY).get(path).status_code == 403
        # `anomaly:triage` alone is a write grant, not a read grant.
        assert Client(TRIAGE_ONLY).get(path).status_code == 403
        assert Client(ANOMALY_READER).get(path).status_code == 200
        assert Client(READER).get(path).status_code == 200

    def test_writing_triage_needs_anomaly_triage(self, world) -> None:
        path = url("log_source", world["phase_a"])
        body = {"owner": "someone", "expected_version": 0}
        assert Client(READER).patch(path, json=body).status_code == 403
        assert Client(ANOMALY_READER).patch(path, json=body).status_code == 403
        assert Client(READER).post(f"{path}/comments", json={"text": "x"}).status_code == 403
        assert Client(TRIAGER).patch(path, json=body).status_code == 200

    def test_a_denied_write_is_refused_before_existence_is_revealed(self, world) -> None:
        unknown = uuid.uuid4()
        assert (
            Client(READER)
            .patch(url("log_source", unknown), json={"owner": "x", "expected_version": 0})
            .status_code
            == 403
        )
        assert (
            Client(READER)
            .post(url("log_source", unknown) + "/comments", json={"text": "x"})
            .status_code
            == 403
        )


# ------------------------------------------------------------------- default state
class TestDefaultState:
    def test_an_untriaged_anomaly_reads_as_new_unowned_version_zero(self, world) -> None:
        response = Client(READER).get(url("log_source", world["phase_a"]))
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "NEW"
        assert body["owner"] is None
        assert body["disposition"] is None
        assert body["version"] == 0
        assert body["events"] == {"items": [], "total": 0, "limit": 50, "offset": 0}

    @pytest.mark.parametrize("key", ["phase_a", "waf", "ips", "pw"])
    def test_every_family_reads_triage(self, world, key: str) -> None:
        response = Client(READER).get(url(TRIAGE_PATHS[key], world[key]))
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "NEW"
        assert response.json()["family"] == TRIAGE_PATHS[key]


# --------------------------------------------------------------------- transitions
class TestTransitions:
    def test_assign_start_close_reopen_and_keep_the_history(self, world) -> None:
        client = Client(TRIAGER)
        path = url("log_source", world["phase_a"])

        started = client.patch(
            path,
            json={
                "owner": "me@example.com",
                "status": "IN_PROGRESS",
                "expected_version": 0,
            },
        )
        assert started.status_code == 200, started.text
        body = started.json()
        assert body["status"] == "IN_PROGRESS"
        assert body["owner"] == "me@example.com"
        assert body["version"] == 1
        assert body["events"]["total"] == 2
        assert {e["kind"] for e in body["events"]["items"]} == {"OWNER", "STATUS"}

        stale = client.patch(path, json={"owner": "other", "expected_version": 0})
        assert stale.status_code == 409

        no_disposition = client.patch(
            path, json={"status": "CLOSED", "expected_version": 1}
        )
        assert no_disposition.status_code == 422
        # The refused close wrote nothing.
        assert client.get(path).json()["version"] == 1

        closed = client.patch(
            path,
            json={
                "status": "CLOSED",
                "disposition": "NOISE",
                "disposition_note": "known scanner",
                "expected_version": 1,
            },
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "CLOSED"
        assert closed.json()["disposition"] == "NOISE"
        assert closed.json()["disposition_note"] == "known scanner"
        assert closed.json()["version"] == 2

        # A closed item cannot be edited without an explicit reopen.
        refused = client.patch(path, json={"owner": "other", "expected_version": 2})
        assert refused.status_code == 422

        reopened = client.patch(
            path, json={"status": "IN_PROGRESS", "expected_version": 2}
        )
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["status"] == "IN_PROGRESS"
        # The decision is kept; reopening adds history, it does not erase it.
        assert reopened.json()["disposition"] == "NOISE"
        assert reopened.json()["disposition_note"] == "known scanner"
        assert reopened.json()["version"] == 3
        kinds = [e["kind"] for e in reopened.json()["events"]["items"]]
        assert "STATUS" in kinds and "DISPOSITION" in kinds

    def test_a_disposition_may_be_recorded_before_closing(self, world) -> None:
        client = Client(TRIAGER)
        path = url("waf", world["waf"])
        response = client.patch(
            path, json={"disposition": "KNOWN_CHANGE", "expected_version": 0}
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "NEW"
        assert response.json()["disposition"] == "KNOWN_CHANGE"
        assert response.json()["version"] == 1

    def test_a_no_op_patch_does_not_bump_the_version(self, world) -> None:
        client = Client(TRIAGER)
        path = url("ips", world["ips"])
        first = client.patch(path, json={"owner": "me@example.com", "expected_version": 0})
        assert first.status_code == 200
        version = first.json()["version"]
        again = client.patch(
            path, json={"owner": "me@example.com", "expected_version": version}
        )
        assert again.status_code == 200
        assert again.json()["version"] == version

    def test_an_empty_patch_is_422(self, world) -> None:
        response = Client(TRIAGER).patch(
            url("log_source", world["phase_a"]), json={"expected_version": 0}
        )
        assert response.status_code == 422

    def test_a_missing_body_field_is_422(self, world) -> None:
        response = Client(TRIAGER).patch(
            url("log_source", world["phase_a"]), json={"owner": "x"}
        )
        assert response.status_code == 422


# --------------------------------------------------------------- lookup failures
class TestLookupFailures:
    def test_an_unknown_family_is_422(self, world) -> None:
        path = f"/api/v1/anomalies/triage/not_a_family/{world['phase_a']}"
        assert Client(READER).get(path).status_code == 422
        assert (
            Client(TRIAGER).patch(path, json={"owner": "x", "expected_version": 0}).status_code
            == 422
        )

    def test_an_unknown_anomaly_is_404(self, world) -> None:
        unknown = uuid.uuid4()
        for family in TRIAGE_PATHS.values():
            assert Client(READER).get(url(family, unknown)).status_code == 404
        assert (
            Client(TRIAGER)
            .patch(url("log_source", unknown), json={"owner": "x", "expected_version": 0})
            .status_code
            == 404
        )
        assert (
            Client(TRIAGER)
            .post(url("log_source", unknown) + "/comments", json={"text": "x"})
            .status_code
            == 404
        )

    def test_an_anomaly_id_is_not_reachable_through_another_family(self, world) -> None:
        """A Phase A id under the WAF family is not a WAF anomaly, so 404, not 200."""
        assert (
            Client(READER).get(url("waf", world["phase_a"])).status_code == 404
        )


# ---------------------------------------------------------------------- comments
class TestComments:
    def test_a_comment_is_an_event_and_does_not_bump_the_version(self, world) -> None:
        client = Client(TRIAGER)
        path = url("ips", world["ips"])
        response = client.post(f"{path}/comments", json={"text": "checking the sensor"})
        assert response.status_code == 201, response.text
        event = response.json()
        assert event["kind"] == "COMMENT"
        assert event["comment"] == "checking the sensor"
        assert event["triage_version"] == 1
        state = client.get(path).json()
        assert state["status"] == "NEW"
        assert state["version"] == 1
        assert state["events"]["total"] == 1

    def test_a_comment_at_the_bound_is_accepted_and_one_past_is_422(self, world) -> None:
        client = Client(TRIAGER)
        path = url("property_watch", world["pw"]) + "/comments"
        assert client.post(path, json={"text": "x" * 2000}).status_code == 201
        assert client.post(path, json={"text": "x" * 2001}).status_code == 422

    def test_a_blank_comment_is_422(self, world) -> None:
        response = Client(TRIAGER).post(
            url("property_watch", world["pw"]) + "/comments", json={"text": "   "}
        )
        assert response.status_code == 422

    def test_comment_text_is_not_in_the_audit_detail(self, world) -> None:
        secret = f"SENSITIVE-{uuid.uuid4().hex}"
        client = Client(TRIAGER)
        path = url("waf", world["waf"])
        assert client.post(f"{path}/comments", json={"text": secret}).status_code == 201

        async def reader(session):  # type: ignore[no-untyped-def]
            rows = (
                await session.scalars(
                    select(AuditLog).where(AuditLog.action == "anomaly.triage.comment")
                )
            ).all()
            return [row.detail for row in rows]

        details = run(reader)
        assert details
        assert all(secret not in str(detail) for detail in details)
        assert all("comment" not in detail for detail in details)

    def test_a_patch_is_audited_without_its_note_text(self, world) -> None:
        note = f"NOTE-{uuid.uuid4().hex}"
        client = Client(TRIAGER)
        path = url("log_source", world["phase_a"])
        assert (
            client.patch(
                path,
                json={
                    "status": "CLOSED",
                    "disposition": "TRUE_POSITIVE",
                    "disposition_note": note,
                    "expected_version": 0,
                },
            ).status_code
            == 200
        )

        async def reader(session):  # type: ignore[no-untyped-def]
            rows = (
                await session.scalars(
                    select(AuditLog).where(AuditLog.action == "anomaly.triage.update")
                )
            ).all()
            return [row.detail for row in rows]

        details = run(reader)
        assert details
        assert all(note not in str(detail) for detail in details)
        assert any(
            change["field"] == "STATUS" for detail in details for change in detail["changes"]
        )


# ----------------------------------------------------------------- list surfaces
class TestListSurfaces:
    def _make_triage(self, client: Client, key: str, **body) -> dict:  # type: ignore[no-untyped-def]
        response = client.patch(
            url(TRIAGE_PATHS[key], body.pop("anomaly_id")),
            json={**body, "expected_version": 0},
        )
        assert response.status_code == 200, response.text
        return response.json()

    @pytest.fixture
    def triaged(self, world):  # type: ignore[no-untyped-def]
        """One item per analyst state, spread over the four families.

        log_source: IN_PROGRESS, owned by me.
        waf:         CLOSED/NOISE, owned by someone else.
        ips:         no row at all (NEW, unowned).
        pw:          a row exists but is NEW and unowned.
        """
        client = Client(TRIAGER)
        self._make_triage(
            client,
            "phase_a",
            anomaly_id=world["phase_a"],
            owner="me@example.com",
            status="IN_PROGRESS",
        )
        self._make_triage(
            client,
            "waf",
            anomaly_id=world["waf"],
            owner="other@example.com",
            status="CLOSED",
            disposition="NOISE",
        )
        self._make_triage(client, "pw", anomaly_id=world["pw"], disposition="NOISE")
        return world

    def test_every_list_row_carries_triage(self, world) -> None:
        body = Client(READER).get("/api/v1/anomalies", params={"limit": 200}).json()
        assert body["total"] == 4
        by_id = {item["id"]: item for item in body["items"]}
        for key in ("phase_a", "waf", "ips", "pw"):
            item = by_id[str(world[key])]
            assert item["triage"] == {
                "status": "NEW",
                "owner": None,
                "disposition": None,
                "updated_at": None,
            }

    def test_filter_by_status_across_families(self, triaged) -> None:
        client = Client(READER)
        params = {"limit": 200}

        def ids(status: str) -> set[str]:
            body = client.get(
                "/api/v1/anomalies", params={**params, "triage_status": status}
            ).json()
            return {item["id"] for item in body["items"]}

        assert ids("IN_PROGRESS") == {str(triaged["phase_a"])}
        assert ids("CLOSED") == {str(triaged["waf"])}
        # NEW includes the anomaly with no row and the row that is NEW.
        assert ids("NEW") == {str(triaged["ips"]), str(triaged["pw"])}

    def test_filter_by_owner_me_none_and_a_name(self, triaged) -> None:
        # The principal's subject is "me@example.com"; `me` must resolve to it.
        client = Client(TRIAGER)
        params = {"limit": 200}

        def ids(**extra):  # type: ignore[no-untyped-def]
            body = client.get("/api/v1/anomalies", params={**params, **extra}).json()
            return {item["id"] for item in body["items"]}

        assert ids(owner="me") == {str(triaged["phase_a"])}
        assert ids(owner="none") == {str(triaged["ips"]), str(triaged["pw"])}
        assert ids(owner="other@example.com") == {str(triaged["waf"])}

    def test_filter_by_disposition(self, triaged) -> None:
        client = Client(READER)
        body = client.get(
            "/api/v1/anomalies", params={"limit": 200, "disposition": "NOISE"}
        ).json()
        assert {item["id"] for item in body["items"]} == {
            str(triaged["waf"]),
            str(triaged["pw"]),
        }
        empty = client.get(
            "/api/v1/anomalies", params={"limit": 200, "disposition": "TRUE_POSITIVE"}
        ).json()
        assert empty["total"] == 0

    @pytest.mark.parametrize(
        ("subject_kind", "status", "key"),
        [
            ("LOG_SOURCE", "IN_PROGRESS", "phase_a"),
            ("WAF_ENDPOINT", "CLOSED", "waf"),
            ("IPS_SOURCE", "NEW", "ips"),
            ("PW_VALUE", "NEW", "pw"),
        ],
    )
    def test_each_family_branch_honours_the_filter(
        self, triaged, subject_kind: str, status: str, key: str
    ) -> None:
        body = Client(READER).get(
            "/api/v1/anomalies",
            params={
                "limit": 200,
                "subject_kind": subject_kind,
                "triage_status": status,
            },
        ).json()
        assert {item["id"] for item in body["items"]} == {str(triaged[key])}
        assert body["total"] == 1

    @pytest.mark.parametrize("key", ["phase_a", "waf", "ips", "pw"])
    def test_the_detail_response_carries_triage(self, triaged, key: str) -> None:
        body = Client(READER).get(f"/api/v1/anomalies/{triaged[key]}").json()
        assert "triage" in body
        assert body["triage"]["status"] in {"NEW", "IN_PROGRESS", "CLOSED"}

    def test_the_detail_reflects_the_stored_triage(self, triaged) -> None:
        body = Client(READER).get(f"/api/v1/anomalies/{triaged['waf']}").json()
        assert body["triage"] == {
            "status": "CLOSED",
            "owner": "other@example.com",
            "disposition": "NOISE",
            "updated_at": body["triage"]["updated_at"],
        }
        assert body["triage"]["updated_at"] is not None

    def test_the_summary_lists_carry_triage_too(self, world) -> None:
        """`recently_resolved` and `highest_deviation` use a bulk lookup, not a join."""
        assert (
            Client(TRIAGER)
            .patch(
                url("log_source", world["phase_a"]),
                json={"owner": "me@example.com", "expected_version": 0},
            )
            .status_code
            == 200
        )
        body = Client(READER).get("/api/v1/anomalies/summary").json()
        highest = {item["id"]: item for item in body["highest_deviation"]}
        assert highest[str(world["phase_a"])]["triage"]["owner"] == "me@example.com"


# ------------------------------------------------------------------- statement cost
def test_the_list_costs_the_same_at_1_and_100_anomalies(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed_one(session):  # type: ignore[no-untyped-def]
        inst = await make_instance(session)
        source = await make_log_source(
            session, inst, qradar_id=9100, name=f"cost-{uuid.uuid4().hex[:6]}"
        )
        session.add(phase_a(source.id))
        await session.flush()
        return {"source": source.id}

    ids = run(seed_one)

    async def add_anomalies(session):  # type: ignore[no-untyped-def]
        session.add_all(
            phase_a(ids["source"], detected_at=NOW - timedelta(seconds=i))
            for i in range(99)
        )

    client = Client(READER)
    first = client.get("/api/v1/anomalies", params={"limit": 200})
    assert first.json()["total"] == 1
    small = client.statements

    run(add_anomalies)
    second = client.get("/api/v1/anomalies", params={"limit": 200})
    assert second.json()["total"] == 100
    assert client.statements == small
