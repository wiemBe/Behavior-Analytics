"""Phase B.2 breakdowns: the pure fold, the mock provider, and the REST provider's AQL.

The fold tests pin the contract stated in `app.waf.breakdown`'s docstring. The REST
tests drive the real provider through a mocked httpx transport and assert on the
query text it actually sends, because that text is the only place scoping, bounding
and the property allowlist can be checked: Ariel itself is never contacted.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.providers.dto import WafBreakdownBatch, WafBreakdownGroup
from app.providers.mock import MockQRadarProvider
from app.waf.breakdown import (
    TOP_CLIENTS_STORED,
    fold_actions,
    fold_response_codes,
    fold_source_ips,
)
from app.waf.profile import WafFieldMapping
from tests.unit.test_qradar_rest_provider import build_provider

T0 = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)
T1 = T0 + timedelta(seconds=300)
LOGIN = (None, "/portal/login", "POST")
USERS = (None, "/users/{id}", "GET")

MAPPING = WafFieldMapping(
    url="URL",
    method="HTTP_Method",
    action="WAF_Action",
    blocked_actions=("DENY", "Block"),
    allowed_actions=("ALLOW",),
)


def group(
    value: str | None, count: int, url: str = "/portal/login", method: str = "POST", host=None
) -> WafBreakdownGroup:
    return WafBreakdownGroup(
        raw_url=url, raw_method=method, raw_virtual_host=host, raw_value=value, request_count=count
    )


def batch(
    kind: str, groups: list[WafBreakdownGroup], *, truncated: bool = False
) -> WafBreakdownBatch:
    return WafBreakdownBatch(
        breakdown=kind,
        qradar_log_source_id=312,
        window_start=T0,
        window_end=T1,
        groups=groups,
        truncated=truncated,
    )


# ====================================================================== folding
class TestFoldSumsRawGroups:
    def test_raw_groups_normalizing_to_one_endpoint_are_summed(self) -> None:
        folded, unusable = fold_actions(
            batch(
                "ACTION",
                [
                    group("DENY", 4, url="/users/1", method="get"),
                    group("DENY", 6, url="/users/2?x=1", method="GET"),
                    group("ALLOW", 10, url="/users/3", method="GET"),
                ],
            ),
            MAPPING.action_outcome,
        )
        assert unusable == 0
        assert set(folded) == {USERS}
        c = folded[USERS]
        assert (c.events, c.blocked, c.unmapped) == (20, 10, 0)

    def test_groups_without_a_usable_endpoint_method_or_value_are_counted_not_folded(
        self,
    ) -> None:
        folded, unusable = fold_response_codes(
            batch(
                "RESPONSE_CODE",
                [
                    group("200", 5),
                    group("200", 7, url=None),
                    group("200", 7, method="NOT A METHOD"),
                    group(None, 7),
                ],
            )
        )
        assert unusable == 3
        assert folded[LOGIN].events == 5

    def test_methods_and_hosts_keep_endpoints_apart(self) -> None:
        folded, _ = fold_response_codes(
            batch(
                "RESPONSE_CODE",
                [
                    group("404", 3, method="GET"),
                    group("404", 4, method="POST"),
                    group("404", 5, method="POST", host="App.Example.com:443"),
                ],
            )
        )
        assert {k: v.events for k, v in folded.items()} == {
            (None, "/portal/login", "GET"): 3,
            (None, "/portal/login", "POST"): 4,
            ("app.example.com", "/portal/login", "POST"): 5,
        }


class TestFoldActions:
    def test_an_unmapped_value_is_unmapped_and_never_allowed(self) -> None:
        folded, _ = fold_actions(
            batch(
                "ACTION",
                [group("DENY", 5), group("ALLOW", 20), group("CHALLENGE", 7)],
            ),
            MAPPING.action_outcome,
        )
        c = folded[LOGIN]
        assert c.events == 32
        # CHALLENGE may have been a block: it is neither blocked nor allowed, so
        # it is reported on its own and the blocked count stays a lower bound.
        assert (c.blocked, c.unmapped) == (5, 7)
        assert c.blocked + c.unmapped <= c.events
        # An allowed request is neither blocked nor unmapped.
        assert c.events - c.blocked - c.unmapped == 20

    def test_matching_is_case_and_whitespace_insensitive(self) -> None:
        folded, _ = fold_actions(
            batch("ACTION", [group(" deny ", 3), group("BLOCK", 4), group("allow", 5)]),
            MAPPING.action_outcome,
        )
        c = folded[LOGIN]
        assert (c.events, c.blocked, c.unmapped) == (12, 7, 0)

    def test_an_empty_map_leaves_every_value_unmapped(self) -> None:
        bare = WafFieldMapping(url="URL", method="HTTP_Method", action="WAF_Action")
        folded, _ = fold_actions(
            batch("ACTION", [group("DENY", 5), group("ALLOW", 5)]), bare.action_outcome
        )
        c = folded[LOGIN]
        assert (c.events, c.blocked, c.unmapped) == (10, 0, 10)


class TestFoldResponseCodes:
    def test_client_and_server_errors_are_counted_separately(self) -> None:
        folded, _ = fold_response_codes(
            batch(
                "RESPONSE_CODE",
                [
                    group("200", 50),
                    group("301", 5),
                    group("400", 2),
                    group("404", 3),
                    group("499", 1),
                    group("500", 4),
                    group("503", 6),
                    group("599", 1),
                ],
            )
        )
        c = folded[LOGIN]
        assert c.events == 72
        assert (c.client_errors, c.server_errors, c.unclassified) == (6, 11, 0)

    def test_a_403_is_a_client_error_never_a_waf_block(self) -> None:
        folded, _ = fold_response_codes(batch("RESPONSE_CODE", [group("403", 9)]))
        c = folded[LOGIN]
        assert (c.events, c.client_errors, c.server_errors) == (9, 9, 0)
        # The response-code fold has no notion of a block at all.
        assert not hasattr(c, "blocked")

    @pytest.mark.parametrize(
        "value", ["", "  ", "abc", "40", "4040", "99", "600", "200.0", "-404", "4o4", "0"]
    )
    def test_a_value_that_is_not_a_three_digit_status_is_unclassified(self, value: str) -> None:
        folded, _ = fold_response_codes(batch("RESPONSE_CODE", [group(value, 8)]))
        c = folded[LOGIN]
        assert (c.events, c.unclassified) == (8, 8)
        assert (c.client_errors, c.server_errors) == (0, 0)

    def test_surrounding_whitespace_is_tolerated(self) -> None:
        folded, _ = fold_response_codes(batch("RESPONSE_CODE", [group(" 404 ", 2)]))
        assert folded[LOGIN].client_errors == 2

    def test_a_response_code_that_int_rejects_is_unclassified_not_an_error(self) -> None:
        folded, _ = fold_response_codes(
            batch(
                "RESPONSE_CODE",
                [group("\u00b2", 7), group("404", 3), group("\u0664\u0660\u0664", 2)],
            )
        )
        c = folded[LOGIN]
        assert (c.events, c.unclassified, c.client_errors) == (12, 9, 3)


class TestFoldSourceIps:
    def clients(self, counts: dict[str, int]) -> list[WafBreakdownGroup]:
        return [group(ip, n) for ip, n in counts.items()]

    def test_top_one_top_five_and_distinct_clients(self) -> None:
        folded, _ = fold_source_ips(
            batch(
                "SOURCE_IP",
                self.clients({
                    "192.0.2.1": 100, "192.0.2.2": 50, "192.0.2.3": 40, "192.0.2.4": 30,
                    "192.0.2.5": 20, "192.0.2.6": 10, "192.0.2.7": 1,
                }),
            )
        )
        c = folded[LOGIN]
        assert c.events == 251
        assert (c.top1, c.top5, c.sources) == (100, 240, 7)

    def test_one_address_through_several_raw_urls_is_one_contributor(self) -> None:
        folded, _ = fold_source_ips(
            batch(
                "SOURCE_IP",
                [
                    group("192.0.2.1", 30, url="/users/1", method="GET"),
                    group("192.0.2.1", 25, url="/users/2", method="GET"),
                    group("192.0.2.9", 5, url="/users/3", method="GET"),
                ],
            )
        )
        c = folded[USERS]
        assert (c.events, c.top1, c.sources) == (60, 55, 2)

    def test_addresses_are_normalized_before_they_are_compared(self) -> None:
        folded, _ = fold_source_ips(
            batch(
                "SOURCE_IP",
                [
                    group("2001:DB8:0:0:0:0:0:1", 10),
                    group("2001:db8::1", 15),
                    group(" 192.0.2.1 ", 5),
                    group("192.0.2.1", 5),
                ],
            )
        )
        c = folded[LOGIN]
        assert c.by_source == {"2001:db8::1": 25, "192.0.2.1": 10}

    def test_a_value_that_is_not_an_address_is_counted_as_unattributed(self) -> None:
        folded, _ = fold_source_ips(
            batch("SOURCE_IP", [group("not-an-ip", 4), group("192.0.2.1", 6)])
        )
        c = folded[LOGIN]
        assert c.events == 6
        assert c.by_source == {"192.0.2.1": 6}
        assert c.unattributed == 4

    def test_garbage_values_never_reach_a_ranking_even_as_the_largest_group(self) -> None:
        folded, _ = fold_source_ips(
            batch(
                "SOURCE_IP",
                [group("garbage-a", 500), group("garbage-b", 300), group("192.0.2.1", 25),
                 group("192.0.2.2", 20)],
            )
        )
        c = folded[LOGIN]
        assert (c.events, c.unattributed) == (45, 800)
        assert (c.top1, c.top5, c.sources) == (25, 45, 2)
        assert c.contributors(min_top_requests=20) == [
            {"source_ip": "192.0.2.1", "requests": 25},
            {"source_ip": "192.0.2.2", "requests": 20},
        ]

    def test_only_garbage_values_leave_no_client_and_no_contributors(self) -> None:
        folded, _ = fold_source_ips(batch("SOURCE_IP", [group("x", 900), group("", 100)]))
        c = folded[LOGIN]
        assert (c.events, c.unattributed, c.top1, c.top5, c.sources) == (0, 1000, 0, 0, 0)
        assert c.contributors(min_top_requests=1) is None

    def test_the_ranking_is_total_ties_break_by_address(self) -> None:
        folded, _ = fold_source_ips(
            batch("SOURCE_IP", self.clients({"192.0.2.9": 5, "192.0.2.10": 5, "192.0.2.2": 5}))
        )
        assert [ip for ip, _n in folded[LOGIN].ranked] == [
            "192.0.2.10", "192.0.2.2", "192.0.2.9",
        ]

    def test_contributors_are_bounded_to_the_five_largest(self) -> None:
        counts = {f"192.0.2.{i}": 100 - i for i in range(1, 9)}
        folded, _ = fold_source_ips(batch("SOURCE_IP", self.clients(counts)))
        stored = folded[LOGIN].contributors(min_top_requests=20)
        assert stored is not None and len(stored) == TOP_CLIENTS_STORED == 5
        assert stored[0] == {"source_ip": "192.0.2.1", "requests": 99}
        assert [s["source_ip"] for s in stored] == [f"192.0.2.{i}" for i in range(1, 6)]

    def test_raw_addresses_are_dropped_when_the_largest_client_is_immaterial(self) -> None:
        folded, _ = fold_source_ips(
            batch("SOURCE_IP", self.clients({"192.0.2.1": 19, "192.0.2.2": 3}))
        )
        c = folded[LOGIN]
        assert c.contributors(min_top_requests=20) is None
        # The aggregates survive; only the addresses do not.
        assert (c.top1, c.top5, c.sources) == (19, 22, 2)
        # The floor is inclusive.
        assert c.contributors(min_top_requests=19) is not None

    def test_materiality_is_judged_per_endpoint(self) -> None:
        folded, _ = fold_source_ips(
            batch(
                "SOURCE_IP",
                [
                    group("192.0.2.1", 50, url="/big", method="GET"),
                    group("192.0.2.1", 5, url="/small", method="GET"),
                ],
            )
        )
        assert folded[(None, "/big", "GET")].contributors(min_top_requests=20) is not None
        assert folded[(None, "/small", "GET")].contributors(min_top_requests=20) is None


class TestTruncatedSearchYieldsOnlyTheVisiblePart:
    """The fold has no truncation flag of its own: it counts what it is given.

    `WafBreakdownBatch.truncated` is the marker. The fold must neither extrapolate
    from it nor drop the groups it did receive, and must not clear it.
    """

    def test_values_cover_exactly_the_visible_groups(self) -> None:
        truncated = batch(
            "ACTION", [group("DENY", 5), group("CHALLENGE", 2)], truncated=True
        )
        folded, _ = fold_actions(truncated, MAPPING.action_outcome)
        c = folded[LOGIN]
        assert (c.events, c.blocked, c.unmapped) == (7, 5, 2)
        assert truncated.truncated is True
        assert len(truncated.groups) == 2

    def test_a_truncated_and_a_whole_batch_fold_identically_group_for_group(self) -> None:
        groups = [group("192.0.2.1", 30), group("192.0.2.2", 3)]
        whole, _ = fold_source_ips(batch("SOURCE_IP", groups))
        cut, _ = fold_source_ips(batch("SOURCE_IP", groups, truncated=True))
        assert whole[LOGIN].by_source == cut[LOGIN].by_source

    def test_an_empty_batch_folds_to_nothing_not_to_zeros(self) -> None:
        folded, unusable = fold_response_codes(batch("RESPONSE_CODE", [], truncated=True))
        assert folded == {} and unusable == 0


# ================================================================ mock provider
class TestMockProviderBreakdown:
    async def call(self, breakdown: str, **kw):  # type: ignore[no-untyped-def]
        return await MockQRadarProvider(seed=1337).get_waf_breakdown(
            qradar_log_source_id=312,
            window_start=T0,
            window_end=T1,
            url_property="URL",
            method_property="HTTP_Method",
            virtual_host_property=None,
            breakdown=breakdown,
            breakdown_property=None if breakdown == "SOURCE_IP" else "WAF_Action",
            **kw,
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("breakdown", ["ACTION", "RESPONSE_CODE", "SOURCE_IP"])
    async def test_two_calls_return_identical_batches(self, breakdown: str) -> None:
        first = await self.call(breakdown)
        second = await self.call(breakdown)
        assert first.groups
        assert first.groups == second.groups
        assert first.truncated == second.truncated is False
        assert first.breakdown == breakdown
        assert first.query == f"MOCK WAF BREAKDOWN {breakdown}"

    @pytest.mark.asyncio
    async def test_groups_are_ordered_by_volume_and_scale_the_endpoint_volume(self) -> None:
        batch_ = await self.call("RESPONSE_CODE")
        counts = [g.request_count for g in batch_.groups]
        assert counts == sorted(counts, reverse=True)
        assert {g.raw_value for g in batch_.groups} <= {"200", "404", "500"}
        assert all(g.request_count > 0 for g in batch_.groups)

    @pytest.mark.asyncio
    async def test_each_value_is_its_fixed_share_of_the_endpoints_volume(self) -> None:
        provider = MockQRadarProvider(seed=1337)
        volume = await provider.get_waf_observations(
            qradar_log_source_id=312, window_start=T0, window_end=T1,
            url_property="URL", method_property="HTTP_Method", virtual_host_property=None,
            max_groups=5000,
        )
        by_endpoint = {(g.raw_url, g.raw_method): g.request_count for g in volume.groups}
        shares = {"ALLOW": 0.9, "DENY": 0.1}
        batch_ = await self.call("ACTION")
        expected = {
            (url, method, value): int(count * share)
            for (url, method), count in by_endpoint.items()
            for value, share in shares.items()
            if int(count * share) > 0
        }
        assert {
            (g.raw_url, g.raw_method, g.raw_value): g.request_count for g in batch_.groups
        } == expected

    @pytest.mark.asyncio
    async def test_the_group_cap_bounds_the_result_and_reports_truncation(self) -> None:
        whole = await self.call("ACTION")
        assert len(whole.groups) > 2
        cut = await self.call("ACTION", max_groups=2)
        assert len(cut.groups) == 2
        assert cut.truncated is True
        assert cut.groups == whole.groups[:2]

    @pytest.mark.asyncio
    async def test_the_batch_is_scoped_to_the_requested_source_and_window(self) -> None:
        batch_ = await self.call("SOURCE_IP")
        assert batch_.qradar_log_source_id == 312
        assert (batch_.window_start, batch_.window_end) == (T0, T1)


# ============================================================= REST provider AQL
class BreakdownAriel:
    """Answers every Ariel search with fixed rows and records the query text."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows if rows is not None else []
        self.queries: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            self.queries.append(request.url.params["query_expression"])
            return httpx.Response(201, json={"search_id": "bd-1", "status": "WAIT"})
        if path.endswith("/results"):
            return httpx.Response(200, json={"events": self.rows})
        if "/ariel/searches/bd-1" in path:
            return httpx.Response(200, json={"status": "COMPLETED", "progress": 100})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


