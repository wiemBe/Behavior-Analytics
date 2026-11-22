"""Pure JDBC projections over stored inventory and source-volume facts."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.models.enums import BucketCompleteness
from app.services.search_cadence import slot_start

FlowState = Literal[
    "DISABLED_IN_QRADAR", "RECEIVING", "NO_ATTRIBUTED_EVENTS", "NOT_MEASURED",
    "ABSENT_FROM_INVENTORY",
]
Measurement = Literal["EXACT", "PARTIAL", "TRUNCATED", "NOT_COLLECTED"]
ConnectionCode = Literal["CONNECTED", "WARNING", "CONNECTION_ERROR", "DISABLED_STATUS"]
Inconsistency = Literal["enabled_with_disabled_status"]
AttemptState = Literal[
    "IDLE", "QUEUED", "RUNNING", "COMPLETE", "PARTIAL", "FAILED", "NOT_COLLECTED", "UNSUPPORTED"
]
ReasonCode = Literal[
    "disabled_in_qradar", "measured", "partial", "truncated", "not_collected", "unmonitored",
    "inventory_stale", "inventory_not_collected"
]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def target_bucket(
    now: datetime, *, cadence_seconds: int, width_seconds: int, anchor: int
) -> tuple[datetime, datetime]:
    """Return [D-width,D); anchor is the cadence service's settle offset in seconds."""
    if cadence_seconds <= 0 or width_seconds <= 0 or anchor < 0:
        raise ValueError("invalid cadence, width or settle offset")
    end = slot_start(now, cadence_seconds, anchor)
    return end - timedelta(seconds=width_seconds), end


def observation_stale(
    last_observed_at: datetime | None, now: datetime, *, cadence_seconds: int, width_seconds: int
) -> bool:
    if cadence_seconds <= 0 or width_seconds <= 0:
        raise ValueError("cadence and width must be positive")
    return last_observed_at is None or _utc(now) - _utc(last_observed_at) > timedelta(
        seconds=2 * cadence_seconds + width_seconds
    )


@dataclass(frozen=True)
class LastObservation:
    start: datetime
    end: datetime
    observed_at: datetime
    count: int | None
    completeness: BucketCompleteness
    origin: Literal["live", "history"]


@dataclass(frozen=True)
class FlowResult:
    state: FlowState
    measurement: Measurement
    count: int | None
    target_start: datetime
    target_end: datetime
    last_observation: LastObservation | None


def flow_state(
    *, enabled: bool, target: tuple[datetime, datetime], monitored: bool,
    row_present: bool = False, completeness: BucketCompleteness | None = None,
    origin: str | None = None, count: int | None = None, fleet_empty: bool = False,
    truncated_attempt: bool = False, last_observation: LastObservation | None = None,
    absent: bool = False,
) -> FlowResult:
    """Only a complete live target row can assert attributed arrival or an exact zero.

    Absence is decided from a completed inventory, not from a missing bucket; it takes
    precedence over the stored flag and any current flow judgment, while the separately
    dated last observation stays on the result.
    """
    state: FlowState = "NOT_MEASURED"
    measurement: Measurement = "NOT_COLLECTED"
    exact_count: int | None = None
    if absent:
        state = "ABSENT_FROM_INVENTORY"
    elif not enabled:
        state = "DISABLED_IN_QRADAR"
    elif row_present and origin == "live" and (
        completeness == BucketCompleteness.PARTIAL or fleet_empty
    ):
        measurement = "PARTIAL"
    elif truncated_attempt and (not row_present or origin == "live"):
        measurement = "TRUNCATED"
    elif (row_present and origin == "live" and completeness == BucketCompleteness.COMPLETE
          and type(count) is int and count >= 0):
        state = "RECEIVING" if count > 0 else "NO_ATTRIBUTED_EVENTS"
        measurement, exact_count = "EXACT", count
    return FlowResult(state, measurement, exact_count, _utc(target[0]), _utc(target[1]),
                      last_observation)


def connection_status(
    raw: str | None, *, enabled: bool = False
) -> tuple[ConnectionCode | None, Inconsistency | None]:
    codes: dict[str, ConnectionCode] = {
        "SUCCESS": "CONNECTED", "WARN": "WARNING", "ERROR": "CONNECTION_ERROR",
        "DISABLED": "DISABLED_STATUS",
    }
    conflict: Inconsistency | None = "enabled_with_disabled_status" if enabled and (
        raw == "DISABLED") else None
    return codes.get(raw or ""), conflict


@dataclass(frozen=True)
class InventoryFreshness:
    last_attempt_state: AttemptState
    stale: bool
    reason_code: ReasonCode | None


def inventory_freshness(
    last_complete_at: datetime | None, last_attempt_state: AttemptState, now: datetime,
    *, cadence_seconds: int,
) -> InventoryFreshness:
    if cadence_seconds <= 0:
        raise ValueError("cadence must be positive")
    stale = last_complete_at is None or _utc(now) - _utc(last_complete_at) > timedelta(
        seconds=2 * cadence_seconds
    )
    return InventoryFreshness(
        last_attempt_state, stale,
        "inventory_not_collected" if last_complete_at is None else "inventory_stale" if stale
        else None,
    )
