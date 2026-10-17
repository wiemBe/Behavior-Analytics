"""The property-watch query generator: golden text, the IN form, and hostile input."""

from __future__ import annotations

import random
import re
import string

import pytest

from app.models.enums import PwScopeKind as K
from app.property_watch.aql import (
    build_query,
    pinned_components,
    pinned_rows_per_bucket,
    scope_condition,
    validate_query_inputs,
)
from app.property_watch.reference import PwReferenceError
from app.providers.dto import PwSearchKind as S

TAIL = "GROUP BY {group} ORDER BY SUM(eventcount) DESC LIMIT 5001"


def query(
    search: S,
    kind: K,
    ids: list[int],
    props: list[tuple[str, str]],
    pinned: list[tuple[str, ...]] | None = None,
    *,
    max_sources: int = 50,
) -> str:
    spec = validate_query_inputs(
        search,
        scope_kind=kind,
        scope_ids=ids,
        properties=props,
        pinned=pinned or (),
        max_sources=max_sources,
    )
    return build_query(spec, start_ms=1000, end_ms=2000, bucket_ms=300000, limit=5001)


USER = ("NORMALIZED", "username")
ACTION = ("CUSTOM_PROPERTY", "Action")


class TestGolden:
    def test_source_scope_one_property(self) -> None:
        assert query(S.GROUPED, K.LOG_SOURCE, [412], [("CUSTOM_PROPERTY", "WAF Action")]) == (
            'SELECT "WAF Action" AS "p0", starttime / 300000 AS "k", SUM(eventcount) AS "events" '
            "FROM events WHERE logsourceid IN (412) AND starttime >= 1000 AND starttime < 2000 "
            'AND "WAF Action" IS NOT NULL '
            'GROUP BY "WAF Action", starttime / 300000 ORDER BY SUM(eventcount) DESC LIMIT 5001'
        )

    def test_type_scope_two_properties(self) -> None:
        assert query(S.GROUPED, K.LOG_SOURCE_TYPE, [11], [USER, ACTION]) == (
            'SELECT "username" AS "p0", "Action" AS "p1", starttime / 300000 AS "k", '
            'SUM(eventcount) AS "events" '
            'FROM events WHERE devicetype = 11 AND starttime >= 1000 AND starttime < 2000 '
            'AND "username" IS NOT NULL AND "Action" IS NOT NULL '
            'GROUP BY "username", "Action", starttime / 300000 '
            "ORDER BY SUM(eventcount) DESC LIMIT 5001"
        )

    def test_group_scope_one_chunk_sorts_and_dedupes_the_ids(self) -> None:
        assert query(S.GROUPED, K.LOG_SOURCE_GROUP, [5, 3, 4, 3], [USER]) == (
            'SELECT "username" AS "p0", starttime / 300000 AS "k", SUM(eventcount) AS "events" '
            'FROM events WHERE logsourceid IN (3, 4, 5) AND starttime >= 1000 AND starttime < 2000 '
            'AND "username" IS NOT NULL '
            'GROUP BY "username", starttime / 300000 ORDER BY SUM(eventcount) DESC LIMIT 5001'
        )

    def test_a_group_chunk_is_a_plain_in_list_per_chunk(self) -> None:
        # Two chunks of one group are two independent searches over disjoint id lists.
        first = query(S.TOTALS, K.LOG_SOURCE_GROUP, [1, 2], [])
        second = query(S.TOTALS, K.LOG_SOURCE_GROUP, [3, 4], [])
        assert "logsourceid IN (1, 2)" in first and "logsourceid IN (3, 4)" in second

    def test_totals_has_no_property_condition_and_one_row_per_bucket_key(self) -> None:
        assert query(S.TOTALS, K.LOG_SOURCE, [412], []) == (
            'SELECT starttime / 300000 AS "k", SUM(eventcount) AS "events" '
            "FROM events WHERE logsourceid IN (412) AND starttime >= 1000 AND starttime < 2000 "
            "GROUP BY starttime / 300000 ORDER BY SUM(eventcount) DESC LIMIT 5001"
        )

    def test_totals_ignores_valid_properties(self) -> None:
        assert query(S.TOTALS, K.LOG_SOURCE, [412], [USER]) == query(
            S.TOTALS, K.LOG_SOURCE, [412], []
        )

    def test_pinned_one_property_uses_in(self) -> None:
        text = query(S.PINNED, K.LOG_SOURCE, [412], [USER], [("bob",), ("alice",)])
        assert text == (
            'SELECT "username" AS "p0", starttime / 300000 AS "k", SUM(eventcount) AS "events" '
            'FROM events WHERE logsourceid IN (412) AND starttime >= 1000 AND starttime < 2000 '
            "AND \"username\" IS NOT NULL AND \"username\" IN ('alice','bob') "
            'GROUP BY "username", starttime / 300000 ORDER BY SUM(eventcount) DESC LIMIT 5001'
        )

    def test_pinned_combination_is_in_and_in_over_the_distinct_components(self) -> None:
        pins = [("alice", "login"), ("bob", "login"), ("alice", "logout")]
        text = query(S.PINNED, K.LOG_SOURCE_GROUP, [3, 4], [USER, ACTION], pins)
        assert text == (
            'SELECT "username" AS "p0", "Action" AS "p1", starttime / 300000 AS "k", '
            'SUM(eventcount) AS "events" '
            "FROM events WHERE logsourceid IN (3, 4) AND starttime >= 1000 AND starttime < 2000 "
            'AND "username" IS NOT NULL AND "Action" IS NOT NULL '
            "AND \"username\" IN ('alice','bob') AND \"Action\" IN ('login','logout') "
            'GROUP BY "username", "Action", starttime / 300000 '
            "ORDER BY SUM(eventcount) DESC LIMIT 5001"
        )

    def test_the_pin_generator_never_emits_or_ed_equality(self) -> None:
        pins = [("a", "x"), ("b", "y"), ("c", "z")]
        text = query(S.PINNED, K.LOG_SOURCE, [1], [USER, ACTION], pins)
        assert " OR " not in text.upper()
        assert not re.search(r"\"\s*=\s*'", text)

    def test_limit_is_the_callers_cap_plus_one(self) -> None:
        spec = validate_query_inputs(
            S.GROUPED,
            scope_kind=K.LOG_SOURCE,
            scope_ids=[1],
            properties=[USER],
            max_sources=50,
        )
        text = build_query(spec, start_ms=1000, end_ms=2000, bucket_ms=300000, limit=11)
        assert text.endswith("LIMIT 11")

    def test_scope_condition_forms(self) -> None:
        assert scope_condition(K.LOG_SOURCE, [7]) == "logsourceid IN (7)"
        assert scope_condition(K.LOG_SOURCE_TYPE, [7]) == "devicetype = 7"
        assert scope_condition(K.LOG_SOURCE_GROUP, [7, 8]) == "logsourceid IN (7, 8)"

    def test_pinned_row_bound_is_the_product_of_the_distinct_components(self) -> None:
        spec = validate_query_inputs(
            S.PINNED,
            scope_kind=K.LOG_SOURCE,
            scope_ids=[1],
            properties=[USER, ACTION],
            pinned=[("a", "x"), ("b", "x"), ("a", "y")],
            max_sources=50,
        )
        assert pinned_components(spec) == (("a", "b"), ("x", "y"))
        assert pinned_rows_per_bucket(spec) == 4


