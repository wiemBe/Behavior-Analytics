"""The pivot text builders: window, literals, AQL shape and the two console URL forms."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from app.pivot import aql as q

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
START = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
START_MS = int(START.timestamp() * 1000)


def window(stop: datetime | None = NOW) -> q.PivotWindow:
    built, _ = q.make_window(START, stop, now=NOW)
    return built


def test_window_is_utc_epoch_and_half_open() -> None:
    w = window()
    assert (w.start_ms, w.stop_ms) == (START_MS, int(NOW.timestamp() * 1000))
    text = q.events_by_name_aql(conditions=["logsourceid = 7"], window=w)
    assert f"starttime >= {w.start_ms} AND starttime < {w.stop_ms}" in text
    assert text.endswith(f"START {w.start_ms} STOP {w.stop_ms}")


def test_window_clamps_and_says_so() -> None:
    future, _ = q.make_window(START, NOW + timedelta(hours=3), now=NOW)
    assert future.stop == NOW
    short, notes = q.make_window(NOW, NOW, now=NOW)
    assert short.stop - short.start == q.MIN_WINDOW and notes
    long, notes = q.make_window(NOW - timedelta(days=30), NOW, now=NOW)
    assert long.stop - long.start == q.MAX_WINDOW and "168" in notes[0]
    naive, _ = q.make_window(START.replace(tzinfo=None), None, now=NOW)
    assert naive.start == START and naive.stop == NOW


@pytest.mark.parametrize(
    "value", ["it's", 'a"b', "a\\b", "a\nb", "", "x" * 256, "tab\there"]
)
def test_literal_refuses_instead_of_escaping(value: str) -> None:
    with pytest.raises(q.PivotRefusal):
        q.literal(value)


def test_literal_quotes_a_plain_value() -> None:
    assert q.literal("/login%") == "'/login%'"


def test_property_expr_allowlist() -> None:
    assert q.property_expr("Request URL") == '"Request URL"'
    for bad in ['a"b', "a;b", "a(b)", "", "x" * 65]:
        with pytest.raises(q.PivotRefusal):
            q.property_expr(bad)


def test_ip_and_int_literals() -> None:
    assert q.ip_literal(" 10.0.0.1 ") == "'10.0.0.1'"
    assert q.int_literal("443") == "443"
    for bad in ["10.0.0.1' OR 1=1", "999.1.1.1", ""]:
        with pytest.raises(q.PivotRefusal):
            q.ip_literal(bad)
    for bad in ["4 4", "-1", "1e3", "٣", ""]:
        with pytest.raises(q.PivotRefusal):
            q.int_literal(bad)


def test_grouped_and_grid_shapes() -> None:
    w = window()
    grouped = q.grouped_aql(
        select_expr='"Request URL"', alias="url", conditions=["logsourceid = 7"], window=w
    )
    assert grouped.startswith('SELECT "Request URL" AS "url", SUM(eventcount) AS "events" FROM')
    assert 'GROUP BY "Request URL" ORDER BY SUM(eventcount) DESC LIMIT 50' in grouped
    grid = q.events_grid_aql(conditions=["logsourceid = 7"], window=w)
    assert "ORDER BY starttime DESC LIMIT 200" in grid
    assert "INOFFENSE(42)" in q.inoffense_aql(42, w)


@pytest.mark.parametrize(
    ("host", "kind", "origin"),
    [
        ("https://qr.example.com/", "rest", "https://qr.example.com"),
        ("qr.example.com", "rest", "https://qr.example.com"),
        ("http://10.0.0.5:8443/api", "rest", "http://10.0.0.5:8443"),
    ],
)
def test_console_base(host: str, kind: str, origin: str) -> None:
    assert q.console_base(host, kind).url == origin


@pytest.mark.parametrize(
    ("host", "kind"),
    [
        ("mock", "mock"),
        ("", "rest"),
        (None, "rest"),
        ("https://user:pw@qr.example.com", "rest"),
        ("ftp://qr.example.com", "rest"),
        ("https://bad host/", "rest"),
        ("https://qr.example.com:notaport", "rest"),
    ],
)
def test_console_base_none_with_reason(host: str | None, kind: str) -> None:
    base = q.console_base(host, kind)
    assert base.url is None and base.reason


def test_ariel_search_url_form_and_roundtrip() -> None:
    aql = "SELECT a FROM events WHERE x = 'y' AND z > 1 & q=2"
    url = q.ariel_search_url("https://qr.example.com", aql)
    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == (
        "https", "qr.example.com", "/console/do/ariel/arielSearch",
    )
    assert "value(aql)=" in parts.query and "&value(searchMode)=AQL" in parts.query
    parsed = parse_qs(parts.query)
    assert parsed["value(aql)"] == [aql]
    assert parsed["appName"] == ["EventViewer"] and parsed["pageId"] == ["EventList"]
    assert parsed["value(timeRangeType)"] == ["aqlTime"]


def test_offense_summary_url_form() -> None:
    url = q.offense_summary_url("https://qr.example.com", 1234)
    assert url == (
        "https://qr.example.com/console/qradar/jsp/QRadar.jsp"
        "?appName=Sem&pageId=OffenseSummary&summaryId=1234"
    )
