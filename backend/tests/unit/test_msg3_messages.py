"""MSG-3: the rule-health reasons keep today's English byte for byte.

Every converted branch of `classify` renders through the registry. The expected text below is
written out the way the evaluator built it before the registry (f-strings and literals), so an
edit to a template, or to the rounding of a number, that changes English fails here. Each verdict
also carries the `MessageRef` it was rendered from, and that reference round-trips to the wire.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import ClassVar

import pytest

from app.i18n.messages import REGISTRY, MessageRef, render
from app.models.enums import RuleDependencyKind, RuleHealthStatus
from app.rules.matches import (
    CoverageState,
    FailedWindowFacts,
    MatchCoverage,
    MatchEvidence,
)
from app.services.rule_health import MeasuredMatches, RuleHealthEvaluator
from tests.unit.test_rule_health import NOW, classify, dependency, make_rule, settings


def _utc(value: datetime) -> str:
    """The old house timestamp of a reason sentence."""
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def _legacy_incomplete(matches: MeasuredMatches | None) -> str:
    """`_incomplete_coverage_reason` as it was written before the registry."""
    if (
        matches is None
        or matches.coverage.state == CoverageState.NONE
        or matches.evidence != MatchEvidence.EXACT
    ):
        return (
            "No completed rule-metric collection covers the evaluation "
            "window, so the absence of observed firings is not evidence "
            "that the rule has never fired."
        )
    coverage = matches.coverage
    counts = f"({coverage.complete} of {coverage.expected} windows are COMPLETE)"
    head = (
        "Measured rule-match coverage of the evaluation window is "
        f"incomplete {counts}, so the absence of measured matches is not "
        "evidence that the rule has never fired."
    )
    facts = matches.failed_facts
    if facts is None:
        return head
    causes: list[str] = []
    if facts.failed:
        noun = "window" if facts.failed == 1 else "windows"
        clause = f"{facts.failed} {noun} failed to collect"
        if facts.last_error_class:
            clause += f" (latest error class: {facts.last_error_class})"
        causes.append(clause)
    if facts.skipped:
        noun = "window" if facts.skipped == 1 else "windows"
        verb = "was" if facts.skipped == 1 else "were"
        causes.append(
            f"{facts.skipped} {noun} {verb} skipped beyond the catch-up horizon and never searched"
        )
    span = f"{_utc(facts.first_start)} UTC"
    if facts.last_start != facts.first_start:
        span += f" to {_utc(facts.last_start)} UTC"
    affected = "window" if facts.first_start == facts.last_start else "windows"
    return (
        "Measured rule-match coverage of the evaluation window is "
        f"incomplete {counts}: "
        + "; ".join(causes)
        + f". Affected {affected}: {span}. The absence of measured matches "
        "is not evidence that the rule has never fired. The evaluation window "
        "stops holding the last affected window at "
        f"{_utc(facts.until)} UTC."
    )


def _wire_round_trip(verdict) -> None:
    """The verdict's reference renders its own text, and its wire form is a plain dict."""
    ref = verdict.reason_message
    assert ref is not None
    assert render(ref) == verdict.reason
    wire = ref.to_wire()
    assert wire["code"] == ref.code
    assert isinstance(wire["params"], dict)


class TestPlainBranches:
    def test_disabled(self) -> None:
        verdict = classify(make_rule(enabled=False))

        assert verdict.reason == "The rule is disabled in QRadar, so it cannot produce detections."
        assert verdict.reason_message == MessageRef("rule_health.disabled")
        _wire_round_trip(verdict)

    def test_age_unknown(self) -> None:
        verdict = classify(make_rule(age_days=None))

        assert verdict.reason == "The rule's age is unknown, so inactivity cannot be assessed."
        assert verdict.reason_message == MessageRef("rule_health.age_unknown")

    def test_below_the_minimum_history(self) -> None:
        cfg = settings(rule_health_grace_period_days=0, rule_health_min_history_days=10)

        verdict = classify(make_rule(age_days=5), cfg=cfg)

        assert verdict.reason == "Not enough observed history to classify this rule."
        assert verdict.reason_message == MessageRef("rule_health.min_history")


