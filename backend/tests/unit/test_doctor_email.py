"""The doctor's one e-mail line (MAIL-1): configured or not, and an opt-in handshake."""

from __future__ import annotations

import sys

import pytest

from app.cli import doctor as doctor_module
from app.cli.doctor import CheckResult, Doctor, Status
from app.core.config import Settings
from app.services import email_delivery as delivery

PASSWORD = "doctor-relay-password-planted"


def _make(check_smtp: bool = False, **over: object) -> Doctor:
    return Doctor(settings=Settings(**over), check_smtp=check_smtp)  # type: ignore[arg-type]


@pytest.fixture
def handshakes(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    calls: list[object] = []
    monkeypatch.setattr(delivery, "check_handshake", lambda s, t=0: calls.append((s, t)))
    return calls


def test_the_check_is_registered_with_its_runbook_before_qradar_health() -> None:
    assert ("email", "OPS-R68") in doctor_module._CHECK_RUNBOOKS
    # The existing tests pin qradar_health as the last check; this one sits before it.
    assert doctor_module._CHECK_RUNBOOKS[-1] == ("qradar_health", "OPS-R67")


@pytest.mark.asyncio
async def test_not_configured_is_a_skip_that_says_so_and_touches_nothing(handshakes) -> None:
    result = await _make(check_smtp=True, smtp_host="").check_email()
    assert result.status is Status.SKIP
    assert "not configured" in result.message and "SMTP_HOST" in result.message
    assert handshakes == []


@pytest.mark.asyncio
async def test_configured_prints_host_port_mode_and_login_but_never_connects_by_default(
    handshakes,
) -> None:
    result = await _make(
        smtp_host="relay.example.internal", smtp_port=465, smtp_security="ssl",
        smtp_username="relay-user", smtp_password=PASSWORD,
    ).check_email()
    assert result.status is Status.PASS
    assert result.message == (
        "configured: relay relay.example.internal:465, security ssl, authenticated "
        "(handshake not tested; use --check-smtp)"
    )
    assert PASSWORD not in result.message and "relay-user" not in result.message
    assert handshakes == []


@pytest.mark.asyncio
async def test_the_legacy_switch_is_what_the_line_reports(handshakes) -> None:
    on = await _make(smtp_host="r.example", smtp_use_tls=True).check_email()
    off = await _make(smtp_host="r.example", smtp_use_tls=False).check_email()
    assert "security starttls, no login" in on.message
    assert "security none, no login" in off.message and off.status is Status.PASS


@pytest.mark.asyncio
async def test_a_login_over_a_plain_relay_fails_and_never_connects(handshakes) -> None:
    result = await _make(
        smtp_host="r.example", smtp_security="none", smtp_username="u", smtp_password=PASSWORD
    ).check_email()
    assert result.status is Status.FAIL and result.runbook == "OPS-R68"
    assert "refused" in result.message and "clear text" in result.message
    assert PASSWORD not in result.message
    # Even with --check-smtp nothing connects: the configuration is refused first.
    checked = await _make(
        True, smtp_host="r.example", smtp_security="none", smtp_username="u", smtp_password=PASSWORD
    ).check_email()
    assert checked.status is Status.FAIL
    assert handshakes == []
    # The same plain relay without a username is allowed.
    plain = await _make(smtp_host="r.example", smtp_security="none").check_email()
    assert plain.status is Status.PASS


@pytest.mark.asyncio
async def test_check_smtp_runs_the_handshake_and_reports_success(handshakes) -> None:
    result = await _make(True, smtp_host="r.example", smtp_security="starttls").check_email()
    assert result.status is Status.PASS
    assert result.message.endswith("handshake completed, nothing sent")
    assert len(handshakes) == 1


@pytest.mark.asyncio
async def test_check_smtp_failure_is_a_warn_with_the_code_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(settings, timeout=0):  # type: ignore[no-untyped-def]
        raise delivery.EmailDeliveryError("tls_failed")

    monkeypatch.setattr(delivery, "check_handshake", fail)
    result = await _make(True, smtp_host="r.example").check_email()
    assert result == CheckResult(
        "email",
        Status.WARN,
        "relay r.example:587, security starttls, no login: handshake failed (tls_failed)",
        "OPS-R68",
    )


def test_main_passes_the_flag_only_when_given(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    seen: list[bool] = []

    class Spy:
        def __init__(self, check_smtp: bool = False) -> None:
            seen.append(check_smtp)

        async def run(self) -> list[CheckResult]:
            return [CheckResult("email", Status.PASS, "ok")]

    async def nothing() -> None:
        return None

    monkeypatch.setattr(doctor_module, "Doctor", Spy)
    monkeypatch.setattr(doctor_module, "dispose_engine", nothing)
    monkeypatch.setattr(sys, "argv", ["doctor"])
    assert doctor_module.main() == 0
    monkeypatch.setattr(sys, "argv", ["doctor", "--check-smtp"])
    assert doctor_module.main() == 0
    assert seen == [False, True]
    capsys.readouterr()


def test_the_support_bundle_drops_the_smtp_password_and_keeps_the_rest() -> None:
    from app.cli.support_bundle import filter_config_env

    env = (
        b"SMTP_HOST=relay.example.internal\nSMTP_SECURITY=ssl\nSMTP_USERNAME=relay-user\n"
        b"SMTP_PASSWORD=" + PASSWORD.encode() + b"\nSMTP_CA_FILE=/etc/relay-ca.pem\n"
    )
    kept, secrets = filter_config_env(env)
    assert PASSWORD.encode() not in kept and b"SMTP_PASSWORD" not in kept
    assert secrets == [PASSWORD.encode()]
    for name in (b"SMTP_HOST", b"SMTP_SECURITY", b"SMTP_USERNAME", b"SMTP_CA_FILE"):
        assert name in kept
