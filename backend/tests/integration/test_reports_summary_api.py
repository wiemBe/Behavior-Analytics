"""SOC-8b: `GET /reports/summary` over HTTP, on stored rows.

Everything is seeded through a committed session and read back through the route. Rows are
placed relative to the test's own clock inside `last_7d`; the retention cases use a calendar
month far enough back to pass a family's cutoff.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from statistics import median

import pytest

from app.models.alert import Alert
from app.models.enums import (
    AlertStatus,
    AnomalyState,
    Severity,
    TriageDisposition,
    TriageEventKind,
    TriageStatus,
)
from app.models.triage import AnomalyTriage, AnomalyTriageEvent
from app.security.rbac import Principal
from app.services import reports as reports_service
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_anomaly_triage_api import _seed as seed_every_family
from tests.integration.test_anomaly_triage_api import phase_a
from tests.integration.test_fleet_api import Client, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the report summary API tests",
    ),
]

URL = "/api/v1/reports/summary"
NOW = datetime.now(UTC)
OPENED = NOW - timedelta(days=3)
HOUR = 3600.0

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
ALERTS_ONLY = Principal(subject="a", permissions=frozenset({"read:alerts"}))
ANOMALIES_ONLY = Principal(subject="n", permissions=frozenset({"read:anomalies"}))
NOBODY = Principal(subject="nobody", permissions=frozenset())


# ------------------------------------------------------------------------------ helpers
def alert(**kw) -> Alert:  # type: ignore[no-untyped-def]
    values = dict(
        dedup_key=f"k-{uuid.uuid4().hex}",
        fingerprint=f"f-{uuid.uuid4().hex}",
        title="Alert title",
        severity=Severity.HIGH,
        status=AlertStatus.OPEN,
        source_type="log_source",
        opened_at=OPENED,
        first_seen_at=OPENED,
        evidence_snapshot={},
        source_anomaly_ids=[],
    )
    values.update(kw)
    return Alert(**values)


def acked(hours: float, **kw) -> Alert:  # type: ignore[no-untyped-def]
    return alert(
        status=AlertStatus.ACKNOWLEDGED,
        acknowledged_at=OPENED + timedelta(hours=hours),
        acknowledged_by="analyst",
        **kw,
    )


def resolved(hours: float, by: str = "analyst", **kw) -> Alert:  # type: ignore[no-untyped-def]
    return alert(
        status=AlertStatus.RESOLVED,
        resolved_at=OPENED + timedelta(hours=hours),
        resolved_by=by,
        **kw,
    )


def get(client: Client, **params):  # type: ignore[no-untyped-def]
    return client.get(URL, params={k: v for k, v in params.items() if v is not None})


def put(*rows) -> None:  # type: ignore[no-untyped-def]
    async def work(session):  # type: ignore[no-untyped-def]
        session.add_all(rows)

    run(work)


async def _seed_incidents(session, specs, anomaly_type=None):  # type: ignore[no-untyped-def]
    inst = await make_instance(session)
    source = await make_log_source(session, inst, qradar_id=1, name="src")
    for spec in specs:
        events = spec.pop("events", [])
        opened = spec.pop("opened", OPENED)
        status = spec.pop("status", TriageStatus.CLOSED)
        disposition = spec.pop("disposition", None)
        extra = dict(spec)
        if anomaly_type is not None:
            extra["anomaly_type"] = anomaly_type
        incident = phase_a(source.id, opened_at=opened, detected_at=opened, **extra)
        session.add(incident)
        await session.flush()
        if status is None:
            continue
        row = AnomalyTriage(
            family="log_source",
            anomaly_id=incident.id,
            status=status,
            disposition=disposition,
            updated_by="analyst",
            updated_at=opened,
            version=1,
        )
        session.add(row)
        await session.flush()
        for kind, actor, to_value, hours in events:
            session.add(
                AnomalyTriageEvent(
                    triage_id=row.id,
                    kind=kind,
                    actor=actor,
                    created_at=opened + timedelta(hours=hours),
                    to_value=to_value,
                    triage_version=1,
                )
            )
    await session.flush()


def seed_incidents(specs, anomaly_type=None) -> None:  # type: ignore[no-untyped-def]
    async def work(session):  # type: ignore[no-untyped-def]
        await _seed_incidents(session, specs, anomaly_type)

    run(work)


S = TriageEventKind.STATUS
O = TriageEventKind.OWNER  # noqa: E741


def closed_by_analyst(hours: float, disposition=TriageDisposition.TRUE_POSITIVE) -> dict:  # type: ignore[no-untyped-def]
    return {
        "events": [(S, "analyst", "CLOSED", hours)],
        "status": TriageStatus.CLOSED,
        "disposition": disposition,
    }


# --------------------------------------------------------------------------- the route
class TestRequest:
    def test_a_default_request_is_last_7d(self, db_schema) -> None:
        body = get(Client(READER)).json()
        assert body["period"] == "last_7d"
        start = datetime.fromisoformat(body["start"])
        end = datetime.fromisoformat(body["end"])
        assert end - start == timedelta(days=7)
        assert body["generated_at"] == body["end"]

    def test_an_unknown_period_is_refused_with_its_code(self, db_schema) -> None:
        response = get(Client(READER), period="last_90d")
        assert response.status_code == 422
        assert response.json()["detail_code"] == "reports.period_unknown"

    def test_calendar_periods_need_a_boundary_start(self, db_schema) -> None:
        client = Client(READER)
        missing = get(client, period="calendar_week")
        assert missing.status_code == 422
        assert missing.json()["detail_code"] == "reports.start_required"
        off = get(client, period="calendar_week", start="2026-11-03T00:00:00Z")
        assert off.status_code == 422
        assert off.json()["detail_code"] == "reports.start_not_boundary"
        ok = get(client, period="calendar_week", start="2026-11-02T00:00:00Z")
        assert ok.status_code == 200
        assert ok.json()["end"].startswith("2026-11-09T00:00:00")

    def test_the_current_week_ends_at_generated_at_and_a_future_start_is_refused(
        self, db_schema
    ) -> None:
        now = datetime.now(UTC)
        monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        client = Client(READER)
        start = monday.strftime("%Y-%m-%dT%H:%M:%SZ")
        body = get(client, period="calendar_week", start=start).json()
        assert body["end"] == body["generated_at"]
        assert datetime.fromisoformat(body["end"]) - now < timedelta(minutes=1)
        nxt = monday + timedelta(days=7)
        response = get(client, period="calendar_week", start=nxt.strftime("%Y-%m-%dT%H:%M:%SZ"))
        assert response.status_code == 422
        assert response.json()["detail_code"] == "reports.start_in_future"

    def test_an_extra_query_parameter_is_refused(self, db_schema) -> None:
        response = Client(READER).get(URL, params={"period": "last_7d", "since": "x"})
        assert response.status_code == 422
        assert response.json()["detail_code"] == "reports.param_unknown"

    def test_start_is_ignored_for_last_periods(self, db_schema) -> None:
        response = get(Client(READER), period="last_30d", start="not a date")
        assert response.status_code == 200

    def test_the_route_requires_a_principal(self) -> None:
        from fastapi.testclient import TestClient

        from app.core.config import get_settings
        from app.main import create_app

        app = create_app()
        app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
            update={"auth_provider": "oidc"}
        )
        with TestClient(app) as client:
            assert client.get(URL).status_code == 401


# --------------------------------------------------------------------------- alerts
class TestAlerts:
    def test_counts_by_family_and_severity_with_an_unknown_source_type_as_null(
        self, db_schema
    ) -> None:
        put(
            alert(severity=Severity.HIGH),
            alert(severity=Severity.HIGH),
            alert(severity=Severity.LOW),
            alert(severity=Severity.HIGH, source_type="waf_endpoint"),
            alert(severity=Severity.HIGH, source_type="ips_source"),
            alert(severity=Severity.HIGH, source_type="ips_entity"),
            alert(severity=Severity.MEDIUM, source_type="no_such_producer"),
            alert(opened_at=NOW - timedelta(days=9), first_seen_at=NOW - timedelta(days=9)),
        )
        block = get(Client(READER)).json()["alerts"]
        assert block["state"] == "OK"
        cells = {(c["family"], c["severity"]): c["count"] for c in block["rows"]}
        assert cells == {
            ("log_source", "HIGH"): 2,
            ("log_source", "LOW"): 1,
            ("waf", "HIGH"): 1,
            ("ips", "HIGH"): 2,
            (None, "MEDIUM"): 1,
        }

    def test_the_period_is_half_open(self, db_schema) -> None:
        start = datetime(2026, 11, 2, tzinfo=UTC)
        end = datetime(2026, 11, 9, tzinfo=UTC)
        put(
            alert(opened_at=start, first_seen_at=start),
            alert(opened_at=end - timedelta(seconds=1), first_seen_at=start),
            alert(opened_at=end, first_seen_at=start),
            alert(opened_at=start - timedelta(seconds=1), first_seen_at=start),
        )
        block = get(Client(READER), period="calendar_week", start="2026-11-02T00:00:00Z").json()[
            "alerts"
        ]
        assert sum(c["count"] for c in block["rows"]) == 2

    def test_mtta_and_mttr_are_medians_of_known_data(self, db_schema) -> None:
        ack_hours = [1, 2, 3, 4, 10]
        res_hours = [2, 4, 6, 8, 40]
        put(*[acked(h) for h in ack_hours], *[resolved(h) for h in res_hours])
        block = get(Client(READER)).json()["alerts"]
        assert block["mtta"] == {
            "median_seconds": median(ack_hours) * HOUR,
            "n": 5,
            "state": "OK",
        }
        assert block["mttr"] == {
            "median_seconds": median(res_hours) * HOUR,
            "n": 5,
            "state": "OK",
        }

    def test_an_engine_or_system_resolver_is_not_an_mttr_sample(self, db_schema) -> None:
        analysts = [resolved(h) for h in (2, 4, 6, 8, 10)]
        engines = [
            resolved(1000, by=actor)
            for actor in (
                "system:anomaly-engine",
                "system:log-source-collection-monitor",
                "anomaly-engine",
                "waf-anomaly-engine",
                "ips-anomaly-engine",
                "pw-engine",
                "custom-rule-evaluator",
                "search-scheduler",
                "rule-engine",
            )
        ]
        put(*analysts, *engines)
        mttr = get(Client(READER)).json()["alerts"]["mttr"]
        assert mttr["n"] == 5
        assert mttr["median_seconds"] == 6 * HOUR

    def test_n_of_four_is_null_and_n_of_five_has_a_value(self, db_schema) -> None:
        put(*[acked(h) for h in (1, 2, 3, 4)], *[resolved(h) for h in (1, 2, 3, 4, 5)])
        block = get(Client(READER)).json()["alerts"]
        assert block["mtta"] == {"median_seconds": None, "n": 4, "state": "INSUFFICIENT_DATA"}
        assert block["mttr"]["state"] == "OK" and block["mttr"]["n"] == 5

    def test_an_empty_period_has_no_median_and_no_zero(self, db_schema) -> None:
        block = get(Client(READER)).json()["alerts"]
        assert block["rows"] == []
        assert block["mtta"] == {"median_seconds": None, "n": 0, "state": "INSUFFICIENT_DATA"}


# ------------------------------------------------------------------------ anomalies
class TestAnomalies:
    def test_union_of_the_five_family_tables(self, db_schema) -> None:
        run(seed_every_family)
        block = get(Client(READER)).json()["anomalies"]
        counted = {(c["family"], c["severity"]): c["count"] for c in block["rows"]}
        # `_seed` leaves opened_at null: membership falls back to detected_at.
        assert counted == {
            ("log_source", "HIGH"): 1,
            ("waf", "MEDIUM"): 1,
            ("ips", "MEDIUM"): 1,
            ("property_watch", "LOW"): 1,
        }
        assert block["state"] == "OK"

    def test_a_candidate_is_not_an_opened_incident(self, db_schema) -> None:
        seed_incidents(
            [
                {"status": None, "state": AnomalyState.CANDIDATE},
                {"status": None, "state": AnomalyState.OPEN},
            ]
        )
        block = get(Client(READER)).json()["anomalies"]
        assert sum(c["count"] for c in block["rows"]) == 1

    def test_opened_at_wins_over_detected_at(self, db_schema) -> None:
        seed_incidents(
            [
                {"status": None, "opened": NOW - timedelta(days=1)},
                {"status": None, "opened": NOW - timedelta(days=20)},
            ]
        )
        block = get(Client(READER)).json()["anomalies"]
        assert sum(c["count"] for c in block["rows"]) == 1


# --------------------------------------------------------------------- anomaly clocks
class TestClocks:
    def test_medians_from_triage_events(self, db_schema) -> None:
        # Ack after 1..5 h (an owner event, or a status change), closed after 2..10 h.
        specs = []
        for ack, close in zip((1, 2, 3, 4, 5), (2, 4, 6, 8, 10), strict=True):
            first = (O, "analyst", "alice", ack)
            specs.append(
                {
                    "events": [first, (S, "analyst", "CLOSED", close)],
                    "disposition": TriageDisposition.TRUE_POSITIVE,
                }
            )
        seed_incidents(specs)
        block = get(Client(READER)).json()["anomaly_clocks"]
        assert block["mtta"] == {"median_seconds": 3 * HOUR, "n": 5, "state": "OK"}
        assert block["mttr"] == {"median_seconds": 6 * HOUR, "n": 5, "state": "OK"}

    def test_a_direct_close_from_new_counts_as_the_acknowledgement(self, db_schema) -> None:
        seed_incidents(
            [
                {**closed_by_analyst(h), "events": [(S, "analyst", "CLOSED", h)]}
                for h in (1, 2, 3, 4, 5)
            ]
        )
        block = get(Client(READER)).json()["anomaly_clocks"]
        assert block["mtta"]["n"] == 5 and block["mtta"]["median_seconds"] == 3 * HOUR

    def test_stale_closes_and_the_system_actor_are_not_response_times(self, db_schema) -> None:
        analysts = [closed_by_analyst(h) for h in (1, 2, 3, 4, 5)]
        stale = [
            {
                "events": [(S, "system", "CLOSED", 500)],
                "disposition": TriageDisposition.STALE,
            }
            for _ in range(4)
        ]
        seed_incidents(analysts + stale)
        block = get(Client(READER)).json()["anomaly_clocks"]
        assert block["mttr"] == {"median_seconds": 3 * HOUR, "n": 5, "state": "OK"}
        assert block["mtta"]["n"] == 5 and block["mtta"]["median_seconds"] == 3 * HOUR

    def test_a_still_open_item_and_a_candidate_are_left_out(self, db_schema) -> None:
        specs = [closed_by_analyst(h) for h in (1, 2, 3, 4)]
        specs.append(
            {
                "events": [(S, "analyst", "CLOSED", 1)],
                "disposition": TriageDisposition.TRUE_POSITIVE,
                "state": AnomalyState.CANDIDATE,
            }
        )
        specs.append({"events": [(O, "analyst", "alice", 1)], "status": TriageStatus.IN_PROGRESS})
        seed_incidents(specs)
        block = get(Client(READER)).json()["anomaly_clocks"]
        assert block["mttr"] == {"median_seconds": None, "n": 4, "state": "INSUFFICIENT_DATA"}
        # The in-progress item is acknowledged (n = 5 would need the candidate, which is out).
        assert block["mtta"]["n"] == 5

    def test_an_event_before_the_incident_opened_is_not_a_response(self, db_schema) -> None:
        specs = [closed_by_analyst(h) for h in (1, 2, 3, 4)]
        specs.append(closed_by_analyst(-2))
        seed_incidents(specs)
        block = get(Client(READER)).json()["anomaly_clocks"]
        assert block["mtta"]["n"] == 4 and block["mtta"]["median_seconds"] is None
        assert block["mttr"]["n"] == 4

    def test_n_of_four_is_null(self, db_schema) -> None:
        seed_incidents([closed_by_analyst(h) for h in (1, 2, 3, 4)])
        block = get(Client(READER)).json()["anomaly_clocks"]
        assert block["mtta"]["median_seconds"] is None
        assert block["mtta"]["state"] == "INSUFFICIENT_DATA"


# ------------------------------------------------------------------------------ noise
class TestNoise:
    def test_ratio_counts_judged_closes_only(self, db_schema) -> None:
        judged = [closed_by_analyst(1, TriageDisposition.NOISE) for _ in range(2)] + [
            closed_by_analyst(1, TriageDisposition.TRUE_POSITIVE) for _ in range(2)
        ]
        judged.append(closed_by_analyst(1, TriageDisposition.COLLECTION_PROBLEM))
        not_judged = [
            {"events": [], "disposition": TriageDisposition.STALE} for _ in range(10)
        ] + [{"events": [], "status": TriageStatus.IN_PROGRESS} for _ in range(10)]
        seed_incidents(judged + not_judged)
        rows = get(Client(READER)).json()["noise_by_detector"]["rows"]
        assert len(rows) == 1
        row = rows[0]
        assert (row["judged"], row["noise"], row["state"]) == (5, 2, "OK")
        assert row["ratio"] == pytest.approx(0.4)

    def test_fewer_than_five_judged_is_insufficient_data_not_a_ratio(self, db_schema) -> None:
        seed_incidents([closed_by_analyst(1, TriageDisposition.NOISE) for _ in range(4)])
        row = get(Client(READER)).json()["noise_by_detector"]["rows"][0]
        assert (row["judged"], row["noise"]) == (4, 4)
        assert row["ratio"] is None and row["state"] == "INSUFFICIENT_DATA"

    def test_nothing_judged_is_insufficient_data_not_zero_percent(self, db_schema) -> None:
        seed_incidents([{"status": None} for _ in range(8)])
        row = get(Client(READER)).json()["noise_by_detector"]["rows"][0]
        assert row["judged"] == 0
        assert row["ratio"] is None and row["state"] == "INSUFFICIENT_DATA"


# -------------------------------------------------------------------------- retention
class TestRetention:
    def test_a_window_older_than_a_cutoff_is_partial_for_that_family_only(self, db_schema) -> None:
        # About 8 months back: beyond waf/pw (180 d), inside log_source/ips/rule (365 d).
        month = (NOW.replace(day=1, hour=0, minute=0, second=0, microsecond=0)) - timedelta(
            days=240
        )
        month = month.replace(day=1)
        body = get(
            Client(READER), period="calendar_month", start=month.strftime("%Y-%m-%dT00:00:00Z")
        ).json()
        for name in ("anomalies", "anomaly_clocks", "noise_by_detector"):
            block = body[name]
            assert block["state"] == "PARTIAL", name
            by = {c["family"]: c for c in block["coverage"]}
            for family in ("waf", "property_watch"):
                assert by[family]["state"] == "PARTIAL"
                assert by[family]["retention_days"] == 180
                assert by[family]["covered_from"] is not None
            for family in ("log_source", "ips", "rule"):
                assert by[family]["state"] == "OK" and by[family]["covered_from"] is None
        # Alerts are never deleted: the alert block is complete, and an empty one is measured.
        assert body["alerts"]["state"] == "OK"

    def test_partial_marks_a_measured_median_not_a_zero(self, db_schema) -> None:
        month = (NOW - timedelta(days=240)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        opened = month + timedelta(days=2)
        specs = [
            {**closed_by_analyst(h), "opened": opened} for h in (1, 2, 3, 4, 5)
        ]
        seed_incidents(specs)
        body = get(
            Client(READER), period="calendar_month", start=month.strftime("%Y-%m-%dT00:00:00Z")
        ).json()
        assert body["anomaly_clocks"]["mttr"] == {
            "median_seconds": 3 * HOUR,
            "n": 5,
            "state": "PARTIAL",
        }

    def test_a_recent_window_is_not_partial(self, db_schema) -> None:
        body = get(Client(READER)).json()
        for name in ("anomalies", "anomaly_clocks", "noise_by_detector"):
            assert body[name]["state"] == "OK"
            assert all(c["covered_from"] is None for c in body[name]["coverage"])


# ------------------------------------------------------------------------ permissions
class TestPermissions:
    def test_alerts_only_forbids_the_three_anomaly_blocks(self, db_schema) -> None:
        put(alert())
        body = get(Client(ALERTS_ONLY)).json()
        assert body["alerts"]["state"] == "OK" and body["alerts"]["rows"]
        for name in ("anomalies", "anomaly_clocks", "noise_by_detector"):
            assert body[name]["state"] == "FORBIDDEN"
            assert body[name]["permission"] == "read:anomalies"
            assert body[name].get("rows") is None and body[name].get("coverage") is None

    def test_anomalies_only_forbids_the_alert_block_and_keeps_the_others(self, db_schema) -> None:
        put(alert())
        run(seed_every_family)
        body = get(Client(ANOMALIES_ONLY)).json()
        assert body["alerts"]["state"] == "FORBIDDEN"
        assert body["alerts"]["permission"] == "read:alerts"
        assert body["alerts"]["rows"] is None and body["alerts"]["mtta"] is None
        assert body["anomalies"]["state"] == "OK" and body["anomalies"]["rows"]
        assert body["anomaly_clocks"]["state"] == "OK"
        assert body["noise_by_detector"]["state"] == "OK"

    def test_no_permission_runs_no_statement(self, db_schema) -> None:
        client = Client(NOBODY)
        body = get(client).json()
        for name in ("alerts", "anomalies", "anomaly_clocks", "noise_by_detector"):
            assert body[name]["state"] == "FORBIDDEN"
        assert client.statements == 0

    def test_a_failed_block_is_insufficient_data_and_hides_no_other(
        self, db_schema, monkeypatch, caplog
    ) -> None:
        put(alert())

        async def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("statement failed")

        monkeypatch.setattr(reports_service, "anomalies_block", boom)
        caplog.set_level("CRITICAL", logger="app.services.reports")
        body = get(Client(READER)).json()
        assert body["anomalies"]["state"] == "INSUFFICIENT_DATA"
        assert body["anomalies"]["reason_code"] == "reports.reason.block_failed"
        assert body["anomalies"]["rows"] is None
        assert body["alerts"]["state"] == "OK" and body["alerts"]["rows"]
        assert body["anomaly_clocks"]["state"] == "OK"
        assert body["noise_by_detector"]["state"] == "OK"


# ---------------------------------------------------------------------- statement cost
def test_the_statement_count_is_one_per_block_whatever_the_row_count(db_schema) -> None:
    client = Client(READER)
    get(client)
    empty = client.statements
    assert empty == 4  # alerts, anomalies, clocks, noise: one each

    seed_incidents([closed_by_analyst(h % 5 + 1) for h in range(12)])
    put(*[acked(h % 5 + 1) for h in range(12)], *[resolved(h % 5 + 1) for h in range(12)])
    get(client)
    small = client.statements
    seed_incidents([closed_by_analyst(h % 5 + 1) for h in range(40)])
    put(*[acked(h % 5 + 1, source_type="waf_endpoint") for h in range(40)])
    run(seed_every_family)
    get(client)
    assert small == empty == client.statements == 4
