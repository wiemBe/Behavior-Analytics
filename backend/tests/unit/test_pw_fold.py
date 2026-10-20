"""The pure property-watch fold: sanitizing, the cut, pins, measurement and coverage."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest

from app.models.enums import PwBucketMeasurement as Meas
from app.models.enums import PwPinnedMeasurement as PinM
from app.models.enums import WafMeasurement
from app.property_watch.facts import (
    TUPLE_SEPARATOR,
    PwReadingKind,
    PwRemainderBound,
    join_tuple_key,
    reading_for,
)
from app.property_watch.fold import (
    REPLACEMENT,
    PwFoldError,
    component_limit,
    fold_bucket,
    pinned_value_keys,
    sanitize_component,
)
from app.property_watch.keyed_search import PwBucketOutcome, PwCellState, combine_chunks
from app.providers.dto import PwKeyedRow
from app.providers.dto import PwSearchKind as S

BUCKET = 300
T = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
C, L, F, N = (
    PwCellState.COMPLETE,
    PwCellState.LOWER_BOUND,
    PwCellState.FAILED,
    PwCellState.NOT_COLLECTED,
)


def vrow(*values: str | None, events: int = 10, when: datetime = T) -> PwKeyedRow:
    return PwKeyedRow(bucket_start=when, events=events, values=values)


def grouped(rows: list[PwKeyedRow], state: PwCellState = C) -> PwBucketOutcome:
    return PwBucketOutcome(S.GROUPED, T, state, tuple(rows) if state in (C, L) else ())


def totals(events: int | None, state: PwCellState = C) -> PwBucketOutcome:
    rows = (PwKeyedRow(bucket_start=T, events=events),) if events is not None else ()
    return PwBucketOutcome(S.TOTALS, T, state, rows if state in (C, L) else ())


def pinned_cell(rows: list[PwKeyedRow], state: PwCellState = C) -> PwBucketOutcome:
    return PwBucketOutcome(S.PINNED, T, state, tuple(rows) if state in (C, L) else ())


def fold(
    g: PwBucketOutcome,
    t: PwBucketOutcome | None = None,
    *,
    props: int = 1,
    pinned: list[tuple[str, ...]] | None = None,
    pinned_outcome: PwBucketOutcome | None = None,
    tracked: tuple[str, ...] = (),
    max_values: int = 50,
    max_length: int = 255,
):
    return fold_bucket(
        bucket_start=T,
        bucket_seconds=BUCKET,
        watch_version=1,
        property_count=props,
        grouped=g,
        totals=t or totals(1000),
        pinned_outcome=pinned_outcome,
        pinned=pinned or (),
        tracked=tracked,
        max_values=max_values,
        value_max_length=max_length,
    )


def by_key(fact) -> dict[str, int]:
    return {v.value_key: v.events for v in fact.values}


class TestSanitize:
    def test_strips_and_keeps_inner_spaces(self) -> None:
        assert sanitize_component("  Al  ice \t", 255) == ("Al  ice", False)

    def test_replaces_unprintable_characters(self) -> None:
        text, cut = sanitize_component("a\x00b\x1fc​d\ud800e", 255) or ("", False)
        assert not cut and text.count(REPLACEMENT) == 4
        assert text.isprintable()
        assert TUPLE_SEPARATOR not in text

    def test_two_raw_strings_that_sanitize_alike_are_one_value(self) -> None:
        assert sanitize_component("a\x00b", 255) == sanitize_component("a\x01b", 255)
        assert sanitize_component(" a ", 255) == sanitize_component("a", 255)

    def test_the_cut_is_flagged_and_never_leaves_trailing_space(self) -> None:
        assert sanitize_component("x" * 300, 255) == ("x" * 255, True)
        text, cut = sanitize_component("ab cd", 3) or ("", False)
        assert (text, cut) == ("ab", True)

    @pytest.mark.parametrize("raw", [None, "", "   ", "\t\n", "\x1f", 5, 1.5, b"x", [], True])
    def test_unparsed_forms(self, raw: object) -> None:
        assert sanitize_component(raw, 255) is None

    def test_hostile_input_never_raises(self) -> None:
        rng = random.Random(99)
        pool = "ab \t\x00\x1f\x7f\x85'\"\\;zzzzz"
        pool += "".join(chr(code) for code in (0x200B, 0x2028, 0xD800, 0xE9, 0x1F642))
        for _ in range(3000):
            raw = "".join(rng.choice(pool) for _ in range(rng.randint(0, 400)))
            result = sanitize_component(raw, rng.randint(1, 255))
            if result is not None:
                text, _ = result
                assert text and text == text.strip() and text.isprintable()
                assert TUPLE_SEPARATOR not in text

    def test_component_limit_keeps_the_joined_key_inside_the_width(self) -> None:
        assert component_limit(1, 255) == 255
        assert component_limit(2, 255) == 127
        assert 2 * component_limit(2, 254) + 1 <= 254
        for bad in ((0, 255), (3, 255), (1, 2), (1, 256)):
            with pytest.raises(PwFoldError):
                component_limit(*bad)


class TestValues:
    def test_hostile_values_are_counted_never_raised(self) -> None:
        fact = fold(
            grouped([vrow("a\x00b", events=3), vrow("\ud800", events=2), vrow("ok", events=5)])
        )
        assert fact.parsed_events == 10
        assert by_key(fact) == {"ok": 5, f"a{REPLACEMENT}b": 3, REPLACEMENT: 2}

    def test_rows_that_sanitize_to_one_value_add_up(self) -> None:
        fact = fold(grouped([vrow("a ", events=3), vrow(" a", events=4), vrow("a\x00", events=1)]))
        assert by_key(fact) == {"a": 7, f"a{REPLACEMENT}": 1}

    def test_the_tuple_key_joins_components_with_the_separator(self) -> None:
        fact = fold(grouped([vrow("alice", "login", events=6)]), props=2)
        (value,) = fact.values
        assert value.value_key == "alice" + TUPLE_SEPARATOR + "login"
        assert value.components == ("alice", "login")
        assert value.value_key == join_tuple_key(value.components)

    def test_a_long_tuple_is_cut_per_component_to_fit_the_stored_width(self) -> None:
        fact = fold(grouped([vrow("a" * 300, "b" * 300)]), props=2)
        (value,) = fact.values
        assert len(value.value_key) == 255 and value.truncated
        assert value.components == ("a" * 127, "b" * 127)

    def test_a_cut_value_is_flagged_and_an_uncut_one_is_not(self) -> None:
        fact = fold(grouped([vrow("x" * 300, events=2), vrow("y", events=1)]))
        flags = {v.value_key[0]: v.truncated for v in fact.values}
        assert flags == {"x": True, "y": False}

    def test_a_row_with_a_null_or_empty_component_is_unparsed_not_a_value(self) -> None:
        fact = fold(
            grouped([vrow("a", None, events=4), vrow("", "x", events=3), vrow("a", "x", events=5)]),
            props=2,
            t=None,
        )
        assert by_key(fact) == {"a" + TUPLE_SEPARATOR + "x": 5}
        assert fact.parsed_events == 5
        assert fact.unparsed_events == 995

    def test_a_row_with_the_wrong_number_of_values_is_a_bug(self) -> None:
        with pytest.raises(PwFoldError, match="one value per property"):
            fold(grouped([vrow("a", "b")]))

    def test_a_row_in_another_bucket_is_refused(self) -> None:
        stray = vrow("a", when=T + timedelta(seconds=BUCKET))
        with pytest.raises(PwFoldError, match="another bucket"):
            fold(grouped([stray]))

    def test_outcomes_must_be_the_right_search_and_bucket(self) -> None:
        with pytest.raises(PwFoldError, match="expected a GROUPED"):
            fold(totals(5))
        with pytest.raises(PwFoldError, match="another bucket"):
            fold(PwBucketOutcome(S.GROUPED, T + timedelta(seconds=BUCKET), C))

    def test_values_are_ordered_by_events_then_key(self) -> None:
        fact = fold(grouped([vrow("b", events=5), vrow("a", events=5), vrow("c", events=9)]))
        assert [v.value_key for v in fact.values] == ["c", "a", "b"]


class TestTheCut:
    rows: ClassVar[list[PwKeyedRow]] = [vrow(f"v{i:02d}", events=100 - i) for i in range(10)]

    def test_untracked_values_are_kept_only_inside_the_cut(self) -> None:
        fact = fold(grouped(self.rows), max_values=3)
        assert [v.value_key for v in fact.values] == ["v00", "v01", "v02"]

    def test_ties_are_cut_by_value_so_the_result_is_stable(self) -> None:
        tied = [vrow(k, events=5) for k in ("d", "b", "a", "c")]
        assert [v.value_key for v in fold(grouped(tied), max_values=2).values] == ["a", "b"]

    def test_a_tracked_value_is_kept_whatever_its_rank(self) -> None:
        fact = fold(grouped(self.rows), max_values=2, tracked=("v09", "v07"))
        assert [v.value_key for v in fact.values] == ["v00", "v01", "v07", "v09"]

    def test_a_tracked_value_does_not_use_a_slot_of_the_cut(self) -> None:
        fact = fold(grouped(self.rows), max_values=2, tracked=("v00",))
        assert [v.value_key for v in fact.values] == ["v00", "v01", "v02"]

    def test_the_tail_is_summed_into_untracked_only_when_the_search_was_whole(self) -> None:
        whole = fold(grouped(self.rows), max_values=3)
        assert whole.untracked_events == sum(100 - i for i in range(3, 10))
        assert whole.values_measurement is Meas.EXACT
        cut = fold(grouped(self.rows, L), max_values=3)
        assert cut.untracked_events is None
        assert cut.values_measurement is Meas.LOWER_BOUND
        assert [v.value_key for v in cut.values] == ["v00", "v01", "v02"]

    def test_nothing_is_cut_when_everything_fits(self) -> None:
        fact = fold(grouped(self.rows), max_values=50)
        assert len(fact.values) == 10 and fact.untracked_events == 0

    def test_kept_plus_untracked_is_parsed_when_whole(self) -> None:
        fact = fold(grouped(self.rows), max_values=3)
        assert sum(v.events for v in fact.values) + (fact.untracked_events or 0) == (
            fact.parsed_events
        )


class TestMeasurement:
    def test_a_whole_search_is_exact(self) -> None:
        fact = fold(grouped([vrow("a")]))
        assert fact.values_measurement is Meas.EXACT
        assert fact.pinned_measurement is PinM.NOT_REQUESTED

    @pytest.mark.parametrize("state", [F, N])
    def test_an_unmeasured_search_folds_to_not_collected_never_zero(
        self, state: PwCellState
    ) -> None:
        fact = fold(grouped([], state))
        assert fact.values_measurement is Meas.NOT_COLLECTED
        assert fact.parsed_events is None and fact.untracked_events is None
        assert fact.values == () and fact.unparsed_events is None
        assert reading_for(fact, pinned=False, tracked_at_bucket=True, metric_events=None).kind is (
            PwReadingKind.NO_BUCKET
        )

    def test_a_whole_search_without_rows_is_a_measured_zero_of_parsed_events(self) -> None:
        fact = fold(grouped([]), totals(40))
        assert fact.parsed_events == 0 and fact.unparsed_events == 40
        reading = reading_for(fact, pinned=False, tracked_at_bucket=True, metric_events=None)
        assert reading.is_measured_zero and reading.measurement is WafMeasurement.EXACT

    def test_a_cut_search_does_not_prove_an_absent_value_zero(self) -> None:
        fact = fold(grouped([vrow("a")], L))
        reading = reading_for(fact, pinned=False, tracked_at_bucket=True, metric_events=None)
        assert reading.kind is PwReadingKind.UNKNOWN

    def test_a_forced_cut_with_no_rows_is_still_a_lower_bound_of_zero(self) -> None:
        fact = fold(grouped([], L))
        assert fact.values_measurement is Meas.LOWER_BOUND and fact.parsed_events == 0

    def test_chunk_sums_are_exact_only_when_every_chunk_was_whole(self) -> None:
        whole = combine_chunks([[grouped([vrow("a", events=3)])], [grouped([vrow("a", events=4)])]])
        fact = fold(whole[0])
        assert by_key(fact) == {"a": 7} and fact.values_measurement is Meas.EXACT
        cut = combine_chunks(
            [[grouped([vrow("a", events=3)])], [grouped([vrow("a", events=4)], L)]]
        )
        fact = fold(cut[0])
        assert by_key(fact) == {"a": 7} and fact.values_measurement is Meas.LOWER_BOUND
        assert fact.untracked_events is None
        gone = combine_chunks([[grouped([vrow("a", events=3)])], [grouped([], F)]])
        fact = fold(gone[0])
        assert fact.values_measurement is Meas.NOT_COLLECTED and fact.values == ()


class TestPins:
    def test_a_pinned_value_is_always_kept_even_below_the_cut(self) -> None:
        rows = [vrow(f"v{i}", events=100 - i) for i in range(6)]
        fact = fold(
            grouped(rows), max_values=2, pinned=[("v5",)], pinned_outcome=pinned_cell([])
        )
        assert [v.value_key for v in fact.values] == ["v0", "v1", "v5"]

    def test_a_pin_is_cut_the_way_the_rows_are(self) -> None:
        pin = "x" * 200
        keys = pinned_value_keys([(pin, pin)], 2, component_limit(2, 255))
        assert list(keys) == ["x" * 127 + TUPLE_SEPARATOR + "x" * 127]

    def test_a_pinned_outcome_is_required_exactly_when_values_are_pinned(self) -> None:
        with pytest.raises(PwFoldError, match="exactly when"):
            fold(grouped([]), pinned=[("a",)])
        with pytest.raises(PwFoldError, match="exactly when"):
            fold(grouped([]), pinned_outcome=pinned_cell([]))

    def test_a_whole_pinned_search_that_omits_a_pin_is_that_pins_measured_zero(self) -> None:
        fact = fold(
            grouped([vrow("a")], L),
            pinned=[("a",), ("rare",)],
            pinned_outcome=pinned_cell([vrow("a", events=9)]),
        )
        assert fact.pinned_measurement is PinM.EXACT
        assert fact.values_measurement is Meas.LOWER_BOUND
        rare = reading_for(fact, pinned=True, tracked_at_bucket=True, metric_events=None)
        assert rare.is_measured_zero and rare.measurement is WafMeasurement.EXACT
        # The same absence of an unpinned value stays unknown.
        other = reading_for(fact, pinned=False, tracked_at_bucket=True, metric_events=None)
        assert other.kind is PwReadingKind.UNKNOWN

    def test_the_pinned_count_fills_in_where_the_grouped_search_was_cut(self) -> None:
        fact = fold(
            grouped([vrow("big", events=500)], L),
            pinned=[("small",)],
            pinned_outcome=pinned_cell([vrow("small", events=2)]),
        )
        assert by_key(fact) == {"big": 500, "small": 2}
        assert fact.parsed_events == 502  # raised to cover the pinned count, still a bound

    def test_the_larger_of_the_two_counts_wins_when_the_grouped_one_is_cut(self) -> None:
        fact = fold(
            grouped([vrow("a", events=4)], L),
            pinned=[("a",)],
            pinned_outcome=pinned_cell([vrow("a", events=9)]),
        )
        assert by_key(fact) == {"a": 9}

    def test_the_pinned_count_never_lowers_what_the_grouped_search_saw(self) -> None:
        fact = fold(
            grouped([vrow("a", events=9)], L),
            pinned=[("a",)],
            pinned_outcome=pinned_cell([vrow("a", events=4)]),
        )
        assert by_key(fact) == {"a": 9}

    def test_a_pinned_value_survives_a_failed_grouped_search(self) -> None:
        fact = fold(
            grouped([], F),
            pinned=[("a",), ("b",)],
            pinned_outcome=pinned_cell([vrow("a", events=9)]),
        )
        assert fact.values_measurement is Meas.NOT_COLLECTED
        assert fact.pinned_measurement is PinM.EXACT
        assert by_key(fact) == {"a": 9} and fact.parsed_events is None
        a = reading_for(fact, pinned=True, tracked_at_bucket=True, metric_events=9)
        b = reading_for(fact, pinned=True, tracked_at_bucket=True, metric_events=None)
        assert a.kind is PwReadingKind.COUNT and b.is_measured_zero
        # An unpinned tracked value of that bucket is not collected: never a zero.
        c = reading_for(fact, pinned=False, tracked_at_bucket=True, metric_events=None)
        assert c.kind is PwReadingKind.NO_BUCKET

    @pytest.mark.parametrize("state", [F, N])
    def test_an_unmeasured_pinned_search_proves_nothing(self, state: PwCellState) -> None:
        fact = fold(
            grouped([vrow("a")], L),
            pinned=[("rare",)],
            pinned_outcome=pinned_cell([], state),
        )
        assert fact.pinned_measurement is PinM.NOT_COLLECTED
        rare = reading_for(fact, pinned=True, tracked_at_bucket=True, metric_events=None)
        assert rare.kind is PwReadingKind.UNKNOWN

    def test_a_cut_pinned_search_is_a_lower_bound(self) -> None:
        fact = fold(
            grouped([], N),
            pinned=[("a",)],
            pinned_outcome=pinned_cell([vrow("a", events=9)], L),
        )
        assert fact.pinned_measurement is PinM.LOWER_BOUND and by_key(fact) == {"a": 9}

    def test_a_combination_keeps_only_the_pinned_tuples(self) -> None:
        # `IN x IN` also returns the cross terms; only the pinned tuples are values.
        cell = pinned_cell(
            [
                vrow("alice", "login", events=5),
                vrow("alice", "logout", events=6),
                vrow("bob", "login", events=7),
                vrow("bob", "logout", events=8),
            ]
        )
        fact = fold(
            grouped([], N),
            props=2,
            pinned=[("alice", "login"), ("bob", "logout")],
            pinned_outcome=cell,
        )
        assert by_key(fact) == {
            "alice" + TUPLE_SEPARATOR + "login": 5,
            "bob" + TUPLE_SEPARATOR + "logout": 8,
        }

    def test_a_whole_grouped_search_stays_the_authority_for_counts(self) -> None:
        fact = fold(
            grouped([vrow("a", events=4)]),
            pinned=[("a",)],
            pinned_outcome=pinned_cell([vrow("a", events=99)]),
        )
        assert by_key(fact) == {"a": 4}


class TestCoverage:
    def test_the_remainder_is_never_a_value_row_and_never_a_zero(self) -> None:
        fact = fold(
            grouped([vrow("a", events=60), vrow(None, events=5), vrow("", events=15)]),
            totals(100),
        )
        assert by_key(fact) == {"a": 60}
        assert fact.parsed_events == 60 and fact.unparsed_events == 40
        assert all(v.value_key.strip() for v in fact.values)
        assert fact.unparsed_bound is PwRemainderBound.EXACT
        assert fact.coverage.ratio == pytest.approx(0.6) and not fact.coverage.lower_bound

    def test_a_lower_bound_parsed_over_states_the_remainder_only(self) -> None:
        fact = fold(grouped([vrow("a", events=60)], L), totals(100))
        assert fact.unparsed_events == 40
        assert fact.unparsed_bound is PwRemainderBound.UPPER_BOUND
        assert fact.coverage.lower_bound and fact.coverage.ratio == pytest.approx(0.6)
        assert fact.coverage.reaches(0.5) and not fact.coverage.reaches(0.9)

    def test_a_property_that_parsed_on_no_event_is_not_applicable_but_a_silent_scope_is_not(
        self,
    ) -> None:
        broken = fold(grouped([]), totals(50))
        assert broken.property_not_parsed
        silent = fold(grouped([]), totals(0))
        assert silent.total_events == 0 and not silent.property_not_parsed
        assert silent.coverage.ratio is None  # undefined, not a zero

    def test_totals_with_no_row_from_a_whole_search_is_a_measured_zero(self) -> None:
        fact = fold(grouped([]), totals(None))
        assert fact.totals_complete and fact.total_events == 0

    @pytest.mark.parametrize(
        "cell",
        [totals(None, F), totals(None, N), totals(40, L), totals(None, L)],
    )
    def test_totals_that_were_not_whole_leave_the_total_unknown_never_zero(
        self, cell: PwBucketOutcome
    ) -> None:
        fact = fold(grouped([vrow("a", events=5)]), cell)
        assert not fact.totals_complete and fact.total_events is None
        assert fact.parsed_events == 5 and fact.unparsed_events is None
        assert fact.coverage.ratio is None and fact.unparsed_bound is None

    @pytest.mark.parametrize("cell", [totals(None, F), totals(None, N), totals(None, L)])
    def test_an_empty_result_that_was_not_whole_is_not_a_zero_total(
        self, cell: PwBucketOutcome
    ) -> None:
        fact = fold(grouped([]), cell)
        assert not fact.totals_complete and fact.total_events is None
        assert fact.unparsed_events is None and not fact.property_not_parsed

    def test_searches_that_disagree_leave_coverage_unknown(self) -> None:
        fact = fold(grouped([vrow("a", events=50)]), totals(40))
        assert not fact.totals_complete and fact.total_events is None
        assert fact.parsed_events == 50 and fact.coverage.ratio is None
