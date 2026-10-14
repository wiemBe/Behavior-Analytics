"""The pure geo fold: classification, summing, the cap, and that no key is ever logged."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from app.geo.fold import (
    GeoKeyedRow,
    GeoRemainder,
    classify_key,
    fold_geo_bucket,
    parse_geo_rows,
)
from app.providers.base import ProviderError
from app.providers.mock import geo_key

AT = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
US = geo_key("US", "United States", 6252001)


def rows(*pairs: tuple[str | None, int]) -> list[GeoKeyedRow]:
    return [GeoKeyedRow(key=key, bucket_start=AT, events=events) for key, events in pairs]


class TestClassify:
    def test_a_valid_code_is_a_country(self) -> None:
        assert classify_key(US) == "US"

    def test_the_empty_object_is_unresolved(self) -> None:
        assert classify_key("{}") is GeoRemainder.UNRESOLVED

    def test_an_object_with_no_country_is_unresolved(self) -> None:
        assert classify_key('{"registered_country":{"iso_code":"US"}}') is (
            GeoRemainder.UNRESOLVED
        )

    @pytest.mark.parametrize(
        "key",
        [
            None,
            "",
            "not json",
            "[]",
            '"US"',
            "null",
            "42",
            '{"country":null}',
            '{"country":{}}',
            '{"country":{"iso_code":"USA"}}',
            '{"country":{"iso_code":"us"}}',
            '{"country":{"iso_code":"U"}}',
            '{"country":{"iso_code":"U1"}}',
            '{"country":{"iso_code":"ÜS"}}',
            '{"country":{"iso_code":12}}',
            '{"country":{"iso_code":"US\\n"}}',
            "{" * 100_000,
        ],
    )
    def test_anything_else_is_unparseable_and_never_raises(self, key: str | None) -> None:
        assert classify_key(key) is GeoRemainder.UNPARSEABLE


class TestFold:
    def test_a_code_is_summed_across_strings(self) -> None:
        a = geo_key("US", "United States", 6252001)
        b = geo_key("US", "United States", 6252001, confidence=90)
        fact = fold_geo_bucket(rows((a, 40), (b, 2), (geo_key("DE"), 5)))
        assert fact.countries == (("US", 42), ("DE", 5))
        assert (fact.unresolved, fact.over_cap, fact.unparseable) == (0, 0, 0)

    def test_the_remainders_are_counted_apart(self) -> None:
        fact = fold_geo_bucket(rows(("{}", 7), ('{"x":1}', 3), ("junk", 11), (US, 1)))
        assert fact.countries == (("US", 1),)
        assert (fact.unresolved, fact.unparseable, fact.unparseable_keys) == (10, 11, 1)
        assert fact.events == 22

    def test_an_empty_bucket_folds_to_nothing(self) -> None:
        fact = fold_geo_bucket([])
        assert fact.countries == () and fact.events == 0

    def test_the_cap_keeps_the_busiest_ties_by_code_and_sums_the_rest(self) -> None:
        codes = [f"{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(70)]
        # 70 codes, all with 5 events: a pure tie, so the first 64 by code are kept.
        fact = fold_geo_bucket(rows(*((geo_key(c), 5) for c in reversed(codes))))
        assert [c for c, _ in fact.countries] == sorted(codes)[:64]
        assert fact.over_cap == 6 * 5
        assert fact.events == 70 * 5

    def test_a_busy_code_beats_the_alphabet(self) -> None:
        fact = fold_geo_bucket(
            rows((geo_key("ZZ"), 99), (geo_key("AA"), 1), (geo_key("AB"), 1)), max_countries=2
        )
        assert fact.countries == (("ZZ", 99), ("AA", 1))
        assert fact.over_cap == 1

    def test_the_cap_must_keep_a_country(self) -> None:
        with pytest.raises(ValueError):
            fold_geo_bucket([], max_countries=0)

    def test_no_key_is_ever_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        secret = '{"country":{"iso_code":"SECRET-KEY-TEXT","ip":"203.0.113.77"}}'
        with caplog.at_level(logging.DEBUG):
            fold_geo_bucket(rows((secret, 4), ("SECRET-JUNK 10.1.2.3", 1), (US, 2)))
        assert caplog.records, "the defect is logged, by count"
        text = caplog.text
        assert "SECRET" not in text and "203.0.113.77" not in text and "10.1.2.3" not in text
        assert "2 distinct unparseable" in text


class TestParse:
    def raw(self, **over: object) -> dict:
        return {"geo": US, "k": int(AT.timestamp()) // 300, "events": "5", **over}

    def parse(self, *raw: dict, max_rows: int = 10):  # type: ignore[no-untyped-def]
        return parse_geo_rows(
            list(raw),
            window_start=AT,
            window_end=datetime(2026, 10, 13, 9, 15, tzinfo=UTC),
            bucket_seconds=300,
            max_rows=max_rows,
        )

    def test_a_row_is_parsed(self) -> None:
        parsed, truncated = self.parse(self.raw())
        assert parsed[0].key == US and parsed[0].events == 5 and parsed[0].bucket_start == AT
        assert not truncated

    def test_a_missing_or_non_text_key_is_kept_as_none(self) -> None:
        parsed, _ = self.parse(self.raw(geo=None), self.raw(geo=5))
        assert [r.key for r in parsed] == [None, None]

    @pytest.mark.parametrize(
        "over",
        [{"k": None}, {"k": "x"}, {"k": 0}, {"k": 10**9}, {"events": None}, {"events": -1}],
    )
    def test_a_malformed_row_fails_the_search(self, over: dict) -> None:
        with pytest.raises(ProviderError):
            self.parse(self.raw(**over))

    def test_more_than_max_rows_is_truncation(self) -> None:
        parsed, truncated = self.parse(*[self.raw(events=i) for i in range(4)], max_rows=3)
        assert truncated and len(parsed) == 3
