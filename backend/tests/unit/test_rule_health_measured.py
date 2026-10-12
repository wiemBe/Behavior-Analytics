"""Rule health with measured rule matches (Phase E.1a), through `classify`.

The invariants:

  * EVENT rules: a measured zero is a real zero only over COMPLETE coverage.
  * COMMON rules: matches prove firing, never silence.
  * Every other type, and "match collector never ran": exactly as before.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.config import get_settings
from app.models.enums import RuleHealthStatus
from app.rules.matches import CoverageState, MatchCoverage, MatchEvidence
from app.services.rule_health import MeasuredMatches, RuleHealthEvaluator
from tests.unit.test_rule_health import NOW, make_rule, settings

COMPLETE = MatchCoverage(CoverageState.COMPLETE, expected=720, complete=720)
INCOMPLETE = MatchCoverage(CoverageState.INCOMPLETE, expected=720, complete=719, failed=1)
NONE = MatchCoverage(CoverageState.NONE)


def classify(rule, matches, *, observation_complete: bool = False, trigger_count: int = 0,
             cfg=None):
    evaluator = RuleHealthEvaluator(
        session=None,  # type: ignore[arg-type]
        settings=cfg or settings(),
        clock=lambda: NOW,
    )
    return evaluator.classify(
        rule,
        now=NOW,
        window_start=NOW - timedelta(days=30),
        trigger_count=trigger_count,
        offense_count=0,
        dependencies=[],
        building_block_enabled={},
        log_source_type_healthy={},
        observation_complete=observation_complete,
        matches=matches,
    )


def measured(evidence, coverage, events=0, last=None) -> MeasuredMatches:
    return MeasuredMatches(evidence, coverage, events, last)


class TestEventRules:
    def test_absent_from_every_complete_window_is_never_observed(self) -> None:
        verdict = classify(make_rule(), measured(MatchEvidence.EXACT, COMPLETE))

        assert verdict.status == RuleHealthStatus.NEVER_OBSERVED
        assert verdict.evidence["match_coverage"] == "complete"
        assert verdict.trigger_count == 0

    def test_absent_with_one_failed_window_is_insufficient_data(self) -> None:
        verdict = classify(make_rule(), measured(MatchEvidence.EXACT, INCOMPLETE))

        assert verdict.status == RuleHealthStatus.INSUFFICIENT_DATA
        assert "incomplete" in verdict.reason

    def test_incomplete_coverage_overrides_a_legacy_complete_observation(self) -> None:
        """The measured path may only be stricter than the old one."""
        verdict = classify(
            make_rule(), measured(MatchEvidence.EXACT, INCOMPLETE), observation_complete=True
        )

        assert verdict.status == RuleHealthStatus.INSUFFICIENT_DATA

    def test_many_measured_matches_above_the_rate_are_noisy(self) -> None:
        verdict = classify(
            make_rule(expected_daily_firings=100.0),
            measured(MatchEvidence.EXACT, COMPLETE, events=30 * 100 * 3 + 30),
        )

        assert verdict.status == RuleHealthStatus.NOISY
        assert verdict.trigger_count == 30 * 100 * 3 + 30

    def test_measured_matches_below_the_rate_are_healthy(self) -> None:
        verdict = classify(
            make_rule(), measured(MatchEvidence.EXACT, COMPLETE, events=40, last=NOW)
        )

        assert verdict.status == RuleHealthStatus.HEALTHY
        assert verdict.trigger_count == 40

    def test_the_measured_count_replaces_the_inferred_one(self) -> None:
        verdict = classify(
            make_rule(), measured(MatchEvidence.EXACT, COMPLETE, events=7, last=NOW),
            trigger_count=2,
        )

        assert verdict.trigger_count == 7
        assert verdict.evidence["metric_kind"] == "direct"

    def test_incomplete_coverage_still_proves_firing_as_a_lower_bound(self) -> None:
        verdict = classify(
            make_rule(), measured(MatchEvidence.EXACT, INCOMPLETE, events=5, last=NOW)
        )

        assert verdict.status == RuleHealthStatus.HEALTHY
        assert verdict.trigger_count == 5
        assert verdict.evidence["metric_kind"] == "measured_lower_bound"

    def test_a_recent_measured_match_overrides_a_stale_offense_based_last_fired(self) -> None:
        stale = make_rule(last_fired_at=NOW - timedelta(days=25))

        verdict = classify(
            stale, measured(MatchEvidence.EXACT, COMPLETE, events=3, last=NOW - timedelta(hours=2))
        )

        assert verdict.status == RuleHealthStatus.HEALTHY
        assert verdict.last_triggered_at == NOW - timedelta(hours=2)

    def test_the_grace_period_still_comes_first(self) -> None:
        verdict = classify(make_rule(age_days=2), measured(MatchEvidence.EXACT, COMPLETE))

        assert verdict.status == RuleHealthStatus.INSUFFICIENT_DATA

    def test_disabled_still_comes_first(self) -> None:
        verdict = classify(make_rule(enabled=False), measured(MatchEvidence.EXACT, COMPLETE))

        assert verdict.status == RuleHealthStatus.DISABLED


class TestCommonRules:
    def test_absence_is_never_never_observed_even_over_complete_coverage(self) -> None:
        verdict = classify(make_rule(), measured(MatchEvidence.LOWER_BOUND, COMPLETE))

        assert verdict.status == RuleHealthStatus.INSUFFICIENT_DATA
        assert verdict.status != RuleHealthStatus.NEVER_OBSERVED

    def test_matches_prove_firing(self) -> None:
        verdict = classify(
            make_rule(), measured(MatchEvidence.LOWER_BOUND, COMPLETE, events=12, last=NOW)
        )

        assert verdict.status == RuleHealthStatus.HEALTHY
        assert verdict.trigger_count == 12
        assert verdict.evidence["metric_kind"] == "measured_lower_bound"
        assert "At least 12" in verdict.reason

    def test_a_lower_bound_above_the_rate_is_noisy(self) -> None:
        verdict = classify(
            make_rule(expected_daily_firings=10.0),
            measured(MatchEvidence.LOWER_BOUND, COMPLETE, events=30 * 10 * 3 + 30),
        )

        assert verdict.status == RuleHealthStatus.NOISY

    def test_complete_coverage_never_flips_the_observation_flag_for_common(self) -> None:
        """Only an EXACT rule's COMPLETE coverage may declare the span observed."""
        verdict = classify(make_rule(), measured(MatchEvidence.LOWER_BOUND, COMPLETE))

        assert verdict.evidence["observation_status"] == "never_run"


