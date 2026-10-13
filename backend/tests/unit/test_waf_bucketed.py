"""Keyed WAF searches: the pure fold, the REST queries, and the mock.

The fold is the part that replaces `ORDER BY SUM(eventcount) DESC LIMIT n`, which
`LIMIT` cannot do per bucket in a keyed search (T-032). The acceptance bar is
that, for any window the search returned whole, each bucket equals what the
per-bucket query returns for it.
"""

from __future__ import annotations

import random
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.providers.base import ProviderError
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_rest import QRadarRestProvider
from app.waf.bucketed import (
    breakdown_sort_key,
    fold_breakdown_buckets,
    fold_observation_buckets,
    observation_sort_key,
)

START = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)
BUCKET = 300
BUCKET_MS = BUCKET * 1000
END = START + timedelta(seconds=3 * BUCKET)  # three buckets
LOG_SOURCE = 312


def key_of(index: int) -> int:
    return (int(START.timestamp()) + index * BUCKET) // BUCKET


def group_row(index: int, url: str, count: object, method: str = "GET", **extra: object) -> dict:
    return {
        "k": key_of(index),
        "raw_url": url,
        "raw_method": method,
        "request_count": count,
        "record_count": count,
        "distinct_clients": 2,
        **extra,
    }


def bd_row(index: int, url: str, value: str, count: object, method: str = "GET") -> dict:
    return {
        "k": key_of(index),
        "raw_url": url,
        "raw_method": method,
        "raw_value": value,
        "request_count": count,
    }


def fold_obs(rows: list[dict], totals: list[dict] | None = None, *, max_groups: int = 10):
    return fold_observation_buckets(
        rows,
        totals or [],
        qradar_log_source_id=LOG_SOURCE,
        window_start=START,
        window_end=END,
        bucket_seconds=BUCKET,
        max_groups=max_groups,
    )


def fold_bd(rows: list[dict], *, max_groups: int = 10):
    return fold_breakdown_buckets(
        rows,
        breakdown="ACTION",
        qradar_log_source_id=LOG_SOURCE,
        window_start=START,
        window_end=END,
        bucket_seconds=BUCKET,
        max_groups=max_groups,
    )


