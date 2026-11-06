"""TOP-1a traffic ranking over HTTP: WAF endpoints and IPS destination IPs.

Every test seeds committed rows and reads them back through the API, so the
assertions see exactly what an analyst's client sees. The window is anchored to
a fixed newest bucket, not to the clock.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.models.enums import (
    BucketCompleteness,
    IpsDimension,
    IpsPreset,
    IpsProfileValidationStatus,
    WafMeasurement,
    WafProfileValidationStatus,
)
from app.models.ips import IpsEntity, IpsEntityMetric, IpsSourceBucket, IpsSourceProfile
from app.models.waf import WafEndpoint, WafEndpointMetric, WafSourceBucket, WafSourceProfile
from app.security.rbac import Principal
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_fleet_api import Client, run

pytestmark = pytest.mark.integration

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
MISSING = "00000000-0000-0000-0000-000000000000"

#: The newest complete bucket; every window ends at its end.
T0 = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)
BUCKET = 300
NEWEST_END = T0 + timedelta(seconds=BUCKET)
#: One hour at the seeded bucket width.
HOUR = 12


def at(index: int) -> datetime:
    """Bucket `index` counting backwards from the newest one."""
    return T0 - timedelta(seconds=BUCKET * index)


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def url_waf(source_id) -> str:  # type: ignore[no-untyped-def]
    return f"/api/v1/behavior/waf/sources/{source_id}/traffic"


def url_ips(source_id) -> str:  # type: ignore[no-untyped-def]
    return f"/api/v1/behavior/ips/sources/{source_id}/traffic"


# ------------------------------------------------------------------- WAF seeds
def waf_profile(instance_id, source_id, **kw):  # type: ignore[no-untyped-def]
    defaults = dict(
        instance_id=instance_id, log_source_id=source_id, enabled=True, profile_version=2,
        url_property="URL", method_property="Method", product_label="lab-waf",
        validation_status=WafProfileValidationStatus.VALID, validated_profile_version=2,
        validated_at=T0, validation_report={},
    )
    defaults.update(kw)
    return WafSourceProfile(**defaults)


def waf_endpoint(
    instance_id, source_id, *, host, path, method="GET", active=True, **kw
):  # type: ignore[no-untyped-def]
    defaults = dict(
        instance_id=instance_id, log_source_id=source_id, virtual_host=host,
        normalized_endpoint=path, http_method=method,
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        first_seen_at=at(24 * 40), last_seen_at=T0, tracked_since=at(24 * 40),
        is_active=active, retired_at=None if active else T0 + timedelta(days=1),
    )
    defaults.update(kw)
    return WafEndpoint(**defaults)


def waf_bucket(source_id, index, **kw):  # type: ignore[no-untyped-def]
    defaults = dict(
        log_source_id=source_id, bucket_start=at(index), bucket_seconds=BUCKET,
        profile_version=2, metric_semantics="sem",
        completeness=BucketCompleteness.COMPLETE,
        endpoint_coverage_ratio=0.97, total_events=5000, parsed_events=4850, group_count=3,
        groups_truncated=False, unusable_groups=0, observed_endpoints=3,
        tracked_endpoints_observed=3, admitted_endpoints=0, below_admission_threshold=0,
        rejected_over_quota=0, untracked_endpoints=2, untracked_requests=50,
        admission_truncated=False, zero_filled_endpoints=0,
        request_count_measurement=WafMeasurement.EXACT, sparse_endpoint_rows=True,
        in_maintenance=False, collection_source="TEST", query_provenance={},
    )
    defaults.update(kw)
    defaults.setdefault("origin", "LIVE")
    return WafSourceBucket(**defaults)


def waf_row(endpoint_id, index, requests, **kw):  # type: ignore[no-untyped-def]
    defaults = dict(
        waf_endpoint_id=endpoint_id, bucket_start=at(index), bucket_seconds=BUCKET,
        request_count=requests, endpoint_coverage_ratio=1.0,
        completeness=BucketCompleteness.COMPLETE,
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
    )
    defaults.update(kw)
    return WafEndpointMetric(**defaults)


async def waf_world(s):  # type: ignore[no-untyped-def]
    """Twelve whole buckets and a set that exercises every ranking rule."""
    inst = await make_instance(s)
    src = await make_log_source(s, inst, qradar_id=1, name="waf-1")
    s.add(waf_profile(inst.id, src.id))
    specs = {
        # key: (host, path, method, active)
        "api": ("api.example.com", "/v1/orders/{id}", "GET", True),
        "shop": ("shop.example.com", "/cart", "POST", True),
        "static": ("static.example.com", "/img/{id}", "GET", False),
        "old": ("old.example.com", "/legacy", "GET", True),
        "health": (None, "/health", "GET", True),
        "norows": ("api.example.com", "/never", "GET", True),
    }
    eps: dict[str, WafEndpoint] = {}
    for key, (host, path, method, active) in specs.items():
        ep = waf_endpoint(inst.id, src.id, host=host, path=path, method=method, active=active)
        s.add(ep)
        eps[key] = ep
    for k in range(22):
        ep = waf_endpoint(
            inst.id, src.id, host="bulk.example.com", path=f"/bulk/{k:02d}"
        )
        s.add(ep)
        eps[f"bulk{k}"] = ep
    await s.flush()
    for i in range(HOUR):
        s.add(waf_bucket(src.id, i))
    for i in range(HOUR):
        s.add(waf_row(eps["api"].id, i, 210 - 10 * i))
        s.add(waf_row(eps["shop"].id, i, 50))
    for i in range(6):
        s.add(waf_row(eps["static"].id, i, 20))
    for i in range(6, HOUR):
        s.add(waf_row(eps["old"].id, i, 10))
    s.add(waf_row(eps["health"].id, 0, 1))
    for k in range(22):
        s.add(waf_row(eps[f"bulk{k}"].id, 0, 5))
    return inst, src, eps


#: The expected totals of `waf_world`'s 12-bucket hour.
WAF_API_EVENTS = sum(210 - 10 * i for i in range(HOUR))  # 1860
WAF_ATTRIBUTED = WAF_API_EVENTS + 12 * 50 + 6 * 20 + 6 * 10 + 1 + 22 * 5
WAF_SOURCE_TOTAL = 12 * 5000
WAF_UNTRACKED = 12 * 50
WAF_MEASURED_SECONDS = HOUR * BUCKET


def waf_ids(body: dict) -> list[str]:
    return [item["id"] for item in body["items"]]


# ------------------------------------------------------------------- IPS seeds
def ips_profile(instance_id, source_id, **kw):  # type: ignore[no-untyped-def]
    defaults = dict(
        instance_id=instance_id, log_source_id=source_id, enabled=True, profile_version=2,
        product_label="lab-ips", preset=IpsPreset.CISCO_FTD, mapping={}, action_value_map={},
        validation_status=IpsProfileValidationStatus.VALID, validated_profile_version=2,
        validated_at=T0, validation_report={},
    )
    defaults.update(kw)
    return IpsSourceProfile(**defaults)


def ips_entity(
    instance_id, source_id, value_key, *, dimension=IpsDimension.DESTINATION_IP,
    active=True, display_name=None, **kw,
):  # type: ignore[no-untyped-def]
    defaults = dict(
        instance_id=instance_id, log_source_id=source_id, dimension=dimension,
        value_key=value_key, display_name=display_name,
        first_seen_at=at(24 * 40), last_seen_at=T0, active=active,
        retired_at=None if active else T0 + timedelta(days=1),
        normalizer_version=1,
    )
    defaults.update(kw)
    return IpsEntity(**defaults)


def ips_bucket(source_id, index, **kw):  # type: ignore[no-untyped-def]
    defaults = dict(
        log_source_id=source_id, bucket_start=at(index), bucket_seconds=BUCKET,
        total_events=4000, total_records=4100,
        destination_port_measurement="EXACT", destination_port_coverage_ratio=0.98,
        destination_ip_measurement="EXACT", destination_ip_coverage_ratio=0.99,
        signature_measurement="EXACT", signature_coverage_ratio=0.97,
        source_ip_measurement="EXACT", source_ip_coverage_ratio=1.0,
        port_fanout_measurement="EXACT",
        action_measurement="NOT_COLLECTED",
        source_events=3900, distinct_sources=40, top1_events=700, top5_events=1200,
        max_distinct_ports=12, contributors={},
        admitted={"DESTINATION_PORT": 30, "DESTINATION_IP": 10, "SIGNATURE": 5},
        below_admission_threshold={}, rejected_over_quota={},
        untracked_entities={}, untracked_events={"DESTINATION_IP": 7},
        admission_truncated={
            "DESTINATION_PORT": False, "DESTINATION_IP": False, "SIGNATURE": False
        },
        profile_version=2, normalizer_version=1, in_maintenance=False,
        collected_at=at(index), query_provenance={},
    )
    defaults.update(kw)
    defaults.setdefault("origin", "LIVE")
    return IpsSourceBucket(**defaults)


def ips_row(entity_id, index, events):  # type: ignore[no-untyped-def]
    return IpsEntityMetric(
        ips_entity_id=entity_id, bucket_start=at(index), events=events,
        bucket_seconds=BUCKET,
    )


async def ips_world(s):  # type: ignore[no-untyped-def]
    """Twelve whole buckets of destination-IP entities, plus noise to ignore."""
    inst = await make_instance(s)
    src = await make_log_source(s, inst, qradar_id=2, name="ips-1")
    s.add(ips_profile(inst.id, src.id))
    specs = {
        "web": ("10.20.30.40", "web-1", True),
        "db": ("10.20.30.41", None, True),
        "retired": ("192.168.1.9", "old-host", False),
        "old": ("10.20.30.42", None, True),
        "norows": ("10.20.30.43", None, True),
    }
    ents: dict[str, IpsEntity] = {}
    for key, (value, display, active) in specs.items():
        ent = ips_entity(inst.id, src.id, value, active=active, display_name=display)
        s.add(ent)
        ents[key] = ent
    port = ips_entity(
        inst.id, src.id, "443/tcp", dimension=IpsDimension.DESTINATION_PORT
    )
    s.add(port)
    ents["port"] = port
    for k in range(22):
        ent = ips_entity(inst.id, src.id, f"10.20.40.{k + 1}")
        s.add(ent)
        ents[f"bulk{k}"] = ent
    await s.flush()
    for i in range(HOUR):
        s.add(ips_bucket(src.id, i))
    for i in range(HOUR):
        s.add(ips_row(ents["web"].id, i, 255 - 5 * i))
        s.add(ips_row(ents["db"].id, i, 40))
        s.add(ips_row(ents["port"].id, i, 500))
    for i in range(4):
        s.add(ips_row(ents["retired"].id, i, 10))
    for i in range(6, HOUR):
        s.add(ips_row(ents["old"].id, i, 10))
    for k in range(22):
        s.add(ips_row(ents[f"bulk{k}"].id, 0, 5))
    return inst, src, ents


IPS_WEB_EVENTS = sum(255 - 5 * i for i in range(HOUR))  # 2730
IPS_ATTRIBUTED = IPS_WEB_EVENTS + 12 * 40 + 4 * 10 + 6 * 10 + 22 * 5
IPS_SOURCE_TOTAL = 12 * 4000
IPS_UNTRACKED = 12 * 7
IPS_MEASURED_SECONDS = HOUR * BUCKET


def ips_ids(body: dict) -> list[str]:
    return [item["id"] for item in body["items"]]


# ------------------------------------------------------------------ WAF reads
def test_waf_each_window_reports_its_expected_buckets_and_range(db_schema) -> None:
    _inst, src, _eps = run(waf_world)
    client = Client(READER)
    expectations = {
        "5m": (1, 1, "OK"),
        "1h": (12, 12, "OK"),
        "24h": (288, 12, "PARTIAL"),
        "7d": (2016, 12, "PARTIAL"),
        "28d": (8064, 12, "PARTIAL"),
    }
    for window, (expected, measured, state) in expectations.items():
        body = client.get(url_waf(src.id), params={"window": window}).json()
        assert body["window"] == window
        assert body["bucket_seconds"] == BUCKET
        assert body["buckets_expected"] == expected
        assert body["buckets_measured"] == measured
        assert body["state"] == state
        assert parse(body["range_end"]) == NEWEST_END
        assert parse(body["range_start"]) == NEWEST_END - timedelta(
            seconds=expected * BUCKET
        )
        # The rate divides by the measured seconds, never by the wall-clock range.
        api = body["items"][0]
        api_events = 210 if window == "5m" else WAF_API_EVENTS
        assert api["rate_per_second"] == pytest.approx(
            api_events / (measured * BUCKET)
        )
        if state == "OK":
            assert body["reason_code"] is None
        else:
            assert body["reason_code"] == "waf_traffic_detail.reason.buckets_missing"
    # 5m is one bucket, not five minutes of anything else.
    five = client.get(url_waf(src.id), params={"window": "5m"}).json()
    assert five["buckets_measured"] == 1


def test_waf_five_minutes_is_the_newest_bucket_only(db_schema) -> None:
    _inst, src, eps = run(waf_world)
    body = Client(READER).get(url_waf(src.id), params={"window": "5m"}).json()
    ids = waf_ids(body)
    assert str(eps["old"].id) not in ids
    assert str(eps["norows"].id) not in ids
    api = next(item for item in body["items"] if item["id"] == str(eps["api"].id))
    assert api["events"] == 210
    assert api["buckets_with_data"] == 1
    assert body["source_total_events"] == 5000
    assert body["unattributed_events"] == 50


def test_waf_items_carry_rate_peak_and_identity(db_schema) -> None:
    _inst, src, eps = run(waf_world)
    body = Client(READER).get(url_waf(src.id), params={"limit": 100}).json()
    assert body["state"] == "OK"
    assert body["total"] == 27
    api = next(item for item in body["items"] if item["id"] == str(eps["api"].id))
    assert api["virtual_host"] == "api.example.com"
    assert api["http_method"] == "GET"
    assert api["normalized_endpoint"] == "/v1/orders/{id}"
    assert api["events"] == WAF_API_EVENTS == 1860
    assert api["rate_per_second"] == pytest.approx(1860 / WAF_MEASURED_SECONDS)
    assert api["peak_rate_per_second"] == pytest.approx(210 / BUCKET)
    assert parse(api["peak_bucket_start"]) == T0
    assert api["buckets_with_data"] == 12
    assert api["partial_buckets"] == 0
    assert api["active"] is True
    assert body["max_rate_per_second"] == pytest.approx(1860 / WAF_MEASURED_SECONDS)


def test_waf_retired_endpoint_is_listed_and_an_empty_one_is_not(db_schema) -> None:
    _inst, src, eps = run(waf_world)
    body = Client(READER).get(url_waf(src.id), params={"limit": 100}).json()
    ids = waf_ids(body)
    static = next(item for item in body["items"] if item["id"] == str(eps["static"].id))
    assert static["active"] is False
    assert static["events"] == 120
    assert str(eps["norows"].id) not in ids


def test_waf_totals_and_the_unattributed_share(db_schema) -> None:
    _inst, src, _eps = run(waf_world)
    body = Client(READER).get(url_waf(src.id), params={"limit": 100}).json()
    assert body["source_total_events"] == WAF_SOURCE_TOTAL == 60000
    assert body["unattributed_events"] == WAF_UNTRACKED == 600
    assert body["attributed_events"] == WAF_ATTRIBUTED


def test_waf_max_rate_comes_from_the_whole_set_not_the_page(db_schema) -> None:
    _inst, src, eps = run(waf_world)
    body = Client(READER).get(
        url_waf(src.id), params={"sort": "endpoint", "order": "asc", "limit": 1}
    ).json()
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] != str(eps["api"].id)
    assert body["max_rate_per_second"] == pytest.approx(1860 / WAF_MEASURED_SECONDS)


@pytest.mark.parametrize("sort", ["rate", "peak", "host", "endpoint"])
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_waf_each_sort_and_order_is_server_side(db_schema, sort: str, order: str) -> None:
    _inst, src, _eps = run(waf_world)
    body = Client(READER).get(
        url_waf(src.id), params={"sort": sort, "order": order, "limit": 100}
    ).json()
    assert body["sort"] == sort and body["order"] == order
    items = body["items"]
    assert len(items) == body["total"] == 27
    if sort == "rate":
        values = [item["rate_per_second"] for item in items]
    elif sort == "peak":
        values = [item["peak_rate_per_second"] for item in items]
    elif sort == "host":
        # Hostless endpoints have no text to sort on; they go last both ways.
        values = [item["virtual_host"] or "" for item in items]
        assert items[-1]["virtual_host"] is None
        values = values[:-1]
    else:
        values = [item["normalized_endpoint"] for item in items]
    assert values == sorted(values, reverse=(order == "desc"))


@pytest.mark.parametrize("sort", ["rate", "peak", "host", "endpoint"])
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_waf_pages_are_slices_of_the_whole_sorted_list(
    db_schema, sort: str, order: str
) -> None:
    _inst, src, _eps = run(waf_world)
    client = Client(READER)
    params = {"sort": sort, "order": order}
    first = client.get(url_waf(src.id), params={**params, "limit": 10}).json()
    second = client.get(
        url_waf(src.id), params={**params, "limit": 10, "offset": 10}
    ).json()
    twenty = client.get(url_waf(src.id), params={**params, "limit": 20}).json()
    paged = waf_ids(first) + waf_ids(second)
    assert paged == waf_ids(twenty)
    assert len(set(paged)) == len(paged) == 20
    assert first["total"] == second["total"] == twenty["total"] == 27


def test_waf_offset_past_the_end_still_reports_the_total(db_schema) -> None:
    _inst, src, _eps = run(waf_world)
    body = Client(READER).get(url_waf(src.id), params={"limit": 10, "offset": 30}).json()
    assert body["items"] == []
    assert body["total"] == 27
    assert body["max_rate_per_second"] == pytest.approx(1860 / WAF_MEASURED_SECONDS)


def test_waf_a_missing_bucket_is_partial_and_its_rows_are_not_counted(db_schema) -> None:
    _inst, src, eps = run(waf_world)

    async def drop_one_bucket(s):  # type: ignore[no-untyped-def]
        await s.execute(
            delete(WafSourceBucket).where(
                WafSourceBucket.log_source_id == src.id,
                WafSourceBucket.bucket_start == at(5),
            )
        )

    run(drop_one_bucket)
    body = Client(READER).get(url_waf(src.id), params={"limit": 100}).json()
    assert body["state"] == "PARTIAL"
    assert body["reason_code"] == "waf_traffic_detail.reason.buckets_missing"
    assert body["buckets_measured"] == 11
    assert body["buckets_expected"] == 12
    assert body["source_total_events"] == 11 * 5000
    assert body["unattributed_events"] == 11 * 50
    api = next(item for item in body["items"] if item["id"] == str(eps["api"].id))
    # The row left behind has no bucket record, so it is not a measurement.
    assert api["buckets_with_data"] == 11
    assert api["events"] == 1860 - 160


def test_waf_a_partial_measured_bucket_is_partial(db_schema) -> None:
    _inst, src, eps = run(waf_world)

    async def make_partial(s):  # type: ignore[no-untyped-def]
        bucket = await s.get(WafSourceBucket, (src.id, at(5)))
        assert bucket is not None
        bucket.completeness = BucketCompleteness.PARTIAL
        bucket.request_count_measurement = WafMeasurement.LOWER_BOUND
        row = await s.get(WafEndpointMetric, (eps["api"].id, at(5)))
        assert row is not None
        row.completeness = BucketCompleteness.PARTIAL

    run(make_partial)
    body = Client(READER).get(url_waf(src.id), params={"limit": 100}).json()
    assert body["state"] == "PARTIAL"
    assert body["reason_code"] == "waf_traffic_detail.reason.bucket_partial"
    assert body["buckets_measured"] == 12
    api = next(item for item in body["items"] if item["id"] == str(eps["api"].id))
    assert api["partial_buckets"] == 1
    assert api["events"] == 1860
    assert api["buckets_with_data"] == 12


def test_waf_a_truncated_bucket_is_truncated(db_schema) -> None:
    _inst, src, _eps = run(waf_world)

    async def truncate(s):  # type: ignore[no-untyped-def]
        bucket = await s.get(WafSourceBucket, (src.id, at(5)))
        assert bucket is not None
        bucket.groups_truncated = True
        bucket.completeness = BucketCompleteness.PARTIAL
        bucket.request_count_measurement = WafMeasurement.LOWER_BOUND

    run(truncate)
    body = Client(READER).get(url_waf(src.id)).json()
    assert body["state"] == "TRUNCATED"
    assert body["reason_code"] == "waf_traffic_detail.reason.truncated"
    assert body["buckets_measured"] == 12


def test_waf_without_a_complete_bucket_is_insufficient_data(db_schema) -> None:
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=1, name="waf-1")
        s.add(waf_profile(inst.id, src.id))
        ep = waf_endpoint(inst.id, src.id, host="api.example.com", path="/v1")
        s.add(ep)
        await s.flush()
        s.add(
            waf_bucket(
                src.id, 0,
                completeness=BucketCompleteness.PARTIAL,
                request_count_measurement=WafMeasurement.LOWER_BOUND,
            )
        )
        s.add(waf_row(ep.id, 0, 10, completeness=BucketCompleteness.PARTIAL))
        return src

    src = run(seed)
    body = Client(READER).get(url_waf(src.id)).json()
    assert body["state"] == "INSUFFICIENT_DATA"
    assert body["reason_code"] == "waf_traffic_detail.reason.no_complete_bucket"
    assert body["items"] == [] and body["total"] == 0
    assert body["range_start"] is None and body["range_end"] is None
    assert body["bucket_seconds"] is None
    assert body["source_total_events"] is None
    assert body["max_rate_per_second"] is None


@pytest.mark.parametrize(
    "profile_kw",
    [
        pytest.param({"enabled": False}, id="disabled"),
        pytest.param(
            {"validation_status": WafProfileValidationStatus.UNVALIDATED}, id="unvalidated"
        ),
        pytest.param({"validated_profile_version": 1}, id="stale-version"),
    ],
)
def test_waf_a_source_that_is_not_collected_is_not_collected_200(
    db_schema, profile_kw: dict
) -> None:
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=1, name="waf-1")
        s.add(waf_profile(inst.id, src.id, **profile_kw))
        return src

    src = run(seed)
    body = Client(READER).get(url_waf(src.id))
    assert body.status_code == 200
    payload = body.json()
    assert payload["state"] == "NOT_COLLECTED"
    assert payload["reason_code"] == "waf_traffic_detail.reason.not_collected"
    assert payload["items"] == [] and payload["total"] == 0
    assert payload["range_end"] is None and payload["buckets_measured"] == 0


def test_waf_a_measured_window_with_no_endpoint_rows_is_ok_and_empty(db_schema) -> None:
    """A whole window with no endpoint rows is a measured empty, not a failure:
    the unattributed count explains where the traffic went."""

    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=1, name="waf-1")
        s.add(waf_profile(inst.id, src.id))
        await s.flush()
        for i in range(HOUR):
            s.add(waf_bucket(src.id, i))
        return src

    src = run(seed)
    body = Client(READER).get(url_waf(src.id)).json()
    assert body["state"] == "OK"
    assert body["reason_code"] is None
    assert body["buckets_measured"] == 12
    assert body["total"] == 0
    assert body["items"] == []
    assert body["attributed_events"] == 0
    assert body["source_total_events"] == WAF_SOURCE_TOTAL
    assert body["unattributed_events"] == WAF_UNTRACKED
    assert body["max_rate_per_second"] is None


def test_waf_a_source_without_a_profile_is_not_collected_not_404(db_schema) -> None:
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        return await make_log_source(s, inst, qradar_id=1, name="plain")

    src = run(seed)
    response = Client(READER).get(url_waf(src.id))
    assert response.status_code == 200
    assert response.json()["state"] == "NOT_COLLECTED"
    assert response.json()["reason_code"] == "waf_traffic_detail.reason.not_collected"


def test_waf_unknown_source_is_404(db_schema) -> None:
    assert Client(READER).get(url_waf(MISSING)).status_code == 404


@pytest.mark.parametrize(
    "params",
    [
        {"window": "2h"},
        {"window": "5"},
        {"sort": "ip"},
        {"order": "up"},
        {"limit": 1001},
        {"limit": 0},
        {"offset": -1},
    ],
)
def test_waf_invalid_queries_are_422(db_schema, params: dict) -> None:
    _inst, src, _eps = run(waf_world)
    assert Client(READER).get(url_waf(src.id), params=params).status_code == 422


def test_waf_statement_count_is_constant_at_200_endpoints(db_schema) -> None:
    async def seed_one(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=1, name="waf-1")
        s.add(waf_profile(inst.id, src.id))
        ep = waf_endpoint(inst.id, src.id, host="api.example.com", path="/v1")
        s.add(ep)
        await s.flush()
        for i in range(HOUR):
            s.add(waf_bucket(src.id, i))
            s.add(waf_row(ep.id, i, 10))
        return inst, src

    inst, src = run(seed_one)
    client = Client(READER)
    assert client.get(url_waf(src.id), params={"limit": 10}).status_code == 200
    small = client.statements

    async def grow(s):  # type: ignore[no-untyped-def]
        for k in range(199):
            ep = waf_endpoint(inst.id, src.id, host="bulk.example.com", path=f"/b/{k}")
            s.add(ep)
            await s.flush()
            s.add(waf_row(ep.id, 0, 5))

    run(grow)
    assert client.get(url_waf(src.id), params={"limit": 10}).status_code == 200
    assert client.statements == small, (small, client.statements)
    # A page of one and a page of two hundred are the same statements: the
    # series is one more statement for the page's ids, never one per item.
    assert client.get(url_waf(src.id), params={"limit": 1}).status_code == 200
    assert client.statements == small, ("limit=1", small, client.statements)
    assert client.get(url_waf(src.id), params={"limit": 200}).status_code == 200
    assert client.statements == small, ("limit=200", small, client.statements)
    # Pinned so a per-entity query cannot hide behind "still constant".
    assert small == 5


# ------------------------------------------------------------------ IPS reads
def test_ips_each_window_reports_its_expected_buckets_and_range(db_schema) -> None:
    _inst, src, _ents = run(ips_world)
    client = Client(READER)
    expectations = {
        "5m": (1, 1, "OK"),
        "1h": (12, 12, "OK"),
        "24h": (288, 12, "PARTIAL"),
        "7d": (2016, 12, "PARTIAL"),
        "28d": (8064, 12, "PARTIAL"),
    }
    for window, (expected, measured, state) in expectations.items():
        body = client.get(url_ips(src.id), params={"window": window}).json()
        assert body["window"] == window
        assert body["bucket_seconds"] == BUCKET
        assert body["buckets_expected"] == expected
        assert body["buckets_measured"] == measured
        assert body["state"] == state
        assert parse(body["range_end"]) == NEWEST_END
        assert parse(body["range_start"]) == NEWEST_END - timedelta(
            seconds=expected * BUCKET
        )
        web = body["items"][0]
        web_events = 255 if window == "5m" else IPS_WEB_EVENTS
        assert web["rate_per_second"] == pytest.approx(web_events / (measured * BUCKET))


def test_ips_five_minutes_is_the_newest_bucket_only(db_schema) -> None:
    _inst, src, ents = run(ips_world)
    body = Client(READER).get(url_ips(src.id), params={"window": "5m"}).json()
    ids = ips_ids(body)
    assert str(ents["old"].id) not in ids
    assert str(ents["norows"].id) not in ids
    assert str(ents["port"].id) not in ids
    web = next(item for item in body["items"] if item["id"] == str(ents["web"].id))
    assert web["events"] == 255
    assert web["buckets_with_data"] == 1


def test_ips_items_cover_destination_ips_only_and_carry_rate_and_peak(db_schema) -> None:
    _inst, src, ents = run(ips_world)
    body = Client(READER).get(url_ips(src.id), params={"limit": 100}).json()
    assert body["state"] == "OK"
    assert body["total"] == 26
    ids = ips_ids(body)
    # A DESTINATION_PORT entity is never part of this ranking.
    assert str(ents["port"].id) not in ids
    assert str(ents["norows"].id) not in ids
    web = next(item for item in body["items"] if item["id"] == str(ents["web"].id))
    assert web["destination_ip"] == "10.20.30.40"
    assert web["display_name"] == "web-1"
    assert web["http_method"] is None and web["normalized_endpoint"] is None
    assert web["events"] == IPS_WEB_EVENTS == 2730
    assert web["rate_per_second"] == pytest.approx(2730 / IPS_MEASURED_SECONDS)
    assert web["peak_rate_per_second"] == pytest.approx(255 / BUCKET)
    assert parse(web["peak_bucket_start"]) == T0
    assert web["buckets_with_data"] == 12
    assert web["active"] is True


def test_ips_retired_entity_is_listed_and_totals_are_the_whole_set(db_schema) -> None:
    _inst, src, ents = run(ips_world)
    body = Client(READER).get(url_ips(src.id), params={"limit": 100}).json()
    retired = next(
        item for item in body["items"] if item["id"] == str(ents["retired"].id)
    )
    assert retired["active"] is False
    assert retired["destination_ip"] == "192.168.1.9"
    assert body["source_total_events"] == IPS_SOURCE_TOTAL == 48000
    assert body["unattributed_events"] == IPS_UNTRACKED == 84
    assert body["attributed_events"] == IPS_ATTRIBUTED


def test_ips_max_rate_comes_from_the_whole_set_not_the_page(db_schema) -> None:
    _inst, src, _ents = run(ips_world)
    body = Client(READER).get(
        url_ips(src.id), params={"sort": "ip", "order": "desc", "limit": 1}
    ).json()
    assert body["items"][0]["destination_ip"] == "192.168.1.9"
    assert body["max_rate_per_second"] == pytest.approx(IPS_WEB_EVENTS / IPS_MEASURED_SECONDS)


@pytest.mark.parametrize("sort", ["rate", "peak", "ip"])
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_ips_each_sort_and_order_is_server_side(db_schema, sort: str, order: str) -> None:
    _inst, src, _ents = run(ips_world)
    body = Client(READER).get(
        url_ips(src.id), params={"sort": sort, "order": order, "limit": 100}
    ).json()
    assert body["sort"] == sort and body["order"] == order
    items = body["items"]
    assert len(items) == body["total"] == 26
    if sort == "ip":
        values = [item["destination_ip"] for item in items]
    elif sort == "peak":
        values = [item["peak_rate_per_second"] for item in items]
    else:
        values = [item["rate_per_second"] for item in items]
    assert values == sorted(values, reverse=(order == "desc"))


@pytest.mark.parametrize("sort", ["rate", "peak", "ip"])
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_ips_pages_are_slices_of_the_whole_sorted_list(
    db_schema, sort: str, order: str
) -> None:
    _inst, src, _ents = run(ips_world)
    client = Client(READER)
    params = {"sort": sort, "order": order}
    first = client.get(url_ips(src.id), params={**params, "limit": 10}).json()
    second = client.get(
        url_ips(src.id), params={**params, "limit": 10, "offset": 10}
    ).json()
    twenty = client.get(url_ips(src.id), params={**params, "limit": 20}).json()
    paged = ips_ids(first) + ips_ids(second)
    assert paged == ips_ids(twenty)
    assert len(set(paged)) == len(paged) == 20
    assert first["total"] == second["total"] == twenty["total"] == 26


def test_ips_a_missing_bucket_is_partial(db_schema) -> None:
    _inst, src, ents = run(ips_world)

    async def drop_one_bucket(s):  # type: ignore[no-untyped-def]
        await s.execute(
            delete(IpsSourceBucket).where(
                IpsSourceBucket.log_source_id == src.id,
                IpsSourceBucket.bucket_start == at(5),
            )
        )

    run(drop_one_bucket)
    body = Client(READER).get(url_ips(src.id), params={"limit": 100}).json()
    assert body["state"] == "PARTIAL"
    assert body["reason_code"] == "ips_traffic_detail.reason.buckets_missing"
    assert body["buckets_measured"] == 11
    web = next(item for item in body["items"] if item["id"] == str(ents["web"].id))
    assert web["buckets_with_data"] == 11
    assert web["events"] == IPS_WEB_EVENTS - 230


def test_ips_an_unmeasured_destination_ip_bucket_is_not_counted(db_schema) -> None:
    _inst, src, ents = run(ips_world)

    async def unmeasure(s):  # type: ignore[no-untyped-def]
        bucket = await s.get(IpsSourceBucket, (src.id, at(5)))
        assert bucket is not None
        bucket.destination_ip_measurement = "NOT_COLLECTED"

    run(unmeasure)
    body = Client(READER).get(url_ips(src.id), params={"limit": 100}).json()
    assert body["state"] == "PARTIAL"
    assert body["buckets_measured"] == 11
    assert body["source_total_events"] == 11 * 4000
    web = next(item for item in body["items"] if item["id"] == str(ents["web"].id))
    assert web["events"] == IPS_WEB_EVENTS - 230


def test_ips_a_lower_bound_bucket_is_partial_and_marks_its_rows(db_schema) -> None:
    _inst, src, ents = run(ips_world)

    async def lower_bound(s):  # type: ignore[no-untyped-def]
        bucket = await s.get(IpsSourceBucket, (src.id, at(5)))
        assert bucket is not None
        bucket.destination_ip_measurement = "LOWER_BOUND"

    run(lower_bound)
    body = Client(READER).get(url_ips(src.id), params={"limit": 100}).json()
    assert body["state"] == "PARTIAL"
    assert body["reason_code"] == "ips_traffic_detail.reason.bucket_partial"
    assert body["buckets_measured"] == 12
    web = next(item for item in body["items"] if item["id"] == str(ents["web"].id))
    assert web["partial_buckets"] == 1


def test_ips_a_bucket_below_the_coverage_floor_is_partial(db_schema) -> None:
    """`app.ips.inputs.entity_count`: an exact search under the floor cannot
    prove that an absent entity had zero events, so the bucket is a lower bound."""
    _inst, src, ents = run(ips_world)

    async def under_covered(s):  # type: ignore[no-untyped-def]
        bucket = await s.get(IpsSourceBucket, (src.id, at(5)))
        assert bucket is not None
        bucket.destination_ip_coverage_ratio = 0.5

    run(under_covered)
    body = Client(READER).get(url_ips(src.id), params={"limit": 100}).json()
    assert body["state"] == "PARTIAL"
    assert body["reason_code"] == "ips_traffic_detail.reason.bucket_partial"
    assert body["buckets_measured"] == 12
    web = next(item for item in body["items"] if item["id"] == str(ents["web"].id))
    assert web["partial_buckets"] == 1


def test_ips_a_truncated_bucket_is_truncated(db_schema) -> None:
    _inst, src, _ents = run(ips_world)

    async def truncate(s):  # type: ignore[no-untyped-def]
        bucket = await s.get(IpsSourceBucket, (src.id, at(5)))
        assert bucket is not None
        bucket.admission_truncated = {
            "DESTINATION_PORT": False, "DESTINATION_IP": True, "SIGNATURE": False
        }

    run(truncate)
    body = Client(READER).get(url_ips(src.id)).json()
    assert body["state"] == "TRUNCATED"
    assert body["reason_code"] == "ips_traffic_detail.reason.truncated"


def test_ips_an_unknown_untracked_count_leaves_unattributed_null(db_schema) -> None:
    _inst, src, _ents = run(ips_world)

    async def drop_key(s):  # type: ignore[no-untyped-def]
        bucket = await s.get(IpsSourceBucket, (src.id, at(5)))
        assert bucket is not None
        bucket.untracked_events = {}

    run(drop_key)
    body = Client(READER).get(url_ips(src.id)).json()
    assert body["unattributed_events"] is None
    assert body["source_total_events"] == IPS_SOURCE_TOTAL


def test_ips_without_a_bucket_is_insufficient_data(db_schema) -> None:
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=2, name="ips-1")
        s.add(ips_profile(inst.id, src.id))
        return src

    src = run(seed)
    body = Client(READER).get(url_ips(src.id)).json()
    assert body["state"] == "INSUFFICIENT_DATA"
    assert body["reason_code"] == "ips_traffic_detail.reason.no_complete_bucket"
    assert body["items"] == [] and body["total"] == 0
    assert body["range_start"] is None and body["range_end"] is None
    assert body["bucket_seconds"] is None


def test_ips_a_window_without_a_usable_destination_ip_bucket_is_insufficient(
    db_schema,
) -> None:
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=2, name="ips-1")
        s.add(ips_profile(inst.id, src.id))
        s.add(ips_bucket(src.id, 0, destination_ip_measurement="NOT_COLLECTED"))
        return src

    src = run(seed)
    body = Client(READER).get(url_ips(src.id)).json()
    assert body["state"] == "INSUFFICIENT_DATA"
    assert body["reason_code"] == "ips_traffic_detail.reason.no_usable_bucket"
    # The anchor still defines the window, so the client can show which range
    # was looked at; only the measurements are missing.
    assert parse(body["range_end"]) == NEWEST_END
    assert body["bucket_seconds"] == BUCKET
    assert body["buckets_expected"] == 12
    assert body["buckets_measured"] == 0
    assert body["items"] == [] and body["total"] == 0
    assert body["source_total_events"] is None
    assert body["max_rate_per_second"] is None


@pytest.mark.parametrize(
    "profile_kw",
    [
        pytest.param({"enabled": False}, id="disabled"),
        pytest.param(
            {"validation_status": IpsProfileValidationStatus.UNVALIDATED}, id="unvalidated"
        ),
        pytest.param({"validated_profile_version": 1}, id="stale-version"),
    ],
)
def test_ips_a_source_that_is_not_collected_is_not_collected_200(
    db_schema, profile_kw: dict
) -> None:
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=2, name="ips-1")
        s.add(ips_profile(inst.id, src.id, **profile_kw))
        return src

    src = run(seed)
    response = Client(READER).get(url_ips(src.id))
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "NOT_COLLECTED"
    assert payload["reason_code"] == "ips_traffic_detail.reason.not_collected"
    assert payload["items"] == [] and payload["total"] == 0


def test_ips_a_measured_window_with_no_entity_rows_is_ok_and_empty(db_schema) -> None:
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=2, name="ips-1")
        s.add(ips_profile(inst.id, src.id))
        await s.flush()
        for i in range(HOUR):
            s.add(ips_bucket(src.id, i))
        return src

    src = run(seed)
    body = Client(READER).get(url_ips(src.id)).json()
    assert body["state"] == "OK"
    assert body["reason_code"] is None
    assert body["buckets_measured"] == 12
    assert body["total"] == 0
    assert body["items"] == []
    assert body["attributed_events"] == 0
    assert body["source_total_events"] == IPS_SOURCE_TOTAL
    assert body["unattributed_events"] == IPS_UNTRACKED
    assert body["max_rate_per_second"] is None


def test_ips_a_source_without_a_profile_is_not_collected_not_404(db_schema) -> None:
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        return await make_log_source(s, inst, qradar_id=2, name="plain")

    src = run(seed)
    response = Client(READER).get(url_ips(src.id))
    assert response.status_code == 200
    assert response.json()["state"] == "NOT_COLLECTED"


def test_ips_unknown_source_is_404(db_schema) -> None:
    assert Client(READER).get(url_ips(MISSING)).status_code == 404


@pytest.mark.parametrize(
    "params",
    [
        {"window": "2h"},
        {"sort": "host"},
        {"sort": "endpoint"},
        {"order": "up"},
        {"limit": 1001},
        {"limit": 0},
        {"offset": -1},
    ],
)
def test_ips_invalid_queries_are_422(db_schema, params: dict) -> None:
    _inst, src, _ents = run(ips_world)
    assert Client(READER).get(url_ips(src.id), params=params).status_code == 422


def test_ips_statement_count_is_constant_at_200_entities(db_schema) -> None:
    async def seed_one(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=2, name="ips-1")
        s.add(ips_profile(inst.id, src.id))
        ent = ips_entity(inst.id, src.id, "10.20.30.40")
        s.add(ent)
        await s.flush()
        for i in range(HOUR):
            s.add(ips_bucket(src.id, i))
            s.add(ips_row(ent.id, i, 10))
        return inst, src

    inst, src = run(seed_one)
    client = Client(READER)
    assert client.get(url_ips(src.id), params={"limit": 10}).status_code == 200
    small = client.statements

    async def grow(s):  # type: ignore[no-untyped-def]
        for k in range(199):
            ent = ips_entity(inst.id, src.id, f"10.21.0.{k + 1}")
            s.add(ent)
            await s.flush()
            s.add(ips_row(ent.id, 0, 5))

    run(grow)
    assert client.get(url_ips(src.id), params={"limit": 10}).status_code == 200
    assert client.statements == small, (small, client.statements)
    assert client.get(url_ips(src.id), params={"limit": 1}).status_code == 200
    assert client.statements == small, ("limit=1", small, client.statements)
    assert client.get(url_ips(src.id), params={"limit": 200}).status_code == 200
    assert client.statements == small, ("limit=200", small, client.statements)
    assert small == 5


def test_the_two_routes_are_on_the_behavior_router_with_anomaly_read(db_schema) -> None:
    _inst, wsrc, _eps = run(waf_world)
    _inst2, isrc, _ents = run(ips_world)
    no_permission = Client(Principal(subject="nobody", permissions=frozenset()))
    assert no_permission.get(url_waf(wsrc.id)).status_code == 403
    assert no_permission.get(url_ips(isrc.id)).status_code == 403
    assert Client(READER).get(url_waf(wsrc.id)).status_code == 200
    assert Client(READER).get(url_ips(isrc.id)).status_code == 200


def test_the_response_shape_matches_the_contract(db_schema) -> None:
    _inst, src, _eps = run(waf_world)
    body = Client(READER).get(url_waf(src.id)).json()
    assert set(body) == {
        "window", "range_start", "range_end", "bucket_seconds", "buckets_expected",
        "buckets_measured", "state", "reason_code", "source_total_events",
        "attributed_events", "unattributed_events", "max_rate_per_second",
        "series_start", "series_step_seconds", "series_points", "total",
        "limit", "offset", "sort", "order", "items",
    }
    assert set(body["items"][0]) == {
        "id", "virtual_host", "http_method", "normalized_endpoint", "destination_ip",
        "display_name", "events", "rate_per_second", "peak_rate_per_second",
        "peak_bucket_start", "buckets_with_data", "partial_buckets", "active",
        "series",
    }


# ----------------------------------------------- series grid (Amendment 1)
def series_of(body: dict, item_id) -> list[float | None]:  # type: ignore[no-untyped-def]
    return next(item["series"] for item in body["items"] if item["id"] == str(item_id))


WAF_GRID = {
    "5m": (1, 300),
    "1h": (12, 300),
    "24h": (48, 1800),
    "7d": (56, 10800),
    "28d": (56, 43200),
}


def test_waf_series_grid_is_the_contract_table(db_schema) -> None:  # type: ignore[no-untyped-def]
    _inst, src, eps = run(waf_world)
    client = Client(READER)
    for window, (points, step) in WAF_GRID.items():
        body = client.get(url_waf(src.id), params={"window": window}).json()
        assert body["series_points"] == points, window
        assert body["series_step_seconds"] == step, window
        assert parse(body["series_start"]) == parse(body["range_start"]), window
        api = next(item for item in body["items"] if item["id"] == str(eps["api"].id))
        assert len(api["series"]) == points, window


def test_waf_series_is_one_rate_per_bucket_on_the_hour_window(db_schema) -> None:  # type: ignore[no-untyped-def]
    """1h has one bucket per step, oldest step first, so each point is that
    bucket's own rate: the newest bucket is the last point."""
    _inst, src, eps = run(waf_world)
    body = Client(READER).get(url_waf(src.id), params={"window": "1h"}).json()
    expected = [(100 + 10 * step) / BUCKET for step in range(HOUR)]
    assert series_of(body, eps["api"].id) == pytest.approx(expected)