class TestGrace:
    #: 0.05 and 1.25 are the rounding edges: Python gives 0.1 and 1.2, a half-up formatter 0.1
    #: and 1.3. The reference carries the Python result so another locale shows the same digits.
    @pytest.mark.parametrize(
        ("age_days", "shown"),
        [(0.0, "0.0"), (0.05, "0.1"), (1.0, "1.0"), (1.25, "1.2"), (2.5, "2.5"), (6.96, "7.0")],
    )
    def test_the_age_is_rounded_to_one_decimal_as_python_does(
        self, age_days: float, shown: str
    ) -> None:
        cfg = settings(rule_health_grace_period_days=7)

        verdict = classify(make_rule(age_days=age_days), cfg=cfg)

        assert verdict.status == RuleHealthStatus.INSUFFICIENT_DATA
        assert verdict.reason == (
            f"The rule is {age_days:.1f} days old, inside the 7-day grace period."
        )
        assert verdict.reason == f"The rule is {shown} days old, inside the 7-day grace period."
        assert verdict.reason_message is not None
        assert verdict.reason_message.params["age_days"] == float(shown)
        _wire_round_trip(verdict)


class TestDependencies:
    def test_degraded_names_each_dependency_by_name_or_by_fallback(self) -> None:
        verdict = classify(
            make_rule(age_days=200),
            dependencies=[
                dependency(RuleDependencyKind.BUILDING_BLOCK, "77", target_name="BB: Admin Hosts"),
                dependency(RuleDependencyKind.BUILDING_BLOCK, "78"),
                dependency(RuleDependencyKind.LOG_SOURCE_TYPE, "12"),
            ],
            building_block_enabled={"77": False, "78": False},
            log_source_type_healthy={"12": False},
        )

        assert verdict.status == RuleHealthStatus.DEPENDENCY_DEGRADED
        assert verdict.reason == (
            "The rule cannot fire reliably: BB: Admin Hosts is disabled; "
            "building block 78 is disabled; log source type 12 is not delivering events. "
            "Its silence is not evidence about the rule itself."
        )
        assert verdict.missing_dependencies == [
            "BB: Admin Hosts is disabled",
            "building block 78 is disabled",
            "log source type 12 is not delivering events",
        ]
        assert verdict.reason_message is not None
        assert verdict.reason_message.code == "rule_health.dependency_degraded"
        wire = verdict.reason_message.to_wire()
        items = wire["params"]["dependencies"]["items"]  # type: ignore[index]
        assert [i["code"] for i in items] == ["rule_health.dependency.disabled"] * 2 + [
            "rule_health.dependency.not_delivering"
        ]
        assert items[0]["params"]["label"] == {
            "code": "rule_health.label.named",
            "params": {"name": "BB: Admin Hosts"},
        }
        assert items[1]["params"]["label"] == {
            "code": "rule_health.label.building_block",
            "params": {"ref": "78"},
        }
        _wire_round_trip(verdict)

    def test_unobserved_lists_at_most_five_and_evidence_keeps_all(self) -> None:
        deps = [dependency(RuleDependencyKind.BUILDING_BLOCK, str(n)) for n in range(7)]

        verdict = classify(make_rule(age_days=200), dependencies=deps)

        assert verdict.status == RuleHealthStatus.INSUFFICIENT_DATA
        shown = "; ".join(f"building block {n} has not been observed" for n in range(5))
        assert verdict.reason == (
            f"A dependency has not been observed, so the rule cannot be judged: {shown}."
        )
        assert len(verdict.missing_dependencies) == 7
        assert verdict.reason_message is not None
        wire = verdict.reason_message.to_wire()
        assert len(wire["params"]["dependencies"]["items"]) == 5  # type: ignore[index]
        _wire_round_trip(verdict)

    def test_unobserved_log_source_type_with_a_name(self) -> None:
        verdict = classify(
            make_rule(age_days=200),
            dependencies=[
                dependency(RuleDependencyKind.LOG_SOURCE_TYPE, "12", target_name="Linux OS")
            ],
        )

        assert verdict.reason == (
            "A dependency has not been observed, so the rule cannot be judged: "
            "Linux OS has not been observed."
        )


class TestNoisy:
    #: (trigger_count, window_days) -> the old f-string over the same inputs.
    @pytest.mark.parametrize(
        ("triggers", "window_days"),
        [(1000, 1.0), (1501, 1.0), (4075, 2.5), (100_000, 30.0), (1251, 1.0)],
    )
    def test_numbers_round_as_the_old_format_specs_did(
        self, triggers: int, window_days: float
    ) -> None:
        cfg = settings(rule_health_noisy_daily_firings=500.0)
        rule = make_rule(age_days=200)
        evaluator = RuleHealthEvaluator(
            session=None,  # type: ignore[arg-type]
            settings=cfg,
            clock=lambda: NOW,
        )

        verdict = evaluator.classify(
            rule,
            now=NOW,
            window_start=NOW - timedelta(days=window_days),
            trigger_count=triggers,
            offense_count=0,
            dependencies=[],
            building_block_enabled={},
            log_source_type_healthy={},
            observation_complete=True,
        )

        per_day = triggers / max(1.0, window_days)
        assert verdict.status == RuleHealthStatus.NOISY
        assert verdict.reason == (
            f"The rule fired {per_day:.1f} times a day over the last "
            f"{max(1.0, window_days):.0f} days, above the expected {500.0:.1f}."
        )
        _wire_round_trip(verdict)

    def test_expected_rate_threshold_is_three_times(self) -> None:
        verdict = classify(
            make_rule(age_days=200, expected_daily_firings=0.35), trigger_count=100
        )

        assert verdict.status == RuleHealthStatus.NOISY
        # 3 * 0.35 is 1.0499999999999998 in float arithmetic -> "1.0"; 100 / 30 -> "3.3"
        assert verdict.reason == (
            "The rule fired 3.3 times a day over the last 30 days, above the expected 1.0."
        )


