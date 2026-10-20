"""The progress codec: every field survives, and nothing malformed is guessed."""

from __future__ import annotations

import copy
import dataclasses
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.history.planner import Gap, ScopeProgress
from app.history.progress_codec import (
    PROGRESS_VERSION,
    ProgressCodecError,
    decode_progress,
    encode_progress,
)

T0 = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)


def _full() -> ScopeProgress:
    return ScopeProgress(
        target_start=T0,
        started_at=T0 + timedelta(hours=1, seconds=7),
        floor=T0 + timedelta(hours=2),
        first_event_bucket=T0 + timedelta(hours=3),
        through=T0 + timedelta(hours=4),
        scan_cursor=T0 + timedelta(hours=5),
        scan_failures=2,
        chunk_buckets=144,
        chunk_ceiling=288,
        ceiling_successes=2,
        failures=1,
        searches=17,
        buckets_read=900,
        lower_bound_buckets=4,
        rows_seen=12345,
        non_silent_buckets=800,
        recent_rows_per_bucket=15.43125,
        density_checked=True,
        gaps=(
            Gap(T0 + timedelta(hours=6), T0 + timedelta(hours=7), "search failed"),
            Gap(T0 + timedelta(hours=1), T0 + timedelta(hours=2), "earlier, listed second"),
        ),
    )


def test_a_progress_with_every_field_set_round_trips_through_json() -> None:
    progress = _full()
    adapter = {"floors": {"17": "2026-09-01T00:00:00+00:00"}, "n": [1, 2]}
    for item in dataclasses.fields(ScopeProgress):
        default = item.default if item.default is not dataclasses.MISSING else None
        if item.name not in ("gaps",) and item.default is not dataclasses.MISSING:
            assert getattr(progress, item.name) != default, item.name  # a real test of the field
    stored = json.loads(json.dumps(encode_progress(progress, adapter)))
    decoded = decode_progress(stored)
    assert decoded.progress == progress
    assert decoded.adapter == adapter
    assert decoded.progress.gaps == progress.gaps  # order kept


def test_the_encoded_key_set_is_exactly_the_dataclass_fields() -> None:
    stored = encode_progress(_full())
    assert set(stored) == {"version", "progress", "adapter"}
    assert set(stored["progress"]) == {item.name for item in dataclasses.fields(ScopeProgress)}
    assert stored["version"] == PROGRESS_VERSION


def test_minimal_progress_round_trips_with_nulls() -> None:
    progress = ScopeProgress(target_start=T0, started_at=T0)
    stored = json.loads(json.dumps(encode_progress(progress)))
    assert decode_progress(stored) == (progress, {})
    assert stored["progress"]["ceiling_successes"] == 0


def _version_1(stored: dict) -> dict:
    """The same progress as version 1 encoded it: no `ceiling_successes`."""
    old = copy.deepcopy(stored)
    old["version"] = 1
    old["progress"].pop("ceiling_successes")
    return old


def _version_2(stored: dict) -> dict:
    """The same progress as version 2 encoded it: no `recent_rows_per_bucket`."""
    old = copy.deepcopy(stored)
    old["version"] = 2
    old["progress"].pop("recent_rows_per_bucket")
    return old


def test_version_3_round_trips_the_release_counter_and_the_recent_density() -> None:
    progress = _full()
    assert progress.ceiling_successes == 2
    assert progress.recent_rows_per_bucket == 15.43125
    stored = json.loads(json.dumps(encode_progress(progress)))
    assert stored["version"] == 3 == PROGRESS_VERSION
    decoded = decode_progress(stored).progress
    assert decoded.ceiling_successes == 2
    assert decoded.recent_rows_per_bucket == 15.43125


def test_a_version_1_dict_decodes_with_no_release_count() -> None:
    stored = _version_1(json.loads(json.dumps(encode_progress(_full(), {"a": 1}))))
    decoded = decode_progress(stored)
    assert decoded.progress.ceiling_successes == 0
    assert decoded.progress.recent_rows_per_bucket is None
    assert decoded.adapter == {"a": 1}
    # every other field is the version-3 progress's field
    assert dataclasses.replace(
        decoded.progress, ceiling_successes=2, recent_rows_per_bucket=15.43125
    ) == _full()


def test_a_version_2_dict_decodes_with_no_recent_density() -> None:
    stored = _version_2(json.loads(json.dumps(encode_progress(_full(), {"a": 1}))))
    decoded = decode_progress(stored)
    assert decoded.progress.ceiling_successes == 2
    assert decoded.progress.recent_rows_per_bucket is None
    assert decoded.adapter == {"a": 1}
    assert dataclasses.replace(decoded.progress, recent_rows_per_bucket=15.43125) == _full()


def test_datetimes_are_iso_utc_and_decode_aware() -> None:
    plus_three = timezone(timedelta(hours=3))
    progress = dataclasses.replace(_full(), through=datetime(2026, 9, 1, 12, tzinfo=plus_three))
    stored = encode_progress(progress)
    assert stored["progress"]["through"] == "2026-09-01T09:00:00+00:00"
    decoded = decode_progress(stored).progress
    assert decoded.through == progress.through
    assert decoded.through is not None and decoded.through.utcoffset() == timedelta(0)


