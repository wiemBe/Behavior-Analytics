"""Pure folds from a cell's keyed rows to IPS facts (design D.4, D.5).

Raw values (ports, addresses, QIDs, signatures, action values) live only inside
these functions. What leaves them is bounded: the top `max_entities` entities of
a dimension, and at most `CONTRIBUTOR_LIMIT` `(address, number)` contributors for
source IPs and fan-out. Source IPs are never entities.

A cell without a measurement (`FAILED`, `REJECTED`, `NOT_COLLECTED`) folds to
None, never to a zero. A `COMPLETE` cell with no rows is a measured zero.
`LOWER_BOUND` is carried into every fact built from such a cell.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from app.ips.keyed_search import IpsCellOutcome
from app.ips.profile import IpsFieldMapping, _action_key
from app.models.enums import IpsCellState, IpsDimension, IpsMeasurement
from app.providers.dto import IpsKeyedRow

CONTRIBUTOR_LIMIT = 10
SIGNATURE_VALUE_MAX = 128
#: IANA protocol numbers named in a port entity's `value_key`; any other 1-254 is its number.
PROTOCOL_NAMES = {1: "icmp", 6: "tcp", 17: "udp", 58: "ipv6-icmp", 132: "sctp"}
GENERIC_NAME_PREFIX = "Unknown "


class IpsFoldError(ValueError):
    """A fold was handed something the collection contract rules out: a bug, not data."""


@dataclass(frozen=True)
class IpsTotalsFact:
    events: int
    records: int
    measurement: IpsMeasurement


@dataclass(frozen=True)
class IpsEntityFact:
    value_key: str
    events: int
    #: `QIDNAME(qid)` for a normalized signature; display only.
    display_name: str | None = None
    #: The name starts with "Unknown ": a claim about this signature is not safe (§D.8).
    generic_name: bool = False


@dataclass(frozen=True)
class IpsEntityBucketFact:
    """DESTINATION_PORT, DESTINATION_IP or SIGNATURE for one (source, bucket)."""

    dimension: IpsDimension
    measurement: IpsMeasurement
    #: At most `max_entities`, by events then `value_key`.
    entities: tuple[IpsEntityFact, ...]
    #: Distinct attributable entities before the cut.
    entity_count: int
    #: The sum over every row, not just the kept entities.
    dimension_events: int
    #: Rows that could not form an entity (counted in `dimension_events`).
    unattributed_events: int

    @property
    def covered_events(self) -> int:
        return self.dimension_events


@dataclass(frozen=True)
class IpsSourceIpFact:
    measurement: IpsMeasurement
    source_events: int
    #: Distinct source addresses: the row count, a lower bound under LOWER_BOUND.
    distinct_sources: int
    top1_events: int
    top5_events: int
    unattributed_events: int
    #: At most `CONTRIBUTOR_LIMIT` `(address, events)`.
    contributors: tuple[tuple[str, int], ...]

    @property
    def covered_events(self) -> int:
        return self.source_events


@dataclass(frozen=True)
class IpsFanoutFact:
    measurement: IpsMeasurement
    #: The largest distinct destination-port count of one source address.
    max_distinct_ports: int
    #: At most `CONTRIBUTOR_LIMIT` `(address, distinct ports)`.
    contributors: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class IpsActionFact:
    measurement: IpsMeasurement
    blocked_events: int
    allowed_events: int
    #: Every value the profile's map does not name. Never counted as allowed.
    unmapped_events: int

    @property
    def covered_events(self) -> int:
        return self.blocked_events + self.allowed_events + self.unmapped_events


@dataclass(frozen=True)
class IpsCoverage:
    ratio: float
    #: The dimension search was truncated: the true share is at least `ratio`.
    lower_bound: bool


IpsFact = (
    IpsTotalsFact
    | IpsEntityBucketFact
    | IpsSourceIpFact
    | IpsFanoutFact
    | IpsActionFact
)


def protocol_name(protocol_id: int | None) -> str:
    """`tcp`, `udp`, ...; the number for any other 1-254; `unknown` for 0, 255 and absent."""
    if protocol_id is None or protocol_id <= 0 or protocol_id >= 255:
        return "unknown"
    return PROTOCOL_NAMES.get(protocol_id, str(protocol_id))


def canonical_ip(value: str | None) -> str | None:
    """The address in canonical text, or None when it does not parse."""
    if value is None:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _measurement(outcome: IpsCellOutcome) -> IpsMeasurement | None:
    if outcome.state is IpsCellState.COMPLETE:
        return IpsMeasurement.EXACT
    if outcome.state is IpsCellState.LOWER_BOUND:
        return IpsMeasurement.LOWER_BOUND
    return None


def _expect(outcome: IpsCellOutcome, dimension: IpsDimension) -> None:
    if outcome.dimension is not dimension:
        raise IpsFoldError(f"expected a {dimension.value} outcome, got {outcome.dimension.value}")


def fold_totals(outcome: IpsCellOutcome) -> IpsTotalsFact | None:
    _expect(outcome, IpsDimension.TOTALS)
    measurement = _measurement(outcome)
    if measurement is None:
        return None
    return IpsTotalsFact(
        events=sum(row.events for row in outcome.rows),
        records=sum(row.records for row in outcome.rows),
        measurement=measurement,
    )


def _entity_key(dimension: IpsDimension, row: IpsKeyedRow) -> tuple[str, str | None] | None:
    """`(value_key, display name)` of a row, or None when it forms no entity."""
    if dimension is IpsDimension.DESTINATION_PORT:
        port = row.destination_port
        if port is None:
            return None
        if port == 0:
            raise IpsFoldError("port 0 reached the fold; the search excludes it")
        if not 0 < port <= 65535:
            return None
        return f"{port}/{protocol_name(row.protocol_id)}", None
    if dimension is IpsDimension.DESTINATION_IP:
        address = canonical_ip(row.destination_ip)
        return (address, None) if address is not None else None
    if row.qid is not None:
        return (f"qid:{row.qid}", row.name) if row.qid > 0 else None
    value = (row.signature or "").strip()[:SIGNATURE_VALUE_MAX].strip()
    return (f"sig:{value}", None) if value else None


def fold_entity_dimension(
    outcome: IpsCellOutcome, *, max_entities: int
) -> IpsEntityBucketFact | None:
    """The top entities of a port, destination or signature cell, with the full total."""
    dimension = outcome.dimension
    if dimension not in (
        IpsDimension.DESTINATION_PORT,
        IpsDimension.DESTINATION_IP,
        IpsDimension.SIGNATURE,
    ):
        raise IpsFoldError(f"{dimension.value} is not an entity dimension")
    measurement = _measurement(outcome)
    if measurement is None:
        return None
    if max_entities < 1:
        raise IpsFoldError("max_entities must be positive")
    events: dict[str, int] = {}
    names: dict[str, str | None] = {}
    unattributed = 0
    total = 0
    for row in outcome.rows:
        total += row.events
        keyed = _entity_key(dimension, row)
        if keyed is None:
            unattributed += row.events
            continue
        value_key, name = keyed
        events[value_key] = events.get(value_key, 0) + row.events
        if names.get(value_key) is None:
            names[value_key] = name
    ranked = sorted(events.items(), key=lambda item: (-item[1], item[0]))
    return IpsEntityBucketFact(
        dimension=dimension,
        measurement=measurement,
        entities=tuple(
            IpsEntityFact(
                value_key=value_key,
                events=count,
                display_name=names[value_key],
                generic_name=(names[value_key] or "").startswith(GENERIC_NAME_PREFIX),
            )
            for value_key, count in ranked[:max_entities]
        ),
        entity_count=len(ranked),
        dimension_events=total,
        unattributed_events=unattributed,
    )


def fold_source_ips(outcome: IpsCellOutcome) -> IpsSourceIpFact | None:
    _expect(outcome, IpsDimension.SOURCE_IP)
    measurement = _measurement(outcome)
    if measurement is None:
        return None
    by_address: dict[str, int] = {}
    unattributed = 0
    total = 0
    for row in outcome.rows:
        total += row.events
        address = canonical_ip(row.source_ip)
        if address is None:
            unattributed += row.events
            continue
        by_address[address] = by_address.get(address, 0) + row.events
    ranked = sorted(by_address.items(), key=lambda item: (-item[1], item[0]))
    return IpsSourceIpFact(
        measurement=measurement,
        source_events=total,
        distinct_sources=len(ranked),
        top1_events=sum(count for _, count in ranked[:1]),
        top5_events=sum(count for _, count in ranked[:5]),
        unattributed_events=unattributed,
        contributors=tuple(ranked[:CONTRIBUTOR_LIMIT]),
    )


def fold_port_fanout(outcome: IpsCellOutcome) -> IpsFanoutFact | None:
    _expect(outcome, IpsDimension.PORT_FANOUT)
    measurement = _measurement(outcome)
    if measurement is None:
        return None
    by_address: dict[str, int] = {}
    for row in outcome.rows:
        address = canonical_ip(row.source_ip)
        if address is None or row.distinct_ports is None:
            continue
        by_address[address] = max(by_address.get(address, 0), row.distinct_ports)
    ranked = sorted(by_address.items(), key=lambda item: (-item[1], item[0]))
    return IpsFanoutFact(
        measurement=measurement,
        max_distinct_ports=ranked[0][1] if ranked else 0,
        contributors=tuple(ranked[:CONTRIBUTOR_LIMIT]),
    )


def fold_actions(outcome: IpsCellOutcome, mapping: IpsFieldMapping) -> IpsActionFact | None:
    """Blocked, allowed and unmapped events through the profile's value map.

    A value is matched by `_action_key` (strip and casefold). Anything the map
    does not name, including a missing value, is unmapped: it is never allowed.
    """
    _expect(outcome, IpsDimension.ACTION)
    measurement = _measurement(outcome)
    if measurement is None:
        return None
    if mapping.action is None:
        raise IpsFoldError("an ACTION cell needs a profile with an ACTION field")
    blocked = {_action_key(v) for v in mapping.blocked_actions}
    allowed = {_action_key(v) for v in mapping.allowed_actions}
    counts = {"blocked": 0, "allowed": 0, "unmapped": 0}
    for row in outcome.rows:
        key = _action_key(row.action) if row.action is not None else None
        if key is not None and key in blocked:
            counts["blocked"] += row.events
        elif key is not None and key in allowed:
            counts["allowed"] += row.events
        else:
            counts["unmapped"] += row.events
    return IpsActionFact(
        measurement=measurement,
        blocked_events=counts["blocked"],
        allowed_events=counts["allowed"],
        unmapped_events=counts["unmapped"],
    )


def fold_outcome(
    outcome: IpsCellOutcome,
    *,
    mapping: IpsFieldMapping | None = None,
    max_entities: int,
) -> IpsFact | None:
    """Dispatch on the outcome's dimension. None when the cell carries no measurement."""
    dimension = outcome.dimension
    if dimension is IpsDimension.TOTALS:
        return fold_totals(outcome)
    if dimension is IpsDimension.SOURCE_IP:
        return fold_source_ips(outcome)
    if dimension is IpsDimension.PORT_FANOUT:
        return fold_port_fanout(outcome)
    if dimension is IpsDimension.ACTION:
        if mapping is None:
            raise IpsFoldError("folding ACTION needs the source's field mapping")
        return fold_actions(outcome, mapping)
    return fold_entity_dimension(outcome, max_entities=max_entities)


def coverage(
    fact: IpsEntityBucketFact | IpsSourceIpFact | IpsActionFact | None,
    totals: IpsTotalsFact | None,
) -> IpsCoverage | None:
    """The dimension's summed events over the cell's totals (design D.5).

    None when the dimension fact or the totals are missing (failed, rejected,
    not collected, or totals that are only a lower bound) or the totals are zero:
    an unmeasurable share is never drawn as 0. A measured 0.0 is a dimension that saw nothing
    while the totals did. Under a LOWER_BOUND dimension the ratio is flagged as
    a lower bound.
    """
    if fact is None or totals is None or totals.events <= 0:
        return None
    if totals.measurement is not IpsMeasurement.EXACT:
        return None
    return IpsCoverage(
        ratio=fact.covered_events / totals.events,
        lower_bound=fact.measurement is IpsMeasurement.LOWER_BOUND,
    )
