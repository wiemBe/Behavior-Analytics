"""Sign-in, sign-out, password change, and directory configuration.

Login is unauthenticated. Logout and password change need a principal.
Directory configuration needs ``admin:auth``. The test probe is not audited
and its password is not stored.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import SessionDep, SettingsDep, get_principal, requires
from app.api.request_meta import client_ip, user_agent
from app.core.config import Settings, get_settings
from app.core.rate_limit import limiter
from app.i18n.messages import MessageRef
from app.i18n.refusal import CodedHTTPException
from app.models.identity import LdapConfig, User
from app.repositories.audit import record_audit
from app.schemas.auth import (
    BindResult,
    CertificateResult,
    LdapConfigIn,
    LdapConfigOut,
    LdapTestIn,
    LdapTestOut,
    LoginIn,
    LoginOut,
    PasswordChangeIn,
    TlsResult,
)
from app.security.rbac import PERM_AUTH_ADMIN, Principal
from app.services.auth_login import (
    LoginFailure,
    authenticate,
    change_own_password,
)
from app.services.auth_sessions import delete_by_token
from app.services.ldap_bind import bind_directory
from app.services.ldap_config import (
    get_ldap_row,
    snapshot,
    spec_from_row,
    validate_directory_coded,
)

public_router = APIRouter(prefix="/auth", tags=["auth"])
session_router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    dependencies=[Depends(get_principal)],
)
admin_router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    dependencies=[Depends(requires(PERM_AUTH_ADMIN))],
)


def _login_limit() -> str:
    return get_settings().rate_limit_login


def _set_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        "qd_session",
        token,
        httponly=True,
        samesite="strict",
        secure=settings.is_hardened,
        path="/",
        max_age=settings.session_max_hours * 3600,
    )


def _clear_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        "qd_session",
        path="/",
        httponly=True,
        samesite="strict",
        secure=settings.is_hardened,
    )


@public_router.post("/login", response_model=LoginOut)
@limiter.limit(_login_limit)
async def login(
    request: Request,
    body: LoginIn,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> LoginOut:
    result = await authenticate(
        session,
        settings,
        body.username,
        body.password,
        source_ip=client_ip(request),
        user_agent=user_agent(request),
    )
    if isinstance(result, LoginFailure):
        # Commit before raising. The session dependency rolls a raised
        # HTTPException back, which would forget the lockout.
        await session.commit()
        raise CodedHTTPException(result.status_code, result.ref)
    _set_cookie(response, result.token, settings)
    return LoginOut(
        username=result.username,
        auth_source=result.auth_source,
        must_change_password=result.must_change_password,
        roles=list(result.roles),
    )


@session_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    principal: Principal = Depends(get_principal),
) -> None:
    token = request.cookies.get("qd_session")
    await delete_by_token(session, token)
    await record_audit(
        session,
        action="auth.logout",
        actor=principal.username or principal.subject,
        actor_id=principal.user_id,
        object_type="user" if principal.user_id is not None else None,
        object_id=str(principal.user_id) if principal.user_id is not None else None,
        outcome="SUCCESS",
        detail={"username": principal.username or principal.subject},
        source_ip=client_ip(request),
        user_agent=user_agent(request),
    )
    _clear_cookie(response, settings)


@session_router.post("/password", response_model=LoginOut)
async def change_password(
    request: Request,
    body: PasswordChangeIn,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    principal: Principal = Depends(get_principal),
) -> LoginOut:
    if principal.user_id is None or principal.auth_source != "LOCAL":
        ref = MessageRef(
            "auth.password.ldap_directory"
            if principal.auth_source == "LDAP"
            else "auth.password.local_only"
        )
        raise CodedHTTPException(status.HTTP_403_FORBIDDEN, ref)
    user = await session.scalar(
        select(User)
        .where(User.id == principal.user_id)
        .options(selectinload(User.roles))
    )
    if user is None or user.disabled_at is not None or not user.is_active:
        raise CodedHTTPException(
            status.HTTP_401_UNAUTHORIZED, MessageRef("auth.session.required")
        )
    result = await change_own_password(
        session,
        settings,
        user,
        body.current_password,
        body.new_password,
        source_ip=client_ip(request),
        user_agent=user_agent(request),
    )
    _set_cookie(response, result.token, settings)
    return LoginOut(
        username=result.username,
        auth_source=result.auth_source,
        must_change_password=result.must_change_password,
        roles=list(result.roles),
    )


def _config_out(row: LdapConfig | None) -> LdapConfigOut:
    if row is None:
        return LdapConfigOut(
            enabled=False,
            urls=[],
            start_tls=False,
            ca_pem=None,
            bind_template="{username}",
            timeout_seconds=5,
            user_search_base=None,
            version=0,
            updated_by=None,
            updated_at=None,
        )
    return LdapConfigOut(
        enabled=row.enabled,
        urls=list(row.urls or []),
        start_tls=row.start_tls,
        ca_pem=row.ca_pem,
        bind_template=row.bind_template,
        timeout_seconds=row.timeout_seconds,
        user_search_base=row.user_search_base,
        version=row.version,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


@admin_router.get("/ldap-config", response_model=LdapConfigOut)
async def read_ldap_config(session: SessionDep) -> LdapConfigOut:
    return _config_out(await get_ldap_row(session))


@admin_router.put("/ldap-config", response_model=LdapConfigOut)
async def write_ldap_config(
    request: Request,
    body: LdapConfigIn,
    session: SessionDep,
    principal: Principal = Depends(get_principal),
) -> LdapConfigOut:
    spec, reasons = validate_directory_coded(
        enabled=body.enabled,
        urls=body.urls,
        start_tls=body.start_tls,
        ca_pem=body.ca_pem,
        bind_template=body.bind_template,
        timeout_seconds=body.timeout_seconds,
        user_search_base=body.user_search_base,
    )
    if spec is None:
        raise CodedHTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, refs=reasons)
    row = await get_ldap_row(session)
    if row is None:
        if body.version != 0:
            raise CodedHTTPException(
                status.HTTP_409_CONFLICT, MessageRef("auth.directory.stale")
            )
        before = None
        row = LdapConfig(
            id=1,
            enabled=spec.enabled,
            urls=list(spec.urls),
            start_tls=spec.start_tls,
            ca_pem=spec.ca_pem,
            bind_template=spec.bind_template,
            timeout_seconds=spec.timeout_seconds,
            user_search_base=spec.user_search_base,
            version=1,
            updated_by=(principal.username or principal.subject)[:255],
            updated_at=datetime.now(UTC),
        )
        session.add(row)
    else:
        if row.version != body.version:
            raise CodedHTTPException(
                status.HTTP_409_CONFLICT, MessageRef("auth.directory.stale")
            )
        before = snapshot(spec_from_row(row), row.version)
        row.enabled = spec.enabled
        row.urls = list(spec.urls)
        row.start_tls = spec.start_tls
        row.ca_pem = spec.ca_pem
        row.bind_template = spec.bind_template
        row.timeout_seconds = spec.timeout_seconds
        row.user_search_base = spec.user_search_base
        row.version += 1
        row.updated_by = (principal.username or principal.subject)[:255]
        row.updated_at = datetime.now(UTC)
    await session.flush()
    await record_audit(
        session,
        action="auth.ldap_config.update",
        actor=principal.username or principal.subject,
        actor_id=principal.user_id,
        object_type="ldap_config",
        object_id="1",
        outcome="SUCCESS",
        detail={"before": before, "after": snapshot(spec, row.version)},
        source_ip=client_ip(request),
        user_agent=user_agent(request),
    )
    return _config_out(row)


@admin_router.post("/ldap-config/test", response_model=LdapTestOut)
async def test_ldap_config(body: LdapTestIn) -> LdapTestOut:
    """Probe a draft. Nothing is stored and the call is not audited."""
    spec, reasons = validate_directory_coded(
        enabled=body.enabled,
        urls=body.urls,
        start_tls=body.start_tls,
        ca_pem=body.ca_pem,
        bind_template=body.bind_template,
        timeout_seconds=body.timeout_seconds,
        user_search_base=body.user_search_base,
    )
    if spec is None:
        raise CodedHTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, refs=reasons)
    if not spec.urls:
        raise CodedHTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            refs=[MessageRef("auth.directory.test_needs_url")],
        )
    report = await bind_directory(spec, body.username, body.password)
    certificate = None
    if report.certificate is not None:
        certificate = CertificateResult(
            subject=report.certificate.subject,
            issuer=report.certificate.issuer,
            not_after=report.certificate.not_after,
        )
    return LdapTestOut(
        tls=TlsResult(
            status=report.tls_status, reason=report.tls_reason, trust=report.trust
        ),
        certificate=certificate,
        bind=BindResult(status=report.bind_status, reason=report.bind_reason),
        latency_ms=report.latency_ms,
    )
