"""Every §F.5 exit of the pure custom-rule fold, plus the bucket walk."""

from __future__ import annotations

import math
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.custom_rules.evaluate import (
    BreachState,
    BucketFacts,
    FoldInput,
    FoldOutput,
    InterpretedReading,
    RuleStatus,
    collection_silence,
    fold_rule,
    interpret,
    latest_closed_bucket,
    stale_bound_seconds,
    walk_buckets,
)
from app.models.enums import CustomRuleMetric

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _at(minute: int, hour: int = 10) -> datetime:
    return datetime(2026, 7, 15, hour, minute, tzinfo=UTC)


def _reading(
    minute: int,
    value: float | None,
    measurement: str | None = "EXACT",
    *,
    maintenance: bool = False,
    reason: str | None = None,
) -> InterpretedReading:
    return InterpretedReading(_at(minute), value, measurement, maintenance, reason)


def _fold(**overrides: object) -> FoldOutput:
    spec = FoldInput(
        rule_id=uuid.uuid4(),
        rule_name="high eps",
        severity="HIGH",
        metric=CustomRuleMetric.EVENT_COUNT,
        operator="GT",
        threshold_value=100.0,
        sustain_buckets=1,
        recovery_buckets=2,
        consecutive_breaching=0,
        consecutive_under=0,
        last_evaluated_bucket_start=None,
        previous_status=None,
        previous_reason=None,
        subject_kind="LOG_SOURCE",
        subject_label="edge-proxy",
        platform_enabled=True,
        enabled=True,
        retired=False,
        retired_reason=None,
        family_collected=True,
        family_reason=None,
        breach=None,
        readings=(),
        now=NOW,
    )
    return fold_rule(replace(spec, **overrides))  # type: ignore[arg-type]


def _live(**overrides: object) -> BreachState:
    breach = BreachState(
        id=uuid.uuid4(),
        state="OPEN",
        first_bucket_start=_at(0),
        last_bucket_start=_at(0),
        opened_at=NOW,
        recovering_at=None,
        resolved_at=None,
        worst_value=150.0,
        worst_measurement="EXACT",
        last_value=150.0,
        last_measurement="EXACT",
        unmeasured_buckets=0,
        subject_label="edge-proxy",
        details={"metric": "event_count"},
    )
    return replace(breach, **overrides)  # type: ignore[arg-type]


def _facts(**overrides: object) -> BucketFacts:
    return replace(BucketFacts(bucket_start=_at(0), present=True), **overrides)  # type: ignore[arg-type]


def test_every_rule_status_fits_the_column() -> None:
    for status in RuleStatus:
        assert len(status.value) <= 16
    names = {status.value for status in RuleStatus}
    assert "INSUFFICIENT_DATA" not in names
    assert {"NOT_ENABLED", "SUBJECT_RETIRED", "DISABLED", "NOT_COLLECTED", "STALE"} <= names


def test_sustain_opens_on_the_bucket_where_it_is_met_and_not_before() -> None:
    result = _fold(
        sustain_buckets=3,
        readings=(_reading(0, 150), _reading(5, 160), _reading(10, 170)),
    )
    assert result.breach is not None
    assert result.breach.state == "OPEN"
    assert result.breach.is_new
    assert result.breach.first_bucket_start == _at(10)
    assert result.breach.worst_value == 170
    assert [event.action for event in result.events] == ["open"]
    assert result.status == "OPEN"
    assert result.consecutive_breaching == 3


def test_recovery_resolves_only_after_consecutive_exact_unders() -> None:
    live = _live()
    result = _fold(
        recovery_buckets=2,
        breach=live,
        consecutive_breaching=3,
        last_evaluated_bucket_start=_at(0),
        readings=(_reading(5, 10), _reading(10, 20)),
    )
    assert result.breach is None
    assert result.resolved is not None
    assert result.resolved.state == "RESOLVED"
    assert result.resolved.resolved_at == NOW
    assert result.resolved.id == live.id
    assert [event.action for event in result.events] == ["resolve"]
    assert result.events[0].reason == "condition recovered"
    assert result.consecutive_breaching == 0
    assert result.consecutive_under == 0
    assert result.status == "OK"