class TestSilence:
    def test_never_observed_over_complete_windows(self) -> None:
        verdict = _with_matches(
            MeasuredMatches(
                MatchEvidence.EXACT,
                MatchCoverage(CoverageState.COMPLETE, expected=720, complete=720),
            )
        )

        assert verdict.status == RuleHealthStatus.NEVER_OBSERVED
        assert verdict.reason == (
            "The rule is enabled and its dependencies are healthy, but no matching event was "
            "measured in any of the 720 COMPLETE windows of the evaluation span."
        )
        _wire_round_trip(verdict)

    @pytest.mark.parametrize(
        ("age_days", "shown"), [(45.0, "45"), (7.5, "8"), (8.5, "8"), (30.4, "30")]
    )
    def test_never_observed_without_measurement_rounds_the_age_to_whole_days(
        self, age_days: float, shown: str
    ) -> None:
        verdict = classify(make_rule(age_days=age_days))

        assert verdict.status == RuleHealthStatus.NEVER_OBSERVED
        assert verdict.reason == (
            "The rule is enabled and its dependencies are healthy, but it "
            f"has never been observed firing in {age_days:.0f} days."
        )
        assert f"firing in {shown} days." in verdict.reason
        _wire_round_trip(verdict)

    @pytest.mark.parametrize(("quiet", "shown"), [(20.0, "20"), (14.5, "14"), (15.5, "16")])
    def test_inactive_rounds_the_quiet_days_to_whole_days(self, quiet: float, shown: str) -> None:
        verdict = classify(
            make_rule(age_days=200, last_fired_at=NOW - timedelta(days=quiet)),
            trigger_count=0,
        )

        assert verdict.status == RuleHealthStatus.INACTIVE
        assert verdict.reason == (
            f"The rule last fired {quiet:.0f} days ago, beyond the 14-day inactivity window."
        )
        assert f"fired {shown} days ago" in verdict.reason
        _wire_round_trip(verdict)


def _with_matches(matches: MeasuredMatches, *, observation_complete: bool = False):
    evaluator = RuleHealthEvaluator(
        session=None,  # type: ignore[arg-type]
        settings=settings(),
        clock=lambda: NOW,
    )
    return evaluator.classify(
        make_rule(),
        now=NOW,
        window_start=NOW - timedelta(days=30),
        trigger_count=0,
        offense_count=0,
        dependencies=[],
        building_block_enabled={},
        log_source_type_healthy={},
        observation_complete=observation_complete,
        matches=matches,
    )


