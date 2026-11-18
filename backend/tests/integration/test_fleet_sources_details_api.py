"""UIQ-7d: the details on a fleet row and the criticality, owner, yes/no and collector facets."""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

from app.models.enums import Criticality
from app.models.fleet import LogSourceGroup
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_fleet_api import (  # noqa: F401  (fixtures and helpers)
    CLOSED,
    NOW,
    VIEWER,
    Client,
    _fixed_clock,
    classification,
    run,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run fleet source details integration tests",
    ),
]

URL = "/api/v1/fleet/sources"


def _six(db_schema):  # type: ignore[no-untyped-def]
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        s.add(LogSourceGroup(instance_id=inst.id, qradar_id=10, name="Perimeter",
                             assignable=True, synced_at=CLOSED))
        rows = [
            # name, criticality, owner, auto, monitoring, maintenance, collector
            ("a", Criticality.CRITICAL, "net-team", True, True, False, 1),
            ("b", Criticality.HIGH, "net-team", False, True, True, 1),
            ("c", Criticality.HIGH, "soc", None, False, False, 2),
            ("d", Criticality.LOW, None, True, True, False, None),
            ("e", Criticality.MEDIUM, "soc", False, False, True, 2),
            ("f", Criticality.MEDIUM, "Net-Ops", True, True, False, 3),
        ]
        for i, (n, crit, owner, auto, mon, maint, coll) in enumerate(rows, start=1):
            ls = await make_log_source(
                s, inst, qradar_id=i, name=n, criticality=crit, owner=owner,
                auto_discovered=auto, monitoring_enabled=mon, maintenance_mode=maint,
                target_event_collector_id=coll, last_event_time=NOW - timedelta(minutes=2),
                description=f"desc-{n}", protocol_type_id=12 + i, credibility=5,
                qradar_status="SUCCESS", group_ids=[10] if n == "a" else None,
                maintenance_until=NOW + timedelta(hours=1) if maint else None,
                qradar_created_at=CLOSED - timedelta(days=9),
                qradar_modified_at=CLOSED - timedelta(days=1),
            )
            s.add(classification(ls, "OTHER"))

    run(setup)


def _names(client, query: str) -> list[str]:  # type: ignore[no-untyped-def]
    resp = client.get(f"{URL}?{query}")
    assert resp.status_code == 200, resp.text
    return [i["name"] for i in resp.json()["items"]]


def _counts(facets, family):  # type: ignore[no-untyped-def]
    return {x["value"]: x["count"] for x in facets[family]}


def test_row_carries_the_details(db_schema) -> None:
    _six(db_schema)
    rows = {r["name"]: r for r in Client(VIEWER).get(URL).json()["items"]}
    a = rows["a"]
    assert a["description"] == "desc-a" and a["protocol_type_id"] == 13
    assert a["target_event_collector_id"] == 1 and a["credibility"] == 5
    assert a["qradar_status"] == "SUCCESS" and a["criticality"] == "CRITICAL"
    assert a["owner"] == "net-team" and a["monitoring_enabled"] is True
    assert a["maintenance_mode"] is False and a["maintenance_until"] is None
    assert a["qradar_created_at"] == "2026-10-04T10:05:00Z"
    assert a["groups"] == ["Perimeter"]
    b = rows["b"]
    assert b["maintenance_mode"] is True and b["maintenance_until"].startswith("2026-10-13T11:07")
    assert rows["d"]["owner"] is None and rows["d"]["target_event_collector_id"] is None


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("criticality=HIGH", ["b", "c"]),
        ("criticality=HIGH&criticality=LOW", ["b", "c", "d"]),
        ("owner=soc", ["c", "e"]),
        ("owner=soc&owner=net-team", ["a", "b", "c", "e"]),
        ("auto_discovered=YES", ["a", "d", "f"]),
        ("auto_discovered=NO", ["b", "e"]),  # unknown (null) matches neither
        ("monitoring=NO", ["c", "e"]),
        ("monitoring=YES&maintenance=YES", ["b"]),
        ("maintenance=NO", ["a", "c", "d", "f"]),
        ("collector=2", ["c", "e"]),
        ("collector=1&collector=3", ["a", "b", "f"]),
        ("criticality=MEDIUM&owner=soc&monitoring=NO", ["e"]),
    ],
)
def test_each_filter(db_schema, query: str, expected: list[str]) -> None:
    _six(db_schema)
    assert _names(Client(VIEWER), query) == expected


