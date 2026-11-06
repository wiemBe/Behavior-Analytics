"""Wire shapes for the noisy-sources list (SOC-17a, design §2).

The schemas are presentation shapes, deliberately separate from the read model's
dataclasses. Two rules carried from ``app.schemas.anomaly`` apply unchanged:

  * an unmeasured count is ``None``, never ``0`` -- a not-collected source has
    null counts, and a monitored source with no incidents has a real zero;
  * every honesty marker travels with the number it qualifies, as a stable code
    plus its rendered English sentence, so a client never has to infer it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.anomaly.thresholds import ThresholdOrigin
from app.models.enums import AnomalyType, Criticality, SourceClass
from app.noise.backtest import (
    ReplayCounts,
    ReplayReason,
    ReplayResult,
    ReplayState,
)
from app.noise.read_model import (
    Coverage,
    CoverageState,
    DetectorSummary,
    Judgement,
    NoiseList,
    NoiseOrder,
    SourceDetail,
    SourceSummary,
)
from app.schemas.log_source import ThresholdConfig
from app.schemas.messages import MessageParamValue


class NoiseReasonOut(BaseModel):
    """A stable code, its raw parameters and the English sentence they render to."""

    code: str
    params: dict[str, MessageParamValue]
    text: str


class NoiseCoverageOut(BaseModel):
    """How much of the requested window the stored incidents cover."""

    state: CoverageState
    window_days: int
    covered_days: int
    retention_days: int
    reason: NoiseReasonOut | None = None


class NoiseDetectorOut(BaseModel):
    """One detector's counts for both windows; None means "not measured"."""

    anomaly_type: AnomalyType
    incidents_7d: int | None = None
    incidents_28d: int | None = None
    opened_7d: int | None = None
    opened_28d: int | None = None
    alerts_7d: int | None = None
    alerts_28d: int | None = None
    noise_closed: int | None = None
    noise_closed_total: int | None = None
    noise_share: float | None = None
    worst_ratio: float | None = None
    worst_z: float | None = None


class NoiseSourceOut(BaseModel):
    """One noisy-source row: both windows side by side plus the honesty marker."""

    log_source_id: uuid.UUID
    name: str
    instance_id: uuid.UUID
    instance_name: str | None = None
    criticality: Criticality
    source_class: SourceClass | None = None
    monitoring_enabled: bool
    judgement: Judgement
    judged: bool
    reason: NoiseReasonOut | None = None
    incidents_7d: int | None = None
    incidents_28d: int | None = None
    opened_7d: int | None = None
    opened_28d: int | None = None
    alerts_7d: int | None = None
    alerts_28d: int | None = None
    noise_closed: int | None = None
    noise_closed_total: int | None = None
    noise_share: float | None = None
    worst_ratio: float | None = None
    worst_z: float | None = None
    top_detector: AnomalyType | None = None
    evaluated_through: datetime | None = None


class NoiseEffectiveThresholdOut(BaseModel):
    """One resolved tuning knob and the layer that supplied it.

    ``source`` is ``default``, ``criticality``, ``lab_mode`` or ``custom`` --
    the same names the resolver reports internally. A stored override that never
    passed validation is passed through as-is (reads are never rejected), so the
    value is typed as ``Any`` rather than coerced into a number it is not.
    """

    value: Any
    source: ThresholdOrigin


class NoiseEffectiveThresholdsOut(BaseModel):
    """The six tuning knobs as the engine resolves them for this source now.

    The tuning form pre-fills from these values, never from a hard-coded
    default: a form showing 3.5 z while a custom 4.5 is in force would misstate
    what the engine does.
    """

    deviation_z: NoiseEffectiveThresholdOut
    spike_ratio: NoiseEffectiveThresholdOut
    drop_ratio: NoiseEffectiveThresholdOut
    min_absolute_delta_events: NoiseEffectiveThresholdOut
    open_after: NoiseEffectiveThresholdOut
    direction: NoiseEffectiveThresholdOut


class NoiseCurrentOut(BaseModel):
    """The source's current detection settings, shown beside the 17b replay."""

    criticality: Criticality
    custom_thresholds: dict[str, Any]
    maintenance_mode: bool
    maintenance_until: datetime | None = None
    business_hours_only: bool
    expected_interval_seconds: int | None = None
    effective_thresholds: NoiseEffectiveThresholdsOut
    #: True when LAB_MODE overrides the criticality hysteresis; criticality then
    #: has no detection effect (design §3.2).
    lab_mode: bool
    #: True when the stored overrides contain a non-empty ``by_type`` block. The
    #: page says one sentence about it: detector thresholds there are inert,
    #: only ``open_after``/``resolve_after`` move.
    has_by_type: bool


