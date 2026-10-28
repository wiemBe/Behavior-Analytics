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

from app.i18n.messages import MessageRef, render
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
    """:func:`validate_directory_coded`, with every reason as its English sentence."""
    spec, reasons = validate_directory_coded(
        enabled=enabled,
        urls=urls,
        start_tls=start_tls,
        ca_pem=ca_pem,
        bind_template=bind_template,
        timeout_seconds=timeout_seconds,
        user_search_base=user_search_base,
    )
    return spec, [render(reason) for reason in reasons]


def validate_directory_coded(
    *,
    enabled: bool,
    urls: list[str],
    start_tls: bool,
    ca_pem: str | None,
    bind_template: str,
    timeout_seconds: int,
    user_search_base: str | None,
) -> tuple[DirectorySpec | None, list[MessageRef]]:
    """Return the normalised spec, or None plus every reason it was refused, as codes.

    Reasons accumulate. A bad URL does not hide an expired certificate.
    """
    reasons: list[MessageRef] = []
    cleaned_urls: list[str] = []
    saw_ldap = False
    saw_ldaps = False
    # The URL reasons are the ones the "needs a URL" rules below defer to.
    url_reason_seen = False

    if len(urls) > _MAX_URLS:
        reasons.append(MessageRef("auth.directory.urls_max", {"max": _MAX_URLS}))
        url_reason_seen = True
    for raw in urls:
        url = raw.strip()
        if not url:
            reasons.append(MessageRef("auth.directory.url_empty"))
            url_reason_seen = True
            continue
        if len(url) > _MAX_URL_LENGTH:
            reasons.append(MessageRef("auth.directory.url_too_long"))
            url_reason_seen = True
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
        reasons.append(MessageRef("auth.directory.start_tls_with_ldaps"))
    if start_tls and not saw_ldap:
        reasons.append(MessageRef("auth.directory.start_tls_needs_ldap"))
    if not start_tls and saw_ldap:
        reasons.append(MessageRef("auth.directory.ldap_needs_start_tls"))
    # Enabled with no URL at all. A URL that failed validation is already listed.
    enabled_needs_url = MessageRef("auth.directory.enabled_needs_url")
    if enabled and not urls and not cleaned_urls and not url_reason_seen:
        reasons.append(enabled_needs_url)
    if enabled and urls and not cleaned_urls and enabled_needs_url not in reasons:
        reasons.append(MessageRef("auth.directory.enabled_needs_valid_url"))

    template = bind_template.strip()
    if len(template) > _MAX_TEMPLATE:
        reasons.append(MessageRef("auth.directory.template_too_long"))
    if "{username}" not in template:
        reasons.append(MessageRef("auth.directory.template_username"))

    if not 1 <= timeout_seconds <= 30:
        reasons.append(MessageRef("auth.directory.timeout_range"))

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


def _url_reasons(url: str) -> list[MessageRef]:
    parts = urlsplit(url)
    reasons: list[MessageRef] = []
    if parts.scheme not in ("ldap", "ldaps"):
        reasons.append(MessageRef("auth.directory.url_scheme", {"url": url}))
        return reasons
    if parts.username or parts.password:
        reasons.append(MessageRef("auth.directory.url_credentials", {"url": url}))
    if not parts.hostname:
        reasons.append(MessageRef("auth.directory.url_host", {"url": url}))
    if parts.path not in ("", "/"):
        reasons.append(MessageRef("auth.directory.url_path", {"url": url}))
    if parts.query:
        reasons.append(MessageRef("auth.directory.url_query", {"url": url}))
    if parts.fragment:
        reasons.append(MessageRef("auth.directory.url_fragment", {"url": url}))
    return reasons


def _certificate(ca_pem: str | None) -> tuple[str | None, list[MessageRef]]:
    if ca_pem is None or not ca_pem.strip():
        return None, []
    text = ca_pem.strip()
    if len(text) > _MAX_PEM:
        return None, [MessageRef("auth.directory.ca_too_long")]
    if "PRIVATE KEY" in text:
        return None, [MessageRef("auth.directory.ca_private_key")]
    try:
        certs = load_pem_x509_certificates(text.encode("utf-8"))
    except (ValueError, TypeError):
        return None, [MessageRef("auth.directory.ca_invalid")]
    if not certs:
        return None, [MessageRef("auth.directory.ca_invalid")]
    now = datetime.now(UTC)
    reasons: list[MessageRef] = []
    for cert in certs:
        subject = cert.subject.rfc4514_string()
        if cert.not_valid_before_utc > now:
            reasons.append(
                MessageRef(
                    "auth.directory.cert_not_yet_valid",
                    {"subject": subject, "not_before": cert.not_valid_before_utc.isoformat()},
                )
            )
        if cert.not_valid_after_utc <= now:
            reasons.append(
                MessageRef(
                    "auth.directory.cert_expired",
                    {"subject": subject, "not_after": cert.not_valid_after_utc.isoformat()},
                )
            )
    if reasons:
        return None, reasons
    return text + "\n" if not text.endswith("\n") else text, []


def _search_base(value: str | None) -> tuple[str | None, list[MessageRef]]:
    if value is None or not value.strip():
        return None, []
    base = value.strip()
    if len(base) > _MAX_BASE:
        return None, [MessageRef("auth.directory.base_too_long")]
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in base):
        return None, [MessageRef("auth.directory.base_control_character")]
    return base, []


async def get_ldap_row(session: AsyncSession) -> LdapConfig | None:
    return await session.get(LdapConfig, 1)
