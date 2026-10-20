"""Measured inputs supplied to property-watch detectors (design §G.8.0).

The collector decides whether a value count is exact, a lower bound, or unknown.
This module only maps that decision onto the shared WAF measurement vocabulary so
the capability specs, rather than a detector, decide what a verdict may claim.
Pure; no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.models.enums import PwBucketMeasurement, WafMeasurement
from app.property_watch.facts import PwBucketFact, PwReading
from app.waf.inputs import HistoryFacts

PW_VALUE_EVENTS_METRIC = "value_events"


class PwInput(StrEnum):
    VALUE_EVENTS = "VALUE_EVENTS"
    COVERAGE = "COVERAGE"
    VALUE_HISTORY = "VALUE_HISTORY"
    COMPONENT_HISTORY = "COMPONENT_HISTORY"
    MATERIALITY = "MATERIALITY"


@dataclass(frozen=True)
class PwObservation:
    """Everything capability arithmetic needs for one value and bucket."""

    bucket: PwBucketFact | None
    reading: PwReading
    history: HistoryFacts | None = None
    component_history: WafMeasurement | None = None
    materiality: WafMeasurement | None = None


def bucket_measurement(measurement: PwBucketMeasurement) -> WafMeasurement | None:
    """Map the PW enum to the shared verdict vocabulary (the IPS input shape)."""
    if measurement is PwBucketMeasurement.EXACT:
        return WafMeasurement.EXACT
    if measurement is PwBucketMeasurement.LOWER_BOUND:
        return WafMeasurement.LOWER_BOUND
    return None


def input_measurement(source: PwInput, observation: PwObservation) -> WafMeasurement | None:
    """What one declared PW input is known to be for this observation."""
    match source:
        case PwInput.VALUE_EVENTS:
            return observation.reading.measurement
        case PwInput.COVERAGE:
            if observation.bucket is None:
                return None
            return bucket_measurement(observation.bucket.values_measurement)
        case PwInput.VALUE_HISTORY:
            if observation.history is None or not observation.history.complete:
                return None
            return WafMeasurement.EXACT
        case PwInput.COMPONENT_HISTORY:
            return observation.component_history
        case PwInput.MATERIALITY:
            return observation.materiality
