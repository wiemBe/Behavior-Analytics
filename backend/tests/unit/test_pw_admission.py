"""Property-watch admission, tracking windows and retirement (design §G.7.3). Pure."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import PwBucketMeasurement, PwPinnedMeasurement
from app.property_watch.admission import (
    PwAdmissionPolicy,
    TrackedValue,
    admit_bucket,
    retire_other_versions,
    retire_stale,
    sync_pins,
)
from app.property_watch.facts import PwBucketFact, PwValueFact

T0 = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
STEP = timedelta(minutes=5)
POLICY = PwAdmissionPolicy(min_events=3, max_new_per_bucket=2, max_active=5, active_days=7)


def fact(
    values: dict[str, int],
    *,
    at: datetime = T0,
    observed: int | None = None,
    untracked: int | None = 0,
    measurement: PwBucketMeasurement = PwBucketMeasurement.EXACT,
) -> PwBucketFact:
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    parsed = sum(values.values()) + (untracked or 0)
    return PwBucketFact(
        bucket_start=at,
        bucket_seconds=300,
        watch_version=1,
        totals_complete=True,
        values_measurement=measurement,
        pinned_measurement=PwPinnedMeasurement.NOT_REQUESTED,
        total_events=parsed + 4,
        parsed_events=parsed,
        untracked_events=untracked if measurement is PwBucketMeasurement.EXACT else None,
        values=tuple(PwValueFact(k, v, (k,)) for k, v in ordered),
        observed_values=len(values) if observed is None else observed,
    )


def tracked(key: str, *, pinned: bool = False, active: bool = True, last: datetime | None = T0,
            since: datetime | None = T0 - timedelta(days=1)) -> TrackedValue:
    import uuid

    return TrackedValue(
        id=uuid.uuid4(), value_key=key, components=(key,), pinned=pinned, active=active,
        first_seen_at=T0 - timedelta(days=10), last_seen_at=last, tracked_since=since,
    )


def admit(f: PwBucketFact, values: dict[str, TrackedValue], policy: PwAdmissionPolicy = POLICY):
    return admit_bucket(f, values, policy, property_count=1)


class TestAdmission:
    def test_a_value_below_the_threshold_is_not_admitted_and_its_events_go_untracked(self) -> None:
        values: dict[str, TrackedValue] = {}
        out = admit(fact({"a": 10, "b": 2, "c": 1}), values)
        assert set(values) == {"a"}
        assert [v.value_key for v in out.fact.values] == ["a"]
        assert out.metrics == {"a": 10}
        f = out.fact
        assert (f.observed_values, f.admitted_values) == (3, 1)
        assert (f.below_admission_threshold, f.rejected_over_quota) == (2, 0)
        assert f.untracked_events == 3 and f.admission_truncated is False

    def test_the_threshold_is_inclusive(self) -> None:
        values: dict[str, TrackedValue] = {}
        admit(fact({"a": 3, "b": 2}), values)
        assert set(values) == {"a"}

    def test_the_quota_admits_the_busiest_first_and_counts_the_rest(self) -> None:
        values: dict[str, TrackedValue] = {}
        out = admit(fact({"a": 10, "b": 30, "c": 20, "d": 5}), values)
        assert set(values) == {"b", "c"}
        assert out.fact.rejected_over_quota == 2 and out.fact.admission_truncated is True
        assert out.fact.untracked_events == 15

    def test_ties_go_to_the_lower_key(self) -> None:
        values: dict[str, TrackedValue] = {}
        admit(fact({"z": 9, "m": 9, "a": 9}), values)
        assert set(values) == {"a", "m"}

    def test_the_ceiling_bounds_admission_and_counts_pinned_values(self) -> None:
        values = {"p1": tracked("p1", pinned=True), "p2": tracked("p2", pinned=True),
                  "t1": tracked("t1"), "t2": tracked("t2")}  # 4 of 5 slots used
        out = admit(fact({"x": 9, "y": 8, "z": 7}), values)
        assert out.fact.admitted_values == 1 and set(values) >= {"x"}
        assert "y" not in values and out.fact.rejected_over_quota == 2
        assert out.fact.admission_truncated is True

    def test_a_full_ceiling_admits_nothing_but_still_records_tracked_values(self) -> None:
        values = {f"t{i}": tracked(f"t{i}") for i in range(5)}
        out = admit(fact({"t0": 7, "x": 9}), values)
        assert out.fact.admitted_values == 0 and "x" not in values
        assert out.metrics == {"t0": 7}
        assert out.fact.tracked_values_observed == 1

    def test_the_ceiling_and_the_quota_hold_across_the_buckets_of_a_tick(self) -> None:
        values: dict[str, TrackedValue] = {}
        policy = PwAdmissionPolicy(3, 2, 3, 7)
        admit(fact({"a": 9, "b": 8, "c": 7}, at=T0), values, policy)
        assert set(values) == {"a", "b"}
        out = admit(fact({"a": 9, "c": 7, "d": 6}, at=T0 + STEP), values, policy)
        assert set(values) == {"a", "b", "c"} and out.fact.admitted_values == 1
        assert out.fact.rejected_over_quota == 1

    def test_a_tracked_value_below_the_threshold_still_gets_its_metric(self) -> None:
        values = {"a": tracked("a")}
        out = admit(fact({"a": 1}), values)
        assert out.metrics == {"a": 1} and out.fact.below_admission_threshold == 0

    def test_admission_updates_the_tracking_window(self) -> None:
        values: dict[str, TrackedValue] = {}
        admit(fact({"a": 9}, at=T0 + STEP), values)
        a = values["a"]
        assert a.tracked_since == T0 + STEP == a.first_seen_at == a.last_seen_at
        assert a.active and a.dirty and not a.exists and a.tracked_until is None

    def test_a_returning_value_starts_a_new_period_and_keeps_its_first_sighting(self) -> None:
        old = tracked("a", active=False, since=T0 - timedelta(days=9), last=T0 - timedelta(days=8))
        old.tracked_until = T0 - timedelta(days=1)
        old.retired_at = T0 - timedelta(days=1)
        values = {"a": old}
        out = admit(fact({"a": 9}, at=T0 + STEP), values)
        assert out.fact.admitted_values == 1
        assert old.active and old.tracked_since == T0 + STEP
        assert old.tracked_until is None and old.retired_at is None
        assert old.first_seen_at == T0 - timedelta(days=10)
        assert old.last_seen_at == T0 + STEP

    def test_a_retired_value_competes_for_the_quota_like_a_new_one(self) -> None:
        old = tracked("a", active=False)
        values = {"a": old}
        out = admit(fact({"a": 2, "b": 9}), values)  # `a` is under the threshold
        assert out.fact.below_admission_threshold == 1 and not old.active

    def test_last_seen_only_moves_forward(self) -> None:
        values = {"a": tracked("a", last=T0 + 4 * STEP)}
        admit(fact({"a": 9}, at=T0), values)
        assert values["a"].last_seen_at == T0 + 4 * STEP

    def test_a_lower_bound_bucket_admits_but_leaves_untracked_unknown(self) -> None:
        values: dict[str, TrackedValue] = {}
        out = admit(
            fact({"a": 9, "b": 1}, measurement=PwBucketMeasurement.LOWER_BOUND), values
        )
        assert out.fact.untracked_events is None and set(values) == {"a"}

    def test_a_bucket_that_measured_nothing_admits_nothing(self) -> None:
        values: dict[str, TrackedValue] = {}
        empty = PwBucketFact(
            bucket_start=T0, bucket_seconds=300, watch_version=1, totals_complete=False,
            values_measurement=PwBucketMeasurement.NOT_COLLECTED,
            pinned_measurement=PwPinnedMeasurement.NOT_REQUESTED,
        )
        out = admit(empty, values)
        assert out.fact is empty and out.metrics == {} and values == {}

    def test_values_the_fold_cut_mark_the_bucket_truncated(self) -> None:
        values: dict[str, TrackedValue] = {}
        out = admit(fact({"a": 9}, observed=4, untracked=30), values)
        assert out.fact.admission_truncated is True and out.fact.rejected_over_quota == 0
        assert out.fact.untracked_events == 30

    def test_nothing_not_admitted_survives_in_the_result(self) -> None:
        values: dict[str, TrackedValue] = {}
        out = admit(fact({"keep": 9, "secret": 1}), values)
        assert "secret" not in {v.value_key for v in out.fact.values}
        assert "secret" not in out.metrics and "secret" not in values

    def test_a_truncated_value_flags_the_row(self) -> None:
        f = fact({"a": 9})
        f = PwBucketFact(**{**f.__dict__, "values": (PwValueFact("a", 9, ("a",), True),)})
        values: dict[str, TrackedValue] = {}
        admit(f, values)
        assert values["a"].truncated is True

    def test_policy_rejects_nonsense(self) -> None:
        with pytest.raises(ValueError):
            PwAdmissionPolicy(0, 1, 1, 1)
        with pytest.raises(ValueError):
            PwAdmissionPolicy(1, -1, 1, 1)


class TestPins:
    def test_a_pin_without_a_row_is_tracked_from_the_first_bucket(self) -> None:
        values: dict[str, TrackedValue] = {}
        sync_pins(values, {"carol": ("carol",)}, first_bucket=T0)
        v = values["carol"]
        assert v.pinned and v.active and v.tracked_since == T0 and not v.exists

    def test_an_existing_value_becomes_pinned_and_a_retired_one_is_reactivated(self) -> None:
        values = {"a": tracked("a"), "b": tracked("b", active=False)}
        values["b"].tracked_until = T0
        values["b"].retired_at = T0
        sync_pins(values, {"a": ("a",), "b": ("b",)}, first_bucket=T0 + STEP)
        assert values["a"].pinned and values["b"].pinned
        assert values["b"].active and values["b"].tracked_until is None
        assert values["b"].tracked_since == T0 + STEP

    def test_a_removed_pin_loses_its_protection_but_stays_tracked(self) -> None:
        values = {"a": tracked("a", pinned=True)}
        sync_pins(values, {}, first_bucket=T0)
        assert not values["a"].pinned and values["a"].active and values["a"].dirty

    def test_a_pinned_value_is_never_subject_to_admission_or_retirement(self) -> None:
        values: dict[str, TrackedValue] = {}
        sync_pins(values, {"carol": ("carol",)}, first_bucket=T0)
        out = admit(fact({"carol": 1, "a": 9}), values)  # 1 event: under the threshold
        assert out.metrics["carol"] == 1
        values["carol"].last_seen_at = None
        retired = retire_stale(values, reference=T0 + timedelta(days=60), policy=POLICY)
        assert retired == ["a"] and values["carol"].active

    def test_a_pinned_value_counts_toward_the_ceiling(self) -> None:
        values: dict[str, TrackedValue] = {}
        sync_pins(values, {f"p{i}": (f"p{i}",) for i in range(4)}, first_bucket=T0)
        out = admit(fact({"x": 9, "y": 8}), values)
        assert out.fact.admitted_values == 1 and out.fact.rejected_over_quota == 1


class TestRetirement:
    def test_an_unobserved_value_is_retired_with_its_window_closed(self) -> None:
        old = tracked("old", last=T0 - timedelta(days=8))
        fresh = tracked("fresh", last=T0 - timedelta(days=6))
        values = {"old": old, "fresh": fresh}
        assert retire_stale(values, reference=T0, policy=POLICY) == ["old"]
        assert not old.active and old.tracked_until == T0 == old.retired_at and old.dirty
        assert fresh.active and fresh.tracked_until is None

    def test_the_boundary_is_inclusive_of_the_active_window(self) -> None:
        edge = tracked("edge", last=T0 - timedelta(days=7))
        assert retire_stale({"edge": edge}, reference=T0, policy=POLICY) == []

    def test_a_value_never_observed_ages_from_its_first_sighting(self) -> None:
        v = tracked("v", last=None)
        v.first_seen_at = T0 - timedelta(days=9)
        assert retire_stale({"v": v}, reference=T0, policy=POLICY) == ["v"]

    def test_inactive_values_are_left_alone(self) -> None:
        v = tracked("v", active=False, last=T0 - timedelta(days=30))
        assert retire_stale({"v": v}, reference=T0, policy=POLICY) == []
        assert not v.dirty

    def test_values_of_another_version_are_retired(self) -> None:
        a, b = tracked("a"), tracked("b", active=False)
        out = retire_other_versions([a, b], at=T0)
        assert out == [a] and not a.active and a.tracked_until == T0 and not b.dirty
