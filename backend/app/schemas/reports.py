"""Report summary response shapes (SOC-8b, design `SOC-8.design.md` §1-§4).

Four blocks, each with its own `state`, so one block's failure or missing permission never
hides another. A figure that cannot be judged is null with a state, never a zero: the median
needs at least `MIN_FIGURE_N` rows, and a window older than a family's retention is `PARTIAL`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from app.models.enums import AnomalyFamily


class ReportPeriod(StrEnum):
    LAST_7D = "last_7d"
    LAST_30D = "last_30d"
    CALENDAR_WEEK = "calendar_week"
    CALENDAR_MONTH = "calendar_month"


class BlockState(StrEnum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    FORBIDDEN = "FORBIDDEN"


class FigureState(StrEnum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class Block(BaseModel):
    state: BlockState
    #: The permission a FORBIDDEN block needs.
    permission: str | None = None
    #: Closed set, see `app/services/reports.py` (`REASON_*`): why a block is not OK.
    reason_code: str | None = None


class AlertCell(BaseModel):
    #: `AlertFamily` value, or null for a `source_type` no family claims.
    family: str | None
    severity: str
    count: int


class AnomalyCell(BaseModel):
    family: AnomalyFamily
    severity: str
    count: int


class Clock(BaseModel):
    """A median in seconds. Null whenever `n` is below the floor."""

    median_seconds: float | None
    n: int
    state: FigureState


class FamilyCoverage(BaseModel):
    family: AnomalyFamily
    state: FigureState
    retention_days: int
    #: Set when PARTIAL: the earliest instant the family still holds closed rows for.
    covered_from: datetime | None


class NoiseRow(BaseModel):
    family: AnomalyFamily
    anomaly_type: str
    judged: int
    noise: int
    #: NOISE / judged; null unless the figure is OK or PARTIAL.
    ratio: float | None
    state: FigureState


class AlertsBlock(Block):
    rows: list[AlertCell] | None = None
    mtta: Clock | None = None
    mttr: Clock | None = None


class AnomaliesBlock(Block):
    rows: list[AnomalyCell] | None = None
    coverage: list[FamilyCoverage] | None = None


class AnomalyClocksBlock(Block):
    mtta: Clock | None = None
    mttr: Clock | None = None
    coverage: list[FamilyCoverage] | None = None


class NoiseBlock(Block):
    rows: list[NoiseRow] | None = None
    coverage: list[FamilyCoverage] | None = None


class ReportSummaryOut(BaseModel):
    period: ReportPeriod
    #: The resolved half-open range `[start, end)`, UTC.
    start: datetime
    end: datetime
    generated_at: datetime
    alerts: AlertsBlock
    anomalies: AnomaliesBlock
    anomaly_clocks: AnomalyClocksBlock
    noise_by_detector: NoiseBlock