class TestOtherTypes:
    @pytest.mark.parametrize("coverage", [COMPLETE, INCOMPLETE, NONE])
    def test_not_measured_types_are_judged_exactly_as_before(self, coverage) -> None:
        rule = make_rule(last_fired_at=NOW - timedelta(days=3))
        before = classify(rule, None, observation_complete=False, trigger_count=0)
        after = classify(
            rule, measured(MatchEvidence.NOT_MEASURED, coverage, events=999),
            observation_complete=False, trigger_count=0,
        )

        assert (after.status, after.reason, after.trigger_count, after.evidence) == (
            before.status, before.reason, before.trigger_count, before.evidence,
        )

    def test_a_silent_not_measured_rule_is_never_never_observed(self) -> None:
        verdict = classify(make_rule(), measured(MatchEvidence.NOT_MEASURED, COMPLETE))

        assert verdict.status == RuleHealthStatus.INSUFFICIENT_DATA

    def test_no_match_windows_at_all_leaves_event_rules_unchanged(self) -> None:
        rule = make_rule()
        before = classify(rule, None)
        after = classify(rule, measured(MatchEvidence.EXACT, NONE))

        assert (after.status, after.reason, after.evidence) == (
            before.status, before.reason, before.evidence,
        )


def test_the_logic_version_is_bumped() -> None:
    assert get_settings().rule_health_logic_version == 2
