"""Resolve anomaly thresholds and hysteresis for a log source.

Resolution order (later overrides earlier):
  1. Global defaults (from Settings).
  2. Per-criticality adjustments (critical sources are more sensitive and open
     faster; low-criticality sources are damped).
  3. Per-source overrides in LogSource.custom_thresholds.

Hysteresis (N intervals to open, M to resolve) is likewise resolvable per
anomaly type and per criticality, so a CRITICAL source's outage opens after one
interval while a noisy LOW source needs several.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.config import Settings, get_settings
from app.models.enums import AnomalyType, Criticality

#: The six knobs the tuning page shows, in display order (design §3.1). They are
#: a subset of ``Thresholds``: the legacy fields stay accepted and keep working,
#: but the page neither shows nor writes them.
TUNING_KNOBS: tuple[str, ...] = (
    "deviation_z",
    "spike_ratio",
    "drop_ratio",
    "min_absolute_delta_events",
    "open_after",
    "direction",
)

#: The two volume directions a source may tune away, and `both` (today's behaviour).
#: Stored in `custom_thresholds["direction"]` as a plain string; an unknown stored
#: value is treated as `both`, because a row written before validation exists must
#: keep the behaviour it has today.
DIRECTION_BOTH = "both"
DIRECTION_SPIKE = "spike"
DIRECTION_DROP = "drop"
DIRECTIONS = frozenset({DIRECTION_BOTH, DIRECTION_SPIKE, DIRECTION_DROP})


def direction_excluded(direction: object) -> dict[AnomalyType, str]:
    """Detectors a source's `direction` knob excludes, with their reason.

    A spike-only source does not run the drop detector and a drop-only source
    does not run the spike detector. Every other value -- including `both` and a
    malformed value stored before validation existed -- excludes nothing.
    """
    if direction == DIRECTION_SPIKE:
        return {AnomalyType.VOLUME_DROP: "direction is spike-only"}
    if direction == DIRECTION_DROP:
        return {AnomalyType.VOLUME_SPIKE: "direction is drop-only"}
    return {}


@dataclass(frozen=True)
class Thresholds:
    # deviation z-score magnitude to flag volume anomalies
    deviation_z: float = 3.5
    # --- Phase A volume guards, all conjunctive with deviation_z ------------
    # observed/expected must reach this to be a spike candidate
    spike_ratio: float = 2.0
    # observed/expected must fall to this to be a drop candidate
    drop_ratio: float = 0.5
    # |observed - expected| in events per bucket; kills the 0.2 -> 0.4 EPS case
    min_absolute_delta_events: float = 100.0
    # a source must be moving this many events/bucket to be judged at all
    min_bucket_events: float = 50.0
    # Which volume direction the detectors watch: "both" runs spike and drop.
    # Only these two detectors read it; every other detector is directionless.
    direction: str = DIRECTION_BOTH
    # empty buckets tolerated before NO_EVENTS fires
    silence_grace_buckets: int = 1
    # ratio floors/ceilings
    min_parsed_ratio: float = 0.5           # below -> PARSING_DEGRADATION
    max_unknown_ratio: float = 0.2          # above -> UNKNOWN_EVENT_SPIKE
    # timestamp delay: multiple of the expected interval
    delay_interval_multiple: float = 3.0
    # cardinality drop: fraction of baseline below which we flag
    cardinality_drop_fraction: float = 0.4
    # collection errors above this flag COLLECTION_ERROR
    collection_error_threshold: int = 0
    # repeated payload across >= this many consecutive intervals
    repeated_payload_intervals: int = 3
    # hysteresis
    open_after: int = 2
    resolve_after: int = 3


# Per-criticality multipliers/overrides. CRITICAL opens after a single interval.
_CRITICALITY_OPEN_AFTER = {
    Criticality.CRITICAL: 1,
    Criticality.HIGH: 2,
    Criticality.MEDIUM: 2,
    Criticality.LOW: 3,
}
_CRITICALITY_RESOLVE_AFTER = {
    Criticality.CRITICAL: 2,
    Criticality.HIGH: 3,
    Criticality.MEDIUM: 3,
    Criticality.LOW: 4,
}


class ThresholdOrigin(StrEnum):
    """The layer that supplied a resolved value, in the resolver's own order."""

    DEFAULT = "default"
    CRITICALITY = "criticality"
    LAB_MODE = "lab_mode"
    CUSTOM = "custom"


