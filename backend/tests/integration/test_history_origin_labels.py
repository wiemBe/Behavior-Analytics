"""Every family's bucket and series API says which rows were read from QRadar's past.

HB-14. The history backfill writes the rows it read from QRadar's past into the
family tables, marked `origin = 'HISTORY'` (Phase A marks them in
`collection_source`). Every API that returns such a row is a DISPLAY reader
(HISTORY-BACKFILL §B.4.6): it may read both kinds, but it must say which is
which, so the UI can label a history bucket in text.

Each test here inserts a LIVE row and a HISTORY row directly (the `origin`
column exists on every family table since migration 0030) and reads the API
back over HTTP. The marker is the only thing that travels: no raw value of any
kind is added to a response.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.api.routes import fleet as fleet_routes
from app.models.catchall import CatchallBucket
from app.models.enums import (
    BucketCompleteness,
    IpsDimension,
    IpsPreset,
    IpsProfileValidationStatus,
    WafMeasurement,
    WafProfileValidationStatus,
)
from app.models.ips import (
    IpsEntity,
    IpsEntityMetric,
    IpsSourceBucket,
    IpsSourceProfile,
)
from app.models.log_source import LogSourceMetric
from app.models.rule import AnalyticsRule, RuleMatchWindow, RuleMatchWindowStatus
from app.models.waf import (
    WafEndpoint,
    WafEndpointMetric,
    WafSourceBucket,
    WafSourceProfile,
)
from app.security.rbac import Principal
from app.services import rule_statistics as rule_statistics_service
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from tests.integration.factories import add_metric, make_instance, make_log_source
from tests.integration.test_fleet_api import Client, classification, run

pytestmark = pytest.mark.integration

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
WAF_ADMIN = Principal(subject="waf-admin", permissions=frozenset({"admin:waf"}))

#: A fixed "now" the fleet and rule-statistics windows are computed against.
NOW = datetime(2026, 10, 13, 10, 7, tzinfo=UTC)
#: The last closed 5-minute bucket before NOW.
CLOSED = datetime(2026, 10, 13, 10, 5, tzinfo=UTC)
#: The rule-statistics clock: 12:10 with a 300 s settle lag and hourly windows
#: makes 12:00 the last settled window.
STATS_NOW = datetime(2026, 10, 12, 12, 10, tzinfo=UTC)
STATS_END = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


class _FrozenClock(datetime):
    """A `datetime` whose `now` is a fixed instant.

    The rule-statistics service takes the clock from the module, so pinning it
    here pins the window the route measures.
    """

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return STATS_NOW if tz is None else STATS_NOW.astimezone(tz)


@pytest.fixture
def _fleet_clock(monkeypatch) -> None:
    monkeypatch.setattr(fleet_routes, "_utcnow", lambda: NOW)


@pytest.fixture
def _stats_clock(monkeypatch) -> None:
    monkeypatch.setattr(rule_statistics_service, "datetime", _FrozenClock)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _window(**extra) -> dict:  # type: ignore[no-untyped-def]
    return {
        "since": _iso(NOW - timedelta(hours=6)),
        "until": _iso(NOW + timedelta(hours=1)),
        **extra,
    }


# --------------------------------------------------------------------- Phase A
async def _seed_phase_a(s) -> dict:  # type: ignore[no-untyped-def]
    """One source whose four buckets spell the four Phase A markers.

    Phase A has no `origin` column: the marker is `collection_source`, and both
    history spellings (`ariel_history`, `mock_history`) mean HISTORY while any
    other value (including NULL) means LIVE (§B.4.1).
    """
    inst = await make_instance(s)
    src = await make_log_source(s, inst, qradar_id=1, name="src")
    markers = [None, "ariel_aggregate", "ariel_history", "mock_history"]
    for index, marker in enumerate(markers):
        await add_metric(
            s,
            src,
            CLOSED - timedelta(minutes=5 * (len(markers) - index)),
            event_count=100,
            collection_source=marker,
        )
    return {"source": src.id}


def test_phase_a_metrics_say_which_buckets_came_from_history(db_schema) -> None:
    ids = run(_seed_phase_a)
    resp = Client(READER).get(
        f"/api/v1/behavior/sources/{ids['source']}/metrics", params=_window()
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["origin"] for r in rows] == ["LIVE", "LIVE", "HISTORY", "HISTORY"]
    # The marker travels alone: the bucket's own measurements are unchanged.
    assert all(r["event_count"] == 100 for r in rows)


# ----------------------------------------------------------------------- WAF
def _waf_profile(instance_id, source_id, **kw) -> WafSourceProfile:  # type: ignore[no-untyped-def]
    defaults = dict(
        instance_id=instance_id, log_source_id=source_id, enabled=True, profile_version=2,
        url_property="URL", method_property="Method", product_label="lab-waf",
        validation_status=WafProfileValidationStatus.VALID, validated_profile_version=2,
        validated_at=CLOSED - timedelta(minutes=60), validation_report={},
    )
    defaults.update(kw)
    return WafSourceProfile(**defaults)


def _waf_bucket(source_id, bucket_start, **kw) -> WafSourceBucket:  # type: ignore[no-untyped-def]
    defaults = dict(
        log_source_id=source_id, bucket_start=bucket_start, bucket_seconds=300,
        origin="LIVE", profile_version=2, metric_semantics="sem",
        completeness=BucketCompleteness.COMPLETE, endpoint_coverage_ratio=0.97,
        total_events=500, parsed_events=485, group_count=3, groups_truncated=False,
        unusable_groups=0, observed_endpoints=3, tracked_endpoints_observed=3,
        admitted_endpoints=0, below_admission_threshold=0, rejected_over_quota=0,
        untracked_endpoints=0, untracked_requests=0, admission_truncated=False,
        zero_filled_endpoints=0, request_count_measurement=WafMeasurement.EXACT,
        sparse_endpoint_rows=True, in_maintenance=False, collection_source="TEST",
        query_provenance={"queries": ["AQL"]},
    )
    defaults.update(kw)
    return WafSourceBucket(**defaults)


async def _seed_waf(s) -> dict:  # type: ignore[no-untyped-def]
    """A WAF source with a LIVE bucket, a HISTORY one and an endpoint's rows."""
    inst = await make_instance(s)
    src = await make_log_source(s, inst, qradar_id=1, name="waf-1")
    live_only = await make_log_source(s, inst, qradar_id=2, name="waf-2")
    s.add_all([_waf_profile(inst.id, src.id), _waf_profile(inst.id, live_only.id)])
    ep = WafEndpoint(
        instance_id=inst.id, log_source_id=src.id, virtual_host=None,
        normalized_endpoint="/login", http_method="POST",
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        first_seen_at=CLOSED - timedelta(hours=1), last_seen_at=CLOSED,
        tracked_since=CLOSED - timedelta(hours=1), is_active=True,
    )
    s.add(ep)
    await s.flush()
    live_at = CLOSED - timedelta(minutes=10)
    history_at = CLOSED - timedelta(minutes=5)
    orphan_at = CLOSED - timedelta(minutes=20)
    s.add_all([
        _waf_bucket(src.id, live_at),
        _waf_bucket(src.id, history_at, origin="HISTORY"),
        _waf_bucket(live_only.id, live_at),
    ])
    s.add_all([
        WafEndpointMetric(
            waf_endpoint_id=ep.id, bucket_start=live_at, bucket_seconds=300,
            request_count=120, endpoint_coverage_ratio=0.97,
            completeness=BucketCompleteness.COMPLETE, normalizer_version=1,
        ),
        WafEndpointMetric(
            waf_endpoint_id=ep.id, bucket_start=history_at, bucket_seconds=300,
            request_count=90, endpoint_coverage_ratio=0.97,
            completeness=BucketCompleteness.COMPLETE, normalizer_version=1,
        ),
        # No source bucket record for this one: the marker cannot be read, so it
        # is unknown rather than guessed.
        WafEndpointMetric(
            waf_endpoint_id=ep.id, bucket_start=orphan_at, bucket_seconds=300,
            request_count=5, endpoint_coverage_ratio=1.0,
            completeness=BucketCompleteness.COMPLETE, normalizer_version=1,
        ),
    ])
    await s.flush()
    return {"source": src.id, "live_only": live_only.id, "endpoint": ep.id}


