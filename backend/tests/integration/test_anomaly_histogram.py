"""GET /anomalies/histogram: uncapped counts per UTC hour and severity.

The histogram's population is exactly the list's, so wherever a test can, it
asserts against `GET /anomalies` for the same arguments rather than against
hand-added numbers alone. Everything is seeded through committed sessions and
read back over HTTP, as the other API tests do.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.models.enums import AnomalyState, AnomalyType, EvidenceStatus, Severity, WafAnomalyType
from app.models.log_source import LogSourceAnomaly
from app.models.waf import WafAnomaly, WafEndpoint
from app.security.rbac import Principal
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_fleet_api import Client, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the anomaly histogram integration tests",
    ),
]

HIST = "/api/v1/anomalies/histogram"

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
ANOMALY_READER = Principal(subject="ar", permissions=frozenset({"read:anomalies"}))
NOBODY = Principal(subject="nobody", permissions=frozenset())

# Fixed instants: the route always gets an explicit range, so the wall clock
# plays no part and the hour boundaries are readable straight off the literals.
H0 = datetime(2026, 10, 13, 8, 0, tzinfo=UTC)


def at(hours: float, minutes: int = 0, seconds: int = 0) -> datetime:
    return H0 + timedelta(hours=hours, minutes=minutes, seconds=seconds)


def _iso(dt: datetime) -> str:
    """UTC timestamp in the Z spelling: a bare `+` decodes to a space in a query."""
    return dt.isoformat().replace("+00:00", "Z")


def rng(hours: int) -> dict[str, str]:
    return {"since": _iso(H0), "until": _iso(at(hours))}


def phase_a(source_id, when: datetime, **kw) -> LogSourceAnomaly:  # type: ignore[no-untyped-def]
    defaults = dict(
        log_source_id=source_id, anomaly_type=AnomalyType.VOLUME_SPIKE,
        severity=Severity.MEDIUM, state=AnomalyState.OPEN, detected_at=when,
        evidence_status=EvidenceStatus.NOT_REQUESTED,
    )
    defaults.update(kw)
    return LogSourceAnomaly(**defaults)


def waf(endpoint_id, when: datetime, **kw) -> WafAnomaly:  # type: ignore[no-untyped-def]
    defaults = dict(
        waf_endpoint_id=endpoint_id, detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
        severity=Severity.MEDIUM, state=AnomalyState.OPEN, detected_at=when,
        evidence_status=EvidenceStatus.NOT_REQUESTED,
    )
    defaults.update(kw)
    return WafAnomaly(**defaults)


def endpoint(instance_id, source_id, path="/login", **kw) -> WafEndpoint:  # type: ignore[no-untyped-def]
    defaults = dict(
        instance_id=instance_id, log_source_id=source_id, virtual_host=None,
        normalized_endpoint=path, http_method="POST",
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        first_seen_at=at(-24), last_seen_at=at(24), tracked_since=at(-24), is_active=True,
    )
    defaults.update(kw)
    return WafEndpoint(**defaults)


async def world(s):  # type: ignore[no-untyped-def]
    """Both families over hours 0..3 of [H0, H0+5h), mixed severities and states.

    Hour 0: 2 CRITICAL Phase A + 1 CRITICAL WAF -- the same severity from both
    families in one hour, so the branches must add up, not overwrite one
    another. Hour 1: 1 HIGH, RESOLVED -- every state counts, the list's
    default. Hour 2: 1 LOW. Hour 3: 1 MEDIUM WAF.
    """
    inst = await make_instance(s)
    fw = await make_log_source(s, inst, qradar_id=1, name="fw-1")
    ws = await make_log_source(s, inst, qradar_id=2, name="waf-1")
    ep = endpoint(inst.id, ws.id)
    s.add(ep)
    await s.flush()
    s.add_all([
        phase_a(fw.id, at(0), severity=Severity.CRITICAL),
        phase_a(fw.id, at(0, minutes=30), severity=Severity.CRITICAL),
        waf(ep.id, at(0, minutes=45), severity=Severity.CRITICAL),
        phase_a(fw.id, at(1), severity=Severity.HIGH, state=AnomalyState.RESOLVED),
        phase_a(fw.id, at(2, minutes=10), severity=Severity.LOW),
        waf(ep.id, at(3, minutes=5), severity=Severity.MEDIUM),
    ])
    return dict(inst=inst.id, fw=fw.id, ws=ws.id, ep=ep.id)


def histogram(client: Client, params: dict) -> dict:  # type: ignore[type-arg]
    resp = client.get(HIST, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ------------------------------------------------------------------ population
def test_the_totals_equal_the_list_for_the_same_range(db_schema) -> None:  # type: ignore[no-untyped-def]
    run(world)
    client = Client(READER)
    listed = client.get("/api/v1/anomalies", params={**rng(5), "limit": 200}).json()
    body = histogram(client, rng(5))
    assert listed["total"] == 6
    assert body["total"] == listed["total"]
    assert sum(hour["total"] for hour in body["hours"]) == listed["total"]


def test_the_hours_carry_their_counts_in_order(db_schema) -> None:  # type: ignore[no-untyped-def]
    run(world)
    body = histogram(Client(READER), rng(5))
    assert body["bucket_seconds"] == 3600
    assert body["families"] == ["LOG_SOURCE", "WAF_ENDPOINT"]
    assert body["since"] == _iso(H0) and body["until"] == _iso(at(5))
    assert [hour["start"] for hour in body["hours"]] == [_iso(at(i)) for i in range(5)]
    by_start = {hour["start"]: hour for hour in body["hours"]}
    # Only non-zero severities hold an entry; the rest counted zero in the hour.
    # The two families' CRITICAL rows land in the same hour and must add up.
    assert by_start[_iso(at(0))]["counts"] == {"CRITICAL": 3}
    assert by_start[_iso(at(0))]["total"] == 3
    assert by_start[_iso(at(1))]["counts"] == {"HIGH": 1}
    assert by_start[_iso(at(2))]["counts"] == {"LOW": 1}
    assert by_start[_iso(at(3))]["counts"] == {"MEDIUM": 1}
    assert by_start[_iso(at(4))] == {"start": _iso(at(4)), "counts": {}, "total": 0}


def test_every_state_is_counted_like_the_lists_default(db_schema) -> None:  # type: ignore[no-untyped-def]
    run(world)
    client = Client(READER)
    active = client.get(
        "/api/v1/anomalies", params={**rng(5), "active_only": "true", "limit": 200}
    ).json()
    body = histogram(client, rng(5))
    assert active["total"] == 5  # active_only drops the RESOLVED row
    assert body["total"] == 6  # the histogram has no such filter
    assert histogram(client, {"since": _iso(at(1)), "until": _iso(at(2))})["total"] == 1


@pytest.mark.parametrize("kind,count", [("LOG_SOURCE", 4), ("WAF_ENDPOINT", 2)])
def test_subject_kind_selects_the_family(db_schema, kind: str, count: int) -> None:  # type: ignore[no-untyped-def]
    run(world)
    client = Client(READER)
    params = {**rng(5), "subject_kind": kind}
    listed = client.get("/api/v1/anomalies", params={**params, "limit": 200}).json()
    body = histogram(client, params)
    assert listed["total"] == count
    assert body["total"] == count
    assert body["families"] == [kind]
    assert sum(hour["total"] for hour in body["hours"]) == count


def test_the_boundaries_are_half_open(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        fw = await make_log_source(s, inst, qradar_id=9, name="fw-bounds")
        s.add_all([
            phase_a(fw.id, at(2)),  # == since: counted
            phase_a(fw.id, at(2, minutes=59, seconds=59)),  # last instant of its hour
            phase_a(fw.id, at(4)),  # == until: excluded, lands in no hour
        ])

    run(setup)
    body = histogram(
        Client(READER), {"since": _iso(at(2)), "until": _iso(at(4))}
    )
    assert [hour["start"] for hour in body["hours"]] == [_iso(at(2)), _iso(at(3))]
    assert body["hours"][0]["counts"] == {"MEDIUM": 2}
    assert body["hours"][1] == {"start": _iso(at(3)), "counts": {}, "total": 0}
    assert body["total"] == 2


def test_an_empty_range_is_all_measured_zeros(db_schema) -> None:  # type: ignore[no-untyped-def]
    run(world)
    body = histogram(Client(READER), {"since": _iso(at(20)), "until": _iso(at(24))})
    assert body["total"] == 0
    # The families are the population the zeros are measured over, present even
    # when nothing was counted.
    assert body["families"] == ["LOG_SOURCE", "WAF_ENDPOINT"]
    assert len(body["hours"]) == 4
    assert all(hour["counts"] == {} and hour["total"] == 0 for hour in body["hours"])


# --------------------------------------------------------------------- limits
def test_seven_days_is_the_ceiling(db_schema) -> None:  # type: ignore[no-untyped-def]
    client = Client(READER)
    assert client.get(HIST, params=rng(168)).status_code == 200
    past = client.get(HIST, params={"since": _iso(H0), "until": _iso(at(169))})
    assert past.status_code == 422
    assert "168 hours" in past.text


def test_the_api_range_ceiling_still_applies(db_schema) -> None:  # type: ignore[no-untyped-def]
    client = Client(READER)
    too_wide = {"since": _iso(at(-24 * 400)), "until": _iso(H0)}
    assert client.get(HIST, params=too_wide).status_code == 422
    # A tightened ceiling is honored too: the histogram reuses _validate_range.
    settings = get_settings().model_copy(update={"api_max_range_days": 3})
    client.http.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    assert client.get(HIST, params=rng(24 * 4)).status_code == 422
    assert client.get(HIST, params=rng(24 * 3)).status_code == 200


def test_a_missing_or_inverted_range_is_422(db_schema) -> None:  # type: ignore[no-untyped-def]
    client = Client(READER)
    assert client.get(HIST).status_code == 422
    assert client.get(HIST, params={"since": _iso(H0)}).status_code == 422
    assert client.get(HIST, params={"until": _iso(H0)}).status_code == 422
    inverted = {"since": _iso(at(2)), "until": _iso(H0)}
    assert client.get(HIST, params=inverted).status_code == 422


# -------------------------------------------------------------------- routing
def test_the_histogram_is_not_captured_by_the_id_route(db_schema) -> None:  # type: ignore[no-untyped-def]
    """Declared before /{anomaly_id}: routed after it, "histogram" would be
    parsed as a uuid and every call would die as a parse error."""
    run(world)
    client = Client(READER)
    assert "hours" in histogram(client, rng(5))
    assert client.get(f"/api/v1/anomalies/{uuid.uuid4()}").status_code == 404


# ------------------------------------------------------------------ statement
def test_one_statement_whatever_the_data_size(db_schema) -> None:  # type: ignore[no-untyped-def]
    ids = run(world)
    client = Client(READER)
    assert histogram(client, rng(5))["total"] == 6
    small = client.statements

    async def grow(s):  # type: ignore[no-untyped-def]
        eps = [endpoint(ids["inst"], ids["ws"], path=f"/p{i}") for i in range(10)]
        s.add_all(eps)
        await s.flush()
        s.add_all([
            phase_a(ids["fw"], at(i % 5, minutes=i % 60)) for i in range(60)
        ])
        s.add_all([
            waf(eps[i % 10].id, at(i % 5, minutes=30)) for i in range(40)
        ])

    run(grow)
    listed = client.get("/api/v1/anomalies", params={**rng(5), "limit": 200}).json()
    body = histogram(client, rng(5))
    assert listed["total"] == 106
    assert body["total"] == listed["total"]
    assert client.statements == small == 1


# ----------------------------------------------------------------------- auth
def test_read_anomalies_is_required(db_schema) -> None:  # type: ignore[no-untyped-def]
    run(world)
    assert Client(NOBODY).get(HIST, params=rng(5)).status_code == 403
    assert Client(ANOMALY_READER).get(HIST, params=rng(5)).status_code == 200
    assert Client(READER).get(HIST, params=rng(5)).status_code == 200


def test_the_boundaries_are_half_open_inside_an_hour_for_both_families(db_schema) -> None:  # type: ignore[no-untyped-def]
    """`until` mid-hour: a row exactly at it would land in a present hour, so a
    leaked boundary row cannot hide behind the hour axis. Both families, since
    each has its own branch and its own predicate."""
    since, until = at(2, minutes=15), at(3, minutes=30)

    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        fw = await make_log_source(s, inst, qradar_id=11, name="fw-mid")
        ws = await make_log_source(s, inst, qradar_id=12, name="waf-mid")
        ep = endpoint(inst.id, ws.id)
        s.add(ep)
        await s.flush()
        s.add_all([
            phase_a(fw.id, since), phase_a(fw.id, until),
            phase_a(fw.id, since - timedelta(microseconds=1)),
            waf(ep.id, since), waf(ep.id, until),
            waf(ep.id, since - timedelta(microseconds=1)),
        ])

    run(setup)
    client = Client(READER)
    params = {"since": _iso(since), "until": _iso(until)}
    body = histogram(client, params)
    listed = client.get("/api/v1/anomalies", params={**params, "limit": 200}).json()
    assert body["total"] == listed["total"] == 2
    assert [hour["start"] for hour in body["hours"]] == [_iso(at(2)), _iso(at(3))]
    assert body["hours"][0]["counts"] == {"MEDIUM": 2}
    assert body["hours"][1]["counts"] == {}