# ------------------------------------------------------------------- the fold
class TestObservationFold:
    def test_keys_map_to_buckets_and_every_bucket_has_a_batch(self) -> None:
        rows = [group_row(0, "/a", 5), group_row(2, "/b", 7), group_row(2, "/c", 9)]
        totals = [{"k": key_of(0), "total_events": 6}, {"k": key_of(2), "total_events": 20}]

        out = fold_obs(rows, totals)

        starts = [START + timedelta(seconds=BUCKET * i) for i in range(3)]
        assert list(out) == starts
        assert [g.raw_url for g in out[starts[0]].groups] == ["/a"]
        assert out[starts[1]].groups == [] and out[starts[1]].total_events == 0
        assert [g.raw_url for g in out[starts[2]].groups] == ["/c", "/b"]
        assert [b.total_events for b in out.values()] == [6, 0, 20]
        assert out[starts[2]].window_start == starts[2]
        assert out[starts[2]].window_end == starts[2] + timedelta(seconds=BUCKET)

    @pytest.mark.parametrize("bad", [-1, 3, 10**9])
    def test_a_key_outside_the_window_fails_the_whole_search(self, bad: int) -> None:
        row = group_row(0, "/a", 5)
        row["k"] = key_of(0) + bad
        with pytest.raises(ProviderError, match="outside the window"):
            fold_obs([group_row(1, "/ok", 5), row])

    @pytest.mark.parametrize("bad", [None, "abc", 1.5, True, {}])
    def test_a_non_integer_key_fails_the_whole_search(self, bad: object) -> None:
        row = group_row(0, "/a", 5)
        row["k"] = bad
        with pytest.raises(ProviderError, match="non-integer bucket key"):
            fold_obs([row])

    def test_an_outside_key_in_the_totals_also_fails_the_search(self) -> None:
        with pytest.raises(ProviderError, match="outside the window"):
            fold_obs([], [{"k": key_of(3), "total_events": 4}])

    def test_two_totals_for_one_bucket_fail_the_search(self) -> None:
        totals = [{"k": key_of(0), "total_events": 4}, {"k": key_of(0), "total_events": 5}]
        with pytest.raises(ProviderError, match="two rows"):
            fold_obs([], totals)

    def test_a_bucket_keeps_its_top_groups_by_request_count(self) -> None:
        rows = [group_row(1, f"/p{i}", 10 + i) for i in range(6)]
        out = fold_obs(rows, max_groups=4)
        kept = out[START + timedelta(seconds=BUCKET)].groups
        assert [g.request_count for g in kept] == [15, 14, 13, 12]

    def test_the_cut_is_per_bucket_not_over_the_whole_search(self) -> None:
        rows = [group_row(0, f"/big{i}", 1000 + i) for i in range(4)]
        rows += [group_row(1, "/small", 1)]
        out = fold_obs(rows, max_groups=4)
        # The small bucket loses nothing to its busy neighbour.
        assert [g.raw_url for g in out[START + timedelta(seconds=BUCKET)].groups] == ["/small"]

    def test_ties_break_on_the_raw_key_whatever_the_row_order(self) -> None:
        rows = [
            group_row(0, "/b", 10),
            group_row(0, "/a", 10, method="POST"),
            group_row(0, "/a", 10, method="GET"),
            group_row(0, "/c", 10),
        ]
        expected = [("/a", "GET"), ("/a", "POST"), ("/b", "GET")]
        for seed in range(5):
            shuffled = rows[:]
            random.Random(seed).shuffle(shuffled)
            kept = fold_obs(shuffled, max_groups=3)[START].groups
            assert [(g.raw_url, g.raw_method) for g in kept] == expected

    def test_a_missing_part_of_the_key_sorts_as_empty(self) -> None:
        rows = [group_row(0, "/a", 5), group_row(0, None, 5)]  # type: ignore[arg-type]
        kept = fold_obs(rows)[START].groups
        assert [g.raw_url for g in kept] == [None, "/a"]

    def test_truncated_exactly_at_max_groups(self) -> None:
        rows = [group_row(0, f"/p{i}", 10 + i) for i in range(4)]
        assert fold_obs(rows, max_groups=4)[START].truncated is True
        assert fold_obs(rows, max_groups=5)[START].truncated is False

    def test_truncated_at_max_groups_minus_one_is_not(self) -> None:
        rows = [group_row(0, f"/p{i}", 10 + i) for i in range(3)]
        assert fold_obs(rows, max_groups=4)[START].truncated is False

    def test_truncation_is_judged_per_bucket(self) -> None:
        rows = [group_row(0, f"/p{i}", 10 + i) for i in range(4)] + [group_row(1, "/x", 3)]
        out = fold_obs(rows, max_groups=4)
        assert [b.truncated for b in out.values()] == [True, False, False]

    def test_rows_without_a_usable_count_are_dropped_and_cut_first(self) -> None:
        bad = [group_row(0, "/n", None), group_row(0, "/neg", -4), group_row(0, "/s", "x")]
        # Two good rows and a bad one under a cap of 3: the bad row is cut, not kept.
        out = fold_obs([group_row(0, "/a", 5), group_row(0, "/b", 3), *bad], max_groups=3)
        assert [g.raw_url for g in out[START].groups] == ["/a", "/b"]
        # As today: the cap counts rows the query returned, so a cut that spent
        # a slot on an unusable row leaves fewer than `max_groups` usable ones.
        assert out[START].truncated is False

    def test_the_result_carries_the_search_text_and_no_group_values(self) -> None:
        rows = [group_row(0, "/secret/path", 5)]
        out = fold_observation_buckets(
            rows, [], qradar_log_source_id=LOG_SOURCE, window_start=START, window_end=END,
            bucket_seconds=BUCKET, max_groups=10, query="AQL", total_query="TOTAL AQL",
        )
        batch = out[START]
        assert (batch.query, batch.total_query, batch.ariel_searches) == ("AQL", "TOTAL AQL", 0)
        # Raw values exist only on the group objects of the bucket they belong to.
        empty = out[START + timedelta(seconds=BUCKET)]
        assert "/secret/path" not in empty.model_dump_json()

    def test_a_misaligned_window_is_rejected_before_any_fold(self) -> None:
        with pytest.raises(ValueError, match="epoch-aligned whole buckets"):
            fold_observation_buckets(
                [], [], qradar_log_source_id=1, window_start=START + timedelta(seconds=1),
                window_end=END, bucket_seconds=BUCKET, max_groups=5,
            )

    def test_sort_key_is_total_over_the_raw_key(self) -> None:
        rows = [group_row(0, "/a", 5, method="GET")]
        group = fold_obs(rows)[START].groups[0]
        assert observation_sort_key(group) == (-5, "/a", "GET", "")


