"""Validation and DNS pinning for named webhook destinations.

The guard is pure apart from its injected resolver.  Saving and sending use the
same function: every resolved address must be acceptable, and the send path
connects to one address from the returned, already-checked tuple.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

Resolver = Callable[[str, int], Sequence[str]]


class URLGuardError(ValueError):
    """A coded destination refusal safe to return through the admin API."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GuardedURL:
    """A parsed URL plus the complete address set checked for this operation."""

    original: str
    parts: SplitResult
    hostname: str
    port: int
    addresses: tuple[str, ...]

    @property
    def display(self) -> str:
        return _origin(self.parts, self.hostname)

    @property
    def host_header(self) -> str:
        return _authority(self.hostname, self.parts.port)

    def pinned_url(self, address: str) -> str:
        """Rewrite only the authority host, preserving the stored path and query."""
        if address not in self.addresses:
            raise ValueError("the pinned address was not checked")
        authority = _authority(address, self.parts.port)
        return urlunsplit(
            (self.parts.scheme, authority, self.parts.path, self.parts.query, "")
        )


def resolve_host(host: str, port: int) -> tuple[str, ...]:
    """Resolve a stream endpoint into a stable, de-duplicated address tuple."""
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(str(answer[4][0]) for answer in answers))


def _parse(url: str, *, allow_http: bool) -> tuple[SplitResult, str, int]:
    try:
        parts = urlsplit(url)
        port = parts.port
        hostname = parts.hostname
    except ValueError as exc:
        raise URLGuardError("notifications.url_invalid") from exc
    if parts.scheme not in {"https", "http"} or hostname is None or not parts.netloc:
        raise URLGuardError("notifications.url_invalid")
    if parts.username is not None or parts.password is not None or parts.fragment:
        raise URLGuardError("notifications.url_invalid")
    if parts.scheme == "http" and not allow_http:
        raise URLGuardError("notifications.http_refused")
    return parts, hostname, port or (443 if parts.scheme == "https" else 80)


def _authority(host: str, explicit_port: int | None) -> str:
    rendered = f"[{host}]" if ":" in host else host
    return rendered if explicit_port is None else f"{rendered}:{explicit_port}"


def _origin(parts: SplitResult, host: str) -> str:
    return f"{parts.scheme}://{_authority(host, parts.port)}"


def display_url(url: str) -> str:
    """Return only ``scheme://host[:port]`` from a previously validated URL."""
    parts, hostname, _ = _parse(url, allow_http=True)
    return _origin(parts, hostname)


def _ip_is_refused(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None and _ip_is_refused(mapped):
        return True
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_unspecified,
            address.is_reserved,
        )
    )


def guard_url(
    url: str,
    *,
    allow_http: bool = False,
    allow_private: bool = False,
    resolver: Resolver = resolve_host,
) -> GuardedURL:
    """Validate a destination and return only addresses checked in this call."""
    original = url.strip()
    parts, hostname, port = _parse(original, allow_http=allow_http)
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            resolved = tuple(dict.fromkeys(resolver(hostname, port)))
        except (OSError, UnicodeError, ValueError) as exc:
            raise URLGuardError("notifications.url_invalid") from exc
    else:
        resolved = (str(literal),)
    if not resolved:
        raise URLGuardError("notifications.url_invalid")
    addresses: list[str] = []
    for raw in resolved:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise URLGuardError("notifications.url_invalid") from exc
        if not allow_private and _ip_is_refused(address):
            raise URLGuardError("notifications.private_refused")
        addresses.append(str(address))
    return GuardedURL(original, parts, hostname, port, tuple(addresses))
