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
from typing import Any

from pydantic import BaseModel

from app.models.enums import AnomalyType, Criticality, SourceClass
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


class NoiseCurrentOut(BaseModel):
    """The source's current detection settings, shown beside the 17b replay."""

    criticality: Criticality
    custom_thresholds: dict[str, Any]
    maintenance_mode: bool
    maintenance_until: datetime | None = None
    business_hours_only: bool
    expected_interval_seconds: int | None = None


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
