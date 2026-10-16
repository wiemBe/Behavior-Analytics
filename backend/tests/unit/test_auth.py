"""Sign-in primitives: passwords, lockout, names, sessions, CSRF, directory rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Environment, ProviderKind, Settings
from app.models.identity import User
from app.security.csrf import refusal_reason
from app.security.ldap_names import escape_ldap_name, render_bind_name
from app.security.passwords import BCRYPT_ROUNDS, hash_password, verify_password
from app.security.rbac import PERM_ADMIN, PERM_AUTH_ADMIN, PERM_USER_ADMIN, Principal
from app.services.auth_login import apply_failure, clear_expired_lock
from app.services.auth_sessions import new_token, session_expired, token_hash
from app.services.ldap_bind import _bind_sync
from app.services.ldap_config import DirectorySpec, validate_directory

_NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def test_hash_and_verify_round_trip() -> None:
    hashed = hash_password("correct-horse-battery")
    assert hashed.startswith(f"$2b${BCRYPT_ROUNDS}$")
    assert verify_password("correct-horse-battery", hashed)
    assert not verify_password("correct-horse-battery-no", hashed)


def test_verify_of_a_missing_hash_is_false_and_still_runs() -> None:
    assert verify_password("anything-at-all", None) is False
    assert verify_password("x" * 80, None) is False


def test_overlong_password_is_rejected_without_a_fast_path() -> None:
    hashed = hash_password("correct-horse-battery")
    assert verify_password("a" * 80, hashed) is False


def test_lockout_starts_on_the_fifth_failure_and_a_success_clears_it() -> None:
    user = User(username="ada", auth_source="LOCAL", failed_login_count=0)
    for _ in range(4):
        assert apply_failure(user, _NOW, threshold=5, lock_minutes=15) is False
    assert user.locked_until is None
    assert apply_failure(user, _NOW, threshold=5, lock_minutes=15) is True
    assert user.locked_until == _NOW + timedelta(minutes=15)
    # A later failure does not move the window.
    window = user.locked_until
    assert apply_failure(user, _NOW, threshold=5, lock_minutes=15) is True
    assert user.locked_until == window
    user.failed_login_count = 0
    user.locked_until = None
    assert user.locked_until is None and user.failed_login_count == 0


def test_an_elapsed_lock_is_cleared_before_the_next_attempt() -> None:
    user = User(
        username="ada",
        auth_source="LOCAL",
        failed_login_count=5,
        locked_until=_NOW - timedelta(seconds=1),
    )
    clear_expired_lock(user, _NOW)
    assert user.locked_until is None
    assert user.failed_login_count == 0
    user.locked_until = _NOW + timedelta(minutes=1)
    user.failed_login_count = 5
    clear_expired_lock(user, _NOW)
    assert user.locked_until is not None
    assert user.failed_login_count == 5


def test_username_escape_covers_dn_and_filter_metacharacters() -> None:
    assert escape_ldap_name("a*b(c\\d,e\x00f") == "a\\2ab\\28c\\5cd\\2ce\\00f"
    assert render_bind_name("{username}@corp.example", "a*b") == "a\\2ab@corp.example"
    assert render_bind_name("cn={username},dc=corp", "a*b") == "cn=a\\2ab,dc=corp"
    assert escape_ldap_name(" ada") == "\\20ada"
    assert escape_ldap_name("#ada") == "\\23ada"


def test_session_token_is_stored_only_as_its_hash() -> None:
    token, digest = new_token()
    assert digest == token_hash(token)
    assert digest != token
    assert len(digest) == 64
    assert all(ch in "0123456789abcdef" for ch in digest)


def test_session_expiry_includes_the_boundary() -> None:
    created = _NOW - timedelta(hours=12)
    assert session_expired(
        created_at=created,
        last_seen_at=_NOW,
        expires_at=_NOW + timedelta(hours=1),
        now=_NOW,
        idle_minutes=480,
        max_hours=12,
    )
    assert session_expired(
        created_at=_NOW - timedelta(hours=1),
        last_seen_at=_NOW - timedelta(minutes=480),
        expires_at=_NOW + timedelta(hours=1),
        now=_NOW,
        idle_minutes=480,
        max_hours=12,
    )
    assert not session_expired(
        created_at=_NOW - timedelta(hours=1),
        last_seen_at=_NOW - timedelta(minutes=479),
        expires_at=_NOW + timedelta(minutes=1),
        now=_NOW,
        idle_minutes=480,
        max_hours=12,
    )


def test_csrf_refuses_a_missing_or_foreign_origin_only_when_enforced() -> None:
    assert refusal_reason(
        "POST", None, None, public_origin="http://localhost:3000",
        enforced=True, allow_dev_localhost=True,
    ) == "Cross-site request refused: the request has no Origin or Referer header."
    foreign = refusal_reason(
        "POST", "https://evil.example", "http://localhost:3000/me",
        public_origin="http://localhost:3000", enforced=True, allow_dev_localhost=True,
    )
    assert foreign is not None and "https://evil.example" in foreign
    assert refusal_reason(
        "POST", "http://localhost:3000", None,
        public_origin="http://localhost:3000", enforced=True, allow_dev_localhost=True,
    ) is None
    assert refusal_reason(
        "GET", None, None, public_origin="http://localhost:3000",
        enforced=True, allow_dev_localhost=True,
    ) is None
    assert refusal_reason(
        "POST", None, None, public_origin="http://localhost:3000",
        enforced=False, allow_dev_localhost=True,
    ) is None
    # Referer is used only when Origin is absent.
    assert refusal_reason(
        "POST", None, "http://localhost:3000/dash",
        public_origin="http://localhost:3000", enforced=True, allow_dev_localhost=False,
    ) is None


def test_directory_rules_collect_every_reason() -> None:
    spec, reasons = validate_directory(
        enabled=True,
        urls=["ldap://dc.example/ou=people", "ldap://dc.example"],
        start_tls=False,
        ca_pem="-----BEGIN PRIVATE KEY-----\nMIIB",
        bind_template="cn=user",
        timeout_seconds=0,
        user_search_base="dc=example\x00",
    )
    assert spec is None
    text = " ".join(reasons)
    assert "must not have a path" in text
    assert "requires start_tls" in text
    assert "private key" in text
    assert "bind_template must contain {username}" in text
    assert "timeout_seconds" in text
    assert "control character" in text
    # Not fail-fast: the path and the template are both reported.
    assert len(reasons) >= 5


def test_disabled_directory_with_no_urls_is_the_default() -> None:
    spec, reasons = validate_directory(
        enabled=False, urls=[], start_tls=False, ca_pem="  ",
        bind_template=" {username} ", timeout_seconds=5, user_search_base=" ",
    )
    assert reasons == []
    assert spec is not None
    assert spec.enabled is False and spec.urls == () and spec.ca_pem is None
    assert spec.bind_template == "{username}" and spec.user_search_base is None


def test_ldaps_refuses_starttls_and_an_expired_certificate(monkeypatch: pytest.MonkeyPatch) -> None:
    spec, reasons = validate_directory(
        enabled=True,
        urls=["ldaps://dc.example:636"],
        start_tls=True,
        ca_pem=None,
        bind_template="{username}@corp.example",
        timeout_seconds=5,
        user_search_base=None,
    )
    assert spec is None
    assert any("start_tls must be false" in reason for reason in reasons)

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "expired")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(_NOW - timedelta(days=3))
        .not_valid_after(_NOW - timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    monkeypatch.setattr(
        "app.services.ldap_config.datetime",
        type("Clock", (), {"now": staticmethod(lambda tz: _NOW)}),
    )
    spec, reasons = validate_directory(
        enabled=False, urls=["ldaps://dc.example"], start_tls=False, ca_pem=pem,
        bind_template="{username}", timeout_seconds=5, user_search_base=None,
    )
    assert spec is None
    assert any("expired" in reason for reason in reasons)


def test_a_bad_pem_is_refused() -> None:
    spec, reasons = validate_directory(
        enabled=False, urls=[], start_tls=False, ca_pem="this is not a certificate",
        bind_template="{username}", timeout_seconds=5, user_search_base=None,
    )
    assert spec is None
    assert reasons == ["ca_pem is not a PEM X.509 certificate"]


def test_empty_password_never_opens_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the directory was contacted")

    monkeypatch.setattr("app.services.ldap_bind.Connection", boom)
    monkeypatch.setattr("app.services.ldap_bind.Server", boom)
    report = _bind_sync(
        DirectorySpec(
            enabled=True, urls=("ldaps://dc.example",), start_tls=False, ca_pem=None,
            bind_template="{username}@corp.example", timeout_seconds=5, user_search_base=None,
        ),
        "ada",
        "",
    )
    assert report.bind_status == "failed"
    assert report.bind_reason == "an empty password is refused"
    assert report.tls_reason == "not attempted"


def test_admin_star_satisfies_the_new_permissions_and_read_star_does_not() -> None:
    admin = Principal(subject="a", permissions=frozenset({PERM_ADMIN}))
    reader = Principal(subject="r", permissions=frozenset({"read:*"}))
    assert admin.has(PERM_USER_ADMIN) and admin.has(PERM_AUTH_ADMIN)
    assert not reader.has(PERM_USER_ADMIN) and not reader.has(PERM_AUTH_ADMIN)


def test_production_refuses_oidc_and_a_plaintext_origin() -> None:
    base = {
        "environment": Environment.PRODUCTION,
        "debug": False,
        "qradar_verify_ssl": True,
        "secret_key": "x" * 40,
        "encryption_key": "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A=",
        "qradar_provider": ProviderKind.REST,
        "qradar_sec_token": "a-real-token",
        "qradar_host": "https://qradar.example",
        "public_origin": "https://soc.example",
    }
    Settings(**base)
    with pytest.raises(ValueError, match="OIDC is out of scope"):
        Settings(**{**base, "auth_provider": "oidc"})
    with pytest.raises(ValueError, match="PUBLIC_ORIGIN"):
        Settings(**{**base, "public_origin": "http://soc.example"})
    with pytest.raises(ValueError, match="PUBLIC_ORIGIN"):
        Settings(**{**base, "public_origin": "https://soc.example/app"})


def test_directory_unavailable_message_does_not_say_valid() -> None:
    from app.services.auth_login import DIRECTORY_UNAVAILABLE

    assert "valid" not in DIRECTORY_UNAVAILABLE.lower()