class TestRefusals:
    @pytest.mark.parametrize(
        ("kind", "ids"),
        [
            (K.LOG_SOURCE, []),
            (K.LOG_SOURCE, [1, 2]),
            (K.LOG_SOURCE_TYPE, [1, 2]),
            (K.LOG_SOURCE_GROUP, []),
            (K.LOG_SOURCE, [-1]),
            (K.LOG_SOURCE, [True]),
            (K.LOG_SOURCE, ["1; DROP"]),
            (K.LOG_SOURCE_GROUP, [1.5]),
        ],
    )
    def test_bad_scope_ids(self, kind: K, ids: list) -> None:
        with pytest.raises(ValueError):
            query(S.TOTALS, kind, ids, [])

    def test_a_group_chunk_over_the_source_cap_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at most 2"):
            query(S.TOTALS, K.LOG_SOURCE_GROUP, [1, 2, 3], [], max_sources=2)

    @pytest.mark.parametrize(
        "props",
        [
            [("NORMALIZED", "sourceip")],
            [("NORMALIZED", "username"), ("NORMALIZED", "username")],
            [USER, ACTION, ("CUSTOM_PROPERTY", "Third")],
            [("CUSTOM_PROPERTY", 'x" OR 1=1 --')],
            [("CUSTOM_PROPERTY", "")],
            [("NOPE", "x")],
            [("CUSTOM_PROPERTY", None)],
            [("CUSTOM_PROPERTY",)],
        ],
    )
    def test_a_bad_reference_never_reaches_the_generator(self, props: list) -> None:
        with pytest.raises(PwReferenceError) as caught:
            query(S.GROUPED, K.LOG_SOURCE, [1], props)
        assert "OR 1=1" not in str(caught.value)

    def test_a_grouped_search_needs_a_property(self) -> None:
        with pytest.raises(PwReferenceError):
            query(S.GROUPED, K.LOG_SOURCE, [1], [])

    @pytest.mark.parametrize("bad", ["o'brien", 'a"b', "a\\b", "a\nb", " a", "a ", "", "x" * 256])
    def test_a_bad_pin_is_refused_without_echo(self, bad: str) -> None:
        with pytest.raises(PwReferenceError) as caught:
            query(S.PINNED, K.LOG_SOURCE, [1], [USER], [(bad,)])
        if len(bad) > 3:
            assert bad not in str(caught.value)

    def test_pins_must_match_the_property_count_and_the_search(self) -> None:
        with pytest.raises(PwReferenceError):
            query(S.PINNED, K.LOG_SOURCE, [1], [USER, ACTION], [("a",)])
        with pytest.raises(PwReferenceError):
            query(S.PINNED, K.LOG_SOURCE, [1], [USER], [])
        with pytest.raises(PwReferenceError):
            query(S.GROUPED, K.LOG_SOURCE, [1], [USER], [("a",)])

    @pytest.mark.parametrize("field", ["start_ms", "end_ms", "bucket_ms", "limit"])
    def test_bounds_are_positive_integers(self, field: str) -> None:
        spec = validate_query_inputs(
            S.TOTALS, scope_kind=K.LOG_SOURCE, scope_ids=[1], properties=[], max_sources=50
        )
        bounds = {"start_ms": 1000, "end_ms": 2000, "bucket_ms": 300000, "limit": 5}
        for bad in (0, -1, True, "5", 1.5):
            with pytest.raises(ValueError):
                build_query(spec, **{**bounds, field: bad})


