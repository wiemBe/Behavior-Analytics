"""Administration and explicit test sends for named webhook destinations."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, cast

import httpx
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.alerts.notifiers.url_guard import (
    GuardedURL,
    URLGuardError,
    display_url,
    guard_url,
    resolve_host,
)
from app.alerts.notifiers.webhook_v1 import (
    SIGNATURE_HEADER,
    build_webhook_v1_test,
    sign_v1,
)
from app.api.deps import SessionDep
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.i18n.messages import MessageRef, render
from app.i18n.refusal import CodedHTTPException
from app.models.notification_destination import NotificationDestination
from app.models.notification_rule import NotificationRule
from app.repositories.audit import record_audit
from app.schemas.notification_destination import (
    DestinationCreate,
    DestinationOut,
    DestinationPatch,
    DestinationTestOut,
    MessageOut,
    TestClass,
)
from app.security.auth import get_principal
from app.security.rbac import Principal

router = APIRouter(prefix="/admin/notifications/destinations", tags=["notification-destinations"])
PrincipalDep = Annotated[Principal, Depends(get_principal)]

MAX_DESTINATIONS = 10
MAX_RESPONSE_BYTES = 64 * 1024
READ_CHUNK_BYTES = 8 * 1024

# Named seams, not settings: tests replace both and therefore never resolve or
# contact a real endpoint. Production uses the system resolver and direct HTTPX.
_resolver = resolve_host


def _http_transport() -> httpx.AsyncBaseTransport:
    # A transport has no redirect or environment-proxy layer. Calling it directly
    # also avoids HTTPX's INFO request log, which includes the sensitive path/query.
    return httpx.AsyncHTTPTransport(retries=0)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _test_limit() -> str:
    return get_settings().rate_limit_webhook_test


def _coded(
    status_code: int, code: str, params: dict[str, object] | None = None
) -> CodedHTTPException:
    return CodedHTTPException(status_code, MessageRef(code, params or {}))


def _guard_refusal(exc: URLGuardError) -> CodedHTTPException:
    return _coded(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.code)


async def _guard(
    url: str, *, allow_http: bool, allow_private: bool
) -> GuardedURL:
    return await asyncio.to_thread(
        guard_url,
        url,
        allow_http=allow_http,
        allow_private=allow_private,
        resolver=_resolver,
    )


def _encode_headers(headers: dict[str, str]) -> str:
    return json.dumps(headers, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _decode_headers(row: NotificationDestination) -> dict[str, str]:
    value = json.loads(row.headers)
    if not isinstance(value, dict) or not all(
        isinstance(name, str) and isinstance(header, str) for name, header in value.items()
    ):
        raise RuntimeError("stored notification destination headers are malformed")
    return cast(dict[str, str], value)


def _out(row: NotificationDestination) -> DestinationOut:
    return DestinationOut(
        id=row.id,
        name=row.name,
        enabled=row.enabled,
        url_display=display_url(row.url),
        allow_http=row.allow_http,
        allow_private=row.allow_private,
        secret_set=row.hmac_secret is not None,
        header_names=sorted(_decode_headers(row)),
        last_test_at=row.last_test_at,
        last_test_status=row.last_test_status,
        last_test_class=row.last_test_class,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


async def _get(
    session: SessionDep, destination_id: uuid.UUID, *, for_update: bool = False
) -> NotificationDestination:
    statement = select(NotificationDestination).where(NotificationDestination.id == destination_id)
    if for_update:
        statement = statement.with_for_update()
    row = await session.scalar(statement)
    if row is None:
        raise _coded(status.HTTP_404_NOT_FOUND, "notifications.destination_unknown")
    return row


async def _name_taken(
    session: SessionDep, name: str, *, except_id: uuid.UUID | None = None
) -> bool:
    statement = select(NotificationDestination.id).where(NotificationDestination.name == name)
    if except_id is not None:
        statement = statement.where(NotificationDestination.id != except_id)
    return await session.scalar(statement) is not None


def _duplicate(name: str) -> CodedHTTPException:
    return _coded(status.HTTP_409_CONFLICT, "notifications.name_taken", {"name": name})


def _audit_detail(row: NotificationDestination) -> dict[str, str]:
    # redact() treats a bare UUID as token-shaped. The prefix preserves the ID in
    # the audit record without weakening redaction for credentials elsewhere.
    return {"id": f"urn:uuid:{row.id}", "name": row.name}


@router.get("", response_model=list[DestinationOut])
async def list_destinations(session: SessionDep) -> list[DestinationOut]:
    rows = await session.scalars(
        select(NotificationDestination).order_by(
            NotificationDestination.name, NotificationDestination.id
        )
    )
    return [_out(row) for row in rows]


@router.post("", response_model=DestinationOut, status_code=status.HTTP_201_CREATED)
async def create_destination(
    payload: DestinationCreate, session: SessionDep, principal: PrincipalDep
) -> DestinationOut:
    try:
        checked = await _guard(
            payload.url,
            allow_http=payload.allow_http,
            allow_private=payload.allow_private,
        )
    except URLGuardError as exc:
        raise _guard_refusal(exc) from exc
    # The cap is a service invariant, not a DB CHECK. Serialising creates closes
    # the count/insert race while leaving reads concurrent.
    await session.execute(
        text("LOCK TABLE notification_destination IN SHARE ROW EXCLUSIVE MODE")
    )
    count = await session.scalar(select(func.count()).select_from(NotificationDestination)) or 0
    if count >= MAX_DESTINATIONS:
        raise _coded(status.HTTP_422_UNPROCESSABLE_CONTENT, "notifications.limit")
    if await _name_taken(session, payload.name):
        raise _duplicate(payload.name)
    row = NotificationDestination(
        name=payload.name,
        enabled=payload.enabled,
        url=checked.original,
        allow_http=payload.allow_http,
        allow_private=payload.allow_private,
        hmac_secret=payload.hmac_secret,
        headers=_encode_headers(payload.headers),
        created_by=principal.subject,
        updated_by=principal.subject,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _duplicate(payload.name) from exc
    await session.refresh(row)
    await record_audit(
        session,
        action="notification_destination.create",
        actor=principal.subject,
        object_type="notification_destination",
        object_id=str(row.id),
        detail=_audit_detail(row),
    )
    return _out(row)


@router.get("/{destination_id}", response_model=DestinationOut)
async def get_destination(destination_id: uuid.UUID, session: SessionDep) -> DestinationOut:
    return _out(await _get(session, destination_id))


@router.patch("/{destination_id}", response_model=DestinationOut)
async def patch_destination(
    destination_id: uuid.UUID,
    payload: DestinationPatch,
    session: SessionDep,
    principal: PrincipalDep,
) -> DestinationOut:
    row = await _get(session, destination_id, for_update=True)
    name = payload.name if "name" in payload.model_fields_set else row.name
    url = payload.url if "url" in payload.model_fields_set else row.url
    allow_http = payload.allow_http if "allow_http" in payload.model_fields_set else row.allow_http
    allow_private = (
        payload.allow_private
        if "allow_private" in payload.model_fields_set
        else row.allow_private
    )
    assert name is not None and url is not None
    assert allow_http is not None and allow_private is not None
    if await _name_taken(session, name, except_id=row.id):
        raise _duplicate(name)
    try:
        checked = await _guard(url, allow_http=allow_http, allow_private=allow_private)
    except URLGuardError as exc:
        raise _guard_refusal(exc) from exc
    row.name = name
    row.url = checked.original
    row.allow_http = allow_http
    row.allow_private = allow_private
    if "enabled" in payload.model_fields_set:
        assert payload.enabled is not None
        row.enabled = payload.enabled
    if "hmac_secret" in payload.model_fields_set:
        row.hmac_secret = payload.hmac_secret
    if "headers" in payload.model_fields_set:
        assert payload.headers is not None
        row.headers = _encode_headers(payload.headers)
    row.updated_by = principal.subject
    row.updated_at = _utcnow()
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _duplicate(name) from exc
    await session.refresh(row)
    await record_audit(
        session,
        action="notification_destination.patch",
        actor=principal.subject,
        object_type="notification_destination",
        object_id=str(row.id),
        detail=_audit_detail(row),
    )
    return _out(row)


@router.delete("/{destination_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_destination(
    destination_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Response:
    row = await _get(session, destination_id, for_update=True)
    in_use = await session.scalar(
        select(NotificationRule.id)
        .where(NotificationRule.destination_ids.contains([destination_id]))
        .limit(1)
    )
    if in_use is not None:
        raise _coded(
            status.HTTP_409_CONFLICT, "notifications.destination_in_use", {"name": row.name}
        )
    detail = _audit_detail(row)
    await session.delete(row)
    await session.flush()
    await record_audit(
        session,
        action="notification_destination.delete",
        actor=principal.subject,
        object_type="notification_destination",
        object_id=str(destination_id),
        detail=detail,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _discard_body(response: httpx.Response) -> None:
    if response.is_stream_consumed:
        return
    read = 0
    async for chunk in response.aiter_raw(chunk_size=READ_CHUNK_BYTES):
        read += len(chunk)
        if read >= MAX_RESPONSE_BYTES:
            break


class _BodyStream(httpx.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        yield self.body


async def _send(row: NotificationDestination, now: datetime) -> tuple[TestClass, int | None]:
    try:
        checked = await _guard(
            row.url, allow_http=row.allow_http, allow_private=row.allow_private
        )
    except URLGuardError as exc:
        result: TestClass = (
            "ssrf_refused"
            if exc.code in {"notifications.private_refused", "notifications.http_refused"}
            else "transport"
        )
        return result, None
    body, headers = build_webhook_v1_test(str(row.id), now)
    headers["Host"] = checked.host_header
    if row.hmac_secret is not None:
        headers[SIGNATURE_HEADER] = sign_v1(body, row.hmac_secret)
    headers.update(_decode_headers(row))
    request = httpx.Request(
        "POST",
        checked.pinned_url(checked.addresses[0]),
        stream=_BodyStream(body),
        headers=headers,
        extensions={
            "sni_hostname": checked.hostname,
            "timeout": httpx.Timeout(10.0).as_dict(),
        },
    )
    try:
        async with _http_transport() as transport:
            response = await transport.handle_async_request(request)
            response.request = request
            response_status = response.status_code
            try:
                await _discard_body(response)
            finally:
                await response.aclose()
    except httpx.TimeoutException:
        return "timeout", None
    except httpx.TransportError:
        return "transport", None
    if 200 <= response_status < 300:
        return "ok", response_status
    if 300 <= response_status < 400:
        return "redirect_refused", response_status
    return "http_status", response_status


def _test_outcome(result: TestClass, response_status: int | None) -> DestinationTestOut:
    if result == "ok":
        ref = MessageRef("notifications.test_ok")
    else:
        ref = MessageRef("notifications.test_failed", {"failure_class": result})
    return DestinationTestOut(
        ok=result == "ok",
        status=response_status,
        result=result,
        outcome=MessageOut(code=ref.code, params=dict(ref.params), message=render(ref)),
    )


@router.post("/{destination_id}/test", response_model=DestinationTestOut)
@limiter.limit(_test_limit)
async def send_test(
    request: Request,
    response: Response,
    destination_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
) -> DestinationTestOut:
    """Send only the operator-requested test event, with a bounded response read."""
    row = await _get(session, destination_id, for_update=True)
    now = _utcnow()
    result, response_status = await _send(row, now)
    row.last_test_at = now
    row.last_test_status = response_status
    row.last_test_class = result
    row.updated_at = now
    row.updated_by = principal.subject
    await session.flush()
    await record_audit(
        session,
        action="notification_destination.test",
        actor=principal.subject,
        object_type="notification_destination",
        object_id=str(row.id),
        outcome="SUCCESS" if result == "ok" else "FAILURE",
        detail=_audit_detail(row),
    )
    return _test_outcome(result, response_status)
