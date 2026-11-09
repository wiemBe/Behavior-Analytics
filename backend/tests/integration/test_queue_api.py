"""SOC-4b: `GET /queue`, the triage queue read model, over HTTP.

Everything is seeded through a committed session and read back through the route, as the
other API tests do. Times are relative to the test's own clock: `BEFORE` rows predate the
comparison instant, `AFTER` rows follow it.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.main import create_app
from app.models.alert import Alert
from app.models.catchall import CatchallSender
from app.models.enums import (
    AlertStatus,
    AnomalyState,
    RuleAnomalyType,
    Severity,
    TriageDisposition,
    TriageStatus,
)
from app.models.fleet import LogSourceClassification
from app.models.identity import AuditLog
from app.models.instance import QRadarInstance
from app.models.monitoring import CollectionWatermark
from app.models.offense import OffenseSnapshot
from app.models.property_watch import PropertyWatch
from app.models.rule import AnalyticsRule
from app.models.rule_anomaly import RuleAnomaly
from app.models.triage import AnomalyTriage
from app.models.waf import WafEndpoint
from app.security.rbac import Principal
from app.services import queue as queue_service
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_anomaly_triage_api import ips, phase_a, pw, waf
from tests.integration.test_fleet_api import Client, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the queue API tests",
    ),
]

URL = "/api/v1/queue"
NOW = datetime.now(UTC)
FROM = NOW - timedelta(hours=6)
BEFORE = NOW - timedelta(hours=10)
AFTER = NOW - timedelta(hours=2)
SINCE = FROM.strftime("%Y-%m-%dT%H:%M:%SZ")

READER = Principal(subject="reader", username="Alice", permissions=frozenset({"read:*"}))
NOBODY = Principal(subject="nobody", permissions=frozenset())
ONLY = {
    "anomaly": Principal(subject="p", permissions=frozenset({"read:anomalies"})),
    "alert": Principal(subject="p", permissions=frozenset({"read:alerts"})),
    "offense": Principal(subject="p", permissions=frozenset({"read:offenses"})),
    "source_review": Principal(subject="p", permissions=frozenset({"read:fleet"})),
    "catchall_sender": Principal(subject="p", permissions=frozenset({"read:fleet"})),
}
PERMISSION = {
    "anomaly": "read:anomalies",
    "alert": "read:alerts",
    "offense": "read:offenses",
    "source_review": "read:fleet",
    "catchall_sender": "read:fleet",
}
KINDS = list(PERMISSION)


# ------------------------------------------------------------------------------ helpers
def alert(**kw) -> Alert:  # type: ignore[no-untyped-def]
    values = dict(
        dedup_key=f"k-{uuid.uuid4().hex}",
        fingerprint=f"f-{uuid.uuid4().hex}",
        title="Alert title",
        severity=Severity.HIGH,
        status=AlertStatus.OPEN,
        source_type="custom_rule",
        opened_at=AFTER,
        first_seen_at=AFTER,
        evidence_snapshot={},
        source_anomaly_ids=[],
    )
    values.update(kw)
    return Alert(**values)


def offense(instance_id, offense_id: int, **kw) -> OffenseSnapshot:  # type: ignore[no-untyped-def]
    values = dict(
        instance_id=instance_id,
        qradar_offense_id=offense_id,
        captured_at=AFTER,
        start_time=AFTER,
        status="OPEN",
        magnitude=8,
        description=f"Offense {offense_id}",
        is_assigned=False,
    )
    values.update(kw)
    return OffenseSnapshot(**values)


def triage(family: str, anomaly_id, **kw) -> AnomalyTriage:  # type: ignore[no-untyped-def]
    values = dict(
        family=family,
        anomaly_id=anomaly_id,
        owner=None,
        status=TriageStatus.NEW,
        updated_by="somebody",
        updated_at=AFTER,
    )
    values.update(kw)
    return AnomalyTriage(**values)


def watermark(instance_id, *, last_run_at=None, failures: int = 0) -> CollectionWatermark:  # type: ignore[no-untyped-def]
    return CollectionWatermark(
        instance_id=instance_id,
        collector="offense_snapshot",
        last_run_at=last_run_at,
        consecutive_failures=failures,
    )


def rule(instance_id, qradar_id: int, **kw) -> AnalyticsRule:  # type: ignore[no-untyped-def]
    values = dict(
        instance_id=instance_id,
        qradar_id=qradar_id,
        name=f"rule-{qradar_id}",
        rule_type="EVENT",
        enabled=True,
        origin="USER",
        is_building_block=False,
    )
    values.update(kw)
    return AnalyticsRule(**values)


def rule_incident(rule_row, **kw) -> RuleAnomaly:  # type: ignore[no-untyped-def]
    values = dict(
        rule_id=rule_row.id,
        qradar_id=rule_row.qradar_id,
        is_building_block=rule_row.is_building_block,
        detector_type=RuleAnomalyType.RULE_STORM,
        fingerprint=f"rule-{uuid.uuid4().hex}",
        severity=Severity.HIGH,
        state=AnomalyState.OPEN,
        detected_at=AFTER,
        details={},
    )
    values.update(kw)
    return RuleAnomaly(**values)


async def _base(session):  # type: ignore[no-untyped-def]
    """A console, one source, and a fresh offense watermark."""
    inst = await make_instance(session)
    source = await make_log_source(session, inst, qradar_id=1, name="src-1")
    session.add(watermark(inst.id, last_run_at=datetime.now(UTC)))
    await session.flush()
    return inst, source


def get(client: Client, **params):  # type: ignore[no-untyped-def]
    flat = {k: v for k, v in params.items() if v is not None}
    return client.get(URL, params=flat)


def source_of(body: dict, kind: str) -> dict:
    return next(s for s in body["sources"] if s["kind"] == kind)


def rows_of(body: dict, kind: str) -> list[dict]:
    return [r for r in body["items"] if r["kind"] == kind]


def ids_of(body: dict, kind: str | None = None) -> list[str]:
    return [r["id"] for r in body["items"] if kind is None or r["kind"] == kind]


@pytest.fixture(autouse=True)
def _quiet_logger(caplog):  # type: ignore[no-untyped-def]
    """The fault-injection tests log the failure on purpose; keep it off the console."""
    caplog.set_level("CRITICAL", logger="app.services.queue")


# ------------------------------------------------------------------------------- auth
def test_the_route_requires_a_principal() -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"auth_provider": "oidc"}
    )
    with TestClient(app) as client:
        assert client.get(URL).status_code == 401


# ------------------------------------------------------------------ permission matrix
class TestPermissionMatrix:
    @pytest.fixture
    def world(self, db_schema):  # type: ignore[no-untyped-def]
        async def seed(session):  # type: ignore[no-untyped-def]
            inst, source = await _base(session)
            a = phase_a(source.id, opened_at=AFTER)
            session.add_all(
                [
                    a,
                    alert(),
                    offense(inst.id, 1),
                    CatchallSender(
                        instance_id=inst.id,
                        catchall_log_source_id=source.id,
                        identifier="10.0.0.1",
                        first_seen_at=AFTER,
                        last_seen_at=AFTER,
                        active=True,
                        review_status="NEW",
                    ),
                ]
            )
            await session.flush()

        run(seed)

    def test_a_caller_with_no_permission_gets_five_forbidden_sources_and_no_statement(
        self, world
    ) -> None:
        client = Client(NOBODY)
        response = get(client)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0
        for kind in KINDS:
            source = source_of(body, kind)
            assert source["state"] == "FORBIDDEN"
            assert source["permission"] == PERMISSION[kind]
            assert source["count"] is None and source["new_count"] is None
        # Not even a savepoint: a forbidden kind's statement is never run.
        assert client.statements == 0

    @pytest.mark.parametrize("kind", KINDS)
    def test_each_permission_opens_only_its_own_kind(self, world, kind: str) -> None:
        body = get(Client(ONLY[kind])).json()
        for other in KINDS:
            source = source_of(body, other)
            if PERMISSION[other] == PERMISSION[kind]:
                assert source["state"] in ("OK", "PARTIAL"), (other, source)
                assert source["count"] == 1
            else:
                assert source["state"] == "FORBIDDEN"
                assert source["permission"] == PERMISSION[other]
        assert {r["kind"] for r in body["items"]} <= {
            k for k in KINDS if PERMISSION[k] == PERMISSION[kind]
        }

    def test_anomalies_without_read_alerts_say_their_alert_chips_are_unavailable(
        self, world
    ) -> None:
        body = get(Client(ONLY["anomaly"])).json()
        source = source_of(body, "anomaly")
        assert source["state"] == "PARTIAL"
        assert source["reason_code"] == "ALERTS_NOT_READABLE"
        # The anomaly count itself is exact and listed.
        assert source["count"] == 1
        assert len(rows_of(body, "anomaly")) == 1

    def test_wildcards_satisfy_every_kind(self, world) -> None:
        body = get(Client(READER)).json()
        assert {s["state"] for s in body["sources"]} == {"OK"}
        admin = Principal(subject="a", permissions=frozenset({"admin:*"}))
        assert {s["state"] for s in get(Client(admin)).json()["sources"]} == {"OK"}


# ---------------------------------------------------------------- statement count
async def _seed_fleet(session, n: int) -> None:  # type: ignore[no-untyped-def]
    inst = await make_instance(session)
    session.add(watermark(inst.id, last_run_at=datetime.now(UTC)))
    for i in range(n):
        source = await make_log_source(session, inst, qradar_id=100 + i, name=f"s-{i}")
        a = phase_a(source.id, opened_at=AFTER - timedelta(minutes=i))
        session.add_all(
            [
                a,
                alert(),
                offense(inst.id, 1000 + i),
                CatchallSender(
                    instance_id=inst.id,
                    catchall_log_source_id=source.id,
                    identifier=f"id-{i}",
                    first_seen_at=AFTER,
                    last_seen_at=AFTER,
                    active=True,
                    review_status="NEW",
                ),
            ]
        )
        await session.flush()
        session.add(alert(source_anomaly_ids=[str(a.id)], source_type="log_source"))


def test_statement_count_is_the_same_at_one_and_at_forty_sources(db_schema) -> None:  # type: ignore[no-untyped-def]
    run(lambda s: _seed_fleet(s, 1))
    small = Client(READER)
    first = get(small, limit=100)
    assert first.status_code == 200
    one = small.statements
    # One row of each of the five kinds (the chip-carrying alert is not a row).
    assert len(first.json()["items"]) == 5

    run(lambda s: _seed_fleet(s, 40))
    big = Client(READER)
    second = get(big, limit=100)
    assert second.status_code == 200
    forty = big.statements
    assert len(second.json()["items"]) == 100
    assert one == forty, (one, forty)
    # Five sources in a savepoint each (three statements per guard), the offense freshness
    # read, one chip lookup: a handful, never a function of the row or source count.
    assert forty <= 25


# -------------------------------------------------------------------- fault injection
class TestFaultInjection:
    @pytest.fixture
    def world(self, db_schema):  # type: ignore[no-untyped-def]
        run(lambda s: _seed_fleet(s, 2))

    @pytest.mark.parametrize(
        ("kind", "loader"),
        [
            ("anomaly", "_load_anomalies"),
            ("alert", "_load_alerts"),
            ("offense", "_load_offenses"),
            ("source_review", "_load_source_review"),
            ("catchall_sender", "_load_catchall"),
        ],
    )
    def test_a_failing_source_reads_failed_and_the_others_still_list(
        self, world, monkeypatch, kind: str, loader: str
    ) -> None:
        async def broken(session, *args, **kwargs):  # type: ignore[no-untyped-def]
            # A real database error, so the savepoint has something to roll back.
            await session.execute(text("select * from table_that_does_not_exist"))

        monkeypatch.setattr(queue_service, loader, broken)
        response = get(Client(READER), limit=100)
        assert response.status_code == 200, response.text
        body = response.json()
        failed = source_of(body, kind)
        assert failed["state"] == "FAILED"
        assert failed["count"] is None and failed["new_count"] is None
        assert failed["reason_code"] == "LOAD_FAILED"
        assert rows_of(body, kind) == []
        # Never a zero total presented as exact.
        assert body["total_exact"] is False
        for other in KINDS:
            if other != kind:
                assert source_of(body, other)["state"] == "OK"
                assert source_of(body, other)["count"] >= 1
                assert rows_of(body, other), other

    def test_a_failed_chip_lookup_marks_the_anomalies_partial_but_keeps_the_rows(
        self, world, monkeypatch
    ) -> None:
        async def broken(session, ids):  # type: ignore[no-untyped-def]
            await session.execute(text("select * from table_that_does_not_exist"))

        monkeypatch.setattr(queue_service, "_alert_chips", broken)
        body = get(Client(READER), kind="anomaly").json()
        source = source_of(body, "anomaly")
        assert source["state"] == "PARTIAL"
        assert source["reason_code"] == "ALERT_LINK_FAILED"
        assert source["count"] == 2
        assert len(rows_of(body, "anomaly")) == 2
        assert all(r["alert"] is None for r in rows_of(body, "anomaly"))


# --------------------------------------------------------------- offense freshness
class TestOffenseFreshness:
    def _state(self, db_schema, seed):  # type: ignore[no-untyped-def]
        run(seed)
        body = get(Client(READER), kind="offense").json()
        return source_of(body, "offense"), body

    def test_no_console_is_not_collected(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            pass

        source, body = self._state(db_schema, seed)
        assert source["state"] == "NOT_COLLECTED"
        assert source["count"] is None
        assert body["items"] == [] and body["total_exact"] is False

    def test_a_console_with_no_watermark_row_is_not_collected(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            inst = await make_instance(session)
            session.add(offense(inst.id, 1))

        source, body = self._state(db_schema, seed)
        assert source["state"] == "NOT_COLLECTED"
        assert body["items"] == []

    def test_a_watermark_that_never_ran_is_not_collected(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            inst = await make_instance(session)
            session.add_all([watermark(inst.id), offense(inst.id, 1)])

        source, _ = self._state(db_schema, seed)
        assert source["state"] == "NOT_COLLECTED"

    def test_a_watermark_with_no_snapshot_at_all_is_not_collected(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            inst = await make_instance(session)
            session.add(watermark(inst.id, last_run_at=datetime.now(UTC)))

        source, _ = self._state(db_schema, seed)
        assert source["state"] == "NOT_COLLECTED"
        assert source["count"] is None

    def test_an_old_run_is_stale_and_the_rows_are_still_listed(self, db_schema) -> None:
        old = datetime.now(UTC) - timedelta(hours=3)

        async def seed(session):  # type: ignore[no-untyped-def]
            inst = await make_instance(session)
            session.add_all([watermark(inst.id, last_run_at=old), offense(inst.id, 1)])

        source, body = self._state(db_schema, seed)
        assert source["state"] == "STALE"
        assert source["reason_code"] == "COLLECTION_STALE"
        assert source["count"] == 1
        assert source["as_of"] is not None
        assert len(rows_of(body, "offense")) == 1

    def test_consecutive_failures_are_stale_even_with_a_recent_run(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            inst = await make_instance(session)
            session.add_all(
                [
                    watermark(inst.id, last_run_at=datetime.now(UTC), failures=2),
                    offense(inst.id, 1),
                ]
            )

        source, _ = self._state(db_schema, seed)
        assert source["state"] == "STALE"
        assert source["reason_code"] == "COLLECTION_FAILING"

    def test_a_fresh_run_is_ok_and_an_empty_list_is_a_real_none(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            inst = await make_instance(session)
            session.add_all(
                [
                    watermark(inst.id, last_run_at=datetime.now(UTC)),
                    offense(inst.id, 1, status="CLOSED"),
                ]
            )

        source, body = self._state(db_schema, seed)
        assert source["state"] == "OK"
        assert source["count"] == 0
        assert body["items"] == []

    def test_only_enabled_consoles_count(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            inst = await make_instance(session)
            session.add_all(
                [watermark(inst.id, last_run_at=datetime.now(UTC)), offense(inst.id, 1)]
            )
            disabled = QRadarInstance(
                name="off", console_host="x", provider_kind="mock", enabled=False
            )
            session.add(disabled)

        source, _ = self._state(db_schema, seed)
        assert source["state"] == "OK"


# --------------------------------------------------------------------- scope mapping
class TestScope:
    @pytest.fixture
    def world(self, db_schema):  # type: ignore[no-untyped-def]
        async def seed(session):  # type: ignore[no-untyped-def]
            inst, source = await _base(session)
            mine = phase_a(source.id, opened_at=AFTER)
            theirs = phase_a(source.id, opened_at=AFTER, anomaly_type="SILENCE")
            free = phase_a(source.id, opened_at=AFTER, anomaly_type="VOLUME_DROP")
            session.add_all([mine, theirs, free])
            await session.flush()
            session.add_all(
                [
                    triage("log_source", mine.id, owner="reader", status=TriageStatus.IN_PROGRESS),
                    triage("log_source", theirs.id, owner="colleague"),
                ]
            )
            a_open = alert(title="open")
            a_mine = alert(
                title="mine", status=AlertStatus.ACKNOWLEDGED, acknowledged_by="reader",
                acknowledged_at=BEFORE,
            )
            a_other = alert(
                title="other", status=AlertStatus.ACKNOWLEDGED, acknowledged_by="colleague",
                acknowledged_at=BEFORE,
            )
            session.add_all(
                [
                    a_open,
                    a_mine,
                    a_other,
                    offense(inst.id, 1, assigned_to="ALICE", is_assigned=True),
                    offense(inst.id, 2, assigned_to="bob", is_assigned=True),
                    offense(inst.id, 3),
                ]
            )
            return {"mine": str(mine.id), "theirs": str(theirs.id), "free": str(free.id)}

        self.ids = run(seed)

    def test_mine(self, world) -> None:
        body = get(Client(READER), scope="mine").json()
        assert ids_of(body, "anomaly") == [self.ids["mine"]]
        assert [r["title"] for r in rows_of(body, "alert")] == ["mine"]
        # Case-insensitive match of the QRadar assignee to the username.
        assert [r["id"].split(":")[1] for r in rows_of(body, "offense")] == ["1"]
        # Review rows are never "mine".
        for kind in ("source_review", "catchall_sender"):
            assert rows_of(body, kind) == []
            assert source_of(body, kind)["count"] == 0
            assert source_of(body, kind)["reason_code"] == "NOT_APPLICABLE_TO_FILTER"
        assert body["total"] == 3

    def test_unassigned(self, world) -> None:
        body = get(Client(READER), scope="unassigned").json()
        assert ids_of(body, "anomaly") == [self.ids["free"]]
        assert [r["title"] for r in rows_of(body, "alert")] == ["open"]
        assert [r["id"].split(":")[1] for r in rows_of(body, "offense")] == ["3"]
        # A source with no classification row is unassigned work.
        assert len(rows_of(body, "source_review")) == 1

    def test_all_lists_everything(self, world) -> None:
        body = get(Client(READER), scope="all").json()
        assert len(rows_of(body, "anomaly")) == 3
        assert len(rows_of(body, "alert")) == 3
        assert len(rows_of(body, "offense")) == 3

    def test_an_unmapped_owner_is_stated_and_never_an_empty_offense_list(self, world) -> None:
        dev = Principal(subject="local-dev", permissions=frozenset({"read:*"}))
        client = Client(dev)
        body = get(client, scope="mine").json()
        offense_source = source_of(body, "offense")
        assert offense_source["state"] == "PARTIAL"
        assert offense_source["reason_code"] == "OWNER_UNMAPPED"
        assert offense_source["count"] is None
        assert rows_of(body, "offense") == []
        assert body["total_exact"] is False
        # The other mappings still work for the same caller.
        assert source_of(body, "anomaly")["state"] in ("OK", "PARTIAL")

    def test_unmapped_owner_is_only_a_problem_for_mine(self, world) -> None:
        dev = Principal(subject="local-dev", permissions=frozenset({"read:*"}))
        body = get(Client(dev), scope="all").json()
        assert source_of(body, "offense")["state"] == "OK"
        assert len(rows_of(body, "offense")) == 3


# ----------------------------------------------------------- membership and ordering
class TestMembership:
    def test_only_unfinished_unsuppressed_incidents_of_all_four_families_are_listed(
        self, db_schema
    ) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            inst, source = await _base(session)
            endpoint = WafEndpoint(
                instance_id=inst.id, log_source_id=source.id, normalized_endpoint="/login",
                http_method="POST", normalizer_version=ENDPOINT_NORMALIZER_VERSION,
                first_seen_at=AFTER, last_seen_at=AFTER, tracked_since=AFTER, is_active=True,
            )
            watch = PropertyWatch(
                instance_id=inst.id, name="w", scope_kind="LOG_SOURCE",
                log_source_id=source.id, properties=[{"kind": "NORMALIZED", "name": "URL"}],
                detectors=["PW_VALUE_SPIKE"],
            )
            session.add_all([endpoint, watch])
            await session.flush()
            kept = {
                "log_source": phase_a(source.id, opened_at=AFTER),
                "waf": waf(endpoint.id, opened_at=AFTER),
                "ips": ips(source.id, opened_at=AFTER),
                "property_watch": pw(watch.id, opened_at=AFTER),
            }
            resolved = phase_a(
                source.id, state=AnomalyState.RESOLVED, opened_at=BEFORE, resolved_at=BEFORE,
                anomaly_type="SILENCE",
            )
            recovering = phase_a(
                source.id, state=AnomalyState.RECOVERING, opened_at=BEFORE,
                anomaly_type="VOLUME_DROP",
            )
            dropped = {
                "candidate": phase_a(
                    source.id, state=AnomalyState.CANDIDATE, anomaly_type="VOLUME_SPIKE",
                    detected_at=BEFORE - timedelta(days=1),
                ),
                "suppressed": phase_a(source.id, suppressed=True, anomaly_type="BASELINE_DRIFT"),
                "normal": phase_a(source.id, state=AnomalyState.NORMAL, anomaly_type="X"),
                "closed": phase_a(source.id, anomaly_type="Y"),
            }
            session.add_all([*kept.values(), resolved, recovering, *dropped.values()])
            await session.flush()
            session.add(
                triage(
                    "log_source", dropped["closed"].id, status=TriageStatus.CLOSED,
                    disposition=TriageDisposition.NOISE,
                )
            )
            return (
                {k: str(v.id) for k, v in kept.items()},
                {str(resolved.id), str(recovering.id)},
                {str(v.id) for v in dropped.values()},
            )

        kept, also, dropped = run(seed)
        body = get(Client(READER), kind="anomaly").json()
        listed = set(ids_of(body))
        assert set(kept.values()) <= listed
        assert also <= listed, "a detector-RESOLVED incident with unfinished triage stays"
        assert not (dropped & listed)
        assert {r["family"] for r in body["items"]} == set(kept)
        assert source_of(body, "anomaly")["count"] == 6

    def test_a_family_filter_alone_narrows_the_queue_to_anomalies(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            _, source = await _base(session)
            session.add_all([phase_a(source.id, opened_at=AFTER), alert()])

        run(seed)
        body = get(Client(READER), family="waf").json()
        assert [s["kind"] for s in body["sources"]] == ["anomaly"]
        assert body["items"] == []
        body = get(Client(READER), family="log_source").json()
        assert len(body["items"]) == 1

    def test_severity_then_oldest_first_with_review_rows_last(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            inst, _ = await _base(session)
            session.add_all(
                [
                    alert(title="low-old", severity=Severity.LOW,
                          opened_at=NOW - timedelta(days=3)),
                    alert(title="high-new", severity=Severity.HIGH,
                          opened_at=NOW - timedelta(hours=1)),
                    alert(title="high-old", severity=Severity.HIGH,
                          opened_at=NOW - timedelta(days=2)),
                    alert(title="crit", severity=Severity.CRITICAL,
                          opened_at=NOW - timedelta(minutes=5)),
                    offense(inst.id, 7, magnitude=9, start_time=NOW - timedelta(days=5)),
                    offense(inst.id, 6, magnitude=7, start_time=NOW - timedelta(days=4)),
                    offense(inst.id, 5, magnitude=5, start_time=NOW - timedelta(days=4)),
                    offense(inst.id, 4, magnitude=3, start_time=NOW - timedelta(days=4)),
                    offense(inst.id, 3, magnitude=2, start_time=NOW - timedelta(days=4)),
                    offense(inst.id, 2, magnitude=None, start_time=NOW - timedelta(days=4)),
                ]
            )

        run(seed)
        body = get(Client(READER), limit=100).json()
        order = [
            (r["severity"], r["title"] if r["kind"] == "alert" else r["magnitude"])
            for r in body["items"]
            if r["kind"] != "source_review"
        ]
        assert order == [
            ("CRITICAL", 9),  # offense, waited 5 days
            ("CRITICAL", "crit"),
            ("HIGH", 7),  # offense 4 days, ahead of the 2-day alert
            ("HIGH", "high-old"),
            ("HIGH", "high-new"),
            ("MEDIUM", 5),
            ("LOW", 3),
            ("LOW", "low-old"),  # 3 days: after the 4-day offense in the same band
            ("INFO", 2),
            (None, None),  # missing magnitude: no severity, never INFO
        ]
        assert body["items"][-1]["kind"] == "source_review"
        assert body["items"][-1]["severity"] is None

    def test_severity_filter_uses_the_offense_band_and_skips_review_rows(
        self, db_schema
    ) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            inst, _ = await _base(session)
            session.add_all(
                [
                    offense(inst.id, 1, magnitude=10),
                    offense(inst.id, 2, magnitude=8),
                    alert(severity=Severity.CRITICAL),
                    alert(severity=Severity.LOW),
                ]
            )

        run(seed)
        body = get(Client(READER), severity="CRITICAL").json()
        assert {(r["kind"], r["severity"]) for r in body["items"]} == {
            ("offense", "CRITICAL"),
            ("alert", "CRITICAL"),
        }
        assert source_of(body, "source_review")["count"] == 0
        assert source_of(body, "source_review")["reason_code"] == "NOT_APPLICABLE_TO_FILTER"

    def test_kind_filter_lists_only_the_requested_sources(self, db_schema) -> None:
        run(lambda s: _seed_fleet(s, 1))
        body = get(Client(READER), kind=["alert", "offense"]).json()
        assert [s["kind"] for s in body["sources"]] == ["alert", "offense"]
        assert {r["kind"] for r in body["items"]} == {"alert", "offense"}

    def test_a_page_across_kinds_has_no_repeat_and_no_gap(self, db_schema) -> None:
        run(lambda s: _seed_fleet(s, 6))
        client = Client(READER)
        full = ids_of(get(client, limit=100).json())
        paged: list[str] = []
        for offset in range(0, len(full), 7):
            paged += ids_of(get(client, limit=7, offset=offset).json())
        assert paged == full
        assert len(set(full)) == len(full)


# ------------------------------------------------------------ deep page cap and limits
class TestPaging:
    @pytest.fixture(autouse=True)
    def _empty(self, db_schema) -> None:  # type: ignore[no-untyped-def]
        pass

    @pytest.mark.parametrize(
        ("limit", "offset", "status"),
        [(100, 900, 200), (100, 901, 422), (1, 1000, 422), (1, 999, 200), (50, 951, 422)],
    )
    def test_offset_plus_limit_is_capped_at_one_thousand(
        self, limit: int, offset: int, status: int
    ) -> None:
        response = get(Client(READER), limit=limit, offset=offset)
        assert response.status_code == status, response.text

    def test_a_limit_above_one_hundred_is_refused(self) -> None:
        assert get(Client(READER), limit=101).status_code == 422
        assert get(Client(READER), limit=0).status_code == 422
        assert get(Client(READER), offset=-1).status_code == 422

    def test_an_unknown_kind_scope_or_severity_is_refused(self) -> None:
        assert get(Client(READER), kind="nonsense").status_code == 422
        assert get(Client(READER), scope="everyone").status_code == 422
        assert get(Client(READER), severity="URGENT").status_code == 422
        assert get(Client(READER), family="proxy").status_code == 422

    def test_the_effective_limit_is_echoed(self) -> None:
        body = get(Client(READER), limit=10, offset=20).json()
        assert (body["limit"], body["offset"]) == (10, 20)


# -------------------------------------------------------------------- since handling
class TestSince:
    @pytest.fixture
    def world(self, db_schema):  # type: ignore[no-untyped-def]
        async def seed(session):  # type: ignore[no-untyped-def]
            await _base(session)
            session.add_all(
                [
                    alert(title="3h", opened_at=NOW - timedelta(hours=3)),
                    alert(title="30h", opened_at=NOW - timedelta(hours=30)),
                ]
            )

        run(seed)

    def test_absent_means_the_last_24_hours(self, world) -> None:
        body = get(Client(READER), kind="alert").json()
        assert body["marker"]["kind"] == "DEFAULT_WINDOW"
        assert body["filters_applied"]["since"] is None
        flags = {r["title"]: r["new_since_last_visit"] for r in body["items"]}
        assert flags == {"3h": True, "30h": False}
        compared = datetime.fromisoformat(body["marker"]["compare_from"])
        as_of = datetime.fromisoformat(body["as_of"])
        assert as_of - compared == timedelta(hours=24)

    def test_present_overrides_the_window_and_is_echoed(self, world) -> None:
        since = (NOW - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = get(Client(READER), kind="alert", since=since).json()
        assert body["marker"]["kind"] == "OVERRIDE"
        assert {r["title"]: r["new_since_last_visit"] for r in body["items"]} == {
            "3h": True,
            "30h": True,
        }
        assert body["filters_applied"]["since"] is not None

    def test_a_naive_timestamp_is_read_as_utc(self, world) -> None:
        naive = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        body = get(Client(READER), kind="alert", since=naive).json()
        assert body["marker"]["kind"] == "OVERRIDE"
        assert not any(r["new_since_last_visit"] for r in body["items"])

    def test_the_future_is_refused(self, world) -> None:
        future = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert get(Client(READER), since=future).status_code == 422

    def test_malformed_is_refused(self, world) -> None:
        assert get(Client(READER), since="yesterday").status_code == 422

    def test_older_than_the_maximum_range_is_refused(self, world) -> None:
        old = (NOW - timedelta(days=get_settings().api_max_range_days + 2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        assert get(Client(READER), since=old).status_code == 422

    def test_since_never_changes_membership(self, world) -> None:
        recent = get(Client(READER), kind="alert", since=SINCE).json()
        ancient = get(
            Client(READER),
            kind="alert",
            since=(NOW - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ).json()
        assert sorted(ids_of(recent)) == sorted(ids_of(ancient))

    def test_new_only_keeps_flagged_rows_and_counts_them(self, world) -> None:
        body = get(Client(READER), kind="alert", new_only="true").json()
        assert [r["title"] for r in body["items"]] == ["3h"]
        assert source_of(body, "alert")["count"] == 1
        assert source_of(body, "alert")["new_count"] == 1
        everything = get(Client(READER), kind="alert").json()
        assert source_of(everything, "alert")["count"] == 2
        assert source_of(everything, "alert")["new_count"] == 1


# ------------------------------------------------------------- new and updated per kind
class TestChange:
    def test_anomaly_new_updated_and_my_own_change_is_not_updated(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            _, source = await _base(session)
            fresh = phase_a(source.id, opened_at=AFTER, detected_at=AFTER, anomaly_type="A")
            # Opened before, only its candidate time is recent: not new.
            old = phase_a(source.id, opened_at=BEFORE, detected_at=AFTER, anomaly_type="B")
            resolved = phase_a(
                source.id, opened_at=BEFORE, detected_at=BEFORE, resolved_at=AFTER,
                state=AnomalyState.RESOLVED, anomaly_type="C",
            )
            by_other = phase_a(source.id, opened_at=BEFORE, detected_at=BEFORE, anomaly_type="D")
            by_me = phase_a(source.id, opened_at=BEFORE, detected_at=BEFORE, anomaly_type="E")
            quiet = phase_a(source.id, opened_at=BEFORE, detected_at=BEFORE, anomaly_type="F")
            stale_triage = phase_a(
                source.id, opened_at=BEFORE, detected_at=BEFORE, anomaly_type="G"
            )
            fixtures = {
                "fresh": fresh, "old": old, "resolved": resolved, "by_other": by_other,
                "by_me": by_me, "quiet": quiet, "stale_triage": stale_triage,
            }
            session.add_all(fixtures.values())
            await session.flush()
            session.add_all(
                [
                    triage("log_source", by_other.id, status=TriageStatus.IN_PROGRESS,
                           owner="colleague", updated_by="colleague", updated_at=AFTER),
                    triage("log_source", by_me.id, status=TriageStatus.IN_PROGRESS,
                           owner="reader", updated_by="reader", updated_at=AFTER),
                    triage("log_source", stale_triage.id, status=TriageStatus.IN_PROGRESS,
                           owner="colleague", updated_by="colleague", updated_at=BEFORE),
                ]
            )
            return {k: str(v.id) for k, v in fixtures.items()}

        ids = run(seed)
        body = get(Client(READER), kind="anomaly", since=SINCE).json()
        change = {r["id"]: r["change"] for r in body["items"]}
        assert change[ids["fresh"]] == "NEW"
        assert change[ids["old"]] is None
        assert change[ids["resolved"]] == "UPDATED"
        assert change[ids["by_other"]] == "UPDATED"
        assert change[ids["by_me"]] is None, "my own change must not light the row"
        assert change[ids["quiet"]] is None
        assert change[ids["stale_triage"]] is None
        flags = {r["id"]: r["new_since_last_visit"] for r in body["items"]}
        assert flags[ids["fresh"]] and flags[ids["by_other"]]
        assert not flags[ids["by_me"]]
        assert source_of(body, "anomaly")["new_count"] == 3
        # The triage block and the owner travel with the row.
        row = next(r for r in body["items"] if r["id"] == ids["by_other"])
        assert row["triage"] == {"status": "IN_PROGRESS", "version": 1}
        assert row["owner"] == "colleague" and row["owner_kind"] == "app"
        untriaged = next(r for r in body["items"] if r["id"] == ids["fresh"])
        assert untriaged["triage"] == {"status": "NEW", "version": 0}

    def test_alert_new_updated_and_my_own_acknowledgement(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            await _base(session)
            session.add_all(
                [
                    alert(title="new", opened_at=AFTER),
                    alert(title="acked-by-other", opened_at=BEFORE,
                          status=AlertStatus.ACKNOWLEDGED, acknowledged_at=AFTER,
                          acknowledged_by="colleague"),
                    alert(title="acked-by-me", opened_at=BEFORE,
                          status=AlertStatus.ACKNOWLEDGED, acknowledged_at=AFTER,
                          acknowledged_by="reader"),
                    alert(title="refired", opened_at=BEFORE, last_seen_at=AFTER,
                          occurrence_count=9),
                ]
            )

        run(seed)
        body = get(Client(READER), kind="alert", since=SINCE).json()
        change = {r["title"]: r["change"] for r in body["items"]}
        assert change == {
            "new": "NEW",
            "acked-by-other": "UPDATED",
            "acked-by-me": None,
            "refired": None,  # a re-fire while open is not a change
        }
        refired = next(r for r in body["items"] if r["title"] == "refired")
        assert refired["occurrence_count"] == 9

    def test_offense_new_by_start_time_and_updated_by_a_later_snapshot(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            inst, _ = await _base(session)
            session.add_all(
                [
                    offense(inst.id, 1, start_time=AFTER, captured_at=AFTER),
                    # Backfilled: collected after the marker but started long before.
                    offense(inst.id, 2, start_time=BEFORE, captured_at=BEFORE),
                    offense(inst.id, 3, start_time=BEFORE, captured_at=BEFORE),
                    offense(inst.id, 3, start_time=BEFORE, captured_at=AFTER, magnitude=9),
                ]
            )

        run(seed)
        body = get(Client(READER), kind="offense", since=SINCE).json()
        change = {r["id"].split(":")[1]: r["change"] for r in rows_of(body, "offense")}
        assert change == {"1": "NEW", "2": None, "3": "UPDATED"}
        # The latest snapshot wins: offense 3 reads its newer magnitude.
        three = next(r for r in body["items"] if r["id"].endswith(":3"))
        assert three["magnitude"] == 9 and three["severity"] == "CRITICAL"

    def test_a_closed_latest_snapshot_leaves_the_queue(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            inst, _ = await _base(session)
            session.add_all(
                [
                    offense(inst.id, 1, captured_at=BEFORE),
                    offense(inst.id, 1, captured_at=AFTER, status="CLOSED"),
                ]
            )

        run(seed)
        body = get(Client(READER), kind="offense").json()
        assert body["items"] == [] and source_of(body, "offense")["count"] == 0

    def test_review_rows_are_new_by_first_seen_and_never_updated(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            inst = await make_instance(session)
            fresh = await make_log_source(session, inst, qradar_id=1, name="fresh",
                                          first_seen_at=AFTER)
            await make_log_source(session, inst, qradar_id=2, name="older",
                                  first_seen_at=BEFORE)
            done = await make_log_source(session, inst, qradar_id=3, name="done",
                                         first_seen_at=AFTER)
            pending = await make_log_source(session, inst, qradar_id=4, name="pending",
                                            first_seen_at=BEFORE)
            for source, review in ((done, "REVIEWED"), (pending, "NEW")):
                session.add(
                    LogSourceClassification(
                        log_source_id=source.id, source_class="OTHER", basis="NAME_REGEX",
                        conflicts=[], classified_at=AFTER, review_status=review,
                    )
                )
            for name, seen, status, active in (
                ("sender-new", AFTER, "NEW", True),
                ("sender-old", BEFORE, "NEW", True),
                ("sender-reviewed", AFTER, "REVIEWED", True),
                ("sender-dismissed", AFTER, "DISMISSED", True),
                ("sender-retired", AFTER, "NEW", False),
            ):
                session.add(
                    CatchallSender(
                        instance_id=inst.id, catchall_log_source_id=fresh.id,
                        identifier=name, first_seen_at=seen, last_seen_at=seen,
                        active=active, review_status=status,
                    )
                )
            return None

        run(seed)
        body = get(Client(READER), kind=["source_review", "catchall_sender"], since=SINCE).json()
        reviews = {r["title"]: (r["change"], r["state"]) for r in rows_of(body, "source_review")}
        assert reviews == {
            "fresh": ("NEW", "UNCLASSIFIED"),
            "older": (None, "UNCLASSIFIED"),
            "pending": (None, "NEW"),
        }
        senders = {r["title"]: r["change"] for r in rows_of(body, "catchall_sender")}
        assert senders == {"sender-new": "NEW", "sender-old": None}
        assert all(r["severity"] is None and r["owner"] is None for r in body["items"])
        assert source_of(body, "source_review")["new_count"] == 1


# ---------------------------------------------------------------------- alert chips
class TestAlertChips:
    def test_an_anomaly_alert_is_a_chip_on_its_anomaly_and_never_a_row(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            _, source = await _base(session)
            one = phase_a(source.id, opened_at=AFTER, anomaly_type="A")
            two = phase_a(source.id, opened_at=AFTER, anomaly_type="B")
            three = phase_a(source.id, opened_at=AFTER, anomaly_type="C")
            session.add_all([one, two, three])
            await session.flush()
            acked = alert(status=AlertStatus.ACKNOWLEDGED, source_anomaly_ids=[str(one.id)],
                          opened_at=AFTER)
            opened = alert(source_anomaly_ids=[str(one.id), str(two.id)],
                           opened_at=BEFORE)
            resolved = alert(status=AlertStatus.RESOLVED, source_anomaly_ids=[str(three.id)])
            plain = alert(title="plain")
            session.add_all([acked, opened, resolved, plain])
            await session.flush()
            return {
                "one": str(one.id), "two": str(two.id), "three": str(three.id),
                "acked": str(acked.id), "opened": str(opened.id), "plain": str(plain.id),
            }

        ids = run(seed)
        body = get(Client(READER), limit=100).json()
        by_id = {r["id"]: r for r in body["items"]}
        # An OPEN alert is preferred to a newer ACKNOWLEDGED one: it is the one needing action.
        assert by_id[ids["one"]]["alert"] == {"id": ids["opened"], "status": "OPEN"}
        assert by_id[ids["two"]]["alert"] == {"id": ids["opened"], "status": "OPEN"}
        assert by_id[ids["three"]]["alert"] is None, "a resolved alert is no chip"
        # Only the alert with no anomaly is a row of its own.
        assert [r["id"] for r in rows_of(body, "alert")] == [ids["plain"]]
        assert source_of(body, "alert")["count"] == 1
        assert ids["acked"] not in by_id and ids["opened"] not in by_id

    def test_a_chip_is_looked_up_only_for_the_anomalies_on_the_page(self, db_schema) -> None:
        async def seed(session):  # type: ignore[no-untyped-def]
            _, source = await _base(session)
            first = phase_a(
                source.id, opened_at=BEFORE, anomaly_type="A", severity=Severity.CRITICAL
            )
            second = phase_a(source.id, opened_at=AFTER, anomaly_type="B", severity=Severity.LOW)
            session.add_all([first, second])
            await session.flush()
            session.add(alert(source_anomaly_ids=[str(second.id)]))
            return str(second.id)

        second = run(seed)
        page_one = get(Client(READER), kind="anomaly", limit=1).json()
        assert page_one["items"][0]["id"] != second
        page_two = get(Client(READER), kind="anomaly", limit=1, offset=1).json()
        assert page_two["items"][0]["id"] == second
        assert page_two["items"][0]["alert"]["status"] == "OPEN"


# ------------------------------------------------------------ row contents and links
def test_rows_carry_absolute_time_age_and_an_application_link(db_schema) -> None:  # type: ignore[no-untyped-def]
    opened = datetime.now(UTC) - timedelta(hours=3)

    async def seed(session):  # type: ignore[no-untyped-def]
        inst, source = await _base(session)
        a = phase_a(source.id, opened_at=opened)
        session.add_all([a, offense(inst.id, 41, start_time=NOW - timedelta(hours=1))])
        await session.flush()
        return str(a.id), str(inst.id)

    anomaly_id, instance_id = run(seed)
    body = get(Client(READER), kind=["anomaly", "offense"]).json()
    row = next(r for r in body["items"] if r["kind"] == "anomaly")
    assert row["link"] == f"/anomalies/{anomaly_id}"
    assert row["subject"] == "src-1" and row["detector"] == "VOLUME_SPIKE"
    waited = (datetime.now(UTC) - opened).total_seconds()
    assert 0 <= waited - row["age_seconds"] < 120 and row["age_seconds"] >= 3 * 3600
    assert datetime.fromisoformat(row["waiting_since"]).tzinfo is not None
    other = next(r for r in body["items"] if r["kind"] == "offense")
    assert other["id"] == f"{instance_id}:41"
    assert other["link"] == f"/offenses/41?instance_id={instance_id}"
    assert other["magnitude"] == 8 and other["severity"] == "HIGH"
    assert other["owner"] is None and other["owner_kind"] is None
    assert all(r["link"].startswith("/") for r in body["items"])


# ------------------------------------------------------------------ the GET writes nothing
def test_a_get_writes_nothing_and_audits_nothing(db_schema) -> None:  # type: ignore[no-untyped-def]
    run(lambda s: _seed_fleet(s, 3))
    tables = (Alert, AnomalyTriage, AuditLog, OffenseSnapshot, CatchallSender,
              CollectionWatermark, LogSourceClassification)

    async def counts(session):  # type: ignore[no-untyped-def]
        out = {}
        for model in tables:
            out[model.__tablename__] = await session.scalar(select(func.count()).select_from(model))
        out["fingerprint"] = (
            await session.execute(
                text(
                    "select md5(string_agg(t::text, '' order by t::text)) from "
                    "(select updated_at, status, acknowledged_at from alert) t"
                )
            )
        ).scalar()
        out["watermark"] = (
            await session.execute(
                text("select md5(string_agg(t::text, '' order by t::text)) "
                     "from (select * from collection_watermark) t")
            )
        ).scalar()
        return out

    before = run(counts)
    client = Client(READER)
    for params in ({}, {"scope": "mine"}, {"since": SINCE, "new_only": "true"}):
        assert get(client, **params).status_code == 200
    # Fault paths roll back their savepoint; they must not leave anything behind either.
    assert run(counts) == before


# -------------------------------------------------------------------- rule family
class TestRuleFamily:
    def test_an_open_rule_incident_with_no_triage_row_is_listed(self, db_schema) -> None:  # type: ignore[no-untyped-def]
        async def seed(session):  # type: ignore[no-untyped-def]
            inst, _ = await _base(session)
            tracked = rule(inst.id, 501, name="Brute force")
            session.add(tracked)
            await session.flush()
            kept = rule_incident(tracked, opened_at=AFTER, detected_at=AFTER)
            session.add(kept)
            await session.flush()
            return str(kept.id)

        kept = run(seed)
        body = get(Client(READER), kind="anomaly").json()
        row = next(r for r in body["items"] if r["id"] == kept)
        assert row["family"] == "rule"
        assert row["subject"] == "Brute force"
        assert row["detector"] == "RULE_STORM"
        assert row["link"] == f"/anomalies/{kept}"
        assert row["triage"] == {"status": "NEW", "version": 0}
        assert source_of(body, "anomaly")["count"] == 1
        assert source_of(body, "anomaly")["new_count"] == 1

    def test_family_rule_lists_only_the_rule_incident(self, db_schema) -> None:  # type: ignore[no-untyped-def]
        async def seed(session):  # type: ignore[no-untyped-def]
            inst, source = await _base(session)
            tracked = rule(inst.id, 501, name="Brute force")
            session.add(tracked)
            await session.flush()
            kept = rule_incident(tracked, opened_at=AFTER, detected_at=AFTER)
            session.add_all([kept, phase_a(source.id, opened_at=AFTER), alert()])
            await session.flush()
            return str(kept.id)

        kept = run(seed)
        body = get(Client(READER), family="rule").json()
        assert [s["kind"] for s in body["sources"]] == ["anomaly"]
        assert ids_of(body) == [kept]
        assert body["items"][0]["family"] == "rule"
        assert body["total"] == 1

    def test_a_suppressed_or_triage_closed_rule_incident_is_not_listed(self, db_schema) -> None:  # type: ignore[no-untyped-def]
        async def seed(session):  # type: ignore[no-untyped-def]
            inst, _ = await _base(session)
            tracked = rule(inst.id, 501, name="Brute force")
            session.add(tracked)
            await session.flush()
            suppressed = rule_incident(tracked, opened_at=AFTER, suppressed=True)
            closed = rule_incident(
                tracked, opened_at=AFTER, detector_type=RuleAnomalyType.RULE_SILENCE
            )
            session.add_all([suppressed, closed])
            await session.flush()
            session.add(
                triage(
                    "rule",
                    closed.id,
                    status=TriageStatus.CLOSED,
                    disposition=TriageDisposition.NOISE,
                )
            )
            return str(suppressed.id), str(closed.id)

        hidden = set(run(seed))
        body = get(Client(READER), kind="anomaly").json()
        assert not (hidden & set(ids_of(body)))
        assert source_of(body, "anomaly")["count"] == 0

    def test_the_rule_rows_keep_the_total_and_new_windows(self, db_schema) -> None:  # type: ignore[no-untyped-def]
        async def seed(session):  # type: ignore[no-untyped-def]
            inst, _ = await _base(session)
            tracked = rule(inst.id, 501, name="Brute force")
            session.add(tracked)
            await session.flush()
            fresh = rule_incident(tracked, opened_at=AFTER, detected_at=AFTER)
            old = rule_incident(
                tracked,
                opened_at=BEFORE,
                detected_at=BEFORE,
                detector_type=RuleAnomalyType.RULE_SILENCE,
            )
            session.add_all([fresh, old])
            await session.flush()
            return str(fresh.id), str(old.id)

        fresh, old = run(seed)
        body = get(Client(READER), kind="anomaly", since=SINCE).json()
        assert set(ids_of(body)) == {fresh, old}
        assert source_of(body, "anomaly")["count"] == 2
        assert source_of(body, "anomaly")["new_count"] == 1
        new_only = get(Client(READER), kind="anomaly", since=SINCE, new_only="true").json()
        assert ids_of(new_only) == [fresh]
        assert source_of(new_only, "anomaly")["count"] == 1
        assert source_of(new_only, "anomaly")["new_count"] == 1

    def test_the_rule_rows_triage_row_is_found_by_its_family(self, db_schema) -> None:  # type: ignore[no-untyped-def]
        """The queue rejoins the triage row by the family of the subject kind. A rule
        incident's row must be found, not read as a missing (NEW, version 0) one."""
        async def seed(session):  # type: ignore[no-untyped-def]
            inst, _ = await _base(session)
            tracked = rule(inst.id, 501, name="Brute force")
            session.add(tracked)
            await session.flush()
            kept = rule_incident(tracked, opened_at=BEFORE, detected_at=BEFORE)
            session.add(kept)
            await session.flush()
            session.add(
                triage("rule", kept.id, status=TriageStatus.IN_PROGRESS, version=4)
            )
            return str(kept.id)

        kept = run(seed)
        body = get(Client(READER), kind="anomaly").json()
        row = next(r for r in body["items"] if r["id"] == kept)
        assert row["triage"] == {"status": "IN_PROGRESS", "version": 4}
