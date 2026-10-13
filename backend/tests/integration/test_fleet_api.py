"""Phase C fleet API: summary, source list, override, review, rules. DB-gated."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.alerts.fingerprint import inventory_fingerprint
from app.alerts.service import AlertInput, AlertService
from app.api.routes import fleet as fleet_routes
from app.core.database import get_session
from app.fleet.service import ensure_seed_rules
from app.main import create_app
from app.models.alert import Alert
from app.models.enums import (
    AlertStatus,
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    Severity,
    SourceClass,
)
from app.models.fleet import ClassificationRule, LogSourceClassification
from app.models.identity import AuditLog
from app.models.log_source import LogSourceAnomaly, LogSourceBaseline
from app.models.outage import OutageAlertPolicy
from app.security.auth import get_principal
from app.security.rbac import Principal
from tests.integration.factories import add_metric, make_instance, make_log_source

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run fleet API integration tests",
    ),
]

ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
VIEWER = Principal(subject="viewer", permissions=frozenset({"read:*"}))
EDITOR = Principal(subject="editor", permissions=frozenset({"read:*", "log_source:write"}))
RULE_ADMIN = Principal(
    subject="rule-admin", permissions=frozenset({"read:*", "admin:classification"})
)
NOTHING = Principal(subject="nobody", permissions=frozenset())

# 2026-10-13 is a Tuesday. The route closes the window at the last whole bucket.
NOW = datetime(2026, 10, 13, 10, 7, tzinfo=UTC)
CLOSED = datetime(2026, 10, 13, 10, 5, tzinfo=UTC)
MISSING_ID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch) -> None:
    monkeypatch.setattr(fleet_routes, "_utcnow", lambda: NOW)


def _maker():  # type: ignore[no-untyped-def]
    # NullPool: TestClient runs each request on a fresh event loop, and a pooled
    # asyncpg connection is bound to the loop that opened it.
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], future=True, poolclass=NullPool)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class Client:
    """A TestClient for one principal that also counts SQL statements per request."""

    def __init__(self, principal: Principal) -> None:
        self.engine, maker = _maker()
        self.statements = 0

        @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
        def _count(*args, **kwargs):  # type: ignore[no-untyped-def]
            self.statements += 1

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
        app.dependency_overrides[get_principal] = lambda: principal
        self.http = TestClient(app)

    def request(self, method: str, url: str, **kwargs):  # type: ignore[no-untyped-def]
        self.statements = 0
        return self.http.request(method, url, **kwargs)

    def get(self, url: str, **kw):  # type: ignore[no-untyped-def]
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw):  # type: ignore[no-untyped-def]
        return self.request("POST", url, **kw)

    def patch(self, url: str, **kw):  # type: ignore[no-untyped-def]
        return self.request("PATCH", url, **kw)

    def delete(self, url: str, **kw):  # type: ignore[no-untyped-def]
        return self.request("DELETE", url, **kw)


def run(work: Callable[..., Awaitable]):  # type: ignore[no-untyped-def]
    """Run `work(session)` in its own committed session, outside the app."""

    async def _go():  # type: ignore[no-untyped-def]
        engine, maker = _maker()
        try:
            async with maker() as session:
                result = await work(session)
                await session.commit()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(_go())


def classification(source, cls: str, *, review="REVIEWED", basis="NAME_REGEX", conflicts=None):
    return LogSourceClassification(
        log_source_id=source.id, source_class=cls, basis=basis,
        conflicts=conflicts or [], classified_at=CLOSED, review_status=review,
    )


def audit_rows(action: str) -> list[AuditLog]:
    async def _q(s):  # type: ignore[no-untyped-def]
        return list((await s.scalars(select(AuditLog).where(AuditLog.action == action))).all())

    return run(_q)


def by_class(body: dict) -> dict[str, dict]:
    return {c["source_class"]: c for c in body["classes"]}


# ----------------------------------------------------------------- access control


def test_every_route_needs_read_fleet_and_writes_need_more(db_schema) -> None:
    assert Client(NOTHING).get("/api/v1/fleet/summary").status_code == 403
    assert Client(NOTHING).get("/api/v1/fleet/sources").status_code == 403
    assert Client(NOTHING).get("/api/v1/classification-rules").status_code == 403
    assert Client(VIEWER).get("/api/v1/fleet/summary").status_code == 200
    assert Client(VIEWER).get("/api/v1/fleet/sources").status_code == 200
    assert Client(VIEWER).get("/api/v1/classification-rules").status_code == 200

    viewer = Client(VIEWER)
    # 403 before 404: a forbidden caller learns nothing about which ids exist.
    assert viewer.patch(
        f"/api/v1/log-sources/{MISSING_ID}/classification", json={"override_class": None}
    ).status_code == 403
    assert viewer.post(f"/api/v1/log-sources/{MISSING_ID}/review").status_code == 403
    assert viewer.post(
        "/api/v1/classification-rules",
        json={"kind": "NAME_REGEX", "pattern": "x", "target_class": "OTHER"},
    ).status_code == 403
    assert viewer.patch(
        f"/api/v1/classification-rules/{MISSING_ID}", json={"enabled": False}
    ).status_code == 403
    assert viewer.delete(f"/api/v1/classification-rules/{MISSING_ID}").status_code == 403
    # log_source:write does not grant rule administration, nor the reverse.
    editor = Client(EDITOR)
    assert editor.delete(f"/api/v1/classification-rules/{MISSING_ID}").status_code == 403
    assert Client(RULE_ADMIN).patch(
        f"/api/v1/log-sources/{MISSING_ID}/classification", json={"override_class": None}
    ).status_code == 403
    assert audit_rows("log_source.classification_override") == []


# ----------------------------------------------------------------------- summary


def _bucket(k: int) -> datetime:
    """The k-th closed 5-minute bucket before the window end (k=1 is the latest)."""
    return CLOSED - timedelta(minutes=5 * k)


def test_summary_reports_completeness_and_never_a_zero_for_a_missing_measurement(
    db_schema,
) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        fw_a = await make_log_source(s, inst, qradar_id=1, name="fw-a")
        fw_b = await make_log_source(s, inst, qradar_id=2, name="fw-b")
        fw_off = await make_log_source(s, inst, qradar_id=3, name="fw-off", enabled=False)
        ips = await make_log_source(s, inst, qradar_id=4, name="ips-a")
        waf = await make_log_source(s, inst, qradar_id=5, name="waf-a")
        internal = await make_log_source(s, inst, qradar_id=6, name="Health Metrics")
        loose = await make_log_source(s, inst, qradar_id=7, name="no-classification-row")
        s.add_all([
            classification(fw_a, "FIREWALL"),
            classification(fw_b, "FIREWALL", review="NEW", conflicts=[
                {"basis": "TYPE_NAME_REGEX", "rule_id": "r", "class": "IPS"}]),
            classification(fw_off, "FIREWALL"),
            classification(ips, "IPS"),
            classification(waf, "WAF"),
            classification(internal, "QRADAR_INTERNAL", basis="INTERNAL"),
        ])
        await s.flush()
        # fw-a: all 12 buckets of the hour, 3000 events each (10 eps).
        for k in range(1, 13):
            await add_metric(s, fw_a, _bucket(k), event_count=3000, average_eps=10.0)
        # fw-b: 6 complete (1500 events, 5 eps), 2 partial, 1 explicit MISSING.
        for k in range(1, 7):
            await add_metric(s, fw_b, _bucket(k), event_count=1500, average_eps=5.0)
        for k in (7, 8):
            await add_metric(s, fw_b, _bucket(k), event_count=999,
                             completeness=BucketCompleteness.PARTIAL)
        await add_metric(s, fw_b, _bucket(9), event_count=0,
                         completeness=BucketCompleteness.MISSING)
        # Outside the window (older than 1h, and the still-open current bucket).
        await add_metric(s, fw_a, _bucket(13), event_count=777)
        await add_metric(s, fw_a, CLOSED, event_count=777)
        # waf: only a partial bucket -> no complete measurement at all.
        await add_metric(s, waf, _bucket(1), event_count=5000,
                         completeness=BucketCompleteness.PARTIAL)
        # internal source reports volume; it must not leak into any class.
        await add_metric(s, internal, _bucket(1), event_count=100000)
        s.add(LogSourceBaseline(
            log_source_id=fw_a.id, metric_name="average_eps", weekday=2, hour=10,
            median=9.0, mad=1.0, sample_count=30, is_reliable=True, observations=[9.0],
        ))
        s.add(LogSourceBaseline(  # unreliable: never an expectation
            log_source_id=fw_b.id, metric_name="average_eps", weekday=2, hour=10,
            median=500.0, mad=1.0, sample_count=2, is_reliable=False, observations=[1.0],
        ))
        s.add(LogSourceBaseline(  # wrong hour
            log_source_id=ips.id, metric_name="average_eps", weekday=2, hour=11,
            median=4.0, mad=1.0, sample_count=30, is_reliable=True, observations=[4.0],
        ))
        s.add_all([
            LogSourceAnomaly(
                log_source_id=fw_a.id, anomaly_type=AnomalyType.VOLUME_DROP,
                severity=Severity.HIGH, detected_at=CLOSED, state=AnomalyState.OPEN),
            LogSourceAnomaly(
                log_source_id=fw_b.id, anomaly_type=AnomalyType.VOLUME_DROP,
                severity=Severity.HIGH, detected_at=CLOSED, state=AnomalyState.OPEN),
            LogSourceAnomaly(
                log_source_id=fw_b.id, anomaly_type=AnomalyType.NO_EVENTS,
                severity=Severity.HIGH, detected_at=CLOSED, state=AnomalyState.RESOLVED),
            LogSourceAnomaly(
                log_source_id=ips.id, anomaly_type=AnomalyType.TIMESTAMP_DELAY,
                severity=Severity.LOW, detected_at=CLOSED, state=AnomalyState.OPEN,
                suppressed=True),
        ])
        del loose

    run(setup)
    body = Client(VIEWER).get("/api/v1/fleet/summary?window=1h").json()

    assert body["window"] == "1h" and body["bucket_seconds"] == 300
    assert body["window_end"].startswith("2026-10-13T10:05:00")
    assert body["window_start"].startswith("2026-10-13T09:05:00")
    classes = by_class(body)
    assert "QRADAR_INTERNAL" not in classes

    fw = classes["FIREWALL"]
    assert fw["sources"] == {"enabled": 2, "disabled": 1}
    # 2 monitored+enabled sources x 12 buckets; 18 complete, 2 partial, 4 neither.
    assert fw["buckets"] == {"expected": 24, "complete": 18, "partial": 2, "missing": 4}
    assert fw["events"] == 12 * 3000 + 6 * 1500
    assert fw["mean_eps"] == pytest.approx(10.0 + 5.0)
    assert fw["expected_eps"] == pytest.approx(9.0)
    assert fw["baseline_sources"] == 1 and fw["baseline_share"] == pytest.approx(0.5)
    assert fw["open_incidents"] == {"VOLUME_DROP": 2}
    assert [t["name"] for t in fw["top_sources"]] == ["fw-a", "fw-b"]
    assert [t["events"] for t in fw["top_sources"]] == [36000, 9000]

    ips = classes["IPS"]
    assert ips["sources"] == {"enabled": 1, "disabled": 0}
    assert ips["buckets"] == {"expected": 12, "complete": 0, "partial": 0, "missing": 12}
    assert ips["events"] is None and ips["mean_eps"] is None
    assert ips["expected_eps"] is None and ips["baseline_share"] == 0.0
    assert ips["open_incidents"] == {}  # the only incident is suppressed
    assert ips["top_sources"] == []

    waf = classes["WAF"]
    assert waf["buckets"] == {"expected": 12, "complete": 0, "partial": 1, "missing": 11}
    assert waf["events"] is None and waf["mean_eps"] is None  # partial is not a measurement

    dns = classes["DNS"]  # no sources at all: nothing to measure, not a zero
    assert dns["sources"] == {"enabled": 0, "disabled": 0}
    assert dns["events"] is None and dns["baseline_share"] is None
    assert dns["buckets"]["expected"] == 0

    assert body["internal"] == {"sources": {"enabled": 1, "disabled": 0}}
    assert body["new_sources"] == 1
    assert body["conflict_sources"] == 1
    # The source with no classification row is UNCLASSIFIED, not dropped.
    assert body["unclassified_sources"] == 1
    assert classes["UNCLASSIFIED"]["sources"]["enabled"] == 1


def test_summary_window_sizes_and_validation(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=1, name="fw-a")
        s.add(classification(src, "FIREWALL"))
        await s.flush()
        await add_metric(s, src, CLOSED - timedelta(days=2), event_count=600)

    run(setup)
    client = Client(VIEWER)
    day = by_class(client.get("/api/v1/fleet/summary?window=24h").json())["FIREWALL"]
    week = by_class(client.get("/api/v1/fleet/summary?window=7d").json())["FIREWALL"]
    assert day["buckets"]["expected"] == 288 and day["events"] is None
    assert week["buckets"]["expected"] == 2016 and week["events"] == 600
    assert client.get("/api/v1/fleet/summary").json()["window"] == "24h"
    assert client.get("/api/v1/fleet/summary?window=2h").status_code == 422


def test_summary_reports_the_class_outage_mode_and_counts_source_overrides(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        fw_a = await make_log_source(s, inst, qradar_id=1, name="fw-a")
        fw_b = await make_log_source(s, inst, qradar_id=2, name="fw-b")
        ips = await make_log_source(s, inst, qradar_id=3, name="ips-a")
        s.add_all([
            classification(fw_a, "FIREWALL"),
            classification(fw_b, "FIREWALL"),
            classification(ips, "IPS"),
        ])
        s.add_all([
            OutageAlertPolicy(scope_kind="CLASS", scope_value="FIREWALL",
                              mode="RECORD_ONLY"),
            # A source override: counts against its class, but the class mode
            # stays what the class row (or the default) says.
            OutageAlertPolicy(scope_kind="SOURCE", scope_value=str(fw_a.id),
                              mode="ALERT"),
            OutageAlertPolicy(scope_kind="SOURCE", scope_value=str(ips.id),
                              mode="RECORD_ONLY"),
        ])
        await s.flush()

    run(setup)
    classes = by_class(Client(VIEWER).get("/api/v1/fleet/summary?window=1h").json())

    # The class row decides FIREWALL as a whole, ignoring its ALERT override.
    assert classes["FIREWALL"]["outage_mode"] == "RECORD_ONLY"
    assert classes["FIREWALL"]["outage_overrides"] == 1
    # No class row for IPS: its only row is a source override, which the class
    # mode ignores, so the default applies; the override is still counted.
    assert classes["IPS"]["outage_mode"] == "ALERT"
    assert classes["IPS"]["outage_overrides"] == 1
    # No rows at all: the default, and nothing overridden.
    assert classes["DNS"]["outage_mode"] == "ALERT"
    assert classes["DNS"]["outage_overrides"] == 0


@pytest.mark.parametrize("window", ["1h", "7d"])
def test_summary_sql_is_constant_in_fleet_size(db_schema, window) -> None:
    counts = []
    for size in (10, 400):
        async def setup(s, size=size):  # type: ignore[no-untyped-def]
            await s.execute(LogSourceClassification.__table__.delete())
            inst = await make_instance(s)
            classes = [c.value for c in SourceClass]
            sources = []
            for i in range(size):
                sources.append(await make_log_source(
                    s, inst, qradar_id=100_000 * size + i, name=f"src-{size}-{i}"))
            s.add_all(
                classification(src, classes[i % len(classes)]) for i, src in enumerate(sources)
            )
            await s.flush()
            for src in sources:
                await add_metric(s, src, _bucket(1), event_count=300)
            s.add_all(
                LogSourceBaseline(
                    log_source_id=src.id, metric_name="average_eps", weekday=2, hour=10,
                    median=1.0, mad=0.1, sample_count=30, is_reliable=True,
                    observations=[1.0])
                for src in sources
            )
            s.add_all(
                LogSourceAnomaly(
                    log_source_id=src.id, anomaly_type=AnomalyType.VOLUME_DROP,
                    severity=Severity.LOW, detected_at=CLOSED, state=AnomalyState.OPEN)
                for src in sources
            )

        run(setup)
        client = Client(VIEWER)
        resp = client.get(f"/api/v1/fleet/summary?window={window}")
        assert resp.status_code == 200
        counts.append(client.statements)
    assert counts[0] == counts[1], counts
    assert counts[0] <= 6


# ------------------------------------------------------------------------ sources


def _list_fixture(db_schema):  # type: ignore[no-untyped-def]
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        names = ["a-fw", "b-fw", "c-ips", "d-dns", "e-none"]
        srcs = [
            await make_log_source(s, inst, qradar_id=i, name=n, auto_discovered=(i == 3))
            for i, n in enumerate(names, 1)
        ]
        conflict = [{"basis": "NAME_REGEX", "rule_id": "r", "class": "IPS"}]
        s.add_all([
            classification(srcs[0], "FIREWALL"),
            classification(srcs[1], "FIREWALL", review="NEW", conflicts=conflict),
            classification(srcs[2], "IPS", basis="TYPE_NAME_REGEX", review="NEW"),
            classification(srcs[3], "DNS", basis="OVERRIDE"),
        ])
        return [str(x.id) for x in srcs]

    return run(setup)


def test_sources_list_filters_and_pagination(db_schema) -> None:
    ids = _list_fixture(db_schema)
    client = Client(VIEWER)

    def names(query: str) -> list[str]:
        resp = client.get(f"/api/v1/fleet/sources{query}")
        assert resp.status_code == 200, resp.text
        return [i["name"] for i in resp.json()["items"]]

    assert names("") == ["a-fw", "b-fw", "c-ips", "d-dns", "e-none"]
    assert names("?class=FIREWALL") == ["a-fw", "b-fw"]
    assert names("?class=UNCLASSIFIED") == ["e-none"]
    assert names("?review_status=NEW") == ["b-fw", "c-ips"]
    assert names("?has_conflict=true") == ["b-fw"]
    assert names("?has_conflict=false") == ["a-fw", "c-ips", "d-dns", "e-none"]
    assert names("?basis=OVERRIDE") == ["d-dns"]
    assert names("?basis=UNCLASSIFIED") == ["e-none"]
    assert names("?class=FIREWALL&review_status=NEW") == ["b-fw"]
    assert names("?class=DNS&review_status=NEW") == []

    page = client.get("/api/v1/fleet/sources?limit=2&offset=1").json()
    assert [i["name"] for i in page["items"]] == ["b-fw", "c-ips"]
    assert (page["total"], page["limit"], page["offset"]) == (5, 2, 1)

    first = client.get("/api/v1/fleet/sources?class=IPS").json()["items"][0]
    assert first["id"] == ids[2]
    assert first["auto_discovered"] is True
    assert first["basis"] == "TYPE_NAME_REGEX" and first["review_status"] == "NEW"
    assert first["first_seen_at"] and first["classified_at"]
    assert first["override_class"] is None and first["conflicts"] == []

    # A source the classifier never saw is UNCLASSIFIED with no review verdict.
    loose = client.get("/api/v1/fleet/sources?class=UNCLASSIFIED").json()["items"][0]
    assert loose["review_status"] is None and loose["classified_at"] is None

    for bad in ("?class=NOPE", "?review_status=MAYBE", "?basis=GUESS", "?limit=0",
                "?offset=-1", "?has_conflict=perhaps"):
        assert client.get(f"/api/v1/fleet/sources{bad}").status_code == 422, bad


# ---------------------------------------------------------------------- rules API

RULES = "/api/v1/classification-rules"


def _rule(**over):  # type: ignore[no-untyped-def]
    body = {"kind": "NAME_REGEX", "pattern": "^alpha-", "target_class": "WAF"}
    body.update(over)
    return body


def _instance_with_sources(count: int = 1, prefix: str = "alpha"):  # type: ignore[no-untyped-def]
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        srcs = [
            await make_log_source(s, inst, qradar_id=i, name=f"{prefix}-{i}")
            for i in range(1, count + 1)
        ]
        s.add_all(classification(x, "UNCLASSIFIED", basis="UNCLASSIFIED") for x in srcs)
        return str(inst.id), [str(x.id) for x in srcs]

    return run(setup)


def _class_of(source_id: str) -> dict:
    page = Client(VIEWER).get("/api/v1/fleet/sources?limit=1000").json()
    return next(i for i in page["items"] if i["id"] == source_id)


def test_rule_create_audits_and_reclassifies_the_instance(db_schema) -> None:
    instance_id, (source_id,) = _instance_with_sources()
    assert _class_of(source_id)["source_class"] == "UNCLASSIFIED"

    admin = Client(RULE_ADMIN)
    resp = admin.post(RULES, json=_rule(instance_id=instance_id, description="alpha hosts"))
    assert resp.status_code == 201, resp.text
    rule = resp.json()
    assert rule["built_in"] is False and rule["created_by"] == "rule-admin"
    assert rule["enabled"] is True and rule["priority"] == 100

    after = _class_of(source_id)
    assert after["source_class"] == "WAF"
    assert after["basis"] == "NAME_REGEX" and after["rule_id"] == rule["id"]
    rows = audit_rows("classification_rule.create")
    assert len(rows) == 1 and rows[0].actor == "rule-admin"
    assert rows[0].object_id == rule["id"] and rows[0].detail["pattern"] == "^alpha-"

    # Classification also seeds the built-in rules; the operator's own is one of many.
    everything = Client(VIEWER).get(RULES).json()
    assert rule["id"] in {r["id"] for r in everything}
    assert any(r["built_in"] for r in everything)
    custom = Client(VIEWER).get(f"{RULES}?built_in=false").json()
    assert [r["id"] for r in custom] == [rule["id"]]
    assert Client(VIEWER).get(f"{RULES}?built_in=false&enabled=false").json() == []


def test_global_rule_reclassifies_every_instance(db_schema) -> None:
    _, (first,) = _instance_with_sources()
    _, (second,) = _instance_with_sources()
    assert Client(ADMIN).post(RULES, json=_rule()).status_code == 201
    assert _class_of(first)["source_class"] == "WAF"
    assert _class_of(second)["source_class"] == "WAF"


def test_rule_validation_and_duplicates(db_schema) -> None:
    instance_id, _ = _instance_with_sources()
    admin = Client(ADMIN)
    assert admin.post(RULES, json=_rule(pattern="([unclosed")).status_code == 422
    assert admin.post(RULES, json=_rule(pattern="a" * 257)).status_code == 422
    assert admin.post(RULES, json=_rule(pattern="")).status_code == 422
    assert admin.post(RULES, json=_rule(kind="GROUP", pattern="dmz")).status_code == 422
    assert admin.post(RULES, json=_rule(kind="NOPE")).status_code == 422
    assert admin.post(RULES, json=_rule(target_class="UNCLASSIFIED")).status_code == 422
    assert admin.post(RULES, json=_rule(instance_id=MISSING_ID)).status_code == 422
    assert admin.post(RULES, json=_rule(surprise=1)).status_code == 422
    assert admin.post(RULES, json=_rule(pattern="a" * 256)).status_code == 201
    assert admin.post(RULES, json=_rule(kind="GROUP", pattern="42")).status_code == 201

    assert admin.post(RULES, json=_rule(instance_id=instance_id)).status_code == 201
    assert admin.post(RULES, json=_rule(instance_id=instance_id)).status_code == 409
    # Same pattern for a different scope is a different rule (global vs instance).
    assert admin.post(RULES, json=_rule()).status_code == 201
    assert admin.post(RULES, json=_rule()).status_code == 409
    assert len(audit_rows("classification_rule.create")) == 4


def test_custom_rule_update_and_delete_reclassify_and_audit(db_schema) -> None:
    instance_id, (source_id,) = _instance_with_sources()
    admin = Client(ADMIN)
    rule_id = admin.post(RULES, json=_rule(instance_id=instance_id)).json()["id"]
    assert _class_of(source_id)["source_class"] == "WAF"

    resp = admin.patch(f"{RULES}/{rule_id}", json={"target_class": "DNS", "priority": 200})
    assert resp.status_code == 200 and resp.json()["target_class"] == "DNS"
    assert _class_of(source_id)["source_class"] == "DNS"
    change = audit_rows("classification_rule.update")[0].detail["changes"]
    assert change["target_class"] == {"from": "WAF", "to": "DNS"}
    assert change["priority"] == {"from": 100, "to": 200}

    assert admin.patch(f"{RULES}/{rule_id}", json={"enabled": False}).status_code == 200
    assert _class_of(source_id)["source_class"] == "UNCLASSIFIED"
    assert admin.patch(f"{RULES}/{rule_id}", json={"enabled": True}).status_code == 200
    assert _class_of(source_id)["source_class"] == "DNS"

    assert admin.patch(f"{RULES}/{rule_id}", json={"pattern": "(bad"}).status_code == 422
    assert admin.patch(f"{RULES}/{rule_id}", json={"pattern": None}).status_code == 422
    assert admin.patch(f"{RULES}/{rule_id}", json={"enabled": None}).status_code == 422
    assert admin.patch(f"{RULES}/{rule_id}", json={"kind": "TYPE"}).status_code == 422
    assert admin.patch(
        f"{RULES}/{rule_id}", json={"target_class": "UNCLASSIFIED"}
    ).status_code == 422
    assert _class_of(source_id)["source_class"] == "DNS"

    other = admin.post(RULES, json=_rule(instance_id=instance_id, pattern="^beta-")).json()
    assert admin.patch(f"{RULES}/{other['id']}", json={"pattern": "^alpha-"}).status_code == 409

    assert admin.delete(f"{RULES}/{rule_id}").status_code == 204
    assert _class_of(source_id)["source_class"] == "UNCLASSIFIED"
    assert audit_rows("classification_rule.delete")[0].object_id == rule_id
    assert admin.delete(f"{RULES}/{rule_id}").status_code == 404
    assert admin.patch(f"{RULES}/{rule_id}", json={"enabled": False}).status_code == 404


def test_built_in_rule_can_only_be_disabled(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        await ensure_seed_rules(s)
        return str(await s.scalar(
            select(ClassificationRule.id).where(ClassificationRule.built_in.is_(True)).limit(1)
        ))

    built_in = run(setup)
    admin = Client(ADMIN)
    for body in ({"pattern": "x"}, {"target_class": "OTHER"}, {"priority": 1},
                 {"description": "x"}, {"enabled": False, "priority": 1}):
        assert admin.patch(f"{RULES}/{built_in}", json=body).status_code == 409, body
    assert admin.delete(f"{RULES}/{built_in}").status_code == 409

    resp = admin.patch(f"{RULES}/{built_in}", json={"enabled": False})
    assert resp.status_code == 200 and resp.json()["enabled"] is False
    # Nothing but `enabled` moved, and the refused attempts left no audit rows.
    assert len(audit_rows("classification_rule.update")) == 1
    assert audit_rows("classification_rule.delete") == []
    assert Client(VIEWER).get(f"{RULES}?built_in=true&enabled=false").json()[0]["id"] == built_in


def test_rule_change_sql_is_constant_in_fleet_size(db_schema) -> None:
    counts = []
    for size in (5, 200):
        instance_id, _ = _instance_with_sources(size, prefix=f"size{size}")
        client = Client(ADMIN)
        resp = client.post(RULES, json=_rule(instance_id=instance_id, pattern=f"^size{size}-"))
        assert resp.status_code == 201
        counts.append(client.statements)
    assert counts[0] == counts[1], counts


# ------------------------------------------------------------------------ override


def test_override_set_and_clear_reclassify_that_source_and_audit(db_schema) -> None:
    instance_id, (target, bystander) = _instance_with_sources(2)
    admin = Client(ADMIN)
    assert admin.post(RULES, json=_rule(instance_id=instance_id)).status_code == 201
    assert _class_of(target)["source_class"] == "WAF"

    editor = Client(EDITOR)
    url = f"/api/v1/log-sources/{target}/classification"
    resp = editor.patch(url, json={"override_class": "FIREWALL"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source_class"] == "FIREWALL" and body["basis"] == "OVERRIDE"
    assert body["override_class"] == "FIREWALL" and body["override_by"] == "editor"
    assert body["override_at"] is not None
    assert _class_of(bystander)["source_class"] == "WAF"  # only that source moved

    rows = audit_rows("log_source.classification_override")
    assert len(rows) == 1
    assert rows[0].actor == "editor" and rows[0].object_id == target
    assert rows[0].detail == {"from": None, "to": "FIREWALL", "class": "FIREWALL"}

    # A rule change must not erase the override.
    assert admin.post(RULES, json=_rule(instance_id=instance_id, pattern="^al")).status_code == 201
    assert _class_of(target)["override_class"] == "FIREWALL"

    cleared = editor.patch(url, json={"override_class": None}).json()
    assert cleared["override_class"] is None and cleared["override_by"] is None
    assert cleared["source_class"] == "WAF" and cleared["basis"] == "NAME_REGEX"
    assert audit_rows("log_source.classification_override")[1].detail["from"] == "FIREWALL"


def test_override_errors_and_review_state_untouched(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=1, name="alpha-1")
        s.add(classification(src, "OTHER", review="NEW", basis="UNCLASSIFIED"))
        return str(src.id)

    source_id = run(setup)
    editor = Client(EDITOR)
    url = f"/api/v1/log-sources/{source_id}/classification"
    assert editor.patch(
        f"/api/v1/log-sources/{MISSING_ID}/classification", json={"override_class": "DNS"}
    ).status_code == 404
    assert editor.patch(url, json={"override_class": "NOT_A_CLASS"}).status_code == 422
    assert editor.patch(url, json={}).status_code == 422  # omitted is not "clear"
    assert editor.patch(url, json={"override_class": "DNS", "x": 1}).status_code == 422
    assert audit_rows("log_source.classification_override") == []

    body = editor.patch(url, json={"override_class": "DNS"}).json()
    assert body["review_status"] == "NEW" and body["reviewed_at"] is None


def test_override_on_a_source_without_a_classification_row_creates_it(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        return str((await make_log_source(s, inst, qradar_id=1, name="bare")).id)

    source_id = run(setup)
    resp = Client(EDITOR).patch(
        f"/api/v1/log-sources/{source_id}/classification", json={"override_class": "VPN"}
    )
    assert resp.status_code == 200 and resp.json()["source_class"] == "VPN"


# -------------------------------------------------------------------------- review


def _new_source_with_alert(inst, name: str, qradar_id: int, s):  # type: ignore[no-untyped-def]
    async def go():  # type: ignore[no-untyped-def]
        src = await make_log_source(s, inst, qradar_id=qradar_id, name=name)
        s.add(classification(src, "OTHER", review="NEW", basis="UNCLASSIFIED"))
        await s.flush()
        await AlertService(s).open_or_update(AlertInput(
            fingerprint=inventory_fingerprint(inst.id, qradar_id),
            title=f"New log source: {name}", severity=Severity.LOW,
            source_type="log_source", source_id=src.id,
            context={"source_class": "OTHER"},
        ))
        return str(src.id)

    return go()


def test_review_resolves_exactly_that_sources_alert_and_is_idempotent(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        one = await _new_source_with_alert(inst, "new-one", 1, s)
        two = await _new_source_with_alert(inst, "new-two", 2, s)
        return str(inst.id), one, two

    instance_id, one, two = run(setup)

    async def alerts(s):  # type: ignore[no-untyped-def]
        rows = (await s.scalars(select(Alert).order_by(Alert.title))).all()
        return {a.title: (a.status, a.resolved_by, a.resolved_at) for a in rows}

    # Classifying does not resolve it (the deliberate act is reviewing).
    Client(EDITOR).patch(
        f"/api/v1/log-sources/{one}/classification", json={"override_class": "LINUX"}
    )
    assert run(alerts)["New log source: new-one"][0] == AlertStatus.OPEN

    editor = Client(EDITOR)
    resp = editor.post(f"/api/v1/log-sources/{one}/review")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["review_status"] == "REVIEWED" and body["reviewed_by"] == "editor"
    assert body["reviewed_at"] is not None and body["source_class"] == "LINUX"

    state = run(alerts)
    assert state["New log source: new-one"][0] == AlertStatus.RESOLVED
    assert state["New log source: new-one"][1] == "editor"
    assert state["New log source: new-two"][0] == AlertStatus.OPEN
    assert len(audit_rows("log_source.review")) == 1
    assert audit_rows("log_source.review")[0].detail == {"alert_resolved": True}

    again = editor.post(f"/api/v1/log-sources/{one}/review")
    assert again.status_code == 200
    assert again.json()["reviewed_at"] == body["reviewed_at"]
    assert run(alerts) == state
    assert len(audit_rows("log_source.review")) == 1

    # The sources' own inventory alert is not recreated by anything here.
    assert instance_id and two


def test_review_without_an_open_alert_and_errors(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=1, name="quiet")
        s.add(classification(src, "OTHER", review="NEW", basis="UNCLASSIFIED"))
        bare = await make_log_source(s, inst, qradar_id=2, name="bare")
        return str(src.id), str(bare.id)

    quiet, bare = run(setup)
    editor = Client(EDITOR)
    assert editor.post(f"/api/v1/log-sources/{MISSING_ID}/review").status_code == 404
    assert editor.post("/api/v1/log-sources/not-a-uuid/review").status_code == 422

    resp = editor.post(f"/api/v1/log-sources/{quiet}/review")
    assert resp.status_code == 200 and resp.json()["review_status"] == "REVIEWED"
    assert audit_rows("log_source.review")[0].detail == {"alert_resolved": False}

    # A source with no classification row is classified and left REVIEWED.
    resp = editor.post(f"/api/v1/log-sources/{bare}/review")
    assert resp.status_code == 200 and resp.json()["review_status"] == "REVIEWED"

    async def count(s):  # type: ignore[no-untyped-def]
        return await s.scalar(select(func.count()).select_from(Alert))

    assert run(count) == 0


def test_expected_eps_averages_every_cell_the_window_spans(db_schema) -> None:
    # The 24h window ending 2026-10-13 10:05 UTC spans Mon 11..23 and Tue 0..10.
    spanned = [(1, h) for h in range(11, 24)] + [(2, h) for h in range(0, 11)]
    assert len(spanned) == 24

    def baselines(src, medians, *, skip=None, unreliable=None):  # type: ignore[no-untyped-def]
        return [
            LogSourceBaseline(
                log_source_id=src.id, metric_name="average_eps", weekday=cell[0],
                hour=cell[1], median=m, mad=1.0, sample_count=30,
                is_reliable=cell != unreliable, observations=[m],
            )
            for cell, m in zip(spanned, medians, strict=True)
            if cell != skip
        ]

    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        full = await make_log_source(s, inst, qradar_id=1, name="fw-full")
        missing = await make_log_source(s, inst, qradar_id=2, name="fw-missing-cell")
        shaky = await make_log_source(s, inst, qradar_id=3, name="fw-unreliable-cell")
        none = await make_log_source(s, inst, qradar_id=4, name="fw-none")
        s.add_all(classification(x, "FIREWALL") for x in (full, missing, shaky, none))
        await s.flush()
        medians = [float(i) for i in range(24)]  # mean 11.5; differs per cell
        s.add_all(baselines(full, medians))
        s.add_all(baselines(missing, [1000.0] * 24, skip=spanned[5]))
        s.add_all(baselines(shaky, [1000.0] * 24, unreliable=spanned[7]))
        # A cell outside the window must not enter the mean.
        s.add(LogSourceBaseline(
            log_source_id=full.id, metric_name="average_eps", weekday=3, hour=3,
            median=9999.0, mad=1.0, sample_count=30, is_reliable=True, observations=[1.0]))

    run(setup)
    client = Client(VIEWER)
    day = by_class(client.get("/api/v1/fleet/summary?window=24h").json())["FIREWALL"]
    assert day["expected_eps"] == pytest.approx(11.5)
    assert day["baseline_sources"] == 1
    assert day["baseline_share"] == pytest.approx(0.25)  # 1 of 4; never counted as 0

    # 1h spans one cell (Tue 10, median 23): the three 24-cell sources differ there.
    hour = by_class(client.get("/api/v1/fleet/summary?window=1h").json())["FIREWALL"]
    assert hour["expected_eps"] == pytest.approx(23.0 + 1000.0 + 1000.0)
    assert hour["baseline_sources"] == 3

    # 7d needs all 168 cells; nobody has them, so no expectation at all.
    week = by_class(client.get("/api/v1/fleet/summary?window=7d").json())["FIREWALL"]
    assert week["expected_eps"] is None and week["baseline_share"] == 0.0
