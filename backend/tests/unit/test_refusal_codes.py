"""Refusal sentences (I18N-B1 Family 15): English byte for byte, the code beside it."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import app.i18n.messages as messages
from app.api.deps import requires
from app.i18n.messages import MessageRef, MessageSpec, ParamKind, ParamSpec, render
from app.i18n.refusal import CodedHTTPException, coded_body, coded_http_exception_handler
from app.main import create_app
from app.security.auth import get_principal
from app.security.csrf import refusal_reason, refusal_ref
from app.security.passwords import password_policy_reasons, password_policy_refs
from app.security.rbac import Principal
from app.services import property_watches as svc
from app.services.auth_login import (
    DIRECTORY_UNAVAILABLE,
    INVALID_CREDENTIALS,
    LoginFailure,
)
from app.services.dashboards import DashboardError
from app.services.ldap_config import validate_directory, validate_directory_coded


def _app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(CodedHTTPException, coded_http_exception_handler)  # type: ignore[arg-type]
    return app


# --- the English is exactly what the old code built ----------------------------------------

#: (code, params, the sentence the code built before MSG-1). Edge values included.
PINS: list[tuple[str, dict[str, object], str]] = [
    (
        "auth.permission.missing",
        {"permission": "admin:users"},
        "missing required permission: admin:users",
    ),
    ("auth.permission.missing", {"permission": "read:*"}, "missing required permission: read:*"),
    ("auth.password.too_short", {}, "password must be at least 12 characters"),
    ("auth.password.too_long", {}, "password must be at most 72 bytes"),
    ("auth.password.equals_username", {}, "password must not equal the username"),
    (
        "auth.password.ldap_directory",
        {},
        "LDAP accounts change their password in the directory, not here.",
    ),
    ("auth.password.local_only", {}, "only a local account can change its password"),
    ("auth.password.current_wrong", {}, "current password is wrong"),
    (
        "auth.username.shape",
        {},
        "username must be 1 to 255 characters without spaces or control characters",
    ),
    ("auth.login.invalid_credentials", {}, "Invalid username or password"),
    (
        "auth.login.directory_unavailable",
        {},
        "The directory server is unavailable. Sign-in was not completed.",
    ),
    ("auth.session.required", {}, "Authentication required."),
    (
        "auth.session.must_change_password",
        {},
        "A password change is required before any other request.",
    ),
    (
        "auth.csrf.origin_missing",
        {},
        "Cross-site request refused: the request has no Origin or Referer header.",
    ),
    (
        "auth.csrf.origin_mismatch",
        {"origin": "https://evil.example"},
        "Cross-site request refused: origin https://evil.example "
        "is not the configured public origin.",
    ),
    (
        "auth.user.last_admin",
        {},
        "the last active user holding admin:* cannot be disabled or lose that role",
    ),
    ("auth.user.not_found", {}, "No user has that id."),
    ("auth.user.ldap_no_password", {}, "An LDAP user has no password here."),
    ("auth.user.local_needs_password", {}, "A local user needs an initial password."),
    ("auth.user.source_invalid", {}, "auth_source must be LOCAL or LDAP"),
    ("auth.user.exists", {}, "A user with that username or email already exists."),
    ("auth.user.username_taken", {}, "A user with that username already exists."),
    ("auth.user.email_taken", {}, "A user with that email already exists."),
    ("auth.user.email_unusable", {}, "email is not usable"),
    (
        "auth.user.reset_local_only",
        {},
        "Only a local account can have its password reset here.",
    ),
    ("auth.user.role_unknown", {"name": "nope"}, "unknown role: nope"),
    (
        "auth.directory.stale",
        {},
        "The directory configuration is stale. Reload it and retry.",
    ),
    ("auth.directory.test_needs_url", {}, "the test needs a directory URL"),
    ("auth.directory.urls_max", {"max": 8}, "at most 8 directory URLs are allowed"),
    ("auth.directory.url_empty", {}, "a directory URL is empty"),
    ("auth.directory.url_too_long", {}, "a directory URL is longer than 512 characters"),
    (
        "auth.directory.url_scheme",
        {"url": "http://dc"},
        "http://dc must use ldap:// or ldaps://",
    ),
    (
        "auth.directory.url_credentials",
        {"url": "ldap://u@dc"},
        "ldap://u@dc must not contain credentials",
    ),
    ("auth.directory.url_host", {"url": "ldap://"}, "ldap:// must name a host"),
    ("auth.directory.url_path", {"url": "ldap://dc/x"}, "ldap://dc/x must not have a path"),
    ("auth.directory.url_query", {"url": "ldap://dc?x"}, "ldap://dc?x must not have a query"),
    (
        "auth.directory.url_fragment",
        {"url": "ldap://dc#x"},
        "ldap://dc#x must not have a fragment",
    ),
    (
        "auth.directory.start_tls_with_ldaps",
        {},
        "start_tls must be false for an ldaps:// URL",
    ),
    ("auth.directory.start_tls_needs_ldap", {}, "start_tls requires an ldap:// URL"),
    ("auth.directory.ldap_needs_start_tls", {}, "an ldap:// URL requires start_tls"),
    (
        "auth.directory.enabled_needs_url",
        {},
        "an enabled configuration needs at least one directory URL",
    ),
    (
        "auth.directory.enabled_needs_valid_url",
        {},
        "an enabled configuration needs at least one valid directory URL",
    ),
    (
        "auth.directory.template_too_long",
        {},
        "bind_template is longer than 512 characters",
    ),
    ("auth.directory.template_username", {}, "bind_template must contain {username}"),
    ("auth.directory.timeout_range", {}, "timeout_seconds must be between 1 and 30"),
    ("auth.directory.ca_too_long", {}, "ca_pem is too long"),
    (
        "auth.directory.ca_private_key",
        {},
        "ca_pem must be a certificate, not a private key",
    ),
    ("auth.directory.ca_invalid", {}, "ca_pem is not a PEM X.509 certificate"),
    (
        "auth.directory.cert_not_yet_valid",
        {"subject": "CN=ca", "not_before": "2030-01-01T00:00:00+00:00"},
        "certificate CN=ca is not yet valid (not_before 2030-01-01T00:00:00+00:00)",
    ),
    (
        "auth.directory.cert_expired",
        {"subject": "CN=ca", "not_after": "2020-01-01T00:00:00+00:00"},
        "certificate CN=ca is expired (not_after 2020-01-01T00:00:00+00:00)",
    ),
    (
        "auth.directory.base_too_long",
        {},
        "user_search_base is longer than 1024 characters",
    ),
    (
        "auth.directory.base_control_character",
        {},
        "user_search_base contains a control character",
    ),
    ("dashboard.not_found", {}, "dashboard not found"),
    (
        "dashboard.name_taken",
        {"name": "Main"},
        "you already have a dashboard named 'Main'",
    ),
    (
        "dashboard.name_taken",
        {"name": "it's \"odd\""},
        "you already have a dashboard named 'it's \"odd\"'",
    ),
    ("dashboard.owner_limit", {"limit": 20}, "the limit is 20 dashboards per owner"),
    (
        "dashboard.owner_only",
        {},
        "only the owner or an administrator can change this dashboard",
    ),
    (
        "dashboard.visibility_needs_permission",
        {"permission": "admin:dashboards"},
        "changing visibility needs the admin:dashboards permission",
    ),
    ("dashboard.changed_since_opened", {}, "this dashboard changed since you opened it"),
    (
        "propertywatch.name_exists",
        {"name": "Admin logons"},
        "a property watch named 'Admin logons' already exists",
    ),
    (
        "customrule.name_exists",
        {"name": "Volume drop"},
        "a custom rule named 'Volume drop' already exists",
    ),
    (
        "customrule.watch_value_gone",
        {},
        "the property-watch value no longer exists (its watch changed or was deleted "
        "while the rule was saved); reload and choose a value again",
    ),
    (
        "propertywatch.dependency.refused",
        {"action": MessageRef("propertywatch.action.delete"), "names": "'A', 'B'"},
        "cannot delete the watch: custom rule(s) 'A', 'B' pin a value of this watch; "
        "detach or delete the rule(s) first",
    ),
    (
        "propertywatch.dependency.raced",
        {"action": MessageRef("propertywatch.action.change_scope")},
        "cannot change the scope or properties (the watch version would change): a custom "
        "rule pinned a value of this watch while the request ran; reload and try again",
    ),
]


@pytest.mark.parametrize(("code", "params", "english"), PINS)
def test_each_refusal_renders_the_sentence_the_old_code_built(
    code: str, params: dict[str, object], english: str
) -> None:
    assert render(MessageRef(code, params)) == english


def test_every_msg1_code_is_pinned_above() -> None:
    families = ("auth.", "dashboard.", "propertywatch.", "customrule.")
    registered = {
        spec.code
        for spec in messages.REGISTRY
        if spec.code.startswith(families)
    }
    pinned = {code for code, _, _ in PINS} | {
        "propertywatch.action.change_pins",
        "propertywatch.action.change_scope",
        "propertywatch.action.unpin",
        "propertywatch.action.delete",
    }
    assert registered == pinned


@pytest.mark.parametrize(
    ("action", "english"),
    [
        ("change_pins", "change the scope, properties or pins"),
        ("change_scope", "change the scope or properties (the watch version would change)"),
        ("unpin", "unpin a value"),
        ("delete", "delete the watch"),
    ],
)
def test_each_dependency_action_renders_its_old_phrase(action: str, english: str) -> None:
    assert render(MessageRef(f"propertywatch.action.{action}")) == english
    assert svc.action_ref(english) == MessageRef(f"propertywatch.action.{action}")


# --- the wire ------------------------------------------------------------------------------


def test_a_sentence_refusal_carries_detail_and_its_code_siblings() -> None:
    app = _app()

    @app.get("/x")
    async def route() -> None:
        raise CodedHTTPException(409, MessageRef("dashboard.name_taken", {"name": "Main"}))

    response = TestClient(app).get("/x")
    assert response.status_code == 409
    assert response.json() == {
        "detail": "you already have a dashboard named 'Main'",
        "detail_code": "dashboard.name_taken",
        "detail_params": {"name": "Main"},
    }


def test_a_list_refusal_carries_parallel_codes() -> None:
    app = _app()

    @app.get("/x")
    async def route() -> None:
        raise CodedHTTPException(
            422,
            refs=[
                MessageRef("auth.password.too_short"),
                MessageRef("auth.user.role_unknown", {"name": "ghost"}),
            ],
        )

    body = TestClient(app).get("/x").json()
    assert body == {
        "detail": ["password must be at least 12 characters", "unknown role: ghost"],
        "detail_codes": [
            {"code": "auth.password.too_short", "params": {}},
            {"code": "auth.user.role_unknown", "params": {"name": "ghost"}},
        ],
    }
    assert "detail_code" not in body


def test_a_structured_refusal_keeps_message_and_carries_the_code_inside() -> None:
    app = _app()

    @app.get("/x")
    async def route() -> None:
        raise CodedHTTPException(
            409, MessageRef("dashboard.changed_since_opened"), fields={"current_revision": 4}
        )

    body = TestClient(app).get("/x").json()
    assert body == {
        "detail": {
            "message": "this dashboard changed since you opened it",
            "current_revision": 4,
            "code": "dashboard.changed_since_opened",
            "params": {},
        }
    }


def test_a_coded_refusal_needs_exactly_one_shape() -> None:
    with pytest.raises(ValueError):
        CodedHTTPException(422)
    with pytest.raises(ValueError):
        CodedHTTPException(422, MessageRef("auth.password.too_short"), refs=[])
    with pytest.raises(ValueError):
        CodedHTTPException(422, refs=[], fields={"a": 1})


def test_a_coded_refusal_keeps_its_headers() -> None:
    app = _app()

    @app.get("/x")
    async def route() -> None:
        raise CodedHTTPException(
            401, MessageRef("auth.session.required"), headers={"WWW-Authenticate": "Bearer"}
        )

    assert TestClient(app).get("/x").headers["www-authenticate"] == "Bearer"


def test_the_application_registers_the_handler_for_coded_refusals() -> None:
    assert create_app().exception_handlers[CodedHTTPException] is coded_http_exception_handler


def test_a_plain_http_exception_is_unchanged() -> None:
    from fastapi import HTTPException

    app = _app()

    @app.get("/x")
    async def route() -> None:
        raise HTTPException(404, "search not found")

    assert TestClient(app).get("/x").json() == {"detail": "search not found"}


def test_the_permission_guard_answers_403_with_the_permission_code() -> None:
    app = _app()
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="s", permissions=frozenset({"read:*"})
    )

    @app.get("/x", dependencies=[Depends(requires("admin:users"))])
    async def route() -> dict[str, str]:
        return {"ok": "yes"}

    response = TestClient(app).get("/x")
    assert response.status_code == 403
    assert response.json() == {
        "detail": "missing required permission: admin:users",
        "detail_code": "auth.permission.missing",
        "detail_params": {"permission": "admin:users"},
    }


def test_the_csrf_refusal_body_is_coded() -> None:
    ref = refusal_ref(
        "POST",
        "https://evil.example\r\n",
        None,
        public_origin="https://qradar.example",
        enforced=True,
        allow_dev_localhost=False,
    )
    assert ref == MessageRef("auth.csrf.origin_mismatch", {"origin": "https://evil.example"})
    assert coded_body(ref) == {
        "detail": "Cross-site request refused: origin https://evil.example "
        "is not the configured public origin.",
        "detail_code": "auth.csrf.origin_mismatch",
        "detail_params": {"origin": "https://evil.example"},
    }
    kwargs = {"public_origin": "https://q.example", "enforced": True, "allow_dev_localhost": False}
    missing = refusal_ref("DELETE", None, None, **kwargs)
    assert missing == MessageRef("auth.csrf.origin_missing")
    assert refusal_reason("DELETE", None, None, **kwargs) == render(missing)
    assert refusal_ref("GET", None, None, **kwargs) is None
    assert refusal_ref("POST", "https://q.example", None, **kwargs) is None


# --- the sites ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("password", "username", "codes"),
    [
        ("a" * 12, "bob", []),
        ("a" * 11, "bob", ["auth.password.too_short"]),
        ("", "bob", ["auth.password.too_short"]),
        ("é" * 37, "bob", ["auth.password.too_long"]),
        ("é" * 36, "bob", []),
        ("Bobbobbobbob", "BOBBOBBOBBOB", ["auth.password.equals_username"]),
        ("short", "SHORT", ["auth.password.too_short", "auth.password.equals_username"]),
    ],
)
def test_the_password_policy_codes_and_english_agree(
    password: str, username: str, codes: list[str]
) -> None:
    refs = password_policy_refs(password, username)
    assert [ref.code for ref in refs] == codes
    assert password_policy_reasons(password, username) == [render(ref) for ref in refs]


def test_the_login_failures_keep_their_constants_and_carry_their_codes() -> None:
    invalid = LoginFailure(401, MessageRef("auth.login.invalid_credentials"))
    assert invalid.detail == INVALID_CREDENTIALS == "Invalid username or password"
    unavailable = LoginFailure(503, MessageRef("auth.login.directory_unavailable"))
    assert unavailable.detail == DIRECTORY_UNAVAILABLE


def test_a_dashboard_refusal_keeps_its_ref_and_extra_fields() -> None:
    plain = DashboardError(409, MessageRef("dashboard.owner_limit", {"limit": 20}))
    assert plain.detail == "the limit is 20 dashboards per owner" and plain.fields is None
    stale = DashboardError(
        409, MessageRef("dashboard.changed_since_opened"), {"current_revision": 3}
    )
    assert stale.detail == {
        "message": "this dashboard changed since you opened it",
        "current_revision": 3,
    }


_GOOD = {
    "enabled": True,
    "urls": ["ldaps://dc.example"],
    "start_tls": False,
    "ca_pem": None,
    "bind_template": "{username}@corp.example",
    "timeout_seconds": 5,
    "user_search_base": None,
}


@pytest.mark.parametrize(
    ("change", "codes"),
    [
        ({}, []),
        ({"urls": []}, ["auth.directory.enabled_needs_url"]),
        ({"urls": [], "enabled": False}, []),
        (
            {"urls": [" "]},
            ["auth.directory.url_empty", "auth.directory.enabled_needs_valid_url"],
        ),
        (
            {"urls": ["x" * 513]},
            ["auth.directory.url_too_long", "auth.directory.enabled_needs_valid_url"],
        ),
        (
            {"urls": ["http://dc"]},
            ["auth.directory.url_scheme", "auth.directory.enabled_needs_valid_url"],
        ),
        (
            {"urls": ["ldap://u:p@dc/x?q#f"], "start_tls": True},
            [
                "auth.directory.url_credentials",
                "auth.directory.url_path",
                "auth.directory.url_query",
                "auth.directory.url_fragment",
                "auth.directory.start_tls_needs_ldap",
                "auth.directory.enabled_needs_valid_url",
            ],
        ),
        ({"urls": ["ldap://dc"]}, ["auth.directory.ldap_needs_start_tls"]),
        (
            {"urls": ["ldaps://dc"], "start_tls": True},
            ["auth.directory.start_tls_with_ldaps", "auth.directory.start_tls_needs_ldap"],
        ),
        ({"urls": [f"ldaps://d{i}" for i in range(9)]}, ["auth.directory.urls_max"]),
        ({"bind_template": "cn=user"}, ["auth.directory.template_username"]),
        ({"bind_template": "{username}" + "x" * 512}, ["auth.directory.template_too_long"]),
        ({"timeout_seconds": 0}, ["auth.directory.timeout_range"]),
        ({"timeout_seconds": 31}, ["auth.directory.timeout_range"]),
        ({"ca_pem": "nope"}, ["auth.directory.ca_invalid"]),
        ({"ca_pem": "-----BEGIN PRIVATE KEY-----"}, ["auth.directory.ca_private_key"]),
        ({"ca_pem": "x" * (64 * 1024 + 1)}, ["auth.directory.ca_too_long"]),
        ({"user_search_base": "d" * 1025}, ["auth.directory.base_too_long"]),
        ({"user_search_base": "dc=a\x01"}, ["auth.directory.base_control_character"]),
    ],
)
def test_directory_validation_codes_match_the_english_reasons(
    change: dict[str, object], codes: list[str]
) -> None:
    arguments = {**_GOOD, **change}
    spec, refs = validate_directory_coded(**arguments)  # type: ignore[arg-type]
    assert [ref.code for ref in refs] == codes
    assert (spec is None) == bool(codes)
    plain_spec, plain = validate_directory(**arguments)  # type: ignore[arg-type]
    assert plain == [render(ref) for ref in refs]
    assert (plain_spec is None) == (spec is None)


def test_the_expiry_reasons_carry_the_subject_and_the_instant() -> None:
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])

    def pem(start: datetime, end: datetime) -> str:
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(start)
            .not_valid_after(end)
            .sign(key, hashes.SHA256())
        )
        return cert.public_bytes(serialization.Encoding.PEM).decode()

    now = datetime.now(UTC)
    old = pem(now - timedelta(days=30), now - timedelta(days=1))
    _, refs = validate_directory_coded(**{**_GOOD, "ca_pem": old})  # type: ignore[arg-type]
    assert [ref.code for ref in refs] == ["auth.directory.cert_expired"]
    assert refs[0].params["subject"] == "CN=test-ca"
    assert render(refs[0]).startswith("certificate CN=test-ca is expired (not_after 20")
    future = pem(now + timedelta(days=1), now + timedelta(days=30))
    _, refs = validate_directory_coded(**{**_GOOD, "ca_pem": future})  # type: ignore[arg-type]
    assert [ref.code for ref in refs] == ["auth.directory.cert_not_yet_valid"]


def test_the_dependency_refusal_names_the_rules_and_codes_the_action() -> None:
    import uuid

    rules = [
        svc.DependentRule(id=uuid.uuid4(), name="Rule A"),
        svc.DependentRule(id=uuid.uuid4(), name="Rule B"),
    ]
    refusal = svc.dependency_refusal(rules, svc.ACTION_UNPIN)
    assert isinstance(refusal, CodedHTTPException) and refusal.status_code == 409
    detail = refusal.detail
    assert isinstance(detail, dict)
    assert detail["message"] == (
        "cannot unpin a value: custom rule(s) 'Rule A', 'Rule B' pin a value of this watch; "
        "detach or delete the rule(s) first"
    )
    assert detail["rules"] == [
        {"id": str(rules[0].id), "name": "Rule A"},
        {"id": str(rules[1].id), "name": "Rule B"},
    ]
    assert detail["code"] == "propertywatch.dependency.refused"
    assert detail["params"] == {
        "action": {"code": "propertywatch.action.unpin", "params": {}},
        "names": "'Rule A', 'Rule B'",
    }


def test_an_unknown_action_phrase_stays_uncoded_english() -> None:
    from fastapi import HTTPException

    refused = svc.dependency_refusal([], "do something else")
    raced = svc.dependency_raced("do something else")
    assert type(refused) is HTTPException and type(raced) is HTTPException
    assert refused.detail["message"].startswith("cannot do something else: custom rule(s)")  # type: ignore[index]
    assert raced.detail["rules"] == []  # type: ignore[index]
    assert "code" not in refused.detail  # type: ignore[operator]


# --- the registry cache ---------------------------------------------------------------------


def test_the_module_registry_map_is_built_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real = messages._registry_map

    def counting(registry):  # type: ignore[no-untyped-def]
        calls.append(len(registry))
        return real(registry)

    monkeypatch.setattr(messages, "_registry_map", counting)
    for _ in range(3):
        assert render(MessageRef("auth.session.required")) == "Authentication required."
        assert MessageRef("auth.session.required").to_wire() == {
            "code": "auth.session.required",
            "params": {},
        }
    assert calls == []


def test_an_explicit_registry_is_still_used_not_the_cached_one() -> None:
    other = (
        MessageSpec(
            code="auth.session.required",
            template="other {x}",
            params={"x": ParamSpec(ParamKind.TEXT)},
            sample={"x": "1"},
        ),
    )
    assert render(MessageRef("auth.session.required", {"x": "y"}), registry=other) == "other y"
    assert render(MessageRef("auth.session.required")) == "Authentication required."