def test_waf_series_marks_a_step_without_a_measured_bucket_as_null(db_schema) -> None:  # type: ignore[no-untyped-def]
    """24h: the 12 measured buckets are the newest two 1800 s steps; every
    earlier step is a gap, so it is null and never 0 (rule 8)."""
    _inst, src, eps = run(waf_world)
    body = Client(READER).get(url_waf(src.id), params={"window": "24h"}).json()
    series = series_of(body, eps["api"].id)
    assert len(series) == 48
    assert series[:46] == [None] * 46
    newest = sum(210 - 10 * i for i in range(6)) / (6 * BUCKET)
    older = sum(210 - 10 * i for i in range(6, 12)) / (6 * BUCKET)
    assert series[46] == pytest.approx(older)
    assert series[47] == pytest.approx(newest)
    assert sum(value is not None for value in series) == 2


def test_waf_series_has_a_gap_where_a_bucket_was_not_collected(db_schema) -> None:  # type: ignore[no-untyped-def]
    _inst, src, eps = run(waf_world)

    async def drop_one_bucket(s):  # type: ignore[no-untyped-def]
        await s.execute(
            delete(WafSourceBucket).where(
                WafSourceBucket.log_source_id == src.id,
                WafSourceBucket.bucket_start == at(5),
            )
        )

    run(drop_one_bucket)
    body = Client(READER).get(url_waf(src.id), params={"window": "1h"}).json()
    series = series_of(body, eps["api"].id)
    # Bucket 5 is step 11 - 5 = 6, and it is not a measurement any more.
    assert series[6] is None
    assert all(value is not None for i, value in enumerate(series) if i != 6)
    assert body["buckets_measured"] == 11