def test_one_exact_under_enters_recovery_without_an_alert() -> None:
    result = _fold(
        recovery_buckets=2,
        breach=_live(),
        last_evaluated_bucket_start=_at(0),
        readings=(_reading(5, 10),),
    )
    assert result.breach is not None
    assert result.breach.state == "RECOVERING"
    assert result.breach.recovering_at == NOW
    assert result.events == ()
    assert result.status == "RECOVERING"


def test_a_breach_during_recovery_returns_to_open_and_refreshes() -> None:
    live = _live(state="RECOVERING", recovering_at=NOW)
    result = _fold(
        breach=live,
        consecutive_under=1,
        last_evaluated_bucket_start=_at(0),
        readings=(_reading(5, 200),),
    )
    assert result.breach is not None
    assert result.breach.state == "OPEN"
    assert result.breach.recovering_at is None
    assert [event.action for event in result.events] == ["refresh"]
    assert result.consecutive_under == 0


def test_unmeasured_advances_neither_counter_and_is_not_a_zero() -> None:
    live = _live()
    result = _fold(
        breach=live,
        consecutive_breaching=3,
        last_evaluated_bucket_start=_at(0),
        readings=(_reading(5, None, None, reason="no row for this subject in the bucket"),),
    )
    assert result.consecutive_breaching == 3
    assert result.consecutive_under == 0
    assert result.breach is not None
    assert result.breach.state == "OPEN"
    assert result.breach.unmeasured_buckets == 1
    assert result.events == ()
    assert result.status == "OPEN"
    # The caller's object is not the one the fold mutates.
    assert live.unmeasured_buckets == 0

    idle = _fold(readings=(_reading(0, None, None, reason="no row"),))
    assert idle.breach is None
    assert idle.status == "NO_VERDICT"
    assert idle.status != "INSUFFICIENT_DATA"
    assert idle.consecutive_breaching == 0
    assert idle.consecutive_under == 0


def test_an_unmeasured_bucket_pauses_a_streak_instead_of_resetting_it() -> None:
    result = _fold(
        sustain_buckets=3,
        readings=(
            _reading(0, 150),
            _reading(5, None, None, reason="no row"),
            _reading(10, 150),
        ),
    )
    assert result.breach is None
    assert result.consecutive_breaching == 2
    assert result.events == ()


def test_lower_bound_above_a_gt_line_opens_and_one_below_it_does_not_resolve() -> None:
    opened = _fold(
        metric=CustomRuleMetric.REQUEST_COUNT,
        readings=(_reading(0, 150, "LOWER_BOUND"),),
    )
    assert opened.breach is not None
    assert opened.breach.state == "OPEN"
    assert opened.breach.worst_measurement == "LOWER_BOUND"
    assert [event.action for event in opened.events] == ["open"]

    held = _fold(
        metric=CustomRuleMetric.REQUEST_COUNT,
        breach=opened.breach,
        consecutive_breaching=opened.consecutive_breaching,
        last_evaluated_bucket_start=opened.last_evaluated_bucket_start,
        readings=(_reading(5, 1, "LOWER_BOUND"),),
    )
    assert held.breach is not None
    assert held.breach.state == "OPEN"
    assert held.breach.resolved_at is None
    assert held.breach.unmeasured_buckets == 1
    assert held.consecutive_breaching == opened.consecutive_breaching
    assert held.consecutive_under == 0
    assert held.events == ()
    assert "proves nothing" in (held.status_reason or "")


def test_lower_bound_with_lt_is_insufficient_and_does_not_open() -> None:
    result = _fold(
        metric=CustomRuleMetric.REQUEST_COUNT,
        operator="LT",
        threshold_value=100.0,
        readings=(_reading(0, 1, "LOWER_BOUND"),),
    )
    assert result.breach is None
    assert result.consecutive_breaching == 0
    assert result.consecutive_under == 0
    assert result.status == "NO_VERDICT"
    assert "under" in (result.status_reason or "")


def test_exact_only_metric_refuses_a_lower_bound_even_for_gt() -> None:
    """The catalog, not the fold, decides that event_count cannot use a lower bound."""
    result = _fold(
        metric=CustomRuleMetric.EVENT_COUNT,
        readings=(_reading(0, 10_000, "LOWER_BOUND"),),
    )
    assert result.breach is None
    assert result.status == "NO_VERDICT"
    assert result.consecutive_breaching == 0


