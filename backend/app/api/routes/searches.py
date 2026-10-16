"""Scheduled-search catalog, versions, executions, and authorized manual run."""

from __future__ import annotations

import copy
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.routing import default_policy
from app.alerts.search_alerts import SearchAlertEvaluator
from app.api.deps import ProviderDep, SessionDep, SettingsDep
from app.core.config import get_settings
from app.models.search import SearchExecution, SearchQueryVersion, SearchResultMetric
from app.repositories.audit import record_audit
from app.schemas.search import (
    AuthoringDetail,
    ExecutionOut,
    ResultMetricPoint,
    ScheduledSearchCreate,
    ScheduledSearchOut,
    ScheduledSearchUpdate,
    SearchResultTrendOut,
    SearchVersionOut,
    ThresholdUnreachableError,
    authoring_detail_for_body,
)
from app.security.auth import get_principal
from app.security.rbac import (
    PERM_SEARCH_EXECUTE,
    PERM_SEARCH_WRITE,
    PermissionDenied,
    Principal,
    require_permission,
)
from app.services.concurrency import InMemoryConcurrencyLimiter
from app.services.scheduled_search import EnabledSearchLimitError, ScheduledSearchService
from app.services.search_executor import SearchExecutor
from app.services.search_scheduler import CronFloorError

router = APIRouter(prefix="/searches", tags=["searches"])

PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _request_body(model: type[BaseModel]) -> dict[str, Any]:
    """OpenAPI body for a handler that parses the JSON itself.

    The handler takes a dict so an authoring failure can be the reasons
    object instead of FastAPI's list. The documented body stays the model's
    schema. `$ref`s are inlined: a nested schema's `#/$defs/...` would point
    at the OpenAPI document root, not at this body.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = str(node["$ref"]).rsplit("/", 1)[-1]
                resolved = resolve(copy.deepcopy(defs[name]))
                extras = {
                    key: resolve(value) for key, value in node.items() if key != "$ref"
                }
                resolved.update(extras)
                return resolved
            return {key: resolve(value) for key, value in node.items()}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    inlined = resolve(schema)
    # FastAPI's generated schema for `dict` sets additionalProperties. The
    # model does not.
    inlined["additionalProperties"] = False
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": inlined}},
        }
    }


_CREATE_BODY = _request_body(ScheduledSearchCreate)
_UPDATE_BODY = _request_body(ScheduledSearchUpdate)


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalise a query-string bound to UTC.

    A naive timestamp is read as UTC rather than rejected: the whole platform
    stores and reports UTC, so that is the only defensible reading, and it keeps
    `?start=2026-07-01T00:00:00` working alongside an explicit offset.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _guard(principal: Principal, permission: str) -> None:
    try:
        require_permission(principal, permission)
    except PermissionDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


def _is_authoring_error(error: Any) -> bool:
    """True for the AQL and threshold validators, which get the reasons object.

    Any other pydantic error (a missing field, a bad type) stays in FastAPI's
    list, including when it arrives beside one of these.
    """
    if not isinstance(error, Mapping):
        return False
    if error.get("type") != "value_error":
        return False
    raw_loc = error.get("loc", ())
    loc = tuple(raw_loc) if isinstance(raw_loc, (tuple, list)) else ()
    msg = str(error.get("msg", ""))
    if loc == ("aql_query",) and "unsafe AQL:" in msg:
        return True
    return loc == () and "can never be reached" in msg


def _request_validation(exc: ValidationError) -> RequestValidationError:
    """Schema errors keep FastAPI's list shape. `loc` stays under `body`."""
    errors: list[dict[str, Any]] = []
    for error in exc.errors():
        copied: dict[str, Any] = dict(error)
        raw_loc = copied.get("loc", ())
        loc = tuple(raw_loc) if isinstance(raw_loc, (tuple, list)) else ()
        if not loc or loc[0] != "body":
            copied["loc"] = ("body", *loc)
        errors.append(copied)
    return RequestValidationError(errors)


def _authoring_http(detail: AuthoringDetail) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, dict(detail))


