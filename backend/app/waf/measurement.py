"""Which verdicts a WAF count can support, given what the count is known to be.

A count is `EXACT` or a `LOWER_BOUND` (`app.models.enums.WafMeasurement`). A
detector declares, once, how it may use a lower bound
(`WafMeasurementRequirement`), and `verdict_scope` combines the two into what
the detector is allowed to conclude for one bucket:

  * `FULL` -- any verdict. The count is exact.
  * `INCREASE_ONLY` -- only an anomalous verdict whose every guard is
    monotonically non-decreasing in the count. If the lower bound already
    satisfies all of them, the true count -- which is at least as large --
    satisfies them too. A normal verdict is never concluded from a lower bound:
    the hidden events could be exactly the ones that make it abnormal.
  * `NONE` -- no verdict. The detector needs exact counts, or the measurement
    semantics are unknown (a row with no source bucket record, for example).

Pure. No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.models.enums import BucketCompleteness, WafMeasurement


class WafMeasurementRequirement(StrEnum):
    #: Every verdict needs exact counts. A ratio, a drop or a novelty-absence
    #: claim cannot be bounded by one side of an undercount.
    EXACT = "EXACT"
    #: Increase-direction verdicts may be proven from a lower bound; any other
    #: verdict needs an exact count.
    LOWER_BOUND_PROVES_INCREASE = "LOWER_BOUND_PROVES_INCREASE"


class WafVerdictScope(StrEnum):
    FULL = "FULL"
    INCREASE_ONLY = "INCREASE_ONLY"
    NONE = "NONE"


@dataclass(frozen=True)
class ScopeDecision:
    scope: WafVerdictScope
    #: Why the scope is narrower than FULL. None when it is FULL.
    reason: str | None = None


def verdict_scope(
    requirement: WafMeasurementRequirement, measurement: WafMeasurement | None
) -> ScopeDecision:
    if measurement is WafMeasurement.EXACT:
        return ScopeDecision(WafVerdictScope.FULL)
    if measurement is None:
        return ScopeDecision(
            WafVerdictScope.NONE,
            "the bucket does not state whether its counts are exact or lower bounds",
        )
    if requirement is WafMeasurementRequirement.LOWER_BOUND_PROVES_INCREASE:
        return ScopeDecision(
            WafVerdictScope.INCREASE_ONLY,
            "the count is a lower bound: an increase can be proven, normal behaviour cannot",
        )
    return ScopeDecision(
        WafVerdictScope.NONE, "the detector needs exact counts and this count is a lower bound"
    )


_NARROWNESS = {WafVerdictScope.FULL: 0, WafVerdictScope.INCREASE_ONLY: 1, WafVerdictScope.NONE: 2}


def narrowest(decisions: list[ScopeDecision]) -> ScopeDecision:
    """The scope a detector with several inputs may use: the narrowest of them.

    A verdict is only as strong as its weakest input. An exact denominator does
    not rescue a lower-bound one, and one input that supports no verdict leaves
    the detector with none. The reason reported is the narrowest input's.
    """
    if not decisions:
        return ScopeDecision(WafVerdictScope.FULL)
    return max(decisions, key=lambda d: _NARROWNESS[d.scope])


def default_measurement(completeness: BucketCompleteness | str) -> WafMeasurement | None:
    """The measurement to assume when none was recorded.

    Only a COMPLETE observation is assumed exact. Anything else is unknown, not a
    lower bound: lower-bound reasoning is permitted only where a collector
    positively recorded that semantics.
    """
    return WafMeasurement.EXACT if completeness == BucketCompleteness.COMPLETE else None