def test_open_and_resolve_in_one_run_is_one_resolved_row_and_both_events() -> None:
    result = _fold(
        sustain_buckets=1,
        recovery_buckets=1,
        readings=(_reading(0, 150), _reading(5, 10)),
    )
    assert result.breach is None
    assert result.resolved is not None
    assert result.resolved.is_new
    assert result.resolved.state == "RESOLVED"
    assert result.resolved.first_bucket_start == _at(0)
    assert [event.action for event in result.events] == ["open", "resolve"]
    assert result.consecutive_breaching == 0
    assert result.consecutive_under == 0


def test_re_evaluation_of_an_already_judged_bucket_is_a_no_op() -> None:
    first = _fold(readings=(_reading(0, 150),))
    assert first.breach is not None
    again = _fold(
        breach=first.breach,
        consecutive_breaching=first.consecutive_breaching,
        last_evaluated_bucket_start=first.last_evaluated_bucket_start,
        previous_status="OPEN",
        readings=(_reading(0, 150),),
    )
    assert again.judged == 0
    assert again.events == ()
    assert again.preserve_status
    assert again.status == "OPEN"
    assert again.breach is not None
    assert again.breach.id == first.breach.id
    assert again.consecutive_breaching == first.consecutive_breaching


def test_a_retired_subject_draws_no_verdict_and_does_not_move_the_cursor() -> None:
    result = _fold(
        retired=True,
        retired_reason="the endpoint was retired at 2026-07-01T00:00:00+00:00",
        consecutive_breaching=2,
        readings=(_reading(0, 10_000), _reading(5, 0)),
    )
    assert result.status == "SUBJECT_RETIRED"
    assert "retired at" in (result.status_reason or "")
    assert result.consecutive_breaching == 2
    assert result.last_evaluated_bucket_start is None
    assert result.breach is None
    assert result.events == ()
    assert result.judged == 0


def test_a_disabled_rule_keeps_its_counters_and_its_breach() -> None:
    live = _live()
    result = _fold(
        enabled=False,
        breach=live,
        consecutive_breaching=4,
        consecutive_under=1,
        last_evaluated_bucket_start=_at(0),
        readings=(_reading(5, 10_000), _reading(10, 0)),
    )
    assert result.status == "DISABLED"
    assert result.status_reason == "the rule is disabled"
    assert result.consecutive_breaching == 4
    assert result.consecutive_under == 1
    assert result.last_evaluated_bucket_start == _at(0)
    assert result.breach is not None
    assert result.breach.state == "OPEN"
    assert result.events == ()


def test_platform_off_is_not_enabled_and_ignores_readings() -> None:
    result = _fold(platform_enabled=False, readings=(_reading(0, 10_000),))
    assert result.status == "NOT_ENABLED"
    assert "CUSTOM_RULES_ENABLED" in (result.status_reason or "")
    assert result.breach is None
    assert result.events == ()
    assert result.judged == 0


def test_a_family_that_is_off_is_not_collected_and_names_the_setting() -> None:
    result = _fold(
        family_collected=False,
        family_reason="waf_collection_enabled is off",
        readings=(_reading(0, 10_000),),
    )
    assert result.status == "NOT_COLLECTED"
    assert result.status_reason == "waf_collection_enabled is off"
    assert result.breach is None
    assert result.events == ()


def test_maintenance_does_not_open_refresh_or_resolve() -> None:
    quiet = _fold(readings=(_reading(0, 10_000, maintenance=True),))
    assert quiet.breach is None
    assert quiet.consecutive_breaching == 0
    assert quiet.status == "NO_VERDICT"
    assert quiet.status_reason == "the source is in maintenance"
    assert quiet.events == ()

    live = _live()
    held = _fold(
        breach=live,
        consecutive_breaching=2,
        last_evaluated_bucket_start=_at(0),
        readings=(_reading(5, 0, maintenance=True), _reading(10, 10_000, maintenance=True)),
    )
    assert held.breach is not None
    assert held.breach.state == "OPEN"
    assert held.breach.unmeasured_buckets == 0
    assert held.consecutive_breaching == 2
    assert held.consecutive_under == 0
    assert held.events == ()
    assert held.last_evaluated_bucket_start == _at(10)


