"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.rate_limit import limiter
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
        version="0.1.0",
        # No interactive docs in production — do not advertise the API surface.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)

    # The SPA is same-origin behind the reverse proxy in production; in dev it
    # runs on :3000 and calls the API on :8000 with credentials. No wildcard.
    origins = [settings.public_origin]
    if not settings.is_production and "http://localhost:3000" not in origins:
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
            return _security_headers(JSONResponse(status_code=403, content={"detail": refusal}))
        response = await call_next(request)
        return _security_headers(response)

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=429, content={"detail": "rate limit exceeded", "limit": str(exc.limit)}
        )

    app.include_router(_build_router(), prefix=settings.api_prefix)
    return app


def _build_router():  # type: ignore[no-untyped-def]
    from app.api.router import api_router

    return api_router


app = create_app()
