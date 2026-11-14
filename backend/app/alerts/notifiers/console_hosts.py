"""Refuse a webhook that points at a QRadar console (SEC-RO2).

A destination may carry custom headers and a signed body. Aimed at a console, that is a way to
make the product send a request the operator typed to QRadar, so a URL whose host is a registered
console (or the MCP sidecar) is refused. The comparison is on normalised host strings only: a
host name is lower-cased and loses its trailing dot, an IP literal is written in its canonical
form. No DNS lookup is made, so a name that merely resolves to a console address is not caught
here; the private-address guard in `url_guard` covers that class when private targets are off.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.notifiers.url_guard import URLGuardError
from app.core.config import get_settings
from app.models.instance import QRadarInstance

CONSOLE_REFUSED = "notifications.console_refused"


def normalise_host(value: str | None) -> str | None:
    """The comparable host of a URL or a bare ``host[:port]``, or None when there is none."""
    if not value or not value.strip():
        return None
    text = value.strip()
    try:
        parts = urlsplit(text if "//" in text else f"//{text}")
        host = parts.hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.rstrip(".").lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    mapped = getattr(address, "ipv4_mapped", None)
    return str(mapped if mapped is not None else address)


def host_set(values: Iterable[str | None]) -> frozenset[str]:
    return frozenset(h for v in values if (h := normalise_host(v)) is not None)


async def registered_console_hosts(session: AsyncSession) -> frozenset[str]:
    """Hosts of every registered console, its MCP sidecar, and the environment's own."""
    rows = (
        await session.execute(select(QRadarInstance.console_host, QRadarInstance.mcp_base_url))
    ).all()
    settings = get_settings()
    values: list[str | None] = [settings.qradar_host, settings.mcp_base_url]
    for console_host, mcp_base_url in rows:
        values.extend((console_host, mcp_base_url))
    return host_set(values)


def targets_console(url: str, console_hosts: frozenset[str]) -> bool:
    host = normalise_host(url)
    return host is not None and host in console_hosts


def refuse_console_target(url: str, console_hosts: frozenset[str]) -> None:
    """Raise the coded refusal when ``url`` names a console. The message carries no URL."""
    if targets_console(url, console_hosts):
        raise URLGuardError(CONSOLE_REFUSED)