def test_the_waf_source_detail_says_where_its_latest_bucket_came_from(db_schema) -> None:
    ids = run(_seed_waf)
    resp = Client(READER).get(
        f"/api/v1/behavior/waf/sources/{ids['source']}", params=_window()
    )
    assert resp.status_code == 200
    assert resp.json()["latest_bucket"]["origin"] == "HISTORY"
    other = Client(READER).get(
        f"/api/v1/behavior/waf/sources/{ids['live_only']}", params=_window()
    )
    assert other.json()["latest_bucket"]["origin"] == "LIVE"


def test_the_waf_endpoint_metrics_say_where_each_bucket_came_from(db_schema) -> None:
    ids = run(_seed_waf)
    resp = Client(READER).get(
        f"/api/v1/behavior/waf/endpoints/{ids['endpoint']}/metrics", params=_window()
    )
    assert resp.status_code == 200
    rows = resp.json()["items"]
    # Oldest first: the orphan row (no source bucket record), the live bucket,
    # the history bucket.
    assert [r["origin"] for r in rows] == [None, "LIVE", "HISTORY"]


def test_the_waf_admin_status_says_where_the_last_bucket_came_from(db_schema) -> None:
    ids = run(_seed_waf)
    resp = Client(WAF_ADMIN).get("/api/v1/waf/sources")
    assert resp.status_code == 200
    by_source = {i["log_source_id"]: i for i in resp.json()}
    assert by_source[str(ids["source"])]["last_bucket"]["origin"] == "HISTORY"
    assert by_source[str(ids["live_only"])]["last_bucket"]["origin"] == "LIVE"