class NoiseSourceDetailOut(NoiseSourceOut):
    """One source with its per-detector breakdown and its current settings."""

    detectors: list[NoiseDetectorOut]
    current: NoiseCurrentOut
    coverage: NoiseCoverageOut
    #: 17d fills this with the active suppression. Always null in 17a.
    suppression: dict[str, Any] | None = None
    #: 17b fills this with the replay coverage. Always null in 17a.
    replay: dict[str, Any] | None = None


class NoiseListOut(BaseModel):
    """The fired rows, the quiet count and the coverage the counts rest on."""

    window_days: int
    order: NoiseOrder
    limit: int
    offset: int
    total: int
    zero_fired: int
    zero_fired_judged: int
    zero_fired_insufficient_data: int
    last_collection: datetime | None = None
    coverage: NoiseCoverageOut
    empty_reason: NoiseReasonOut | None = None
    items: list[NoiseSourceOut]


# --- SOC-17b: the 28-day replay preview -------------------------------------
class NoisePreviewIn(BaseModel):
    """The tuning page's proposed knobs and the window it asked for.

    The knobs are the same six the PATCH accepts, so a value that cannot be saved
    cannot be previewed either. ``window_days`` is 7 or 28.
    """

    window_days: Literal[7, 28] = 28
    thresholds: ThresholdConfig = ThresholdConfig()


class NoisePreviewWindowOut(BaseModel):
    """The requested window and the stored span it actually covers."""

    days: int
    first_bucket: datetime | None = None
    last_bucket: datetime | None = None
    covered_days: int


class NoisePreviewBaselineOut(BaseModel):
    """The baseline snapshot the replay judged against, and its lookback."""

    computed_at: datetime | None = None
    baseline_version: int | None = None
    lookback_days: int


class NoisePreviewDetectorOut(BaseModel):
    """One detector's incident count in one replay run.

    ``incidents`` is null exactly when ``excluded_by_direction`` is true: the
    run's direction disabled the detector, so it never ran and there is no
    measured zero to report. A detector that ran and found nothing stays ``0``.
    """

    anomaly_type: AnomalyType
    incidents: int | None = None
    excluded_by_direction: bool = False


class NoisePreviewCountsOut(BaseModel):
    """Incidents the replay would open: a total and its per-detector breakdown.

    ``total_incidents`` sums only the detectors that ran. A detector the run's
    direction excluded is listed with ``incidents: null`` and
    ``excluded_by_direction: true`` and contributes nothing to the total; it is
    "did not run", never a measured zero.
    """

    total_incidents: int
    by_detector: list[NoisePreviewDetectorOut]


class NoisePreviewRecordedOut(BaseModel):
    """What the engine actually recorded in the same window."""

    incidents: int
    opened: int
    suppressed: int


class NoisePreviewNotReplayableOut(BaseModel):
    anomaly_type: AnomalyType
    reason: NoiseReasonOut


class NoisePreviewOut(BaseModel):
    """Current versus proposed replay, with every fail-closed marker.

    ``current`` and ``proposed`` are null whenever ``available`` is false: a
    count the window cannot support is absent, never zero (design §3.5).

    Both runs use the same row rule: a detector the run's direction excluded is
    ``incidents: null`` with ``excluded_by_direction: true``. ``current`` also
    names such a detector in ``not_replayable`` (that list describes the current
    rule), so a client may render the row once and ignore the duplicate reason.
    """

    state: ReplayState
    available: bool
    window: NoisePreviewWindowOut
    baseline: NoisePreviewBaselineOut
    current: NoisePreviewCountsOut | None = None
    proposed: NoisePreviewCountsOut | None = None
    recorded: NoisePreviewRecordedOut | None = None
    not_replayable: list[NoisePreviewNotReplayableOut]
    not_judged_buckets: int | None = None
    maintenance_buckets: int | None = None
    warnings: list[NoiseReasonOut]
    reason: NoiseReasonOut | None = None


def _reason_out(value: Any) -> NoiseReasonOut | None:
    if value is None:
        return None
    return NoiseReasonOut(code=value.code, params=value.params, text=value.text)


def coverage_out(value: Coverage) -> NoiseCoverageOut:
    return NoiseCoverageOut(
        state=value.state,
        window_days=value.window_days,
        covered_days=value.covered_days,
        retention_days=value.retention_days,
        reason=_reason_out(value.reason),
    )


def detector_out(value: DetectorSummary) -> NoiseDetectorOut:
    return NoiseDetectorOut(
        anomaly_type=value.anomaly_type,
        incidents_7d=value.incidents_7d,
        incidents_28d=value.incidents_28d,
        opened_7d=value.opened_7d,
        opened_28d=value.opened_28d,
        alerts_7d=value.alerts_7d,
        alerts_28d=value.alerts_28d,
        noise_closed=value.noise_closed,
        noise_closed_total=value.noise_closed_total,
        noise_share=value.noise_share,
        worst_ratio=value.worst_ratio,
        worst_z=value.worst_z,
    )


