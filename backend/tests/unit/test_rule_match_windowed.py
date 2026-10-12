"""The keyed multi-window `creEventList` search (T-037): fold, REST query, mock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.providers.base import CapabilityNotSupportedError, ProviderError
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_mcp import QRadarMCPProvider
from app.providers.qradar_rest import parse_rule_match_rows
from app.rules.matches import fold_windowed_rows, window_starts
from tests.unit.test_qradar_rest_provider import build_provider
from tests.unit.test_rule_match_provider import search_handler

HOUR = 3600
START = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)
END = START + timedelta(hours=4)
WINDOW_MS = HOUR * 1000
FIRST_KEY = int(START.timestamp() * 1000) // WINDOW_MS


def row(rule_id, k, records=1, events=1) -> dict:
    return {"rule_id": rule_id, "k": k, "records": records, "events": events}


def fold(rows, **kw):
    args = {
        "window_start": START,
        "window_end": END,
        "window_seconds": HOUR,
        "row_cap": 100,
        "parse_rows": parse_rule_match_rows,
        "query_version": "rule_match/v2",
        "query": "q",
    }
    return fold_windowed_rows(rows, **{**args, **kw})


class TestWindowStarts:
    def test_a_whole_aligned_span_lists_its_windows(self) -> None:
        assert window_starts(START, END, HOUR) == [START + timedelta(hours=i) for i in range(4)]

    @pytest.mark.parametrize(
        ("start", "end"),
        [
            (START + timedelta(minutes=30), END + timedelta(minutes=30)),  # off the grid
            (START, END + timedelta(minutes=30)),  # not a whole number of windows
            (END, START),  # inverted
            (START, START),  # empty
            (START.replace(tzinfo=None), END),  # naive
        ],
    )
    def test_a_misaligned_span_is_rejected(self, start, end) -> None:
        with pytest.raises(ValueError):
            window_starts(start, end, HOUR)

    def test_alignment_is_the_epoch_grid_not_the_hour_of_day(self) -> None:
        # 90-minute windows: 08:00 is 1,791,792,000 s, not a multiple of 5,400.
        with pytest.raises(ValueError, match="grid"):
            window_starts(START, START + timedelta(minutes=180), 5400)


class TestFold:
    def test_keys_map_to_windows_and_every_window_gets_a_batch(self) -> None:
        batches = fold(
            [row(1, FIRST_KEY, 2, 5), row(2, FIRST_KEY, 1, 1), row(1, FIRST_KEY + 2, 7, 9)]
        )

        assert list(batches) == window_starts(START, END, HOUR)
        by_hour = {s.hour: b for s, b in batches.items()}
        assert [(r.qradar_rule_id, r.records, r.events) for r in by_hour[8].rows] == [
            (1, 2, 5),
            (2, 1, 1),
        ]
        assert by_hour[9].rows == [] and by_hour[11].rows == []  # searched, empty
        assert [(r.qradar_rule_id, r.events) for r in by_hour[10].rows] == [(1, 9)]
        assert all(
            b.row_cap == 100 and b.query_version == "rule_match/v2" for b in batches.values()
        )
        assert all(not b.truncated and b.malformed_rows == 0 for b in batches.values())

    @pytest.mark.parametrize("key", [FIRST_KEY - 1, FIRST_KEY + 4, 0])
    def test_a_key_outside_the_span_fails_the_whole_search(self, key) -> None:
        with pytest.raises(ProviderError, match="outside"):
            fold([row(1, FIRST_KEY), row(2, key)])

    def test_a_null_rule_id_still_has_its_key_checked(self) -> None:
        with pytest.raises(ProviderError):
            fold([row(None, FIRST_KEY + 9)])

    @pytest.mark.parametrize("key", [None, "x", FIRST_KEY + 0.5, True, str(FIRST_KEY)])
    def test_a_non_integer_key_fails_the_whole_search(self, key) -> None:
        with pytest.raises(ProviderError, match="non-integer"):
            fold([row(1, key)])

    def test_a_missing_key_fails_the_whole_search(self) -> None:
        with pytest.raises(ProviderError):
            fold([{"rule_id": 1, "records": 1, "events": 1}])

    def test_an_integral_float_key_is_accepted(self) -> None:
        batches = fold([row(1, float(FIRST_KEY + 1))])

        assert [len(b.rows) for b in batches.values()] == [0, 1, 0, 0]

    def test_malformed_rows_are_handled_as_the_per_window_parser_does(self) -> None:
        raw = [
            row(None, FIRST_KEY),  # no rule matched: excluded, not malformed
            row("junk", FIRST_KEY),
            row(5.5, FIRST_KEY),
            row(7, FIRST_KEY, records=None),
            row(8, FIRST_KEY, events=-1),
            row(9, FIRST_KEY),
            row("junk", FIRST_KEY + 1),
        ]

        batches = fold(raw)

        first, second = list(batches.values())[:2]
        expected_rows, expected_malformed = parse_rule_match_rows(raw[:6])
        assert first.rows == expected_rows and first.malformed_rows == expected_malformed == 4
        assert second.rows == [] and second.malformed_rows == 1

    def test_a_window_at_the_cap_is_truncated_as_a_single_query_would_be(self) -> None:
        batches = fold([row(i, FIRST_KEY) for i in range(3)] + [row(1, FIRST_KEY + 1)], row_cap=3)

        flags = [b.truncated for b in batches.values()]
        assert flags == [True, False, False, False]


class TestRestWindowed:
    @pytest.mark.asyncio
    async def test_the_query_is_keyed_grouped_bounded_and_probes_one_row_past_the_cap(
        self,
    ) -> None:
        captured: dict = {}
        provider, _ = build_provider(search_handler([], captured), page_size=100, max_pages=5)

        result = await provider.get_rule_matches_windowed(
            START, END, window_seconds=HOUR, max_rows=200
        )

        query = captured["query"]
        start_ms, end_ms = int(START.timestamp() * 1000), int(END.timestamp() * 1000)
        assert f'starttime / {WINDOW_MS} AS "k"' in query
        assert f"GROUP BY creEventList, starttime / {WINDOW_MS}" in query
        assert "ORDER BY COUNT(*) DESC LIMIT 201 " in query
        assert f"WHERE starttime >= {start_ms} AND starttime < {end_ms}" in query
        assert f"START {start_ms} STOP {end_ms}" in query
        assert captured["range"] == "items=0-201"
        assert result.truncated is False
        assert result.row_cap == 200
        assert result.ariel_searches == 1
        assert len(result.batches) == 4
        assert {b.query_version for b in result.batches.values()} == {"rule_match/v2"}

    @pytest.mark.asyncio
    async def test_rows_land_in_their_windows(self) -> None:
        rows = [row(1, FIRST_KEY, 3, 6), row(2, FIRST_KEY + 3, 1, 1)]
        provider, _ = build_provider(search_handler(rows), page_size=100, max_pages=5)

        result = await provider.get_rule_matches_windowed(
            START, END, window_seconds=HOUR, max_rows=10
        )

        sizes = [len(b.rows) for b in result.batches.values()]
        assert sizes == [1, 0, 0, 1]

    @pytest.mark.asyncio
    async def test_n_plus_one_rows_mean_truncated_and_n_rows_do_not(self) -> None:
        many = [row(i, FIRST_KEY) for i in range(4)]
        provider, _ = build_provider(search_handler(many), page_size=100, max_pages=5)

        over = await provider.get_rule_matches_windowed(
            START, END, window_seconds=HOUR, max_rows=3
        )
        exact = await provider.get_rule_matches_windowed(
            START, END, window_seconds=HOUR, max_rows=4
        )

        assert over.truncated is True
        assert exact.truncated is False

    @pytest.mark.asyncio
    async def test_the_cap_is_bounded_by_the_provider_ceiling(self) -> None:
        provider, _ = build_provider(search_handler([]), page_size=10, max_pages=2)

        result = await provider.get_rule_matches_windowed(
            START, END, window_seconds=HOUR, max_rows=5000
        )

        assert result.row_cap == 20

    @pytest.mark.parametrize(
        ("start", "end"),
        [
            (START + timedelta(minutes=30), END + timedelta(minutes=30)),
            (START, END + timedelta(minutes=30)),
            (END, START),
        ],
    )
    @pytest.mark.asyncio
    async def test_a_misaligned_window_is_rejected_before_any_request(self, start, end) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request may be made")

        provider, _ = build_provider(handler)

        with pytest.raises(ValueError):
            await provider.get_rule_matches_windowed(
                start, end, window_seconds=HOUR, max_rows=10
            )

    @pytest.mark.asyncio
    async def test_a_key_outside_the_span_fails_the_search(self) -> None:
        provider, _ = build_provider(
            search_handler([row(1, FIRST_KEY + 99)]), page_size=100, max_pages=5
        )

        with pytest.raises(ProviderError):
            await provider.get_rule_matches_windowed(
                START, END, window_seconds=HOUR, max_rows=10
            )

    @pytest.mark.asyncio
    async def test_a_failed_search_raises_instead_of_returning_empty_windows(self) -> None:
        provider, _ = build_provider(search_handler([], status="ERROR"))

        with pytest.raises(ProviderError):
            await provider.get_rule_matches_windowed(
                START, END, window_seconds=HOUR, max_rows=10
            )


class TestMockWindowed:
    @pytest.mark.asyncio
    async def test_each_window_equals_the_per_window_call(self) -> None:
        mock = MockQRadarProvider(seed=1)

        result = await mock.get_rule_matches_windowed(
            START, END, window_seconds=HOUR, max_rows=100
        )

        assert result.truncated is False
        for start in window_starts(START, END, HOUR):
            single = await mock.get_rule_matches(
                start, start + timedelta(hours=1), max_rows=100
            )
            assert result.batches[start] == single

    @pytest.mark.asyncio
    async def test_it_is_truncated_when_the_span_holds_more_than_the_cap(self) -> None:
        mock = MockQRadarProvider(seed=1)  # 7 rows a window, 4 windows

        over = await mock.get_rule_matches_windowed(START, END, window_seconds=HOUR, max_rows=27)
        exact = await mock.get_rule_matches_windowed(START, END, window_seconds=HOUR, max_rows=28)

        assert over.truncated is True
        assert exact.truncated is False

    @pytest.mark.asyncio
    async def test_a_misaligned_span_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            await MockQRadarProvider(seed=1).get_rule_matches_windowed(
                START, END + timedelta(minutes=1), window_seconds=HOUR, max_rows=10
            )

    @pytest.mark.asyncio
    async def test_mcp_cannot_run_it(self) -> None:
        provider = QRadarMCPProvider.__new__(QRadarMCPProvider)

        with pytest.raises(CapabilityNotSupportedError):
            await provider.get_rule_matches_windowed(
                START, END, window_seconds=HOUR, max_rows=10
            )
