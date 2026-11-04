"""GET /anomalies/summary `active_by_family`: active anomalies of all four families.

The Phase A fields of the summary keep their meaning (the /behavior page reads them); the
per-family counts must add up to the unified list's `total` for `active_only=true`.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import (
    AnomalyState,
    IpsAnomalyType,
    Severity,
)
from app.models.ips import IpsAnomaly
from app.models.property_watch import PropertyWatch, PwAnomaly
from app.security.rbac import Principal
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_anomaly_histogram import endpoint, phase_a, waf
from tests.integration.test_fleet_api import Client, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the summary integration tests",
    ),
]

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
SUMMARY = "/api/v1/anomalies/summary"


def _ago(hours: float = 1) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


def _ips(source_id, state=AnomalyState.OPEN, when=None) -> IpsAnomaly:  # type: ignore[no-untyped-def]
    return IpsAnomaly(
        subject_kind="IPS_SOURCE", ips_entity_id=None, log_source_id=source_id,
        detector_type=IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE,
        fingerprint=f"fp-{uuid.uuid4()}", severity=Severity.MEDIUM, state=state,
        detected_at=when or _ago(), evidence_status="NOT_REQUESTED",
        profile_version=2, normalizer_version=1, observed_measurement="EXACT", details={},
    )


def _pw(watch_id, state=AnomalyState.OPEN, when=None) -> PwAnomaly:  # type: ignore[no-untyped-def]
    return PwAnomaly(
        watch_id=watch_id, pw_value_id=None, value_key="v", detector_type="PW_VALUE_SPIKE",
        fingerprint=f"fp-{uuid.uuid4()}", severity=Severity.HIGH, state=state,
        detected_at=when or _ago(), watch_version=1, consecutive_buckets=3, policy_version=1,
        details={}, evidence_status="NOT_REQUESTED", observed_measurement="EXACT",
    )


async def _world(s, *, with_resolved=True):  # type: ignore[no-untyped-def]
    inst = await make_instance(s)
    fw = await make_log_source(s, inst, qradar_id=1, name="fw-1")
    ws = await make_log_source(s, inst, qradar_id=2, name="waf-1")
    ep = endpoint(inst.id, ws.id)
    s.add(ep)
    watch = PropertyWatch(
        instance_id=inst.id, name="w-1", scope_kind="LOG_SOURCE", log_source_id=fw.id,
        properties=[{"name": "x"}], pinned_values=[], detectors=[], watch_version=1,
        enabled=True,
    )
    s.add(watch)
    await s.flush()
    rows = [
        phase_a(fw.id, _ago()),
        phase_a(fw.id, _ago(2), state=AnomalyState.CANDIDATE),
        waf(ep.id, _ago(), state=AnomalyState.RECOVERING),
        _ips(fw.id),
        _pw(watch.id),
    ]
    if with_resolved:
        rows += [
            phase_a(fw.id, _ago(), state=AnomalyState.RESOLVED),
            waf(ep.id, _ago(), state=AnomalyState.RESOLVED),
            _ips(fw.id, AnomalyState.RESOLVED),
            _pw(watch.id, AnomalyState.RESOLVED),
        ]
    s.add_all(rows)


def test_one_active_anomaly_per_family_is_counted_and_resolved_ones_are_not(db_schema) -> None:  # type: ignore[no-untyped-def]
    run(_world)
    body = Client(READER).get(SUMMARY).json()
    fam = body["active_by_family"]
    assert fam["log_source"] == {"open": 1, "candidate": 1, "recovering": 0, "total": 2}
    assert fam["waf"] == {"open": 0, "candidate": 0, "recovering": 1, "total": 1}
    assert fam["ips"] == {"open": 1, "candidate": 0, "recovering": 0, "total": 1}
    assert fam["property_watch"] == {"open": 1, "candidate": 0, "recovering": 0, "total": 1}


def test_the_family_counts_add_up_to_the_active_lists_total(db_schema) -> None:  # type: ignore[no-untyped-def]
    run(_world)
    client = Client(READER)
    fam = client.get(SUMMARY).json()["active_by_family"]
    listed = client.get("/api/v1/anomalies", params={"active_only": "true", "limit": 200}).json()
    assert sum(f["total"] for f in fam.values()) == listed["total"] == 5


def test_the_phase_a_fields_keep_their_meaning(db_schema) -> None:  # type: ignore[no-untyped-def]
    run(_world)
    body = Client(READER).get(SUMMARY).json()
    # Phase A only: the WAF, IPS and property-watch anomalies are not in these.
    assert body["open_anomalies"] == 2
    assert body["spikes"] == 2
    assert body["candidates"] == 1
    assert body["recovering"] == 0


def test_an_empty_database_measures_zero_in_every_family(db_schema) -> None:  # type: ignore[no-untyped-def]
    fam = Client(READER).get(SUMMARY).json()["active_by_family"]
    zero = {"open": 0, "candidate": 0, "recovering": 0, "total": 0}
    assert fam == {"log_source": zero, "waf": zero, "ips": zero, "property_watch": zero}


def test_an_active_anomaly_older_than_the_ceiling_is_counted(db_schema) -> None:  # type: ignore[no-untyped-def]
    """The range ceiling bounds a named window, not the active set: an incident still
    active after a year is still active, and the count must say so."""
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        fw = await make_log_source(s, inst, qradar_id=1, name="fw-1")
        s.add(phase_a(fw.id, _ago(24 * 400)))

    run(setup)
    client = Client(READER)
    fam = client.get(SUMMARY).json()["active_by_family"]
    assert fam["log_source"]["total"] == 1
    assert client.get("/api/v1/anomalies", params={"active_only": "true"}).json()["total"] == 1


def test_every_family_counts_an_old_active_anomaly_at_any_age(db_schema) -> None:  # type: ignore[no-untyped-def]
    """One active incident per family, all detected far beyond the ceiling, plus one
    resolved incident per family at the same age. Each family must count its active row
    only, and the four totals must equal the list's `total`."""
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        fw = await make_log_source(s, inst, qradar_id=1, name="fw-1")
        ws = await make_log_source(s, inst, qradar_id=2, name="waf-1")
        ep = endpoint(inst.id, ws.id)
        s.add(ep)
        watch = PropertyWatch(
            instance_id=inst.id, name="w-1", scope_kind="LOG_SOURCE", log_source_id=fw.id,
            properties=[{"name": "x"}], pinned_values=[], detectors=[], watch_version=1,
            enabled=True,
        )
        s.add(watch)
        await s.flush()
        old = _ago(24 * 400)
        s.add_all([
            phase_a(fw.id, old),
            waf(ep.id, old),
            _ips(fw.id, when=old),
            _pw(watch.id, when=old),
            phase_a(fw.id, old, state=AnomalyState.RESOLVED),
            waf(ep.id, old, state=AnomalyState.RESOLVED),
            _ips(fw.id, AnomalyState.RESOLVED, when=old),
            _pw(watch.id, AnomalyState.RESOLVED, when=old),
        ])

    run(setup)
    client = Client(READER)
    body = client.get(SUMMARY).json()
    fam = body["active_by_family"]
    assert {name: counts["total"] for name, counts in fam.items()} == {
        "log_source": 1,
        "waf": 1,
        "ips": 1,
        "property_watch": 1,
    }
    listed = client.get(
        "/api/v1/anomalies", params={"active_only": "true", "limit": 200}
    ).json()
    assert sum(counts["total"] for counts in fam.values()) == listed["total"] == 4
    assert {item["state"] for item in listed["items"]} <= {
        "OPEN", "CANDIDATE", "RECOVERING",
    }


