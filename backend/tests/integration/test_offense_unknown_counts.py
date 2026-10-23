"""FIX-4: offense counts say how many open offenses they could not classify.

The operator path is the HTTP API. An OPEN offense with a null magnitude is in
neither `critical` nor the known rest, and one with a null age is not in
`exceeding_sla`. Both are counted, and the overview and the aggregates share
one critical threshold from the settings.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.config import get_settings
from app.models.offense import OffenseSnapshot
from tests.integration.factories import make_instance
from tests.integration.test_fleet_api import VIEWER, Client, run

pytestmark = pytest.mark.integration

CAPTURED = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
SLA = 24 * 3600


def _snap(instance_id, qid: int, status: str = "OPEN", **kw) -> OffenseSnapshot:  # type: ignore[no-untyped-def]
    return OffenseSnapshot(
        instance_id=instance_id, qradar_offense_id=qid, captured_at=CAPTURED,
        start_time=CAPTURED, status=status, **kw,
    )


def _client(threshold: int | None = None) -> Client:
    client = Client(VIEWER)
    if threshold is not None:
        patched = get_settings().model_copy(update={"offense_critical_magnitude": threshold})
        client.http.app.dependency_overrides[get_settings] = lambda: patched  # type: ignore[attr-defined]
    return client


def _mixed(s):  # type: ignore[no-untyped-def]
    async def go():  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        s.add(_snap(inst.id, 1, magnitude=9, age_seconds=SLA + 1))
        s.add(_snap(inst.id, 2, magnitude=7, age_seconds=10))
        s.add(_snap(inst.id, 3, magnitude=None, age_seconds=SLA + 5))
        s.add(_snap(inst.id, 4, magnitude=8, age_seconds=None))
        s.add(_snap(inst.id, 5, magnitude=None, age_seconds=None))
        # Closed rows never count, known or unknown.
        s.add(_snap(inst.id, 6, status="CLOSED", magnitude=None, age_seconds=None))
    return go()


def test_aggregates_and_analytics_count_unknown_magnitude_and_age(db_schema) -> None:
    run(_mixed)
    client = _client()
    for url, pick in (
        ("/api/v1/offenses/aggregates", lambda b: b),
        ("/api/v1/offenses/analytics", lambda b: b["aggregates"]),
    ):
        counts = pick(client.get(url).json())
        assert counts["active"] == 5
        assert counts["critical"] == 3  # 9, 7, 8
        assert counts["critical_unknown"] == 2  # #3 and #5, not the closed #6
        assert counts["exceeding_sla"] == 2  # #1 and #3
        assert counts["age_unknown"] == 2  # #4 and #5, not the closed #6


def test_no_snapshot_makes_the_unknown_counts_null(db_schema) -> None:
    run(make_instance)
    client = _client()
    counts = client.get("/api/v1/offenses/aggregates").json()
    assert counts["critical_unknown"] is None
    assert counts["age_unknown"] is None
    analytics = client.get("/api/v1/offenses/analytics").json()["aggregates"]
    assert analytics["critical_unknown"] is None
    assert analytics["age_unknown"] is None


def test_a_capture_with_nothing_open_measures_zero_unknowns(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        s.add(_snap(inst.id, 1, status="CLOSED", magnitude=None, age_seconds=None))

    run(setup)
    counts = _client().get("/api/v1/offenses/aggregates").json()
    assert counts["critical_unknown"] == 0
    assert counts["age_unknown"] == 0


def test_overview_and_aggregates_share_one_non_default_critical_threshold(db_schema) -> None:
    run(_mixed)
    client = _client(threshold=8)
    overview = client.get("/api/v1/overview").json()["offenses"]
    aggregates = client.get("/api/v1/offenses/aggregates").json()
    assert aggregates["critical_magnitude"] == 8
    assert aggregates["critical"] == 2  # 9 and 8; 7 is below the threshold
    assert overview["critical"] == aggregates["critical"]
    assert overview["critical_unknown"] == aggregates["critical_unknown"] == 2
