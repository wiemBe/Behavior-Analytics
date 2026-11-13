"""SSRF guard for named webhook destinations; no DNS or network is used."""

from __future__ import annotations

import pytest

from app.alerts.notifiers.url_guard import URLGuardError, guard_url
from app.core.config import Settings

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:4700:4700::1111"


def _resolver(*addresses: str):  # type: ignore[no-untyped-def]
    def resolve(host: str, port: int) -> tuple[str, ...]:
        assert host == "hooks.example" and port in {80, 443, 8443}
        return addresses

    return resolve


@pytest.mark.parametrize(
    ("address", "address_class"),
    [
        ("10.0.0.1", "private IPv4"),
        ("127.0.0.1", "loopback IPv4"),
        ("169.254.3.4", "link-local IPv4"),
        ("224.0.0.1", "multicast IPv4"),
        ("0.0.0.0", "unspecified IPv4"),  # noqa: S104 - input to a pure refusal
        ("240.0.0.1", "reserved IPv4"),
        ("169.254.169.254", "cloud metadata"),
        ("fc00::1", "private IPv6"),
        ("::1", "loopback IPv6"),
        ("fe80::1", "link-local IPv6"),
        ("ff02::1", "multicast IPv6"),
        ("::", "unspecified IPv6"),
        ("2001:db8::1", "non-public reserved IPv6"),
        ("::ffff:127.0.0.1", "IPv4-mapped IPv6 loopback"),
        ("::ffff:169.254.169.254", "IPv4-mapped metadata"),
    ],
)
def test_every_non_public_address_class_is_refused(address: str, address_class: str) -> None:
    del address_class
    with pytest.raises(URLGuardError) as caught:
        guard_url("https://hooks.example/x", resolver=_resolver(address))
    assert caught.value.code == "notifications.private_refused"


def test_any_private_answer_refuses_the_whole_hostname() -> None:
    with pytest.raises(URLGuardError) as caught:
        guard_url(
            "https://hooks.example/x",
            resolver=_resolver(PUBLIC_V4, "10.0.0.9", PUBLIC_V6),
        )
    assert caught.value.code == "notifications.private_refused"


def test_private_flag_allows_private_literal_mapped_and_mixed_answers() -> None:
    literal = guard_url("https://[::1]/x", allow_private=True, resolver=_resolver())
    assert literal.addresses == ("::1",)
    mapped = guard_url(
        "https://hooks.example/x",
        allow_private=True,
        resolver=_resolver("::ffff:10.0.0.2", PUBLIC_V4),
    )
    assert mapped.addresses == ("::ffff:10.0.0.2", PUBLIC_V4)


def test_http_needs_its_own_flag_independently_of_private_addresses() -> None:
    with pytest.raises(URLGuardError) as caught:
        guard_url("http://hooks.example/x", resolver=_resolver(PUBLIC_V4))
    assert caught.value.code == "notifications.http_refused"
    checked = guard_url(
        "http://hooks.example/x", allow_http=True, resolver=_resolver(PUBLIC_V4)
    )
    assert checked.port == 80 and checked.addresses == (PUBLIC_V4,)
    with pytest.raises(URLGuardError) as private:
        guard_url(
            "http://hooks.example/x",
            allow_http=True,
            resolver=_resolver("10.0.0.1"),
        )
    assert private.value.code == "notifications.private_refused"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://hooks.example/x",
        "https:///missing-host",
        "https://user@hooks.example/x",
        "https://user:pass@hooks.example/x",
        "https://hooks.example:99999/x",
        "https://hooks.example/x#fragment",
    ],
)
def test_malformed_or_credentialed_urls_are_refused(url: str) -> None:
    with pytest.raises(URLGuardError) as caught:
        guard_url(url, resolver=_resolver(PUBLIC_V4))
    assert caught.value.code == "notifications.url_invalid"


@pytest.mark.parametrize("answers", [(), ("not-an-address",)])
def test_empty_or_malformed_resolver_answers_are_refused(answers: tuple[str, ...]) -> None:
    with pytest.raises(URLGuardError) as caught:
        guard_url("https://hooks.example/x", resolver=_resolver(*answers))
    assert caught.value.code == "notifications.url_invalid"


def test_resolver_failure_is_a_coded_invalid_url() -> None:
    def fail(host: str, port: int) -> tuple[str, ...]:
        raise OSError("DNS details must not escape")

    with pytest.raises(URLGuardError) as caught:
        guard_url("https://hooks.example/x", resolver=fail)
    assert caught.value.code == "notifications.url_invalid"
    assert "DNS details" not in str(caught.value)


def test_literal_ip_is_checked_without_calling_the_resolver() -> None:
    def no_dns(host: str, port: int) -> tuple[str, ...]:
        raise AssertionError("a literal address must not use DNS")

    checked = guard_url(f"https://{PUBLIC_V4}/x", resolver=no_dns)
    assert checked.addresses == (PUBLIC_V4,)


def test_pinned_url_preserves_path_query_host_and_sni_inputs() -> None:
    checked = guard_url(
        "https://hooks.example:8443/private/path?token=hidden",
        resolver=_resolver(PUBLIC_V4, PUBLIC_V6),
    )
    assert checked.display == "https://hooks.example:8443"
    assert checked.host_header == "hooks.example:8443"
    assert checked.pinned_url(PUBLIC_V4) == (
        f"https://{PUBLIC_V4}:8443/private/path?token=hidden"
    )
    assert checked.pinned_url(PUBLIC_V6) == (
        f"https://[{PUBLIC_V6}]:8443/private/path?token=hidden"
    )
    with pytest.raises(ValueError, match="was not checked"):
        checked.pinned_url("8.8.8.8")


def test_webhook_test_limit_defaults_to_five_per_minute() -> None:
    assert Settings.model_fields["rate_limit_webhook_test"].default == "5/minute"
