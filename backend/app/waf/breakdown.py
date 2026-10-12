"""Fold one breakdown search into per-endpoint counts. Pure; no I/O.

A breakdown groups a source's requests by the raw endpoint key plus one field
(`app.models.enums.WafBreakdown`). Normalization is many-to-one, so raw groups
are summed into normalized endpoints exactly as `fold_groups` does for volume.
For each endpoint the groups' sum is the denominator of whatever ratio the
breakdown supports, and the matching groups' sum is the numerator -- both from
the same search, so they describe the same population of events.

What each breakdown measures per endpoint:

  * ACTION -- requests carrying an action value; of those, the ones the
    profile's map names BLOCKED; and the ones it names in neither list.
    Unmapped values are never treated as allowed: while any exist, the blocked
    count is only a lower bound.
  * RESPONSE_CODE -- requests carrying a response-code value; of those, 4xx and
    5xx separately; and values that are not a three-digit status. A 403 is
    counted as a 4xx response, never as a WAF block.
  * SOURCE_IP -- requests attributed to a source IP; the largest source IP's
    requests, the five largest's, and the number of distinct source IPs. A
    value that is not an address is not attributed: it counts in neither
    `events` nor any ranking, only in `unattributed`, so a flood of garbage
    values can never pose as a dominant client. It lowers the breakdown's
    coverage (`client_events / request_count`) instead, and a detector's
    coverage floor then yields INSUFFICIENT_DATA. The
    address is QRadar's normalized `sourceip`: behind a proxy, load balancer or
    NAT it is the intermediary, not the end user, and nothing here claims more.

When the search was truncated every value is the visible part only. Which
verdict that can support is not decided here but by each detector's declared
input requirements (`app.anomaly.capabilities`).

Raw values live only inside these functions. Action and response-code values
are reduced to counts; source IPs survive only as the bounded top contributors
of an endpoint whose largest client is material.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass, field

from app.providers.dto import WafBreakdownBatch, WafBreakdownGroup
from app.waf.normalize import (
    normalize_endpoint,
    normalize_method,
    normalize_response_code,
    normalize_virtual_host,
)

EndpointKey = tuple[str | None, str, str]

#: Contributors stored per endpoint, per bucket, at most.
TOP_CLIENTS_STORED = 5


@dataclass
class ActionCounts:
    events: int = 0
    blocked: int = 0
    unmapped: int = 0


@dataclass
class ResponseCounts:
    events: int = 0
    client_errors: int = 0
    server_errors: int = 0
    unclassified: int = 0


@dataclass
class ClientCounts:
    """Requests attributed to a parseable source IP, and the ones that were not.

    `events` and `by_source` cover attributed requests only. `unattributed`
    counts the requests whose value is not an address: they are kept out of
    every ranking and out of the stored contributors, so they cannot look like
    a client, and out of `events`, so they show up as lost coverage.
    """

    events: int = 0
    unattributed: int = 0
    by_source: dict[str, int] = field(default_factory=dict)

    @property
    def ranked(self) -> list[tuple[str, int]]:
        """Largest first, ties broken by address, so the order is total."""
        return sorted(self.by_source.items(), key=lambda kv: (-kv[1], kv[0]))

    @property
    def top1(self) -> int:
        return max(self.by_source.values(), default=0)

    @property
    def top5(self) -> int:
        return sum(n for _ip, n in self.ranked[:TOP_CLIENTS_STORED])

    @property
    def sources(self) -> int:
        return len(self.by_source)

    def contributors(self, *, min_top_requests: int) -> list[dict[str, object]] | None:
        """The bounded contributor list, or None when the largest is immaterial."""
        if self.top1 < min_top_requests:
            return None
        return [
            {"source_ip": ip, "requests": n} for ip, n in self.ranked[:TOP_CLIENTS_STORED]
        ]


def endpoint_key(group: WafBreakdownGroup) -> EndpointKey | None:
    """The normalized endpoint a raw group belongs to, or None when unusable."""
    endpoint = normalize_endpoint(group.raw_url)
    method = normalize_method(group.raw_method)
    if not endpoint.is_usable or endpoint.value is None or method is None:
        return None
    return (normalize_virtual_host(group.raw_virtual_host), endpoint.value, method)


def _fold[C](
    batch: WafBreakdownBatch,
    make: Callable[[], C],
    add: Callable[[C, WafBreakdownGroup], None],
) -> tuple[dict[EndpointKey, C], int]:
    out: dict[EndpointKey, C] = {}
    unusable = 0
    for group in batch.groups:
        key = endpoint_key(group)
        if key is None or group.raw_value is None:
            unusable += 1
            continue
        counts = out.get(key)
        if counts is None:
            counts = out[key] = make()
        add(counts, group)
    return out, unusable


def fold_actions(
    batch: WafBreakdownBatch, outcome: Callable[[str], str | None]
) -> tuple[dict[EndpointKey, ActionCounts], int]:
    """`outcome` maps a raw action value to BLOCKED, ALLOWED or None (unmapped)."""

    def add(c: ActionCounts, g: WafBreakdownGroup) -> None:
        c.events += g.request_count
        decided = outcome(g.raw_value or "")
        if decided == "BLOCKED":
            c.blocked += g.request_count
        elif decided is None:
            c.unmapped += g.request_count

    return _fold(batch, ActionCounts, add)


def fold_response_codes(batch: WafBreakdownBatch) -> tuple[dict[EndpointKey, ResponseCounts], int]:
    def add(c: ResponseCounts, g: WafBreakdownGroup) -> None:
        c.events += g.request_count
        code = normalize_response_code(g.raw_value)
        if code is None:
            c.unclassified += g.request_count
        elif 400 <= code <= 499:
            c.client_errors += g.request_count
        elif 500 <= code <= 599:
            c.server_errors += g.request_count

    return _fold(batch, ResponseCounts, add)


def normalize_source_ip(raw: str | None) -> str | None:
    """The canonical address, or None when the value is not an address."""
    try:
        return str(ipaddress.ip_address((raw or "").strip()))
    except ValueError:
        return None


def fold_source_ips(batch: WafBreakdownBatch) -> tuple[dict[EndpointKey, ClientCounts], int]:
    def add(c: ClientCounts, g: WafBreakdownGroup) -> None:
        ip = normalize_source_ip(g.raw_value)
        if ip is None:
            c.unattributed += g.request_count
            return
        c.events += g.request_count
        # One source IP reaching an endpoint through several raw URLs is one
        # contributor: summed, exactly as the endpoint's volume is.
        c.by_source[ip] = c.by_source.get(ip, 0) + g.request_count

    return _fold(batch, ClientCounts, add)
