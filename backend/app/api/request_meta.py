"""Pure helpers for the caller's address and user agent on audited routes."""

from starlette.requests import Request

# The audit_log column widths (String(64) for source_ip, String(512) for user_agent).
CLIENT_IP_MAX = 64
USER_AGENT_MAX = 512


def client_ip(request: Request) -> str | None:
    """The caller's address as the audit log records it; None when unknown or empty."""
    # The proxy's client address. X-Forwarded-For is not trusted: a caller
    # can put anything in it, and the audit record would then carry a lie.
    if request.client is None or not request.client.host:
        return None
    return request.client.host[:CLIENT_IP_MAX]


def user_agent(request: Request) -> str | None:
    """The caller's user agent as the audit log records it; None when absent or empty."""
    raw = request.headers.get("user-agent")
    return raw[:USER_AGENT_MAX] if raw else None