ALPHABET = string.ascii_letters + string.digits + " _-'\"\\;()=,%\n\t\x00\u2028é*/"


def _random_text(rng: random.Random) -> str:
    return "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 12)))


class TestHostileInput:
    """Whatever text arrives, operator characters sit only in a validated, quoted position."""

    def test_operator_text_appears_only_inside_validated_quotes(self) -> None:
        rng = random.Random(20261017)
        accepted_names = accepted_pins = rejected = 0
        for _ in range(3000):
            name = _random_text(rng)
            pin = _random_text(rng)
            kind = rng.choice(["CUSTOM_PROPERTY", "NORMALIZED"])
            try:
                text = query(S.PINNED, K.LOG_SOURCE, [9], [(kind, name)], [(pin,)])
            except ValueError:
                rejected += 1
                continue
            accepted_names += 1
            accepted_pins += 1
            # Every double-quoted run is the validated name; every single-quoted run is
            # the validated pin; with those cut out, only generator text remains.
            assert re.findall(r'"([^"]*)"', text).count(name) >= 1
            assert set(re.findall(r'"([^"]*)"', text)) <= {name, "p0", "k", "events"}
            assert re.findall(r"'([^']*)'", text) == [pin]
            skeleton = re.sub(r"\"[^\"]*\"|'[^']*'", "Q", text)
            assert re.fullmatch(r"[A-Za-z0-9 _/(),<>=*]+", skeleton), skeleton
        assert accepted_names > 50, "the generator never produced an accepted case"
        assert rejected > 500, "the generator never produced a rejected case"

    def test_a_rejected_pin_or_name_never_reaches_the_generator(self) -> None:
        rng = random.Random(7)
        for _ in range(2000):
            pin = _random_text(rng)
            try:
                text = query(S.PINNED, K.LOG_SOURCE, [9], [USER], [(pin,)])
            except PwReferenceError:
                continue
            # Accepted: nothing but a clean literal. A rejected one produced no text at all.
            assert not re.search(r"['\"\\\x00-\x1f]", pin)
            assert f"'{pin}'" in text