def test_bad_values_are_422(db_schema) -> None:
    _six(db_schema)
    c = Client(VIEWER)
    for q in ("criticality=URGENT", "auto_discovered=MAYBE", "collector=x", "monitoring=1"):
        assert c.get(f"{URL}?{q}").status_code == 422, q


def test_facets_count_under_the_other_filters(db_schema) -> None:
    _six(db_schema)
    c = Client(VIEWER)
    f = c.get(URL).json()["facets"]
    assert _counts(f, "criticality") == {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 2, "LOW": 1}
    assert _counts(f, "owner") == {"net-team": 2, "soc": 2, "Net-Ops": 1}
    assert _counts(f, "auto_discovered") == {"YES": 3, "NO": 2}
    assert _counts(f, "monitoring") == {"YES": 4, "NO": 2}
    assert _counts(f, "maintenance") == {"YES": 2, "NO": 4}
    assert _counts(f, "collector") == {"1": 2, "2": 2, "3": 1}
    # Own filter ignored, the others obeyed.
    g = c.get(f"{URL}?criticality=HIGH&owner=soc").json()
    assert g["total"] == 1
    assert _counts(g["facets"], "criticality") == {"HIGH": 1, "MEDIUM": 1, "CRITICAL": 0, "LOW": 0}
    assert _counts(g["facets"], "owner") == {"soc": 1, "net-team": 1}
    assert _counts(g["facets"], "monitoring") == {"YES": 0, "NO": 1}
    h = c.get(f"{URL}?monitoring=NO").json()["facets"]
    assert _counts(h, "monitoring") == {"YES": 4, "NO": 2}
    assert _counts(h, "collector") == {"2": 2}
    # The existing families obey the new filters.
    k = c.get(f"{URL}?collector=1").json()["facets"]
    assert sum(x["count"] for x in k["flow"]) == 2


def test_owner_facet_search_and_selection(db_schema) -> None:
    _six(db_schema)
    c = Client(VIEWER)
    q = c.get(f"{URL}?owner_q=NET").json()
    assert q["total"] == 6  # the search never filters the list
    assert _counts(q["facets"], "owner") == {"net-team": 2, "Net-Ops": 1}
    assert _counts(c.get(f"{URL}?owner_q=%25").json()["facets"], "owner") == {}
    # A selected owner is always listed, even when the search would hide it.
    s = c.get(f"{URL}?owner=soc&owner_q=net").json()["facets"]
    assert "soc" in _counts(s, "owner")


def test_csv_carries_every_detail_column(db_schema) -> None:
    _six(db_schema)
    resp = Client(VIEWER).get(f"{URL}.csv?owner=net-team&sort=name")
    lines = resp.text.split("\r\n")
    header = lines[0].split(",")
    assert "description" in header and "maintenance_until" in header
    row = dict(zip(header, lines[1].split(","), strict=False))
    assert row["criticality"] == "CRITICAL" and row["owner"] == "net-team"
    assert row["qradar_created_at"] == "2026-10-04T10:05:00Z"
    assert len(lines) == 4  # header, two rows, trailing


def test_sql_is_constant_with_the_new_facets(db_schema) -> None:
    _six(db_schema)
    c = Client(VIEWER)
    seen = {}
    everything = (
        "criticality=HIGH&owner=soc&auto_discovered=YES&monitoring=NO&maintenance=NO&collector=2"
    )
    for label, query in (
        ("plain", ""),
        ("all", f"?{everything}"),
        ("all5", f"?limit=5&{everything}"),
    ):
        assert c.get(f"{URL}{query}").status_code == 200
        seen[label] = c.statements
    assert seen["all"] == seen["all5"] == seen["plain"], seen
    assert seen["plain"] <= 12, seen
