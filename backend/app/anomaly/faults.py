"""How a bucket's failed evaluations are bounded.

Pure: the decision the engine applies after recording one failed attempt, and
the rule it follows, so both can be tested without a database. `attempts` counts
evaluation ticks, not seconds (T-170 §3 "K = 3, and why").
"""

from __future__ import annotations

from enum import StrEnum


class FaultDecision(StrEnum):
    #: Keep the bucket and retry it on the next evaluation tick.
    RETRY = "retry"
    #: Stop retrying: the bucket stays "not judged" and the engine moves on.
    GIVE_UP = "give_up"


def next_attempt(attempts: int, max_attempts: int) -> FaultDecision:
    """What to do after the `attempts`-th failed evaluation of one bucket.

    `attempts` includes the failure just recorded, so the bucket is given up on
    the `max_attempts`-th failure. Both bounds are refused rather than silently
    coerced: an attempt count below one would mean nothing was recorded, and a
    limit below one would give up on a bucket that was never tried.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    return FaultDecision.GIVE_UP if attempts >= max_attempts else FaultDecision.RETRY