class TestHealthy:
    @pytest.mark.parametrize("count", [1, 87, 12_345_678])
    def test_direct(self, count: int) -> None:
        verdict = classify(make_rule(age_days=200), trigger_count=count, window_days=36500)

        assert verdict.status == RuleHealthStatus.HEALTHY
        assert verdict.reason == (
            f"The rule fired {count} times in the evaluation window with healthy dependencies."
        )
        assert verdict.reason_message == MessageRef("rule_health.healthy.direct", {"count": count})
        _wire_round_trip(verdict)

    def test_inferred_from_offense_contributions(self) -> None:
        evaluator = RuleHealthEvaluator(
            session=None,  # type: ignore[arg-type]
            settings=settings(),
            clock=lambda: NOW,
        )

        verdict = evaluator.classify(
            make_rule(age_days=200),
            now=NOW,
            window_start=NOW - timedelta(days=30),
            trigger_count=3,
            offense_count=3,
            dependencies=[],
            building_block_enabled={},
            log_source_type_healthy={},
            observation_complete=True,
            metric_inferred=True,
        )

        assert verdict.status == RuleHealthStatus.HEALTHY
        assert verdict.reason == (
            "At least 3 offense-contributing firings were observed in the evaluation window "
            "with healthy dependencies."
        )
        assert verdict.reason_message == MessageRef("rule_health.healthy.inferred", {"count": 3})

    def test_lower_bound_from_measured_matches(self) -> None:
        verdict = _with_matches(
            MeasuredMatches(
                MatchEvidence.EXACT,
                MatchCoverage(CoverageState.COMPLETE, expected=720, complete=720),
                events=1,
                last_match_at=NOW - timedelta(days=1),
            )
        )

        assert verdict.status == RuleHealthStatus.HEALTHY
        assert verdict.reason == (
            "The rule fired 1 times in the evaluation window with healthy dependencies."
        )

    def test_a_common_rule_match_count_is_a_lower_bound(self) -> None:
        verdict = _with_matches(
            MeasuredMatches(
                MatchEvidence.LOWER_BOUND,
                MatchCoverage(CoverageState.COMPLETE, expected=720, complete=720),
                events=12,
                last_match_at=NOW - timedelta(days=1),
            )
        )

        assert verdict.status == RuleHealthStatus.HEALTHY
        assert verdict.reason == (
            "At least 12 matching events were measured in the evaluation window "
            "with healthy dependencies."
        )
        assert verdict.reason_message == MessageRef(
            "rule_health.healthy.lower_bound", {"count": 12}
        )


COMPLETE = MatchCoverage(CoverageState.COMPLETE, expected=720, complete=720)
INCOMPLETE = MatchCoverage(CoverageState.INCOMPLETE, expected=720, complete=719, failed=1)
NONE = MatchCoverage(CoverageState.NONE)


def _facts(**overrides) -> FailedWindowFacts:
    base = dict(
        failed=2,
        skipped=0,
        first_start=datetime(2026, 10, 25, 10, tzinfo=UTC),
        last_start=datetime(2026, 10, 25, 11, tzinfo=UTC),
        last_error_class="ProviderError",
        until=datetime(2026, 11, 24, 11, tzinfo=UTC),
    )
    base.update(overrides)
    return FailedWindowFacts(**base)  # type: ignore[arg-type]


