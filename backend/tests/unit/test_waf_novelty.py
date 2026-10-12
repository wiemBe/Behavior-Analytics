"""Verification of the history behind a new-endpoint claim. Pure; no database."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import BucketCompleteness, WafMeasurement
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from app.waf.novelty import semantics_normalizer_version, verify_endpoint_history
from app.waf.observations import BucketFacts
from app.waf.semantics import breakdown_semantics, request_count_semantics

WIDTH = 300
END = datetime(2026, 8, 3, 19, 0, tzinfo=UTC)
COUNT = 12
START = END - timedelta(seconds=WIDTH * COUNT)
SEMANTICS = request_count_semantics(
    aggregation="SUM(eventcount)",
    normalizer_version=ENDPOINT_NORMALIZER_VERSION,
    profile_version=3,
)


def bucket(index: int, **overrides) -> BucketFacts:  # type: ignore[no-untyped-def]
    base = BucketFacts(
        bucket_start=START + timedelta(seconds=WIDTH * index),
        bucket_seconds=WIDTH,
        completeness=BucketCompleteness.COMPLETE,
        endpoint_coverage_ratio=1.0,
        metric_semantics=SEMANTICS,
        in_maintenance=False,
        measurement=WafMeasurement.EXACT,
        sparse=True,
        groups_truncated=False,
        admission_truncated=False,
        profile_version=3,
    )
    return replace(base, **overrides)


def history(**overrides) -> list[BucketFacts]:  # type: ignore[no-untyped-def]
    return [bucket(i, **overrides) for i in range(COUNT)]


def verify(buckets, **overrides):  # type: ignore[no-untyped-def]
    args = {
        "history_start": START,
        "history_end": END,
        "bucket_seconds": WIDTH,
        "profile_version": 3,
        "normalizer_version": ENDPOINT_NORMALIZER_VERSION,
    }
    args.update(overrides)
    return verify_endpoint_history(buckets, **args)


def test_a_complete_whole_history_is_complete() -> None:
    facts = verify(history())
    assert facts.complete is True
    assert facts.reason is None


def test_order_of_the_input_does_not_matter() -> None:
    assert verify(list(reversed(history()))).complete is True


def test_missing_buckets_are_counted_in_the_reason() -> None:
    buckets = [b for i, b in enumerate(history()) if i not in (2, 7, 9)]
    facts = verify(buckets)
    assert facts.complete is False
    assert facts.reason == f"3 of {COUNT} history buckets were never collected"


def test_an_empty_history_is_all_missing() -> None:
    facts = verify([])
    assert facts.complete is False
    assert facts.reason == f"{COUNT} of {COUNT} history buckets were never collected"


def test_a_window_not_divisible_by_the_bucket_width_is_refused() -> None:
    facts = verify(history(), history_end=END + timedelta(seconds=1))
    assert facts.complete is False
    assert "not divisible" in (facts.reason or "")


def test_a_zero_bucket_width_is_refused() -> None:
    assert verify(history(), bucket_seconds=0).complete is False


def test_a_misaligned_extra_record_is_refused() -> None:
    extra = bucket(0, bucket_start=START + timedelta(seconds=17))
    facts = verify([*history(), extra])
    assert facts.complete is False
    assert f"{COUNT + 1} bucket records for {COUNT} expected" in (facts.reason or "")


def test_a_bucket_of_another_width_is_refused() -> None:
    buckets = history()
    buckets[4] = replace(buckets[4], bucket_seconds=60)
    facts = verify(buckets)
    assert facts.complete is False
    assert "different collection bucket width" in (facts.reason or "")


def test_truncated_discovery_in_one_history_bucket_is_refused() -> None:
    buckets = history()
    buckets[5] = replace(buckets[5], groups_truncated=True)
    facts = verify(buckets)
    assert facts.complete is False
    assert buckets[5].bucket_start.isoformat() in (facts.reason or "")
    assert "truncated endpoint discovery" in (facts.reason or "")


def test_truncated_admission_in_one_history_bucket_is_refused() -> None:
    buckets = history()
    buckets[6] = replace(buckets[6], admission_truncated=True)
    facts = verify(buckets)
    assert facts.complete is False
    assert "truncated endpoint admission" in (facts.reason or "")


def test_a_different_profile_version_is_refused() -> None:
    buckets = history()
    buckets[1] = replace(buckets[1], profile_version=2)
    facts = verify(buckets)
    assert facts.complete is False
    assert "used profile version 2, not first-appearance version 3" in (facts.reason or "")


def test_a_bucket_without_a_profile_version_is_refused() -> None:
    buckets = history()
    buckets[1] = replace(buckets[1], profile_version=None)
    assert verify(buckets).complete is False


def test_a_bucket_without_semantics_is_refused() -> None:
    buckets = history()
    buckets[3] = replace(buckets[3], metric_semantics=None)
    facts = verify(buckets)
    assert facts.complete is False
    assert "incompatible with endpoint normalizer version" in (facts.reason or "")


def test_a_bucket_under_another_normalizer_is_refused() -> None:
    other = request_count_semantics(
        aggregation="SUM(eventcount)",
        normalizer_version=ENDPOINT_NORMALIZER_VERSION + 1,
        profile_version=3,
    )
    buckets = history()
    buckets[3] = replace(buckets[3], metric_semantics=other)
    assert verify(buckets).complete is False


def test_the_first_failing_condition_is_the_one_reported() -> None:
    buckets = history(groups_truncated=True, profile_version=1)
    del buckets[0]
    assert "never collected" in (verify(buckets).reason or "")
    assert "truncated endpoint discovery" in (verify(history(groups_truncated=True)).reason or "")
    both = history(admission_truncated=True, profile_version=1)
    assert "truncated endpoint admission" in (verify(both).reason or "")


def test_normalizer_is_read_as_a_component_not_a_substring() -> None:
    assert semantics_normalizer_version(SEMANTICS) == ENDPOINT_NORMALIZER_VERSION
    assert semantics_normalizer_version("x;normalizer/v12;y") == 12
    assert semantics_normalizer_version("x;mynormalizer/v1;y") is None
    assert semantics_normalizer_version("x;normalizer/vX;y") is None
    assert semantics_normalizer_version(None) is None
    breakdown = breakdown_semantics(
        "ACTION", aggregation=None, normalizer_version=4, profile_version=1
    )
    assert semantics_normalizer_version(breakdown) == 4


@pytest.mark.parametrize("version", (1, 2, 10))
def test_v1_is_not_taken_for_v10(version: int) -> None:
    semantics = request_count_semantics(
        aggregation=None, normalizer_version=10, profile_version=3
    )
    assert (semantics_normalizer_version(semantics) == version) is (version == 10)
