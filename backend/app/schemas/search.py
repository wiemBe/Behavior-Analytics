"""API schemas for scheduled searches, versions and executions."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.i18n.messages import MessageRef, render
from app.models.enums import Severity, VisualizationType
from app.schemas.messages import MessageParamValue
from app.services.aql_validator import AQLValidationError, validate_aql


def unreachable_threshold_reason(
    operator: str, threshold_value: float | None, max_result_rows: int | None
) -> str | None:
    """The English sentence of `unreachable_threshold_ref`, or None when it can be reached."""
    ref = unreachable_threshold_ref(operator, threshold_value, max_result_rows)
    return None if ref is None else render(ref)


def unreachable_threshold_ref(
    operator: str, threshold_value: float | None, max_result_rows: int | None
) -> MessageRef | None:
    """Why a threshold can never be breached, or None when it can.

    A run returns at most ``max_result_rows`` rows, and a truncated run proves
    only that the true count is at least ``max_result_rows + 1``. A strict-
    greater threshold at or above the cap, or a greater-or-equal threshold above
    the cap-plus-one, is therefore outside anything the search can ever observe.
    The ``LT``/``LE``/``EQ`` operators and a ``None`` threshold are unaffected.
    """
    if threshold_value is None or max_result_rows is None:
        return None
    boundary = max_result_rows + 1
    unreachable = (operator == "GT" and threshold_value >= boundary) or (
        operator == "GE" and threshold_value > boundary
    )
    if not unreachable:
        return None
    return MessageRef(
        "search.threshold.unreachable",
        {
            "operator": operator,
            "threshold": f"{threshold_value:g}",
            "max_result_rows": max_result_rows,
        },
    )


class ThresholdUnreachableError(ValueError):
    """Raised when a create or update would store a never-breachable threshold.

    Built from the message code (`.ref`); `str(exc)` is its English sentence.
    """

    def __init__(self, reason: MessageRef | str) -> None:
        self.ref: MessageRef | None = reason if isinstance(reason, MessageRef) else None
        super().__init__(render(reason) if isinstance(reason, MessageRef) else reason)


class AuthoringDetail(TypedDict):
    """422 `detail` for an unsafe query or an unreachable threshold.

    `reasons` is every problem, unparaphrased: `AQLValidationError.reasons` in
    the validator's order, then the threshold-reachability sentence when it
    applies. `message` is the compatibility string — the same text the schema
    validators already raise (`unsafe AQL: ` plus those reasons joined by
    `"; "`, and the threshold sentence). It is composed from the reasons and carries no code
    of its own; no view shows it (the editors list `reasons`). `reason_codes` is parallel to
    `reasons`: `{code, params}` or `None` for a reason with no code.
    """

    message: str
    reasons: list[str]
    reason_codes: list[dict[str, object] | None]


_THRESHOLD_OPERATORS = frozenset({"GT", "GE", "LT", "LE", "EQ"})


def _compat_message(aql_reasons: list[str], threshold_reason: str | None) -> str:
    """The string the API already returned, kept beside the reasons list."""
    parts: list[str] = []
    if aql_reasons:
        # `str(AQLValidationError)` is the reasons joined by "; ". The schema
        # validators raise `ValueError(f"unsafe AQL: {exc}")`, so this is that
        # same string.
        parts.append("unsafe AQL: " + "; ".join(aql_reasons))
    if threshold_reason is not None:
        parts.append(threshold_reason)
    return "; ".join(parts)


def _aql_reasons(
    body: Mapping[str, Any], *, check_aql: bool
) -> list[tuple[str, MessageRef | None]]:
    if not check_aql or "aql_query" not in body:
        return []
    aql = body["aql_query"]
    if not isinstance(aql, str):
        return []
    try:
        validate_aql(aql)
    except AQLValidationError as exc:
        return list(zip(exc.reasons, exc.refs, strict=True))
    return []


def _resolved_threshold(
    body: Mapping[str, Any],
    *,
    current_operator: str,
    current_threshold: float | None,
    current_max_rows: int,
) -> tuple[str, float | None, int] | None:
    """Effective threshold inputs, or None when a field is the wrong JSON type.

    A wrong type is the schema's 422, not an authoring reason. Skipping it here
    lets that error surface unchanged.
    """
    operator = body.get("threshold_operator", current_operator)
    if not isinstance(operator, str) or operator not in _THRESHOLD_OPERATORS:
        return None

    if "threshold_value" in body:
        raw_threshold = body["threshold_value"]
        if raw_threshold is None:
            threshold: float | None = None
        elif isinstance(raw_threshold, bool) or not isinstance(raw_threshold, (int, float)):
            return None
        else:
            threshold = float(raw_threshold)
    else:
        threshold = current_threshold

    if "max_result_rows" in body:
        raw_rows = body["max_result_rows"]
        if isinstance(raw_rows, bool) or not isinstance(raw_rows, int):
            return None
        max_rows = raw_rows
    else:
        max_rows = current_max_rows
    return operator, threshold, max_rows


def authoring_detail_for_body(
    body: Mapping[str, Any],
    *,
    current_operator: str = "GT",
    current_threshold: float | None = None,
    current_max_rows: int = 10_000,
    check_aql: bool = True,
    check_threshold: bool = True,
) -> AuthoringDetail | None:
    """Build the 422 detail, or None when these checks pass.

    Create calls this with the schema defaults (`GT`, no threshold, 10_000
    rows) so an omitted cap is the cap the row would store. Update calls it
    with the stored values, and passes `check_aql=False` once the query text
    has already been accepted. Reasons are copied off the validator; they are
    not rewritten.
    """
    aql_pairs = _aql_reasons(body, check_aql=check_aql)
    aql_reasons = [text for text, _ref in aql_pairs]
    threshold_ref: MessageRef | None = None
    threshold_reason: str | None = None
    if check_threshold:
        resolved = _resolved_threshold(
            body,
            current_operator=current_operator,
            current_threshold=current_threshold,
            current_max_rows=current_max_rows,
        )
        if resolved is not None:
            threshold_ref = unreachable_threshold_ref(*resolved)
            threshold_reason = None if threshold_ref is None else render(threshold_ref)
    if not aql_reasons and threshold_reason is None:
        return None
    reasons = list(aql_reasons)
    refs = [ref for _text, ref in aql_pairs]
    if threshold_reason is not None:
        reasons.append(threshold_reason)
        refs.append(threshold_ref)
    return AuthoringDetail(
        message=_compat_message(aql_reasons, threshold_reason),
        reasons=reasons,
        reason_codes=[None if ref is None else ref.to_wire() for ref in refs],
    )


class ScheduledSearchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    owner: str | None = Field(default=None, max_length=255)
    category: str | None = Field(default=None, max_length=128)
    mitre_techniques: list[str] = Field(default_factory=list)
    aql_query: str = Field(min_length=1)
    schedule_cron: str = Field(min_length=1, max_length=128)
    schedule_timezone: str = Field(default="UTC", max_length=64)
    threshold_value: float | None = None
    threshold_operator: str = Field(default="GT", pattern="^(GT|GE|LT|LE|EQ)$")
    severity: Severity = Severity.MEDIUM
    timeout_seconds: int = Field(default=300, gt=0)
    max_time_range_hours: int = Field(default=24, gt=0)
    max_result_rows: int = Field(default=10_000, gt=0)
    visualization_type: VisualizationType = VisualizationType.TABLE
    enabled: bool = True

    @field_validator("aql_query")
    @classmethod
    def _validate_aql(cls, v: str) -> str:
        # Structural safety check at authoring time. The executor re-validates
        # against the per-search limits before every run.
        try:
            validate_aql(v)
        except AQLValidationError as exc:
            raise ValueError(f"unsafe AQL: {exc}") from exc
        return v

    @model_validator(mode="after")
    def _threshold_must_be_reachable(self) -> ScheduledSearchCreate:
        # A threshold the search can never breach would silently never alert;
        # refuse it here so the analyst hears about it while authoring.
        reason = unreachable_threshold_reason(
            self.threshold_operator, self.threshold_value, self.max_result_rows
        )
        if reason is not None:
            raise ValueError(reason)
        return self


class ScheduledSearchUpdate(BaseModel):
    """Editable fields. Changing aql_query mints a new immutable version."""

    description: str | None = Field(default=None, max_length=2000)
    owner: str | None = Field(default=None, max_length=255)
    category: str | None = Field(default=None, max_length=128)
    mitre_techniques: list[str] | None = None
    aql_query: str | None = None
    schedule_cron: str | None = Field(default=None, max_length=128)
    schedule_timezone: str | None = Field(default=None, max_length=64)
    threshold_value: float | None = None
    threshold_operator: str | None = Field(default=None, pattern="^(GT|GE|LT|LE|EQ)$")
    severity: Severity | None = None
    timeout_seconds: int | None = Field(default=None, gt=0)
    max_time_range_hours: int | None = Field(default=None, gt=0)
    max_result_rows: int | None = Field(default=None, gt=0)
    visualization_type: VisualizationType | None = None
    enabled: bool | None = None
    change_note: str | None = Field(default=None, max_length=1000)

    @field_validator("aql_query")
    @classmethod
    def _validate_aql(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            validate_aql(v)
        except AQLValidationError as exc:
            raise ValueError(f"unsafe AQL: {exc}") from exc
        return v


class SearchVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: int
    aql_query: str
    changed_by: str | None
    change_note: str | None
    created_at: datetime


class ScheduledSearchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    owner: str | None
    category: str | None
    mitre_techniques: list[str]
    aql_query: str
    query_version: int
    schedule_cron: str
    schedule_timezone: str
    threshold_value: float | None
    threshold_operator: str
    severity: Severity
    timeout_seconds: int
    max_time_range_hours: int
    max_result_rows: int
    visualization_type: VisualizationType
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    consecutive_failures: int


class ExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    search_id: uuid.UUID
    query_version: int
    status: str
    trigger: str
    triggered_by: str | None
    ariel_search_id: str | None
    ariel_status: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    result_count: int | None
    truncated: bool
    error_type: str | None
    error_message: str | None
    #: Stable code and raw values of a fixed `error_message`; null on an old row and for free text.
    error_message_code: str | None = None
    error_message_params: dict[str, MessageParamValue] | None = None
    retry_count: int
    threshold_breached: bool


class ResultMetricPoint(BaseModel):
    """One stored aggregate, joined to the execution that produced it.

    `query_version` is carried per point on purpose: results either side of an
    AQL change are not comparable, so the chart must be able to mark where the
    query changed rather than drawing one continuous line across the boundary.
    """

    model_config = ConfigDict(from_attributes=True)

    bucket_start: datetime
    metric_key: str
    value: float
    dimensions: dict = Field(default_factory=dict)

    execution_id: uuid.UUID
    execution_status: str
    duration_ms: int | None
    result_count: int | None
    threshold_breached: bool

    query_version: int
    query_version_id: uuid.UUID | None


class SearchResultTrendOut(BaseModel):
    """Chronological result trend for one search and one metric key.

    `threshold_value` is the search's *current* threshold, so it is reported once
    at the top level rather than per point -- a stored point carries whether it
    breached at the time (`threshold_breached`), which is the value that stays
    true retrospectively.
    """

    search_id: uuid.UUID
    metric_key: str
    threshold_value: float | None
    threshold_operator: str
    count: int
    points: list[ResultMetricPoint]


class MetricKeyStat(BaseModel):
    """One stored metric key: how many points it has, and when the latest was."""

    metric_key: str
    point_count: int
    last_bucket_start: datetime


class MetricKeyListOut(BaseModel):
    """Distinct metric keys of one search, newest bucket first, `total` leading.

    `total` is the number of distinct keys. `count` is how many this response
    holds after the limit. An unknown search is 404, not an empty list.
    """

    search_id: uuid.UUID
    total: int
    count: int
    keys: list[MetricKeyStat]


class LatestGroupRow(BaseModel):
    """One stored aggregate of the latest execution that stored any row."""

    metric_key: str
    dimensions: dict[str, Any] = Field(default_factory=dict)
    value: float


class LatestGroupsOut(BaseModel):
    """Grouped rows of the latest execution that stored a metric row.

    Ordered by value, descending. `empty` says why `rows` is empty:
    `no_execution` when the search has no execution, `no_rows` when every
    execution stored nothing. Both are 200. An unknown search is 404.
    `values_are_lower_bounds` is true when that execution was truncated: each
    value is a floor, so the UI shows "≥ n" and does not treat it as measured.
    """

    search_id: uuid.UUID
    execution_id: uuid.UUID | None = None
    started_at: datetime | None = None
    status: str | None = None
    truncated: bool = False
    values_are_lower_bounds: bool = False
    empty: Literal["no_execution", "no_rows"] | None = None
    total: int = 0
    rows: list[LatestGroupRow] = Field(default_factory=list)
