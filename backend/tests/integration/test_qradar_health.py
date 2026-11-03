"""QRadar health in detail against a real database (HLT-2): collector, API, retention."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.collectors.qradar_health_collector import QRadarHealthCollector
from app.core.config import get_settings
from app.models.enums import SearchFamily
from app.models.qradar_health import (
    QRadarHealthMetricBucket,
    QRadarHealthNotificationBucket,
    QRadarHealthRestSample,
    QRadarHealthRun,
)
from app.models.search_cadence import SearchCadenceSetting
from app.providers.base import ProviderUnavailableError
from app.providers.dto import CustomPropertyDTO
from app.providers.mock import MockQRadarProvider
from app.security.rbac import Principal
from app.services.qradar_health_retention import QRadarHealthRetention
from tests.integration.factories import make_instance
from tests.integration.test_fleet_api import Client, run
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run QRadar health integration tests",
    ),
]

NOW = datetime(2026, 11, 3, 12, 17, tzinfo=UTC)
SLOT = datetime(2026, 11, 3, 12, 15, tzinfo=UTC)
WINDOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
VIEWER = Principal(subject="viewer", permissions=frozenset({"read:*"}))
CADENCE = 900


@pytest_asyncio.fixture
async def pin_cadence(db_session):  # type: ignore[no-untyped-def]
    db_session.add(
        SearchCadenceSetting(
            family=SearchFamily.QRADAR_HEALTH.value, cadence_seconds=CADENCE, updated_at=NOW
        )
    )
    await db_session.commit()


def collector(session, provider, **overrides: Any) -> QRadarHealthCollector:  # type: ignore[no-untyped-def]
    settings = get_settings().model_copy(update=overrides)
    return QRadarHealthCollector(
        session,
        provider,
        settings=settings,
        clock=lambda: NOW,
        health_provider_factory=lambda _instance: MockQRadarProvider(seed=1, now=NOW),
    )


def metric_row(host: str, metric: str, element: str | None, value: float | None) -> dict:
    return {
        "source": f"Health Metrics-2 :: {host}",
        "metric": metric,
        "element": element,
        "max_value": value,
        "avg_value": value,
        "samples": 30,
    }


async def metric_rows(session) -> list[QRadarHealthMetricBucket]:  # type: ignore[no-untyped-def]
    found = await session.scalars(
        select(QRadarHealthMetricBucket)
        .execution_options(populate_existing=True)
        .order_by(
            QRadarHealthMetricBucket.host,
            QRadarHealthMetricBucket.metric,
            QRadarHealthMetricBucket.element,
        )
    )
    return list(found.all())


async def only_run(session) -> QRadarHealthRun:  # type: ignore[no-untyped-def]
    return await session.scalar(
        select(QRadarHealthRun).execution_options(populate_existing=True)
    )


@pytest.mark.usefixtures("pin_cadence")
class TestRun:
    async def test_a_run_stores_the_window_the_buckets_and_one_rest_sample(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        provider = MockQRadarProvider(seed=1)

        report = await collector(db_session, provider).collect(inst, slot=SLOT)

        assert report.metrics_state == "OK" and report.notifications_state == "OK"
        assert report.searches_sent == 2 and report.buckets_complete == 1
        assert report.rest_sample_written is True
        assert report.as_dict()["status"] == "ok"
        run_row = await only_run(db_session)
        assert (run_row.window_start, run_row.window_end) == (WINDOW, SLOT)
        assert run_row.cadence_seconds == CADENCE and run_row.collected_at == NOW
        assert (run_row.metrics_state, run_row.notifications_state) == ("OK", "OK")
        assert run_row.dropped_mounts == 2 and run_row.query_version == 1
        rows = await metric_rows(db_session)
        assert {(r.metric, r.element) for r in rows if r.metric == "DiskUsage"} == {
            ("DiskUsage", "/"),
            ("DiskUsage", "/store"),
            ("DiskUsage", "/tmp"),  # noqa: S108
        }
        assert {r.host for r in rows} == {"qradarce2"}
        assert all(r.bucket_start == WINDOW and r.bucket_end == SLOT for r in rows)
        notices = (await db_session.scalars(select(QRadarHealthNotificationBucket))).all()
        assert {(n.kind, n.host, n.qid_name, n.events) for n in notices} == {
            ("GROUP", "qradarce2", "Warning Message", 3),
            ("GROUP", "qradarce2", "SAR Sentinel: threshold crossed", 1),
        }
        samples = (await db_session.scalars(select(QRadarHealthRestSample))).all()
        assert len(samples) == 1 and samples[0].slot_start == SLOT
        assert samples[0].reachable is True and samples[0].sampled_at == NOW

    async def test_a_second_run_of_the_slot_replaces_the_window_and_adds_no_second_sample(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        provider = MockQRadarProvider(seed=1)
        await collector(db_session, provider).collect(inst, slot=SLOT)
        provider.health_metric_script = lambda _s: [metric_row("qradarce2", "SystemCPU", None, 9)]

        await collector(db_session, provider).collect(inst, slot=SLOT)

        assert [(r.metric, r.max_value) for r in await metric_rows(db_session)] == [
            ("SystemCPU", 9.0)
        ]
        assert await db_session.scalar(select(func.count()).select_from(QRadarHealthRun)) == 1
        assert (
            await db_session.scalar(select(func.count()).select_from(QRadarHealthRestSample)) == 1
        )

    async def test_a_null_value_is_never_stored_as_a_zero(self, db_session) -> None:
        inst = await make_instance(db_session)
        provider = MockQRadarProvider(seed=1)
        provider.health_metric_script = lambda _s: [
            metric_row("qradarce2", "CREQueueSize", None, None),
            metric_row("qradarce2", "SystemCPU", None, 4.0),
        ]
        await collector(db_session, provider).collect(inst, slot=SLOT)
        rows = await metric_rows(db_session)
        assert [(r.metric, r.max_value) for r in rows] == [("SystemCPU", 4.0)]
        assert (await only_run(db_session)).null_values == 1

    async def test_a_missing_property_stores_no_metric_and_still_stores_the_notifications(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)

        class NoValue(MockQRadarProvider):
            async def list_custom_properties(self, *, log_source_type_id=None):  # type: ignore[no-untyped-def]
                listed = await super().list_custom_properties()
                return [p for p in listed if p.name != "Value"]

        provider = NoValue(seed=1)
        report = await collector(db_session, provider).collect(inst, slot=SLOT)

        run_row = await only_run(db_session)
        assert run_row.metrics_state == "NOT_COLLECTED"
        assert run_row.metrics_reason_code == "qradar_health_detail.reason.property_missing"
        assert run_row.metrics_reason_params == {"names": "Value"}
        assert run_row.notifications_state == "OK"
        assert await metric_rows(db_session) == []
        assert (await db_session.scalars(select(QRadarHealthNotificationBucket))).all()
        assert [s.kind for s in provider.health_searches] == ["notifications"]
        assert report.as_dict()["status"] == "failed" and report.searches_sent == 1

    async def test_an_ariel_failure_is_failed_and_stores_no_bucket(self, db_session) -> None:
        inst = await make_instance(db_session)
        provider = MockQRadarProvider(seed=1)
        provider.health_fault = lambda _s: ProviderUnavailableError("down")

        report = await collector(db_session, provider).collect(inst, slot=SLOT)

        run_row = await only_run(db_session)
        assert (run_row.metrics_state, run_row.notifications_state) == ("FAILED", "FAILED")
        assert run_row.metrics_reason_code == "qradar_health_detail.reason.search_failed"
        assert await metric_rows(db_session) == []
        assert (await db_session.scalars(select(QRadarHealthNotificationBucket))).all() == []
        assert report.as_dict()["status"] == "failed" and report.buckets_complete == 0

    async def test_a_truncated_search_is_partial_and_keeps_what_it_read(self, db_session) -> None:
        inst = await make_instance(db_session)
        provider = MockQRadarProvider(seed=1)
        provider.health_force_truncated = lambda s: s.kind == "metrics"

        report = await collector(db_session, provider).collect(inst, slot=SLOT)

        run_row = await only_run(db_session)
        assert run_row.metrics_state == "PARTIAL" and run_row.notifications_state == "OK"
        assert await metric_rows(db_session)
        assert report.buckets_complete == 0 and report.buckets_lower_bound == 1

    async def test_the_notifications_keep_the_top_fifty_and_one_remainder(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        provider = MockQRadarProvider(seed=1)
        provider.health_notification_script = lambda _s: [
            {
                "source": "System Notification-2 :: qradarce2",
                "qid_name": f"Message {n:03d}",
                "severity": 3,
                "events": 100 - n,
                "last_seen": int(NOW.timestamp() * 1000),
            }
            for n in range(60)
        ]
        await collector(db_session, provider).collect(inst, slot=SLOT)
        rows = (await db_session.scalars(select(QRadarHealthNotificationBucket))).all()
        groups = [r for r in rows if r.kind == "GROUP"]
        remainder = [r for r in rows if r.kind == "REMAINDER"]
        assert len(groups) == 50 and len(remainder) == 1
        assert (remainder[0].host, remainder[0].qid_name, remainder[0].groups) == ("", "", 10)
        assert sum(r.events for r in rows) == sum(100 - n for n in range(60))

    async def test_a_unsupported_provider_writes_nothing(self, db_session) -> None:
        inst = await make_instance(db_session)

        class NoAql(MockQRadarProvider):
            def supports(self, capability):  # type: ignore[no-untyped-def]
                return False

        report = await collector(db_session, NoAql(seed=1)).collect(inst, slot=SLOT)
        assert report.unsupported
        assert await db_session.scalar(select(func.count()).select_from(QRadarHealthRun)) == 0

    async def test_the_sql_per_run_does_not_grow_with_the_hosts(self, db_session) -> None:
        counts: dict[int, int] = {}
        for hosts in (1, 40):
            inst = await make_instance(db_session)
            provider = MockQRadarProvider(seed=1)
            provider.health_metric_script = lambda _s, n=hosts: [
                metric_row(f"host{h}", metric, element, 0.5)
                for h in range(n)
                for metric, element in (
                    ("DiskUsage", "/"),
                    ("DiskUsage", "/store"),
                    ("SystemCPU", None),
                    ("CREQueueSize", None),
                )
            ]
            provider.health_notification_script = lambda _s, n=hosts: [
                {
                    "source": f"System Notification-2 :: host{h}",
                    "qid_name": "Warning Message",
                    "severity": 3,
                    "events": 2,
                    "last_seen": int(NOW.timestamp() * 1000),
                }
                for h in range(n)
            ]
            with recorded_statements(db_session) as statements:
                await collector(db_session, provider).collect(inst, slot=SLOT)
            counts[hosts] = len(statements)
        assert counts[1] == counts[40], counts


class TestRetention:
    async def test_old_windows_are_deleted_and_recent_ones_stay(self, db_session) -> None:
        inst = await make_instance(db_session)
        old = NOW - timedelta(days=40)
        for start in (old, WINDOW):
            db_session.add(
                QRadarHealthMetricBucket(
                    instance_id=inst.id, bucket_start=start,
                    bucket_end=start + timedelta(minutes=15),
                    host="h", metric="SystemCPU", element="", max_value=1.0, avg_value=1.0,
                    samples=3,
                )
            )
            db_session.add(
                QRadarHealthRestSample(instance_id=inst.id, slot_start=start, sampled_at=start)
            )
        await db_session.commit()

        report = await QRadarHealthRetention(
            db_session, settings=get_settings(), clock=lambda: NOW
        ).run()

        assert report.deleted["qradar_health_metric_bucket"] == 1
        assert report.deleted["qradar_health_rest_sample"] == 1
        assert report.incomplete == []
        assert [r.bucket_start for r in await metric_rows(db_session)] == [WINDOW]


# ------------------------------------------------------------------------------------- API
URL = "/api/v1/admin/qradar-health"


def seed_run(
    inst_id: uuid.UUID,
    *,
    end: datetime,
    metrics: str = "OK",
    notifications: str = "OK",
    hosts: tuple[str, ...] = ("qr1",),
    fraction: float = 0.5,
):  # type: ignore[no-untyped-def]
    """Rows of one stored window, as the collector would write them."""

    async def _seed(session):  # type: ignore[no-untyped-def]
        start = end - timedelta(seconds=CADENCE)
        session.add(
            QRadarHealthRun(
                instance_id=inst_id, window_start=start, window_end=end, cadence_seconds=CADENCE,
                collected_at=end, query_version=1,
                metrics_state=metrics,
                metrics_reason_code=(
                    None if metrics == "OK" else "qradar_health_detail.reason.search_failed"
                ),
                notifications_state=notifications,
                notifications_reason_code=(
                    None if notifications == "OK" else "qradar_health_detail.reason.search_failed"
                ),
                unparseable_hosts=0, dropped_mounts=2, dropped_metrics=0, null_values=0,
                invalid_values=0, dropped_events=0,
            )
        )
        if metrics in ("OK", "PARTIAL"):
            for host in hosts:
                for metric, element, value in (
                    ("DiskUsage", "/store", fraction),
                    ("DiskUsage", "/", 0.2),
                    ("DiskSpaceUsed", "/store", 50.0),
                    ("DiskSpaceTotal", "/store", 100.0),
                    ("SystemCPU", "", 30.0),
                    ("CREQueueSize", "", 120.0),
                    ("QueuedEventThrottleFilterDiskSize", "", 0.0),
                    ("EventParserThreadInfo", "", 18.0),
                    ("ArielWriterThreadInfo", "", 40.0),
                ):
                    session.add(
                        QRadarHealthMetricBucket(
                            instance_id=inst_id, bucket_start=start, bucket_end=end, host=host,
                            metric=metric, element=element, max_value=value, avg_value=value,
                            samples=30,
                        )
                    )
        if notifications in ("OK", "PARTIAL"):
            session.add(
                QRadarHealthNotificationBucket(
                    instance_id=inst_id, bucket_start=start, bucket_end=end, kind="GROUP",
                    host="qr1", qid_name="Warning Message", events=4, groups=1, max_severity=4,
                    last_seen=end,
                )
            )
            session.add(
                QRadarHealthNotificationBucket(
                    instance_id=inst_id, bucket_start=start, bucket_end=end, kind="REMAINDER",
                    host="", qid_name="", events=9, groups=3,
                )
            )
        session.add(
            QRadarHealthRestSample(
                instance_id=inst_id, slot_start=end, sampled_at=end, reachable=True,
                api_latency_ms=42, hosts_active=1, hosts_total=1,
            )
        )
        await session.flush()

    return _seed


def fresh_end() -> datetime:
    return datetime.now(UTC) - timedelta(minutes=3)


class TestLatestApi:
    def test_only_an_admin_may_read_it(self, db_schema) -> None:
        for path in ("/latest", "/history"):
            assert Client(VIEWER).get(URL + path).status_code == 403
            assert Client(ADMIN).get(URL + path).status_code == 200

    def test_a_console_with_no_window_is_not_collected_never_zeros(self, db_schema) -> None:
        run(make_instance)
        body = Client(ADMIN).get(URL + "/latest").json()
        [console] = body["consoles"]
        assert console["window"] is None and console["hosts"] == []
        assert console["metrics"]["state"] == "NOT_COLLECTED"
        assert console["metrics"]["reason_code"] == "no_window"
        assert console["notification_list"] is None

    def test_no_console_is_an_empty_list(self, db_schema) -> None:
        assert Client(ADMIN).get(URL + "/latest").json()["consoles"] == []

    def test_the_latest_window_has_the_host_values_and_states(self, db_schema) -> None:
        inst = run(make_instance)
        run(seed_run(inst.id, end=fresh_end(), fraction=0.9))
        body = Client(ADMIN).get(URL + "/latest").json()
        [console] = body["consoles"]
        assert console["metrics"] == {"state": "OK", "reason_code": None, "reason": None}
        [host] = console["hosts"]
        assert host["host"] == "qr1"
        fullest = host["disk"]["fullest"]
        assert fullest["mount"] == "/store" and fullest["level"] == "WARN"
        assert fullest["used_percent"] == pytest.approx(90.0)
        assert fullest["free_bytes"] == 50.0 and fullest["total_bytes"] == 100.0
        assert [m["mount"] for m in host["disk"]["mounts"]] == ["/", "/store"]
        assert host["pipeline"]["cre_queue"] == {
            "state": "OK", "value": 120.0, "average": 120.0, "samples": 30,
            "reason_code": None, "reason": None,
        }
        assert host["pipeline"]["throttle_spilling"] is False
        assert host["pipeline"]["busiest_stage"] == {"stage": "ArielWriter", "busy_percent": 40.0}
        cpu = host["system"]["cpu_percent"]
        assert cpu["state"] == "OK" and cpu["value"] == 30.0
        # Never reported: not collected, with no value and a reason.
        swap = host["system"]["swap_percent"]
        assert swap["state"] == "NOT_COLLECTED" and swap["value"] is None and swap["reason"]
        assert console["notification_list"]["entries"][0]["qid_name"] == "Warning Message"
        assert console["notification_list"]["remainder"] == {"events": 9, "groups": 3}
        assert console["rest_sample"]["api_latency_ms"] == 42
        assert console["dropped_mounts"] == 2

    def test_a_failed_newest_run_shows_the_failure_not_the_older_values(self, db_schema) -> None:
        inst = run(make_instance)
        run(seed_run(inst.id, end=fresh_end() - timedelta(minutes=30)))
        run(seed_run(inst.id, end=fresh_end(), metrics="FAILED", notifications="FAILED"))
        [console] = Client(ADMIN).get(URL + "/latest").json()["consoles"]
        assert console["metrics"]["state"] == "FAILED" and console["metrics"]["reason"]
        assert console["hosts"] == [] and console["notification_list"] is None
        assert console["last_metrics_window_end"] is not None

    def test_an_old_window_is_stale_and_keeps_its_values(self, db_schema) -> None:
        inst = run(make_instance)
        run(seed_run(inst.id, end=datetime.now(UTC) - timedelta(hours=3)))
        [console] = Client(ADMIN).get(URL + "/latest").json()["consoles"]
        assert console["metrics"]["state"] == "STALE"
        assert console["metrics"]["reason_code"] == "stale"
        [host] = console["hosts"]
        assert host["disk"]["state"] == "STALE"
        assert host["pipeline"]["cre_queue"]["state"] == "STALE"
        assert host["pipeline"]["cre_queue"]["value"] == 120.0

    def test_the_statements_do_not_grow_with_consoles_or_hosts(self, db_schema) -> None:
        counts = {}
        for consoles, hosts in ((1, 1), (4, 30)):
            for _ in range(consoles - (1 if consoles > 1 else 0)):
                inst = run(make_instance)
                run(seed_run(inst.id, end=fresh_end(),
                             hosts=tuple(f"host{n}" for n in range(hosts))))
            client = Client(ADMIN)
            assert len(client.get(URL + "/latest").json()["consoles"]) >= 1
            counts[consoles] = client.statements
        assert counts[1] == counts[4], counts


class TestHistoryApi:
    def test_an_unknown_range_is_a_coded_400(self, db_schema) -> None:
        for hours in ("12", "25", "0", "abc"):
            response = Client(ADMIN).get(URL + f"/history?hours={hours}")
            assert response.status_code in (400, 422)
        assert Client(ADMIN).get(URL + "/history?hours=12").status_code == 400

    def test_no_window_is_not_collected_per_series(self, db_schema) -> None:
        run(make_instance)
        body = Client(ADMIN).get(URL + "/history?hours=24").json()
        [console] = body["consoles"]
        assert console["metrics"]["state"] == "NOT_COLLECTED"
        assert console["reachability"]["state"] == "NOT_COLLECTED"
        assert console["hosts"] == [] and console["reachability"]["points"] == []
        assert body["hours"] == 24 and body["expected_interval_seconds"] == 900

    def test_series_carry_the_fullest_mount_queue_spill_and_reachability(self, db_schema) -> None:
        inst = run(make_instance)
        for back in (60, 45, 30, 15):
            run(seed_run(inst.id, end=datetime.now(UTC) - timedelta(minutes=back),
                         fraction=0.4 + back / 1000))
        body = Client(ADMIN).get(URL + "/history?hours=24").json()
        [console] = body["consoles"]
        assert console["metrics"]["state"] == "OK"
        [host] = console["hosts"]
        disk = host["disk_fullest_fraction"]
        assert disk["state"] == "OK" and len(disk["points"]) == 4
        assert {p["mount"] for p in disk["points"]} == {"/store"}
        assert [p["value"] for p in disk["points"]] == sorted(
            [p["value"] for p in disk["points"]], reverse=True
        )
        assert [p["value"] for p in host["cre_queue"]["points"]] == [120.0] * 4
        assert [p["value"] for p in host["throttle_spill_bytes"]["points"]] == [0.0] * 4
        reach = console["reachability"]
        assert reach["state"] == "OK" and len(reach["points"]) == 4
        first = reach["points"][0]
        assert first["reachable"] is True and first["api_latency_ms"] == 42

    def test_a_week_is_bounded_and_a_day_excludes_older_windows(self, db_schema) -> None:
        inst = run(make_instance)
        run(seed_run(inst.id, end=datetime.now(UTC) - timedelta(hours=30)))
        day = Client(ADMIN).get(URL + "/history?hours=24").json()["consoles"][0]
        week = Client(ADMIN).get(URL + "/history?hours=168").json()["consoles"][0]
        assert day["hosts"] == [] and day["metrics"]["state"] == "NOT_COLLECTED"
        assert len(week["hosts"]) == 1

    def test_the_statements_do_not_grow_with_hosts(self, db_schema) -> None:
        inst = run(make_instance)
        run(seed_run(inst.id, end=datetime.now(UTC) - timedelta(minutes=20)))
        one = Client(ADMIN)
        one.get(URL + "/history?hours=24")
        other = run(make_instance)
        run(seed_run(other.id, end=datetime.now(UTC) - timedelta(minutes=20),
                     hosts=tuple(f"host{n}" for n in range(25))))
        many = Client(ADMIN)
        many.get(URL + "/history?hours=24")
        assert one.statements == many.statements


def test_a_custom_property_dto_is_what_the_check_reads() -> None:
    # The collector compares names only; this pins that the DTO field is called `name`.
    assert "name" in CustomPropertyDTO.model_fields
