"""Reference matching for CFG-1 slice 1b. No database and no provider."""

from __future__ import annotations

import inspect
import time
from pathlib import Path

import pytest

import app.config_transfer as pkg
import app.config_transfer.refs as refs
from app.config_transfer.refs import MatchOutcome, RefMatch, ResolutionCatalog, copy_property_ref

U = "00000000-0000-4000-8000-000000000001"
EX, AM, MI = MatchOutcome.EXACT, MatchOutcome.AMBIGUOUS, MatchOutcome.MISSING
ONE = "1 log source has this name and type"
NONE_LS = "no log source has this name and type"
TWO_LS = "2 log sources share this name and type"
TWO_GR = "2 log source groups share this name and parent"
LS_BAD = "log source ref needs name and type_name"
EP_FIELDS = (
    "WAF endpoint ref needs virtual_host, normalized_endpoint, "
    "http_method, and normalizer_version"
)
EP_SHAPE = "WAF endpoint ref needs a log source and the endpoint fields"
RULE_BAD = "analytics rule ref needs name, rule_type, and is_building_block"
IPS_BAD = "IPS entity ref needs a log source, dimension, and value_key"
Case = tuple[object, MatchOutcome, tuple[object, ...], str | None]


def ok(
    got: RefMatch,
    outcome: MatchOutcome,
    ids: tuple[object, ...],
    reason: str | None = None,
) -> None:
    exact = ids[0] if outcome is EX else None
    assert (got.outcome, got.ids, got.exact_id) == (outcome, ids, exact)
    if reason is not None:
        assert got.reason == reason


def run(cat: ResolutionCatalog, method: str, rows: tuple[Case, ...]) -> None:
    for ref, outcome, ids, reason in rows:
        ok(getattr(cat, method)(ref), outcome, ids, reason)


def ls(name: str, type_name: str | None) -> dict[str, object]:
    return {"name": name, "type_name": type_name}


def ep(source: dict[str, object], **over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "log_source": source,
        "virtual_host": None,
        "normalized_endpoint": "/login",
        "http_method": "GET",
        "normalizer_version": 1,
    }
    body.update(over)
    return body


def ent(source: dict[str, object], **over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "log_source": source,
        "dimension": "SIGNATURE",
        "value_key": "1:2",
    }
    body.update(over)
    return body


def rule(
    name: str, rule_type: object, flag: object, *, omit_type: bool = False,
) -> dict[str, object]:
    body: dict[str, object] = {"name": name, "is_building_block": flag}
    if not omit_type:
        body["rule_type"] = rule_type
    return body


def test_package_init_and_no_io() -> None:
    path = pkg.__file__
    assert path is not None
    text = Path(path).read_text(encoding="utf-8")
    assert text == '"""Operator configuration export and import (CFG-1, D-78)."""\n'
    source = inspect.getsource(refs)
    for banned in ("sqlalchemy", "app.providers", "app.models", "app.core.database", "open("):
        assert banned not in source


