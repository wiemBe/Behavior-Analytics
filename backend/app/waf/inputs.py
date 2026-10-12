"""The measured inputs WAF detectors read, and what each one is known to be.

A detector that combines several metrics -- a ratio's numerator and denominator,
a share and its total -- cannot be granted lower-bound reasoning as a whole. Each
input has its own measurement, decided by the search that produced it, and its
own mathematical requirement, declared once per detector in
`app.anomaly.capabilities`:

  * a ratio's *numerator* may be a lower bound when the denominator is exact:
    the proportion is non-decreasing in the numerator at a fixed denominator,
    so a lower bound that clears every guard proves the true proportion does;
  * a ratio's *denominator* must be exact: a lower-bound denominator can make
    the proportion too large or too small, so it bounds nothing;
  * a composition's largest contributor and total must both be exact: in a
    truncated breakdown the largest source IP may be entirely hidden, and
    hidden raw groups can fold into a visible one;
  * a novelty claim's history must be complete: "not seen before" is an
    absence claim, and no lower bound supports one.

This module answers the first half -- what each input *is* for one observation.
Pure; no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.models.enums import WafMeasurement, WafTelemetryStatus
from app.waf.observations import EndpointObservation


class WafInput(StrEnum):
    REQUEST_COUNT = "REQUEST_COUNT"
    BLOCKED_REQUESTS = "BLOCKED_REQUESTS"
    ACTION_EVENTS = "ACTION_EVENTS"
    SERVER_ERRORS = "SERVER_ERRORS"
    CLIENT_ERRORS = "CLIENT_ERRORS"
    RESPONSE_CODE_EVENTS = "RESPONSE_CODE_EVENTS"
    TOP_CLIENT_REQUESTS = "TOP_CLIENT_REQUESTS"
    CLIENT_EVENTS = "CLIENT_EVENTS"
    ENDPOINT_HISTORY = "ENDPOINT_HISTORY"


def _status(status: WafTelemetryStatus) -> WafMeasurement | None:
    if status is WafTelemetryStatus.EXACT:
        return WafMeasurement.EXACT
    if status is WafTelemetryStatus.LOWER_BOUND:
        return WafMeasurement.LOWER_BOUND
    return None


def _numerator(status: WafTelemetryStatus, uncertain: int | None) -> WafMeasurement | None:
    """A numerator is a lower bound when its search was, or when some of the
    denominator's events carried a value that could not be classified."""
    base = _status(status)
    if base is WafMeasurement.EXACT and uncertain:
        return WafMeasurement.LOWER_BOUND
    return base


@dataclass(frozen=True)
class HistoryFacts:
    """Whether an endpoint's pre-appearance history is complete. See novelty."""

    complete: bool
    reason: str | None = None


def input_measurement(
    source: WafInput, obs: EndpointObservation, history: HistoryFacts | None = None
) -> WafMeasurement | None:
    """What one input is for one observation: exact, a lower bound, or unknown."""
    b, c = obs.breakdowns, obs.counts
    match source:
        case WafInput.REQUEST_COUNT:
            return obs.measurement
        case WafInput.BLOCKED_REQUESTS:
            return _numerator(b.action, c.unmapped_actions)
        case WafInput.ACTION_EVENTS:
            return _status(b.action)
        case WafInput.SERVER_ERRORS | WafInput.CLIENT_ERRORS:
            return _numerator(b.response_code, c.unclassified_responses)
        case WafInput.RESPONSE_CODE_EVENTS:
            return _status(b.response_code)
        case WafInput.TOP_CLIENT_REQUESTS | WafInput.CLIENT_EVENTS:
            return _status(b.client)
        case WafInput.ENDPOINT_HISTORY:
            if history is None or not history.complete:
                return None
            return WafMeasurement.EXACT