def test_waf_series_counts_a_measured_step_without_a_row_as_zero(db_schema) -> None:  # type: ignore[no-untyped-def]
    """`old` first appears six buckets back. The earlier steps were measured for
    the source, so its absence there is zero events, not a gap."""
    _inst, src, eps = run(waf_world)
    body = Client(READER).get(url_waf(src.id), params={"window": "1h"}).json()
    series = series_of(body, eps["old"].id)
    assert series[:6] == pytest.approx([10 / BUCKET] * 6)
    assert series[6:] == [0.0] * 6


def test_waf_series_is_computed_for_the_page_only(db_schema) -> None:  # type: ignore[no-untyped-def]
    _inst, src, _eps = run(waf_world)
    body = Client(READER).get(url_waf(src.id), params={"limit": 2}).json()
    assert len(body["items"]) == 2
    assert all(len(item["series"]) == 12 for item in body["items"])


def test_ips_series_grid_and_values(db_schema) -> None:  # type: ignore[no-untyped-def]
    _inst, src, ents = run(ips_world)
    client = Client(READER)
    for window, (points, step) in WAF_GRID.items():
        body = client.get(url_ips(src.id), params={"window": window}).json()
        assert body["series_points"] == points, window
        assert body["series_step_seconds"] == step, window
        web = next(item for item in body["items"] if item["id"] == str(ents["web"].id))
        assert len(web["series"]) == points, window
    body = client.get(url_ips(src.id), params={"window": "1h"}).json()
    expected = [(200 + 5 * step) / BUCKET for step in range(HOUR)]
    assert series_of(body, ents["web"].id) == pytest.approx(expected)
    day = client.get(url_ips(src.id), params={"window": "24h"}).json()
    assert series_of(day, ents["web"].id)[:46] == [None] * 46