def rest_rows(n: int) -> list[dict]:
    return [
        {"raw_url": f"/p/{i}", "raw_method": "GET", "raw_value": "200", "request_count": 100.0 - i}
        for i in range(n)
    ]


async def rest_breakdown(  # type: ignore[no-untyped-def]
    ariel: BreakdownAriel, breakdown: str, *, provider_kw=None, **kw
):
    provider, _ = build_provider(ariel.handler, **(provider_kw or {"page_size": 100}))
    args = {
        "qradar_log_source_id": 312,
        "window_start": T0,
        "window_end": T1,
        "url_property": "URL",
        "method_property": "HTTP_Method",
        "virtual_host_property": None,
        "breakdown": breakdown,
        "breakdown_property": None if breakdown == "SOURCE_IP" else "WAF_Prop",
    }
    args.update(kw)
    return await provider.get_waf_breakdown(**args)


ALL_BREAKDOWNS = ["ACTION", "RESPONSE_CODE", "SOURCE_IP"]


class TestRestBreakdownQuery:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("breakdown", ALL_BREAKDOWNS)
    async def test_every_breakdown_is_scoped_by_start_and_stop(self, breakdown: str) -> None:
        """Commit dc9aa34: an unscoped Ariel search silently returns nothing for a
        historical bucket, which the collector would read as a measured zero."""
        ariel = BreakdownAriel(rest_rows(1))
        await rest_breakdown(ariel, breakdown)

        (aql,) = ariel.queries
        start_ms, end_ms = int(T0.timestamp() * 1000), int(T1.timestamp() * 1000)
        assert aql.endswith(f"START {start_ms} STOP {end_ms}")
        assert f"starttime >= {start_ms} AND starttime < {end_ms}" in aql
        assert "logsourceid = 312" in aql

    @pytest.mark.asyncio
    @pytest.mark.parametrize("breakdown", ALL_BREAKDOWNS)
    async def test_volume_is_summed_events_over_a_non_null_population(
        self, breakdown: str
    ) -> None:
        ariel = BreakdownAriel(rest_rows(1))
        await rest_breakdown(ariel, breakdown)

        (aql,) = ariel.queries
        assert 'SUM(eventcount) AS "request_count"' in aql
        assert "COUNT(*)" not in aql
        assert "ORDER BY SUM(eventcount) DESC" in aql
        assert " IS NOT NULL" in aql

    @pytest.mark.asyncio
    async def test_action_and_response_code_group_by_the_quoted_profile_property(self) -> None:
        for breakdown, prop in (("ACTION", "WAF_Action"), ("RESPONSE_CODE", "WAF Resp-Code")):
            ariel = BreakdownAriel(rest_rows(1))
            await rest_breakdown(ariel, breakdown, breakdown_property=prop)
            (aql,) = ariel.queries
            assert f'"{prop}" AS "raw_value"' in aql
            assert f'"{prop}" IS NOT NULL' in aql
            assert aql.count(f'"{prop}"') >= 3  # select, filter, group by
            assert 'GROUP BY "URL", "HTTP_Method", ' + f'"{prop}"' in aql

    @pytest.mark.asyncio
    async def test_source_ip_uses_the_normalized_field_and_ignores_any_configured_property(
        self,
    ) -> None:
        ariel = BreakdownAriel(rest_rows(1))
        # Even a hostile value in `breakdown_property` must not reach the query.
        await rest_breakdown(
            ariel, "SOURCE_IP", breakdown_property='x" OR 1=1 --'
        )
        (aql,) = ariel.queries
        assert 'sourceip AS "raw_value"' in aql
        assert "sourceip IS NOT NULL" in aql
        assert "OR 1=1" not in aql and "x\"" not in aql

    @pytest.mark.asyncio
    async def test_a_mapped_virtual_host_is_selected_and_grouped(self) -> None:
        ariel = BreakdownAriel(rest_rows(1))
        await rest_breakdown(ariel, "ACTION", virtual_host_property="WAF Host")
        (aql,) = ariel.queries
        assert '"WAF Host" AS "raw_virtual_host"' in aql
        assert 'GROUP BY "URL", "HTTP_Method", "WAF Host", "WAF_Prop"' in aql

    @pytest.mark.asyncio
    async def test_the_aql_is_read_only_single_statement(self) -> None:
        ariel = BreakdownAriel(rest_rows(1))
        await rest_breakdown(ariel, "ACTION")
        (aql,) = ariel.queries
        assert aql.startswith("SELECT ")
        assert ";" not in aql
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|CREATE)\b", aql, re.IGNORECASE)


