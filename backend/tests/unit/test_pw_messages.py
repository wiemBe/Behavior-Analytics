"""MSG-4b: the property-watch sentences carry message codes and keep their English.

The detector table is the English the code wrote before it had codes (captured from that code,
sentence by sentence, for the scenarios in `pw_message_scenarios.py`); a reworded template, a
changed number format or a lost code fails here. Everything else in the family is pinned the
same way, site by site.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.history.planner import Gap
from app.history.progress_codec import ProgressCodecError, decode_progress, encode_progress
from app.i18n.messages import REGISTRY, MessageRef, render
from app.models.enums import PwBucketMeasurement, PwBucketOrigin, PwScopeKind
from app.property_watch.facts import PwBucketFact
from app.property_watch.history import PwHistoryFacts, verify_value_history
from app.property_watch.scope import GroupRow, SourceRow, resolve_scope
from tests.unit.pw_message_scenarios import SCENARIOS
from tests.unit.test_property_watch_detection import NOW, bucket

EXPECTED: dict[str, tuple[str, str]] = {
    "no_status": ("UNKNOWN", "the watch has never established a collection status"),
    "watch_blocked": ("UNKNOWN", "the watch is SCOPE_STALE: no value verdict is possible"),
    "bucket_missing": ("UNKNOWN", "the bucket is missing: no count was measured"),
    "bucket_not_collected": ("UNKNOWN", "the bucket was NOT_COLLECTED: its count is not zero"),
    "bucket_membership": ("UNKNOWN", "membership changed during this bucket"),
    "bucket_not_parsed": (
        "UNKNOWN",
        "the property parsed on none of this bucket's events: NOT_APPLICABLE",
    ),
    "no_baseline": ("UNKNOWN", "no value-events baseline for this weekday/hour cell yet"),
    "baseline_version": ("UNKNOWN", "the baseline uses watch version 2, not the bucket's 1"),
    "baseline_few_visits": (
        "UNKNOWN",
        (
            "the value-events baseline has 12 sample(s), but they come from too few"
            " separate weekly visits of this weekday and hour: a baseline needs the"
            " same hour from at least 2 different weeks"
        ),
    ),
    "baseline_few_visits_one": (
        "UNKNOWN",
        (
            "the value-events baseline has 12 sample(s), but they come from too few"
            " separate weekly visits of this weekday and hour: a baseline needs the"
            " same hour from at least 1 different week"
        ),
    ),
    "baseline_samples": (
        "UNKNOWN",
        "the value-events baseline has 5 sample(s), below the minimum of 8",
    ),
    "count_unknown": ("UNKNOWN", "the value's count is unknown for this bucket, not zero"),
    "inputs_unsupported": (
        "UNKNOWN",
        "volume: the detector needs exact counts and this count is a lower bound",
    ),
    "coverage_below": ("UNKNOWN", "property coverage was 62\u00a0%, below the 90\u00a0% floor"),
    "coverage_unknown": ("UNKNOWN", "property coverage was unknown, below the 90\u00a0% floor"),
    "spike_min_events": ("HEALTHY", "12 events is below the 20 minimum"),
    "ratio_not_reached": ("HEALTHY", "observed/expected ratio 1.50 has not reached 2.00"),
    "spike_delta_below_min": (
        "HEALTHY",
        "increase of 25 events is below the 50 minimum absolute delta",
    ),
    "z_not_reached_high": ("HEALTHY", "robust z=1.69 has not reached +3.5"),
    "mad_within_p95": (
        "HEALTHY",
        "robust score is unavailable (MAD=0) and the value is within p95 200.00",
    ),
    "spike_not_proven_lower": (
        "UNKNOWN",
        (
            "12 events is below the 20 minimum; volume: the count is a lower bound:"
            " an increase can be proven, normal behaviour cannot, so a normal"
            " verdict cannot be concluded"
        ),
    ),
    "spike_fired": (
        "ANOMALOUS",
        "520 events is above the median 100.0 (+420, 5.20x); robust z=28.33 (threshold +3.5)",
    ),
    "spike_fired_lower": (
        "ANOMALOUS",
        (
            "at least 520 events is above the median 100.0 (+420, 5.20x); robust"
            " z=28.33 (threshold +3.5)"
        ),
    ),
    "spike_fired_mad_zero": (
        "ANOMALOUS",
        (
            "520 events is above the median 100.0 (+420, 5.20x); robust score is"
            " unavailable (MAD=0); the value is above p95 120.00"
        ),
    ),
    "spike_zero_median": (
        "ANOMALOUS",
        "90 events rose from a zero median; robust z=6.07 (threshold +3.5)",
    ),
    "spike_zero_median_lower": (
        "ANOMALOUS",
        "at least 90 events rose from a zero median; robust z=6.07 (threshold +3.5)",
    ),
    "drop_median_floor": ("HEALTHY", "baseline median 12.0 is below the 20.0 traffic floor"),
    "drop_ratio_above": ("HEALTHY", "observed/expected ratio 0.80 is above the 0.50 drop line"),
    "drop_delta_below_min": ("HEALTHY", "fall of 40 events is below the 50 minimum absolute delta"),
    "z_not_reached_low": ("HEALTHY", "robust z=-0.79 has not reached -3.5"),
    "mad_within_p05": (
        "HEALTHY",
        "robust score is unavailable (MAD=0) and the value is within p05 5.00",
    ),
    "drop_fired": (
        "ANOMALOUS",
        "10 exact events is below the median 100.0 (-90, 0.10x); robust z=-6.07 (threshold -3.5)",
    ),
    "drop_fired_mad_zero": (
        "ANOMALOUS",
        (
            "0 exact events is below the median 100.0 (-100, 0.00x); robust score"
            " is unavailable (MAD=0); the value is below p05 80.00"
        ),
    ),
    "last_seen_unknown": ("UNKNOWN", "when the value was last observed is not known"),
    "not_active": ("HEALTHY", "the value was not observed within the last 7 days"),
    "disappear_median_floor": (
        "HEALTHY",
        "baseline median 2.0 is below the 5.0 disappearance floor",
    ),
    "observed_positive": ("HEALTHY", "the value carried 14 exact events in this bucket"),
    "window_bucket_missing": (
        "UNKNOWN",
        (
            "the bucket at 2026-10-17T11:45:00+00:00 inside the disappearance"
            " window was not supplied: its count is not zero"
        ),
    ),
    "window_membership": ("UNKNOWN", "membership changed inside the disappearance window"),
    "window_count_unknown": (
        "UNKNOWN",
        "the value count at 2026-10-17T11:45:00+00:00 is unknown, not zero",
    ),
    "window_coverage": (
        "UNKNOWN",
        "property coverage at 2026-10-17T11:45:00+00:00 was below the floor",
    ),
    "streak_short": (
        "HEALTHY",
        "the value has 3 consecutive measured-zero bucket(s), below the required 6",
    ),
    "disappeared_fired": (
        "ANOMALOUS",
        (
            "the value had zero parsed events for 6 consecutive exact buckets; the"
            " seasonal median is 100.0 events"
        ),
    ),
    "novelty_unestablished": ("UNKNOWN", "the value's first appearance was not established"),
    "established_hand_off": (
        "HEALTHY",
        (
            "established value: first seen 2026-10-17T11:55:00+00:00 in the history"
            " read before the watch went live at 2026-10-17T12:00:00+00:00"
        ),
    ),
    "established_outside_window": (
        "HEALTHY",
        (
            "established value: first admitted 2026-10-17T10:00:00+00:00, outside"
            " the 12-bucket novelty window"
        ),
    ),
    "pinned_never_new": (
        "HEALTHY",
        "pinned values are operator-named and are never classified as new",
    ),
    "learning_unknown": ("UNKNOWN", "learning: the watch's first collected bucket is unknown"),
    "learning_day": ("UNKNOWN", "learning: day 2 of 7"),
    "grace_membership": ("UNKNOWN", "membership changed inside the first-appearance grace window"),
    "history_incomplete_text": (
        "UNKNOWN",
        "novelty cannot be established: one bucket was never collected",
    ),
    "history_incomplete_default": (
        "UNKNOWN",
        (
            "novelty cannot be established: history: the bucket does not state"
            " whether its counts are exact or lower bounds"
        ),
    ),
    "admission_truncated": (
        "UNKNOWN",
        (
            "the first-appearance bucket had truncated admission: the value"
            " appeared in a flood and is not individually new"
        ),
    ),
    "materiality_short": (
        "HEALTHY",
        "10 event(s) in 2 bucket(s) since first appearance has not reached 20 events in 2 buckets",
    ),
    "materiality_lower_bound": (
        "UNKNOWN",
        (
            "10 event(s) in 2 bucket(s) since first appearance has not reached 20"
            " events in 2 buckets; the materiality counts are lower bounds"
        ),
    ),
    "new_fired": (
        "ANOMALOUS",
        (
            "30 events across 2 buckets since first appearance; the value stayed"
            " below 3 events in every bucket of the verified history"
        ),
    ),
    "new_fired_lower": (
        "ANOMALOUS",
        (
            "at least 40 events across 3 buckets since first appearance; the value"
            " stayed below 3 events in every bucket of the verified history"
        ),
    ),
    "new_value_is_combination": (
        "HEALTHY",
        "both tuple components were tracked before: this is classified only as a new combination",
    ),
    "one_property": ("UNKNOWN", "a one-property watch has nothing to combine"),
    "components_missing": ("UNKNOWN", "the tuple's two component histories were not supplied"),
    "component_untracked": (
        "HEALTHY",
        "at least one component was not previously tracked: this is classified only as a new value",
    ),
    "component_untracked_below_admission": (
        "HEALTHY",
        (
            "at least one component was not previously tracked (seen only below the"
            " admission threshold): this is classified only as a new value"
        ),
    ),
    "combination_fired": (
        "ANOMALOUS",
        (
            "30 events across 2 buckets since first appearance; both components"
            " were tracked before, but never together in the verified history"
        ),
    ),
    "combination_fired_lower": (
        "ANOMALOUS",
        (
            "at least 40 events across 2 buckets since first appearance; both"
            " components were tracked before, but never together in the verified"
            " history"
        ),
    ),
    "combination_materiality_short": (
        "HEALTHY",
        "3 event(s) in 2 bucket(s) since first appearance has not reached 20 events in 2 buckets",
    ),
}


#: Scenarios whose whole sentence is the scope decision's own wording (shared with WAF and IPS,
#: coded by their tasks): they stay English text with no code.
UNCODED = {"inputs_unsupported"}


def _codes(ref: MessageRef, seen: set[str]) -> None:
    seen.add(ref.code)
    for value in ref.params.values():
        if isinstance(value, MessageRef):
            _codes(value, seen)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_detector_english_is_unchanged(name: str) -> None:
    evidence = SCENARIOS[name]()
    assert (evidence.signal.value, evidence.reason) == EXPECTED[name]


def test_every_scenario_has_a_pinned_sentence() -> None:
    assert set(SCENARIOS) == set(EXPECTED)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_a_detector_sentence_renders_from_its_code(name: str) -> None:
    evidence = SCENARIOS[name]()
    if name in UNCODED:
        assert evidence.reason_message is None
        return
    ref = evidence.reason_message
    assert ref is not None
    assert render(ref) == evidence.reason
    wire = evidence.to_dict()
    assert "reason_message" not in wire
    assert wire["reason_code"] == ref.code
    assert wire["reason_params"] == ref.to_wire()["params"]
    json.dumps(wire)  # the stored evidence stays JSON-safe, timestamps included


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_no_verdict_without_data_is_ever_normal(name: str) -> None:
    evidence = SCENARIOS[name]()
    if evidence.signal.value == "UNKNOWN":
        assert evidence.insufficient_data is True
    assert evidence.reason  # a sentence, never empty


def test_the_scenarios_reach_every_detector_code_but_the_unreachable_fallbacks() -> None:
    seen: set[str] = set()
    for build in SCENARIOS.values():
        evidence = build()
        if evidence.reason_message is not None:
            _codes(evidence.reason_message, seen)
        claim = evidence.extra.get("claim_code")
        if claim:
            seen.add(claim)
    registered = {s.code for s in REGISTRY if s.code.startswith("pw.detector.")}
    #: Reached only when the scope decision carries no wording, which `verdict_scope` never
    #: produces for a narrowed scope; pinned directly in the next test.
    fallbacks = {
        "pw.detector.inputs_unsupported",
        "pw.detector.novelty_inputs_unsupported",
        "pw.detector.combination_inputs_unsupported",
    }
    assert registered - seen == fallbacks


def test_a_scope_decision_without_wording_falls_back_to_a_code() -> None:
    from app.models.enums import PwAnomalyType
    from app.property_watch.detectors import PwDetectionContext, _Guard
    from app.waf.measurement import ScopeDecision, WafVerdictScope
    from tests.unit.test_property_watch_detection import context

    ctx: PwDetectionContext = context()
    guard = _Guard(ctx, PwAnomalyType.PW_VALUE_SPIKE)
    guard.decision = ScopeDecision(WafVerdictScope.NONE, None)
    for fallback, english in (
        ("inputs_unsupported", "the declared inputs do not support a verdict"),
        ("novelty_inputs_unsupported", "the declared novelty inputs do not support a verdict"),
        (
            "combination_inputs_unsupported",
            "the declared combination inputs do not support a verdict",
        ),
    ):
        evidence = guard.insufficient(guard.scope_reason(fallback)).evidence
        assert evidence.reason == english
        assert evidence.reason_message == MessageRef(f"pw.detector.{fallback}")
    guard.decision = ScopeDecision(WafVerdictScope.NONE, "volume: the shared wording")
    shared = guard.insufficient(guard.scope_reason("inputs_unsupported")).evidence
    assert shared.reason == "volume: the shared wording" and shared.reason_message is None


def test_the_claim_travels_inside_the_details_with_its_code() -> None:
    value = SCENARIOS["new_fired"]()
    assert value.extra["claim"] == (
        "the value stayed below 3 events in every bucket of the verified history"
    )
    assert value.extra["claim_code"] == "pw.detector.claim_new_value"
    assert value.extra["claim_params"] == {"min_events": 3}
    combination = SCENARIOS["combination_fired"]()
    assert combination.extra["claim"] == (
        "both components were tracked before, but never together in the verified history"
    )
    assert combination.extra["claim_code"] == "pw.detector.claim_new_combination"
    assert combination.extra["claim_params"] == {}
    json.dumps(value.to_dict())


def test_the_not_proven_sentence_nests_the_reason_it_wraps() -> None:
    evidence = SCENARIOS["spike_not_proven_lower"]()
    assert evidence.reason_message is not None
    assert evidence.reason_message.code == "pw.detector.not_proven_lower_bound"
    inner = evidence.reason_message.params["reason"]
    assert isinstance(inner, MessageRef) and inner.code == "pw.detector.spike_min_events"
    assert evidence.insufficient_data is True


def test_a_history_reason_that_is_only_text_is_kept_as_text() -> None:
    evidence = SCENARIOS["history_incomplete_text"]()
    assert evidence.reason_message is not None
    assert evidence.reason_message.params["reason"] == "one bucket was never collected"
    assert evidence.to_dict()["reason_params"]["reason"] == "one bucket was never collected"


# --------------------------------------------------------------- the history verifier
def _rows(**change: Any) -> list[PwBucketFact]:
    start = NOW - timedelta(minutes=15)
    return [bucket(start + timedelta(minutes=5 * i), **change) for i in range(3)]


def _verify(rows: list[PwBucketFact], **change: Any) -> Any:
    kwargs: dict[str, Any] = {
        "history_start": NOW - timedelta(minutes=15),
        "history_end": NOW,
        "bucket_seconds": 300,
        "watch_version": 1,
        "min_coverage": 0.9,
    }
    return verify_value_history(rows, **{**kwargs, **change})


def _verifier_cases() -> list[tuple[str, Any, str, str]]:
    first = NOW - timedelta(minutes=15)
    stamp = first.isoformat()
    rows = _rows()
    lower = replace(
        rows[0], values_measurement=PwBucketMeasurement.LOWER_BOUND, untracked_events=None
    )
    low_history = replace(
        rows[0], origin=PwBucketOrigin.HISTORY, total_events=100, parsed_events=89
    )
    return [
        (
            "not_divisible",
            _verify(rows, bucket_seconds=400),
            "the history window is not divisible by the collection bucket width",
            "pw.history_check.not_divisible",
        ),
        (
            "never_collected",
            _verify(rows[1:]),
            f"history bucket {stamp} was never collected (1 of 3 missing)",
            "pw.history_check.bucket_never_collected",
        ),
        (
            "record_count",
            _verify([*rows, rows[0]]),
            "the history contains 4 bucket records for 3 expected buckets",
            "pw.history_check.record_count",
        ),
        (
            "width",
            _verify([replace(rows[0], bucket_seconds=600), *rows[1:]]),
            f"history bucket {stamp} used a different collection bucket width",
            "pw.history_check.bucket_width",
        ),
        (
            "not_collected",
            _verify([bucket(first, measurement=PwBucketMeasurement.NOT_COLLECTED), *rows[1:]]),
            f"history bucket {stamp} was NOT_COLLECTED",
            "pw.history_check.bucket_not_collected",
        ),
        (
            "discovery",
            _verify([lower, *rows[1:]]),
            f"history bucket {stamp} had truncated value discovery",
            "pw.history_check.discovery_truncated",
        ),
        (
            "admission",
            _verify([replace(rows[0], admission_truncated=True), *rows[1:]]),
            f"history bucket {stamp} had truncated value admission",
            "pw.history_check.admission_truncated",
        ),
        (
            "over_quota",
            _verify([replace(rows[0], rejected_over_quota=2), *rows[1:]]),
            f"history bucket {stamp} had truncated value admission",
            "pw.history_check.admission_truncated",
        ),
        (
            "version",
            _verify([replace(rows[0], watch_version=2), *rows[1:]], watch_version=1),
            f"history bucket {stamp} used another watch version, "
            "not first-appearance version 1",
            "pw.history_check.bucket_version",
        ),
        (
            "coverage",
            _verify([low_history, *rows[1:]]),
            f"history bucket {stamp} had parsing coverage below the floor",
            "pw.history_check.bucket_coverage",
        ),
    ]


@pytest.mark.parametrize("case", range(10))
def test_each_history_failure_keeps_its_english_and_names_its_code(case: int) -> None:
    label, facts, english, code = _verifier_cases()[case]
    assert facts.complete is False, label
    assert facts.reason == english
    assert isinstance(facts, PwHistoryFacts)
    assert facts.message is not None and facts.message.code == code
    assert render(facts.message) == english
    facts.message.to_wire()  # the timestamps are wire-safe


def test_a_complete_history_has_no_reason_and_no_code() -> None:
    facts = _verify(_rows())
    assert facts.complete is True and facts.reason is None


def test_the_history_verifier_checks_in_a_fixed_order() -> None:
    # A missing bucket is reported before a bad one: the first failing check wins.
    rows = _rows()
    facts = _verify([replace(rows[0], watch_version=2), *rows[2:]])
    assert facts.message is not None and facts.message.code.endswith("bucket_never_collected")


def test_every_verifier_code_is_reached() -> None:
    seen = {case[3] for case in _verifier_cases()}
    registered = {s.code for s in REGISTRY if s.code.startswith("pw.history_check.")}
    engine_side = {
        "pw.history_check.first_bucket_never_collected",
        "pw.history_check.outside_window",
        "pw.history_check.incomplete",
    }
    assert registered - seen == engine_side


def test_the_engine_side_history_sentences_keep_their_english() -> None:
    from app.property_watch import engine

    assert render(engine._HISTORY_NEVER_COLLECTED) == (
        "the first-appearance bucket was never collected"
    )
    assert render(engine._HISTORY_OUTSIDE_WINDOW) == "outside the novelty window"
    assert render(MessageRef("pw.history_check.incomplete")) == "the history is incomplete"


# --------------------------------------------------------------------------------- scope
def _group(qid: int, parent: int | None, synced: datetime) -> GroupRow:
    return GroupRow(qid, parent, synced)


def _resolve(kind: PwScopeKind, **change: Any) -> Any:
    args: dict[str, Any] = {
        "log_source_id": None,
        "type_id": None,
        "group_id": None,
        "groups": {},
        "sources": [],
        "now": NOW,
        "stale_after": timedelta(hours=24),
        "max_sources": 50,
        "max_chunks": 4,
    }
    args.update(change)
    return resolve_scope(kind, **args)


def _members(count: int, group: int = 1) -> list[SourceRow]:
    return [SourceRow(uuid.uuid4(), 100 + i, None, (group,)) for i in range(count)]


def _scope_cases() -> list[tuple[Any, str, str]]:
    fresh = NOW - timedelta(hours=1)
    old = NOW - timedelta(days=3)
    return [
        (
            _resolve(PwScopeKind.LOG_SOURCE, log_source_id=uuid.uuid4()),
            "the log source is not in the inventory",
            "pw.scope.source_missing",
        ),
        (
            _resolve(PwScopeKind.LOG_SOURCE_TYPE),
            "the watch names no log source type",
            "pw.scope.names_no_type",
        ),
        (
            _resolve(PwScopeKind.LOG_SOURCE_TYPE, type_id=9),
            "no synced log source has this type",
            "pw.scope.type_empty",
        ),
        (
            _resolve(PwScopeKind.LOG_SOURCE_GROUP),
            "the watch names no log source group",
            "pw.scope.names_no_group",
        ),
        (
            _resolve(PwScopeKind.LOG_SOURCE_GROUP, group_id=7),
            "the log source group is not in the inventory",
            "pw.scope.group_missing",
        ),
        (
            _resolve(
                PwScopeKind.LOG_SOURCE_GROUP,
                group_id=1,
                groups={1: _group(1, None, fresh)},
            ),
            "the group and its descendants hold no synced log source",
            "pw.scope.group_empty",
        ),
        (
            _resolve(
                PwScopeKind.LOG_SOURCE_GROUP,
                group_id=1,
                groups={1: _group(1, None, fresh)},
                sources=_members(5),
                max_sources=2,
                max_chunks=1,
            ),
            "5 sources need 3 searches; the limit is 1",
            "pw.scope.too_large",
        ),
        (
            _resolve(
                PwScopeKind.LOG_SOURCE_GROUP,
                group_id=1,
                groups={1: _group(1, None, old)},
                sources=_members(1),
            ),
            "the group inventory was last synced at 2026-10-14T12:00:00+00:00, "
            "older than 1 day, 0:00:00",
            "pw.scope.group_stale",
        ),
    ]


@pytest.mark.parametrize("case", range(8))
def test_each_scope_sentence_keeps_its_english_and_names_its_code(case: int) -> None:
    scope, english, code = _scope_cases()[case]
    assert scope.resolved is False
    assert scope.reason == english
    assert scope.message is not None and scope.message.code == code
    assert render(scope.message) == scope.reason


@pytest.mark.parametrize(
    ("stale_after", "shown"),
    [
        (timedelta(minutes=30), "0:30:00"),
        (timedelta(hours=24), "1 day, 0:00:00"),
        (timedelta(hours=26), "1 day, 2:00:00"),
        (timedelta(hours=1, seconds=5), "1:00:05"),
        (timedelta(days=10, minutes=1), "10 days, 0:01:00"),
    ],
)
def test_the_staleness_limit_reads_as_python_prints_a_timedelta(
    stale_after: timedelta, shown: str
) -> None:
    old = NOW - stale_after - timedelta(seconds=1)
    scope = _resolve(
        PwScopeKind.LOG_SOURCE_GROUP,
        group_id=1,
        groups={1: _group(1, None, old)},
        sources=_members(1),
        stale_after=stale_after,
    )
    assert scope.reason == (
        f"the group inventory was last synced at {old.isoformat()}, older than {shown}"
    )
    assert shown == str(stale_after)
    assert scope.message is not None
    assert scope.message.to_wire()["params"]["limit"] == {
        "kind": "duration",
        "seconds": stale_after.total_seconds(),
    }


def test_a_resolved_scope_has_no_reason_and_no_code() -> None:
    scope = _resolve(
        PwScopeKind.LOG_SOURCE_GROUP,
        group_id=1,
        groups={1: _group(1, None, NOW - timedelta(hours=1))},
        sources=_members(3),
    )
    assert scope.resolved and scope.reason is None and scope.message is None


# ---------------------------------------------------------------- styles of the registry
def _style_registry(**spec: Any) -> Any:
    from app.i18n.messages import MessageSpec, ParamKind, ParamSpec

    kind = spec.pop("kind")
    return (
        MessageSpec(
            code="test.style",
            template="{value}",
            params={"value": ParamSpec(ParamKind(kind), **spec)},
            sample={"value": {"number": 4.0, "duration": 1800, "timestamp": NOW}[kind]},
        ),
    )


def test_the_repr_number_style_keeps_a_trailing_zero_and_an_int() -> None:
    registry = _style_registry(kind="number", style="repr")
    for value, shown in ((3.5, "3.5"), (4.0, "4.0"), (4, "4"), (0.0001, "0.0001")):
        assert render(MessageRef("test.style", {"value": value}), registry=registry) == shown


def test_the_timedelta_duration_style_is_pythons_str_of_a_timedelta() -> None:
    registry = _style_registry(kind="duration", style="timedelta")
    for seconds, shown in ((1800, "0:30:00"), (93_600, "1 day, 2:00:00"), (0, "0:00:00")):
        assert render(MessageRef("test.style", {"value": seconds}), registry=registry) == shown
    with pytest.raises(ValueError, match="negative"):
        render(MessageRef("test.style", {"value": -1}), registry=registry)


def test_the_iso_timestamp_style_is_isoformat_and_needs_a_timezone() -> None:
    registry = _style_registry(kind="timestamp", style="iso")
    shown = render(MessageRef("test.style", {"value": NOW}), registry=registry)
    assert shown == NOW.isoformat() == "2026-10-17T12:00:00+00:00"
    assert render(MessageRef("test.style", {"value": shown}), registry=registry) == shown
    with pytest.raises(ValueError, match="timezone"):
        render(MessageRef("test.style", {"value": datetime(2026, 10, 17, 12)}), registry=registry)
    plus_two = NOW.astimezone(timezone_plus_two())
    assert render(MessageRef("test.style", {"value": plus_two}), registry=registry) == (
        "2026-10-17T14:00:00+02:00"
    )


def timezone_plus_two() -> Any:
    from datetime import timezone

    return timezone(timedelta(hours=2))


def test_the_new_styles_are_refused_on_other_kinds() -> None:
    from app.i18n.messages import (
        MessageRegistryError,
        MessageSpec,
        ParamKind,
        ParamSpec,
        validate_registry,
    )

    for kind, style in (
        (ParamKind.COUNT, "repr"),
        (ParamKind.NUMBER, "timedelta"),
        (ParamKind.DURATION, "iso"),
        (ParamKind.TIMESTAMP, "timedelta"),
    ):
        bad = MessageSpec(
            "test.bad", "{value}", {"value": ParamSpec(kind, style=style)}, {"value": 1}
        )
        with pytest.raises(MessageRegistryError, match="unsupported"):
            validate_registry((bad,))


# ------------------------------------------------------- the gap the planner records
def test_a_recorded_gap_carries_its_code_and_keeps_its_english() -> None:
    from tests.unit.test_history_planner import (
        END,
        SET,
        T0,
        B,
        SearchStatus,
        bad,
        prog,
        settle_step,
    )

    p = prog(floor=T0, chunk_buckets=1, chunk_ceiling=1, failures=SET.max_failures - 1)
    step = settle_step(p, [bad(T0, 1, SearchStatus.FAILED)], end=END, settings=SET)
    gap = step.new_gap
    assert gap is not None and gap.end == T0 + B
    assert gap.reason == f"search failed {SET.max_failures} times at the minimum width"
    assert gap.reason_code == "history.gap.search_failed"
    assert gap.reason_params == {"failures": SET.max_failures}


def _gapped(*gaps: Gap) -> dict[str, Any]:
    from tests.unit.test_history_planner import T0, prog

    return encode_progress(prog(floor=T0, gaps=gaps))


def test_a_coded_gap_round_trips_through_the_stored_progress() -> None:
    start = datetime(2026, 10, 1, tzinfo=UTC)
    coded = Gap(
        start,
        start + timedelta(hours=1),
        "search failed 3 times at the minimum width",
        "history.gap.search_failed",
        {"failures": 3},
    )
    old = Gap(start + timedelta(hours=2), start + timedelta(hours=3), "stored before codes")
    stored = _gapped(coded, old)
    assert stored["progress"]["gaps"][0]["reason_code"] == "history.gap.search_failed"
    assert "reason_code" not in stored["progress"]["gaps"][1]
    json.dumps(stored)
    back = decode_progress(stored).progress.gaps
    assert back == (coded, old)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda gap: gap.pop("reason_params"),
        lambda gap: gap.pop("reason_code"),
        lambda gap: gap.update(reason_code=5),
        lambda gap: gap.update(reason_params=[1]),
        lambda gap: gap.update(extra="x"),
    ],
)
def test_a_gap_with_a_broken_code_pair_is_refused(mutate: Any) -> None:
    start = datetime(2026, 10, 1, tzinfo=UTC)
    stored = _gapped(
        Gap(start, start + timedelta(hours=1), "x", "history.gap.search_failed", {"failures": 3})
    )
    mutate(stored["progress"]["gaps"][0])
    with pytest.raises(ProgressCodecError):
        decode_progress(stored)


# ------------------------------------------------------------------ constants and refs
def test_the_engine_and_service_reasons_are_the_registry_sentences() -> None:
    from app.property_watch import engine
    from app.services import property_watches as svc

    assert engine.REASON_RECOVERED == "condition recovered"
    assert engine.REASON_VERSION == "watch definition changed"
    assert engine.REASON_RETIRED == "value retired; no longer evaluated"
    assert svc.REASON_WATCH_DELETED == "the watch was deleted"
    assert svc.REASON_WATCH_DISABLED == "watch disabled; no longer evaluated"
    assert svc.REASON_HISTORY_SWITCHED_OFF == "history reading is switched off"
    assert svc.REASON_HISTORY_STARTS == "the reading starts at the next collection"
    assert render(svc.MESSAGE_HISTORY_UNREADABLE) == (
        "the stored history progress could not be read back"
    )
    assert engine.MESSAGE_VERSION.code == "pw.transition.definition_changed"
    assert svc.MESSAGE_WATCH_DISABLED.code == "pw.transition.watch_disabled"


def test_the_status_sentences_of_the_collector_and_the_engine() -> None:
    from app.collectors import pw_collector
    from app.property_watch import engine

    assert pw_collector._NOT_APPLICABLE_REASON == (
        "the property parsed on no event in scope; the values are not measured"
    )
    assert render(engine._STATUS_OPEN) == "one or more value incidents are open"
    assert render(engine._STATUS_RECOVERING) == "one or more value incidents are recovering"
    assert render(engine._STATUS_EVALUATED) == "the latest collected bucket was evaluated"
    latest = MessageRef("pw.status.latest_search_failed", {"bucket": NOW})
    assert render(latest) == (
        "the latest search failed; bucket 2026-10-17T12:00:00+00:00 is stored NOT_COLLECTED"
    )
    assert render(MessageRef("pw.status.disabled_by", {"actor": "detection_engineer"})) == (
        "disabled by detection_engineer"
    )


def test_set_reason_writes_the_text_and_the_code_together_and_clears_both() -> None:
    from app.collectors.pw_collector import _set_reason, _set_scope_reason

    row: dict[str, Any] = {
        "last_status_reason": "an earlier sentence",
        "last_status_reason_i18n": {"code": "pw.status.open", "params": {}},
    }
    _set_reason(row, MessageRef("pw.status.not_applicable"))
    assert row == {
        "last_status_reason": "the property parsed on no event in scope; the values are not "
        "measured",
        "last_status_reason_i18n": {"code": "pw.status.not_applicable", "params": {}},
    }
    _set_reason(row, None)
    assert row == {"last_status_reason": None, "last_status_reason_i18n": None}
    scope = _scope_cases()[0][0]
    _set_scope_reason(row, scope)
    assert row["last_status_reason"] == "the log source is not in the inventory"
    assert row["last_status_reason_i18n"] == {"code": "pw.scope.source_missing", "params": {}}


_READING = "Reading history from QRadar: {} days; nothing is judged until it finishes"


@pytest.mark.parametrize(
    ("done", "total", "shown"),
    [
        (0.0, 7.0, "0.0 of 7.0"),
        (3.46, 7.0, "3.5 of 7.0"),
        (3.44, 6.95, "3.4 of 7.0"),
        (0.04, 0.04, "0.0 of 0.0"),
        (28.0, 28.0, "28.0 of 28.0"),
    ],
)
def test_the_reading_status_keeps_the_one_decimal_of_the_old_sentence(
    done: float, total: float, shown: str
) -> None:
    ref = MessageRef("pw.status.reading_history", {"done": done, "total": total})
    assert render(ref) == _READING.format(shown)
