"""Rate limiting via slowapi.

A single shared Limiter keyed by client IP. Every API route carries the default
per-client and per-path limit through the ``default_limit`` dependency, so the
health probes Docker and the installer poll stay the only exempt routes. Each
route that spends a scarce Ariel search slot — a manual search run, a property
watch preview — declares the tighter ``rate_limit_search_execution`` limit on
its endpoint, and a decorated route is never also counted against the default.
"""

from __future__ import annotations

from fastapi import Request, Response
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings


def _default_limit() -> str:
    """The default limit, read per request like every other limit string."""
    return get_settings().rate_limit_default


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[_default_limit],
    headers_enabled=True,
)


def default_limit(request: Request, response: Response) -> None:
    """Count one API request against the limiter's default limits.

    ``SlowAPIMiddleware`` cannot do this here: it resolves the handler by
    scanning ``app.routes`` for an ``endpoint`` attribute, and FastAPI includes
    routers lazily — every API route sits inside an ``_IncludedRouter`` wrapper
    with no endpoint, so the middleware found no handler and treated the whole
    API as exempt (FIX-8). A dependency runs after routing, when
    ``scope["endpoint"]`` is the real handler, and therefore sees what the
    middleware would have seen on a flat route table: an exempt or decorated
    endpoint keeps the limit it declared for itself and is not counted twice.
    What remains is exactly the check the middleware would have made, default
    limits keyed per client and per path, plus the ``X-RateLimit-*`` headers
    slowapi would have injected.
    """
    handler = request.scope.get("endpoint")
    if not _own_limit_or_exempt(handler):
        limiter._check_request_limit(request, handler, in_middleware=True)
        # Unset when the limiter is disabled or checked nothing; slowapi's own
        # injection skips a missing window the same way.
        window = getattr(request.state, "view_rate_limit", None)
        if window is not None:
            limiter._inject_headers(response, window)


def _own_limit_or_exempt(handler: object) -> bool:
    """True when the resolved endpoint is exempt or carries its own limit.

    slowapi's ``_should_exempt`` answers this for static limit strings, but it
    never looks at ``_dynamic_route_limits``, where a callable limit lands —
    and every limit here is read per request. Following it blindly would count
    a decorated route against the default as well, halving its own window. A
    missing handler is not exempt here: the whole defect was that "no handler"
    excused everything, so the default applies.
    """
    name = f"{getattr(handler, '__module__', '')}.{getattr(handler, '__name__', '')}"
    if name in limiter._exempt_routes:
        return True
    return name in limiter._route_limits or name in limiter._dynamic_route_limits