class TestBreakdownFold:
    def test_keys_map_to_buckets(self) -> None:
        rows = [bd_row(0, "/a", "DENY", 3), bd_row(2, "/a", "ALLOW", 9)]
        out = fold_bd(rows)
        starts = list(out)
        assert [len(out[s].groups) for s in starts] == [1, 0, 1]
        assert out[starts[0]].groups[0].raw_value == "DENY"
        assert out[starts[2]].breakdown == "ACTION"

    def test_a_bad_key_fails_the_search(self) -> None:
        outside = bd_row(0, "/a", "DENY", 3)
        outside["k"] = key_of(7)
        with pytest.raises(ProviderError, match="outside the window"):
            fold_bd([outside])
        broken = bd_row(0, "/a", "DENY", 3)
        broken["k"] = "x"
        with pytest.raises(ProviderError, match="non-integer"):
            fold_bd([broken])

    def test_ties_break_on_endpoint_then_value(self) -> None:
        rows = [
            bd_row(0, "/b", "ALLOW", 7),
            bd_row(0, "/a", "DENY", 7),
            bd_row(0, "/a", "ALLOW", 7),
        ]
        kept = fold_bd(rows, max_groups=2)[START].groups
        assert [(g.raw_url, g.raw_value) for g in kept] == [("/a", "ALLOW"), ("/a", "DENY")]
        assert breakdown_sort_key(kept[0]) == (-7, "/a", "GET", "", "ALLOW")

    def test_truncated_exactly_at_max_groups_and_not_one_below(self) -> None:
        rows = [bd_row(0, f"/p{i}", "DENY", 10 + i) for i in range(4)]
        assert fold_bd(rows, max_groups=4)[START].truncated is True
        assert fold_bd(rows, max_groups=5)[START].truncated is False

    def test_the_cut_counts_unusable_rows_as_the_per_bucket_query_does(self) -> None:
        rows = [bd_row(0, "/a", "DENY", 5), bd_row(0, "/b", "DENY", None)]
        out = fold_bd(rows, max_groups=2)[START]
        assert [g.raw_url for g in out.groups] == ["/a"]
        # Two rows came back for a two-row page: the query could not tell us
        # more, so the bucket is a lower bound.
        assert out.truncated is True


# ---------------------------------------------------------------- the REST path
Record = dict


class WafAriel:
    """An Ariel that evaluates today's per-bucket WAF queries and the keyed ones.

    It honours `GROUP BY`, `ORDER BY SUM(eventcount) DESC`, `LIMIT` and the
    `START`/`STOP` bounds it is given, so a provider that asked for the wrong
    thing gets the wrong numbers back.
    """

    def __init__(self, records: list[Record]) -> None:
        self.records = records
        self.queries: list[str] = []

    def _within(self, query: str) -> list[Record]:
        start, stop = (int(x) for x in re.search(r"START (\d+) STOP (\d+)", query).groups())
        return [r for r in self.records if start <= r["ts"] < stop]

    @staticmethod
    def _limit(query: str, rows: list[dict]) -> list[dict]:
        return rows[: int(re.search(r"LIMIT (\d+)", query).group(1))]

    def results(self, query: str) -> list[dict]:
        rows = self._within(query)
        keyed = '"k"' in query
        bucket = (lambda r: r["ts"] // BUCKET_MS) if keyed else (lambda r: 0)
        if '"total_events"' in query:
            sums: dict[int, int] = {}
            for r in rows:
                sums[bucket(r)] = sums.get(bucket(r), 0) + r["eventcount"]
            out = [{"total_events": v, **({"k": k} if keyed else {})} for k, v in sums.items()]
            return self._limit(query, out)
        by_value = '"raw_value"' in query
        groups: dict[tuple, int] = {}
        for r in rows:
            value = r["sourceip"] if by_value else None
            groups.setdefault((bucket(r), r["url"], r["method"], value), 0)
            groups[(bucket(r), r["url"], r["method"], value)] += r["eventcount"]
        ordered = sorted(groups.items(), key=lambda kv: -kv[1])
        out = []
        for (k, url, method, value), total in ordered:
            row = {"raw_url": url, "raw_method": method, "request_count": total}
            if by_value:
                row["raw_value"] = value
            else:
                row.update(record_count=total, distinct_clients=1)
            if keyed:
                row["k"] = k
            out.append(row)
        return self._limit(query, out)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            self.queries.append(request.url.params["query_expression"])
            return httpx.Response(
                201, json={"search_id": f"s-{len(self.queries)}", "status": "WAIT"}
            )
        if path.endswith("/results"):
            index = int(path.split("/")[-2].removeprefix("s-")) - 1
            return httpx.Response(200, json={"events": self.results(self.queries[index])})
        if "/ariel/searches/s-" in path:
            return httpx.Response(200, json={"status": "COMPLETED", "progress": 100})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


def provider_for(ariel: WafAriel) -> QRadarRestProvider:
    async def no_sleep(_seconds: float) -> None:
        return None

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(ariel.handler),
        base_url="https://qradar.example.internal/api",
        headers={"SEC": "t", "Version": "20.0", "Accept": "application/json"},
    )
    return QRadarRestProvider(
        base_url="https://qradar.example.internal",
        sec_token="t",
        client=client,
        rng=random.Random(1),
        sleep=no_sleep,
        page_size=50,
        max_pages=4,
        ariel_stop_pad_seconds=0,
    )


