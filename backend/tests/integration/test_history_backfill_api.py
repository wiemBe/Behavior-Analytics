"""The history-backfill admin API: the page, the estimate, the requests, the permissions.

HB-8, HISTORY-BACKFILL §B.10.3. The API writes only `history_backfill_decision`
(HB-2b): every scope row in this file is inserted by the test itself, the way a
family adapter would. The tests drive the routes over HTTP, so they see what an
administrator's browser sees.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.database import get_session
from app.history.planner import Gap, PlannerSettings, new_progress
from app.history.progress_codec import encode_progress
from app.main import create_app
from app.models.catchall import CatchallBucket
from app.models.enums import (
    BucketCompleteness,
    IpsDimension,
    IpsPreset,
    IpsProfileValidationStatus,
    WafMeasurement,
    WafProfileValidationStatus,
)
from app.models.history_backfill import HistoryBackfillDecision, HistoryBackfillScope
from app.models.identity import AuditLog
from app.models.ips import IpsEntity, IpsSourceBucket, IpsSourceProfile
from app.models.rule import AnalyticsRule
from app.models.search_cadence import SearchCadenceSetting
from app.models.waf import WafEndpoint, WafSourceBucket, WafSourceProfile
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.history_backfill import HistoryBackfillService
from tests.integration.factories import make_instance, make_log_source

pytestmark = pytest.mark.integration

BASE = "/api/v1/admin/history-backfill"

ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
NOBODY = Principal(subject="nobody", permissions=frozenset({"read:offenses"}))

STAMP = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
#: A bucket-aligned instant, so a stored progress decodes.
START = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- the client


class Client:
    """A TestClient for one principal that records the SQL its requests run."""

    def __init__(
        self,
        url: str,
        principal: Principal | None,
        *,
        settings_update: dict[str, Any] | None = None,
    ) -> None:
        # NullPool: TestClient runs each request on a fresh event loop, and a
        # pooled asyncpg connection is bound to the loop that opened it.
        self.engine = create_async_engine(url, future=True, poolclass=NullPool)
        maker = async_sessionmaker(self.engine, expire_on_commit=False)
        self.statements: list[str] = []

        @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
        def _record(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
            self.statements.append(statement)

        async def _session():  # type: ignore[no-untyped-def]
            async with maker() as s:
                try:
                    yield s
                    await s.commit()
                except Exception:
                    await s.rollback()
                    raise

        app = create_app()
        app.dependency_overrides[get_session] = _session
        if settings_update is not None:
            app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
                update=settings_update
            )
        if principal is not None:
            app.dependency_overrides[get_principal] = lambda: principal
        self.http = TestClient(app)

    def request(self, method: str, url: str, **kwargs):  # type: ignore[no-untyped-def]
        self.statements = []
        return self.http.request(method, url, **kwargs)

    def get(self, url: str, **kw):  # type: ignore[no-untyped-def]
        return self.request("GET", url, **kw)

    def put(self, url: str, **kw):  # type: ignore[no-untyped-def]
        return self.request("PUT", url, **kw)

    def post(self, url: str, **kw):  # type: ignore[no-untyped-def]
        return self.request("POST", url, **kw)

    def writes_to(self, table: str) -> list[str]:
        """The statements that write `table`."""
        return [
            s
            for s in self.statements
            if table in s and s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]


# --------------------------------------------------------------------------- seed helpers


def decision(instance_id: uuid.UUID, answer: str = "YES", **kw) -> HistoryBackfillDecision:  # type: ignore[no-untyped-def]
    values: dict[str, Any] = {
        "instance_id": instance_id,
        "answer": answer,
        "days": 28 if answer == "YES" else None,
        "decided_at": STAMP,
        "decided_by": "installer",
        "source": "INSTALLER",
    }
    values.update(kw)
    return HistoryBackfillDecision(**values)


def scope_row(  # type: ignore[no-untyped-def]
    instance_id: uuid.UUID, family: str = "PHASE_A", key: str = "", **kw
) -> HistoryBackfillScope:
    values: dict[str, Any] = {
        "instance_id": instance_id,
        "family": family,
        "scope_key": key,
        "state": "READING",
        "days_requested": 28,
        "days_target": 28,
        "target_start": None,
        "floor": None,
        "through": None,
        "anchor": None,
        "started_at": STAMP,
        "finished_at": None,
        "reason": None,
        "progress": {},
    }
    values.update(kw)
    return HistoryBackfillScope(**values)


def progress_body(  # type: ignore[no-untyped-def]
    *,
    through: datetime | None = None,
    floor: datetime | None = None,
    searches: int = 0,
    buckets_read: int = 0,
    gaps: tuple[Gap, ...] = (),
) -> dict:
    """A stored progress the codec can read back."""
    settings = PlannerSettings(bucket_seconds=300)
    body = new_progress(START, START, settings)
    body = replace(
        body,
        floor=floor,
        through=through,
        searches=searches,
        buckets_read=buckets_read,
        gaps=gaps,
    )
    return encode_progress(body, {})


def waf_profile(instance_id: uuid.UUID, source_id: uuid.UUID, **kw) -> WafSourceProfile:  # type: ignore[no-untyped-def]
    values: dict[str, Any] = {
        "instance_id": instance_id,
        "log_source_id": source_id,
        "enabled": True,
        "profile_version": 2,
        "url_property": "URL",
        "method_property": "Method",
        "validation_status": WafProfileValidationStatus.VALID,
        "validated_profile_version": 2,
        "validated_at": STAMP,
        "validation_report": {},
    }
    values.update(kw)
    return WafSourceProfile(**values)


def waf_bucket(source_id: uuid.UUID, bucket_start: datetime, **kw) -> WafSourceBucket:  # type: ignore[no-untyped-def]
    values: dict[str, Any] = {
        "log_source_id": source_id,
        "bucket_start": bucket_start,
        "bucket_seconds": 300,
        "origin": "LIVE",
        "profile_version": 2,
        "metric_semantics": "sem",
        "completeness": BucketCompleteness.COMPLETE,
        "endpoint_coverage_ratio": 0.97,
        "total_events": 500,
        "parsed_events": 485,
        "group_count": 3,
        "groups_truncated": False,
        "unusable_groups": 0,
        "observed_endpoints": 3,
        "tracked_endpoints_observed": 3,
        "admitted_endpoints": 0,
        "below_admission_threshold": 0,
        "rejected_over_quota": 0,
        "untracked_endpoints": 0,
        "untracked_requests": 0,
        "admission_truncated": False,
        "zero_filled_endpoints": 0,
        "request_count_measurement": WafMeasurement.EXACT,
        "sparse_endpoint_rows": True,
        "in_maintenance": False,
        "collection_source": "TEST",
        "query_provenance": {"queries": ["SELECT sourceip FROM events"]},
    }
    values.update(kw)
    return WafSourceBucket(**values)


def waf_endpoint(instance_id: uuid.UUID, source_id: uuid.UUID, path: str, **kw) -> WafEndpoint:  # type: ignore[no-untyped-def]
    values: dict[str, Any] = {
        "instance_id": instance_id,
        "log_source_id": source_id,
        "virtual_host": None,
        "normalized_endpoint": path,
        "http_method": "GET",
        "normalizer_version": 1,
        "first_seen_at": STAMP - timedelta(days=10),
        "last_seen_at": STAMP,
        "tracked_since": STAMP - timedelta(days=10),
        "is_active": True,
    }
    values.update(kw)
    return WafEndpoint(**values)


def ips_profile(instance_id: uuid.UUID, source_id: uuid.UUID, **kw) -> IpsSourceProfile:  # type: ignore[no-untyped-def]
    values: dict[str, Any] = {
        "instance_id": instance_id,
        "log_source_id": source_id,
        "enabled": True,
        "profile_version": 2,
        "product_label": "lab-ips",
        "preset": IpsPreset.CISCO_FTD,
        "mapping": {},
        "action_value_map": {},
        "validation_status": IpsProfileValidationStatus.VALID,
        "validated_profile_version": 2,
        "validated_at": STAMP,
        "validation_report": {},
    }
    values.update(kw)
    return IpsSourceProfile(**values)


def ips_entity(  # type: ignore[no-untyped-def]
    instance_id: uuid.UUID, source_id: uuid.UUID, dimension: IpsDimension, value_key: str, **kw
) -> IpsEntity:
    values: dict[str, Any] = {
        "instance_id": instance_id,
        "log_source_id": source_id,
        "dimension": dimension,
        "value_key": value_key,
        "first_seen_at": STAMP - timedelta(days=10),
        "last_seen_at": STAMP,
        "active": True,
        "normalizer_version": 1,
    }
    values.update(kw)
    return IpsEntity(**values)


def ips_bucket(source_id: uuid.UUID, bucket_start: datetime, **kw) -> IpsSourceBucket:  # type: ignore[no-untyped-def]
    values: dict[str, Any] = {
        "log_source_id": source_id,
        "bucket_start": bucket_start,
        "bucket_seconds": 300,
        "origin": "LIVE",
        "total_events": 5000,
        "total_records": 5100,
        "destination_port_measurement": "EXACT",
        "destination_ip_measurement": "EXACT",
        "signature_measurement": "EXACT",
        "source_ip_measurement": "EXACT",
        "port_fanout_measurement": "EXACT",
        "action_measurement": "NOT_COLLECTED",
        "source_events": 4800,
        "distinct_sources": 40,
        "top1_events": 700,
        "top5_events": 1200,
        "max_distinct_ports": 12,
        "contributors": {},
        "admitted": {},
        "below_admission_threshold": {},
        "rejected_over_quota": {},
        "untracked_entities": {},
        "untracked_events": {},
        "admission_truncated": {},
        "profile_version": 2,
        "normalizer_version": 1,
        "in_maintenance": False,
        "collected_at": bucket_start,
        "query_provenance": {"queries": ["SELECT sourceip FROM events"]},
    }
    values.update(kw)
    return IpsSourceBucket(**values)


def catchall_bucket(source_id: uuid.UUID, bucket_start: datetime, **kw) -> CatchallBucket:  # type: ignore[no-untyped-def]
    values: dict[str, Any] = {
        "catchall_log_source_id": source_id,
        "bucket_start": bucket_start,
        "bucket_seconds": 300,
        "origin": "LIVE",
        "events": 10,
        "records": 2,
        "distinct_senders": 1,
        "distinct_senders_lower_bound": False,
        "measurement": "EXACT",
        "top_senders": [],
        "admitted": 1,
        "below_admission_threshold": 0,
        "rejected_over_quota": 0,
        "untracked_senders": 0,
        "untracked_events": 0,
        "query_provenance": {"split_depth": 0},
        "collected_at": bucket_start,
    }
    values.update(kw)
    return CatchallBucket(**values)


def rule(instance_id: uuid.UUID, qradar_id: int, **kw) -> AnalyticsRule:  # type: ignore[no-untyped-def]
    values: dict[str, Any] = {
        "instance_id": instance_id,
        "qradar_id": qradar_id,
        "name": f"rule-{qradar_id}",
        "enabled": True,
    }
    values.update(kw)
    return AnalyticsRule(**values)


def family_of(console: dict, name: str) -> dict:
    """The family entry of a console, by name."""
    return next(item for item in console["families"] if item["family"] == name)


async def audit_rows(session, action: str) -> list[AuditLog]:  # type: ignore[no-untyped-def]
    """The audit rows of an action, read over the test's own session."""
    rows = list(
        (
            await session.scalars(
                select(AuditLog)
                .where(AuditLog.action == action)
                .order_by(AuditLog.created_at)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    return rows


# --------------------------------------------------------------------------- permissions


def _five_routes(instance_id: uuid.UUID) -> list[tuple[str, str, dict]]:
    return [
        ("GET", BASE, {}),
        ("GET", f"{BASE}/estimate", {}),
        ("PUT", f"{BASE}/{instance_id}", {"json": {"answer": "YES"}}),
        ("POST", f"{BASE}/{instance_id}/start", {}),
        ("POST", f"{BASE}/{instance_id}/stop", {}),
    ]


async def test_every_route_needs_a_principal(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    await db_session.commit()
    client = Client(db_schema, None, settings_update={"auth_provider": "oidc"})
    for method, url, kwargs in _five_routes(instance.id):
        response = client.request(method, url, **kwargs)
        assert response.status_code == 401, f"{method} {url} -> {response.status_code}"


async def test_a_principal_with_neither_permission_is_refused_everywhere(  # type: ignore[no-untyped-def]
    db_session, db_schema
) -> None:
    instance = await make_instance(db_session)
    await db_session.commit()
    client = Client(db_schema, NOBODY)
    for method, url, kwargs in _five_routes(instance.id):
        response = client.request(method, url, **kwargs)
        assert response.status_code == 403, f"{method} {url} -> {response.status_code}"


async def test_read_only_reads_and_cannot_write(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    await db_session.commit()
    client = Client(db_schema, READER)
    assert client.get(BASE).status_code == 200
    assert client.get(f"{BASE}/estimate").status_code == 200
    for method, url, kwargs in _five_routes(instance.id)[2:]:
        response = client.request(method, url, **kwargs)
        assert response.status_code == 403, f"{method} {url} -> {response.status_code}"


async def test_an_administrator_does_all_five(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    await db_session.commit()
    client = Client(db_schema, ADMIN)
    assert client.get(BASE).status_code == 200
    assert client.get(f"{BASE}/estimate").status_code == 200
    assert client.put(f"{BASE}/{instance.id}", json={"answer": "YES"}).status_code == 201
    assert client.post(f"{BASE}/{instance.id}/start").status_code == 200
    assert client.post(f"{BASE}/{instance.id}/stop").status_code == 200


async def test_an_unknown_or_disabled_console_is_a_404_naming_the_console(  # type: ignore[no-untyped-def]
    db_session, db_schema
) -> None:
    instance = await make_instance(db_session)
    instance.enabled = False
    await db_session.commit()
    client = Client(db_schema, ADMIN)
    missing = uuid.uuid4()
    for console_id in (missing, instance.id):
        response = client.put(f"{BASE}/{console_id}", json={"answer": "YES"})
        assert response.status_code == 404
        assert "console" in response.json()["detail"].lower()
        assert client.post(f"{BASE}/{console_id}/start").status_code == 404
        assert client.post(f"{BASE}/{console_id}/stop").status_code == 404


async def test_an_answer_that_is_not_yes_or_no_is_refused(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    await db_session.commit()
    client = Client(db_schema, ADMIN)
    response = client.put(f"{BASE}/{instance.id}", json={"answer": "MAYBE"})
    assert response.status_code == 422
    # The browser cannot ask for a window, a number of days or a query.
    for body in ({"answer": "YES", "days": 90}, {"answer": "YES", "aql": "SELECT * FROM events"}):
        assert client.put(f"{BASE}/{instance.id}", json=body).status_code == 422


# --------------------------------------------------------------------------- the page


async def test_the_page_shows_an_undecided_console(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    await db_session.commit()
    client = Client(db_schema, READER)
    body = client.get(BASE).json()
    assert len(body) == 1
    console = body[0]
    assert console["instance_id"] == str(instance.id)
    assert console["decision"] is None
    assert console["mode"] == "UNDECIDED"
    assert [f["family"] for f in console["families"]] == [
        "PHASE_A",
        "WAF",
        "IPS",
        "CATCHALL",
        "RULE_MATCH",
    ]
    phase_a = family_of(console, "PHASE_A")
    assert phase_a["collection_enabled"] is True
    assert phase_a["collection_reason"]
    assert phase_a["window"]["days"] == 28
    assert phase_a["scopes"] == []
    assert phase_a["later_yes"] == "possible"
    assert phase_a["sources"] is None


async def test_a_disabled_console_is_not_on_the_page(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    instance.enabled = False
    await db_session.commit()
    client = Client(db_schema, READER)
    assert client.get(BASE).json() == []
    assert client.get(f"{BASE}/estimate").json() == []


async def test_the_page_shows_a_no(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    db_session.add(decision(instance.id, "NO"))
    await db_session.commit()
    console = Client(db_schema, READER).get(BASE).json()[0]
    assert console["mode"] == "NO"
    assert console["decision"]["answer"] == "NO"
    assert console["decision"]["days"] is None
    assert console["decision"]["decided_by"] == "installer"
    assert console["decision"]["source"] == "INSTALLER"
    assert console["decision"]["start_requested_at"] is None
    assert console["decision"]["stop_requested_at"] is None


async def test_the_page_shows_a_yes_with_a_scope_row_in_every_state(  # type: ignore[no-untyped-def]
    db_session, db_schema
) -> None:
    instance = await make_instance(db_session)
    db_session.add(decision(instance.id, "YES"))
    gaps = (
        Gap(START, START + timedelta(minutes=30), "search failed 3 times at the minimum width"),
        Gap(START + timedelta(hours=2), START + timedelta(hours=3), "search failed 3 times"),
    )
    db_session.add_all(
        [
            scope_row(instance.id, "PHASE_A", "", state="WAITING", progress={}),
            scope_row(
                instance.id,
                "CATCHALL",
                "",
                state="READING",
                floor=START,
                through=START + timedelta(hours=4, minutes=45),
                progress=progress_body(
                    floor=START,
                    through=START + timedelta(hours=4, minutes=45),
                    searches=12,
                    buckets_read=58,
                    gaps=gaps,
                ),
            ),
            scope_row(instance.id, "RULE_MATCH", "", state="COMPLETE", finished_at=STAMP),
            scope_row(
                instance.id,
                "IPS",
                "1",
                state="PARTIAL",
                finished_at=STAMP,
                reason="stopped by ayse",
            ),
            scope_row(instance.id, "WAF", "1", state="UNAVAILABLE", finished_at=STAMP),
            scope_row(instance.id, "WAF", "2", state="NOT_ELIGIBLE", finished_at=STAMP),
            scope_row(
                instance.id,
                "WAF",
                "3",
                state="READING",
                progress={"version": 99},
            ),
        ]
    )
    await db_session.commit()
    console = Client(db_schema, READER).get(BASE).json()[0]
    assert console["mode"] == "ACTIVE"
    assert console["decision"]["answer"] == "YES"
    assert console["decision"]["days"] == 28

    phase_a = family_of(console, "PHASE_A")
    waiting = phase_a["scopes"][0]
    assert waiting["state"] == "WAITING"
    # A scope that has not searched has no counts, and no zeros either.
    assert (waiting["searches"], waiting["buckets_read"], waiting["gaps"]) == (None, None, None)
    assert waiting["progress_unreadable"] is False

    catchall = family_of(console, "CATCHALL")
    reading = catchall["scopes"][0]
    assert reading["state"] == "READING"
    assert reading["searches"] == 12
    assert reading["buckets_read"] == 58
    assert reading["days_read"] == 0.2
    assert [g["reason"] for g in reading["gaps"]] == [g.reason for g in gaps]
    assert reading["gaps"][0]["start"].startswith("2026-10-01T00:00:00")
    assert reading["progress_unreadable"] is False

    complete = family_of(console, "RULE_MATCH")["scopes"][0]
    assert complete["state"] == "COMPLETE" and complete["reason"] is None
    partial = family_of(console, "IPS")["scopes"][0]
    assert partial["state"] == "PARTIAL" and partial["reason"] == "stopped by ayse"

    waf = family_of(console, "WAF")
    unavailable, not_eligible, unreadable = waf["scopes"]
    assert unavailable["state"] == "UNAVAILABLE"
    assert not_eligible["state"] == "NOT_ELIGIBLE"
    assert not_eligible["later_yes"] == "not possible: this source already collected"
    # A progress that does not decode is unknown, never a zero.
    assert unreadable["progress_unreadable"] is True
    assert (unreadable["searches"], unreadable["buckets_read"], unreadable["gaps"]) == (
        None,
        None,
        None,
    )


async def test_a_restart_stopped_before_stepping_shows_kept_days_without_counts(
    db_session, db_schema
) -> None:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    target = START
    floor = target - timedelta(hours=1)
    through = target + timedelta(hours=23, minutes=55)
    db_session.add_all(
        [
            decision(instance.id, "YES"),
            scope_row(
                instance.id,
                "CATCHALL",
                "",
                state="PARTIAL",
                days_target=7,
                target_start=target,
                floor=floor,
                through=through,
                finished_at=STAMP,
                reason="stopped by the operator",
                progress={},
            ),
        ]
    )
    await db_session.commit()

    shown = family_of(Client(db_schema, READER).get(BASE).json()[0], "CATCHALL")["scopes"][0]

    assert shown["days_read"] == 1.0
    assert shown["searches"] is None
    assert shown["buckets_read"] is None
    assert shown["gaps"] is None
    assert shown["progress_unreadable"] is False


async def test_the_page_lists_waf_and_ips_sources_with_and_without_live_rows(  # type: ignore[no-untyped-def]
    db_session, db_schema
) -> None:
    instance = await make_instance(db_session)
    live = await make_log_source(db_session, instance, qradar_id=1, name="waf-live")
    fresh = await make_log_source(db_session, instance, qradar_id=2, name="waf-fresh")
    scoped = await make_log_source(db_session, instance, qradar_id=3, name="waf-scoped")
    ips_live = await make_log_source(db_session, instance, qradar_id=4, name="ips-live")
    db_session.add_all(
        [
            waf_profile(instance.id, live.id),
            waf_profile(instance.id, fresh.id),
            waf_profile(instance.id, scoped.id),
            ips_profile(instance.id, ips_live.id),
            waf_bucket(live.id, START),
            scope_row(instance.id, "WAF", str(scoped.qradar_id), state="READING"),
        ]
    )
    await db_session.commit()
    console = Client(db_schema, READER).get(BASE).json()[0]
    waf = family_of(console, "WAF")
    # A source with a scope row is a scope, not a source entry.
    assert [s["scope_key"] for s in waf["scopes"]] == [str(scoped.qradar_id)]
    assert {s["scope_key"]: s["later_yes"] for s in waf["sources"]} == {
        str(live.qradar_id): "not possible: this source already collected",
        str(fresh.qradar_id): "possible",
    }
    assert all(s["name"] for s in waf["sources"])
    ips = family_of(console, "IPS")
    assert {s["scope_key"]: s["later_yes"] for s in ips["sources"]} == {
        str(ips_live.qradar_id): "possible"
    }
    assert ips["later_yes"] is None


async def test_a_scope_row_carries_its_log_sources_name(db_session, db_schema) -> None:
    """HB-8r1: a WAF or IPS scope row is named; a console-wide row and an unknown id are not."""
    instance = await make_instance(db_session)
    await make_log_source(db_session, instance, qradar_id=7, name="web-7")
    await make_log_source(db_session, instance, qradar_id=9, name="ips-9")
    db_session.add_all(
        [
            scope_row(instance.id, "WAF", "7", state="READING"),
            # The source is gone from the inventory: the page shows the key, not a name.
            scope_row(instance.id, "WAF", "8", state="READING"),
            scope_row(instance.id, "IPS", "9", state="READING"),
            scope_row(instance.id, "PHASE_A", "", state="READING"),
        ]
    )
    await db_session.commit()
    console = Client(db_schema, READER).get(BASE).json()[0]
    assert [s["name"] for s in family_of(console, "WAF")["scopes"]] == ["web-7", None]
    assert [s["name"] for s in family_of(console, "IPS")["scopes"]] == ["ips-9"]
    assert [s["name"] for s in family_of(console, "PHASE_A")["scopes"]] == [None]


async def test_the_page_says_which_family_floor_is_proved(db_session, db_schema) -> None:
    """§B.3.2: a read start is not a proved floor, and its margin buckets are not read."""
    instance = await make_instance(db_session)
    target_start = START + timedelta(hours=24)
    db_session.add_all(
        [
            scope_row(
                instance.id,
                "PHASE_A",
                "",
                floor=target_start - timedelta(hours=24),
                target_start=target_start,
                through=target_start + timedelta(hours=10),
            ),
            scope_row(
                instance.id,
                "CATCHALL",
                "",
                floor=START,
                through=START + timedelta(hours=4, minutes=45),
            ),
            scope_row(instance.id, "WAF", "7", floor=START, through=START + timedelta(hours=10)),
            scope_row(instance.id, "IPS", "9", floor=START, through=START + timedelta(hours=10)),
            scope_row(
                instance.id, "RULE_MATCH", "", floor=START, through=START + timedelta(hours=10)
            ),
        ]
    )
    await db_session.commit()
    console = Client(db_schema, READER).get(BASE).json()[0]
    phase_a = family_of(console, "PHASE_A")["scopes"][0]
    assert phase_a["floor_proved"] is False
    # `floor` is the read start, `T0 - m`; the days read count from `T0`.
    assert phase_a["floor"].startswith("2026-10-01T00:00:00")
    assert phase_a["target_start"].startswith("2026-10-02T00:00:00")
    assert phase_a["days_read"] == 0.4
    catchall = family_of(console, "CATCHALL")["scopes"][0]
    assert catchall["floor_proved"] is False
    assert catchall["days_read"] == 0.2
    for family, days in (("WAF", 0.4), ("IPS", 0.4), ("RULE_MATCH", 0.5)):
        scope = family_of(console, family)["scopes"][0]
        assert scope["floor_proved"] is True, family
        # Five-minute buckets for WAF and IPS, hourly windows for rule matches.
        assert scope["days_read"] == days, family


async def test_a_family_that_is_switched_off_is_a_row_not_an_absence(  # type: ignore[no-untyped-def]
    db_session, db_schema
) -> None:
    await make_instance(db_session)
    await db_session.commit()
    # WAF collection is off by default; the family is still a row.
    console = Client(db_schema, READER).get(BASE).json()[0]
    waf = family_of(console, "WAF")
    assert waf["collection_enabled"] is False
    assert "WAF_COLLECTION_ENABLED" in waf["collection_reason"]
    assert waf["scopes"] == []


async def test_the_window_is_limited_by_the_retention(db_session, db_schema) -> None:
    await make_instance(db_session)
    await db_session.commit()
    client = Client(
        db_schema,
        READER,
        settings_update={"waf_collection_enabled": True, "waf_metric_retention_days": 30},
    )
    console = client.get(BASE).json()[0]
    window = family_of(console, "WAF")["window"]
    assert window["days_requested"] == 28
    assert window["days"] == 26
    assert window["available"] is True
    assert window["limited_by"] == ["waf_metric_retention_days"]
    assert "reduced from 28 to 26 days" in window["reason"]


async def test_a_window_below_the_minimum_is_unavailable_with_its_reason(  # type: ignore[no-untyped-def]
    db_session, db_schema
) -> None:
    await make_instance(db_session)
    await db_session.commit()
    # A retention that leaves less than the seven days of §B.6.5: the family has
    # no window at all, and says why.
    client = Client(
        db_schema,
        READER,
        settings_update={
            "waf_collection_enabled": True,
            "waf_metric_retention_days": 9,
            "baseline_lookback_days": 7,
        },
    )
    console = client.get(BASE).json()[0]
    window = family_of(console, "WAF")["window"]
    assert window["available"] is False
    assert window["days"] is None
    assert "history unavailable" in window["reason"]
    assert "WAF_METRIC_RETENTION_DAYS" in window["reason"]


# --------------------------------------------------------------------------- the estimate


async def test_the_estimate_uses_the_seeded_counts(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    for index in (1, 2):
        await make_log_source(db_session, instance, qradar_id=index, name=f"src-{index}")
    waf_source = await make_log_source(db_session, instance, qradar_id=10, name="waf-1")
    ips_source = await make_log_source(db_session, instance, qradar_id=11, name="ips-1")
    catchall_source = await make_log_source(db_session, instance, qradar_id=12, name="catch-all")
    db_session.add_all(
        [
            waf_profile(instance.id, waf_source.id),
            ips_profile(instance.id, ips_source.id),
            waf_endpoint(instance.id, waf_source.id, "/a"),
            waf_endpoint(instance.id, waf_source.id, "/b"),
            waf_endpoint(instance.id, waf_source.id, "/c"),
            ips_entity(instance.id, ips_source.id, IpsDimension.DESTINATION_PORT, "443/tcp"),
            ips_entity(instance.id, ips_source.id, IpsDimension.DESTINATION_PORT, "80/tcp"),
            ips_entity(instance.id, ips_source.id, IpsDimension.DESTINATION_IP, "10.0.0.1"),
            ips_entity(instance.id, ips_source.id, IpsDimension.SIGNATURE, "1234"),
            catchall_bucket(catchall_source.id, datetime.now(UTC) - timedelta(hours=1)),
            rule(instance.id, 1),
            rule(instance.id, 2),
        ]
    )
    await db_session.commit()
    client = Client(
        db_schema,
        READER,
        settings_update={
            "waf_collection_enabled": True,
            "ips_collection_enabled": True,
            "rule_match_collection_enabled": True,
        },
    )
    body = client.get(f"{BASE}/estimate").json()
    assert len(body) == 1
    console = body[0]
    assert console["kind"] == "estimate"
    estimates = {item["family"]: item for item in console["families"]}
    assert set(estimates) == {"PHASE_A", "WAF", "IPS", "CATCHALL", "RULE_MATCH"}
    # The §B.8.2 formulas over the seeded counts: five monitored sources, one WAF
    # source with three tracked endpoints, one IPS source with two ports and one
    # IP and one signature, one sender per catch-all bucket and two rules.
    assert estimates["PHASE_A"]["searches"] == 11
    assert estimates["WAF"]["searches"] == 23
    assert estimates["IPS"]["searches"] == 153
    assert estimates["CATCHALL"]["searches"] == 6
    # WAF spends 20 searches per tick (two sources under the global permit, §B.8.3),
    # so 23 searches are two ticks and not three.
    assert estimates["WAF"]["ticks"] == 2
    # Two rules fit in one search of 168 hourly windows, but a search never covers
    # more than a week: 28 days of hourly windows are five searches, plus the
    # console total.
    assert estimates["RULE_MATCH"]["searches"] == 6
    for item in estimates.values():
        assert item["days_requested"] == 28
        assert item["days_fitted"] == 28
        assert item["reduced"] is False
        assert item["refused"] is False
        assert item["ticks"] >= 1
        assert item["hours"] > 0
        assert item["assumptions"]
    assert any(
        "QRadar time per search not measured" in a for a in estimates["PHASE_A"]["assumptions"]
    )


async def test_a_family_without_data_has_no_estimate_not_a_zero(db_session, db_schema) -> None:
    await make_instance(db_session)
    await db_session.commit()
    client = Client(
        db_schema,
        READER,
        settings_update={"waf_collection_enabled": True, "ips_collection_enabled": True},
    )
    console = client.get(f"{BASE}/estimate").json()[0]
    estimates = {item["family"]: item for item in console["families"]}
    waf = estimates["WAF"]
    assert (waf["searches"], waf["ticks"], waf["hours"], waf["days_fitted"]) == (
        None,
        None,
        None,
        None,
    )
    assert "no WAF source" in waf["reason"]
    # A family whose collection is switched off says so, and has no numbers either.
    rules = estimates["RULE_MATCH"]
    assert rules["searches"] is None
    assert "switched off" in rules["reason"]


async def test_the_estimate_hours_follow_the_family_cadence(db_session, db_schema) -> None:
    """The wall time is the model's figure at the cadence the operator set (§B.8.3)."""
    instance = await make_instance(db_session)
    for index in (1, 2, 3, 4, 5):
        await make_log_source(db_session, instance, qradar_id=index, name=f"src-{index}")
    db_session.add(
        SearchCadenceSetting(
            family="PHASE_A_VOLUME", cadence_seconds=3600, updated_at=STAMP, updated_by="admin"
        )
    )
    await db_session.commit()
    console = Client(db_schema, READER).get(f"{BASE}/estimate").json()[0]
    phase_a = next(item for item in console["families"] if item["family"] == "PHASE_A")
    assert phase_a["searches"] == 11
    assert phase_a["ticks"] == 2
    # Two ticks at an hour each, not at the default five minutes.
    assert phase_a["hours"] == 2.0
    assert "one tick every 1 hour" in " ".join(phase_a["assumptions"])


async def test_the_waf_estimate_describes_the_same_fitted_window_as_the_adapter(
    db_session, db_schema
) -> None:
    """HB-15 removes the old warning because the adapter now applies this fit."""
    instance = await make_instance(db_session)
    for index in (1, 2, 3):
        await make_log_source(db_session, instance, qradar_id=index, name=f"src-{index}")
    waf_source = await make_log_source(db_session, instance, qradar_id=10, name="waf-1")
    db_session.add(waf_profile(instance.id, waf_source.id))
    db_session.add_all(
        [waf_endpoint(instance.id, waf_source.id, f"/{index}") for index in ("a", "b", "c")]
    )
    await db_session.commit()
    client = Client(
        db_schema,
        READER,
        settings_update={
            "waf_collection_enabled": True,
            "history_backfill_max_hours": 1,
            "history_backfill_searches_per_tick": 1,
        },
    )
    estimates = {
        item["family"]: item
        for item in client.get(f"{BASE}/estimate").json()[0]["families"]
    }
    waf = estimates["WAF"]
    assert waf["refused"] or waf["reduced"]
    assert "the WAF reading does not apply it" not in waf["reason"]
    assert "does not shorten its window or refuse it" not in waf["reason"]
    assert "per-source" in waf["reason"]
    assert "does not shorten" not in estimates["PHASE_A"]["reason"]


async def test_a_source_qradar_has_disabled_is_still_collected_and_counted(
    db_session, db_schema
) -> None:
    """The Phase A count is the collector's own filter: every monitored source."""
    instance = await make_instance(db_session)
    monitored = await make_log_source(db_session, instance, qradar_id=1, name="on")
    disabled = await make_log_source(db_session, instance, qradar_id=2, name="off")
    disabled.enabled = False  # disabled in QRadar, still monitored by the platform
    await db_session.commit()
    console = Client(db_schema, READER).get(f"{BASE}/estimate").json()[0]
    phase_a = next(item for item in console["families"] if item["family"] == "PHASE_A")
    # Two monitored sources: one search of 4 000 rows covers both.
    assert phase_a["searches"] == 5
    assert "2 monitored sources" in " ".join(phase_a["assumptions"])
    assert monitored.id != disabled.id


async def test_a_catch_all_bucket_without_senders_is_a_measurement_not_a_gap(
    db_session, db_schema
) -> None:
    """An empty bucket is a measured zero; the estimate says so instead of "no data"."""
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=12, name="catch-all")
    db_session.add(
        catchall_bucket(
            source.id,
            datetime.now(UTC) - timedelta(hours=1),
            distinct_senders=0,
            events=0,
        )
    )
    await db_session.commit()
    console = Client(db_schema, READER).get(f"{BASE}/estimate").json()[0]
    catchall = next(item for item in console["families"] if item["family"] == "CATCHALL")
    assert catchall["searches"] is not None
    assert "0 senders per bucket" in " ".join(catchall["assumptions"])
    assert "no catch-all bucket" not in catchall["reason"]


# --------------------------------------------------------------------------- the decision


async def test_put_records_a_first_answer_and_audits_it(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    await db_session.commit()
    client = Client(db_schema, ADMIN)
    response = client.put(f"{BASE}/{instance.id}", json={"answer": "YES"})
    assert response.status_code == 201, response.text
    console = response.json()
    assert console["mode"] == "ACTIVE"
    assert console["decision"]["answer"] == "YES"
    assert console["decision"]["days"] == 28
    assert console["decision"]["decided_by"] == "admin"
    assert console["decision"]["source"] == "UI"

    rows = await audit_rows(db_session, "history_backfill.decide")
    assert len(rows) == 1
    assert rows[0].actor == "admin"
    assert rows[0].object_type == "qradar_instance"
    assert rows[0].object_id == str(instance.id)
    assert rows[0].detail == {
        "answer": "YES",
        "days": 28,
        "source": "UI",
        "previous": None,
    }


async def test_put_upgrades_a_no_to_a_yes(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    db_session.add(decision(instance.id, "NO"))
    await db_session.commit()
    client = Client(db_schema, ADMIN)
    response = client.put(f"{BASE}/{instance.id}", json={"answer": "YES"})
    assert response.status_code == 200, response.text
    console = response.json()
    assert console["mode"] == "ACTIVE"
    assert console["decision"]["answer"] == "YES"
    assert console["decision"]["days"] == 28
    assert console["decision"]["updated_by"] == "admin"
    assert console["decision"]["decided_by"] == "installer"  # the first answer is kept

    rows = await audit_rows(db_session, "history_backfill.decide")
    assert len(rows) == 1
    assert rows[0].detail["previous"] == "NO"
    assert rows[0].detail["answer"] == "YES"


async def test_put_of_the_same_answer_changes_nothing(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    db_session.add(decision(instance.id, "YES"))
    await db_session.commit()
    client = Client(db_schema, ADMIN)
    response = client.put(f"{BASE}/{instance.id}", json={"answer": "YES"})
    assert response.status_code == 200
    assert response.json()["decision"]["answer"] == "YES"
    assert await audit_rows(db_session, "history_backfill.decide") == []


async def test_put_cannot_reverse_a_yes(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    db_session.add(decision(instance.id, "YES"))
    await db_session.commit()
    client = Client(db_schema, ADMIN)
    response = client.put(f"{BASE}/{instance.id}", json={"answer": "NO"})
    assert response.status_code == 409
    body = response.json()
    assert "never reversed" in body["detail"]
    assert "stop the reading" in body["detail"]
    # The stored answer is unchanged.
    assert body["console"]["decision"]["answer"] == "YES"
    assert await audit_rows(db_session, "history_backfill.decide") == []


async def test_the_stored_row_wins_a_race(db_session, db_schema, monkeypatch) -> None:
    instance = await make_instance(db_session)
    await db_session.commit()
    original = HistoryBackfillService._stored_decision

    async def raced(self, instance_id):  # type: ignore[no-untyped-def]
        stored = await original(self, instance_id)
        if stored is None:
            # Another administrator answers between this read and the write.
            self.session.add(
                HistoryBackfillDecision(
                    instance_id=instance_id,
                    answer="NO",
                    days=None,
                    decided_at=STAMP,
                    decided_by="rival",
                    source="UI",
                )
            )
            await self.session.flush()
        return stored

    monkeypatch.setattr(HistoryBackfillService, "_stored_decision", raced)
    client = Client(db_schema, ADMIN)
    response = client.put(f"{BASE}/{instance.id}", json={"answer": "YES"})
    assert response.status_code == 409
    body = response.json()
    assert body["console"]["decision"]["answer"] == "NO"
    assert body["console"]["decision"]["decided_by"] == "rival"
    # The stored row won: nothing was written and nothing was audited.
    assert await audit_rows(db_session, "history_backfill.decide") == []


# --------------------------------------------------------------------------- the requests


async def test_start_records_the_request_and_audits_the_counts(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    db_session.add(decision(instance.id, "YES"))
    db_session.add_all(
        [
            scope_row(instance.id, "PHASE_A", "", state="READING"),
            scope_row(instance.id, "WAF", "1", state="READING"),
            scope_row(instance.id, "CATCHALL", "", state="COMPLETE", finished_at=STAMP),
        ]
    )
    await db_session.commit()
    client = Client(db_schema, ADMIN)
    response = client.post(f"{BASE}/{instance.id}/start")
    assert response.status_code == 200, response.text
    console = response.json()
    assert console["decision"]["start_requested_by"] == "admin"
    assert console["decision"]["start_requested_at"] is not None
    assert console["decision"]["stop_requested_at"] is None
    assert "next collection" in console["message"]

    rows = await audit_rows(db_session, "history_backfill.start")
    assert len(rows) == 1
    assert rows[0].actor == "admin"
    assert rows[0].object_type == "qradar_instance"
    assert rows[0].object_id == str(instance.id)
    assert rows[0].detail == {"scopes_reading": 2, "scopes_finished": 1}


async def test_stop_records_the_request(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    db_session.add(decision(instance.id, "YES"))
    await db_session.commit()
    client = Client(db_schema, ADMIN)
    response = client.post(f"{BASE}/{instance.id}/stop")
    assert response.status_code == 200, response.text
    console = response.json()
    assert console["mode"] == "STOPPED"
    assert console["decision"]["stop_requested_by"] == "admin"
    assert "next collection" in console["message"]
    rows = await audit_rows(db_session, "history_backfill.stop")
    assert len(rows) == 1
    assert rows[0].detail == {"scopes_reading": 0, "scopes_finished": 0}


async def test_a_request_is_refused_without_a_yes(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    db_session.add(decision(instance.id, "NO"))
    await db_session.commit()
    client = Client(db_schema, ADMIN)
    for kind in ("start", "stop"):
        response = client.post(f"{BASE}/{instance.id}/{kind}")
        assert response.status_code == 409
        assert "record Yes first" in response.json()["detail"]
    assert await audit_rows(db_session, "history_backfill.start") == []
    assert await audit_rows(db_session, "history_backfill.stop") == []


async def test_a_request_on_an_undecided_console_is_refused(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    await db_session.commit()
    client = Client(db_schema, ADMIN)
    response = client.post(f"{BASE}/{instance.id}/start")
    assert response.status_code == 409
    assert "record Yes first" in response.json()["detail"]


async def test_a_start_after_a_stop_reads_active_again(db_session, db_schema) -> None:
    """The two requests are separate: the later one wins (HB-2b's tie rule)."""
    instance = await make_instance(db_session)
    db_session.add(decision(instance.id, "YES"))
    await db_session.commit()
    client = Client(db_schema, ADMIN)
    stopped = client.post(f"{BASE}/{instance.id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["mode"] == "STOPPED"
    started = client.post(f"{BASE}/{instance.id}/start")
    assert started.status_code == 200
    console = started.json()
    assert console["mode"] == "ACTIVE"
    assert console["decision"]["stop_requested_by"] == "admin"
    assert console["decision"]["start_requested_by"] == "admin"
    assert console["decision"]["start_requested_at"] > console["decision"]["stop_requested_at"]
    # And a stop after that start is a stop again.
    stopped_again = client.post(f"{BASE}/{instance.id}/stop")
    assert stopped_again.json()["mode"] == "STOPPED"


async def test_a_request_is_stamped_after_the_opposite_one(db_session, db_schema) -> None:
    """A clock that does not move between two requests must not tie them."""
    instance = await make_instance(db_session)
    db_session.add(decision(instance.id, "YES"))
    await db_session.commit()
    frozen = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    service = HistoryBackfillService(db_session, get_settings(), clock=lambda: frozen)
    await service.request(instance.id, "stop", actor="admin", source_ip=None)
    await db_session.commit()
    outcome = await service.request(instance.id, "start", actor="admin", source_ip=None)
    await db_session.commit()
    assert outcome.console.mode == "ACTIVE"
    row = await db_session.scalar(
        select(HistoryBackfillDecision).where(HistoryBackfillDecision.instance_id == instance.id)
    )
    assert row is not None
    assert row.start_requested_at is not None and row.stop_requested_at is not None
    assert row.start_requested_at > row.stop_requested_at


async def test_no_request_changes_a_scope_row(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    db_session.add(decision(instance.id, "YES"))
    db_session.add(
        scope_row(
            instance.id,
            "PHASE_A",
            "",
            state="READING",
            floor=START,
            through=START + timedelta(hours=1),
            progress=progress_body(floor=START, through=START + timedelta(hours=1), searches=3),
        )
    )
    await db_session.commit()
    before = await _scope_table(db_session, instance.id)

    client = Client(db_schema, ADMIN)
    assert client.post(f"{BASE}/{instance.id}/start").status_code == 200
    assert client.post(f"{BASE}/{instance.id}/stop").status_code == 200
    after = await _scope_table(db_session, instance.id)
    assert after == before


async def _scope_table(session, instance_id: uuid.UUID) -> list[tuple]:  # type: ignore[no-untyped-def]
    rows = await session.scalars(
        select(HistoryBackfillScope)
        .where(HistoryBackfillScope.instance_id == instance_id)
        .order_by(HistoryBackfillScope.family, HistoryBackfillScope.scope_key)
        .execution_options(populate_existing=True)
    )
    return [
        (
            row.family,
            row.scope_key,
            row.state,
            row.days_requested,
            row.days_target,
            row.target_start,
            row.floor,
            row.through,
            row.anchor,
            row.started_at,
            row.finished_at,
            row.reason,
            row.progress,
        )
        for row in rows
    ]


# --------------------------------------------------------------------------- the collector's table


async def test_no_route_writes_a_scope_row(db_session, db_schema) -> None:
    instance = await make_instance(db_session)
    db_session.add(decision(instance.id, "YES"))
    db_session.add(scope_row(instance.id, "PHASE_A", "", state="READING"))
    await db_session.commit()
    client = Client(db_schema, ADMIN)

    assert client.get(BASE).status_code == 200
    assert client.writes_to("history_backfill_scope") == []
    assert client.get(f"{BASE}/estimate").status_code == 200
    assert client.writes_to("history_backfill_scope") == []
    assert client.put(f"{BASE}/{instance.id}", json={"answer": "YES"}).status_code == 200
    assert client.writes_to("history_backfill_scope") == []
    assert client.post(f"{BASE}/{instance.id}/start").status_code == 200
    assert client.writes_to("history_backfill_scope") == []
    assert client.post(f"{BASE}/{instance.id}/stop").status_code == 200
    assert client.writes_to("history_backfill_scope") == []


async def test_the_decision_row_is_the_only_thing_a_write_touches(db_session, db_schema) -> None:
    """A write touches the decision row and the audit log, and nothing else."""
    instance = await make_instance(db_session)
    await db_session.commit()
    client = Client(db_schema, ADMIN)
    client.put(f"{BASE}/{instance.id}", json={"answer": "YES"})
    written = {
        table
        for table in ("history_backfill_decision", "history_backfill_scope", "audit_log")
        if client.writes_to(table)
    }
    assert written == {"history_backfill_decision", "audit_log"}


async def test_the_reads_cost_the_same_however_many_rows_there_are(  # type: ignore[no-untyped-def]
    db_session, db_schema
) -> None:
    """Rule 9: the page is a constant number of statements, not one per source."""
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1, name="waf-1")
    db_session.add(waf_profile(instance.id, source.id))
    db_session.add_all(
        [scope_row(instance.id, "WAF", str(index), state="READING") for index in range(1, 4)]
    )
    await db_session.commit()
    client = Client(db_schema, READER)
    client.get(BASE)
    one = len(client.statements)
    first_statements = list(client.statements)

    for index in range(4, 12):
        extra = await make_log_source(db_session, instance, qradar_id=index, name=f"waf-{index}")
        db_session.add(waf_profile(instance.id, extra.id))
        db_session.add(scope_row(instance.id, "WAF", str(index), state="READING"))
    await db_session.commit()
    client.get(BASE)
    many = len(client.statements)
    assert many == one, (first_statements, list(client.statements))

    client.get(f"{BASE}/estimate")
    estimate_statements = len(client.statements)
    extra = await make_log_source(db_session, instance, qradar_id=99, name="waf-99")
    db_session.add(waf_profile(instance.id, extra.id))
    await db_session.commit()
    client.get(f"{BASE}/estimate")
    assert len(client.statements) == estimate_statements


async def test_the_names_of_twenty_waf_scope_rows_cost_one_statement(
    db_session, db_schema
) -> None:
    """HB-8r1 item 3: the names are one bulk statement, measured at 1 and 20 rows (rule 9)."""
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1, name="waf-1")
    db_session.add(waf_profile(instance.id, source.id))
    db_session.add(scope_row(instance.id, "WAF", "1", state="READING"))
    await db_session.commit()
    client = Client(db_schema, READER)

    console = client.get(BASE).json()[0]
    assert [s["name"] for s in family_of(console, "WAF")["scopes"]] == ["waf-1"]
    one = list(client.statements)

    for index in range(2, 21):
        extra = await make_log_source(db_session, instance, qradar_id=index, name=f"waf-{index}")
        db_session.add(waf_profile(instance.id, extra.id))
        db_session.add(scope_row(instance.id, "WAF", str(index), state="READING"))
    await db_session.commit()

    console = client.get(BASE).json()[0]
    waf = family_of(console, "WAF")
    assert len(waf["scopes"]) == 20
    assert {s["scope_key"]: s["name"] for s in waf["scopes"]} == {
        str(index): f"waf-{index}" for index in range(1, 21)
    }
    assert len(client.statements) == len(one), (one, list(client.statements))
