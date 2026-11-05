"""QRadar health in detail against a real database (HLT-2): collector, API, retention."""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.collectors import qradar_health_collector
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
from tests.integration.factories import make_instance, make_log_source
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


async def add_notification_source(  # type: ignore[no-untyped-def]
    session,
    instance,
    *,
    qradar_id: int = 9001,
    enabled: bool = True,
    type_name: str = "System Notification",
):  # type: ignore[no-untyped-def]
    """A log source of the type the notification search reads, shaped as the sync stores it."""
    return await make_log_source(
        session,
        instance,
        qradar_id=qradar_id,
        name=f"System Notification-{qradar_id} :: qradarce2",
        type_name=type_name,
        enabled=enabled,
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
        assert run_row.dropped_mounts == 2 and run_row.query_version == 2
        rows = await metric_rows(db_session)
        assert {(r.metric, r.element) for r in rows if r.metric == "DiskUsage"} == {
            ("DiskUsage", "/"),
            ("DiskUsage", "/store"),
            ("DiskUsage", "/tmp"),  # noqa: S108
        }
        assert {(r.metric, r.element) for r in rows if r.metric.endswith("ThreadInfo")} == {
            ("EventParserThreadInfo", "RunnableRatio"),
            ("ArielWriterThreadInfo", "RunnableRatio"),
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


@asynccontextmanager
async def inventory_table_missing(url: str):  # type: ignore[no-untyped-def]
    """Make the inventory read fail at the database, not in Python (FIX-31).

    `log_source` is renamed on its own connection for the duration of the block, so the
    collector's `SELECT ... FROM log_source` is answered by PostgreSQL with
    `relation "log_source" does not exist` -- a real statement error that aborts the
    transaction it runs in. Patching `notification_source_consoles` to raise would not show
    that: a Python exception leaves the transaction healthy.
    """
    engine = create_async_engine(url, future=True)
    try:
        async with engine.begin() as conn:
            # ACCESS EXCLUSIVE: fail fast rather than wait on a lock the test's own session holds.
            await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
            await conn.execute(text("ALTER TABLE log_source RENAME TO log_source_away"))
        try:
            yield
        finally:
            async with engine.begin() as conn:
                await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
                await conn.execute(text("ALTER TABLE log_source_away RENAME TO log_source"))
    finally:
        await engine.dispose()


@pytest.mark.usefixtures("pin_cadence")
class TestNotificationSource:
    """HLT-8: an empty answer is "none" only when the console can report a notification."""

    async def test_an_empty_answer_with_an_enabled_source_is_ok_and_empty(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        await add_notification_source(db_session, inst)
        provider = MockQRadarProvider(seed=1)
        provider.health_notification_script = lambda _s: []

        report = await collector(db_session, provider).collect(inst, slot=SLOT)

        run_row = await only_run(db_session)
        assert run_row.notifications_state == "OK"
        assert run_row.notifications_reason_code is None
        assert (await db_session.scalars(select(QRadarHealthNotificationBucket))).all() == []
        assert run_row.metrics_state == "OK"
        assert report.notifications_state == "OK" and report.buckets_complete == 1
        assert report.as_dict()["status"] == "ok"

    async def test_an_empty_answer_without_a_source_is_not_collected(self, db_session) -> None:
        inst = await make_instance(db_session)
        provider = MockQRadarProvider(seed=1)
        provider.health_notification_script = lambda _s: []

        report = await collector(db_session, provider).collect(inst, slot=SLOT)

        run_row = await only_run(db_session)
        assert run_row.notifications_state == "NOT_COLLECTED"
        assert run_row.notifications_reason_code == (
            "qradar_health_detail.reason.notification_source_missing"
        )
        assert run_row.metrics_state == "OK"
        assert (await db_session.scalars(select(QRadarHealthNotificationBucket))).all() == []
        # The search still ran, and the run says so rather than reading as healthy (rule 8).
        assert report.searches_sent == 2 and report.as_dict()["status"] == "failed"
        assert report.notifications_state == "NOT_COLLECTED"

    async def test_an_empty_answer_with_only_a_disabled_source_is_not_collected(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        await add_notification_source(db_session, inst, enabled=False)
        provider = MockQRadarProvider(seed=1)
        provider.health_notification_script = lambda _s: []

        await collector(db_session, provider).collect(inst, slot=SLOT)

        run_row = await only_run(db_session)
        assert run_row.notifications_state == "NOT_COLLECTED"
        assert run_row.notifications_reason_code == (
            "qradar_health_detail.reason.notification_source_missing"
        )

    async def test_a_source_of_another_type_is_not_evidence(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_notification_source(db_session, inst, type_name="Health Metrics")
        provider = MockQRadarProvider(seed=1)
        provider.health_notification_script = lambda _s: []

        await collector(db_session, provider).collect(inst, slot=SLOT)

        run_row = await only_run(db_session)
        assert run_row.notifications_state == "NOT_COLLECTED"
        assert run_row.notifications_reason_code == (
            "qradar_health_detail.reason.notification_source_missing"
        )

    async def test_an_unreadable_inventory_is_not_collected_never_ok(
        self, db_session, monkeypatch
    ) -> None:
        inst = await make_instance(db_session)
        provider = MockQRadarProvider(seed=1)
        provider.health_notification_script = lambda _s: []

        async def boom(_session, _ids):  # type: ignore[no-untyped-def]
            raise ProviderUnavailableError("inventory down")

        monkeypatch.setattr(qradar_health_collector, "notification_source_consoles", boom)

        report = await collector(db_session, provider).collect(inst, slot=SLOT)

        run_row = await only_run(db_session)
        assert run_row.notifications_state == "NOT_COLLECTED"
        assert run_row.notifications_reason_code == (
            "qradar_health_detail.reason.notification_source_unreadable"
        )
        assert run_row.metrics_state == "OK"
        assert report.searches_sent == 2 and report.as_dict()["status"] == "failed"

    async def test_a_database_error_in_the_inventory_read_still_writes_the_run(
        self, db_session, db_schema
    ) -> None:
        """FIX-31: the caught read fails at PostgreSQL, so only its savepoint is lost.

        Without the savepoint the run's transaction aborts and `_write` raises
        `InFailedSQLTransactionError`: no run row, no window, nothing recorded.
        """
        inst = await make_instance(db_session)
        provider = MockQRadarProvider(seed=1)
        provider.health_notification_script = lambda _s: []

        async with inventory_table_missing(db_schema):
            report = await collector(db_session, provider).collect(inst, slot=SLOT)

        run_row = await only_run(db_session)
        assert (run_row.window_start, run_row.window_end) == (WINDOW, SLOT)
        assert run_row.notifications_state == "NOT_COLLECTED"
        assert run_row.notifications_reason_code == (
            "qradar_health_detail.reason.notification_source_unreadable"
        )
        # The other part is untouched by the failure: measured and stored as usual.
        assert run_row.metrics_state == "OK"
        assert run_row.metrics_reason_code is None
        assert await metric_rows(db_session), "the metric part is stored"
        assert (await db_session.scalars(select(QRadarHealthNotificationBucket))).all() == []
        assert report.notifications_state == "NOT_COLLECTED"
        assert report.searches_sent == 2 and report.rest_sample_written is True

    async def test_rows_present_never_read_the_inventory(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_notification_source(db_session, inst, enabled=False)
        provider = MockQRadarProvider(seed=1)

        with recorded_statements(db_session) as statements:
            report = await collector(db_session, provider).collect(inst, slot=SLOT)

        assert report.notifications_state == "OK"
        assert [s for s in statements if "FROM log_source" in s] == []

    async def test_the_inventory_read_is_one_statement_whatever_the_log_sources(
        self, db_session
    ) -> None:
        counts: dict[int, int] = {}
        for sources in (1, 40):
            inst = await make_instance(db_session)
            for n in range(sources):
                await add_notification_source(db_session, inst, qradar_id=9000 + n)
            provider = MockQRadarProvider(seed=1)
            provider.health_notification_script = lambda _s: []
            with recorded_statements(db_session) as statements:
                await collector(db_session, provider).collect(inst, slot=SLOT)
            inventory = [s for s in statements if "FROM log_source" in s]
            assert len(inventory) == 1, inventory
            # FIX-31: the read runs in its own savepoint, so a failure contained it and the run
            # still writes its row. The pair is the whole added cost, whatever the row count.
            at = statements.index(inventory[0])
            assert statements[at - 1].startswith("SAVEPOINT"), statements[at - 1]
            assert statements[at + 1].startswith("RELEASE SAVEPOINT"), statements[at + 1]
            counts[sources] = len(statements)
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
    notifications_reason: str | None = None,
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
                    None
                    if notifications == "OK"
                    else notifications_reason
                    or "qradar_health_detail.reason.search_failed"
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
                    ("EventParserThreadInfo", "RunnableRatio", 18.0),
                    ("ArielWriterThreadInfo", "RunnableRatio", 40.0),
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

    def test_a_window_without_a_notification_source_shows_the_new_reason(
        self, db_schema
    ) -> None:
        """HLT-8: an empty answer on a console with no such source is not "none"."""
        inst = run(make_instance)
        run(
            seed_run(
                inst.id,
                end=fresh_end(),
                notifications="NOT_COLLECTED",
                notifications_reason=(
                    "qradar_health_detail.reason.notification_source_missing"
                ),
            )
        )
        [console] = Client(ADMIN).get(URL + "/latest").json()["consoles"]
        assert console["notifications"] == {
            "state": "NOT_COLLECTED",
            "reason_code": "qradar_health_detail.reason.notification_source_missing",
            "reason": (
                "The console has no enabled System Notification log source, so an empty "
                "notification answer does not show that nothing happened."
            ),
        }
        assert console["notification_list"] is None and console["metrics"]["state"] == "OK"
        # The roll-up counts measured windows only, so this window adds nothing to it.
        [rolled] = Client(ADMIN).get(URL + "/latest?notifications_hours=24").json()["consoles"]
        assert rolled["notification_list"] is None

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


class TestNotificationRollupApi:
    """`notifications_hours` rolls the measured windows of a range into one list (HLT-3)."""

    def _seed_windows(self, inst_id, ends):  # type: ignore[no-untyped-def]
        for end in ends:
            run(seed_run(inst_id, end=end))

    def test_the_default_is_the_newest_window_alone(self, db_schema) -> None:
        inst = run(make_instance)
        self._seed_windows(inst.id, [fresh_end() - timedelta(minutes=15), fresh_end()])
        [console] = Client(ADMIN).get(URL + "/latest").json()["consoles"]
        listing = console["notification_list"]
        assert listing["entries"][0]["events"] == 4
        assert listing["hours"] is None and listing["windows"] is None

    def test_windows_in_the_range_are_summed_per_host_and_qid(self, db_schema) -> None:
        inst = run(make_instance)
        end = fresh_end()
        self._seed_windows(inst.id, [end - timedelta(hours=h) for h in (0, 1, 2)])
        # Older than the range: not counted.
        self._seed_windows(inst.id, [end - timedelta(hours=30)])
        [console] = Client(ADMIN).get(URL + "/latest?notifications_hours=24").json()["consoles"]
        listing = console["notification_list"]
        [entry] = listing["entries"]
        assert (entry["host"], entry["qid_name"], entry["events"]) == ("qr1", "Warning Message", 12)
        assert entry["max_severity"] == 4
        assert listing["remainder"] == {"events": 27, "groups": 9}
        assert (listing["hours"], listing["windows"]) == (24, 3)
        assert listing["covered_until"] is not None and listing["covered_from"] is not None

    def test_a_range_with_no_measured_window_has_no_list(self, db_schema) -> None:
        inst = run(make_instance)
        run(seed_run(inst.id, end=fresh_end(), notifications="FAILED"))
        [console] = Client(ADMIN).get(URL + "/latest?notifications_hours=24").json()["consoles"]
        assert console["notification_list"] is None
        assert console["notifications"]["state"] == "FAILED"

    def test_a_failed_newest_run_still_shows_the_measured_windows_before_it(
        self, db_schema
    ) -> None:
        inst = run(make_instance)
        run(seed_run(inst.id, end=fresh_end() - timedelta(minutes=30)))
        run(seed_run(inst.id, end=fresh_end(), notifications="FAILED"))
        [console] = Client(ADMIN).get(URL + "/latest?notifications_hours=24").json()["consoles"]
        assert console["notifications"]["state"] == "FAILED"
        assert console["notification_list"]["windows"] == 1
        assert console["notification_list"]["entries"][0]["events"] == 4

    def test_a_console_with_no_window_has_no_list(self, db_schema) -> None:
        run(make_instance)
        [console] = Client(ADMIN).get(URL + "/latest?notifications_hours=24").json()["consoles"]
        assert console["notification_list"] is None

    @pytest.mark.parametrize("hours", ["0", "169", "-1", "x"])
    def test_a_range_outside_one_to_168_hours_is_refused(self, db_schema, hours: str) -> None:
        assert Client(ADMIN).get(URL + f"/latest?notifications_hours={hours}").status_code == 422

    def test_the_edges_one_and_168_are_accepted(self, db_schema) -> None:
        for hours in ("1", "168"):
            response = Client(ADMIN).get(URL + f"/latest?notifications_hours={hours}")
            assert response.status_code == 200

    def test_the_statements_do_not_grow_with_consoles_windows_or_groups(self, db_schema) -> None:
        counts = {}
        for consoles, windows in ((1, 1), (4, 6)):
            for _ in range(consoles - (1 if consoles > 1 else 0)):
                inst = run(make_instance)
                self._seed_windows(
                    inst.id, [fresh_end() - timedelta(hours=n) for n in range(windows)]
                )
            client = Client(ADMIN)
            assert len(client.get(URL + "/latest?notifications_hours=48").json()["consoles"]) >= 1
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


# ------------------------------------------------------------- HLT-5: carried total, collection off
SETTINGS = get_settings()


def put_window(inst_id, end, rows, *, state="OK"):  # type: ignore[no-untyped-def]
    """One stored window with exactly the metric rows given: (host, metric, element, value)."""

    async def _seed(session):  # type: ignore[no-untyped-def]
        start = end - timedelta(seconds=CADENCE)
        session.add(
            QRadarHealthRun(
                instance_id=inst_id, window_start=start, window_end=end, cadence_seconds=CADENCE,
                collected_at=end, query_version=2, metrics_state=state,
                notifications_state="OK", unparseable_hosts=0, dropped_mounts=0,
                dropped_metrics=0, null_values=0, invalid_values=0, dropped_events=0,
            )
        )
        for host, metric, element, value in rows:
            session.add(
                QRadarHealthMetricBucket(
                    instance_id=inst_id, bucket_start=start, bucket_end=end, host=host,
                    metric=metric, element=element, max_value=value, avg_value=value, samples=3,
                )
            )
        await session.flush()

    return _seed


def disk_rows(host, mount, *, used, total=None):  # type: ignore[no-untyped-def]
    rows = [(host, "DiskUsage", mount, 0.5), (host, "DiskSpaceUsed", mount, used)]
    if total is not None:
        rows.append((host, "DiskSpaceTotal", mount, total))
    return rows


async def latest_mounts(session, now, **kw):  # type: ignore[no-untyped-def]
    from app.services.qradar_health_detail import read_latest

    out = await read_latest(session, SETTINGS, now=now, **kw)
    return {
        (h.host, m.mount): m for c in out.consoles for h in c.hosts for m in h.disk.mounts
    }


class TestCarriedDiskTotal:
    async def test_a_window_with_its_own_total_does_not_carry(self, db_session) -> None:
        inst = await make_instance(db_session)
        end = WINDOW + timedelta(minutes=30)
        await put_window(inst.id, end - timedelta(minutes=15),
                         disk_rows("qr1", "/store", used=10.0, total=999.0))(db_session)
        await put_window(inst.id, end, disk_rows("qr1", "/store", used=40.0, total=100.0))(
            db_session
        )
        mount = (await latest_mounts(db_session, end))[("qr1", "/store")]
        assert mount.total_bytes == 100.0 and mount.total_from is None
        assert mount.free_bytes == 60.0

    async def test_an_earlier_total_45_minutes_old_is_carried(self, db_session) -> None:
        inst = await make_instance(db_session)
        end = WINDOW + timedelta(hours=1)
        old_end = end - timedelta(minutes=45)
        await put_window(inst.id, old_end, disk_rows("qr1", "/store", used=10.0, total=100.0))(
            db_session
        )
        await put_window(inst.id, end, disk_rows("qr1", "/store", used=40.0))(db_session)
        mount = (await latest_mounts(db_session, end))[("qr1", "/store")]
        assert mount.total_bytes == 100.0 and mount.total_from == old_end
        assert mount.free_bytes == 60.0

    async def test_the_newest_earlier_total_wins(self, db_session) -> None:
        inst = await make_instance(db_session)
        end = WINDOW + timedelta(hours=1)
        for age, total in ((90, 500.0), (30, 200.0)):
            await put_window(inst.id, end - timedelta(minutes=age),
                             disk_rows("qr1", "/store", used=1.0, total=total))(db_session)
        await put_window(inst.id, end, disk_rows("qr1", "/store", used=40.0))(db_session)
        mount = (await latest_mounts(db_session, end))[("qr1", "/store")]
        assert mount.total_bytes == 200.0 and mount.free_bytes == 160.0

    async def test_a_total_older_than_two_hours_is_not_used(self, db_session) -> None:
        inst = await make_instance(db_session)
        end = WINDOW + timedelta(hours=3)
        await put_window(inst.id, end - timedelta(hours=2, minutes=1),
                         disk_rows("qr1", "/store", used=10.0, total=100.0))(db_session)
        await put_window(inst.id, end, disk_rows("qr1", "/store", used=40.0))(db_session)
        mount = (await latest_mounts(db_session, end))[("qr1", "/store")]
        assert mount.total_bytes is None and mount.total_from is None
        assert mount.free_bytes is None

    async def test_a_total_exactly_two_hours_old_is_used(self, db_session) -> None:
        inst = await make_instance(db_session)
        end = WINDOW + timedelta(hours=3)
        await put_window(inst.id, end - timedelta(hours=2),
                         disk_rows("qr1", "/store", used=10.0, total=100.0))(db_session)
        await put_window(inst.id, end, disk_rows("qr1", "/store", used=40.0))(db_session)
        mount = (await latest_mounts(db_session, end))[("qr1", "/store")]
        assert mount.free_bytes == 60.0

    async def test_no_earlier_total_stays_null(self, db_session) -> None:
        inst = await make_instance(db_session)
        end = WINDOW + timedelta(hours=1)
        await put_window(inst.id, end, disk_rows("qr1", "/store", used=40.0))(db_session)
        mount = (await latest_mounts(db_session, end))[("qr1", "/store")]
        assert mount.total_bytes is None and mount.free_bytes is None

    async def test_a_total_smaller_than_the_used_bytes_gives_no_free(self, db_session) -> None:
        inst = await make_instance(db_session)
        end = WINDOW + timedelta(hours=1)
        await put_window(inst.id, end - timedelta(minutes=30),
                         disk_rows("qr1", "/store", used=1.0, total=30.0))(db_session)
        await put_window(inst.id, end, disk_rows("qr1", "/store", used=40.0))(db_session)
        mount = (await latest_mounts(db_session, end))[("qr1", "/store")]
        assert mount.free_bytes is None

    async def test_a_later_window_is_never_carried_backwards(self, db_session) -> None:
        """Only windows before the latest count: the latest is what is shown."""
        inst = await make_instance(db_session)
        end = WINDOW + timedelta(hours=1)
        await put_window(inst.id, end, disk_rows("qr1", "/store", used=40.0))(db_session)
        mount = (await latest_mounts(db_session, end))[("qr1", "/store")]
        assert mount.total_from is None

    async def test_mounts_and_hosts_each_carry_their_own_total(self, db_session) -> None:
        inst = await make_instance(db_session)
        end = WINDOW + timedelta(hours=1)
        earlier = (
            disk_rows("qr1", "/store", used=1.0, total=100.0)
            + disk_rows("qr1", "/", used=1.0, total=50.0)
            + disk_rows("qr2", "/store", used=1.0, total=400.0)
        )
        await put_window(inst.id, end - timedelta(minutes=30), earlier)(db_session)
        latest = (
            disk_rows("qr1", "/store", used=40.0)
            + disk_rows("qr1", "/", used=10.0)
            + disk_rows("qr2", "/store", used=100.0)
        )
        await put_window(inst.id, end, latest)(db_session)
        mounts = await latest_mounts(db_session, end)
        assert mounts[("qr1", "/store")].free_bytes == 60.0
        assert mounts[("qr1", "/")].free_bytes == 40.0
        assert mounts[("qr2", "/store")].free_bytes == 300.0

    async def test_statements_do_not_grow_with_hosts_and_mounts(self, db_session) -> None:
        inst = await make_instance(db_session)
        end = WINDOW + timedelta(hours=1)
        counts = []
        for hosts, mounts in ((1, 1), (6, 4)):
            rows_old, rows_new = [], []
            for h in range(hosts):
                for m in range(mounts):
                    rows_old += disk_rows(f"h{h}", f"/m{m}", used=1.0, total=100.0)
                    rows_new += disk_rows(f"h{h}", f"/m{m}", used=40.0)
            await db_session.execute(QRadarHealthRun.__table__.delete())
            await db_session.execute(QRadarHealthMetricBucket.__table__.delete())
            await put_window(inst.id, end - timedelta(minutes=30), rows_old)(db_session)
            await put_window(inst.id, end, rows_new)(db_session)
            with recorded_statements(db_session) as statements:
                found = await latest_mounts(db_session, end)
            assert all(m.free_bytes == 60.0 for m in found.values())
            counts.append(len(statements))
        assert counts[0] == counts[1], counts

    async def test_collection_enabled_follows_the_setting_and_keeps_the_figures(
        self, db_session
    ) -> None:
        from app.core.config import Settings
        from app.services.qradar_health_detail import read_latest

        inst = await make_instance(db_session)
        end = WINDOW + timedelta(hours=1)
        await put_window(inst.id, end, disk_rows("qr1", "/store", used=40.0, total=100.0))(
            db_session
        )
        for flag in (True, False):
            settings = Settings(
                encryption_key=SETTINGS.encryption_key, qradar_health_collection_enabled=flag
            )
            out = await read_latest(db_session, settings, now=end)
            assert out.collection_enabled is flag
            [console] = out.consoles
            assert console.window is not None and console.window.end == end
            assert console.hosts[0].disk.mounts[0].free_bytes == 60.0


class TestCollectionEnabledApi:
    def test_the_latest_response_says_collection_is_on(self, db_schema) -> None:
        body = Client(ADMIN).get(URL + "/latest").json()
        assert body["collection_enabled"] is True

    def test_a_carried_total_is_in_the_response_with_its_window(self, db_schema) -> None:
        inst = run(make_instance)
        end = fresh_end()
        old_end = end - timedelta(minutes=45)
        run(put_window(inst.id, old_end, disk_rows("qr1", "/store", used=10.0, total=100.0)))
        run(put_window(inst.id, end, disk_rows("qr1", "/store", used=40.0)))
        [console] = Client(ADMIN).get(URL + "/latest").json()["consoles"]
        mount = console["hosts"][0]["disk"]["fullest"]
        assert mount["free_bytes"] == 60.0 and mount["total_bytes"] == 100.0
        assert datetime.fromisoformat(mount["total_from"]) == old_end