def test_an_empty_window_reports_no_series_points(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=1, name="waf-1")
        s.add(waf_profile(inst.id, src.id))
        return src

    src = run(seed)
    body = Client(READER).get(url_waf(src.id)).json()
    assert body["series_start"] is None
    assert body["series_step_seconds"] is None
    assert body["series_points"] == 0


# ----------------------------------------------- "All" rows (Amendment 3)
def test_waf_limit_accepts_one_thousand_rows_in_one_statement(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=1, name="waf-bulk")
        s.add(waf_profile(inst.id, src.id))
        await s.flush()
        for i in range(HOUR):
            s.add(waf_bucket(src.id, i))
        for k in range(1000):
            ep = waf_endpoint(inst.id, src.id, host="bulk.example.com", path=f"/b/{k:04d}")
            s.add(ep)
            await s.flush()
            s.add(waf_row(ep.id, 0, k + 1))
        return src

    src = run(seed)
    client = Client(READER)
    body = client.get(url_waf(src.id), params={"limit": 1000}).json()
    assert body["total"] == 1000
    assert len(body["items"]) == 1000
    assert body["items"][0]["events"] == 1000
    assert body["max_rate_per_second"] == pytest.approx(1000 / (12 * BUCKET))
    assert len(body["items"][0]["series"]) == 12
    assert client.get(url_waf(src.id), params={"limit": 1001}).status_code == 422
    # Five statements, whether the page is one row or a thousand.
    client.get(url_waf(src.id), params={"limit": 1})
    assert client.statements == 5
    client.get(url_waf(src.id), params={"limit": 1000})
    assert client.statements == 5


