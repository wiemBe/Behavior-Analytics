"""GET /fleet/eps-series: fail-closed fleet EPS per step. DB-gated."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

from app.api.routes import fleet as fleet_routes
from app.models.enums import BucketCompleteness
from app.models.log_source import LogSourceBaseline
from tests.integration.factories import add_metric, make_instance, make_log_source
from tests.integration.test_fleet_api import (
    CLOSED,
    NOTHING,
    NOW,
    VIEWER,
    Client,
    classification,
    run,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run fleet EPS series integration tests",
    ),
]

URL = "/api/v1/fleet/eps-series"
HOUR_START = CLOSED - timedelta(hours=1)  # 09:05, the 1h window start


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch) -> None:
    monkeypatch.setattr(fleet_routes, "_utcnow", lambda: NOW)


def _bucket(k: int):  # type: ignore[no-untyped-def]
    """The k-th closed 5-minute bucket before the window end (k=1 is the latest)."""
    return CLOSED - timedelta(minutes=5 * k)


async def _fill(s, src, *, skip=(), partial=(), count=300):  # type: ignore[no-untyped-def]
    for k in range(1, 13):
        if k in skip:
            continue
        await add_metric(
            s, src, _bucket(k), event_count=count,
            completeness=(
                BucketCompleteness.PARTIAL if k in partial else BucketCompleteness.COMPLETE
            ),
        )


def _baseline(src, weekday: int, hour: int, median: float, *, reliable: bool = True):  # type: ignore[no-untyped-def]
    return LogSourceBaseline(
        log_source_id=src.id, metric_name="average_eps", weekday=weekday, hour=hour,
        median=median, mad=0.1, p05=0.0, p95=99.0, sample_count=30, is_reliable=reliable,
        observations=[median],
    )


def _fleet(body: dict) -> list[dict]:
    assert [s["key"] for s in body["series"]] == ["FLEET"]
    return body["series"][0]["points"]


def test_complete_cells_give_values_and_every_step_is_present(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        a = await make_log_source(s, inst, qradar_id=1, name="a")
        b = await make_log_source(s, inst, qradar_id=2, name="b")
        await _fill(s, a)
        await _fill(s, b)

    run(setup)
    body = Client(VIEWER).get(f"{URL}?window=1h").json()
    assert body["window_end"].startswith("2026-10-13T10:05:00")
    assert body["bucket_seconds"] == 300 and body["step_seconds"] == 300
    pts = _fleet(body)
    assert len(pts) == 12
    assert pts[0]["step_start"].startswith("2026-10-13T09:05:00")
    assert pts[-1]["step_start"].startswith("2026-10-13T10:00:00")
    for p in pts:
        assert (p["events"], p["eps"]) == (600, 2.0)
        assert (p["cells_complete"], p["cells_expected"], p["sources_expected"]) == (2, 2, 2)
        assert p["expected_eps"] is None
    assert body["coverage"] == {"steps_total": 12, "steps_measured": 12}


def test_a_partial_or_missing_cell_nulls_the_step_not_a_smaller_sum(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        a = await make_log_source(s, inst, qradar_id=1, name="a")
        b = await make_log_source(s, inst, qradar_id=2, name="b")
        await _fill(s, a)
        await _fill(s, b, skip=(1,), partial=(2,))

    run(setup)
    body = Client(VIEWER).get(f"{URL}?window=1h").json()
    pts = _fleet(body)
    assert [p["eps"] for p in pts[-2:]] == [None, None]
    assert [p["events"] for p in pts[-2:]] == [None, None]
    assert [p["cells_complete"] for p in pts[-2:]] == [1, 1]
    assert all(p["eps"] == 2.0 for p in pts[:-2])
    assert body["coverage"] == {"steps_total": 12, "steps_measured": 10}


def test_no_metric_at_all_is_null_and_never_zero(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        await make_log_source(s, inst, qradar_id=1, name="a")

    run(setup)
    pts = _fleet(Client(VIEWER).get(f"{URL}?window=1h").json())
    assert len(pts) == 12
    assert all(p["eps"] is None and p["events"] is None for p in pts)
    assert all(p["cells_complete"] == 0 and p["cells_expected"] == 1 for p in pts)


def test_no_monitored_source_means_no_sum_and_no_class_series(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        off = await make_log_source(s, inst, qradar_id=1, name="off", enabled=False)
        unmon = await make_log_source(s, inst, qradar_id=2, name="unmon", monitoring_enabled=False)
        await _fill(s, off)
        await _fill(s, unmon)

    run(setup)
    client = Client(VIEWER)
    pts = _fleet(client.get(f"{URL}?window=1h").json())
    assert all(p["eps"] is None and p["cells_expected"] == 0 for p in pts)
    assert all(p["sources_expected"] == 0 for p in pts)
    by_class = client.get(f"{URL}?window=1h&group_by=class").json()
    assert by_class["series"] == []
    assert by_class["coverage"] == {"steps_total": 0, "steps_measured": 0}


def test_a_source_disabled_since_adds_neither_expectation_nor_sum(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        a = await make_log_source(s, inst, qradar_id=1, name="a")
        gone = await make_log_source(s, inst, qradar_id=2, name="gone", enabled=False)
        await _fill(s, a)
        await _fill(s, gone, count=9000)

    run(setup)
    pts = _fleet(Client(VIEWER).get(f"{URL}?window=1h").json())
    assert all(p["events"] == 300 and p["sources_expected"] == 1 for p in pts)


def test_a_source_added_during_the_window_nulls_the_steps_before_it_existed(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        a = await make_log_source(s, inst, qradar_id=1, name="a")
        new = await make_log_source(s, inst, qradar_id=2, name="new")
        await _fill(s, a)
        await _fill(s, new, skip=tuple(range(7, 13)))

    run(setup)
    pts = _fleet(Client(VIEWER).get(f"{URL}?window=1h").json())
    assert [p["eps"] is None for p in pts] == [True] * 6 + [False] * 6


def test_expected_eps_is_the_sum_of_reliable_medians_and_null_when_one_lacks_a_cell(
    db_schema,
) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        a = await make_log_source(s, inst, qradar_id=1, name="a")
        b = await make_log_source(s, inst, qradar_id=2, name="b")
        c = await make_log_source(s, inst, qradar_id=3, name="c")
        # 2026-10-13 is ISO weekday 2. 09:xx and 10:xx cells.
        s.add_all([
            _baseline(a, 2, 9, 1.5), _baseline(a, 2, 10, 2.0),
            _baseline(b, 2, 9, 4.0), _baseline(b, 2, 10, 4.0),
            _baseline(c, 2, 10, 8.0),
            _baseline(c, 2, 9, 100.0, reliable=False),
        ])

    run(setup)
    pts = _fleet(Client(VIEWER).get(f"{URL}?window=1h").json())
    # c has no reliable 09:00 cell, so every 09:xx step is null; 10:xx sums.
    assert [p["expected_eps"] for p in pts[:11]] == [None] * 11
    assert pts[-1]["step_start"].startswith("2026-10-13T10:00:00")
    assert pts[-1]["expected_eps"] == 14.0
    for p in pts:
        assert "p05" not in p and "p95" not in p and "band" not in p


def test_a_baseline_of_an_unmonitored_source_or_another_metric_is_ignored(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        a = await make_log_source(s, inst, qradar_id=1, name="a")
        off = await make_log_source(s, inst, qradar_id=2, name="off", monitoring_enabled=False)
        other = _baseline(a, 2, 10, 50.0)
        other.metric_name = "peak_eps"
        s.add_all([_baseline(a, 2, 10, 3.0), _baseline(off, 2, 10, 500.0), other])

    run(setup)
    pts = _fleet(Client(VIEWER).get(f"{URL}?window=1h").json())
    assert pts[-1]["expected_eps"] == 3.0


def test_group_by_class_splits_and_excludes_internal(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        fw1 = await make_log_source(s, inst, qradar_id=1, name="fw1")
        fw2 = await make_log_source(s, inst, qradar_id=2, name="fw2")
        waf = await make_log_source(s, inst, qradar_id=3, name="waf")
        internal = await make_log_source(s, inst, qradar_id=4, name="sim")
        plain = await make_log_source(s, inst, qradar_id=5, name="plain")
        s.add_all([
            classification(fw1, "FIREWALL"), classification(fw2, "FIREWALL"),
            classification(waf, "WAF"), classification(internal, "QRADAR_INTERNAL"),
        ])
        await s.flush()
        await _fill(s, fw1, count=300)
        await _fill(s, fw2, count=600, skip=(1,))
        await _fill(s, waf, count=1500)
        await _fill(s, internal, count=99999)
        await _fill(s, plain, count=3000)
        s.add_all([_baseline(waf, 2, 10, 5.0), _baseline(fw1, 2, 10, 1.0)])

    run(setup)
    client = Client(VIEWER)
    body = client.get(f"{URL}?window=1h&group_by=class").json()
    assert body["group_by"] == "class"
    # SourceClass enum order; QRADAR_INTERNAL excluded; classes with no source absent.
    assert [s["key"] for s in body["series"]] == ["WAF", "FIREWALL", "UNCLASSIFIED"]
    waf, fw, plain = body["series"]
    assert (waf["sources_expected"], fw["sources_expected"]) == (1, 2)
    assert waf["points"][0]["events"] == 1500 and waf["points"][0]["eps"] == 5.0
    assert plain["points"][0]["events"] == 3000
    assert fw["points"][0]["events"] == 900
    assert fw["points"][-1]["events"] is None and fw["points"][-1]["cells_complete"] == 1
    assert waf["points"][-1]["expected_eps"] == 5.0
    assert fw["points"][-1]["expected_eps"] is None  # fw2 has no reliable cell
    assert body["coverage"] == {"steps_total": 36, "steps_measured": 35}
    # The fleet sum over the same data leaves the internal source out too.
    fleet = _fleet(client.get(f"{URL}?window=1h").json())
    assert fleet[0]["sources_expected"] == 4
    assert fleet[0]["events"] == 300 + 600 + 1500 + 3000


def test_step_mapping_per_window(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        a = await make_log_source(s, inst, qradar_id=1, name="a")
        # Day-old bucket (24h and 7d windows) and the latest one.
        await add_metric(s, a, _bucket(1), event_count=300)
        await add_metric(s, a, CLOSED - timedelta(days=2), event_count=1200)
        s.add_all([_baseline(a, 2, 9, 1.0), _baseline(a, 2, 10, 3.0)])

    run(setup)
    client = Client(VIEWER)
    day = client.get(f"{URL}?window=24h").json()
    assert (day["step_seconds"], len(_fleet(day))) == (300, 288)
    week = client.get(f"{URL}?window=7d").json()
    assert (week["step_seconds"], len(_fleet(week))) == (3600, 168)
    wp = _fleet(week)
    assert wp[0]["step_start"].startswith("2026-10-06T10:00:00")
    assert wp[0]["cells_expected"] == 12 and wp[0]["cells_complete"] == 0
    # The window ends at 10:00 (the 10:05 close floored to the hour): the last step is
    # 09:00-10:00, whole, and holds the buckets 09:55 and earlier only.
    assert week["window_end"].startswith("2026-10-13T10:00:00")
    assert wp[-1]["step_start"].startswith("2026-10-13T09:00:00")
    assert wp[-1]["cells_complete"] == 0 and wp[-1]["eps"] is None
    assert default_window(client) == "24h"


def default_window(client) -> str:  # type: ignore[no-untyped-def]
    return client.get(URL).json()["window"]


def test_a_full_hour_step_sums_in_the_seven_day_window(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        a = await make_log_source(s, inst, qradar_id=1, name="a")
        first = CLOSED - timedelta(days=7, minutes=5)  # 10:00, the first step's start
        for k in range(12):
            await add_metric(s, a, first + timedelta(minutes=5 * k), event_count=360)
        # Step 0 is 10:00-11:00 on weekday 2: exactly the (2, 10) cell.
        s.add_all([_baseline(a, 2, 10, 12.0), _baseline(a, 2, 11, 2.0)])

    run(setup)
    p = _fleet(Client(VIEWER).get(f"{URL}?window=7d").json())[0]
    assert (p["events"], p["eps"], p["cells_complete"], p["cells_expected"]) == (
        4320, 1.2, 12, 12)
    assert p["expected_eps"] == 12.0


@pytest.mark.parametrize("window", ["1h", "24h", "7d"])
def test_window_end_is_step_aligned_and_not_after_last_close(db_schema, window) -> None:
    body = Client(VIEWER).get(f"{URL}?window={window}").json()
    step = body["step_seconds"]
    end = datetime.fromisoformat(body["window_end"])
    assert end.timestamp() % step == 0
    assert end <= CLOSED
    assert CLOSED - end < timedelta(seconds=step)


@pytest.mark.parametrize("window", ["1h", "24h", "7d"])
def test_every_step_start_is_aligned(db_schema, window) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        await make_log_source(s, inst, qradar_id=1, name="a")

    run(setup)
    body = Client(VIEWER).get(f"{URL}?window={window}").json()
    step = body["step_seconds"]
    pts = _fleet(body)
    assert all(datetime.fromisoformat(p["step_start"]).timestamp() % step == 0 for p in pts)
    assert datetime.fromisoformat(body["window_end"]) - datetime.fromisoformat(
        pts[-1]["step_start"]
    ) == timedelta(seconds=step)


def test_a_row_of_another_bucket_width_is_not_a_cell(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        a = await make_log_source(s, inst, qradar_id=1, name="a")
        await _fill(s, a, skip=(1,))
        await add_metric(s, a, _bucket(1), event_count=300, bucket_seconds=60)

    run(setup)
    assert _fleet(Client(VIEWER).get(f"{URL}?window=1h").json())[-1]["eps"] is None


@pytest.mark.parametrize(
    "query", ["window=2h", "window=", "group_by=source", "window=1h&group_by=CLASS"]
)
def test_bad_parameters_are_422(db_schema, query) -> None:
    assert Client(VIEWER).get(f"{URL}?{query}").status_code == 422


def test_access_needs_read_fleet(db_schema) -> None:
    assert Client(NOTHING).get(URL).status_code == 403
    assert Client(VIEWER).get(URL).status_code == 200


@pytest.mark.parametrize("group_by", ["none", "class"])
@pytest.mark.parametrize("window", ["1h", "7d"])
def test_sql_is_constant_in_fleet_size(db_schema, window, group_by) -> None:
    counts = []
    for size in (3, 40):
        async def setup(s, size=size):  # type: ignore[no-untyped-def]
            from sqlalchemy import delete

            from app.models.fleet import LogSourceClassification
            from app.models.instance import QRadarInstance

            await s.execute(LogSourceClassification.__table__.delete())
            await s.execute(delete(QRadarInstance))
            inst = await make_instance(s)
            classes = ["FIREWALL", "WAF", "IPS", "DNS"]
            for i in range(size):
                src = await make_log_source(s, inst, qradar_id=i + 1, name=f"s{size}-{i}")
                s.add(classification(src, classes[i % len(classes)]))
                await s.flush()
                await _fill(s, src)
                s.add(_baseline(src, 2, 10, 1.0))

        run(setup)
        client = Client(VIEWER)
        resp = client.get(f"{URL}?window={window}&group_by={group_by}")
        assert resp.status_code == 200
        assert len(resp.json()["series"]) == (1 if group_by == "none" else min(size, 4))
        counts.append(client.statements)
    assert counts[0] == counts[1], counts
    assert counts[0] <= 3
