"""Admission, tracking windows and retirement of property-watch values (design §G.7.3).

The fold keeps, per bucket, the values already tracked, the pinned ones and the top
`pw_max_values_per_bucket` of the rest. Admission decides which of the rest become tracked
values -- rows in `pw_value` -- and so which value texts may persist at all (AGENTS.md rule 3):

  * a **pinned** value is tracked from the first collected bucket after the watch pins it,
    is never subject to admission and is never retired, but counts toward the ceiling;
  * an **automatic** value is admitted in a bucket when it has at least
    `pw_min_admission_events` events there, at most `pw_max_new_values_per_bucket` per bucket,
    and only while the active count stays under `pw_max_active_values_per_watch`. The best
    candidates by events, then by key, win; the rest are counted, not stored;
  * a tracked value unobserved for `pw_value_active_days` is retired: `active` goes false and
    `tracked_until` is set, so a lagging backfill cannot read its absence as a zero (WAF
    §29.4). A returning value starts a new tracking period and keeps `first_seen_at`.

The bucket row records every outcome (`admitted_values`, `below_admission_threshold`,
`rejected_over_quota`, `admission_truncated`), so the new-value detector can tell "not new"
from "refused". A kept value that was not admitted is **not** persisted: its events are
summed into `untracked_events` (only when the grouped search was whole) and its text is gone.

Pure. The caller supplies the tracked set and persists the changes it reports.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from app.models.enums import PwBucketMeasurement
from app.property_watch.facts import PwBucketFact, PwValueFact, split_tuple_key


@dataclass(frozen=True)
class PwAdmissionPolicy:
    min_events: int
    max_new_per_bucket: int
    max_active: int
    active_days: int

    def __post_init__(self) -> None:
        if self.min_events < 1 or self.max_active < 1 or self.active_days < 1:
            raise ValueError("admission thresholds are at least 1")
        if self.max_new_per_bucket < 0:
            raise ValueError("the new-value quota is not negative")


@dataclass
class TrackedValue:
    """A working copy of one `pw_value` row.

    `exists` is whether the row is in the database (an update) or new (an insert);
    `dirty` is whether this tick changed it.
    """

    id: uuid.UUID
    value_key: str
    components: tuple[str, ...]
    pinned: bool
    active: bool
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    tracked_since: datetime | None
    tracked_until: datetime | None = None
    retired_at: datetime | None = None
    truncated: bool = False
    exists: bool = True
    dirty: bool = False


def components_of(value_key: str, property_count: int) -> tuple[str, ...]:
    parts = split_tuple_key(value_key)
    return parts if len(parts) == property_count else (value_key,)


def _new_value(
    key: str, components: tuple[str, ...], *, pinned: bool, at: datetime, truncated: bool = False
) -> TrackedValue:
    return TrackedValue(
        id=uuid.uuid4(),
        value_key=key,
        components=components,
        pinned=pinned,
        active=True,
        first_seen_at=at,
        last_seen_at=None,
        tracked_since=at,
        truncated=truncated,
        exists=False,
        dirty=True,
    )


def _start_tracking(value: TrackedValue, at: datetime) -> None:
    """A new tracking period: a returning value keeps `first_seen_at`."""
    value.active = True
    value.tracked_since = at
    value.tracked_until = None
    value.retired_at = None
    value.dirty = True


def sync_pins(
    values: dict[str, TrackedValue],
    pins: Mapping[str, tuple[str, ...]],
    *,
    first_bucket: datetime,
) -> None:
    """Make the watch's pins tracked, and a pin that was removed an ordinary value.

    `pins` maps each pinned `value_key` to its components. A pin with no row gets one,
    tracked from `first_bucket`, the first bucket collected after it was pinned. A retired
    value that is pinned again is reactivated. A value no longer pinned stays tracked but
    loses its protection: ordinary retirement applies to it from now on.
    """
    for key, components in pins.items():
        value = values.get(key)
        if value is None:
            values[key] = _new_value(key, components, pinned=True, at=first_bucket)
            continue
        if not value.pinned:
            value.pinned = True
            value.dirty = True
        if not value.active:
            _start_tracking(value, first_bucket)
    for key, value in values.items():
        if value.pinned and key not in pins:
            value.pinned = False
            value.dirty = True


@dataclass(frozen=True)
class AdmittedBucket:
    """A bucket after admission: the fact to persist and the metric rows to write."""

    fact: PwBucketFact
    #: `value_key` -> events, for every value persisted as a metric row.
    metrics: Mapping[str, int] = field(default_factory=dict)


def admit_bucket(
    fact: PwBucketFact,
    values: dict[str, TrackedValue],
    policy: PwAdmissionPolicy,
    *,
    property_count: int,
) -> AdmittedBucket:
    """Admission for one bucket; updates `values` in place.

    Order matters across the buckets of a tick: call this oldest first, so a value admitted
    in one bucket is already tracked in the next.
    """
    if fact.values_measurement is PwBucketMeasurement.NOT_COLLECTED:
        return AdmittedBucket(fact)
    at = fact.bucket_start
    tracked_now: list[PwValueFact] = []
    candidates: list[PwValueFact] = []
    for item in fact.values:
        value = values.get(item.value_key)
        if value is not None and value.active:
            tracked_now.append(item)
        else:
            candidates.append(item)
    for item in tracked_now:
        value = values[item.value_key]
        if value.last_seen_at is None or at > value.last_seen_at:
            value.last_seen_at = at
            value.dirty = True
        if item.truncated and not value.truncated:
            value.truncated = True
            value.dirty = True
    eligible = [c for c in candidates if c.events >= policy.min_events]
    below = len(candidates) - len(eligible)
    active_count = sum(1 for v in values.values() if v.active)
    room = max(0, policy.max_active - active_count)
    quota = min(policy.max_new_per_bucket, room)
    # `candidates` arrive ordered by events, then key (the fold's order): the best win.
    admitted = eligible[:quota]
    rejected = len(eligible) - len(admitted)
    for item in admitted:
        value = values.get(item.value_key)
        components = item.components or components_of(item.value_key, property_count)
        if value is None:
            value = _new_value(
                item.value_key, components, pinned=False, at=at, truncated=item.truncated
            )
            value.first_seen_at = at
            values[item.value_key] = value
        else:
            _start_tracking(value, at)
            value.truncated = value.truncated or item.truncated
        value.last_seen_at = at
        value.dirty = True
    kept = tracked_now + admitted
    persisted = {item.value_key for item in kept}
    dropped_events = sum(c.events for c in candidates if c.value_key not in persisted)
    untracked = (
        None if fact.untracked_events is None else fact.untracked_events + dropped_events
    )
    # The fold counted every distinct value of the result; whatever it cut is not in
    # `fact.values`, so a larger count than the kept list means the cut removed values.
    cut_by_fold = max(0, fact.observed_values - len(fact.values))
    final = replace(
        fact,
        values=tuple(kept),
        untracked_events=untracked,
        tracked_values_observed=len(tracked_now),
        admitted_values=len(admitted),
        below_admission_threshold=below,
        rejected_over_quota=rejected,
        admission_truncated=rejected > 0 or cut_by_fold > 0,
    )
    return AdmittedBucket(final, {item.value_key: item.events for item in kept})


def retire_stale(
    values: Mapping[str, TrackedValue],
    *,
    reference: datetime,
    policy: PwAdmissionPolicy,
) -> list[str]:
    """Retire active, unpinned values unobserved for `active_days`; returns their keys.

    `reference` is the newest bucket of the tick. `tracked_until` is that bucket, so every
    bucket after it reads the value's absence as "not tracked", never as a zero.
    """
    cutoff = reference - timedelta(days=policy.active_days)
    retired: list[str] = []
    for key, value in values.items():
        if not value.active or value.pinned:
            continue
        seen = value.last_seen_at or value.first_seen_at or value.tracked_since
        if seen is None or seen >= cutoff:
            continue
        value.active = False
        value.tracked_until = reference
        value.retired_at = reference
        value.dirty = True
        retired.append(key)
    return retired


def retire_other_versions(
    values: Sequence[TrackedValue], *, at: datetime
) -> list[TrackedValue]:
    """Retire still-active values of an older `watch_version`: they belong to no scope now."""
    out: list[TrackedValue] = []
    for value in values:
        if value.active:
            value.active = False
            value.tracked_until = at
            value.retired_at = at
            value.dirty = True
            out.append(value)
    return out
