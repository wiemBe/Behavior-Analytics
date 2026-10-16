"""Origin check for cookie-authenticated unsafe requests.

SameSite=Strict is the first control. This is the second: a request that
changes state must name the configured public origin in Origin, or in Referer
when Origin is absent. A missing pair is refused. The check runs only when
session cookies are enforced, so the development local principal is unchanged.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request

from app.core.config import Settings

_UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_DEV_ORIGIN = "http://localhost:3000"
_MISSING = "Cross-site request refused: the request has no Origin or Referer header."


def _normalise(value: str) -> str:
    parts = urlsplit(value.strip())
    return f"{parts.scheme}://{parts.netloc}".lower()


def _reflect(origin: str) -> str:
    return origin.replace("\r", "").replace("\n", "")[:200]


def refusal_reason(
    method: str,
    origin: str | None,
    referer: str | None,
    *,
    public_origin: str,
    enforced: bool,
    allow_dev_localhost: bool,
) -> str | None:
    """Return the refusal text, or None when the request may proceed."""
    if not enforced or method.upper() not in _UNSAFE:
        return None
    claimed = origin
    if not claimed and referer:
        parts = urlsplit(referer)
        if parts.scheme and parts.netloc:
            claimed = f"{parts.scheme}://{parts.netloc}"
    if not claimed:
        return _MISSING
    allowed = {_normalise(public_origin)}
    if allow_dev_localhost:
        allowed.add(_DEV_ORIGIN)
    if _normalise(claimed) not in allowed:
        return (
            "Cross-site request refused: origin "
            f"{_reflect(claimed)} is not the configured public origin."
        )
    return None


def request_refusal(request: Request, settings: Settings) -> str | None:
    """Apply :func:`refusal_reason` to a request. Origin wins over Referer."""
    return refusal_reason(
        request.method,
        request.headers.get("origin"),
        request.headers.get("referer"),
        public_origin=settings.public_origin,
        enforced=settings.session_cookies_enforced(),
        allow_dev_localhost=not settings.is_production,
    )