def test_ips_limit_accepts_one_thousand_rows_in_one_statement(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=2, name="ips-bulk")
        s.add(ips_profile(inst.id, src.id))
        await s.flush()
        for i in range(HOUR):
            s.add(ips_bucket(src.id, i))
        for k in range(1000):
            ent = ips_entity(inst.id, src.id, f"10.30.{k // 250}.{k % 250 + 1}")
            s.add(ent)
            await s.flush()
            s.add(ips_row(ent.id, 0, k + 1))
        return src

    src = run(seed)
    client = Client(READER)
    body = client.get(url_ips(src.id), params={"limit": 1000}).json()
    assert body["total"] == 1000
    assert len(body["items"]) == 1000
    assert body["items"][0]["events"] == 1000
    assert len(body["items"][0]["series"]) == 12
    assert client.get(url_ips(src.id), params={"limit": 1001}).status_code == 422
    client.get(url_ips(src.id), params={"limit": 1000})
    assert client.statements == 5


# ------------------------------------------------- CSV export (Amendment 2)
def url_waf_csv(source_id) -> str:  # type: ignore[no-untyped-def]
    return f"/api/v1/behavior/waf/sources/{source_id}/traffic.csv"


def url_ips_csv(source_id) -> str:  # type: ignore[no-untyped-def]
    return f"/api/v1/behavior/ips/sources/{source_id}/traffic.csv"


