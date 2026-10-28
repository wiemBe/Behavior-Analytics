"""Structured anomaly evidence.

Every detector returns this shape (never a bare label) so the UI and the alert
payload can show *why* something fired: observed vs expected, the baseline band,
the deviation score, the threshold it crossed, how many samples backed the
baseline, the affected interval, a confidence, and a human-readable reason.

Shared by every behavior domain. The detector type is a type parameter rather
than the Phase A `AnomalyType`, so a WAF or firewall detector records its own
type instead of borrowing a log-source one: the stored `details` payload carries
`anomaly_type`, and a borrowed value there would be false provenance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum

from app.i18n.messages import MessageRef
from app.models.enums import DetectorSignal, Severity


@dataclass
class AnomalyEvidence[T: StrEnum]:
    anomaly_type: T
    signal: DetectorSignal
    severity: Severity
    observed_value: float | None
    expected_value: float | None
    baseline_low: float | None
    baseline_high: float | None
    deviation_score: float | None
    threshold: float | None
    sample_count: int
    interval_start: datetime
    interval_end: datetime
    confidence: float
    reason: str
    extra: dict = field(default_factory=dict)
    # True when the detector could not reach a verdict for want of usable data,
    # as opposed to reaching a "not anomalous" verdict. Both carry the UNKNOWN
    # signal so neither advances hysteresis, but only this one may surface as
    # INSUFFICIENT_DATA — an operator must be able to tell "this source is fine"
    # apart from "we cannot yet say whether this source is fine".
    insufficient_data: bool = False
    # The registry reference `reason` was rendered from, when the detector has one. The text
    # stays the stored and served sentence; the reference travels beside it in `to_dict()` as
    # `reason_code` / `reason_params` (and a writer may store it in an `_i18n` column).
    reason_message: MessageRef | None = None

    @property
    def is_anomalous(self) -> bool:
        return self.signal is DetectorSignal.ANOMALOUS

    def to_dict(self) -> dict:
        d = asdict(self)
        # `asdict` would turn the reference into a nested dict of raw values; the wire form is
        # JSON-safe (timestamps as text) and sits beside `reason`.
        d.pop("reason_message", None)
        if self.reason_message is not None:
            wire = self.reason_message.to_wire()
            d["reason_code"] = wire["code"]
            d["reason_params"] = wire["params"]
        d["anomaly_type"] = self.anomaly_type.value
        d["signal"] = self.signal.value
        d["severity"] = self.severity.value
        d["interval_start"] = self.interval_start.isoformat()
        d["interval_end"] = self.interval_end.isoformat()
        return d


def healthy[T: StrEnum](
    anomaly_type: T,
    *,
    interval_start: datetime,
    interval_end: datetime,
    observed: float | None = None,
    expected: float | None = None,
    reason: str = "within expected range",
    reason_message: MessageRef | None = None,
) -> AnomalyEvidence[T]:
    return AnomalyEvidence(
        anomaly_type=anomaly_type,
        signal=DetectorSignal.HEALTHY,
        severity=Severity.INFO,
        observed_value=observed,
        expected_value=expected,
        baseline_low=None,
        baseline_high=None,
        deviation_score=None,
        threshold=None,
        sample_count=0,
        interval_start=interval_start,
        interval_end=interval_end,
        confidence=1.0,
        reason=reason,
        reason_message=reason_message,
    )


def unknown[T: StrEnum](
    anomaly_type: T,
    *,
    interval_start: datetime,
    interval_end: datetime,
    reason: str,
    reason_message: MessageRef | None = None,
) -> AnomalyEvidence[T]:
    return AnomalyEvidence(
        anomaly_type=anomaly_type,
        signal=DetectorSignal.UNKNOWN,
        severity=Severity.INFO,
        observed_value=None,
        expected_value=None,
        baseline_low=None,
        baseline_high=None,
        deviation_score=None,
        threshold=None,
        sample_count=0,
        interval_start=interval_start,
        interval_end=interval_end,
        confidence=0.0,
        reason=reason,
        reason_message=reason_message,
    )


def insufficient[T: StrEnum](
    anomaly_type: T,
    *,
    interval_start: datetime,
    interval_end: datetime,
    reason: str,
    sample_count: int = 0,
    reason_message: MessageRef | None = None,
) -> AnomalyEvidence[T]:
    """No verdict is possible: the baseline or the bucket is inadequate.

    Deliberately not `healthy()`. Returning "normal" because we could not
    measure anything is the single most misleading thing a monitoring system can
    do — it converts an observability gap into a false assurance.
    """
    ev = unknown(
        anomaly_type,
        interval_start=interval_start,
        interval_end=interval_end,
        reason=reason,
        reason_message=reason_message,
    )
    ev.insufficient_data = True
    ev.sample_count = sample_count
    return ev
