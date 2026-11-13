"""The identity fold: order, the admission floor, and fail-closed measurements.

Raw usernames that the allow-list refuses must be absent from every output
field, not only from the stored list. N, the floors and the catalog version are
pinned to the design values, not read back from the implementation.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from app.identity import fold as fold_module
from app.identity.fold import (
    CountRow,
    CountSearch,
    IdentityFold,
    NameRow,
    NameSearch,
    SeriesKey,
    fold_tick,
)
from app.models import HYPERTABLES, Base
from app.models.enums import (
    IDENTITY_ANOMALY_RETENTION_DAYS,
    IDENTITY_CATALOG_VERSION,
    IDENTITY_COUNT_RETENTION_DAYS,
    IDENTITY_EVENT_CATALOG,
    IDENTITY_RARE_NAME_FLOOR,
    IDENTITY_TOP_N,
    IDENTITY_USERNAME_MAX_LENGTH,
    IDENTITY_VOLUME_NAME_FLOOR,
    AnomalyType,
    IdentityAnomalyType,
    IdentityEventKind,
    IdentityMeasurement,
)
from app.models.identity_detection import IdentityEventCount, IdentityTopUsername

SOURCE = UUID("00000000-0000-0000-0000-0000000000aa")
OTHER = UUID("00000000-0000-0000-0000-0000000000bb")
WHEN = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 13, 12, 5, tzinfo=UTC)


def _key(event_id: int, source: UUID = SOURCE, at: datetime = WHEN) -> SeriesKey:
    return SeriesKey(source, event_id, at)


def _count(event_id: int, count: int, source: UUID = SOURCE, at: datetime = WHEN) -> CountRow:
    return CountRow(source, event_id, at, count)


def _name(
    event_id: int,
    username: object,
    count: int,
    source: UUID = SOURCE,
    at: datetime = WHEN,
) -> NameRow:
    return NameRow(source, event_id, at, username, count)


def _counts(*rows: CountRow, truncated: bool = False) -> CountSearch:
    return CountSearch(True, truncated, rows)


def _quiet() -> CountSearch:
    return CountSearch(False, False, ())


def _name_search(*rows: NameRow, truncated: bool = False, ran: bool = True) -> NameSearch:
    return NameSearch(ran, truncated, rows)


def _fold(
    scope: tuple[SeriesKey, ...],
    counts: CountSearch | None = None,
    rare: NameSearch | None = None,
    volume: NameSearch | None = None,
) -> IdentityFold:
    return fold_tick(
        scope,
        counts if counts is not None else _quiet(),
        rare if rare is not None else NameSearch(False, False, ()),
        volume if volume is not None else NameSearch(False, False, ()),
    )


def _texts(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        found: list[str] = []
        for key, item in value.items():
            found.extend(_texts(key))
            found.extend(_texts(item))
        return found
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _texts(item)]
    if hasattr(value, "__dataclass_fields__"):
        return [text for item in fields(value) for text in _texts(getattr(value, item.name))]
    return []


def _assert_absent(result: object, secret: str) -> None:
    assert secret not in repr(result)
    assert all(secret not in text for text in _texts(result))


def _check(model: type, name: str) -> str:
    return str(
        next(
            item.sqltext
            for item in model.__table__.constraints
            if item.name is not None and item.name.endswith(name)
        )
    )


def test_catalog_version_floors_and_retention_are_the_design_values() -> None:
    assert IDENTITY_CATALOG_VERSION == 1
    assert IDENTITY_TOP_N == 20
    assert IDENTITY_USERNAME_MAX_LENGTH == 255
    assert IDENTITY_VOLUME_NAME_FLOOR == 3
    assert IDENTITY_RARE_NAME_FLOOR == 1
    assert IDENTITY_COUNT_RETENTION_DAYS == 35
    assert IDENTITY_ANOMALY_RETENTION_DAYS == 180
    assert IDENTITY_COUNT_RETENTION_DAYS > 28
    assert [(event_id, kind.value) for event_id, kind in IDENTITY_EVENT_CATALOG] == [
        (4625, "VOLUME"),
        (4740, "VOLUME"),
        (4720, "RARE"),
        (4726, "RARE"),
        (4728, "RARE"),
        (4732, "RARE"),
        (4756, "RARE"),
        (1102, "RARE"),
    ]
    assert [member.value for member in IdentityEventKind] == ["RARE", "VOLUME"]


def test_identity_taxonomy_is_not_an_extension_of_anomaly_type() -> None:
    assert [member.value for member in AnomalyType] == [
        "NO_EVENTS", "VOLUME_DROP", "VOLUME_SPIKE", "PARSING_DEGRADATION",
        "UNKNOWN_EVENT_SPIKE", "TIMESTAMP_DELAY", "CARDINALITY_DROP", "COLLECTION_ERROR",
        "REPEATED_PAYLOAD",
    ]
    identity = {member.value for member in IdentityAnomalyType}
    assert not identity & {member.value for member in AnomalyType}
    assert all(member.value.startswith("IDENTITY_") for member in IdentityAnomalyType)


def test_tables_are_registered_and_the_time_series_are_hypertables() -> None:
    tables = set(Base.metadata.tables)
    assert {
        "identity_source_profile",
        "identity_bucket",
        "identity_event_count",
        "identity_top_username",
    } <= tables
    assert HYPERTABLES["identity_bucket"] == "bucket_start"
    assert HYPERTABLES["identity_event_count"] == "bucket_start"
    assert HYPERTABLES["identity_top_username"] == "bucket_start"


def test_stored_name_checks_encode_the_catalog_and_the_volume_floor() -> None:
    floor = _check(IdentityTopUsername, "name_admission_floor")
    assert "event_count >= 3" in floor
    assert "4625" in floor and "4740" in floor
    assert "event_id IN (4625, 4740, 4720, 4726, 4728, 4732, 4756, 1102)" in _check(
        IdentityTopUsername, "event_id_known"
    )
    assert "rank BETWEEN 1 AND 20" in _check(IdentityTopUsername, "rank_in_top_n")
    honest = _check(IdentityEventCount, "count_honest")
    assert "NOT_COLLECTED" in honest and "event_count IS NULL" in honest


def test_names_order_by_count_descending_then_username_ascending() -> None:
    result = _fold(
        (_key(1102),),
        _counts(_count(1102, 20)),
        _name_search(
            _name(1102, "cara", 4),
            _name(1102, "bob", 4),
            _name(1102, "ann", 4),
            _name(1102, "zed", 9),
        ),
    )
    series = result.get(SOURCE, 1102, WHEN)
    assert [(item.rank, item.username, item.count) for item in series.usernames] == [
        (1, "zed", 9),
        (2, "ann", 4),
        (3, "bob", 4),
        (4, "cara", 4),
    ]


def test_top_n_discards_the_rest_and_the_discarded_name_is_gone() -> None:
    rows = tuple(_name(1102, f"u{index:02d}", 1) for index in range(21))
    result = _fold((_key(1102),), _counts(_count(1102, 21)), _name_search(*rows))
    series = result.get(SOURCE, 1102, WHEN)
    assert [item.username for item in series.usernames] == [f"u{index:02d}" for index in range(20)]
    assert [item.rank for item in series.usernames] == list(range(1, 21))
    _assert_absent(result, "u20")


def test_rare_name_is_kept_at_one_event() -> None:
    result = _fold(
        (_key(1102),),
        _counts(_count(1102, 1)),
        _name_search(_name(1102, "auditor", 1)),
    )
    series = result.get(SOURCE, 1102, WHEN)
    assert [(item.username, item.count) for item in series.usernames] == [("auditor", 1)]


def test_volume_name_is_dropped_at_two_and_kept_at_three() -> None:
    result = _fold(
        (_key(4625), _key(4740)),
        _counts(_count(4625, 5), _count(4740, 3)),
        volume=_name_search(
            _name(4625, "one-off", 2),
            _name(4625, "repeated", 3),
            _name(4740, "locked", 3),
            _name(4740, "almost", 2),
        ),
    )
    failed = result.get(SOURCE, 4625, WHEN)
    lockout = result.get(SOURCE, 4740, WHEN)
    assert [(item.username, item.count) for item in failed.usernames] == [("repeated", 3)]
    assert [(item.username, item.count) for item in lockout.usernames] == [("locked", 3)]
    _assert_absent(result, "one-off")
    _assert_absent(result, "almost")


def test_split_rows_sum_before_the_volume_floor() -> None:
    """Two chunks of two events are one username of four, so the floor keeps it."""
    result = _fold(
        (_key(4625),),
        _counts(_count(4625, 4)),
        volume=_name_search(_name(4625, "split", 2), _name(4625, "split", 2)),
    )
    series = result.get(SOURCE, 4625, WHEN)
    assert [(item.username, item.count) for item in series.usernames] == [("split", 4)]


def test_username_is_cut_at_255_and_flagged() -> None:
    full = "A" * 300
    result = _fold(
        (_key(4720),),
        _counts(_count(4720, 1)),
        _name_search(_name(4720, full, 1)),
    )
    stored = result.get(SOURCE, 4720, WHEN).usernames[0]
    assert stored.username == "A" * 255
    assert stored.truncated is True
    _assert_absent(result, full)


def test_not_collected_is_not_a_zero_and_a_whole_miss_is() -> None:
    absent = _fold(
        (_key(4625),),
        _quiet(),
        volume=NameSearch(False, False, (_name(4625, "leaked-from-unrun", 9),)),
    )
    series = absent.get(SOURCE, 4625, WHEN)
    assert series.measurement is IdentityMeasurement.NOT_COLLECTED
    assert series.count is None
    assert series.count != 0
    assert absent.bucket(SOURCE, WHEN).population is None
    assert absent.bucket(SOURCE, WHEN).measurement is IdentityMeasurement.NOT_COLLECTED
    _assert_absent(absent, "leaked-from-unrun")

    whole = _fold((_key(4625), _key(1102)), _counts(_count(4625, 4)))
    assert whole.get(SOURCE, 4625, WHEN).count == 4
    assert whole.get(SOURCE, 1102, WHEN).count == 0
    assert whole.get(SOURCE, 1102, WHEN).measurement is IdentityMeasurement.EXACT
    assert whole.bucket(SOURCE, WHEN).population == 4


def test_truncated_counts_search_writes_no_healthy_bucket() -> None:
    result = _fold(
        (_key(4625), _key(1102)),
        _counts(_count(4625, 10), truncated=True),
        _name_search(_name(1102, "auditor", 1)),
    )
    bucket = result.bucket(SOURCE, WHEN)
    assert bucket.measurement is IdentityMeasurement.TRUNCATED
    assert bucket.measurement is not IdentityMeasurement.EXACT
    seen = result.get(SOURCE, 4625, WHEN)
    missing = result.get(SOURCE, 1102, WHEN)
    assert seen.measurement is IdentityMeasurement.TRUNCATED
    assert seen.count == 10
    assert missing.measurement is IdentityMeasurement.TRUNCATED
    assert missing.count is None
    assert missing.count != 0
    assert all(item.measurement is not IdentityMeasurement.EXACT for item in bucket.series)


def test_truncated_name_search_sets_admission_truncated_only_for_that_search() -> None:
    result = _fold(
        (_key(1102), _key(4625)),
        _counts(_count(1102, 2), _count(4625, 3)),
        _name_search(_name(1102, "auditor", 1), _name(1102, "below", 0), truncated=True),
        _name_search(_name(4625, "repeated", 3)),
    )
    rare = result.get(SOURCE, 1102, WHEN)
    volume = result.get(SOURCE, 4625, WHEN)
    assert rare.admission_truncated is True
    assert rare.names_measurement is IdentityMeasurement.TRUNCATED
    assert [(item.username, item.count) for item in rare.usernames] == [("auditor", 1)]
    assert volume.admission_truncated is False
    assert volume.names_measurement is IdentityMeasurement.EXACT
    assert result.bucket(SOURCE, WHEN).admission_truncated is True
    _assert_absent(result, "below")


def test_catalog_version_is_stamped_on_the_fold() -> None:
    result = _fold((_key(4726), _key(4726, OTHER, LATER)), _counts(_count(4726, 1)))
    assert result.catalog_version == 1
    assert result.bucket(SOURCE, WHEN).catalog_version == 1
    assert result.get(SOURCE, 4726, WHEN).catalog_version == 1
    assert result.get(OTHER, 4726, LATER).catalog_version == 1


def test_a_name_outside_the_allow_list_shape_never_appears() -> None:
    class _Bad:
        def __repr__(self) -> str:
            return "LEAKED-NAME"

    result = _fold(
        (_key(4625), _key(1102), _key(4624), _key(4720)),
        _counts(_count(4625, 9), _count(1102, 1), _count(4624, 50), _count(4720, 1)),
        _name_search(
            _name(1102, "   ", 4),
            _name(1102, "auditor", 1),
            _name(4720, _Bad(), 4),
            _name(4720, "has\x00nul", 4),
        ),
        _name_search(_name(4625, "one-off", 2), _name(4624, "success-logon", 9)),
    )
    assert [item.username for item in result.get(SOURCE, 1102, WHEN).usernames] == ["auditor"]
    assert result.get(SOURCE, 4720, WHEN).usernames == ()
    assert result.get(SOURCE, 4625, WHEN).usernames == ()
    with pytest.raises(KeyError):
        result.get(SOURCE, 4624, WHEN)
    for secret in ("one-off", "success-logon", "LEAKED-NAME", "has\x00nul"):
        _assert_absent(result, secret)


def test_an_unrun_name_search_is_not_an_empty_list() -> None:
    result = _fold((_key(1102),), _counts(_count(1102, 1)))
    series = result.get(SOURCE, 1102, WHEN)
    assert series.usernames == ()
    assert series.names_measurement is IdentityMeasurement.NOT_COLLECTED
    assert series.admission_truncated is False


def test_fold_does_no_io() -> None:
    source = fold_module.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")
    assert "sqlalchemy" not in text
    assert "datetime.now" not in text
    assert "open(" not in text