def record(index: int, url: str, count: int, *, ip: str = "198.51.100.1", method: str = "GET"):
    at = int(START.timestamp() * 1000) + index * BUCKET_MS + 1000
    return {"ts": at, "url": url, "method": method, "eventcount": count, "sourceip": ip}


def traffic() -> list[Record]:
    """Distinct counts, so ordering never depends on a tie."""
    out: list[Record] = []
    for index in range(3):
        for j in range(7):
            out.append(record(index, f"/p{j}", 100 + 10 * j + index, ip=f"198.51.100.{j}"))
    return out


WAF_ARGS = {
    "qradar_log_source_id": LOG_SOURCE,
    "url_property": "URL",
    "method_property": "HTTP_Method",
}


class TestRestObservations:
    @pytest.mark.asyncio
    async def test_the_queries_carry_key_group_by_limit_and_both_bounds(self) -> None:
        ariel = WafAriel(traffic())
        provider = provider_for(ariel)

        batch = await provider.get_waf_observations_bucketed(
            window_start=START, window_end=END, bucket_seconds=BUCKET,
            max_groups=5, max_rows=50, **WAF_ARGS,
        )

        grouped, totals = ariel.queries
        start_ms, end_ms = int(START.timestamp() * 1000), int(END.timestamp() * 1000)
        for query in (grouped, totals):
            assert f'starttime / {BUCKET_MS} AS "k"' in query
            assert "LIMIT 51" in query
            assert f"WHERE logsourceid = {LOG_SOURCE} AND starttime >= {start_ms}" in query
            assert f"starttime < {end_ms}" in query
            assert query.endswith(f"START {start_ms} STOP {end_ms}")
        assert f'GROUP BY "URL", "HTTP_Method", starttime / {BUCKET_MS}' in grouped
        assert f"GROUP BY starttime / {BUCKET_MS} LIMIT" in totals
        assert "LIMIT 1 " not in totals
        assert batch.ariel_searches == 2 and batch.truncated is False
        assert batch.query == grouped and batch.total_query == totals

    @pytest.mark.asyncio
    async def test_an_n_plus_one_row_result_is_truncated_and_keeps_no_bucket(self) -> None:
        ariel = WafAriel(traffic())  # 21 grouped rows
        provider = provider_for(ariel)

        batch = await provider.get_waf_observations_bucketed(
            window_start=START, window_end=END, bucket_seconds=BUCKET,
            max_groups=5, max_rows=20, **WAF_ARGS,
        )

        assert batch.truncated is True and batch.buckets == {}
        # The totals search is not sent for a result that is already discarded.
        assert len(ariel.queries) == 1 and batch.ariel_searches == 1
        assert "LIMIT 21" in ariel.queries[0]

    @pytest.mark.asyncio
    async def test_exactly_max_rows_is_whole(self) -> None:
        provider = provider_for(WafAriel(traffic()))
        batch = await provider.get_waf_observations_bucketed(
            window_start=START, window_end=END, bucket_seconds=BUCKET,
            max_groups=50, max_rows=21, **WAF_ARGS,
        )
        assert batch.truncated is False and len(batch.buckets) == 3

    @pytest.mark.asyncio
    async def test_a_budget_below_the_bucket_count_is_truncated(self) -> None:
        # One grouped row and one total per bucket: three buckets cannot fit two rows.
        rows = [record(0, "/a", 5), record(1, "/a", 5), record(2, "/a", 5)]
        provider = provider_for(WafAriel(rows))
        batch = await provider.get_waf_observations_bucketed(
            window_start=START, window_end=END, bucket_seconds=BUCKET,
            max_groups=50, max_rows=2, **WAF_ARGS,
        )
        assert batch.truncated is True and batch.buckets == {}

    @pytest.mark.asyncio
    async def test_each_bucket_equals_the_per_bucket_query(self) -> None:
        ariel = WafAriel(traffic())
        provider = provider_for(ariel)
        keyed = await provider.get_waf_observations_bucketed(
            window_start=START, window_end=END, bucket_seconds=BUCKET,
            max_groups=5, max_rows=100, **WAF_ARGS,
        )
        assert keyed.truncated is False
        for start, bucket in keyed.buckets.items():
            single = await provider.get_waf_observations(
                window_start=start, window_end=start + timedelta(seconds=BUCKET),
                max_groups=5, **WAF_ARGS,
            )
            assert bucket.groups == single.groups
            assert bucket.total_events == single.total_events
            assert bucket.truncated == single.truncated is True
            assert (bucket.window_start, bucket.window_end) == (single.window_start,
                                                                 single.window_end)

    @pytest.mark.asyncio
    async def test_a_bucket_below_the_cut_is_not_truncated_as_today(self) -> None:
        provider = provider_for(WafAriel(traffic()))
        keyed = await provider.get_waf_observations_bucketed(
            window_start=START, window_end=END, bucket_seconds=BUCKET,
            max_groups=8, max_rows=100, **WAF_ARGS,
        )
        single = await provider.get_waf_observations(
            window_start=START, window_end=START + timedelta(seconds=BUCKET),
            max_groups=8, **WAF_ARGS,
        )
        assert keyed.buckets[START].truncated is single.truncated is False

    @pytest.mark.asyncio
    async def test_an_empty_bucket_is_a_measured_empty_one(self) -> None:
        provider = provider_for(WafAriel([record(0, "/a", 5)]))
        keyed = await provider.get_waf_observations_bucketed(
            window_start=START, window_end=END, bucket_seconds=BUCKET,
            max_groups=8, max_rows=100, **WAF_ARGS,
        )
        later = keyed.buckets[START + timedelta(seconds=BUCKET)]
        assert (later.groups, later.total_events, later.truncated) == ([], 0, False)

    @pytest.mark.asyncio
    async def test_a_row_with_a_key_outside_the_window_fails_the_search(self) -> None:
        stray = record(0, "/a", 5)
        stray["ts"] = int(END.timestamp() * 1000)  # at STOP: a bucket not asked for

        class Wide(WafAriel):
            def _within(self, query: str) -> list[Record]:
                return self.records

        provider = provider_for(Wide([record(1, "/a", 5), stray]))
        with pytest.raises(ProviderError, match="outside the window"):
            await provider.get_waf_observations_bucketed(
                window_start=START, window_end=END, bucket_seconds=BUCKET,
                max_groups=8, max_rows=100, **WAF_ARGS,
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kw",
        [
            {"url_property": 'URL"; DROP'},
            {"method_property": "bad,name"},
            {"virtual_host_property": "a)b"},
        ],
    )
    async def test_property_validation_still_rejects_bad_names_before_any_request(
        self, kw: dict
    ) -> None:
        ariel = WafAriel([])
        provider = provider_for(ariel)
        args = {**WAF_ARGS, **kw}
        with pytest.raises(ValueError, match="unsafe QRadar property name"):
            await provider.get_waf_observations_bucketed(
                window_start=START, window_end=END, bucket_seconds=BUCKET,
                max_groups=8, max_rows=100, **args,
            )
        assert ariel.queries == []

    @pytest.mark.asyncio
    async def test_a_misaligned_window_is_rejected_before_any_request(self) -> None:
        ariel = WafAriel([])
        provider = provider_for(ariel)
        with pytest.raises(ValueError, match="epoch-aligned whole buckets"):
            await provider.get_waf_observations_bucketed(
                window_start=START + timedelta(seconds=1), window_end=END,
                bucket_seconds=BUCKET, max_groups=8, max_rows=100, **WAF_ARGS,
            )
        assert ariel.queries == []

    @pytest.mark.asyncio
    async def test_the_virtual_host_is_selected_and_grouped_when_mapped(self) -> None:
        ariel = WafAriel([])
        provider = provider_for(ariel)
        await provider.get_waf_observations_bucketed(
            window_start=START, window_end=END, bucket_seconds=BUCKET, max_groups=8,
            max_rows=100, virtual_host_property="Host", **WAF_ARGS,
        )
        grouped = ariel.queries[0]
        assert '"Host" AS "raw_virtual_host"' in grouped
        assert f'GROUP BY "URL", "HTTP_Method", "Host", starttime / {BUCKET_MS}' in grouped