# ----------------------------------------------------------------------- IPS
def _ips_profile(instance_id, source_id, **kw) -> IpsSourceProfile:  # type: ignore[no-untyped-def]
    defaults = dict(
        instance_id=instance_id, log_source_id=source_id, enabled=True, profile_version=2,
        product_label="lab-ips", preset=IpsPreset.CISCO_FTD, mapping={}, action_value_map={},
        validation_status=IpsProfileValidationStatus.VALID, validated_profile_version=2,
        validated_at=CLOSED - timedelta(minutes=60), validation_report={},
    )
    defaults.update(kw)
    return IpsSourceProfile(**defaults)


def _ips_bucket(source_id, bucket_start, **kw) -> IpsSourceBucket:  # type: ignore[no-untyped-def]
    defaults = dict(
        log_source_id=source_id, bucket_start=bucket_start, bucket_seconds=300,
        origin="LIVE", total_events=5000, total_records=5100,
        destination_port_measurement="EXACT", destination_port_coverage_ratio=0.98,
        destination_ip_measurement="EXACT", destination_ip_coverage_ratio=0.99,
        signature_measurement="EXACT", signature_coverage_ratio=0.97,
        source_ip_measurement="EXACT", source_ip_coverage_ratio=1.0,
        port_fanout_measurement="EXACT", action_measurement="NOT_COLLECTED",
        profile_version=2, normalizer_version=1, in_maintenance=False,
        collected_at=bucket_start, query_provenance={},
    )
    defaults.update(kw)
    return IpsSourceBucket(**defaults)