def csv_rows(body: str) -> list[list[str]]:
    """The body parsed as RFC 4180, so quoting is checked, not assumed."""
    return list(csv.reader(io.StringIO(body, newline=""), delimiter=",", quotechar='"'))


WAF_CSV_COLUMNS = [
    "rank", "host", "method", "path", "avg_events_per_s", "peak_events_per_s",
    "peak_bucket_start_utc", "events", "buckets_with_data", "partial_buckets",
    "active", "window", "range_start_utc", "range_end_utc", "buckets_measured",
    "buckets_expected", "state", "source", "truncated",
]
IPS_CSV_COLUMNS = [
    "rank", "destination_ip", "display_name", "avg_events_per_s", "peak_events_per_s",
    "peak_bucket_start_utc", "events", "buckets_with_data", "partial_buckets",
    "active", "window", "range_start_utc", "range_end_utc", "buckets_measured",
    "buckets_expected", "state", "source", "truncated",
]


def test_waf_csv_is_the_whole_set_with_the_contract_columns(db_schema) -> None:  # type: ignore[no-untyped-def]
    _inst, src, _eps = run(waf_world)
    response = Client(READER).get(url_waf_csv(src.id))
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == (
        'attachment; filename="traffic-waf-waf-1-1h-20261105T1805Z.csv"'
    )
    assert response.headers["x-export-state"] == "OK"
    assert "x-export-truncated" not in response.headers
    rows = csv_rows(response.text)
    assert rows[0] == WAF_CSV_COLUMNS
    assert len(rows) == 28  # the header and the whole set, not the default page
    assert [row[0] for row in rows[1:]] == [str(rank) for rank in range(1, 28)]
    assert response.text.count("\r\n") == 28
    assert response.text.endswith("\r\n")
    api = next(row for row in rows[1:] if row[0] == "1")
    assert api == [
        "1", "api.example.com", "GET", "/v1/orders/{id}", "0.517", "0.700",
        "2026-11-05T18:00:00Z", "1860", "12", "0", "true", "1h",
        "2026-11-05T17:05:00Z", "2026-11-05T18:05:00Z", "12", "12", "OK", "waf-1",
        "false",
    ]
    assert len(rows[0]) == 19  # the id is not a column; the rank identifies the row