class TestRestBreakdowns:
    @pytest.mark.asyncio
    async def test_the_query_carries_key_group_by_limit_and_both_bounds(self) -> None:
        ariel = WafAriel(traffic())
        provider = provider_for(ariel)

        batch = await provider.get_waf_breakdown_bucketed(
            window_start=START, window_end=END, bucket_seconds=BUCKET,
            virtual_host_property=None, breakdown="SOURCE_IP", breakdown_property=None,
            max_groups=50, max_rows=60, **WAF_ARGS,
        )

        (query,) = ariel.queries
        start_ms, end_ms = int(START.timestamp() * 1000), int(END.timestamp() * 1000)
        assert f'starttime / {BUCKET_MS} AS "k"' in query
        assert "sourceip IS NOT NULL" in query
        assert f'GROUP BY "URL", "HTTP_Method", sourceip, starttime / {BUCKET_MS}' in query
        assert "LIMIT 61 " in query
        assert f"starttime >= {start_ms} AND starttime < {end_ms}" in query
        assert query.endswith(f"START {start_ms} STOP {end_ms}")
        assert batch.ariel_searches == 1 and batch.truncated is False

    @pytest.mark.asyncio
    async def test_n_plus_one_rows_are_truncated(self) -> None:
        provider = provider_for(WafAriel(traffic()))
        batch = await provider.get_waf_breakdown_bucketed(
            window_start=START, window_end=END, bucket_seconds=BUCKET,
            virtual_host_property=None, breakdown="SOURCE_IP", breakdown_property=None,
            max_groups=50, max_rows=20, **WAF_ARGS,
        )
        assert batch.truncated is True and batch.buckets == {}

    @pytest.mark.asyncio
    async def test_each_bucket_equals_the_per_bucket_query(self) -> None:
        provider = provider_for(WafAriel(traffic()))
        keyed = await provider.get_waf_breakdown_bucketed(
            window_start=START, window_end=END, bucket_seconds=BUCKET,
            virtual_host_property=None, breakdown="SOURCE_IP", breakdown_property=None,
            max_groups=4, max_rows=100, **WAF_ARGS,
        )
        for start, bucket in keyed.buckets.items():
            single = await provider.get_waf_breakdown(
                window_start=start, window_end=start + timedelta(seconds=BUCKET),
                virtual_host_property=None, breakdown="SOURCE_IP", breakdown_property=None,
                max_groups=4, **WAF_ARGS,
            )
            assert bucket.groups == single.groups
            assert bucket.truncated == single.truncated is True

    @pytest.mark.asyncio
    async def test_a_custom_property_breakdown_is_validated_and_quoted(self) -> None:
        ariel = WafAriel([])
        provider = provider_for(ariel)
        await provider.get_waf_breakdown_bucketed(
            window_start=START, window_end=END, bucket_seconds=BUCKET,
            virtual_host_property=None, breakdown="ACTION", breakdown_property="WAF_Action",
            max_groups=5, max_rows=10, **WAF_ARGS,
        )
        assert '"WAF_Action" IS NOT NULL' in ariel.queries[0]
        with pytest.raises(ValueError, match="unsafe QRadar property name"):
            await provider.get_waf_breakdown_bucketed(
                window_start=START, window_end=END, bucket_seconds=BUCKET,
                virtual_host_property=None, breakdown="ACTION",
                breakdown_property="x') OR (1=1", max_groups=5, max_rows=10, **WAF_ARGS,
            )
        with pytest.raises(ValueError, match="unsupported WAF breakdown"):
            await provider.get_waf_breakdown_bucketed(
                window_start=START, window_end=END, bucket_seconds=BUCKET,
                virtual_host_property=None, breakdown="ACTION", breakdown_property=None,
                max_groups=5, max_rows=10, **WAF_ARGS,
            )
        assert len(ariel.queries) == 1