async def _seed_ips(s) -> dict:  # type: ignore[no-untyped-def]
    """An IPS source with a LIVE bucket, a HISTORY one and an entity's rows."""
    inst = await make_instance(s)
    src = await make_log_source(s, inst, qradar_id=1, name="ips-1")
    live_only = await make_log_source(s, inst, qradar_id=2, name="ips-2")
    s.add_all([_ips_profile(inst.id, src.id), _ips_profile(inst.id, live_only.id)])
    port = IpsEntity(
        instance_id=inst.id, log_source_id=src.id, dimension=IpsDimension.DESTINATION_PORT,
        value_key="443/tcp", first_seen_at=CLOSED - timedelta(hours=1),
        last_seen_at=CLOSED, active=True, normalizer_version=1,
    )
    s.add(port)
    await s.flush()
    live_at = CLOSED - timedelta(minutes=10)
    history_at = CLOSED - timedelta(minutes=5)
    orphan_at = CLOSED - timedelta(minutes=20)
    s.add_all([
        _ips_bucket(src.id, live_at),
        _ips_bucket(src.id, history_at, origin="HISTORY"),
        _ips_bucket(live_only.id, live_at),
    ])
    s.add_all([
        IpsEntityMetric(
            ips_entity_id=port.id, bucket_start=live_at, bucket_seconds=300, events=812
        ),
        IpsEntityMetric(
            ips_entity_id=port.id, bucket_start=history_at, bucket_seconds=300, events=90
        ),
        # No source bucket record: the marker cannot be read, so it is unknown.
        IpsEntityMetric(
            ips_entity_id=port.id, bucket_start=orphan_at, bucket_seconds=300, events=5
        ),
    ])
    await s.flush()
    return {"source": src.id, "live_only": live_only.id, "entity": port.id}


def test_the_ips_sources_say_where_the_latest_bucket_came_from(db_schema) -> None:
    ids = run(_seed_ips)
    resp = Client(READER).get("/api/v1/behavior/ips/sources")
    assert resp.status_code == 200
    by_source = {i["log_source_id"]: i for i in resp.json()["items"]}
    assert by_source[str(ids["source"])]["latest_bucket"]["origin"] == "HISTORY"
    assert by_source[str(ids["live_only"])]["latest_bucket"]["origin"] == "LIVE"


def test_the_ips_entity_metrics_say_where_each_bucket_came_from(db_schema) -> None:
    ids = run(_seed_ips)
    resp = Client(READER).get(
        f"/api/v1/behavior/ips/entities/{ids['entity']}/metrics", params=_window()
    )
    assert resp.status_code == 200
    rows = resp.json()["items"]
    # Oldest first: the orphan row (no source bucket record), the live bucket,
    # the history bucket.
    assert [r["origin"] for r in rows] == [None, "LIVE", "HISTORY"]


def test_the_ips_source_metrics_say_where_each_bucket_came_from(db_schema) -> None:
    ids = run(_seed_ips)
    resp = Client(READER).get(
        f"/api/v1/behavior/ips/sources/{ids['source']}/metrics", params=_window()
    )
    assert resp.status_code == 200
    rows = resp.json()["items"]
    assert [r["origin"] for r in rows] == ["LIVE", "HISTORY"]


# ------------------------------------------------------------------ catch-all
async def _seed_catchall(s) -> dict:  # type: ignore[no-untyped-def]
    inst = await make_instance(s)
    src = await make_log_source(s, inst, qradar_id=1, name="SIM Generic", type_id=67)
    s.add_all([
        CatchallBucket(
            catchall_log_source_id=src.id, bucket_start=CLOSED - timedelta(minutes=10),
            bucket_seconds=300, origin="LIVE", events=10, records=2, distinct_senders=1,
            distinct_senders_lower_bound=False, measurement="EXACT",
            top_senders=[{"identifier": "a", "events": 10}], admitted=1,
            below_admission_threshold=0, rejected_over_quota=0, untracked_senders=0,
            untracked_events=0, query_provenance={}, collected_at=CLOSED,
        ),
        CatchallBucket(
            catchall_log_source_id=src.id, bucket_start=CLOSED - timedelta(minutes=5),
            bucket_seconds=300, origin="HISTORY", events=20, records=4,
            distinct_senders=2, distinct_senders_lower_bound=False, measurement="EXACT",
            top_senders=[{"identifier": "b", "events": 20}], admitted=2,
            below_admission_threshold=0, rejected_over_quota=0, untracked_senders=0,
            untracked_events=0, query_provenance={}, collected_at=CLOSED,
        ),
    ])
    await s.flush()
    return {"source": src.id}