def test_unknown_operator_and_a_non_finite_threshold_do_not_resolve() -> None:
    live = _live()
    unknown = _fold(
        operator="NE",
        breach=live,
        last_evaluated_bucket_start=_at(0),
        readings=(_reading(5, 0),),
    )
    assert unknown.breach is not None
    assert unknown.breach.state == "OPEN"
    assert unknown.events == ()
    assert unknown.consecutive_under == 0
    assert unknown.breach.unmeasured_buckets == 1

    infinite = _fold(
        threshold_value=math.inf,
        breach=_live(),
        last_evaluated_bucket_start=_at(0),
        readings=(_reading(5, 0),),
    )
    assert infinite.breach is not None
    assert infinite.breach.state == "OPEN"
    assert infinite.events == ()
    assert infinite.status_reason == "threshold_value is not finite"


def test_eq_is_exact_and_worst_follows_the_operator() -> None:
    equal = _fold(operator="EQ", threshold_value=100.0, readings=(_reading(0, 100.0),))
    assert equal.breach is not None
    off = _fold(operator="EQ", threshold_value=100.0, readings=(_reading(0, 100.0000001),))
    assert off.breach is None
    assert off.status == "OK"

    higher = _fold(
        operator="EQ",
        threshold_value=100.0,
        breach=equal.breach,
        consecutive_breaching=1,
        last_evaluated_bucket_start=_at(0),
        readings=(_reading(5, 100.0),),
    )
    assert higher.breach is not None
    assert higher.breach.worst_value == 100.0

    grew = _fold(readings=(_reading(0, 150),))
    grew_more = _fold(
        breach=grew.breach,
        consecutive_breaching=1,
        last_evaluated_bucket_start=_at(0),
        readings=(_reading(5, 400), _reading(10, 200)),
    )
    assert grew_more.breach is not None
    assert grew_more.breach.worst_value == 400
    assert grew_more.breach.last_value == 200

    fell = _fold(operator="LT", readings=(_reading(0, 40),))
    fell_more = _fold(
        operator="LT",
        breach=fell.breach,
        consecutive_breaching=1,
        last_evaluated_bucket_start=_at(0),
        readings=(_reading(5, 10),),
    )
    assert fell_more.breach is not None
    assert fell_more.breach.worst_value == 10


def test_silence_just_under_the_bound_preserves_status_and_just_over_is_stale() -> None:
    """The bound is cadence + bucket + settle + the one-minute scheduler tolerance.

    A lag of exactly that many seconds is still a collection that can be in
    flight. One second more is a stopped collector, and the reason names the
    last judged bucket and its wall-clock age.
    """
    bound = stale_bound_seconds(3600, 120, 300)
    assert bound == 3600 + 300 + 120 + 60
    watermark = datetime(2026, 10, 16, 8, 55, tzinfo=UTC)
    now = datetime(2026, 10, 16, 12, 5, tzinfo=UTC)
    under = collection_silence(
        watermark=watermark,
        closed=watermark + timedelta(seconds=bound),
        now=now,
        bound_seconds=bound,
    )
    assert under is None
    kept = _fold(
        previous_status="OK",
        previous_reason="kept",
        collection_stale_reason=under,
        readings=(),
    )
    assert kept.preserve_status
    assert kept.status == "OK"
    assert kept.status_reason == "kept"
    assert kept.judged == 0
    assert kept.events == ()

    reason = collection_silence(
        watermark=watermark,
        closed=watermark + timedelta(seconds=bound + 1),
        now=now,
        bound_seconds=bound,
    )
    expected = "no bucket collected for this kind since 2026-10-16 08:55 UTC (3 h 10 min)"
    assert reason == expected
    stale = _fold(
        previous_status="OK",
        previous_reason="kept",
        collection_stale_reason=reason,
        readings=(_reading(0, 1),),
    )
    assert stale.status == "STALE"
    assert stale.status_reason == expected
    assert stale.preserve_status is False
    assert stale.judged == 0
    assert stale.events == ()
    assert stale.consecutive_breaching == 0
    assert stale.consecutive_under == 0


def test_a_stale_tick_keeps_an_open_breach_and_does_not_resolve() -> None:
    """An under reading passed with the stale reason is not judged.

    Recovery needs exact unders. A stopped collector is not one, and the
    alert seam is not told to resolve.
    """
    breach = _live(unmeasured_buckets=4)
    result = _fold(
        breach=breach,
        consecutive_breaching=3,
        consecutive_under=1,
        previous_status="OPEN",
        last_evaluated_bucket_start=_at(0),
        collection_stale_reason=(
            "no bucket collected for this kind since 2026-10-16 08:55 UTC (3 h 10 min)"
        ),
        readings=(_reading(20, 1),),
    )
    assert result.status == "STALE"
    assert result.resolved is None
    assert result.events == ()
    assert result.breach is not None
    assert result.breach.state == "OPEN"
    assert result.breach.unmeasured_buckets == 4
    assert result.breach is not breach
    assert result.consecutive_breaching == 3
    assert result.consecutive_under == 1
    assert result.last_evaluated_bucket_start == _at(0)
    assert result.judged == 0