def test_waf_csv_a_null_is_an_empty_cell(db_schema) -> None:  # type: ignore[no-untyped-def]
    _inst, src, _eps = run(waf_world)
    rows = csv_rows(Client(READER).get(url_waf_csv(src.id)).text)
    health = next(row for row in rows[1:] if row[3] == "/health")
    assert health[1] == ""  # the endpoint has no virtual host


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_waf_csv_guards_a_cell_that_starts_with_a_formula_character(  # type: ignore[no-untyped-def]
    db_schema, prefix: str
) -> None:
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=1, name="waf-1")
        s.add(waf_profile(inst.id, src.id))
        ep = waf_endpoint(inst.id, src.id, host=f"{prefix}danger.example", path="/x")
        s.add(ep)
        await s.flush()
        for i in range(HOUR):
            s.add(waf_bucket(src.id, i))
        s.add(waf_row(ep.id, 0, 10))
        return src

    src = run(seed)
    rows = csv_rows(Client(READER).get(url_waf_csv(src.id)).text)
    assert rows[1][1] == f"'{prefix}danger.example"


def test_waf_csv_quotes_a_comma_and_a_quote_rfc4180(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=1, name="waf-1")
        s.add(waf_profile(inst.id, src.id))
        ep = waf_endpoint(inst.id, src.id, host='a"b,c.example', path="/x")
        s.add(ep)
        await s.flush()
        for i in range(HOUR):
            s.add(waf_bucket(src.id, i))
        s.add(waf_row(ep.id, 0, 10))
        return src

    src = run(seed)
    response = Client(READER).get(url_waf_csv(src.id))
    assert '"a""b,c.example"' in response.text
    assert csv_rows(response.text)[1][1] == 'a"b,c.example'