def test_the_catchall_buckets_say_where_each_bucket_came_from(db_schema) -> None:
    ids = run(_seed_catchall)
    resp = Client(READER).get(
        "/api/v1/fleet/catchall/buckets",
        params={
            "catchall_log_source_id": str(ids["source"]),
            "since": _iso(CLOSED - timedelta(hours=1)),
            "until": _iso(CLOSED + timedelta(hours=1)),
        },
    )
    assert resp.status_code == 200
    rows = resp.json()["items"]
    assert [r["origin"] for r in rows] == ["LIVE", "HISTORY"]


# --------------------------------------------------------------- fleet series
async def _seed_fleet(s) -> dict:  # type: ignore[no-untyped-def]
    """Two sources whose buckets share the newest steps.

    The history source's newest buckets carry the Phase A history marker in
    `collection_source` (§B.4.1), which is what the series counts. One of its
    buckets in the middle is missing, so that step cannot be summed.
    """
    inst = await make_instance(s)
    hist = await make_log_source(s, inst, qradar_id=1, name="hist")
    live = await make_log_source(s, inst, qradar_id=2, name="live")
    # Minutes before CLOSED (10:05); the window's newest bucket is 10:00.
    for offset, marker in ((5, "ariel_history"), (10, "ariel_history"), (20, "ariel_aggregate"),
                           (25, "ariel_aggregate")):
        await add_metric(
            s,
            hist,
            CLOSED - timedelta(minutes=offset),
            event_count=100,
            collection_source=marker,
        )
    for offset in (5, 10, 15, 20, 25):
        await add_metric(
            s,
            live,
            CLOSED - timedelta(minutes=offset),
            event_count=200,
            collection_source="ariel_aggregate",
        )
    await s.flush()
    return {"hist": hist.id, "live": live.id}


def _fleet_points(body: dict) -> list[dict]:
    assert [s["key"] for s in body["series"]] == ["FLEET"]
    return body["series"][0]["points"]


def test_the_fleet_series_counts_the_cells_that_came_from_history(
    db_schema, _fleet_clock
) -> None:
    run(_seed_fleet)
    body = Client(READER).get("/api/v1/fleet/eps-series?window=1h").json()
    points = _fleet_points(body)
    # The five newest steps, oldest first: 09:40 and 09:45 (both cells live),
    # 09:50 (the history cell is missing), 09:55 and 10:00 (one cell of the two
    # is history).
    assert [p["cells_from_history"] for p in points[-5:]] == [0, 0, 0, 1, 1]
    assert [p["cells_complete"] for p in points[-5:]] == [2, 2, 1, 2, 2]
    assert all(p["cells_expected"] == 2 for p in points)


def test_a_fleet_step_whose_cells_are_all_live_reports_zero_history_cells(
    db_schema, _fleet_clock
) -> None:
    run(_seed_fleet)
    body = Client(READER).get("/api/v1/fleet/eps-series?window=1h").json()
    point = _fleet_points(body)[-4]
    # A measured zero: the cells are there and none of them is history.
    assert point["cells_complete"] == 2 and point["cells_from_history"] == 0
    assert point["events"] == 300 and point["eps"] == 300 / 300


def test_a_fleet_step_with_a_missing_cell_keeps_its_nulls_and_still_counts(
    db_schema, _fleet_clock
) -> None:
    run(_seed_fleet)
    body = Client(READER).get("/api/v1/fleet/eps-series?window=1h").json()
    point = _fleet_points(body)[-3]
    # One cell of the step is missing: the step cannot be summed, and the
    # counts are still reported.
    assert point["cells_complete"] == 1 and point["cells_expected"] == 2
    assert point["cells_from_history"] == 0
    assert point["events"] is None and point["eps"] is None


def test_the_fleet_series_statement_count_is_unchanged(
    db_schema, _fleet_clock
) -> None:
    """The marker rides the aggregate it already read: no statement is added."""
    run(_seed_fleet)
    client = Client(READER)
    resp = client.get("/api/v1/fleet/eps-series?window=1h")
    assert resp.status_code == 200
    # Measured on the base before this change: 3 statements (the sources, the
    # metric cells, the baselines).
    assert client.statements == 3