def test_the_adapter_is_preserved_untouched() -> None:
    adapter = {"b": {"z": 1, "a": [3, {"k": None}]}, "a": "x"}
    original = copy.deepcopy(adapter)
    stored = encode_progress(_full(), adapter)
    assert stored["adapter"] == original
    assert decode_progress(stored).adapter == original
    assert adapter == original


def test_a_naive_datetime_is_refused_on_encode() -> None:
    naive = dataclasses.replace(_full(), through=datetime(2026, 9, 1, 1))
    with pytest.raises(ProgressCodecError, match="through"):
        encode_progress(naive)
    gap = dataclasses.replace(
        _full(), gaps=(Gap(datetime(2026, 9, 1), T0, "x"),)
    )
    with pytest.raises(ProgressCodecError, match="gap start"):
        encode_progress(gap)


def _stored() -> dict:
    return json.loads(json.dumps(encode_progress(_full(), {"a": 1})))


def _mutations() -> list[tuple[str, object]]:
    cases: list[tuple[str, object]] = []

    def case(label: str, mutate) -> None:  # type: ignore[no-untyped-def]
        stored = _stored()
        mutate(stored)
        cases.append((label, stored))

    case("not an object", lambda s: s.clear())
    case("wrong version", lambda s: s.__setitem__("version", 4))
    case("bool version", lambda s: s.__setitem__("version", True))
    case("missing version", lambda s: s.pop("version"))
    case("missing adapter", lambda s: s.pop("adapter"))
    case("adapter not an object", lambda s: s.__setitem__("adapter", []))
    case("extra top key", lambda s: s.__setitem__("extra", 1))
    case("body not an object", lambda s: s.__setitem__("progress", []))
    case("missing field", lambda s: s["progress"].pop("searches"))
    case("missing version-2 field", lambda s: s["progress"].pop("ceiling_successes"))
    case("missing version-3 field", lambda s: s["progress"].pop("recent_rows_per_bucket"))
    case("unknown field", lambda s: s["progress"].__setitem__("surprise", 1))
    case("negative release count", lambda s: s["progress"].__setitem__("ceiling_successes", -1))
    case("bool release count", lambda s: s["progress"].__setitem__("ceiling_successes", False))
    case(
        "version 1 with a release count",
        lambda s: (
            s.__setitem__("version", 1),
            s["progress"].__setitem__("ceiling_successes", 0),
        ),
    )
    case(
        "version 2 with a recent density",
        lambda s: (
            s.__setitem__("version", 2),
            s["progress"].__setitem__("recent_rows_per_bucket", 1.0),
        ),
    )
    case(
        "string for the density",
        lambda s: s["progress"].__setitem__("recent_rows_per_bucket", "x"),
    )
    case(
        "bool for the density",
        lambda s: s["progress"].__setitem__("recent_rows_per_bucket", True),
    )
    case(
        "not a finite density",
        lambda s: s["progress"].__setitem__("recent_rows_per_bucket", float("inf")),
    )
    case("naive datetime", lambda s: s["progress"].__setitem__("through", "2026-09-01T00:00:00"))
    case("garbage datetime", lambda s: s["progress"].__setitem__("floor", "yesterday"))
    case("datetime not a string", lambda s: s["progress"].__setitem__("floor", 5))
    case("required datetime null", lambda s: s["progress"].__setitem__("target_start", None))
    case("string for an int", lambda s: s["progress"].__setitem__("searches", "3"))
    case("bool for an int", lambda s: s["progress"].__setitem__("searches", True))
    case("float for an int", lambda s: s["progress"].__setitem__("failures", 1.5))
    case("null for a required int", lambda s: s["progress"].__setitem__("searches", None))
    case("int for a bool", lambda s: s["progress"].__setitem__("density_checked", 1))
    case("gaps not a list", lambda s: s["progress"].__setitem__("gaps", {}))
    case("gap not an object", lambda s: s["progress"].__setitem__("gaps", [1]))
    case("gap key missing", lambda s: s["progress"]["gaps"][0].pop("reason"))
    case("gap key extra", lambda s: s["progress"]["gaps"][0].__setitem__("x", 1))
    case("gap reason not a string", lambda s: s["progress"]["gaps"][0].__setitem__("reason", 3))
    case(
        "gap naive start",
        lambda s: s["progress"]["gaps"][0].__setitem__("start", "2026-09-01T00:00"),
    )
    return cases


@pytest.mark.parametrize(("label", "stored"), _mutations(), ids=[c[0] for c in _mutations()])
def test_each_malformed_input_raises(label: str, stored: object) -> None:
    with pytest.raises(ProgressCodecError):
        decode_progress(stored)


def test_a_non_mapping_raises() -> None:
    for bad in (None, [], "x", 3):
        with pytest.raises(ProgressCodecError):
            decode_progress(bad)