def test_log_source_type_group_and_rule() -> None:
    sources = ResolutionCatalog(log_sources=(
        ("1", "Edge", "F5"), ("2", "Edge", "F5"),
        ("3", "Other", None), ("4", "Alone", "Cisco"),
    ))
    run(sources, "match_log_source", (
        (ls("Alone", "Cisco"), EX, ("4",), ONE),
        (ls("Gone", "F5"), MI, (), NONE_LS),
        (ls("Edge", "F5"), AM, ("1", "2"), TWO_LS),
        (ls("Other", None), EX, ("3",), None),
        (ls("Other", "F5"), MI, (), None),
        (ls("alone", "Cisco"), MI, (), None),
        (ls("Alone ", "Cisco"), MI, (), None),
        ({"name": "Other"}, MI, (), LS_BAD),
        ({"name": None, "type_name": "Cisco"}, MI, (), LS_BAD),
        ("Alone", MI, (), LS_BAD),
    ))
    types = ResolutionCatalog(log_source_types=(
        (10, "Windows"), (10, "Windows"), (11, "Cisco"), (12, "Cisco"),
    ))
    run(types, "match_log_source_type", (
        ({"name": "Windows"}, EX, (10,), None),
        ({"name": "Cisco"}, AM, (11, 12), "2 log source types share this name"),
        ({"name": "Gone"}, MI, (), "no log source type has this name"),
        ({}, MI, (), "log source type ref needs name"),
        ({"name": None}, MI, (), "log source type ref needs name"),
    ))
    groups = ResolutionCatalog(log_source_groups=(
        (1, "Root", None), (2, "DMZ", 1), (3, "DMZ", None),
        (4, "Lab", None), (5, "Lab", None), (6, "Orphan", 99),
    ))
    run(groups, "match_log_source_group", (
        ({"name": "DMZ", "parent_name": "Root"}, EX, (2,), None),
        ({"name": "DMZ", "parent_name": None}, EX, (3,), None),
        ({"name": "Root", "parent_name": None}, EX, (1,), None),
        ({"name": "Lab", "parent_name": None}, AM, (4, 5), TWO_GR),
        ({"name": "Orphan", "parent_name": None}, MI, (), None),
        ({"name": "Gone", "parent_name": None}, MI, (), None),
        ({"name": "DMZ"}, MI, (), "log source group ref needs name and parent_name"),
    ))
    rules = ResolutionCatalog(analytics_rules=(
        ("r1", "Excessive Firewall Denies", "EVENT", False),
        ("r2", "Shared", None, False),
        ("r3", "Shared", None, False),
        ("r4", "Block", "EVENT", True),
    ))
    run(rules, "match_analytics_rule", (
        (rule("Excessive Firewall Denies", "EVENT", False), EX, ("r1",), None),
        (rule("Block", "EVENT", True), EX, ("r4",), None),
        (rule("Block", None, True), MI, (), None),
        (rule("Block", "EVENT", False), MI, (), None),
        (rule("Shared", None, False), AM, ("r2", "r3"), None),
        (rule("Nobody", "EVENT", False), MI, (), None),
        (rule("Shared", None, 0), MI, (), RULE_BAD),
        (rule("Shared", None, False, omit_type=True), MI, (), RULE_BAD),
    ))


def test_endpoint_and_ips_entity() -> None:
    edge, gone, shared = ls("Edge", "F5"), ls("Gone", "F5"), ls("Shared", "F5")
    waf = ResolutionCatalog(
        log_sources=(
            ("ls", "Edge", "F5"), ("other", "Other", "F5"),
            ("a", "Shared", "F5"), ("b", "Shared", "F5"),
        ),
        waf_endpoints=(
            ("e1", "ls", None, "/login", "GET", 1),
            ("e2", "ls", None, "/login", "GET", 2),
            ("e3", "ls", "", "/login", "GET", 1),
            ("e4", "other", None, "/login", "GET", 1),
            ("e5", "ls", "app", "/login", "GET", 1),
            ("e6", "ls", "app", "/login", "GET", 1),
        ),
    )
    no_host = ep(edge)
    del no_host["virtual_host"]
    run(waf, "match_waf_endpoint", (
        (ep(edge), EX, ("e1",), None),
        (ep(edge, normalizer_version=2), EX, ("e2",), None),
        (ep(edge, virtual_host=""), EX, ("e3",), None),
        (ep(edge, virtual_host="app"), AM, ("e5", "e6"), None),
        (ep(edge, http_method="POST"), MI, (), None),
        (ep(edge, http_method="get"), MI, (), None),
        (ep(gone), MI, (), "WAF endpoint log source: " + NONE_LS),
        (ep(shared), AM, (), "WAF endpoint log source: " + TWO_LS),
        ({"virtual_host": None}, MI, (), EP_SHAPE),
        (no_host, MI, (), EP_FIELDS),
        (ep(edge, normalizer_version=True), MI, (), EP_FIELDS),
    ))
    cisco, missing, both = ls("Edge", "Cisco"), ls("Gone", "Cisco"), ls("Shared", "Cisco")
    ips = ResolutionCatalog(
        log_sources=(
            ("ls", "Edge", "Cisco"), ("other", "Other", "Cisco"),
            ("a", "Shared", "Cisco"), ("b", "Shared", "Cisco"),
        ),
        ips_entities=(
            ("e", "ls", "SIGNATURE", "1:2", 9),
            ("other-entity", "other", "SIGNATURE", "1:2", 1),
            ("old", "ls", "DESTINATION_PORT", "443", 1),
            ("new", "ls", "DESTINATION_PORT", "443", 9),
        ),
    )
    port = ent(cisco, dimension="DESTINATION_PORT", value_key="443", normalizer_version=1)
    run(ips, "match_ips_entity", (
        (ent(cisco, normalizer_version=1), EX, ("e",), None),
        (port, AM, ("old", "new"), None),
        (ent(cisco, value_key="missing"), MI, (), None),
        (ent(missing), MI, (), "IPS entity log source: " + NONE_LS),
        (ent(both), AM, (), None),
        ({"dimension": "SIGNATURE"}, MI, (), IPS_BAD),
    ))


