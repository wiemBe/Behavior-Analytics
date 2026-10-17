"""The pure fold from a bucket's keyed rows to a `PwBucketFact` (design §G.5, §G.6).

Raw values (usernames, actions, any property text) live only inside these functions.
What leaves them is `PwBucketFact`: counts, and the bounded value list the design allows
-- the values already tracked, the pinned ones, and the top `max_values` of the rest.
Everything else is summed into `untracked_events` and its text is gone (AGENTS.md rule 3).

The tracked and pinned sets are **inputs**: which values are tracked is decided by
admission (§G.7.3, PW-2b). The admission columns of the fact keep their defaults here.

A bucket whose search did not measure it folds to `NOT_COLLECTED` with no counts, never to
a zero. A zero is only ever made by a `COMPLETE` search that did not mention a key
(`facts.count_from_search`, the one place that rule lives).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.models.enums import PwBucketMeasurement, PwPinnedMeasurement
from app.property_watch.facts import (
    PwBucketFact,
    PwCountState,
    PwSearchStatus,
    PwValueFact,
    count_from_search,
    is_unparsed,
    join_tuple_key,
)
from app.property_watch.keyed_search import PwBucketOutcome, PwCellState
from app.providers.dto import PwKeyedRow, PwSearchKind

#: Stands in for a character that cannot be stored or shown: a control character, a lone
#: surrogate, a separator other than the plain space.
REPLACEMENT = "�"
#: The stored width of a `value_key` (`pw_value.value_key`).
VALUE_KEY_WIDTH = 255


class PwFoldError(ValueError):
    """The fold was handed something the collection contract rules out: a bug, not data."""


def component_limit(property_count: int, max_length: int) -> int:
    """The longest one component may be so that the whole `value_key` fits `max_length`.

    One property: the whole width. Two: the joined key (components and the separator)
    is bounded the same, so each component gets half of what is left (design §G.7.2).
    """
    if property_count not in (1, 2):
        raise PwFoldError("a watch compares one or two properties")
    if not 3 <= max_length <= VALUE_KEY_WIDTH:
        raise PwFoldError(f"a value is at most {VALUE_KEY_WIDTH} characters, and at least 3")
    return max_length if property_count == 1 else (max_length - 1) // 2


def sanitize_component(raw: object, limit: int) -> tuple[str, bool] | None:
    """`(text, was_cut)` of one property value, or None when it is the unparsed remainder.

    Printable, stripped, at most `limit` characters. Never raises, whatever `raw` is: a
    non-printable character becomes U+FFFD, which makes two raw strings that differ only
    there one value, and a lone surrogate or a NUL cannot reach storage. Text that is empty
    or only whitespace, and anything that is not text, is unparsed (`facts.is_unparsed`).
    """
    if is_unparsed(raw) or not isinstance(raw, str):
        return None
    text = "".join(char if char.isprintable() else REPLACEMENT for char in raw.strip())
    cut = len(text) > limit
    if cut:
        text = text[:limit].rstrip()
    return text, cut


@dataclass
class _Sum:
    events: int
    components: tuple[str, ...]
    truncated: bool


def _sum_rows(
    rows: Sequence[PwKeyedRow], property_count: int, limit: int
) -> tuple[dict[str, _Sum], int]:
    """The rows added up by value key, and the events of the rows that formed a value.

    A row with any unparsed component forms no value and counts toward no total of parsed
    events: it is the remainder. Rows that sanitize to one key are one value, so the same
    value arriving from two scope chunks adds up here.
    """
    sums: dict[str, _Sum] = {}
    parsed = 0
    for row in rows:
        if len(row.values) != property_count:
            raise PwFoldError("a row carries one value per property")
        cleaned = [sanitize_component(value, limit) for value in row.values]
        if any(item is None for item in cleaned):
            continue
        parts = [item for item in cleaned if item is not None]
        components = tuple(text for text, _ in parts)
        key = join_tuple_key(components)
        entry = sums.setdefault(key, _Sum(0, components, False))
        entry.events += row.events
        entry.truncated = entry.truncated or any(cut for _, cut in parts)
        parsed += row.events
    return sums, parsed


def pinned_value_keys(
    pinned: Sequence[Sequence[str]], property_count: int, limit: int
) -> dict[str, tuple[str, ...]]:
    """The `value_key` of every pinned tuple, cut the way the fold cuts a row's values."""
    out: dict[str, tuple[str, ...]] = {}
    for entry in pinned:
        if len(entry) != property_count:
            raise PwFoldError("a pinned value has one string per property")
        cleaned = [sanitize_component(component, limit) for component in entry]
        if any(item is None for item in cleaned):
            raise PwFoldError("a pinned value cannot be the unparsed remainder")
        components = tuple(item[0] for item in cleaned if item is not None)
        out[join_tuple_key(components)] = components
    return out


def _check(outcome: PwBucketOutcome, search: PwSearchKind, bucket_start: datetime) -> None:
    if outcome.search is not search:
        raise PwFoldError(f"expected a {search.value} outcome, got {outcome.search.value}")
    if outcome.bucket_start != bucket_start:
        raise PwFoldError("an outcome belongs to another bucket")
    for row in outcome.rows:
        if row.bucket_start != bucket_start:
            # The parser and the driver already refuse a key outside the span; this is the
            # fold's own guard, since a row in the wrong bucket is a wrong count.
            raise PwFoldError("a row belongs to another bucket")


