"""The alert list can be sliced by severity, source, family, title, time and order."""

from __future__ import annotations

import os
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import event

from app.core.database import get_session
from app.main import create_app
from app.models.alert import Alert
from app.models.catchall import CatchallSender
from app.models.custom_rule import CustomRule
from app.models.enums import (
    AlertStatus,
    CatchallReviewStatus,
    CustomRuleMetric,
    CustomRuleSubjectKind,
    IpsDimension,
    PwScopeKind,
    Severity,
)
from app.models.ips import IpsEntity
from app.models.property_watch import PropertyWatch, PwValue
from app.models.waf import WafEndpoint
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from tests.integration.factories import make_instance, make_log_source

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the alert filter API tests",
    ),
]

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
BASE = datetime(2026, 10, 23, 12, tzinfo=UTC)


def _client(session):  # type: ignore[no-untyped-def]
    async def _session():  # type: ignore[no-untyped-def]
        yield session

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: READER
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


def _alert(
    title: str,
    *,
    severity: Severity = Severity.HIGH,
    status: AlertStatus = AlertStatus.OPEN,
    source_type: str = "log_source",
    source_id: uuid.UUID | None = None,
    opened_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> Alert:
    fingerprint = f"filter:{uuid.uuid4()}"
    row = Alert(
        dedup_key=fingerprint,
        fingerprint=fingerprint,
        title=title,
        description=None,
        severity=severity,
        status=status,
        source_type=source_type,
        source_id=source_id or uuid.uuid4(),
        context={},
        opened_at=opened_at or BASE,
        first_seen_at=opened_at or BASE,
        occurrence_count=1,
        evidence_snapshot={},
        source_anomaly_ids=[],
    )
    if updated_at is not None:
        row.updated_at = updated_at
    return row


@contextmanager
def _counting(session):  # type: ignore[no-untyped-def]
    """Record every statement the engine sends while the block runs."""
    statements: list[str] = []
    engine = session.bind.sync_engine

    def record(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def _titles(client, params: dict[str, object]) -> list[str]:
    response = await client.get("/api/v1/alerts", params=params)
    assert response.status_code == 200, response.text
    return [item["title"] for item in response.json()["items"]]


async def test_each_filter_alone_and_combined(db_session) -> None:
    edge = uuid.uuid4()
    rows = [
        _alert("volume drop on edge", severity=Severity.HIGH, source_id=edge,
               opened_at=BASE),
        _alert("spike on core", severity=Severity.CRITICAL, opened_at=BASE - timedelta(hours=1)),
        _alert("waf block ratio", severity=Severity.LOW, status=AlertStatus.RESOLVED,
               source_type="waf_endpoint", opened_at=BASE - timedelta(hours=2)),
        _alert("ips burst", severity=Severity.MEDIUM, status=AlertStatus.ACKNOWLEDGED,
               source_type="ips_entity", opened_at=BASE - timedelta(hours=3)),
        _alert("watch value", severity=Severity.HIGH, source_type="pw_value",
               opened_at=BASE - timedelta(hours=4)),
        _alert("search threshold", severity=Severity.INFO, source_type="scheduled_search",
               opened_at=BASE - timedelta(hours=5)),
    ]
    db_session.add_all(rows)
    await db_session.commit()

    async with _client(db_session) as client:
        two_severities = await client.get(
            "/api/v1/alerts", params=[("severity", "HIGH"), ("severity", "LOW")]
        )
        family = await client.get("/api/v1/alerts", params={"family": "log_source"})
        waf = await client.get("/api/v1/alerts", params={"family": "waf"})
        combined = await client.get(
            "/api/v1/alerts", params={"severity": "HIGH", "family": "log_source"}
        )
        with_status = await client.get(
            "/api/v1/alerts", params={"status": "OPEN", "family": "log_source"}
        )
        searched = await client.get("/api/v1/alerts", params={"q": "volume"})
        narrowed = await client.get(
            "/api/v1/alerts", params={"q": "on", "severity": "CRITICAL"}
        )
        by_source = await client.get(
            "/api/v1/alerts", params={"log_source_id": str(edge)}
        )

    assert two_severities.status_code == 200, two_severities.text
    assert {item["title"] for item in two_severities.json()["items"]} == {
        "volume drop on edge", "watch value", "waf block ratio",
    }
    assert two_severities.json()["total"] == 3
    assert two_severities.json()["filters_applied"]["severity"] == ["HIGH", "LOW"]

    assert {item["title"] for item in family.json()["items"]} == {
        "volume drop on edge", "spike on core",
    }
    assert family.json()["filters_applied"]["family"] == "log_source"

    assert [item["title"] for item in waf.json()["items"]] == ["waf block ratio"]

    assert [item["title"] for item in combined.json()["items"]] == ["volume drop on edge"]
    assert combined.json()["total"] == 1

    assert {item["title"] for item in with_status.json()["items"]} == {
        "volume drop on edge", "spike on core",
    }

    assert [item["title"] for item in searched.json()["items"]] == ["volume drop on edge"]

    assert [item["title"] for item in narrowed.json()["items"]] == ["spike on core"]

    # `log_source` alerts hold the log source id in source_id.
    assert [item["title"] for item in by_source.json()["items"]] == ["volume drop on edge"]
    assert by_source.json()["filters_applied"]["log_source_id"] == [str(edge)]


async def test_a_source_filter_reaches_every_alert_family(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=900, name="edge-a")
    other = await make_log_source(db_session, instance, qradar_id=901, name="edge-b")

    endpoint = WafEndpoint(
        instance_id=instance.id,
        log_source_id=source.id,
        virtual_host="portal.example",
        normalized_endpoint="/login/{id}",
        http_method="POST",
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        first_seen_at=BASE - timedelta(days=30),
        last_seen_at=BASE,
        tracked_since=BASE - timedelta(days=30),
        is_active=True,
    )
    entity = IpsEntity(
        instance_id=instance.id,
        log_source_id=other.id,
        dimension=IpsDimension.SIGNATURE,
        value_key="sig-1",
        first_seen_at=BASE,
        last_seen_at=BASE,
        active=True,
    )
    sender = CatchallSender(
        instance_id=instance.id,
        catchall_log_source_id=source.id,
        identifier="sender-1",
        first_seen_at=BASE,
        last_seen_at=BASE,
        review_status=CatchallReviewStatus.NEW,
    )
    watch = PropertyWatch(
        instance_id=instance.id,
        name="watch-b",
        scope_kind=PwScopeKind.LOG_SOURCE,
        log_source_id=other.id,
        properties=[{"kind": "NORMALIZED", "name": "username"}],
        pinned_values=[],
        detectors=[],
        severity=Severity.MEDIUM,
    )
    db_session.add_all([endpoint, entity, sender, watch])
    await db_session.flush()
    value = PwValue(watch_id=watch.id, watch_version=1, value_key="alice", pinned=True)
    db_session.add(value)
    await db_session.flush()
    rule = CustomRule(
        name="rule-a",
        subject_kind=CustomRuleSubjectKind.LOG_SOURCE,
        log_source_id=source.id,
        metric=CustomRuleMetric.EVENT_COUNT,
        operator="GT",
        threshold_value=10.0,
        severity=Severity.HIGH,
    )
    rule_on_value = CustomRule(
        name="rule-b",
        subject_kind=CustomRuleSubjectKind.PW_VALUE,
        pw_value_id=value.id,
        metric=CustomRuleMetric.PW_VALUE_EVENTS,
        operator="GT",
        threshold_value=1.0,
        severity=Severity.LOW,
    )
    db_session.add_all([rule, rule_on_value])
    await db_session.flush()

    titles = {
        "direct": _alert("direct source", source_id=source.id),
        "ips_source": _alert("ips source", source_type="ips_source", source_id=source.id),
        "waf": _alert("waf endpoint", source_type="waf_endpoint", source_id=endpoint.id),
        "entity": _alert("ips entity", source_type="ips_entity", source_id=entity.id),
        "sender": _alert("catchall sender", source_type="catchall_sender", source_id=sender.id),
        "value": _alert("watch value", source_type="pw_value", source_id=value.id),
        "rule_a": _alert("rule on a", source_type="custom_rule", source_id=rule.id),
        "rule_b": _alert(
            "rule on b", source_type="custom_rule", source_id=rule_on_value.id
        ),
        "search": _alert("search threshold", source_type="scheduled_search"),
    }
    db_session.add_all(titles.values())
    await db_session.commit()

    async with _client(db_session) as client:
        only_a = await _titles(client, {"log_source_id": str(source.id), "limit": 500})
        both = await client.get(
            "/api/v1/alerts",
            params=[("log_source_id", str(source.id)), ("log_source_id", str(other.id)),
                    ("limit", 500)],
        )

    assert set(only_a) == {
        "direct source", "ips source", "waf endpoint", "catchall sender", "rule on a",
    }
    assert both.status_code == 200, both.text
    assert {item["title"] for item in both.json()["items"]} == {
        "direct source", "ips source", "waf endpoint", "catchall sender", "rule on a",
        "ips entity", "watch value", "rule on b",
    }
    assert both.json()["filters_applied"]["log_source_id"] == [
        str(source.id), str(other.id)
    ]


async def test_q_is_a_literal_case_insensitive_substring(db_session) -> None:
    db_session.add_all(
        [
            _alert("CPU 100% on edge"),
            _alert("CPU 100x on edge"),
            _alert("a_b on edge"),
            _alert("axb on edge"),
            _alert("a\\b on edge"),
            _alert("something else"),
        ]
    )
    await db_session.commit()

    async with _client(db_session) as client:
        percent = await _titles(client, {"q": "100%"})
        underscore = await _titles(client, {"q": "a_b"})
        backslash = await _titles(client, {"q": "a\\b"})
        folded = await _titles(client, {"q": "cpu 100X"})

    assert percent == ["CPU 100% on edge"]
    assert underscore == ["a_b on edge"]
    assert backslash == ["a\\b on edge"]
    assert folded == ["CPU 100x on edge"]


async def test_the_time_filter_is_half_open_on_the_creation_time(db_session) -> None:
    db_session.add_all(
        [
            _alert("noon", opened_at=BASE),
            _alert("eleven", opened_at=BASE - timedelta(hours=1)),
            _alert("ten", opened_at=BASE - timedelta(hours=2)),
        ]
    )
    await db_session.commit()
    since = (BASE - timedelta(hours=2)).isoformat()
    until = BASE.isoformat()

    async with _client(db_session) as client:
        windowed = await client.get(
            "/api/v1/alerts", params={"since": since, "until": until}
        )
        only_until = await client.get("/api/v1/alerts", params={"until": until})
        too_wide = await client.get(
            "/api/v1/alerts",
            params={
                "since": (BASE - timedelta(days=400)).isoformat(),
                "until": until,
            },
        )
        inverted = await client.get(
            "/api/v1/alerts",
            params={"since": BASE.isoformat(), "until": since},
        )

    assert windowed.status_code == 200, windowed.text
    # `since` is inclusive and `until` exclusive, so the noon row is out.
    assert {item["title"] for item in windowed.json()["items"]} == {"eleven", "ten"}
    echoed = windowed.json()["filters_applied"]["since"].replace("Z", "+00:00")
    assert datetime.fromisoformat(echoed) == BASE - timedelta(hours=2)

    # Only `until` keeps the anomaly routes' one-day default window.
    assert only_until.status_code == 200, only_until.text
    assert only_until.json()["total"] == 2
    assert only_until.json()["filters_applied"]["until"] is not None

    assert too_wide.status_code == 422, too_wide.text
    assert inverted.status_code == 422, inverted.text


@pytest.mark.parametrize(
    "params",
    [
        {"severity": "NOT_A_SEVERITY"},
        {"family": "not_a_family"},
        {"sort": "random_column"},
        {"order": "sideways"},
        {"log_source_id": "not-a-uuid"},
        {"q": "x" * 101},
        {"q": "   "},
        {"q": ""},
    ],
)
async def test_unknown_filter_values_are_422(
    db_session, params: dict[str, str]
) -> None:
    async with _client(db_session) as client:
        response = await client.get("/api/v1/alerts", params=params)

    assert response.status_code == 422, response.text


async def test_sort_and_order_change_the_page_without_changing_the_total(
    db_session,
) -> None:
    db_session.add_all(
        [
            _alert("high-old", severity=Severity.HIGH, opened_at=BASE - timedelta(hours=2),
                   updated_at=BASE - timedelta(hours=10)),
            _alert("info-new", severity=Severity.INFO, opened_at=BASE,
                   updated_at=BASE - timedelta(hours=20)),
            _alert("critical-middle", severity=Severity.CRITICAL,
                   opened_at=BASE - timedelta(hours=1),
                   updated_at=BASE - timedelta(hours=1)),
        ]
    )
    await db_session.commit()

    async with _client(db_session) as client:
        by_severity = await _titles(client, {"sort": "severity"})
        least_first = await _titles(client, {"sort": "severity", "order": "asc"})
        by_update = await _titles(client, {"sort": "updated_at"})
        oldest_first = await _titles(client, {"sort": "created_at", "order": "asc"})
        paged = await client.get(
            "/api/v1/alerts", params={"sort": "severity", "limit": 1, "offset": 1}
        )

    assert by_severity == ["critical-middle", "high-old", "info-new"]
    assert least_first == ["info-new", "high-old", "critical-middle"]
    assert by_update == ["critical-middle", "high-old", "info-new"]
    assert oldest_first == ["high-old", "critical-middle", "info-new"]
    assert paged.status_code == 200, paged.text
    assert paged.json()["total"] == 3
    assert paged.json()["sort"] == "severity"
    assert paged.json()["order"] == "desc"


async def test_facets_ignore_their_own_dimension_and_total_is_the_list_total(
    db_session,
) -> None:
    db_session.add_all(
        [
            _alert("a", severity=Severity.HIGH, status=AlertStatus.OPEN),
            _alert("b", severity=Severity.HIGH, status=AlertStatus.ACKNOWLEDGED),
            _alert("c", severity=Severity.CRITICAL, status=AlertStatus.OPEN,
                   source_type="waf_endpoint"),
            _alert("d", severity=Severity.LOW, status=AlertStatus.RESOLVED,
                   source_type="ips_entity"),
            _alert("e", severity=Severity.INFO, status=AlertStatus.OPEN,
                   source_type="scheduled_search"),
        ]
    )
    await db_session.commit()

    async with _client(db_session) as client:
        listing = await client.get(
            "/api/v1/alerts", params={"family": "log_source", "limit": 500}
        )
        facets = await client.get("/api/v1/alerts/facets", params={"family": "log_source"})
        unknown = await client.get("/api/v1/alerts/facets", params={"family": "nope"})

    assert listing.status_code == 200, listing.text
    assert facets.status_code == 200, facets.text
    assert unknown.status_code == 422, unknown.text

    items = listing.json()["items"]
    expected_severity = Counter(item["severity"] for item in items)
    expected_status = Counter(item["status"] for item in items)
    body = facets.json()
    # `total` still applies every filter, so it is the list's own total.
    assert body["total"] == listing.json()["total"] == len(items) == 2
    assert {
        row["value"]: row["count"] for row in body["severity"] if row["count"]
    } == dict(expected_severity)
    assert {
        row["value"]: row["count"] for row in body["status"] if row["count"]
    } == dict(expected_status)
    # The family facet is the one that ignores the family filter: every family is
    # counted under the other filters (here there are none).
    assert {
        row["value"]: row["count"] for row in body["family"] if row["count"]
    } == {"log_source": 2, "waf": 1, "ips": 1, "scheduled_search": 1}
    assert body["filters_applied"]["family"] == "log_source"
    # Every closed value is present even at zero, in a stable order.
    assert [row["value"] for row in body["severity"]] == [
        "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO",
    ]
    # No severity or status filter was sent, so those two facets exclude nothing and
    # still cover exactly the list; a facet only parts from `total` when its own filter
    # is set (the tests below pin that).
    assert sum(row["count"] for row in body["severity"]) == body["total"]
    assert sum(row["count"] for row in body["status"]) == body["total"]


async def test_the_statement_count_is_constant_for_one_and_for_two_hundred_alerts(
    db_session,
) -> None:
    db_session.add(_alert("only", severity=Severity.HIGH))
    await db_session.commit()

    async with _client(db_session) as client:
        # Warm every lazy import and the statement cache, then measure.
        await client.get("/api/v1/alerts", params={"severity": "HIGH"})
        await client.get("/api/v1/alerts/facets", params={"severity": "HIGH"})
        with _counting(db_session) as small_list:
            await client.get("/api/v1/alerts", params={"severity": "HIGH", "limit": 500})
        with _counting(db_session) as small_facets:
            await client.get("/api/v1/alerts/facets", params={"severity": "HIGH"})

        db_session.add_all(
            [_alert(f"bulk-{index}", severity=Severity.HIGH) for index in range(199)]
        )
        await db_session.commit()

        with _counting(db_session) as large_list:
            await client.get("/api/v1/alerts", params={"severity": "HIGH", "limit": 500})
        with _counting(db_session) as large_facets:
            await client.get("/api/v1/alerts/facets", params={"severity": "HIGH"})

    assert len(small_list) == len(large_list)
    assert len(small_facets) == len(large_facets)
    # The facets are one statement; the list is a count and a page.
    assert len(small_facets) == 1
    assert len(small_list) == 2


def _counts(body: dict, facet: str) -> dict[str, int]:
    """A facet's non-zero counts: the options an analyst can add to the view."""
    return {row["value"]: row["count"] for row in body[facet] if row["count"]}


async def _facets(client, params):  # type: ignore[no-untyped-def]
    response = await client.get("/api/v1/alerts/facets", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_severity_filter_leaves_the_severity_facet_unfiltered(db_session) -> None:
    db_session.add_all(
        [
            _alert("high open", severity=Severity.HIGH, status=AlertStatus.OPEN),
            _alert("high ack", severity=Severity.HIGH, status=AlertStatus.ACKNOWLEDGED),
            _alert("critical", severity=Severity.CRITICAL, source_type="waf_endpoint"),
            _alert("medium", severity=Severity.MEDIUM, source_type="ips_entity"),
        ]
    )
    await db_session.commit()

    async with _client(db_session) as client:
        body = await _facets(client, {"severity": "HIGH"})
        listing = await client.get(
            "/api/v1/alerts", params={"severity": "HIGH", "limit": 500}
        )

    assert listing.status_code == 200, listing.text
    # The severity chips measure what choosing each one would leave, so a severity the
    # filter excludes is not drawn as a false zero.
    assert _counts(body, "severity") == {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 1}
    # `total` is still the list's total under every filter.
    assert body["total"] == listing.json()["total"] == 2
    # The other two facets keep the severity filter.
    assert _counts(body, "status") == {"OPEN": 1, "ACKNOWLEDGED": 1}
    assert _counts(body, "family") == {"log_source": 2}


async def test_two_selected_severities_leave_the_severity_facet_unfiltered(db_session) -> None:
    db_session.add_all(
        [
            _alert("high", severity=Severity.HIGH),
            _alert("low", severity=Severity.LOW, source_type="waf_endpoint"),
            _alert("medium", severity=Severity.MEDIUM, source_type="ips_entity"),
            _alert("info", severity=Severity.INFO, source_type="scheduled_search"),
        ]
    )
    await db_session.commit()

    async with _client(db_session) as client:
        body = await _facets(
            client, [("severity", "HIGH"), ("severity", "LOW")]
        )
        listing = await client.get(
            "/api/v1/alerts",
            params=[("severity", "HIGH"), ("severity", "LOW"), ("limit", 500)],
        )

    assert listing.status_code == 200, listing.text
    assert _counts(body, "severity") == {
        "HIGH": 1, "MEDIUM": 1, "LOW": 1, "INFO": 1,
    }
    assert body["total"] == listing.json()["total"] == 2
    assert _counts(body, "family") == {"log_source": 1, "waf": 1}


async def test_a_status_filter_leaves_the_status_facet_unfiltered(db_session) -> None:
    db_session.add_all(
        [
            _alert("open high", severity=Severity.HIGH, status=AlertStatus.OPEN),
            _alert("open medium", severity=Severity.MEDIUM, status=AlertStatus.OPEN),
            _alert(
                "ack critical", severity=Severity.CRITICAL,
                status=AlertStatus.ACKNOWLEDGED, source_type="waf_endpoint",
            ),
            _alert(
                "resolved low", severity=Severity.LOW,
                status=AlertStatus.RESOLVED, source_type="ips_entity",
            ),
        ]
    )
    await db_session.commit()

    async with _client(db_session) as client:
        body = await _facets(client, {"status": "OPEN"})
        listing = await client.get(
            "/api/v1/alerts", params={"status": "OPEN", "limit": 500}
        )

    assert listing.status_code == 200, listing.text
    # The status select offers every status with the count choosing it would return.
    assert _counts(body, "status") == {"OPEN": 2, "ACKNOWLEDGED": 1, "RESOLVED": 1}
    assert body["total"] == listing.json()["total"] == 2
    # The severity facet honours the status filter: only OPEN alerts are counted.
    assert _counts(body, "severity") == {"HIGH": 1, "MEDIUM": 1}


async def test_family_and_severity_each_ignore_only_their_own_filter(db_session) -> None:
    db_session.add_all(
        [
            _alert("high on a source", severity=Severity.HIGH),
            _alert("low on a source", severity=Severity.LOW),
            _alert("high waf", severity=Severity.HIGH, source_type="waf_endpoint"),
            _alert("critical waf", severity=Severity.CRITICAL, source_type="waf_endpoint"),
        ]
    )
    await db_session.commit()

    async with _client(db_session) as client:
        body = await _facets(client, {"family": "log_source", "severity": "HIGH"})
        listing = await client.get(
            "/api/v1/alerts",
            params={"family": "log_source", "severity": "HIGH", "limit": 500},
        )

    assert listing.status_code == 200, listing.text
    # The family facet ignores the family filter but keeps the severity one.
    assert _counts(body, "family") == {"log_source": 1, "waf": 1}
    # The severity facet ignores the severity filter but keeps the family one.
    assert _counts(body, "severity") == {"HIGH": 1, "LOW": 1}
    assert body["total"] == listing.json()["total"] == 1
    assert _counts(body, "status") == {"OPEN": 1}


async def test_the_other_filters_apply_to_every_facet_and_to_the_total(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=910, name="edge-a")
    other = await make_log_source(db_session, instance, qradar_id=911, name="edge-b")

    db_session.add_all(
        [
            _alert("volume drop on edge", severity=Severity.HIGH, source_id=source.id,
                   opened_at=BASE - timedelta(hours=1)),
            _alert("volume spike on edge", severity=Severity.MEDIUM,
                   status=AlertStatus.ACKNOWLEDGED,
                   source_id=source.id, opened_at=BASE - timedelta(hours=2)),
            _alert("volume drop on core", severity=Severity.CRITICAL, source_id=other.id,
                   opened_at=BASE - timedelta(hours=1)),
            _alert("waf ratio", severity=Severity.LOW, source_id=source.id,
                   opened_at=BASE - timedelta(hours=1)),
            _alert("volume drop old", severity=Severity.LOW, source_id=source.id,
                   opened_at=BASE - timedelta(hours=3)),
            # One HIGH alert outside each of the other filters, so a facet that ignored one
            # of them would count a RESOLVED row and the assertions below would see it.
            _alert("spike on edge", severity=Severity.HIGH, status=AlertStatus.RESOLVED,
                   source_id=source.id, opened_at=BASE - timedelta(hours=1)),
            _alert("volume drop ancient", severity=Severity.HIGH, status=AlertStatus.RESOLVED,
                   source_id=source.id, opened_at=BASE - timedelta(hours=10)),
            _alert("volume drop remote", severity=Severity.HIGH, status=AlertStatus.RESOLVED,
                   source_id=other.id, opened_at=BASE - timedelta(hours=1)),
        ]
    )
    await db_session.commit()
    params = {
        "log_source_id": str(source.id),
        "q": "volume",
        "since": (BASE - timedelta(hours=2)).isoformat(),
        "until": BASE.isoformat(),
        "severity": "HIGH",
    }

    async with _client(db_session) as client:
        body = await _facets(client, params)
        listing = await client.get(
            "/api/v1/alerts", params={**params, "limit": 500}
        )

    assert listing.status_code == 200, listing.text
    assert body["total"] == listing.json()["total"] == 1
    # Every facet honours the source, the search and the window: the severity facet counts
    # the two source-A "volume" rows inside it, and only the HIGH one reaches `total`.
    assert _counts(body, "severity") == {"HIGH": 1, "MEDIUM": 1}
    assert _counts(body, "status") == {"OPEN": 1}
    assert _counts(body, "family") == {"log_source": 1}


async def test_the_facets_stay_one_statement_with_every_filter_kind_applied(db_session) -> None:
    db_session.add(_alert("only", severity=Severity.HIGH))
    await db_session.commit()
    params = {
        "status": "OPEN",
        "severity": "HIGH",
        "family": "log_source",
        "log_source_id": str(uuid.uuid4()),
        "q": "only",
        "since": (BASE - timedelta(hours=1)).isoformat(),
        "until": (BASE + timedelta(hours=1)).isoformat(),
    }

    async with _client(db_session) as client:
        # Warm the statement cache, then measure the real call.
        await client.get("/api/v1/alerts/facets", params=params)
        with _counting(db_session) as statements:
            await client.get("/api/v1/alerts/facets", params=params)

    assert len(statements) == 1