def _parse_create(body: dict[str, Any]) -> ScheduledSearchCreate:
    """Schema list first; an authoring failure is the reasons object.

    AQL and the threshold are decided before the write permission, which is
    where the schema validators used to run (before the handler). When the
    query is unsafe, pydantic stops at that field and never reaches the
    threshold validator, so the reasons are taken from `authoring_detail_for_body`,
    which reports both.
    """
    refusal = authoring_detail_for_body(body)
    try:
        payload = ScheduledSearchCreate.model_validate(body)
    except ValidationError as exc:
        if any(not _is_authoring_error(error) for error in exc.errors()):
            raise _request_validation(exc) from exc
        if refusal is not None:
            raise _authoring_http(refusal) from exc
        raise _request_validation(exc) from exc
    if refusal is not None:
        raise _authoring_http(refusal)
    return payload


def _parse_update(
    body: dict[str, Any],
) -> tuple[ScheduledSearchUpdate | None, AuthoringDetail | None]:
    """Validate a patch. An unsafe query returns no payload and its reasons.

    The caller raises those reasons itself: a reader gets them before the write
    check, and a writer gets them merged with the threshold sentence once the
    stored row is known. A schema error is raised here, as FastAPI's list.
    """
    aql_refusal = authoring_detail_for_body(body, check_threshold=False)
    try:
        payload = ScheduledSearchUpdate.model_validate(body)
    except ValidationError as exc:
        if any(not _is_authoring_error(error) for error in exc.errors()):
            raise _request_validation(exc) from exc
        if aql_refusal is None:
            raise _request_validation(exc) from exc
        return None, aql_refusal
    return payload, None


@router.get("", response_model=list[ScheduledSearchOut])
async def list_searches(session: SessionDep) -> list[ScheduledSearchOut]:
    searches = await ScheduledSearchService(session).list()
    return [ScheduledSearchOut.model_validate(s) for s in searches]


@router.post(
    "",
    response_model=ScheduledSearchOut,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=_CREATE_BODY,
)
async def create_search(
    body: dict[str, Any],
    session: SessionDep,
    principal: PrincipalDep,
    settings: SettingsDep,
) -> ScheduledSearchOut:
    """Create one stored search. The stored query is what later runs.

    An unsafe query or an unreachable threshold is 422, with `detail` an object:

        {"message": "<compatibility string>", "reasons": ["<exact reason>", ...]}

    `reasons` is the validator's list, unparaphrased, plus the
    threshold-reachability sentence when it applies. `message` is the string
    this route already returned: `unsafe AQL: ` plus those reasons joined by
    `"; "`, and the threshold sentence. Ordinary schema errors (a missing
    field, a bad type) stay FastAPI's list of `{type, loc, msg}`.

    A cron that fires more often than every 300 seconds is also 422, and
    `detail` is a string naming the two fire times and the minimum. That check
    runs only for a caller who may write.

    Creating an enabled search past `scheduled_searches_max_enabled` is 409.
    The detail names that limit (`the limit is N enabled searches`). Disabled
    searches are not counted. The count is one query.
    """
    payload = _parse_create(body)
    _guard(principal, PERM_SEARCH_WRITE)
    svc = ScheduledSearchService(session, settings=settings)
    try:
        search = await svc.create(payload, actor=principal.subject)
    except CronFloorError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except EnabledSearchLimitError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await record_audit(
        session, action="search.create", actor=principal.subject,
        object_type="scheduled_search", object_id=str(search.id),
        detail={"name": search.name},
    )
    return ScheduledSearchOut.model_validate(search)


@router.get("/{search_id}", response_model=ScheduledSearchOut)
async def get_search(search_id: uuid.UUID, session: SessionDep) -> ScheduledSearchOut:
    search = await ScheduledSearchService(session).get(search_id)
    if search is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "search not found")
    return ScheduledSearchOut.model_validate(search)


