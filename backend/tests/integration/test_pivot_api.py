"""The pivot routes: AQL text and console links for every family, built from stored rows."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core.database import get_session
from app.main import create_app
from app.models.alert import Alert
from app.models.enums import (
    AlertStatus,
    AnomalyState,
    AnomalyType,
    EvidenceStatus,
    IpsAnomalyType,
    IpsDimension,
    Severity,
    WafAnomalyType,
    WafProfileValidationStatus,
)
from app.models.ips import IpsAnomaly, IpsEntity, IpsSourceProfile
from app.models.log_source import LogSourceAnomaly
from app.models.offense import OffenseSnapshot
from app.models.property_watch import PropertyWatch, PwAnomaly
from app.models.waf import WafAnomaly, WafEndpoint, WafSourceProfile
from app.security.auth import get_principal
from app.security.rbac import Principal
from tests.integration.factories import make_instance, make_log_source

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the pivot API tests",
    ),
]

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
NOBODY = Principal(subject="nobody", permissions=frozenset())
START = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
END = START + timedelta(minutes=30)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)


def _client(session, principal=READER) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    async def _session():  # type: ignore[no-untyped-def]
        yield session

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: principal
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def _world(session, *, host="https://qr.lab.example", kind="rest"):  # type: ignore[no-untyped-def]
    instance = await make_instance(session)
    instance.console_host = host
    instance.provider_kind = kind
    source = await make_log_source(session, instance, qradar_id=77, name="src-77")
    return instance, source


def _phase_a(source_id, **kw) -> LogSourceAnomaly:  # type: ignore[no-untyped-def]
    defaults = dict(
        log_source_id=source_id, anomaly_type=AnomalyType.VOLUME_SPIKE, severity=Severity.HIGH,
        state=AnomalyState.RESOLVED, detected_at=END, anomaly_start=START, anomaly_end=END,
        evidence_status=EvidenceStatus.NOT_REQUESTED,
    )
    defaults.update(kw)
    return LogSourceAnomaly(**defaults)


def _waf_endpoint(instance_id, source_id, path="/api/users/{id}", host=None):  # type: ignore[no-untyped-def]
    return WafEndpoint(
        instance_id=instance_id, log_source_id=source_id, virtual_host=host,
        normalized_endpoint=path, http_method="POST", normalizer_version=1,
        first_seen_at=START, last_seen_at=END, tracked_since=START, is_active=True,
    )


def _waf_anomaly(endpoint_id, **kw) -> WafAnomaly:  # type: ignore[no-untyped-def]
    defaults = dict(
        waf_endpoint_id=endpoint_id, detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
        severity=Severity.MEDIUM, state=AnomalyState.RESOLVED, detected_at=END,
        anomaly_start=START, anomaly_end=END, evidence_status=EvidenceStatus.NOT_REQUESTED,
    )
    defaults.update(kw)
    return WafAnomaly(**defaults)


def _waf_profile(instance_id, source_id, **kw) -> WafSourceProfile:  # type: ignore[no-untyped-def]
    defaults = dict(
        instance_id=instance_id, log_source_id=source_id, enabled=True, profile_version=2,
        url_property="Request URL", method_property="Request Method", product_label="waf",
        validation_status=WafProfileValidationStatus.VALID, validated_profile_version=2,
        validated_at=START, validation_report={},
    )
    defaults.update(kw)
    return WafSourceProfile(**defaults)


def _qs(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


async def test_phase_a_anomaly_pivot(db_session) -> None:
    _, source = await _world(db_session)
    row = _phase_a(source.id)
    db_session.add(row)
    await db_session.flush()
    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/anomalies/{row.id}/pivot")
    assert resp.status_code == 200
    body = resp.json()
    assert body["aql"] == (
        'SELECT QIDNAME(qid) AS "event_name", SUM(eventcount) AS "events" FROM events '  # noqa: S608 - expected text
        f"WHERE logsourceid = 77 AND starttime >= {START_MS} AND starttime < {END_MS} "
        f"GROUP BY qid ORDER BY SUM(eventcount) DESC LIMIT 50 START {START_MS} STOP {END_MS}"
    )
    assert body["time_range"] == {"start": "2026-11-05T10:00:00Z", "stop": "2026-11-05T10:30:00Z"}
    assert body["console_url"] == body["aql_search_url"]
    assert body["console_url"].startswith("https://qr.lab.example/console/do/ariel/arielSearch?")
    assert _qs(body["console_url"])["value(aql)"] == [body["aql"]]
    assert body["console_unavailable_reason"] is None


async def test_open_anomaly_window_ends_now_and_says_so(db_session) -> None:
    _, source = await _world(db_session)
    row = _phase_a(source.id, state=AnomalyState.OPEN, anomaly_end=None, anomaly_start=None,
                   opened_at=datetime.now(UTC) - timedelta(minutes=20))
    db_session.add(row)
    await db_session.flush()
    async with _client(db_session) as client:
        body = (await client.get(f"/api/v1/anomalies/{row.id}/pivot")).json()
    assert any("still open" in n for n in body["notes"])


async def test_missing_console_gives_aql_without_a_link(db_session) -> None:
    _, source = await _world(db_session, host="mock", kind="mock")
    row = _phase_a(source.id)
    db_session.add(row)
    await db_session.flush()
    async with _client(db_session) as client:
        body = (await client.get(f"/api/v1/anomalies/{row.id}/pivot")).json()
    assert body["console_url"] is None and body["aql_search_url"] is None
    assert "mock provider" in body["console_unavailable_reason"]
    assert body["aql"].startswith("SELECT")
    assert body["console_unavailable_reason"] in body["notes"]


async def test_waf_anomaly_pivot_with_path_prefix(db_session) -> None:
    instance, source = await _world(db_session)
    endpoint = _waf_endpoint(instance.id, source.id)
    db_session.add_all([endpoint, _waf_profile(instance.id, source.id)])
    await db_session.flush()
    row = _waf_anomaly(endpoint.id)
    db_session.add(row)
    await db_session.flush()
    async with _client(db_session) as client:
        body = (await client.get(f"/api/v1/anomalies/{row.id}/pivot")).json()
    assert "\"Request Method\" = 'POST'" in body["aql"]
    assert "\"Request URL\" LIKE '/api/users/%'" in body["aql"]
    assert 'GROUP BY "Request URL"' in body["aql"]
    assert any("literal prefix" in n for n in body["notes"])


async def test_waf_value_with_a_quote_is_never_placed_in_the_query(db_session) -> None:
    instance, source = await _world(db_session)
    endpoint = _waf_endpoint(instance.id, source.id, path="/it's' OR 1=1 --")
    db_session.add_all([endpoint, _waf_profile(instance.id, source.id)])
    await db_session.flush()
    row = _waf_anomaly(endpoint.id)
    db_session.add(row)
    await db_session.flush()
    async with _client(db_session) as client:
        body = (await client.get(f"/api/v1/anomalies/{row.id}/pivot")).json()
    assert "OR 1=1" not in body["aql"] and "it's" not in body["aql"]
    assert any(n.startswith("Path filter omitted") for n in body["notes"])
    assert "LIKE" not in body["aql"]


async def test_waf_without_profile_or_host_property_notes_the_gap(db_session) -> None:
    instance, source = await _world(db_session)
    endpoint = _waf_endpoint(instance.id, source.id, path="/login", host="shop.example")
    db_session.add(endpoint)
    await db_session.flush()
    row = _waf_anomaly(endpoint.id)
    db_session.add(row)
    await db_session.flush()
    async with _client(db_session) as client:
        body = (await client.get(f"/api/v1/anomalies/{row.id}/pivot")).json()
    assert "no WAF profile" in " ".join(body["notes"])
    assert "LIKE" not in body["aql"]
    db_session.add(_waf_profile(instance.id, source.id))
    await db_session.flush()
    async with _client(db_session) as client:
        body = (await client.get(f"/api/v1/anomalies/{row.id}/pivot")).json()
    assert any("maps no host property" in n for n in body["notes"])
    assert "\"Request URL\" LIKE '/login%'" in body["aql"]


async def _ips_world(session, dimension, value_key, mapping):  # type: ignore[no-untyped-def]
    instance, source = await _world(session)
    session.add(
        IpsSourceProfile(
            instance_id=instance.id, log_source_id=source.id, enabled=True, profile_version=2,
            mapping=mapping, action_value_map={}, validation_status="VALID",
        )
    )
    entity = IpsEntity(
        instance_id=instance.id, log_source_id=source.id, dimension=dimension,
        value_key=value_key, first_seen_at=START, last_seen_at=END, active=True,
        normalizer_version=1,
    )
    session.add(entity)
    await session.flush()
    row = IpsAnomaly(
        ips_entity_id=entity.id, detector_type=IpsAnomalyType.IPS_PORT_RATE_SPIKE,
        severity=Severity.MEDIUM, state=AnomalyState.RESOLVED, detected_at=END,
        anomaly_start=START, anomaly_end=END, subject_kind="IPS_ENTITY",
        evidence_status=EvidenceStatus.NOT_REQUESTED, fingerprint=f"fp-{uuid.uuid4()}",
        profile_version=2, normalizer_version=1, observed_measurement="EXACT", details={},
    )
    session.add(row)
    await session.flush()
    return row


_IPS_MAPPING = {
    "SOURCE_IP": {"kind": "NORMALIZED", "name": "sourceip"},
    "DESTINATION_IP": {"kind": "NORMALIZED", "name": "destinationip"},
    "DESTINATION_PORT": {"kind": "NORMALIZED", "name": "destinationport"},
    "SIGNATURE": {"kind": "CUSTOM_PROPERTY", "name": "Threat Name"},
}


async def test_ips_entity_pivots(db_session) -> None:
    cases = [
        (IpsDimension.DESTINATION_PORT, "443", '"destinationport" = 443'),
        (IpsDimension.DESTINATION_IP, "10.1.2.3", "\"destinationip\" = '10.1.2.3'"),
        (IpsDimension.SIGNATURE, "Scan Probe", "\"Threat Name\" = 'Scan Probe'"),
    ]
    for dimension, key, expected in cases:
        row = await _ips_world(db_session, dimension, key, _IPS_MAPPING)
        async with _client(db_session) as client:
            body = (await client.get(f"/api/v1/anomalies/{row.id}/pivot")).json()
        assert expected in body["aql"], body["aql"]
        assert 'GROUP BY "sourceip"' in body["aql"]


async def test_ips_bad_entity_values_are_refused_with_a_note(db_session) -> None:
    for dimension, key in [
        (IpsDimension.DESTINATION_IP, "10.0.0.1' OR '1'='1"),
        (IpsDimension.DESTINATION_PORT, "443 OR 1=1"),
        (IpsDimension.SIGNATURE, "it's"),
    ]:
        row = await _ips_world(db_session, dimension, key, _IPS_MAPPING)
        async with _client(db_session) as client:
            body = (await client.get(f"/api/v1/anomalies/{row.id}/pivot")).json()
        assert " OR " not in body["aql"] and "it's" not in body["aql"]
        assert any(n.startswith("Entity filter omitted") for n in body["notes"])


async def test_ips_source_level_anomaly_has_no_entity_filter(db_session) -> None:
    _, source = await _world(db_session)
    row = IpsAnomaly(
        log_source_id=source.id, detector_type=IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE,
        severity=Severity.MEDIUM, state=AnomalyState.RESOLVED, detected_at=END,
        anomaly_start=START, anomaly_end=END, subject_kind="IPS_SOURCE",
        evidence_status=EvidenceStatus.NOT_REQUESTED, fingerprint=f"fp-{uuid.uuid4()}",
        profile_version=2, normalizer_version=1, observed_measurement="EXACT", details={},
    )
    db_session.add(row)
    await db_session.flush()
    async with _client(db_session) as client:
        body = (await client.get(f"/api/v1/anomalies/{row.id}/pivot")).json()
    assert any("source-level" in n for n in body["notes"])
    assert 'GROUP BY "sourceip"' in body["aql"]


async def _pw(session, *, properties, value_key, scope="LOG_SOURCE", **watch_kw):  # type: ignore[no-untyped-def]
    instance, source = await _world(session)
    watch = PropertyWatch(
        instance_id=instance.id, name="w", scope_kind=scope,
        log_source_id=source.id if scope == "LOG_SOURCE" else None,
        properties=properties, pinned_values=[], detectors=[], watch_version=1, enabled=True,
        **watch_kw,
    )
    session.add(watch)
    await session.flush()
    row = PwAnomaly(
        watch_id=watch.id, pw_value_id=None, value_key=value_key,
        detector_type="PW_VALUE_SPIKE", fingerprint=f"fp-{uuid.uuid4()}", severity=Severity.HIGH,
        state=AnomalyState.RESOLVED, detected_at=END, anomaly_start=START, anomaly_end=END,
        watch_version=1, consecutive_buckets=3, policy_version=1, details={},
        evidence_status="NOT_REQUESTED", observed_measurement="EXACT",
    )
    session.add(row)
    await session.flush()
    return row


async def test_pw_pivot_filters_on_the_value(db_session) -> None:
    row = await _pw(
        db_session, properties=[{"kind": "CUSTOM_PROPERTY", "name": "Country"}],
        value_key="Norway",
    )
    async with _client(db_session) as client:
        body = (await client.get(f"/api/v1/anomalies/{row.id}/pivot")).json()
    assert "logsourceid = 77" in body["aql"] and "\"Country\" = 'Norway'" in body["aql"]


async def test_pw_value_with_a_quote_and_scope_gap(db_session) -> None:
    row = await _pw(
        db_session, properties=[{"kind": "CUSTOM_PROPERTY", "name": "Country"}],
        value_key="O'Brien", scope="LOG_SOURCE_TYPE", scope_type_id=11,
    )
    async with _client(db_session) as client:
        body = (await client.get(f"/api/v1/anomalies/{row.id}/pivot")).json()
    assert "O'Brien" not in body["aql"] and "devicetype = 11" in body["aql"]
    assert any(n.startswith("Value filter omitted") for n in body["notes"])
    assert any("No filter could be built" not in n for n in body["notes"])


async def test_pw_with_no_usable_filter_says_so(db_session) -> None:
    row = await _pw(
        db_session, properties=[{"name": "x"}], value_key="v",
        scope="LOG_SOURCE_GROUP", scope_group_id=5,
    )
    async with _client(db_session) as client:
        body = (await client.get(f"/api/v1/anomalies/{row.id}/pivot")).json()
    assert any("No filter could be built" in n for n in body["notes"])
    assert any("log source group" in n for n in body["notes"])


async def test_log_source_pivot_is_the_last_hour(db_session) -> None:
    _, source = await _world(db_session)
    async with _client(db_session) as client:
        body = (await client.get(f"/api/v1/log-sources/{source.id}/pivot")).json()
    start = datetime.fromisoformat(body["time_range"]["start"].replace("Z", "+00:00"))
    stop = datetime.fromisoformat(body["time_range"]["stop"].replace("Z", "+00:00"))
    assert stop - start == timedelta(hours=1)
    assert "logsourceid = 77" in body["aql"] and "ORDER BY starttime DESC" in body["aql"]


async def test_alert_pivots_through_its_anomaly_or_its_source(db_session) -> None:
    _, source = await _world(db_session)
    anomaly = _phase_a(source.id)
    db_session.add(anomaly)
    await db_session.flush()

    def alert(**kw) -> Alert:  # type: ignore[no-untyped-def]
        fingerprint = f"fp:{uuid.uuid4()}"
        base = dict(
            dedup_key=fingerprint, fingerprint=fingerprint, title="t", severity=Severity.HIGH,
            status=AlertStatus.OPEN, source_type="log_source", source_id=source.id, context={},
            opened_at=END, first_seen_at=END, occurrence_count=1, evidence_snapshot={},
            source_anomaly_ids=[],
        )
        base.update(kw)
        return Alert(**base)

    via_anomaly = alert(source_anomaly_ids=[str(uuid.uuid4()), str(anomaly.id)])
    via_source = alert()
    none = alert(source_type="scheduled_search", source_id=uuid.uuid4())
    db_session.add_all([via_anomaly, via_source, none])
    await db_session.flush()
    async with _client(db_session) as client:
        a = await client.get(f"/api/v1/alerts/{via_anomaly.id}/pivot")
        s = await client.get(f"/api/v1/alerts/{via_source.id}/pivot")
        n = await client.get(f"/api/v1/alerts/{none.id}/pivot")
    assert a.status_code == 200 and f"START {START_MS} STOP {END_MS}" in a.json()["aql"]
    assert s.status_code == 200 and "ORDER BY starttime DESC" in s.json()["aql"]
    assert n.status_code == 404


async def test_offense_pivot_links_the_console_page(db_session) -> None:
    instance, _ = await _world(db_session)
    db_session.add(
        OffenseSnapshot(
            instance_id=instance.id, qradar_offense_id=4242, captured_at=END,
            start_time=START, last_updated_time=END, status="OPEN",
        )
    )
    await db_session.flush()
    async with _client(db_session) as client:
        resp = await client.get("/api/v1/offenses/4242/pivot")
    body = resp.json()
    assert resp.status_code == 200
    assert body["console_url"] == (
        "https://qr.lab.example/console/qradar/jsp/QRadar.jsp"
        "?appName=Sem&pageId=OffenseSummary&summaryId=4242"
    )
    assert "INOFFENSE(4242)" in body["aql"]
    assert body["aql_search_url"].startswith("https://qr.lab.example/console/do/ariel/")
    assert body["time_range"]["start"] == "2026-11-05T10:00:00Z"


async def test_unknown_ids_are_404_and_permissions_follow_the_detail_routes(db_session) -> None:
    _, source = await _world(db_session)
    row = _phase_a(source.id)
    db_session.add(row)
    await db_session.flush()
    missing = uuid.uuid4()
    async with _client(db_session) as client:
        for path in (
            f"/api/v1/anomalies/{missing}/pivot",
            f"/api/v1/alerts/{missing}/pivot",
            f"/api/v1/log-sources/{missing}/pivot",
            "/api/v1/offenses/999999/pivot",
        ):
            assert (await client.get(path)).status_code == 404, path
    async with _client(db_session, NOBODY) as client:
        for path in (
            f"/api/v1/anomalies/{row.id}/pivot",
            f"/api/v1/alerts/{missing}/pivot",
            f"/api/v1/log-sources/{source.id}/pivot",
            "/api/v1/offenses/1/pivot",
        ):
            assert (await client.get(path)).status_code == 403, path