class TestIncompleteCoverage:
    """Every shape of the longest composed sentence, singular and plural."""

    CASES: ClassVar[dict[str, MeasuredMatches | None]] = {
        "no_collection": None,
        "match_collector_never_ran": MeasuredMatches(MatchEvidence.EXACT, NONE),
        "not_exact": MeasuredMatches(MatchEvidence.LOWER_BOUND, INCOMPLETE),
        "head_only": MeasuredMatches(MatchEvidence.EXACT, INCOMPLETE),
        "failed_many_class": MeasuredMatches(MatchEvidence.EXACT, INCOMPLETE, 0, None, _facts()),
        "failed_one_class": MeasuredMatches(
            MatchEvidence.EXACT,
            INCOMPLETE,
            0,
            None,
            _facts(failed=1, last_start=datetime(2026, 10, 25, 10, tzinfo=UTC)),
        ),
        "failed_many_no_class": MeasuredMatches(
            MatchEvidence.EXACT, INCOMPLETE, 0, None, _facts(last_error_class=None)
        ),
        "failed_one_no_class": MeasuredMatches(
            MatchEvidence.EXACT,
            INCOMPLETE,
            0,
            None,
            _facts(failed=1, last_error_class=None),
        ),
        "skipped_many": MeasuredMatches(
            MatchEvidence.EXACT,
            INCOMPLETE,
            0,
            None,
            _facts(failed=0, skipped=3, last_error_class=None),
        ),
        "skipped_one_single_window": MeasuredMatches(
            MatchEvidence.EXACT,
            INCOMPLETE,
            0,
            None,
            _facts(
                failed=0,
                skipped=1,
                last_error_class=None,
                last_start=datetime(2026, 10, 25, 10, tzinfo=UTC),
            ),
        ),
        "failed_and_skipped": MeasuredMatches(
            MatchEvidence.EXACT, INCOMPLETE, 0, None, _facts(failed=4, skipped=1)
        ),
        "large_counts": MeasuredMatches(
            MatchEvidence.EXACT,
            MatchCoverage(CoverageState.INCOMPLETE, expected=8640, complete=1234, failed=7406),
            0,
            None,
            _facts(failed=7406, skipped=1000),
        ),
        # A non-UTC instant is rendered in UTC, as the old helper did.
        "non_utc_instants": MeasuredMatches(
            MatchEvidence.EXACT,
            INCOMPLETE,
            0,
            None,
            _facts(
                first_start=datetime(2026, 10, 25, 23, 30, tzinfo=UTC).astimezone(
                    timezone(timedelta(hours=3))
                ),
                last_start=datetime(2026, 10, 26, 1, 0, tzinfo=UTC),
            ),
        ),
    }

    @pytest.mark.parametrize("name", list(CASES))
    def test_the_message_renders_the_legacy_sentence(self, name: str) -> None:
        matches = self.CASES[name]

        message = RuleHealthEvaluator._incomplete_coverage_message(matches)

        assert render(message) == _legacy_incomplete(matches)
        wire = message.to_wire()
        assert wire["code"] == message.code

    @pytest.mark.parametrize(
        ("name", "code"),
        [
            ("no_collection", "rule_health.incomplete.no_collection"),
            ("head_only", "rule_health.incomplete.head"),
            ("failed_many_class", "rule_health.incomplete.detail_many"),
            ("skipped_one_single_window", "rule_health.incomplete.detail_one"),
        ],
    )
    def test_the_shape_picks_its_own_code(self, name: str, code: str) -> None:
        assert RuleHealthEvaluator._incomplete_coverage_message(self.CASES[name]).code == code

    def test_each_cause_variant_has_its_own_code(self) -> None:
        def causes(name: str) -> list[str]:
            message = RuleHealthEvaluator._incomplete_coverage_message(self.CASES[name])
            return [c.code for c in message.params["causes"]]  # type: ignore[union-attr]

        prefix = "rule_health.incomplete.cause."
        assert causes("failed_many_class") == [prefix + "failed_many_class"]
        assert causes("failed_one_class") == [prefix + "failed_one_class"]
        assert causes("failed_many_no_class") == [prefix + "failed_many"]
        assert causes("failed_one_no_class") == [prefix + "failed_one"]
        assert causes("skipped_many") == [prefix + "skipped_many"]
        assert causes("skipped_one_single_window") == [prefix + "skipped_one"]
        # `failed_and_skipped` keeps its error class, so the first cause is the `_class`
        # variant.
        assert causes("failed_and_skipped") == [
            prefix + "failed_many_class",
            prefix + "skipped_one",
        ]

    def test_the_timestamps_are_wire_instants_and_the_error_class_is_text(self) -> None:
        message = RuleHealthEvaluator._incomplete_coverage_message(self.CASES["failed_many_class"])

        wire = message.to_wire()
        params = wire["params"]
        assert params["first_start"] == "2026-10-25T10:00:00+00:00"  # type: ignore[index]
        assert params["last_start"] == "2026-10-25T11:00:00+00:00"  # type: ignore[index]
        assert params["until"] == "2026-11-24T11:00:00+00:00"  # type: ignore[index]
        cause = params["causes"]["items"][0]  # type: ignore[index]
        assert cause == {
            "code": "rule_health.incomplete.cause.failed_many_class",
            "params": {"count": 2, "error_class": "ProviderError"},
        }

    def test_the_verdict_carries_the_message(self) -> None:
        verdict = _with_matches(self.CASES["failed_and_skipped"])  # type: ignore[arg-type]

        assert verdict.status == RuleHealthStatus.INSUFFICIENT_DATA
        assert verdict.reason == _legacy_incomplete(self.CASES["failed_and_skipped"])
        assert verdict.reason_message is not None
        assert verdict.reason_message.code == "rule_health.incomplete.detail_many"
        _wire_round_trip(verdict)


def test_every_rule_health_code_is_in_one_contiguous_block() -> None:
    codes = [spec.code for spec in REGISTRY]
    indexes = [i for i, code in enumerate(codes) if code.startswith("rule_health.")]

    assert indexes == list(range(indexes[0], indexes[-1] + 1))


def test_the_minute_timestamp_style_and_the_semicolon_list_style_render() -> None:
    ref = MessageRef(
        "rule_health.incomplete.detail_one",
        {
            "complete": 1,
            "expected": 2,
            "causes": [
                MessageRef("rule_health.incomplete.cause.failed_one"),
                MessageRef("rule_health.incomplete.cause.skipped_one"),
            ],
            "first_start": datetime(2026, 1, 2, 3, 4, 59, tzinfo=UTC),
            "until": "2026-01-03T03:04:59Z",
        },
    )

    assert render(ref) == (
        "Measured rule-match coverage of the evaluation window is incomplete "
        "(1 of 2 windows are COMPLETE): 1 window failed to collect; 1 window was skipped "
        "beyond the catch-up horizon and never searched. Affected window: 2026-01-02 03:04 UTC. "
        "The absence of measured matches is not evidence that the rule has never fired. "
        "The evaluation window stops holding the last affected window at 2026-01-03 03:04 UTC."
    )
