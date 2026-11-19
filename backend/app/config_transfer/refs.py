"""Match file references for one console (CFG-1, D-78). No session and no provider.

Tuple order is the comment on each ``ResolutionCatalog`` field. A WAF endpoint
version is part of the key. An IPS version is ignored and does not break a tie.
Property kind and name are copied. A null widget binding is not a missing ref.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

type LogSourceRow = tuple[str, str, str | None]
type LogSourceTypeRow = tuple[int, str]
type LogSourceGroupRow = tuple[int, str, int | None]
type AnalyticsRuleRow = tuple[str, str, str | None, bool]
type WafEndpointRow = tuple[str, str, str | None, str, str, int]
type IpsEntityRow = tuple[str, str, str, str, int]
type NamedRow = tuple[str, str]

_UUID = re.compile(
    r"^\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?$"
)
_DANGLING = object()
# singular, plural, qualifier. Reasons name a count, never the looked-up text.
_KIND: dict[str, tuple[str, str, str]] = {
    "log_source": ("log source", "log sources", "this name and type"),
    "source_type": ("log source type", "log source types", "this name"),
    "group": ("log source group", "log source groups", "this name and parent"),
    "rule": (
        "analytics rule",
        "analytics rules",
        "this name, rule type, and building-block flag",
    ),
    "endpoint": ("WAF endpoint", "WAF endpoints", "this log source and these fields"),
    "ips": ("IPS entity", "IPS entities", "this log source, dimension, and value"),
    "watch": ("property watch", "property watches", "this name"),
    "destination": ("notification destination", "notification destinations", "this name"),
    "search": ("scheduled search", "scheduled searches", "this name"),
}
_WIDGET: dict[str, tuple[tuple[str, str], ...]] = {
    "chart.eps_observed_expected": (("source", "log_source"),),
    "waf.endpoint_traffic": (("waf_source", "log_source"), ("endpoint", "endpoint")),
    "chart.property_watch": (("watch_id", "watch"),),
    "chart.search_results": (("search_id", "search"),),
    "map.geography": (),
}
_LS_BAD = "log source ref needs name and type_name"
_RULE_BAD = "analytics rule ref needs name, rule_type, and is_building_block"
_EP_SHAPE = "WAF endpoint ref needs a log source and the endpoint fields"
_EP_FIELDS = (
    "WAF endpoint ref needs virtual_host, normalized_endpoint, "
    "http_method, and normalizer_version"
)
_IPS_BAD = "IPS entity ref needs a log source, dimension, and value_key"


class MatchOutcome(enum.StrEnum):
    EXACT = "exact"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class RefMatch:
    outcome: MatchOutcome
    ids: tuple[str | int, ...]
    reason: str

    @property
    def exact_id(self) -> str | int | None:
        if self.outcome is not MatchOutcome.EXACT or len(self.ids) != 1:
            return None
        return self.ids[0]


@dataclass(frozen=True, slots=True)
class WidgetBinding:
    key: str
    outcome: MatchOutcome | None  # None when the key is null or absent
    match: RefMatch | None = None


@dataclass(frozen=True, slots=True)
class WidgetConfigMatch:
    bindings: tuple[WidgetBinding, ...]
    unresolved: tuple[RefMatch, ...]


def _as_map(ref: object) -> Mapping[str, Any] | None:
    return cast(Mapping[str, Any], ref) if isinstance(ref, Mapping) else None


def _text(
    body: Mapping[str, Any] | None, key: str, *, null: bool = False
) -> tuple[bool, str | None]:
    """Present string, or string-or-null when ``null``. An omitted key is not null."""
    if body is None or key not in body:
        return False, None
    value = body[key]
    if isinstance(value, str) or (null and value is None):
        return True, cast(str | None, value)
    return False, None


def _whole(body: Mapping[str, Any], key: str) -> int | None:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _phrase(kind: str, count: int) -> str:
    one, many, qual = _KIND[kind]
    if count == 1:
        return f"1 {one} has {qual}"
    if count == 0:
        return f"no {one} has {qual}"
    return f"{count} {many} share {qual}"


def _done(ids: Sequence[str | int], kind: str) -> RefMatch:
    count = len(ids)
    if count == 1:
        outcome = MatchOutcome.EXACT
    elif count == 0:
        outcome = MatchOutcome.MISSING
    else:
        outcome = MatchOutcome.AMBIGUOUS
    return RefMatch(outcome, tuple(ids), _phrase(kind, count))


def _miss(reason: str) -> RefMatch:
    return RefMatch(MatchOutcome.MISSING, (), reason)


def _parent(row: LogSourceGroupRow, by_id: Mapping[int, LogSourceGroupRow]) -> object:
    parent_id = row[2]
    if parent_id is None:
        return None
    parent = by_id.get(parent_id)
    if parent is None:
        return _DANGLING
    return parent[1]


def _has_uuid(value: object) -> bool:
    if value is None or isinstance(value, int | float):
        return False
    if isinstance(value, str):
        return _UUID.fullmatch(value) is not None
    if isinstance(value, Mapping):
        return any(_has_uuid(key) or _has_uuid(item) for key, item in value.items())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return any(_has_uuid(item) for item in value)
    return False


def copy_property_ref(ref: object) -> dict[str, str]:
    """Return kind and name. Nothing is looked up."""
    kind, name = _text(_as_map(ref), "kind")[1], _text(_as_map(ref), "name")[1]
    if kind is None or name is None:
        raise ValueError("property ref must be an object of kind and name")
    return {"kind": kind, "name": name}


@dataclass(frozen=True, slots=True)
class ResolutionCatalog:
    log_sources: tuple[LogSourceRow, ...] = ()  # id, name, type_name
    log_source_types: tuple[LogSourceTypeRow, ...] = ()  # type_id, name
    log_source_groups: tuple[LogSourceGroupRow, ...] = ()  # qradar_id, name, parent_qradar_id
    analytics_rules: tuple[AnalyticsRuleRow, ...] = ()  # id, name, rule_type, is_building_block
    waf_endpoints: tuple[WafEndpointRow, ...] = ()  # id, source, host, path, method, version
    ips_entities: tuple[IpsEntityRow, ...] = ()  # id, source, dimension, value_key, version
    watches: tuple[NamedRow, ...] = ()  # id, name
    destinations: tuple[NamedRow, ...] = ()  # id, name
    searches: tuple[NamedRow, ...] = ()  # id, name

    # Built once, not per row: a linear scan per file row is O(rows x sources).
    # Values keep file order, so exact / missing / ambiguous reasons stay identical.
    _log_source_index: dict[tuple[str, str | None], tuple[str, ...]] = field(
        init=False, repr=False, compare=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        grouped: dict[tuple[str, str | None], list[str]] = {}
        for source_id, name, type_name in self.log_sources:
            grouped.setdefault((name, type_name), []).append(source_id)
        object.__setattr__(self, "_log_source_index", {
            key: tuple(ids) for key, ids in grouped.items()
        })

    def match_log_source(self, ref: object) -> RefMatch:
        body = _as_map(ref)
        _, name = _text(body, "name")
        present, type_name = _text(body, "type_name", null=True)
        if name is None or not present:
            return _miss(_LS_BAD)
        return _done(self._log_source_index.get((name, type_name), ()), "log_source")

    def match_log_source_type(self, ref: object) -> RefMatch:
        _, name = _text(_as_map(ref), "name")
        if name is None:
            return _miss(f"{_KIND['source_type'][0]} ref needs name")
        found: list[int] = []
        for type_id, type_name in self.log_source_types:
            if type_name == name and type_id not in found:
                found.append(type_id)
        return _done(found, "source_type")

    def match_log_source_group(self, ref: object) -> RefMatch:
        body = _as_map(ref)
        _, name = _text(body, "name")
        present, parent_name = _text(body, "parent_name", null=True)
        if name is None or not present:
            return _miss(f"{_KIND['group'][0]} ref needs name and parent_name")
        by_id: dict[int, LogSourceGroupRow] = {}
        for row in self.log_source_groups:
            by_id.setdefault(row[0], row)
        found = [
            row[0]
            for row in self.log_source_groups
            if row[1] == name and _parent(row, by_id) == parent_name
        ]
        return _done(found, "group")

    def match_analytics_rule(self, ref: object) -> RefMatch:
        body = _as_map(ref)
        _, name = _text(body, "name")
        present, rule_type = _text(body, "rule_type", null=True)
        flag = body.get("is_building_block") if body else None
        if name is None or not present or not isinstance(flag, bool):
            return _miss(_RULE_BAD)
        want = (name, rule_type, flag)
        found = [row[0] for row in self.analytics_rules if row[1:] == want]
        return _done(found, "rule")

    def match_waf_endpoint(self, ref: object) -> RefMatch:
        body = _as_map(ref)
        if body is None or "log_source" not in body:
            return _miss(_EP_SHAPE)
        source = self.match_log_source(body["log_source"])
        if source.outcome is not MatchOutcome.EXACT:
            text = f"WAF endpoint log source: {source.reason}"
            return RefMatch(source.outcome, (), text)
        present, host = _text(body, "virtual_host", null=True)
        _, path = _text(body, "normalized_endpoint")
        _, method = _text(body, "http_method")
        version = _whole(body, "normalizer_version")
        if not present or path is None or method is None or version is None:
            return _miss(_EP_FIELDS)
        want = (cast(str, source.exact_id), host, path, method, version)
        found = [row[0] for row in self.waf_endpoints if row[1:] == want]
        return _done(found, "endpoint")

    def match_ips_entity(self, ref: object) -> RefMatch:
        body = _as_map(ref)
        if body is None or "log_source" not in body:
            return _miss(_IPS_BAD)
        source = self.match_log_source(body["log_source"])
        if source.outcome is not MatchOutcome.EXACT:
            text = f"IPS entity log source: {source.reason}"
            return RefMatch(source.outcome, (), text)
        _, dimension = _text(body, "dimension")
        _, value_key = _text(body, "value_key")
        if dimension is None or value_key is None:
            return _miss(_IPS_BAD)
        # row[1:4] is source, dimension, value_key. row[4] is version and is ignored.
        want = (cast(str, source.exact_id), dimension, value_key)
        found = [row[0] for row in self.ips_entities if row[1:4] == want]
        return _done(found, "ips")

    def match_watch(self, ref: object) -> RefMatch:
        return self._named(self.watches, ref, "watch")

    def match_destination(self, ref: object) -> RefMatch:
        return self._named(self.destinations, ref, "destination")

    def match_search(self, ref: object) -> RefMatch:
        return self._named(self.searches, ref, "search")

    def match_widget_config(self, widget_type: str, config: object) -> WidgetConfigMatch:
        if not isinstance(config, Mapping):
            return WidgetConfigMatch((), (_miss("widget config is not an object"),))
        spec = _WIDGET.get(widget_type)
        if spec is None:
            if _has_uuid(config):
                return WidgetConfigMatch((), (_miss("widget config contains an id"),))
            return WidgetConfigMatch((), ())
        bindings: list[WidgetBinding] = []
        pending: list[RefMatch] = []
        for key, kind in spec:
            if key not in config or config[key] is None:
                bindings.append(WidgetBinding(key, None))
                continue
            value = config[key]
            if isinstance(value, str) and _UUID.fullmatch(value):
                match = _miss(f"widget binding {key} is an id, not a reference")
            else:
                match = self._bind(kind, value)
            bindings.append(WidgetBinding(key, match.outcome, match))
            if match.outcome is not MatchOutcome.EXACT:
                pending.append(match)
        return WidgetConfigMatch(tuple(bindings), tuple(pending))

    def _named(self, rows: Sequence[NamedRow], ref: object, kind: str) -> RefMatch:
        _, name = _text(_as_map(ref), "name")
        if name is None:
            return _miss(f"{_KIND[kind][0]} ref needs name")
        found = [row_id for row_id, row_name in rows if row_name == name]
        return _done(found, kind)

    def _bind(self, kind: str, ref: object) -> RefMatch:
        bound = {
            "log_source": self.match_log_source,
            "endpoint": self.match_waf_endpoint,
            "watch": self.match_watch,
            "search": self.match_search,
        }.get(kind)
        if bound is None:
            return _miss("widget binding is not a known reference")
        return bound(ref)