@dataclass(frozen=True)
class ResolvedThresholds:
    """Resolved values plus, per ``Thresholds`` field, the layer that set it.

    The tuning page needs the number the engine actually uses and where it came
    from; it must not re-derive either. ``resolve`` and ``resolve_traced`` share
    one implementation, so the engine and the page cannot drift.
    """

    values: Thresholds
    origins: Mapping[str, ThresholdOrigin]

    def origin(self, knob: str) -> ThresholdOrigin:
        return self.origins.get(knob, ThresholdOrigin.DEFAULT)


@dataclass
class ThresholdResolver:
    settings: Settings = field(default_factory=get_settings)

    def resolve(
        self,
        *,
        criticality: Criticality,
        custom: dict | None = None,
        anomaly_type: AnomalyType | None = None,
    ) -> Thresholds:
        return self.resolve_traced(
            criticality=criticality, custom=custom, anomaly_type=anomaly_type
        ).values

    def resolve_traced(
        self,
        *,
        criticality: Criticality,
        custom: dict | None = None,
        anomaly_type: AnomalyType | None = None,
    ) -> ResolvedThresholds:
        """Resolve exactly as ``resolve`` does, and record each field's origin."""
        origins: dict[str, ThresholdOrigin] = dict.fromkeys(
            Thresholds.__dataclass_fields__, ThresholdOrigin.DEFAULT
        )
        # LAB_MODE deliberately uses the explicit 1/2 demo hysteresis for all
        # criticalities. Production retains the established criticality matrix.
        if self.settings.lab_mode:
            open_after = self.settings.anomaly_open_after_intervals
            resolve_after = self.settings.anomaly_resolve_after_intervals
            origins["open_after"] = ThresholdOrigin.LAB_MODE
            origins["resolve_after"] = ThresholdOrigin.LAB_MODE
        else:
            open_after = _CRITICALITY_OPEN_AFTER.get(
                criticality, self.settings.anomaly_open_after_intervals
            )
            resolve_after = _CRITICALITY_RESOLVE_AFTER.get(
                criticality, self.settings.anomaly_resolve_after_intervals
            )
            if criticality in _CRITICALITY_OPEN_AFTER:
                origins["open_after"] = ThresholdOrigin.CRITICALITY
            if criticality in _CRITICALITY_RESOLVE_AFTER:
                origins["resolve_after"] = ThresholdOrigin.CRITICALITY
        # The volume guards are NOT part of the LAB_MODE profile. LAB_MODE may
        # shorten timing (bucket width, sample count, confirmation and recovery
        # counts) but never lowers a detection threshold: a lab that shows a
        # detector production does not run is worse than no lab at all.
        base = Thresholds(
            deviation_z=self.settings.anomaly_deviation_threshold,
            spike_ratio=self.settings.anomaly_spike_ratio,
            drop_ratio=self.settings.anomaly_drop_ratio,
            min_absolute_delta_events=self.settings.anomaly_min_absolute_delta_events,
            min_bucket_events=self.settings.anomaly_min_bucket_events,
            silence_grace_buckets=self.settings.anomaly_silence_grace_buckets,
            open_after=open_after,
            resolve_after=resolve_after,
        )
        merged = base
        if custom:
            merged = self._apply_overrides(base, custom, anomaly_type, origins)
        return ResolvedThresholds(values=merged, origins=origins)

    def _apply_overrides(
        self,
        base: Thresholds,
        custom: dict,
        anomaly_type: AnomalyType | None,
        origins: dict[str, ThresholdOrigin],
    ) -> Thresholds:
        # custom_thresholds shape: {"deviation_z": 4.0, "open_after": 1,
        #   "by_type": {"VOLUME_SPIKE": {"deviation_z": 5.0}}}
        values = {k: getattr(base, k) for k in base.__dataclass_fields__}
        for key in values:
            if key in custom and not isinstance(custom[key], dict):
                values[key] = custom[key]
                origins[key] = ThresholdOrigin.CUSTOM
        by_type = custom.get("by_type", {})
        if anomaly_type is not None and anomaly_type.value in by_type:
            for key, val in by_type[anomaly_type.value].items():
                if key in values:
                    values[key] = val
                    origins[key] = ThresholdOrigin.CUSTOM
        return Thresholds(**values)