class TestRestBreakdownBound:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("breakdown", ALL_BREAKDOWNS)
    async def test_the_group_cap_becomes_the_limit_clause(self, breakdown: str) -> None:
        ariel = BreakdownAriel(rest_rows(1))
        await rest_breakdown(ariel, breakdown, max_groups=37)
        (aql,) = ariel.queries
        assert re.search(r"\bLIMIT 37 START ", aql), aql

    @pytest.mark.asyncio
    async def test_the_limit_never_exceeds_what_the_provider_can_page(self) -> None:
        ariel = BreakdownAriel(rest_rows(1))
        await rest_breakdown(
            ariel, "ACTION", max_groups=10_000, provider_kw={"page_size": 10, "max_pages": 3}
        )
        (aql,) = ariel.queries
        assert re.search(r"\bLIMIT 30 START ", aql), aql

    @pytest.mark.asyncio
    async def test_a_cap_of_zero_still_bounds_the_query(self) -> None:
        ariel = BreakdownAriel(rest_rows(1))
        await rest_breakdown(ariel, "ACTION", max_groups=0)
        (aql,) = ariel.queries
        assert re.search(r"\bLIMIT 1 START ", aql), aql

    @pytest.mark.asyncio
    async def test_a_window_wider_than_the_safety_bound_is_refused_before_any_search(self) -> None:
        ariel = BreakdownAriel()
        with pytest.raises(ValueError, match="168-hour"):
            await rest_breakdown(ariel, "ACTION", window_end=T0 + timedelta(days=30))
        assert ariel.queries == []


