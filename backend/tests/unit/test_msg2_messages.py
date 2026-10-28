"""MSG-2: the coverage and rule-statistics sentences keep today's English byte for byte.

Every converted site renders through the registry. The expected text below is written out
the way the code built it before the registry (f-strings and literals), so an edit to a
template that changes English fails here.
"""

from __future__ import annotations

import pytest

from app.i18n.messages import render
from app.models.enums import CoverageStatus, RuleHealthStatus
from app.rules.matches import SKIPPED_REASON, CoverageState, MatchCoverage
from app.services import rule_statistics
from app.services.detection_coverage import DetectionCoverageEvaluator
from app.services.rule_statistics import _NO_COMPLETE_WINDOW, _window_message, _wire
from tests.unit.test_detection_coverage import evaluate, make_rule, pair, settings


def _legacy_roll_up(total: int, enabled: int, working: int, degraded: int):
    """The sentence ladder as `_roll_up` wrote it before the registry."""
    if enabled == 0:
        if total > 0:
            return (
                CoverageStatus.MISSING,
                "Every rule mapped to this technique is disabled or discounted, "
                "so there is no active detection.",
            )
        return CoverageStatus.MISSING, "No rule is mapped to this technique."
    if working == 0:
        if degraded > 0:
            return (
                CoverageStatus.DEGRADED,
                f"{degraded} of {enabled} enabled rules are not currently detecting.",
            )
        return (
            CoverageStatus.NOT_EVALUATED,
            "Rule health has not been established for the mapped rules.",
        )
    if degraded > 0:
        return (
            CoverageStatus.DEGRADED,
            f"{working} of {enabled} enabled rules are detecting; {degraded} are degraded.",
        )
    if working < enabled:
        return (
            CoverageStatus.PARTIAL,
            f"{working} of {enabled} enabled rules are detecting; the rest are "
            "not yet established.",
        )
    return (
        CoverageStatus.COVERED,
        f"All {enabled} enabled rules mapped to this technique are detecting.",
    )


EVALUATOR = DetectionCoverageEvaluator(session=None, settings=settings())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("total", "enabled", "working", "degraded"),
    [
        (0, 0, 0, 0),
        (2, 0, 0, 0),
        (1, 1, 0, 1),
        (3, 3, 0, 3),
        (1200, 1100, 0, 1100),
        (1, 1, 0, 0),
        (4, 4, 1, 3),
        (1500, 1234, 1000, 234),
        (3, 3, 2, 0),
        (2, 2, 1, 0),
        (1, 1, 1, 0),
        (1234, 1234, 1234, 0),
    ],
)
def test_roll_up_text_equals_the_legacy_sentence(
    total: int, enabled: int, working: int, degraded: int
) -> None:
    status, message, _score = EVALUATOR._roll_up(
        total=total, enabled=enabled, working=working, degraded=degraded
    )
    legacy_status, legacy_text = _legacy_roll_up(total, enabled, working, degraded)
    assert status == legacy_status
    assert render(message) == legacy_text


def test_verdict_text_is_the_rendered_message_for_every_status() -> None:
    for pairs in (
        [],
        [pair(make_rule())],
        [pair(make_rule(enabled=False))],
        [pair(make_rule(health=RuleHealthStatus.INACTIVE))],
        [pair(make_rule(health=RuleHealthStatus.UNKNOWN))],
        [
            pair(make_rule(qradar_id=1)),
            pair(make_rule(qradar_id=2, health=RuleHealthStatus.INACTIVE)),
        ],
    ):
        verdict = evaluate(pairs)
        assert verdict.reason == render(verdict.reason_message)
        assert verdict.reason_message.to_wire()["code"].startswith("coverage.rollup.")


@pytest.mark.parametrize(
    ("confidence", "floor", "expected"),
    [
        (0.3, 0.5, "Mapping confidence 0.30 is below the 0.50 floor; recorded but not "
                   "counted toward coverage."),
        (0.0, 0.25, "Mapping confidence 0.00 is below the 0.25 floor; recorded but not "
                    "counted toward coverage."),
        (0.499, 0.5, "Mapping confidence 0.50 is below the 0.50 floor; recorded but not "
                     "counted toward coverage."),
        (0.125, 0.5, "Mapping confidence 0.12 is below the 0.50 floor; recorded but not "
                     "counted toward coverage."),
    ],
)
def test_below_floor_evidence_equals_the_legacy_sentence(
    confidence: float, floor: float, expected: str
) -> None:
    verdict = evaluate(
        [pair(make_rule(), confidence=confidence)],
        cfg=settings(coverage_min_confidence=floor),
    )
    entry = verdict.evidence[0]
    assert entry.reason == expected
    assert entry.reason_message.code == "coverage.evidence.below_floor"
    assert entry.reason_message.to_wire()["params"] == {"confidence": confidence, "floor": floor}