@router.patch(
    "/{search_id}",
    response_model=ScheduledSearchOut,
    openapi_extra=_UPDATE_BODY,
)
async def update_search(
    search_id: uuid.UUID,
    body: dict[str, Any],
    session: SessionDep,
    principal: PrincipalDep,
    settings: SettingsDep,
) -> ScheduledSearchOut:
    """Edit one stored search. A changed query mints a new version.

    422 `detail` for an unsafe query or an unreachable threshold has the same
    shape as create:

        {"message": "<compatibility string>", "reasons": ["<exact reason>", ...]}

    `reasons` lists the validator's text, unparaphrased, and the
    threshold-reachability sentence when the *effective* search (this patch
    applied to the stored row) could never breach. Both are in that one list
    when both apply. `message` keeps that text as one string. A missing id
    with an unsafe query is still 422; a missing id with a valid query is 404.
    A caller who may not write is told about an unsafe query before the write
    check, and a threshold problem on a valid query is still the write check's
    403, as it was when the threshold lived in the service. A changed
    `schedule_cron` that fires more often than every 300 seconds is 422 with
    `detail` a string naming the fires and the minimum. An unchanged cron is
    kept verbatim, even if it would fail that floor.

    A patch whose only field is `enabled` is audited as `search.enable` or
    `search.disable`, with the actor and the before/after values. Any other
    patch stays `search.update`. Enabling a disabled search past
    `scheduled_searches_max_enabled` is 409, and the detail names that limit.
    """
    payload, aql_refusal = _parse_update(body)
    # A reader used to hear about an unsafe query from the schema, before this
    # handler's write check. Keep that order. The threshold sentence needs the
    # stored row, so a reader with a valid query still stops at the write check.
    if aql_refusal is not None and not principal.has(PERM_SEARCH_WRITE):
        raise _authoring_http(aql_refusal)
    _guard(principal, PERM_SEARCH_WRITE)
    svc = ScheduledSearchService(session, settings=settings)
    search = await svc.get(search_id)
    if search is None:
        if aql_refusal is not None:
            raise _authoring_http(aql_refusal)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "search not found")
    refusal = authoring_detail_for_body(
        body,
        current_operator=search.threshold_operator,
        current_threshold=search.threshold_value,
        current_max_rows=search.max_result_rows,
        check_aql=True,
    )
    if refusal is not None:
        raise _authoring_http(refusal)
    if payload is None:
        # The query was refused and the merged detail above did not carry it.
        if aql_refusal is None:
            raise RuntimeError("scheduled search update refused without a reason")
        raise _authoring_http(aql_refusal)
    before_version = search.query_version
    before_enabled = search.enabled
    changed = payload.model_dump(exclude_unset=True, exclude={"change_note"})
    try:
        search = await svc.update(search, payload, actor=principal.subject)
    except ThresholdUnreachableError as exc:
        # Backstop for the check above. Nothing of this patch is written.
        sentence = str(exc)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"message": sentence, "reasons": [sentence]},
        ) from exc
    except CronFloorError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except EnabledSearchLimitError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if set(changed) == {"enabled"}:
        action = "search.enable" if search.enabled else "search.disable"
        detail: dict[str, object] = {"before": before_enabled, "after": search.enabled}
    else:
        action = "search.update"
        detail = {"version_before": before_version, "version_after": search.query_version}
    await record_audit(
        session, action=action, actor=principal.subject,
        object_type="scheduled_search", object_id=str(search.id),
        detail=detail,
    )
    return ScheduledSearchOut.model_validate(search)


@router.get("/{search_id}/versions", response_model=list[SearchVersionOut])
async def list_versions(search_id: uuid.UUID, session: SessionDep) -> list[SearchVersionOut]:
    svc = ScheduledSearchService(session)
    if await svc.get(search_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "search not found")
    return [SearchVersionOut.model_validate(v) for v in await svc.versions(search_id)]


@router.get("/{search_id}/executions", response_model=list[ExecutionOut])
async def list_executions(search_id: uuid.UUID, session: SessionDep) -> list[ExecutionOut]:
    svc = ScheduledSearchService(session)
    if await svc.get(search_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "search not found")
    return [ExecutionOut.model_validate(e) for e in await svc.executions(search_id)]


RESULTS_DEFAULT_LIMIT = 500
RESULTS_MAX_LIMIT = 5000