def test_the_legacy_fields_and_the_log_source_family_count_one_population(db_schema) -> None:  # type: ignore[no-untyped-def]
    """Legacy fields count Phase A of any age; the family count must be the same rows."""
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        fw = await make_log_source(s, inst, qradar_id=1, name="fw-1")
        s.add_all([
            phase_a(fw.id, _ago(24 * 400)),
            phase_a(fw.id, _ago(2), state=AnomalyState.CANDIDATE),
            phase_a(fw.id, _ago(1)),
            phase_a(fw.id, _ago(24 * 400), state=AnomalyState.RESOLVED),
        ])

    run(setup)
    body = Client(READER).get(SUMMARY).json()
    assert body["open_anomalies"] == 3
    assert body["active_by_family"]["log_source"]["total"] == body["open_anomalies"]


def test_more_anomalies_cost_no_more_statements(db_schema) -> None:  # type: ignore[no-untyped-def]
    run(_world)
    before = Client(READER)
    before.get(SUMMARY)

    async def more(s):  # type: ignore[no-untyped-def]
        from sqlalchemy import select

        from app.models.log_source import LogSource

        src = (await s.scalars(select(LogSource))).first()
        watch = (await s.scalars(select(PropertyWatch))).first()
        s.add_all([phase_a(src.id, _ago(3)), _ips(src.id), _pw(watch.id), _pw(watch.id)])

    run(more)
    after = Client(READER)
    body = after.get(SUMMARY).json()
    assert body["active_by_family"]["property_watch"]["total"] == 3
    assert after.statements == before.statements