def test_an_idle_tick_preserves_the_previous_status() -> None:
    result = _fold(previous_status="OK", previous_reason=None, readings=())
    assert result.judged == 0
    assert result.preserve_status
    assert result.status == "OK"
    assert result.events == ()


def test_latest_closed_bucket_is_the_one_before_the_bucket_in_progress() -> None:
    day = datetime(2026, 7, 15, tzinfo=UTC)
    assert latest_closed_bucket(day.replace(hour=12, minute=0), 300) == day.replace(
        hour=11, minute=55
    )
    assert latest_closed_bucket(day.replace(hour=12, minute=7), 300) == day.replace(hour=12)
    assert latest_closed_bucket(day.replace(hour=12, minute=5), 300) == day.replace(hour=12)
    assert latest_closed_bucket(
        day.replace(hour=12, minute=4, second=59), 300
    ) == day.replace(hour=11, minute=55)
    with pytest.raises(ValueError):
        latest_closed_bucket(datetime(2026, 7, 15, 12, 0), 300)
    with pytest.raises(ValueError):
        latest_closed_bucket(day.replace(hour=12), 0)


def test_walk_buckets_fills_aligned_gaps_and_does_not_invent_a_grid() -> None:
    watermark = _at(0)
    closed = _at(15)
    assert walk_buckets(
        [_at(15)], watermark=watermark, interval_seconds=300, limit=10, closed=closed
    ) == [_at(5), _at(10), _at(15)]
    # The limit stops mid-gap. The next run continues from the new watermark.
    assert walk_buckets(
        [_at(15)], watermark=watermark, interval_seconds=300, limit=2, closed=closed
    ) == [_at(5), _at(10)]
    assert walk_buckets(
        [_at(5), _at(15)],
        watermark=watermark,
        interval_seconds=300,
        limit=10,
        closed=closed,
    ) == [_at(5), _at(10), _at(15)]
    # Not an integer multiple of the interval: do not invent the points between.
    assert walk_buckets(
        [_at(7)], watermark=watermark, interval_seconds=300, limit=10, closed=_at(7)
    ) == [_at(7)]
    assert walk_buckets(
        [_at(0), _at(20)],
        watermark=watermark,
        interval_seconds=300,
        limit=10,
        closed=closed,
    ) == []
    assert walk_buckets(
        [], watermark=watermark, interval_seconds=300, limit=10, closed=closed
    ) == []
    assert walk_buckets(
        [_at(5)], watermark=watermark, interval_seconds=300, limit=10, closed=_at(30)
    ) == [_at(5)]


def test_no_row_is_unmeasured_and_a_partial_phase_a_bucket_proves_nothing() -> None:
    missing = interpret(
        CustomRuleMetric.EVENT_COUNT, BucketFacts(bucket_start=_at(0), present=False)
    )
    assert missing.value is None
    assert missing.reason == "no row for this subject in the bucket"

    partial = interpret(
        CustomRuleMetric.AVERAGE_EPS,
        _facts(completeness="PARTIAL", average_eps=0.0, event_count=0),
    )
    assert partial.value is None
    assert partial.reason == "the bucket is PARTIAL"

    exact = interpret(
        CustomRuleMetric.EVENT_COUNT,
        _facts(completeness="COMPLETE", event_count=0, average_eps=0.0),
    )
    assert exact.value == 0.0
    assert exact.measurement == "EXACT"


def test_fleet_eps_is_exact_only_when_every_monitored_source_is_complete() -> None:
    exact = interpret(
        CustomRuleMetric.EPS,
        _facts(class_events=300, class_seconds=600, complete_sources=2, expected_sources=2),
    )
    assert exact.measurement == "EXACT"
    assert exact.value == 0.5

    lower = interpret(
        CustomRuleMetric.EPS,
        _facts(class_events=0, class_seconds=300, complete_sources=1, expected_sources=2),
    )
    assert lower.measurement == "LOWER_BOUND"
    assert lower.value == 0.0

    empty = interpret(
        CustomRuleMetric.EPS,
        _facts(class_events=0, class_seconds=0, complete_sources=0, expected_sources=2),
    )
    assert empty.value is None
    assert "no COMPLETE" in (empty.reason or "")

    nobody = interpret(
        CustomRuleMetric.EPS,
        _facts(expected_sources=0, complete_sources=0, class_seconds=0),
    )
    assert "no enabled, monitored source" in (nobody.reason or "")