def _total(totals: PwBucketOutcome) -> int | None:
    """The bucket's total events, or None unless the totals search was whole.

    The measured-zero rule is the shared one: a whole search that does not mention the
    bucket measured a silent scope. A cut or failed search is unmeasured, never 0.
    """
    key = totals.bucket_start.isoformat()
    counts: dict[str, object] = {}
    if totals.rows:
        counts[key] = sum(row.events for row in totals.rows)
    # The driver already decided the cap: re-express its verdict for the shared rule.
    status = PwSearchStatus.COMPLETED if totals.measured else PwSearchStatus.FAILED
    count = count_from_search(
        counts,
        key,
        status=status,
        row_count=2 if totals.state is PwCellState.LOWER_BOUND else 0,
        cap=1,
    )
    return count.events if count.state is PwCountState.EXACT else None


def fold_bucket(
    *,
    bucket_start: datetime,
    bucket_seconds: int,
    watch_version: int,
    property_count: int,
    grouped: PwBucketOutcome,
    totals: PwBucketOutcome,
    pinned_outcome: PwBucketOutcome | None = None,
    pinned: Sequence[Sequence[str]] = (),
    tracked: Collection[str] = (),
    max_values: int,
    value_max_length: int,
) -> PwBucketFact:
    """The fact of one bucket from its three (chunk-combined) outcomes.

    `tracked` holds the `value_key`s already tracked and `pinned` the pinned tuples;
    both are kept whatever their rank. Of every other value the top `max_values` by
    events, then by key, are kept, and the cut tail is summed into `untracked_events`
    **only when the grouped search was whole**. `pinned_outcome` is required exactly when
    values are pinned.
    """
    if max_values < 1:
        raise PwFoldError("max_values must be positive")
    limit = component_limit(property_count, value_max_length)
    _check(grouped, PwSearchKind.GROUPED, bucket_start)
    _check(totals, PwSearchKind.TOTALS, bucket_start)
    pins = pinned_value_keys(pinned, property_count, limit)
    if bool(pins) != (pinned_outcome is not None):
        raise PwFoldError("a pinned outcome is given exactly when values are pinned")
    if pinned_outcome is not None:
        _check(pinned_outcome, PwSearchKind.PINNED, bucket_start)

    # -- the open-ended search
    sums, parsed_rows = _sum_rows(grouped.rows, property_count, limit)
    must_keep = set(tracked) | set(pins)
    kept = {key: entry for key, entry in sums.items() if key in must_keep}
    rest = sorted(
        ((key, entry) for key, entry in sums.items() if key not in must_keep),
        key=lambda item: (-item[1].events, item[0]),
    )
    for key, entry in rest[:max_values]:
        kept[key] = entry
    tail = sum(entry.events for _, entry in rest[max_values:])
    parsed: int | None = parsed_rows if grouped.measured else None
    untracked: int | None = tail if grouped.state is PwCellState.COMPLETE else None
    if grouped.state is PwCellState.COMPLETE:
        values_measurement = PwBucketMeasurement.EXACT
    elif grouped.state is PwCellState.LOWER_BOUND:
        values_measurement = PwBucketMeasurement.LOWER_BOUND
    else:
        values_measurement = PwBucketMeasurement.NOT_COLLECTED
        kept = {}

    # -- the pinned search: it names the pinned values more completely than a cut grouped
    # search can, so it fills in where the grouped search is not whole. A whole grouped
    # search already lists every pinned value with events, and stays the one authority.
    if pinned_outcome is None:
        pinned_measurement = PwPinnedMeasurement.NOT_REQUESTED
    elif pinned_outcome.state is PwCellState.COMPLETE:
        pinned_measurement = PwPinnedMeasurement.EXACT
    elif pinned_outcome.state is PwCellState.LOWER_BOUND:
        pinned_measurement = PwPinnedMeasurement.LOWER_BOUND
    else:
        pinned_measurement = PwPinnedMeasurement.NOT_COLLECTED
    if (
        pinned_outcome is not None
        and pinned_outcome.measured
        and grouped.state is not PwCellState.COMPLETE
    ):
        pinned_sums, _ = _sum_rows(pinned_outcome.rows, property_count, limit)
        for key, entry in pinned_sums.items():
            if key not in pins:
                continue  # `IN` x `IN` also admits unpinned combinations of the pinned parts
            held = kept.get(key)
            if held is None:
                kept[key] = entry
            else:
                held.events = max(held.events, entry.events)
                held.truncated = held.truncated or entry.truncated
        if parsed is not None:
            parsed = max(parsed, sum(entry.events for entry in kept.values()))

    # -- the denominator
    total = _total(totals)
    totals_complete = total is not None
    if total is not None and parsed is not None and parsed > total:
        # Two searches over one window disagree (an event stored between them): neither
        # number can be trusted as the other's denominator, so coverage is unknown.
        total, totals_complete = None, False

    ordered = sorted(kept.items(), key=lambda item: (-item[1].events, item[0]))
    return PwBucketFact(
        bucket_start=bucket_start,
        bucket_seconds=bucket_seconds,
        watch_version=watch_version,
        totals_complete=totals_complete,
        values_measurement=values_measurement,
        pinned_measurement=pinned_measurement,
        total_events=total,
        parsed_events=parsed,
        untracked_events=untracked,
        values=tuple(
            PwValueFact(
                value_key=key,
                events=entry.events,
                components=entry.components,
                truncated=entry.truncated,
            )
            for key, entry in ordered
        ),
    )