# --------------------------------------------------------------------- the mock
class TestMock:
    @pytest.mark.asyncio
    async def test_each_keyed_bucket_equals_its_per_bucket_output(self) -> None:
        mock = MockQRadarProvider(seed=7)
        start = datetime(2026, 10, 12, 0, 0, tzinfo=UTC)
        end = start + timedelta(seconds=BUCKET * 24)
        for max_groups in (3, 7, 500):  # truncated, exactly full, whole
            keyed = await mock.get_waf_observations_bucketed(
                qradar_log_source_id=9, window_start=start, window_end=end,
                bucket_seconds=BUCKET, url_property="URL", method_property="M",
                virtual_host_property="Host", max_groups=max_groups, max_rows=5000,
            )
            assert keyed.truncated is False and len(keyed.buckets) == 24
            for bucket_start, bucket in keyed.buckets.items():
                single = await mock.get_waf_observations(
                    qradar_log_source_id=9, window_start=bucket_start,
                    window_end=bucket_start + timedelta(seconds=BUCKET),
                    url_property="URL", method_property="M", virtual_host_property="Host",
                    max_groups=max_groups,
                )
                assert bucket.groups == single.groups
                assert bucket.total_events == single.total_events
                assert bucket.truncated == single.truncated

    @pytest.mark.asyncio
    async def test_keyed_breakdown_buckets_equal_their_per_bucket_output(self) -> None:
        mock = MockQRadarProvider(seed=7)
        start = datetime(2026, 10, 12, 0, 0, tzinfo=UTC)
        end = start + timedelta(seconds=BUCKET * 6)
        for breakdown in ("ACTION", "RESPONSE_CODE", "SOURCE_IP"):
            for max_groups in (4, 21, 2000):
                keyed = await mock.get_waf_breakdown_bucketed(
                    qradar_log_source_id=9, window_start=start, window_end=end,
                    bucket_seconds=BUCKET, url_property="URL", method_property="M",
                    virtual_host_property=None, breakdown=breakdown,
                    breakdown_property="P", max_groups=max_groups, max_rows=5000,
                )
                for bucket_start, bucket in keyed.buckets.items():
                    single = await mock.get_waf_breakdown(
                        qradar_log_source_id=9, window_start=bucket_start,
                        window_end=bucket_start + timedelta(seconds=BUCKET),
                        url_property="URL", method_property="M", virtual_host_property=None,
                        breakdown=breakdown, breakdown_property="P", max_groups=max_groups,
                    )
                    assert bucket.groups == single.groups
                    assert bucket.truncated == single.truncated

    @pytest.mark.asyncio
    async def test_a_row_budget_overrun_is_truncated_and_keeps_no_bucket(self) -> None:
        mock = MockQRadarProvider(seed=7)
        start = datetime(2026, 10, 12, 0, 0, tzinfo=UTC)
        batch = await mock.get_waf_observations_bucketed(
            qradar_log_source_id=9, window_start=start,
            window_end=start + timedelta(seconds=BUCKET * 6), bucket_seconds=BUCKET,
            url_property="URL", method_property="M", max_groups=500, max_rows=10,
        )
        assert batch.truncated is True and batch.buckets == {}

    @pytest.mark.asyncio
    async def test_a_misaligned_window_is_rejected(self) -> None:
        mock = MockQRadarProvider(seed=7)
        with pytest.raises(ValueError):
            await mock.get_waf_observations_bucketed(
                qradar_log_source_id=9, window_start=START + timedelta(seconds=1),
                window_end=END, bucket_seconds=BUCKET, url_property="URL",
                method_property="M", max_groups=5, max_rows=10,
            )