@router.get("/{search_id}/results", response_model=SearchResultTrendOut)
async def list_results(
    search_id: uuid.UUID,
    session: SessionDep,
    metric_key: Annotated[str, Query(min_length=1, max_length=255)] = "total",
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(gt=0, le=RESULTS_MAX_LIMIT)] = RESULTS_DEFAULT_LIMIT,
) -> SearchResultTrendOut:
    """Stored result aggregates for one search, oldest first.

    Read-only, and consistent with the other read routes on this router: no
    additional permission is introduced.

    The window is bounded on both ends and capped at RESULTS_MAX_LIMIT points.
    When more points match than the limit allows, the *most recent* ones win --
    a trend chart that silently showed the oldest 500 of 50,000 points would be
    actively misleading. The rows are re-sorted ascending before returning.
    """
    svc = ScheduledSearchService(session)
    search = await svc.get(search_id)
    if search is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "search not found")

    start = _as_utc(start)
    end = _as_utc(end)
    if start is not None and end is not None and start > end:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "start must not be after end"
        )

    # One joined query, so N points never become N execution lookups. The
    # version join is outer: an execution whose version row was pruned still
    # belongs on the chart, it just has no version id.
    stmt = (
        select(
            SearchResultMetric.bucket_start,
            SearchResultMetric.metric_key,
            SearchResultMetric.value,
            SearchResultMetric.dimensions,
            SearchExecution.id.label("execution_id"),
            SearchExecution.status.label("execution_status"),
            SearchExecution.duration_ms,
            SearchExecution.result_count,
            SearchExecution.threshold_breached,
            SearchExecution.query_version,
            SearchQueryVersion.id.label("query_version_id"),
        )
        .join(SearchExecution, SearchResultMetric.execution_id == SearchExecution.id)
        .outerjoin(
            SearchQueryVersion,
            (SearchQueryVersion.search_id == SearchExecution.search_id)
            & (SearchQueryVersion.version == SearchExecution.query_version),
        )
        .where(
            SearchResultMetric.search_id == search_id,
            SearchResultMetric.metric_key == metric_key,
        )
    )
    if start is not None:
        stmt = stmt.where(SearchResultMetric.bucket_start >= start)
    if end is not None:
        stmt = stmt.where(SearchResultMetric.bucket_start <= end)

    rows = (
        await session.execute(
            stmt.order_by(
                SearchResultMetric.bucket_start.desc(), SearchExecution.id.desc()
            ).limit(limit)
        )
    ).all()

    points = [ResultMetricPoint.model_validate(row, from_attributes=True) for row in reversed(rows)]
    return SearchResultTrendOut(
        search_id=search_id,
        metric_key=metric_key,
        threshold_value=search.threshold_value,
        threshold_operator=search.threshold_operator,
        count=len(points),
        points=points,
    )


@router.post("/{search_id}/run", response_model=ExecutionOut)
async def run_search(
    search_id: uuid.UUID,
    session: SessionDep,
    provider: ProviderDep,
    principal: PrincipalDep,
) -> ExecutionOut:
    """Manually execute a stored search. Requires search:execute.

    Executes the *stored* query only — never AQL from the request body.
    """
    _guard(principal, PERM_SEARCH_EXECUTE)
    svc = ScheduledSearchService(session)
    search = await svc.get(search_id)
    if search is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "search not found")

    settings = get_settings()
    limiter = InMemoryConcurrencyLimiter(
        per_instance=settings.ariel_max_concurrent_searches,
        global_limit=settings.ariel_global_max_concurrent_searches,
    )
    executor = SearchExecutor(session, provider, limiter, settings=settings)
    run_key = svc.manual_run_key(search, principal.subject)
    execution = await executor.execute(
        search, run_key=run_key, instance_key="default",
        trigger="MANUAL", triggered_by=principal.subject,
    )
    # A manual run is a real observation of the condition, so it raises and
    # clears the same alerts a scheduled run would — otherwise a breach found
    # by hand would be invisible to whoever is on call.
    enqueuer = NotificationDispatcher(session, notifiers={}, policy=default_policy())
    await SearchAlertEvaluator(session, settings=settings, enqueuer=enqueuer).evaluate(
        search, execution
    )
    await record_audit(
        session, action="search.execute", actor=principal.subject,
        object_type="scheduled_search", object_id=str(search.id),
        detail={"run_key": run_key, "status": str(execution.status)},
    )
    return ExecutionOut.model_validate(execution)