@pytest.mark.parametrize(
    ("field", "method", "bad"),
    [
        ("watches", "match_watch", "property watch ref needs name"),
        ("destinations", "match_destination", "notification destination ref needs name"),
        ("searches", "match_search", "scheduled search ref needs name"),
    ],
)
def test_named_ref_exact_missing_and_ambiguous(field: str, method: str, bad: str) -> None:
    cat = ResolutionCatalog(**{field: (("1", "alpha"), ("2", "beta"), ("3", "beta"))})
    exact = getattr(cat, method)({"name": "alpha"})
    ok(exact, EX, ("1",))
    assert "alpha" not in exact.reason
    ok(getattr(cat, method)({"name": "gone"}), MI, ())
    ambiguous = getattr(cat, method)({"name": "beta"})
    ok(ambiguous, AM, ("2", "3"))
    assert "beta" not in ambiguous.reason
    ok(getattr(cat, method)({}), MI, (), bad)
    ok(getattr(cat, method)({"name": None}), MI, (), bad)


def test_watch_destination_and_search_are_separate() -> None:
    cat = ResolutionCatalog(watches=(("w", "same"),))
    ok(cat.match_watch({"name": "same"}), EX, ("w",))
    ok(cat.match_destination({"name": "same"}), MI, ())
    ok(cat.match_search({"name": "same"}), MI, ())


def test_property_ref_is_copied_and_not_looked_up() -> None:
    copied = copy_property_ref({"kind": "CUSTOM_PROPERTY", "name": "NotAWatch", "extra": 1})
    assert copied == {"kind": "CUSTOM_PROPERTY", "name": "NotAWatch"}
    assert copy_property_ref({"kind": "NORMALIZED", "name": "sourceip"}) == {
        "kind": "NORMALIZED",
        "name": "sourceip",
    }
    ok(ResolutionCatalog(watches=(("w", "Action"),)).match_watch({"name": "NotAWatch"}), MI, ())
    with pytest.raises(ValueError, match="kind and name"):
        copy_property_ref({"kind": "CUSTOM_PROPERTY"})
    with pytest.raises(ValueError, match="kind and name"):
        copy_property_ref(["CUSTOM_PROPERTY", "Action"])


