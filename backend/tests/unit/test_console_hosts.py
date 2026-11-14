"""SEC-RO2: host normalisation for the console-target refusal (string compare, no DNS)."""

from __future__ import annotations

import pytest

from app.alerts.notifiers.console_hosts import (
    host_set,
    normalise_host,
    refuse_console_target,
    targets_console,
)
from app.alerts.notifiers.url_guard import URLGuardError


@pytest.mark.parametrize(
    ("raw", "host"),
    [
        ("https://Console.Example:8443/api", "console.example"),
        ("console.example", "console.example"),
        ("console.example:443", "console.example"),
        ("https://console.example./", "console.example"),
        ("https://[2001:DB8::1]/x", "2001:db8::1"),
        ("https://[::ffff:10.0.0.5]/x", "10.0.0.5"),
        ("10.0.0.5:5000", "10.0.0.5"),
        ("", None),
        (None, None),
        ("https://", None),
    ],
)
def test_normalise_host(raw: str | None, host: str | None) -> None:
    assert normalise_host(raw) == host


def test_targets_console_and_refusal_carry_no_url() -> None:
    hosts = host_set(["https://qr.example", None, "", "10.9.9.9"])
    assert hosts == {"qr.example", "10.9.9.9"}
    assert targets_console("https://QR.example:9/x?k=secret", hosts)
    assert not targets_console("https://other.example/x", hosts)
    assert not targets_console("not a url at all ::", frozenset({"qr.example"}))
    with pytest.raises(URLGuardError) as caught:
        refuse_console_target("https://qr.example/secret-path", hosts)
    assert caught.value.code == "notifications.console_refused"
    assert "secret-path" not in str(caught.value)
    refuse_console_target("https://other.example/", hosts)  # no raise