def test_waf_csv_truncates_at_the_configured_bound(db_schema) -> None:  # type: ignore[no-untyped-def]
    _inst, src, _eps = run(waf_world)
    client = Client(READER)
    client.http.app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(  # type: ignore[attr-defined]
        update={"api_max_export_rows": 2}
    )
    response = client.get(url_waf_csv(src.id))
    assert response.status_code == 200
    assert response.headers["x-export-truncated"] == "true"
    rows = csv_rows(response.text)
    assert len(rows) == 3
    assert [row[0] for row in rows[1:]] == ["1", "2"]
    assert all(row[-1] == "true" for row in rows[1:])


def test_waf_csv_insufficient_data_is_header_only(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=1, name="waf-1")
        s.add(waf_profile(inst.id, src.id))
        await s.flush()
        s.add(
            waf_bucket(
                src.id, 0,
                completeness=BucketCompleteness.PARTIAL,
                request_count_measurement=WafMeasurement.LOWER_BOUND,
            )
        )
        return src

    src = run(seed)
    response = Client(READER).get(url_waf_csv(src.id))
    assert response.status_code == 200
    assert response.headers["x-export-state"] == "INSUFFICIENT_DATA"
    assert response.text == ",".join(WAF_CSV_COLUMNS) + "\r\n"
    assert "x-export-truncated" not in response.headers


def test_waf_csv_not_collected_is_header_only(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=1, name="waf-1")
        s.add(waf_profile(inst.id, src.id, enabled=False))
        return src

    src = run(seed)
    response = Client(READER).get(url_waf_csv(src.id))
    assert response.status_code == 200
    assert response.headers["x-export-state"] == "NOT_COLLECTED"
    assert response.text == ",".join(WAF_CSV_COLUMNS) + "\r\n"


def test_waf_csv_unknown_source_is_404_and_needs_the_permission(db_schema) -> None:  # type: ignore[no-untyped-def]
    _inst, src, _eps = run(waf_world)
    assert Client(READER).get(url_waf_csv(MISSING)).status_code == 404
    nobody = Client(Principal(subject="nobody", permissions=frozenset()))
    assert nobody.get(url_waf_csv(src.id)).status_code == 403


def test_waf_csv_statement_count_is_constant_at_200_endpoints(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed_one(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=1, name="waf-1")
        s.add(waf_profile(inst.id, src.id))
        ep = waf_endpoint(inst.id, src.id, host="api.example.com", path="/v1")
        s.add(ep)
        await s.flush()
        for i in range(HOUR):
            s.add(waf_bucket(src.id, i))
            s.add(waf_row(ep.id, i, 10))
        return inst, src

    inst, src = run(seed_one)
    client = Client(READER)
    assert client.get(url_waf_csv(src.id)).status_code == 200
    small = client.statements

    async def grow(s):  # type: ignore[no-untyped-def]
        for k in range(199):
            ep = waf_endpoint(inst.id, src.id, host="bulk.example.com", path=f"/b/{k}")
            s.add(ep)
            await s.flush()
            s.add(waf_row(ep.id, 0, 5))

    run(grow)
    assert client.get(url_waf_csv(src.id)).status_code == 200
    assert client.statements == small, (small, client.statements)
    assert small == 4  # no series in the export


def test_ips_csv_columns_context_and_state(db_schema) -> None:  # type: ignore[no-untyped-def]
    _inst, src, _ents = run(ips_world)
    response = Client(READER).get(url_ips_csv(src.id))
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == (
        'attachment; filename="traffic-ips-ips-1-1h-20261105T1805Z.csv"'
    )
    assert response.headers["x-export-state"] == "OK"
    rows = csv_rows(response.text)
    assert rows[0] == IPS_CSV_COLUMNS
    assert len(rows) == 27  # the header and the whole set
    assert [row[0] for row in rows[1:]] == [str(rank) for rank in range(1, 27)]
    web = rows[1]
    assert web == [
        "1", "10.20.30.40", "web-1", "0.758", "0.850", "2026-11-05T18:00:00Z",
        "2730", "12", "0", "true", "1h", "2026-11-05T17:05:00Z",
        "2026-11-05T18:05:00Z", "12", "12", "OK", "ips-1", "false",
    ]
    retired = next(row for row in rows[1:] if row[1] == "192.168.1.9")
    assert retired[9] == "false"


def test_ips_csv_guards_display_name_and_truncates(db_schema) -> None:  # type: ignore[no-untyped-def]
    _inst, src, _ents = run(ips_world)

    async def rename(s):  # type: ignore[no-untyped-def]
        entity = await s.scalar(
            select(IpsEntity).where(
                IpsEntity.log_source_id == src.id, IpsEntity.value_key == "10.20.30.40"
            )
        )
        assert entity is not None
        entity.display_name = "=cmd|calc"

    run(rename)
    client = Client(READER)
    client.http.app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(  # type: ignore[attr-defined]
        update={"api_max_export_rows": 1}
    )
    response = client.get(url_ips_csv(src.id))
    assert response.headers["x-export-truncated"] == "true"
    rows = csv_rows(response.text)
    assert len(rows) == 2
    assert rows[1][2] == "'=cmd|calc"
    assert rows[1][-1] == "true"


def test_ips_csv_not_collected_is_header_only_and_needs_the_permission(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=2, name="ips-1")
        return src

    src = run(seed)
    response = Client(READER).get(url_ips_csv(src.id))
    assert response.status_code == 200
    assert response.headers["x-export-state"] == "NOT_COLLECTED"
    assert response.text == ",".join(IPS_CSV_COLUMNS) + "\r\n"
    assert Client(READER).get(url_ips_csv(MISSING)).status_code == 404
    nobody = Client(Principal(subject="nobody", permissions=frozenset()))
    assert nobody.get(url_ips_csv(src.id)).status_code == 403


def test_ips_csv_statement_count_is_constant_at_200_entities(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed_one(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=2, name="ips-1")
        s.add(ips_profile(inst.id, src.id))
        ent = ips_entity(inst.id, src.id, "10.20.30.40")
        s.add(ent)
        await s.flush()
        for i in range(HOUR):
            s.add(ips_bucket(src.id, i))
            s.add(ips_row(ent.id, i, 10))
        return inst, src

    inst, src = run(seed_one)
    client = Client(READER)
    assert client.get(url_ips_csv(src.id)).status_code == 200
    small = client.statements

    async def grow(s):  # type: ignore[no-untyped-def]
        for k in range(199):
            ent = ips_entity(inst.id, src.id, f"10.21.0.{k + 1}")
            s.add(ent)
            await s.flush()
            s.add(ips_row(ent.id, 0, 5))

    run(grow)
    assert client.get(url_ips_csv(src.id)).status_code == 200
    assert client.statements == small, (small, client.statements)
    assert small == 4


def test_waf_csv_is_utf8_and_a_non_ascii_cell_survives(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=1, name="waf-UTF-8")
        s.add(waf_profile(inst.id, src.id))
        ep = waf_endpoint(inst.id, src.id, host="bücher.example", path="/café")
        s.add(ep)
        await s.flush()
        for i in range(HOUR):
            s.add(waf_bucket(src.id, i))
        s.add(waf_row(ep.id, 0, 10))
        return src

    src = run(seed)
    response = Client(READER).get(url_waf_csv(src.id))
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert "bücher.example" in response.content.decode("utf-8")
    assert "/café" in response.content.decode("utf-8")
    assert "bücher.example".encode() in response.content


def test_ips_csv_insufficient_data_is_header_only(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def seed(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        src = await make_log_source(s, inst, qradar_id=2, name="ips-1")
        s.add(ips_profile(inst.id, src.id))
        s.add(ips_bucket(src.id, 0, destination_ip_measurement="NOT_COLLECTED"))
        return src

    src = run(seed)
    response = Client(READER).get(url_ips_csv(src.id))
    assert response.status_code == 200
    assert response.headers["x-export-state"] == "INSUFFICIENT_DATA"
    assert response.text == ",".join(IPS_CSV_COLUMNS) + "\r\n"
    assert "x-export-truncated" not in response.headers
