"""Directory configuration: one row, validated as a whole.

Save-time checks on ``ca_pem`` are: it contains no private key, it parses as
one or more X.509 certificates, and each certificate's not-before is in the
past and its not-after is in the future. They do not require the CA basic
constraint and they do not check that the certificates sign each other. The
handshake does the chain and hostname checks, against this PEM when it is set
and against the system trust store when it is empty.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from cryptography.x509 import load_pem_x509_certificates
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import LdapConfig

_MAX_URLS = 8
_MAX_URL_LENGTH = 512
_MAX_TEMPLATE = 512
_MAX_BASE = 1024
_MAX_PEM = 64 * 1024


@dataclass(frozen=True)
class DirectorySpec:
    """A directory connection with no password. ``ca_pem`` is a trust anchor."""

    enabled: bool
    urls: tuple[str, ...]
    start_tls: bool
    ca_pem: str | None
    bind_template: str
    timeout_seconds: int
    user_search_base: str | None


def spec_from_row(row: LdapConfig) -> DirectorySpec:
    return DirectorySpec(
        enabled=row.enabled,
        urls=tuple(row.urls or []),
        start_tls=row.start_tls,
        ca_pem=row.ca_pem,
        bind_template=row.bind_template,
        timeout_seconds=row.timeout_seconds,
        user_search_base=row.user_search_base,
    )


def snapshot(spec: DirectorySpec, version: int) -> dict[str, object]:
    """Audit shape. There is no credential in it. ``ca_pem`` is public.

    The timeout is ``directory_timeout``. The audit redactor masks any key
    containing ``sec``, which would hide ``timeout_seconds``.
    """
    return {
        "enabled": spec.enabled,
        "urls": list(spec.urls),
        "start_tls": spec.start_tls,
        "ca_pem": spec.ca_pem,
        "bind_template": spec.bind_template,
        "directory_timeout": spec.timeout_seconds,
        "user_search_base": spec.user_search_base,
        "version": version,
    }


def validate_directory(
    *,
    enabled: bool,
    urls: list[str],
    start_tls: bool,
    ca_pem: str | None,
    bind_template: str,
    timeout_seconds: int,
    user_search_base: str | None,
) -> tuple[DirectorySpec | None, list[str]]:
    """Return the normalised spec, or None plus every reason it was refused.

    Reasons accumulate. A bad URL does not hide an expired certificate.
    """
    reasons: list[str] = []
    cleaned_urls: list[str] = []
    saw_ldap = False
    saw_ldaps = False

    if len(urls) > _MAX_URLS:
        reasons.append(f"at most {_MAX_URLS} directory URLs are allowed")
    for raw in urls:
        url = raw.strip()
        if not url:
            reasons.append("a directory URL is empty")
            continue
        if len(url) > _MAX_URL_LENGTH:
            reasons.append("a directory URL is longer than 512 characters")
            continue
        kind = _url_reasons(url)
        reasons.extend(kind)
        if not kind:
            cleaned_urls.append(url)
            if url.startswith("ldaps://"):
                saw_ldaps = True
            elif url.startswith("ldap://"):
                saw_ldap = True

    if start_tls and saw_ldaps:
        reasons.append("start_tls must be false for an ldaps:// URL")
    if start_tls and not saw_ldap:
        reasons.append("start_tls requires an ldap:// URL")
    if not start_tls and saw_ldap:
        reasons.append("an ldap:// URL requires start_tls")
    # Enabled with no URL at all. A URL that failed validation is already listed.
    if (
        enabled
        and not urls
        and not cleaned_urls
        and not any(
            reason.startswith("a directory URL") or reason.startswith("at most")
            for reason in reasons
        )
    ):
        reasons.append("an enabled configuration needs at least one directory URL")
    if enabled and urls and not cleaned_urls and not any(
        "needs at least one" in reason for reason in reasons
    ):
        reasons.append("an enabled configuration needs at least one valid directory URL")

    template = bind_template.strip()
    if len(template) > _MAX_TEMPLATE:
        reasons.append("bind_template is longer than 512 characters")
    if "{username}" not in template:
        reasons.append("bind_template must contain {username}")

    if not 1 <= timeout_seconds <= 30:
        reasons.append("timeout_seconds must be between 1 and 30")

    pem, pem_reasons = _certificate(ca_pem)
    reasons.extend(pem_reasons)

    base, base_reasons = _search_base(user_search_base)
    reasons.extend(base_reasons)

    if reasons:
        return None, reasons
    return (
        DirectorySpec(
            enabled=enabled,
            urls=tuple(cleaned_urls),
            start_tls=start_tls,
            ca_pem=pem,
            bind_template=template,
            timeout_seconds=timeout_seconds,
            user_search_base=base,
        ),
        [],
    )


def _url_reasons(url: str) -> list[str]:
    parts = urlsplit(url)
    reasons: list[str] = []
    if parts.scheme not in ("ldap", "ldaps"):
        reasons.append(f"{url} must use ldap:// or ldaps://")
        return reasons
    if parts.username or parts.password:
        reasons.append(f"{url} must not contain credentials")
    if not parts.hostname:
        reasons.append(f"{url} must name a host")
    if parts.path not in ("", "/"):
        reasons.append(f"{url} must not have a path")
    if parts.query:
        reasons.append(f"{url} must not have a query")
    if parts.fragment:
        reasons.append(f"{url} must not have a fragment")
    return reasons


def _certificate(ca_pem: str | None) -> tuple[str | None, list[str]]:
    if ca_pem is None or not ca_pem.strip():
        return None, []
    text = ca_pem.strip()
    if len(text) > _MAX_PEM:
        return None, ["ca_pem is too long"]
    if "PRIVATE KEY" in text:
        return None, ["ca_pem must be a certificate, not a private key"]
    try:
        certs = load_pem_x509_certificates(text.encode("utf-8"))
    except (ValueError, TypeError):
        return None, ["ca_pem is not a PEM X.509 certificate"]
    if not certs:
        return None, ["ca_pem is not a PEM X.509 certificate"]
    now = datetime.now(UTC)
    reasons: list[str] = []
    for cert in certs:
        subject = cert.subject.rfc4514_string()
        if cert.not_valid_before_utc > now:
            reasons.append(
                f"certificate {subject} is not yet valid "
                f"(not_before {cert.not_valid_before_utc.isoformat()})"
            )
        if cert.not_valid_after_utc <= now:
            reasons.append(
                f"certificate {subject} is expired "
                f"(not_after {cert.not_valid_after_utc.isoformat()})"
            )
    if reasons:
        return None, reasons
    return text + "\n" if not text.endswith("\n") else text, []


def _search_base(value: str | None) -> tuple[str | None, list[str]]:
    if value is None or not value.strip():
        return None, []
    base = value.strip()
    if len(base) > _MAX_BASE:
        return None, ["user_search_base is longer than 1024 characters"]
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in base):
        return None, ["user_search_base contains a control character"]
    return base, []


async def get_ldap_row(session: AsyncSession) -> LdapConfig | None:
    return await session.get(LdapConfig, 1)