# -------------------------------------------------------------- fleet summary
async def _seed_fleet_summary(s, *, include_history: bool) -> None:  # type: ignore[no-untyped-def]
    """Two classes with complete buckets; history markers are optional."""
    inst = await make_instance(s)
    firewall = await make_log_source(s, inst, qradar_id=11, name="firewall")
    waf = await make_log_source(s, inst, qradar_id=12, name="waf")
    s.add_all([classification(firewall, "FIREWALL"), classification(waf, "WAF")])
    await s.flush()
    markers = (
        ["ariel_history", "ariel_history", "ariel_aggregate"]
        if include_history
        else ["ariel_aggregate"] * 3
    )
    for index, marker in enumerate(markers, start=1):
        await add_metric(
            s,
            firewall,
            CLOSED - timedelta(minutes=5 * index),
            event_count=100,
            collection_source=marker,
        )
    waf_markers = (
        ["ariel_history", "ariel_aggregate", "ariel_aggregate"]
        if include_history
        else ["ariel_aggregate"] * 3
    )
    for index, marker in enumerate(waf_markers, start=1):
        await add_metric(
            s,
            waf,
            CLOSED - timedelta(minutes=5 * index),
            event_count=200,
            collection_source=marker,
        )


def test_the_fleet_summary_counts_complete_buckets_read_from_history_by_class(
    db_schema, _fleet_clock
) -> None:
    run(lambda s: _seed_fleet_summary(s, include_history=True))
    body = Client(READER).get("/api/v1/fleet/summary?window=1h").json()
    classes = {row["source_class"]: row for row in body["classes"]}
    assert classes["FIREWALL"]["buckets"]["complete"] == 3
    assert classes["FIREWALL"]["buckets"]["complete_from_history"] == 2
    assert classes["WAF"]["buckets"]["complete"] == 3
    assert classes["WAF"]["buckets"]["complete_from_history"] == 1


def test_a_live_only_fleet_summary_reports_zero_history_buckets(
    db_schema, _fleet_clock
) -> None:
    run(lambda s: _seed_fleet_summary(s, include_history=False))
    body = Client(READER).get("/api/v1/fleet/summary?window=1h").json()
    classes = {row["source_class"]: row for row in body["classes"]}
    assert classes["FIREWALL"]["buckets"]["complete_from_history"] == 0
    assert classes["WAF"]["buckets"]["complete_from_history"] == 0


def test_the_fleet_summary_statement_count_is_unchanged(db_schema, _fleet_clock) -> None:
    run(lambda s: _seed_fleet_summary(s, include_history=True))
    client = Client(READER)
    resp = client.get("/api/v1/fleet/summary?window=1h")
    assert resp.status_code == 200
    # Measured before HB-21: sources, metrics, baselines, incidents and outage policy.
    assert client.statements == 5


# ------------------------------------------------------------ rule statistics
async def _seed_rule_windows(s, *, history: int, live: int) -> dict:  # type: ignore[no-untyped-def]
    """One instance whose windows are `live` live ones and `history` history ones."""
    inst = await make_instance(s)
    s.add(
        AnalyticsRule(
            instance_id=inst.id, qradar_id=1, name="r1", rule_type="EVENT", enabled=True,
            is_building_block=False, average_capacity=1.0,
        )
    )
    for index in range(live + history):
        origin = "HISTORY" if index < history else "LIVE"
        s.add(
            RuleMatchWindow(
                instance_id=inst.id,
                window_start=STATS_END - timedelta(hours=1) * (index + 1),
                window_seconds=3600, origin=origin,
                status=RuleMatchWindowStatus.COMPLETE, rows_returned=1, row_cap=2000,
                unknown_ids=0, attempts=1, query_version="v1", collected_at=STATS_END,
            )
        )
    await s.flush()
    return {"instance": inst.id}


