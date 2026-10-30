"""FastAPI application factory."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.api.routes.property_watches import CapacityConflict
from app.core.config import Settings, get_settings, unknown_setting_names
from app.core.logging import configure_logging
from app.core.rate_limit import default_limit, limiter
from app.core.version import read_version
from app.i18n.refusal import CodedHTTPException, coded_body, coded_http_exception_handler
from app.schemas.property_watch import CapacityConflictOut
from app.security.csrf import request_refusal

logger = logging.getLogger("app")


def _settings_for(request: Request) -> Settings:
    """The request's settings override, or the process settings.

    ``get_settings`` is cached and does not see a dependency override. Tests
    install the override on the app, and this middleware has to read it or
    the CSRF check would use the wrong origin.
    """
    override = request.app.dependency_overrides.get(get_settings)
    if override is not None:
        value = override()
        if isinstance(value, Settings):
            return value
    return get_settings()


def _security_headers(response: Response) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers.setdefault("Cache-Control", "no-store")
    return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    # One line, names only. A typo such as CATCHALL_COLLECTION_ENABLE is kept
    # across re-install and otherwise never applied.
    unknown = unknown_setting_names(os.environ)
    if unknown:
        logger.warning("ignoring unknown settings: %s", ", ".join(unknown))
    logger.info(
        "starting qradar-observability",
        extra={"environment": str(settings.environment), "provider": str(settings.qradar_provider)},
    )
    yield
    logger.info("shutting down qradar-observability")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="qradar-observability",
        version=read_version(),
        # No interactive docs outside development — do not advertise the API surface.
        docs_url=None if settings.is_hardened else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_hardened else "/openapi.json",
        lifespan=lifespan,
    )

    # The limiter the decorated routes and the 429 handler below share. The
    # default limit itself is applied per route by the `default_limit`
    # dependency at the include below: SlowAPIMiddleware cannot see through
    # FastAPI's lazily included routers (FIX-8).
    app.state.limiter = limiter

    # The SPA is same-origin behind the reverse proxy outside development; in
    # dev it runs on :3000 and calls the API on :8000 with credentials. No wildcard.
    origins = [settings.public_origin]
    if not settings.is_hardened and "http://localhost:3000" not in origins:
        origins.append("http://localhost:3000")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        # Outermost middleware. The CSRF refusal carries the same headers as a
        # normal response. It runs only when session cookies are enforced, so
        # the development local principal and its tests are unchanged.
        refusal = request_refusal(request, _settings_for(request))
        if refusal is not None:
            return _security_headers(JSONResponse(status_code=403, content=coded_body(refusal)))
        response = await call_next(request)
        return _security_headers(response)

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        response = JSONResponse(
            status_code=429, content={"detail": "rate limit exceeded", "limit": str(exc.limit)}
        )
        # The refused request learns the same window it would have read on a
        # successful one; slowapi's own handler does the same.
        window = getattr(request.state, "view_rate_limit", None)
        if window is not None:
            limiter._inject_headers(response, window)
        return response

    app.add_exception_handler(CodedHTTPException, coded_http_exception_handler)  # type: ignore[arg-type]

    @app.exception_handler(CapacityConflict)
    async def _capacity_conflict_handler(request: Request, exc: CapacityConflict) -> JSONResponse:
        """The 409 of a capacity refusal: the reason text and one stable code per reason.

        The refusal is an exception, so the session dependency has already rolled the
        route's half-applied write back: a refusal changes nothing (T-145).
        """
        body = CapacityConflictOut(detail=str(exc.detail), codes=exc.codes)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content=jsonable_encoder(body)
        )

    app.include_router(
        _build_router(),
        prefix=settings.api_prefix,
        # Every route under the API prefix carries the default limit; the
        # probes are exempt and the decorated routes keep their own.
        dependencies=[Depends(default_limit)],
    )
    return app


def _build_router():  # type: ignore[no-untyped-def]
    from app.api.router import api_router

    return api_router


app = create_app()
