"""Identity detectors. Pure: no database, no clock, no per-username query.

The subject is one (log source, catalog event ID, bucket). A username is
evidence inside the stored top-N, never a baseline subject.

Thresholds (1102, 4720, 4726, 4728, 4732, 4756) fire at a count of at least 1.
The count fires even when the name list was cut. A truncated floor below 1, a
missing count, and NOT_COLLECTED are INSUFFICIENT_DATA, never a healthy zero.

Novelty (a username in the stored top-N of 4728, 4732 or 4756) needs a verified
history: every bucket in the window present, names measured exactly, the search
not cut, and the catalog version unchanged. A gap or a cut list is
INSUFFICIENT_DATA for the whole series.

4625 and 4740 use a seasonal rate band on the event-ID count, the same shape as
PW_VALUE_SPIKE. A cold band or a truncated count that does not clear every
increase guard is INSUFFICIENT_DATA, never healthy. The verdict scope is read
from the declared spec; this module does not invent lower-bound reasoning.

SOC-13 analyst exclusions are not applied here. The engine excludes the shared
seasonal anomaly span, which is a different rule.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.anomaly.capabilities import identity_verdict_scope
from app.anomaly.seasonal import CellSummary
from app.anomaly.statistics import ratio, robust_z_with_status
from app.models.enums import (
    IDENTITY_BUCKET_SECONDS,
    DetectorSignal,
    IdentityAnomalyType,
    IdentityMeasurement,
    RobustScoreStatus,
    Severity,
)
from app.waf.measurement import WafVerdictScope

#: Same default as `pw_new_value_history_days`. A dedicated constant so a
#: property-watch knob does not move this family.
NOVELTY_HISTORY_DAYS = 7
THRESHOLD_COUNT = 1
SPIKE_RATIO = 2.0
SPIKE_MIN_ABSOLUTE_DELTA = 1.0
SPIKE_MIN_BUCKET_EVENTS = 1.0
SPIKE_HIGH_RATIO = 4.0

THRESHOLD_BY_EVENT: dict[int, IdentityAnomalyType] = {
    1102: IdentityAnomalyType.IDENTITY_AUDIT_LOG_CLEARED,
    4720: IdentityAnomalyType.IDENTITY_ACCOUNT_CREATED,
    4726: IdentityAnomalyType.IDENTITY_ACCOUNT_DELETED,
    4728: IdentityAnomalyType.IDENTITY_PRIVILEGED_GROUP_ADD,
    4732: IdentityAnomalyType.IDENTITY_PRIVILEGED_GROUP_ADD,
    4756: IdentityAnomalyType.IDENTITY_PRIVILEGED_GROUP_ADD,
}
SPIKE_BY_EVENT: dict[int, IdentityAnomalyType] = {
    4625: IdentityAnomalyType.IDENTITY_FAILED_LOGON_SPIKE,
    4740: IdentityAnomalyType.IDENTITY_LOCKOUT_SPIKE,
}
NOVELTY_EVENT_IDS = frozenset({4728, 4732, 4756})

_THRESHOLD_SEVERITY = {
    IdentityAnomalyType.IDENTITY_AUDIT_LOG_CLEARED: Severity.HIGH,
    IdentityAnomalyType.IDENTITY_PRIVILEGED_GROUP_ADD: Severity.HIGH,
    IdentityAnomalyType.IDENTITY_ACCOUNT_CREATED: Severity.MEDIUM,
    IdentityAnomalyType.IDENTITY_ACCOUNT_DELETED: Severity.HIGH,
}


@dataclass(frozen=True)
class NameEvidence:
    """One stored top-N username. The allow-list is rank, username and count."""

    rank: int
    username: str
    count: int


@dataclass(frozen=True)
class SpikeThresholds:
    ratio: float = SPIKE_RATIO
    min_absolute_delta: float = SPIKE_MIN_ABSOLUTE_DELTA
    min_bucket_events: float = SPIKE_MIN_BUCKET_EVENTS
    deviation_z: float = 3.5
    mad_floor_ratio: float = 0.1


@dataclass(frozen=True)
class IdentityVerdict:
    detector: IdentityAnomalyType
    signal: DetectorSignal
    insufficient: bool
    reason: str
    observed: float | None
    expected: float | None
    ratio: float | None
    robust_z: float | None
    absolute_delta: float | None
    score_status: str | None
    severity: Severity
    usernames: tuple[NameEvidence, ...]
    measurement: IdentityMeasurement


@dataclass(frozen=True)
class NoveltyHistoryBucket:
    """One history bucket the novelty check must see whole."""

    bucket_start: datetime
    names_measurement: IdentityMeasurement
    admission_truncated: bool
    catalog_version: int
    bucket_seconds: int
    usernames: frozenset[str]


def detectors_for(event_id: int) -> tuple[IdentityAnomalyType, ...]:
    """The detectors one catalog event ID is judged by. Unknown IDs judge nothing."""
    found: list[IdentityAnomalyType] = []
    threshold = THRESHOLD_BY_EVENT.get(event_id)
    if threshold is not None:
        found.append(threshold)
    if event_id in NOVELTY_EVENT_IDS:
        found.append(IdentityAnomalyType.IDENTITY_NEW_PRIVILEGED_ACCOUNT)
    spike = SPIKE_BY_EVENT.get(event_id)
    if spike is not None:
        found.append(spike)
    return tuple(found)


def expected_bucket_starts(
    window_start: datetime, window_end: datetime, bucket_seconds: int
) -> tuple[datetime, ...]:
    """Every bucket start in `[window_start, window_end)` on the fixed grid."""
    if bucket_seconds <= 0 or window_end <= window_start:
        return ()
    step = timedelta(seconds=bucket_seconds)
    cursor = window_start
    found: list[datetime] = []
    while cursor < window_end:
        found.append(cursor)
        cursor += step
    return tuple(found)


def verify_novelty_history(
    buckets: Sequence[NoveltyHistoryBucket],
    *,
    window_start: datetime,
    window_end: datetime,
    bucket_seconds: int = IDENTITY_BUCKET_SECONDS,
    catalog_version: int,
) -> tuple[bool, frozenset[str]]:
    """Whether the window is a whole stored top-N, and the names it contains.

    Every expected start must be present, measured exactly, not cut, on the
    same catalog version, and at the same bucket width. One gap fails the
    whole series. The returned set is empty when the history is not verified.
    """
    expected = expected_bucket_starts(window_start, window_end, bucket_seconds)
    by_start = {bucket.bucket_start: bucket for bucket in buckets}
    names: set[str] = set()
    for start in expected:
        bucket = by_start.get(start)
        if bucket is None:
            return False, frozenset()
        if bucket.bucket_seconds != bucket_seconds:
            return False, frozenset()
        if bucket.catalog_version != catalog_version:
            return False, frozenset()
        if bucket.admission_truncated:
            return False, frozenset()
        if bucket.names_measurement is not IdentityMeasurement.EXACT:
            return False, frozenset()
        names.update(bucket.usernames)
    return True, frozenset(names)


def _names(usernames: Sequence[NameEvidence]) -> tuple[NameEvidence, ...]:
    return tuple(usernames)


def _insufficient(
    detector: IdentityAnomalyType,
    measurement: IdentityMeasurement,
    reason: str,
    *,
    observed: float | None = None,
    expected: float | None = None,
    usernames: Sequence[NameEvidence] = (),
) -> IdentityVerdict:
    return IdentityVerdict(
        detector=detector,
        signal=DetectorSignal.UNKNOWN,
        insufficient=True,
        reason=reason,
        observed=observed,
        expected=expected,
        ratio=None,
        robust_z=None,
        absolute_delta=None,
        score_status=None,
        severity=Severity.INFO,
        usernames=_names(usernames),
        measurement=measurement,
    )


def _healthy(
    detector: IdentityAnomalyType,
    measurement: IdentityMeasurement,
    reason: str,
    *,
    observed: float | None,
    expected: float | None = None,
    ratio: float | None = None,
    usernames: Sequence[NameEvidence] = (),
) -> IdentityVerdict:
    return IdentityVerdict(
        detector=detector,
        signal=DetectorSignal.HEALTHY,
        insufficient=False,
        reason=reason,
        observed=observed,
        expected=expected,
        ratio=ratio,
        robust_z=None,
        absolute_delta=None,
        score_status=None,
        severity=Severity.INFO,
        usernames=_names(usernames),
        measurement=measurement,
    )


def _anomalous(
    detector: IdentityAnomalyType,
    measurement: IdentityMeasurement,
    reason: str,
    *,
    observed: float | None,
    expected: float | None,
    ratio: float | None,
    robust_z: float | None,
    absolute_delta: float | None,
    score_status: str | None,
    severity: Severity,
    usernames: Sequence[NameEvidence],
) -> IdentityVerdict:
    return IdentityVerdict(
        detector=detector,
        signal=DetectorSignal.ANOMALOUS,
        insufficient=False,
        reason=reason,
        observed=observed,
        expected=expected,
        ratio=ratio,
        robust_z=robust_z,
        absolute_delta=absolute_delta,
        score_status=score_status,
        severity=severity,
        usernames=_names(usernames),
        measurement=measurement,
    )


def detect_threshold(
    detector: IdentityAnomalyType,
    *,
    event_id: int,
    count: float | None,
    measurement: IdentityMeasurement,
    usernames: Sequence[NameEvidence] = (),
) -> IdentityVerdict:
    """Fire at a count of at least 1. The scope comes from the declared spec."""
    scope = identity_verdict_scope(detector, measurement)
    if scope.scope is WafVerdictScope.NONE or count is None:
        if measurement is IdentityMeasurement.NOT_COLLECTED:
            reason = f"Event {event_id} was not collected."
        elif count is None:
            reason = f"Event {event_id} has no measured count."
        else:
            reason = f"Event {event_id} cannot be judged from this measurement."
        return _insufficient(detector, measurement, reason, observed=count)
    if count >= THRESHOLD_COUNT:
        return _anomalous(
            detector,
            measurement,
            reason=(
                f"Event {event_id} occurred {int(count)} times in the bucket "
                f"(threshold {THRESHOLD_COUNT})."
            ),
            observed=count,
            expected=None,
            ratio=None,
            robust_z=None,
            absolute_delta=None,
            score_status=None,
            severity=_THRESHOLD_SEVERITY[detector],
            usernames=usernames,
        )
    if scope.scope is WafVerdictScope.INCREASE_ONLY:
        return _insufficient(
            detector,
            measurement,
            (
                f"Event {event_id} has a truncated count below {THRESHOLD_COUNT}, "
                "so absence is not proven."
            ),
            observed=count,
        )
    return _healthy(
        detector,
        measurement,
        f"Event {event_id} was measured at zero.",
        observed=count,
    )


def detect_new_privileged_account(
    *,
    event_id: int,
    measurement: IdentityMeasurement,
    admission_truncated: bool,
    history_verified: bool,
    known_names: frozenset[str],
    usernames: Sequence[NameEvidence],
) -> IdentityVerdict:
    """A stored top-N username absent from a verified history.

    `history_verified` is the result of `verify_novelty_history`. A cut list or
    a gap is INSUFFICIENT_DATA for the series, even when one name looks new.
    """
    detector = IdentityAnomalyType.IDENTITY_NEW_PRIVILEGED_ACCOUNT
    scope = identity_verdict_scope(detector, measurement)
    if scope.scope is not WafVerdictScope.FULL:
        if measurement is IdentityMeasurement.NOT_COLLECTED:
            reason = f"Usernames for event {event_id} were not collected."
        else:
            reason = (
                f"The username list for event {event_id} is not whole, "
                "so a new account is not proven."
            )
        return _insufficient(detector, measurement, reason)
    if admission_truncated:
        return _insufficient(
            detector,
            measurement,
            (
                f"The username search for event {event_id} was cut, "
                "so a new account is not proven."
            ),
        )
    if not history_verified:
        return _insufficient(
            detector,
            measurement,
            (
                f"The {NOVELTY_HISTORY_DAYS}-day username history for event {event_id} "
                "is not complete."
            ),
        )
    fresh = tuple(item for item in usernames if item.username not in known_names)
    if fresh:
        return _anomalous(
            detector,
            measurement,
            reason=(
                f"A username in the stored top-N of event {event_id} was absent "
                f"from the verified {NOVELTY_HISTORY_DAYS}-day history."
            ),
            observed=float(len(fresh)),
            expected=0.0,
            ratio=None,
            robust_z=None,
            absolute_delta=None,
            score_status=None,
            severity=Severity.HIGH,
            usernames=fresh,
        )
    return _healthy(
        detector,
        measurement,
        f"Every stored username for event {event_id} was already in the verified history.",
        observed=0.0,
        expected=0.0,
    )


def _p95_upper(cell: CellSummary, mad_floor_ratio: float) -> float:
    spread = max(abs(cell.median) * mad_floor_ratio, 1e-9)
    return cell.p95 if cell.p95 is not None else cell.median + spread


def detect_rate_spike(
    detector: IdentityAnomalyType,
    *,
    event_id: int,
    count: float | None,
    measurement: IdentityMeasurement,
    cell: CellSummary | None,
    cold: bool,
    thresholds: SpikeThresholds,
    usernames: Sequence[NameEvidence] = (),
) -> IdentityVerdict:
    """Seasonal rate on the event-ID count. A cold band is never healthy.

    A lower bound fires only when every increase guard already passes on the
    floor. A floor inside the band is INSUFFICIENT_DATA.
    """
    scope = identity_verdict_scope(detector, measurement)
    if scope.scope is WafVerdictScope.NONE or count is None:
        if measurement is IdentityMeasurement.NOT_COLLECTED:
            reason = f"Event {event_id} was not collected."
        elif count is None:
            reason = f"Event {event_id} has no measured count."
        else:
            reason = f"Event {event_id} cannot be judged from this measurement."
        return _insufficient(detector, measurement, reason, observed=count)
    if cold or cell is None or not cell.is_reliable:
        return _insufficient(
            detector,
            measurement,
            (
                f"The seasonal band for event {event_id} is not ready, "
                "so the count is not judged healthy."
            ),
            observed=count,
            usernames=usernames,
        )
    expected = cell.median
    delta = count - expected
    observed_ratio = ratio(count, expected)
    not_proven = (
        count < thresholds.min_bucket_events
        or (observed_ratio is not None and observed_ratio < thresholds.ratio)
        or delta < thresholds.min_absolute_delta
    )
    score: float | None = None
    status: RobustScoreStatus | None = None
    if not not_proven:
        score, status = robust_z_with_status(
            count, cell.median, cell.mad, mad_floor_ratio=thresholds.mad_floor_ratio
        )
        if status is RobustScoreStatus.OK:
            if score < thresholds.deviation_z:
                not_proven = True
        else:
            upper = _p95_upper(cell, thresholds.mad_floor_ratio)
            if count <= upper:
                not_proven = True
    if not_proven:
        if scope.scope is WafVerdictScope.INCREASE_ONLY:
            return _insufficient(
                detector,
                measurement,
                (
                    f"The truncated count of event {event_id} does not prove an increase "
                    "over the seasonal band."
                ),
                observed=count,
                expected=expected,
                usernames=usernames,
            )
        return _healthy(
            detector,
            measurement,
            f"Event {event_id} is inside its seasonal band.",
            observed=count,
            expected=expected,
            ratio=observed_ratio,
            usernames=usernames,
        )
    assert score is not None and status is not None
    severity = (
        Severity.HIGH
        if observed_ratio is not None and observed_ratio >= SPIKE_HIGH_RATIO
        else Severity.MEDIUM
    )
    ratio_text = "undefined" if observed_ratio is None else f"{observed_ratio:.2f}"
    return _anomalous(
        detector,
        measurement,
        reason=(
            f"Event {event_id} is above its seasonal band "
            f"(observed {count:g}, median {expected:g}, ratio {ratio_text})."
        ),
        observed=count,
        expected=expected,
        ratio=observed_ratio,
        robust_z=score,
        absolute_delta=delta,
        score_status=status.value,
        severity=severity,
        usernames=usernames,
    )