def source_out(value: SourceSummary) -> NoiseSourceOut:
    return NoiseSourceOut(
        log_source_id=value.log_source_id,
        name=value.name,
        instance_id=value.instance_id,
        instance_name=value.instance_name,
        criticality=value.criticality,
        source_class=value.source_class,
        monitoring_enabled=value.monitoring_enabled,
        judgement=value.judgement,
        judged=value.judged,
        reason=_reason_out(value.reason),
        incidents_7d=value.incidents_7d,
        incidents_28d=value.incidents_28d,
        opened_7d=value.opened_7d,
        opened_28d=value.opened_28d,
        alerts_7d=value.alerts_7d,
        alerts_28d=value.alerts_28d,
        noise_closed=value.noise_closed,
        noise_closed_total=value.noise_closed_total,
        noise_share=value.noise_share,
        worst_ratio=value.worst_ratio,
        worst_z=value.worst_z,
        top_detector=value.top_detector,
        evaluated_through=value.evaluated_through,
    )


def _effective_thresholds_out(value: SourceDetail) -> NoiseEffectiveThresholdsOut:
    knobs = {
        item.knob: NoiseEffectiveThresholdOut(value=item.value, source=item.source)
        for item in value.current.effective_thresholds
    }
    return NoiseEffectiveThresholdsOut(
        deviation_z=knobs["deviation_z"],
        spike_ratio=knobs["spike_ratio"],
        drop_ratio=knobs["drop_ratio"],
        min_absolute_delta_events=knobs["min_absolute_delta_events"],
        open_after=knobs["open_after"],
        direction=knobs["direction"],
    )


def detail_out(value: SourceDetail) -> NoiseSourceDetailOut:
    source = source_out(value.source)
    return NoiseSourceDetailOut(
        **source.model_dump(),
        detectors=[detector_out(item) for item in value.detectors],
        current=NoiseCurrentOut(
            criticality=value.current.criticality,
            custom_thresholds=value.current.custom_thresholds,
            maintenance_mode=value.current.maintenance_mode,
            maintenance_until=value.current.maintenance_until,
            business_hours_only=value.current.business_hours_only,
            expected_interval_seconds=value.current.expected_interval_seconds,
            effective_thresholds=_effective_thresholds_out(value),
            lab_mode=value.current.lab_mode,
            has_by_type=value.current.has_by_type,
        ),
        coverage=coverage_out(value.coverage),
        suppression=value.suppression,
        replay=value.replay,
    )


def list_out(value: NoiseList) -> NoiseListOut:
    return NoiseListOut(
        window_days=value.window_days,
        order=value.order,
        limit=value.limit,
        offset=value.offset,
        total=value.total,
        zero_fired=value.zero_fired,
        zero_fired_judged=value.zero_fired_judged,
        zero_fired_insufficient_data=value.zero_fired_insufficient_data,
        last_collection=value.last_collection,
        coverage=coverage_out(value.coverage),
        empty_reason=_reason_out(value.empty_reason),
        items=[source_out(item) for item in value.items],
    )


def _replay_reason_out(value: ReplayReason) -> NoiseReasonOut:
    return NoiseReasonOut(code=value.code, params=value.params, text=value.text)


def _preview_counts_out(value: ReplayCounts | None) -> NoisePreviewCountsOut | None:
    if value is None:
        return None
    return NoisePreviewCountsOut(
        total_incidents=value.total_incidents,
        by_detector=[
            NoisePreviewDetectorOut(
                anomaly_type=item.anomaly_type,
                incidents=item.incidents,
                excluded_by_direction=item.excluded_by_direction,
            )
            for item in value.by_detector
        ],
    )


def preview_out(value: ReplayResult) -> NoisePreviewOut:
    recorded = value.recorded
    return NoisePreviewOut(
        state=value.state,
        available=value.available,
        window=NoisePreviewWindowOut(
            days=value.window.days,
            first_bucket=value.window.first_bucket,
            last_bucket=value.window.last_bucket,
            covered_days=value.window.covered_days,
        ),
        baseline=NoisePreviewBaselineOut(
            computed_at=value.baseline.computed_at,
            baseline_version=value.baseline.baseline_version,
            lookback_days=value.baseline.lookback_days,
        ),
        current=_preview_counts_out(value.current),
        proposed=_preview_counts_out(value.proposed),
        recorded=(
            None
            if recorded is None
            else NoisePreviewRecordedOut(
                incidents=recorded.incidents,
                opened=recorded.opened,
                suppressed=recorded.suppressed,
            )
        ),
        not_replayable=[
            NoisePreviewNotReplayableOut(
                anomaly_type=item.anomaly_type,
                reason=_replay_reason_out(item.reason),
            )
            for item in value.not_replayable
        ],
        not_judged_buckets=value.not_judged_buckets,
        maintenance_buckets=value.maintenance_buckets,
        warnings=[_replay_reason_out(item) for item in value.warnings],
        reason=(
            None if value.reason is None else _replay_reason_out(value.reason)
        ),
    )