def test_ratios_need_an_exact_denominator_and_an_unmapped_remainder_lowers_the_numerator() -> None:
    lowered = interpret(
        CustomRuleMetric.BLOCK_RATIO,
        _facts(
            blocked_count=10,
            action_events=20,
            unmapped_action_count=3,
            action_measurement="EXACT",
        ),
    )
    assert lowered.measurement == "LOWER_BOUND"
    assert lowered.value == 0.5

    exact = interpret(
        CustomRuleMetric.BLOCK_RATIO,
        _facts(
            blocked_count=10,
            action_events=20,
            unmapped_action_count=0,
            action_measurement="EXACT",
        ),
    )
    assert exact.measurement == "EXACT"
    assert exact.value == 0.5

    bound = interpret(
        CustomRuleMetric.SERVER_ERROR_RATIO,
        _facts(
            server_error_count=5,
            response_code_events=10,
            unclassified_response_count=0,
            response_code_measurement="LOWER_BOUND",
        ),
    )
    assert bound.value is None
    assert bound.reason == "the response-code denominator is a lower bound"

    zero = interpret(
        CustomRuleMetric.CLIENT_ERROR_RATIO,
        _facts(
            client_error_count=0,
            response_code_events=0,
            unclassified_response_count=0,
            response_code_measurement="EXACT",
        ),
    )
    assert zero.value is None
    assert zero.reason == "the response-code denominator is zero"

    dark = interpret(
        CustomRuleMetric.BLOCK_RATIO,
        _facts(action_measurement="NOT_COLLECTED"),
    )
    assert dark.value is None
    assert dark.reason == "the action breakdown was not collected"

    opened = _fold(
        metric=CustomRuleMetric.BLOCK_RATIO,
        operator="GT",
        threshold_value=0.2,
        subject_kind="WAF_ENDPOINT",
        readings=(lowered,),
    )
    assert opened.breach is not None
    refused = _fold(
        metric=CustomRuleMetric.BLOCK_RATIO,
        operator="LT",
        threshold_value=0.9,
        subject_kind="WAF_ENDPOINT",
        readings=(lowered,),
    )
    assert refused.breach is None
    assert refused.status == "NO_VERDICT"


def test_request_count_uses_the_bucket_measurement_and_ips_counts_fail_closed() -> None:
    unknown = interpret(CustomRuleMetric.REQUEST_COUNT, _facts(request_count=12))
    assert unknown.value is None
    assert "does not state" in (unknown.reason or "")

    lower = interpret(
        CustomRuleMetric.REQUEST_COUNT,
        _facts(request_count=12, request_count_measurement="LOWER_BOUND"),
    )
    assert lower.measurement == "LOWER_BOUND"
    assert lower.value == 12

    entity = interpret(
        CustomRuleMetric.EVENTS,
        _facts(events=4, dimension_measurement="NOT_COLLECTED"),
    )
    assert entity.value is None
    assert entity.reason == "the dimension was not collected"

    total = interpret(CustomRuleMetric.TOTAL_EVENTS, _facts(total_events=0))
    assert total.measurement == "EXACT"
    assert total.value == 0.0
    absent = interpret(
        CustomRuleMetric.TOTAL_EVENTS,
        BucketFacts(bucket_start=_at(0), present=False, total_events=0),
    )
    assert absent.value is None

    share = interpret(
        CustomRuleMetric.BLOCKED_SHARE,
        _facts(
            blocked_events=2,
            allowed_events=6,
            unmapped_events=2,
            action_measurement="EXACT",
        ),
    )
    assert share.measurement == "LOWER_BOUND"
    assert share.value == 0.2


def test_a_stored_maintenance_flag_is_maintenance_rather_than_a_reading() -> None:
    reading = interpret(
        CustomRuleMetric.TOTAL_EVENTS,
        _facts(total_events=80, in_maintenance=True),
    )
    assert reading.in_maintenance
    assert reading.reason == "the source is in maintenance"