def test_evidence_reasons_equal_the_legacy_sentences() -> None:
    verdict = evaluate(
        [
            pair(make_rule(qradar_id=1, health=RuleHealthStatus.HEALTHY)),
            pair(make_rule(qradar_id=2, enabled=False)),
            pair(make_rule(qradar_id=3, health=RuleHealthStatus.DEPENDENCY_DEGRADED)),
            pair(make_rule(qradar_id=4, health=RuleHealthStatus.NEVER_OBSERVED)),
            pair(make_rule(qradar_id=5, health=RuleHealthStatus.INACTIVE)),
            pair(make_rule(qradar_id=6, health=RuleHealthStatus.INSUFFICIENT_DATA)),
        ]
    )
    by_rule = {e.rule_qradar_id: e for e in verdict.evidence}
    assert {k: (v.reason, v.reason_message.code) for k, v in by_rule.items()} == {
        1: ("The rule is enabled and its health is acceptable.", "coverage.evidence.acceptable"),
        2: (
            "The rule is disabled, so it provides no active coverage.",
            "coverage.evidence.disabled",
        ),
        3: (
            "The rule's required telemetry is unavailable, so it cannot fire.",
            "coverage.evidence.dependency_degraded",
        ),
        4: ("The rule has never been observed firing.", "coverage.evidence.never_observed"),
        5: (
            "The rule has not fired within the inactivity window.",
            "coverage.evidence.inactive",
        ),
        6: (
            "The rule's health has not been established yet.",
            "coverage.evidence.not_established",
        ),
    }


@pytest.mark.parametrize(
    ("coverage", "expected"),
    [
        (MatchCoverage(CoverageState.COMPLETE, 24, 24), None),
        (
            MatchCoverage(CoverageState.NONE),
            "No rule-match window has been collected for this instance: collection is disabled "
            "or has not run yet, so nothing is measured.",
        ),
        (
            MatchCoverage(CoverageState.INCOMPLETE, 0),
            "No rule-match window in the requested span has settled yet.",
        ),
        (
            MatchCoverage(CoverageState.INCOMPLETE, 24, 23, 0, 0, 1),
            "Only 23 of 24 windows in the span are complete (1 missing, 0 truncated, 0 failed)."
            " Absence from an incomplete window proves nothing.",
        ),
        (
            MatchCoverage(CoverageState.INCOMPLETE, 25920, 20000, 3000, 2000, 920),
            "Only 20000 of 25920 windows in the span are complete (920 missing, 3000 truncated, "
            "2000 failed). Absence from an incomplete window proves nothing.",
        ),
    ],
)
def test_window_reason_equals_the_legacy_sentence(
    coverage: MatchCoverage, expected: str | None
) -> None:
    message = _window_message(coverage)
    assert (None if message is None else render(message)) == expected
    assert (message is None) == (expected is None)


def test_statistics_constants_keep_their_legacy_text() -> None:
    assert rule_statistics.SILENT_EXCLUDED_REASON == (
        "Only EVENT rules and EVENT building blocks are judged: an event search sees every one of "
        "their matches. COMMON rules also match on flows, which are not searched, so their silence "
        "is unknown. FLOW and OFFENSE rules are not searched at all."
    )
    assert rule_statistics.BENCHMARK_LABEL == (
        "QRadar capacity benchmark: how fast the rule could be evaluated. "
        "Lower is costlier to evaluate. This is not a firing rate."
    )
    assert rule_statistics.BENCHMARK_UNIT == "millions of events per second (QRadar's unit)"
    assert render(_NO_COMPLETE_WINDOW) == "No complete window to measure."


def test_wire_pair_is_none_for_no_message_and_raw_counts_otherwise() -> None:
    assert _wire(None) == (None, None)
    message = _window_message(MatchCoverage(CoverageState.INCOMPLETE, 24, 23, 0, 0, 1))
    assert _wire(message) == (
        "rule_statistics.window.incomplete",
        {"complete": 23, "expected": 24, "missing": 1, "truncated": 0, "failed": 0},
    )


def test_skipped_reason_stays_the_stored_english_literal() -> None:
    # Compared by string in matches.py, rule_match_collector.py and doctor.py, and stored in a
    # window's last_error: it carries no code (MSG-COMMON).
    assert SKIPPED_REASON == "skipped: beyond the catch-up horizon"