def test_the_rule_statistics_count_the_history_windows(db_schema, _stats_clock) -> None:
    run(lambda s: _seed_rule_windows(s, history=6, live=18))
    resp = Client(READER).get("/api/v1/rules/statistics", params={"days": 1, "top": 5})
    assert resp.status_code == 200
    window = resp.json()["window"]
    assert window["complete_windows"] == 24
    assert window["history_windows"] == 6
    # The rest of the arithmetic is unchanged.
    assert window["expected_windows"] == 24 and window["complete"] is True


def test_the_rule_statistics_report_no_history_window_when_none_was_read(
    db_schema, _stats_clock
) -> None:
    run(lambda s: _seed_rule_windows(s, history=0, live=24))
    resp = Client(READER).get("/api/v1/rules/statistics", params={"days": 1, "top": 5})
    assert resp.status_code == 200
    window = resp.json()["window"]
    assert window["complete_windows"] == 24 and window["history_windows"] == 0


def test_the_rule_statistics_statement_count_is_unchanged(db_schema, _stats_clock) -> None:
    """The marker rides the window rows the coverage already read."""
    run(lambda s: _seed_rule_windows(s, history=6, live=18))
    client = Client(READER)
    resp = client.get("/api/v1/rules/statistics", params={"days": 1, "top": 5})
    assert resp.status_code == 200
    # Measured on the base before this change: 9 statements (the instance
    # lookup, the counts, the coverage pair, the silence list, the two top
    # lists and the two benchmark queries).
    assert client.statements == 9


# ------------------------------------------------------------------ no leaks
def test_no_response_carries_a_raw_value_or_a_query(db_schema) -> None:
    """The marker is the only thing the change adds: no raw value travels."""
    ids = run(_seed_waf)
    resp = Client(READER).get(
        f"/api/v1/behavior/waf/endpoints/{ids['endpoint']}/metrics", params=_window()
    )
    assert "queries" not in resp.text and "provenance" not in resp.text
    assert "source_ip" not in resp.text and "top_clients" not in resp.text


def test_the_marker_is_the_only_new_key_of_a_bucket(db_schema) -> None:
    """The response grows by exactly one field: the marker."""
    ids = run(_seed_ips)
    resp = Client(READER).get(
        f"/api/v1/behavior/ips/sources/{ids['source']}/metrics", params=_window()
    )
    row = resp.json()["items"][0]
    assert set(row) == {
        "bucket_start", "bucket_seconds", "total_events", "total_records", "in_maintenance",
        "origin", "dimensions", "source_events", "distinct_sources",
        "distinct_sources_lower_bound", "top1_events", "top5_events", "max_distinct_ports",
        "blocked_events", "allowed_events", "unmapped_events",
    }


def test_a_phase_a_row_without_a_collection_source_is_live(db_schema) -> None:
    """A NULL marker is live, not unknown: Phase A's live writer sets it."""
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=1, name="src")
        row = LogSourceMetric(
            log_source_id=src.id, bucket_start=CLOSED, bucket_seconds=300, event_count=10,
            average_eps=1.0, completeness=BucketCompleteness.COMPLETE,
        )
        s.add(row)
        await s.flush()
        return {"source": src.id}

    ids = run(setup)
    resp = Client(READER).get(
        f"/api/v1/behavior/sources/{ids['source']}/metrics", params=_window()
    )
    assert resp.status_code == 200
    assert resp.json()[0]["origin"] == "LIVE"


def test_an_unknown_id_is_still_a_404(db_schema) -> None:
    missing = uuid.uuid4()
    assert Client(READER).get(
        f"/api/v1/behavior/sources/{missing}/metrics", params=_window()
    ).status_code == 404
    assert Client(READER).get(
        f"/api/v1/behavior/waf/endpoints/{missing}/metrics", params=_window()
    ).status_code == 404
    assert Client(READER).get(
        f"/api/v1/behavior/ips/entities/{missing}/metrics", params=_window()
    ).status_code == 404
    assert Client(READER).get(
        f"/api/v1/behavior/ips/sources/{missing}/metrics", params=_window()
    ).status_code == 404