class TestRestBreakdownTruncation:
    @pytest.mark.asyncio
    async def test_reaching_the_cap_is_reported_as_truncation(self) -> None:
        ariel = BreakdownAriel(rest_rows(5))
        batch_ = await rest_breakdown(ariel, "RESPONSE_CODE", max_groups=5)
        assert len(batch_.groups) == 5
        assert batch_.truncated is True

    @pytest.mark.asyncio
    async def test_fewer_groups_than_the_cap_is_a_whole_result(self) -> None:
        ariel = BreakdownAriel(rest_rows(4))
        batch_ = await rest_breakdown(ariel, "RESPONSE_CODE", max_groups=5)
        assert len(batch_.groups) == 4
        assert batch_.truncated is False

    @pytest.mark.asyncio
    async def test_the_batch_records_its_query_and_search_count(self) -> None:
        ariel = BreakdownAriel(rest_rows(2))
        batch_ = await rest_breakdown(ariel, "SOURCE_IP", max_groups=5)
        assert batch_.query == ariel.queries[0]
        assert batch_.ariel_searches == 1
        assert batch_.breakdown == "SOURCE_IP"

    @pytest.mark.asyncio
    async def test_rows_are_decoded_and_malformed_counts_are_dropped(self) -> None:
        rows = [
            {"raw_url": "/a", "raw_method": "GET", "raw_value": "403", "request_count": 7.0},
            {"raw_url": "/b", "raw_method": "GET", "raw_value": "403", "request_count": None},
            {"raw_url": "/c", "raw_method": "GET", "raw_value": "403", "request_count": -3},
        ]
        batch_ = await rest_breakdown(BreakdownAriel(rows), "RESPONSE_CODE", max_groups=50)
        assert [(g.raw_url, g.raw_value, g.request_count) for g in batch_.groups] == [
            ("/a", "403", 7)
        ]


