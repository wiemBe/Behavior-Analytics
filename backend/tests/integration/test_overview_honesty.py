"""FIX-2: overview and offense analytics say unknown when nothing was collected.

The operator path is the HTTP API. A missing offense snapshot is null. Zero is
what a capture that contains no OPEN offense measures. Silence is an open
NO_EVENTS finding, not a missing last_event_time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import AnomalyState, AnomalyType, Severity
from app.models.log_source import LogSourceAnomaly
from app.models.offense import OffenseSnapshot
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_fleet_api import VIEWER, Client, run

pytestmark = pytest.mark.integration

CAPTURED = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
AGING = ["0-1h", "1-4h", "4-24h", "24-72h", "72-168h", ">168h"]


def _snapshot(instance_id, *, qradar_id: int, status: str, **kw) -> OffenseSnapshot:  # type: ignore[no-untyped-def]
    return OffenseSnapshot(
        instance_id=instance_id,
        qradar_offense_id=qradar_id,
        captured_at=CAPTURED,
        start_time=CAPTURED,
        status=status,
        **kw,
    )


def _counts(body: dict) -> dict:
    return body["aggregates"]


def test_no_offense_snapshot_is_unknown_on_the_overview_and_the_analytics(db_schema) -> None:
    run(make_instance)
    client = Client(VIEWER)

    overview = client.get("/api/v1/overview")
    assert overview.status_code == 200, overview.text
    offenses = overview.json()["offenses"]
    assert offenses["active"] is None
    assert offenses["critical"] is None
    assert offenses["unassigned"] is None
    assert offenses["oldest_age_seconds"] is None
    assert overview.json()["log_sources"]["silent_log_sources"] == 0

    listing = client.get("/api/v1/offenses?limit=1")
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 0

    analytics = client.get("/api/v1/offenses/analytics")
    assert analytics.status_code == 200, analytics.text
    body = analytics.json()
    counts = _counts(body)
    assert counts["active"] is None
    assert counts["critical"] is None
    assert counts["unassigned"] is None
    assert counts["exceeding_sla"] is None
    assert counts["oldest_age_seconds"] is None
    assert counts["average_magnitude"] is None
    assert counts["sla_hours"] == 24
    assert counts["critical_magnitude"] == 7
    assert [bucket["bucket"] for bucket in body["aging"]] == AGING
    assert all(bucket["count"] is None for bucket in body["aging"])

    aggregates = client.get("/api/v1/offenses/aggregates")
    assert aggregates.status_code == 200, aggregates.text
    again = aggregates.json()
    assert again["active"] is None
    assert again["critical"] is None
    assert again["unassigned"] is None
    assert again["exceeding_sla"] is None
    assert again["sla_hours"] == 24
    assert again["critical_magnitude"] == 7


def test_a_closed_snapshot_measures_zero_open_offenses(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        s.add(_snapshot(inst.id, qradar_id=7, status="CLOSED", magnitude=9))

    run(setup)
    client = Client(VIEWER)

    offenses = client.get("/api/v1/overview").json()["offenses"]
    assert offenses["active"] == 0
    assert offenses["critical"] == 0
    assert offenses["unassigned"] == 0
    assert offenses["oldest_age_seconds"] is None

    assert client.get("/api/v1/offenses?limit=1").json()["total"] == 1
    counts = _counts(client.get("/api/v1/offenses/analytics").json())
    assert counts["active"] == 0
    assert counts["critical"] == 0
    assert counts["unassigned"] == 0
    assert counts["exceeding_sla"] == 0
    aging = client.get("/api/v1/offenses/analytics").json()["aging"]
    assert [bucket["count"] for bucket in aging] == [0, 0, 0, 0, 0, 0]


def test_an_open_snapshot_counts_critical_and_unassigned(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        s.add(_snapshot(
            inst.id, qradar_id=8, status="OPEN", magnitude=8, is_assigned=False, age_seconds=100,
        ))

    run(setup)
    client = Client(VIEWER)
    offenses = client.get("/api/v1/overview").json()["offenses"]
    assert offenses == {
        "active": 1,
        "critical": 1,
        "unassigned": 1,
        "oldest_age_seconds": 100,
    }
    body = client.get("/api/v1/offenses/analytics").json()
    counts = _counts(body)
    assert counts["active"] == 1
    assert counts["critical"] == 1
    assert counts["unassigned"] == 1
    assert counts["exceeding_sla"] == 0
    assert counts["critical_magnitude"] == 7
    bands = {bucket["bucket"]: bucket["count"] for bucket in body["aging"]}
    assert bands["0-1h"] == 1
    assert bands[">168h"] == 0


def test_silence_is_an_open_no_events_finding_not_a_null_timestamp(db_schema) -> None:
    """A source with no last_event_time, and no finding, is unknown.

    The count is how many monitored sources have an active, unsuppressed
    NO_EVENTS anomaly. CANDIDATE and RECOVERING are active. A volume drop, a
    resolved finding, a suppressed finding, and a source outside the monitored
    grid are not silence.
    """

    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        old = CAPTURED - timedelta(days=30)

        async def source(qid: int, name: str, **kw):  # type: ignore[no-untyped-def]
            return await make_log_source(s, inst, qradar_id=qid, name=name, **kw)

        def finding(src, kind, **kw) -> None:  # type: ignore[no-untyped-def]
            s.add(LogSourceAnomaly(
                log_source_id=src.id, anomaly_type=kind, severity=Severity.HIGH,
                detected_at=CAPTURED, **kw,
            ))

        never = await source(1, "never")
        await source(2, "old", last_event_time=old)
        opened = await source(3, "open-silent")
        candidate = await source(4, "candidate-silent")
        recovering = await source(5, "recovering-silent")
        volume = await source(6, "volume")
        resolved = await source(7, "resolved")
        suppressed = await source(8, "suppressed")
        disabled = await source(9, "disabled", enabled=False)
        unmonitored = await source(10, "unmonitored", monitoring_enabled=False)
        maintenance = await source(11, "maintenance", maintenance_mode=True)
        del never
        finding(opened, AnomalyType.NO_EVENTS, state=AnomalyState.OPEN)
        finding(candidate, AnomalyType.NO_EVENTS, state=AnomalyState.CANDIDATE)
        finding(recovering, AnomalyType.NO_EVENTS, state=AnomalyState.RECOVERING)
        finding(volume, AnomalyType.VOLUME_DROP, state=AnomalyState.OPEN)
        finding(resolved, AnomalyType.NO_EVENTS, state=AnomalyState.RESOLVED)
        finding(suppressed, AnomalyType.NO_EVENTS, state=AnomalyState.OPEN, suppressed=True)
        finding(disabled, AnomalyType.NO_EVENTS, state=AnomalyState.OPEN)
        finding(unmonitored, AnomalyType.NO_EVENTS, state=AnomalyState.OPEN)
        finding(maintenance, AnomalyType.NO_EVENTS, state=AnomalyState.OPEN)

    run(setup)
    body = Client(VIEWER).get("/api/v1/overview").json()
    assert body["log_sources"]["silent_log_sources"] == 3
