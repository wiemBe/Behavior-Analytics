"""Per-source floors of a console-wide multi-source scope (HISTORY-BACKFILL §B.3.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.history.progress_codec import ProgressCodecError
from app.history.source_floors import SourceFloorError, SourceFloors, finish_with_sources
from app.history.state import HistoryTrigger

BUCKET = timedelta(minutes=5)
MARGIN = timedelta(hours=24)
T0 = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
READ_START = T0 - MARGIN
KW = {"target_start": T0, "margin": MARGIN}


def observed(**first: datetime) -> SourceFloors:
    return SourceFloors().observe(list(first.items()), read_start=READ_START, bucket_seconds=300)


def test_an_event_in_the_first_margin_bucket_gives_the_floor_t0() -> None:
    floors = observed(a=READ_START)
    assert floors.floor("a", **KW) == T0


def test_an_event_in_the_margin_floors_at_t0_plus_its_offset() -> None:
    floors = observed(a=READ_START + timedelta(hours=3))
    assert floors.floor("a", **KW) == T0 + timedelta(hours=3)


def test_a_source_without_an_event_has_no_floor() -> None:
    assert observed(a=READ_START).floor("b", **KW) is None


def test_the_floor_never_lies_before_t0() -> None:
    # an event inside the window itself: first + m is after T0 anyway
    floors = observed(a=T0 + timedelta(days=1))
    assert floors.floor("a", **KW) == T0 + timedelta(days=1) + MARGIN


def test_writable_is_true_only_at_or_after_the_floor() -> None:
    floors = observed(a=READ_START + timedelta(hours=1))
    floor = floors.floor("a", **KW)
    assert floor == T0 + timedelta(hours=1)
    assert not floors.writable("a", floor - BUCKET, **KW)
    assert floors.writable("a", floor, **KW)
    assert floors.writable("a", floor + BUCKET, **KW)
    assert not floors.writable("silent", floor + BUCKET, **KW)


def test_observe_keeps_the_earliest_bucket_of_each_source() -> None:
    floors = SourceFloors().observe(
        [("a", T0), ("a", T0 - timedelta(hours=2)), ("b", T0)],
        read_start=READ_START,
        bucket_seconds=300,
    )
    assert floors.first_buckets == {"a": T0 - timedelta(hours=2), "b": T0}
    again = floors.observe([("a", T0 - timedelta(hours=1))], read_start=READ_START,
                           bucket_seconds=300)
    assert again.first_buckets["a"] == T0 - timedelta(hours=2)
    assert floors.first_buckets["a"] == T0 - timedelta(hours=2)  # the original is not mutated


def test_a_row_before_the_read_start_raises() -> None:
    with pytest.raises(SourceFloorError, match="before the start"):
        observed(a=READ_START - BUCKET)


def test_a_misaligned_row_raises() -> None:
    with pytest.raises(SourceFloorError, match="aligned"):
        observed(a=READ_START + timedelta(seconds=1))
    with pytest.raises(SourceFloorError, match="aligned"):
        observed(a=READ_START + timedelta(minutes=2))


def test_a_naive_or_empty_input_raises() -> None:
    with pytest.raises(SourceFloorError):
        observed(a=datetime(2026, 8, 31, 0, 0))
    with pytest.raises(SourceFloorError):
        SourceFloors().observe([("", T0)], read_start=READ_START, bucket_seconds=300)
    with pytest.raises(SourceFloorError):
        SourceFloors().observe([], read_start=datetime(2026, 8, 31), bucket_seconds=300)


def test_the_mapping_is_frozen() -> None:
    floors = observed(a=T0)
    with pytest.raises(TypeError):
        floors.first_buckets["b"] = T0  # type: ignore[index]


def test_cut_sources_are_those_whose_floor_is_later_than_t0_plus_a_bucket() -> None:
    floors = observed(
        whole=READ_START,
        within_one_bucket=READ_START + BUCKET,
        cut=READ_START + BUCKET * 2,
        late=T0 + timedelta(hours=5),
    )
    assert floors.cut_sources(**KW, bucket=BUCKET) == {"cut", "late"}


def test_silent_sources_are_the_known_ones_without_an_event() -> None:
    floors = observed(a=T0)
    assert floors.silent_sources(["a", "b", "c"]) == {"b", "c"}
    assert floors.silent_sources([]) == frozenset()


def test_encode_decode_round_trip() -> None:
    floors = observed(a=READ_START, b=T0 + timedelta(hours=1))
    adapter = floors.encode()
    assert adapter == {"floors": {"a": "2026-08-31T00:00:00+00:00",
                                  "b": "2026-09-01T01:00:00+00:00"}}
    assert SourceFloors.decode(adapter) == floors
    assert SourceFloors.decode({**adapter, "other": 1}) == floors  # other keys are the family's
    assert SourceFloors.decode(SourceFloors().encode()) == SourceFloors()


@pytest.mark.parametrize(
    "adapter",
    [
        None,
        [],
        {},
        {"floors": []},
        {"floors": None},
        {"floors": {"": "2026-09-01T00:00:00+00:00"}},
        {"floors": {"a": "2026-09-01T00:00:00"}},  # naive
        {"floors": {"a": "not a time"}},
        {"floors": {"a": 5}},
        {"floors": {"a": None}},
    ],
)
def test_decode_refuses_malformed_input(adapter: object) -> None:
    with pytest.raises(ProgressCodecError):
        SourceFloors.decode(adapter)


def test_a_naive_floor_is_refused_at_construction() -> None:
    with pytest.raises(SourceFloorError):
        SourceFloors({"a": datetime(2026, 9, 1)})


def finish(floors: SourceFloors, known: list[str], trigger=HistoryTrigger.FINISHED_COMPLETE):  # type: ignore[no-untyped-def]
    return finish_with_sources(trigger, "reached the end", floors, known=known, **KW,
                               bucket=BUCKET)


def test_finishing_with_every_source_whole_stays_complete() -> None:
    floors = observed(a=READ_START, b=READ_START + BUCKET)
    assert finish(floors, ["a", "b"]) == (HistoryTrigger.FINISHED_COMPLETE, "reached the end")


def test_one_cut_source_makes_the_scope_partial_with_counts_only() -> None:
    floors = observed(a=READ_START, secret_name=READ_START + timedelta(hours=2))
    trigger, reason = finish(floors, ["a", "secret_name"])
    assert trigger is HistoryTrigger.FINISHED_PARTIAL
    assert reason == "QRadar holds less than asked for 1 of 2 sources"
    assert "secret_name" not in reason


def test_one_silent_source_makes_the_scope_partial() -> None:
    floors = observed(a=READ_START)
    trigger, reason = finish(floors, ["a", "quiet"])
    assert trigger is HistoryTrigger.FINISHED_PARTIAL
    assert reason == "1 source had no event in the window"


def test_cut_and_silent_counts_are_both_in_the_reason() -> None:
    floors = observed(
        **{f"s{i}": READ_START + timedelta(hours=3) for i in range(3)},
        **{f"w{i}": READ_START for i in range(115)},
    )
    known = [*floors.first_buckets, "x", "y"]
    trigger, reason = finish(floors, known)
    assert trigger is HistoryTrigger.FINISHED_PARTIAL
    assert reason == (
        "QRadar holds less than asked for 3 of 120 sources; 2 sources had no event in the window"
    )


def test_a_source_outside_the_known_set_does_not_count() -> None:
    floors = observed(a=READ_START, unmonitored=READ_START + timedelta(hours=9))
    assert finish(floors, ["a"])[0] is HistoryTrigger.FINISHED_COMPLETE


@pytest.mark.parametrize(
    "trigger",
    [t for t in HistoryTrigger if t is not HistoryTrigger.FINISHED_COMPLETE],
)
def test_other_triggers_pass_through_unchanged(trigger: HistoryTrigger) -> None:
    floors = observed(a=READ_START + timedelta(hours=9))
    assert finish(floors, ["a", "b"], trigger) == (trigger, "reached the end")