class TestRestBreakdownAllowlist:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad",
        ['A"B', "A;DROP", "A,B", "A(B)", "A'B", "a\nb", "A=B", "", "x" * 65, "A*"],
    )
    @pytest.mark.parametrize("slot", ["url_property", "method_property", "breakdown_property"])
    async def test_an_unsafe_property_name_is_refused_before_any_search(
        self, bad: str, slot: str
    ) -> None:
        ariel = BreakdownAriel(rest_rows(1))
        with pytest.raises(ValueError):
            await rest_breakdown(ariel, "ACTION", **{slot: bad})
        assert ariel.queries == []

    @pytest.mark.asyncio
    async def test_an_unsafe_virtual_host_property_is_refused(self) -> None:
        ariel = BreakdownAriel(rest_rows(1))
        with pytest.raises(ValueError):
            await rest_breakdown(ariel, "ACTION", virtual_host_property='h" --')
        assert ariel.queries == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("breakdown", ["BYTES", "action", "", "SOURCE_IP; DROP"])
    async def test_an_unknown_breakdown_is_refused_before_any_search(self, breakdown: str) -> None:
        ariel = BreakdownAriel(rest_rows(1))
        with pytest.raises(ValueError, match="unsupported WAF breakdown"):
            await rest_breakdown(ariel, breakdown, breakdown_property="WAF_Prop")
        assert ariel.queries == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("breakdown", ["ACTION", "RESPONSE_CODE"])
    async def test_a_property_breakdown_without_a_mapped_property_is_refused(
        self, breakdown: str
    ) -> None:
        ariel = BreakdownAriel(rest_rows(1))
        with pytest.raises(ValueError, match="unsupported WAF breakdown"):
            await rest_breakdown(ariel, breakdown, breakdown_property=None)
        assert ariel.queries == []

    @pytest.mark.asyncio
    async def test_every_identifier_in_the_aql_is_a_quoted_allowlisted_name_or_sourceip(
        self,
    ) -> None:
        ariel = BreakdownAriel(rest_rows(1))
        await rest_breakdown(
            ariel, "ACTION", virtual_host_property="WAF Host", breakdown_property="WAF-Action_2"
        )
        (aql,) = ariel.queries
        quoted = set(re.findall(r'"([^"]*)"', aql))
        assert quoted == {
            "URL", "HTTP_Method", "WAF Host", "WAF-Action_2",
            "raw_url", "raw_method", "raw_virtual_host", "raw_value", "request_count",
        }