def test_widget_bindings() -> None:
    edge = ls("Edge", "F5")
    empty = ResolutionCatalog()
    cat = ResolutionCatalog(
        log_sources=(
            ("ls", "Edge", "F5"), ("a", "Shared", "F5"),
            ("b", "Shared", "F5"), ("id", U, None),
        ),
        watches=(("watch-1", "Denied actions"),),
        searches=(("search-1", "Denied by WAF"),),
        waf_endpoints=(("ep", "ls", None, "/login", "GET", 1),),
    )

    def view(catalog: ResolutionCatalog, kind: str, config: object) -> refs.WidgetConfigMatch:
        return catalog.match_widget_config(kind, config)

    eps = "chart.eps_observed_expected"
    null = view(empty, eps, {"source": None})
    assert null.unresolved == ()
    assert [(item.key, item.outcome) for item in null.bindings] == [("source", None)]
    absent = view(empty, eps, {})
    assert absent.unresolved == () and absent.bindings[0].outcome is None
    waf = view(empty, "waf.endpoint_traffic", {"waf_source": None, "endpoint": None})
    assert waf.unresolved == () and [item.outcome for item in waf.bindings] == [None, None]
    assert view(empty, "chart.property_watch", {"watch_id": None}).unresolved == ()
    search = view(empty, "chart.search_results", {"search_id": None, "metric_key": None})
    assert search.unresolved == ()
    geo = view(empty, "map.geography", {"direction": "DESTINATION"})
    assert geo.unresolved == () and geo.bindings == ()
    metric = view(empty, "chart.search_results", {"search_id": None, "metric_key": U})
    assert metric.unresolved == ()
    exact = view(cat, eps, {"source": edge})
    assert exact.unresolved == () and exact.bindings[0].match is not None
    assert exact.bindings[0].match.exact_id == "ls"
    missing = view(cat, eps, {"source": ls("Gone", "F5")})
    assert missing.unresolved[0].outcome is MI
    ambiguous = view(cat, eps, {"source": ls("Shared", "F5")})
    assert ambiguous.unresolved[0].outcome is AM
    assert ambiguous.bindings[0].match is not None
    assert ambiguous.bindings[0].match.exact_id is None
    watch = view(cat, "chart.property_watch", {"watch_id": {"name": "Denied actions"}})
    assert watch.unresolved == () and watch.bindings[0].match is not None
    assert watch.bindings[0].match.exact_id == "watch-1"
    found = view(cat, "chart.search_results", {
        "search_id": {"name": "Denied by WAF"}, "metric_key": "events",
    })
    assert found.unresolved == () and found.bindings[0].match is not None
    assert found.bindings[0].match.exact_id == "search-1"
    endpoint = view(cat, "waf.endpoint_traffic", {"waf_source": None, "endpoint": ep(edge)})
    assert endpoint.unresolved == () and endpoint.bindings[0].outcome is None
    assert endpoint.bindings[1].match is not None
    assert endpoint.bindings[1].match.exact_id == "ep"
    as_id = view(cat, eps, {"source": U})
    ok(as_id.unresolved[0], MI, (), "widget binding source is an id, not a reference")
    plain = view(empty, "chart.future", {"source": None, "label": "Edge"})
    assert plain.unresolved == () and plain.bindings == ()
    assert view(empty, "chart.future", {"nested": {"id": None}}).unresolved == ()
    nested = view(empty, "chart.future", {"nested": [{"id": U}]})
    ok(nested.unresolved[0], MI, (), "widget config contains an id")
    assert U not in nested.unresolved[0].reason
    ok(view(empty, "chart.future", U).unresolved[0], MI, (), "widget config is not an object")


def test_log_source_index_resolves_eight_thousand_rows_quickly() -> None:
    count = 8_000
    catalog = ResolutionCatalog(log_sources=tuple(
        (f"id-{i}", f"source-{i}", "F5") for i in range(count)))
    started = time.perf_counter()
    for i in range(count):
        ok(catalog.match_log_source(ls(f"source-{i}", "F5")), EX, (f"id-{i}",))
    # A per-row linear scan takes several seconds at this size; the index is milliseconds.
    assert time.perf_counter() - started < 2.0
