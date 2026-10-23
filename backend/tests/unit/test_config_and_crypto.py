"""Production hardening guards and encrypted-column round-trip."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import (
    EXAMPLE_PLACEHOLDERS,
    SETTINGS_ENV_ALLOWLIST,
    Environment,
    ProviderKind,
    Settings,
    unknown_setting_names,
)


def _prod(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
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
    base.update(overrides)
    return base


def test_valid_production_config_boots() -> None:
    Settings(**_prod())  # must not raise


def test_production_rejects_debug() -> None:
    with pytest.raises(ValueError, match="DEBUG"):
        Settings(**_prod(debug=True))


def test_production_rejects_mock_provider() -> None:
    with pytest.raises(ValueError, match="mock"):
        Settings(**_prod(qradar_provider=ProviderKind.MOCK))


def test_production_rejects_disabled_tls() -> None:
    with pytest.raises(ValueError, match="VERIFY_SSL"):
        Settings(**_prod(qradar_verify_ssl=False))


def test_production_rejects_missing_rest_token() -> None:
    with pytest.raises(ValueError, match="QRADAR_SEC_TOKEN"):
        Settings(**_prod(qradar_sec_token=""))


def test_production_rejects_autonomous_llm_actions() -> None:
    with pytest.raises(ValueError, match="AUTONOMOUS"):
        Settings(**_prod(llm_allow_autonomous_actions=True))


def test_plaintext_qradar_host_rejected_any_env() -> None:
    with pytest.raises(ValueError, match="https"):
        Settings(qradar_host="http://qradar.example", encryption_key="x" * 44)


def test_ariel_timeout_ordering_enforced() -> None:
    with pytest.raises(ValueError, match="MAX_TIMEOUT"):
        Settings(
            ariel_default_timeout_seconds=600,
            ariel_max_timeout_seconds=300,
            encryption_key="x" * 44,
        )


def test_encrypted_column_round_trips() -> None:
    """The EncryptedString type must encrypt on bind and decrypt on result."""
    from app.models.base import EncryptedString

    col = EncryptedString()
    ciphertext = col.process_bind_param("super-secret-token", dialect=None)
    assert ciphertext is not None
    assert "super-secret-token" not in ciphertext  # actually encrypted
    assert col.process_result_value(ciphertext, dialect=None) == "super-secret-token"


def test_encrypted_column_handles_none() -> None:
    from app.models.base import EncryptedString

    col = EncryptedString()
    assert col.process_bind_param(None, dialect=None) is None
    assert col.process_result_value(None, dialect=None) is None


_FERNET = "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A="
_HARDENED = (Environment.STAGING, Environment.PRODUCTION)
_REFUSALS: tuple[tuple[dict[str, object], str], ...] = (
    ({"debug": True}, "DEBUG"),
    ({"qradar_provider": ProviderKind.MOCK}, "mock"),
    ({"qradar_verify_ssl": False}, "VERIFY_SSL"),
    ({"qradar_sec_token": ""}, "QRADAR_SEC_TOKEN"),
    ({"llm_allow_autonomous_actions": True}, "AUTONOMOUS"),
    ({"auth_provider": "oidc"}, "OIDC is out of scope"),
    ({"public_origin": "http://soc.example"}, "PUBLIC_ORIGIN"),
    ({"public_origin": "https://soc.example/app"}, "PUBLIC_ORIGIN"),
    ({"secret_key": "short-secret"}, "at least 32"),
    ({"encryption_key": ""}, "required to encrypt"),
)
_ENV_GET = re.compile(r"""os\.environ\.get\(\s*["']([A-Z0-9_]+)["']""")
_ENV_ITEM = re.compile(r"""os\.environ\[\s*["']([A-Z0-9_]+)["']""")


def test_valid_staging_config_boots() -> None:
    settings = Settings(**_prod(environment=Environment.STAGING))
    assert settings.environment is Environment.STAGING
    assert settings.is_hardened is True
    assert settings.is_production is False


@pytest.mark.parametrize("environment", _HARDENED)
@pytest.mark.parametrize(("overrides", "match"), _REFUSALS)
def test_hardened_environment_refuses_the_production_posture(
    environment: Environment, overrides: dict[str, object], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        Settings(**_prod(environment=environment, **overrides))


def test_development_still_boots_with_the_dev_posture() -> None:
    settings = Settings(
        environment=Environment.DEVELOPMENT,
        debug=True,
        qradar_verify_ssl=False,
        qradar_provider=ProviderKind.MOCK,
        qradar_sec_token="",
        llm_allow_autonomous_actions=True,
        auth_provider="local",
        public_origin="http://localhost:3000",
        secret_key=EXAMPLE_PLACEHOLDERS["SECRET_KEY"],
        encryption_key="x" * 44,
        encryption_keys_json="not-json",
    )
    assert settings.is_hardened is False
    assert settings.session_cookies_enforced() is False


@pytest.mark.parametrize("environment", _HARDENED)
def test_placeholders_refused_outside_development(environment: Environment) -> None:
    secret = EXAMPLE_PLACEHOLDERS["SECRET_KEY"]
    with pytest.raises(ValueError, match="placeholder") as secret_error:
        Settings(**_prod(environment=environment, secret_key=secret))
    assert secret not in str(secret_error.value)

    encryption = EXAMPLE_PLACEHOLDERS["ENCRYPTION_KEY"]
    with pytest.raises(ValueError, match="placeholder") as encryption_error:
        Settings(**_prod(environment=environment, encryption_key=encryption))
    assert encryption not in str(encryption_error.value)


def test_placeholders_accepted_in_development() -> None:
    settings = Settings(
        environment=Environment.DEVELOPMENT,
        secret_key=EXAMPLE_PLACEHOLDERS["SECRET_KEY"],
        encryption_key=EXAMPLE_PLACEHOLDERS["ENCRYPTION_KEY"],
    )
    assert settings.is_hardened is False


@pytest.mark.parametrize("environment", _HARDENED)
def test_non_fernet_encryption_key_refused(environment: Environment) -> None:
    rejected = "x" * 44
    with pytest.raises(ValueError, match="not a Fernet key") as caught:
        Settings(**_prod(environment=environment, encryption_key=rejected))
    assert rejected not in str(caught.value)


def test_valid_encryption_keys_json_boots() -> None:
    Settings(**_prod(encryption_keys_json=json.dumps({"1": _FERNET, "2": _FERNET})))


@pytest.mark.parametrize("environment", _HARDENED)
def test_bad_encryption_keys_json_entry_refused(environment: Environment) -> None:
    planted = "planted-key-value-should-not-appear"
    with pytest.raises(ValueError, match="Fernet cannot construct") as caught:
        Settings(
            **_prod(
                environment=environment,
                encryption_keys_json=json.dumps({"1": planted, "2": _FERNET}),
            )
        )
    assert planted not in str(caught.value)
    assert "version 1" in str(caught.value)


def test_encryption_keys_json_non_integer_version_is_named() -> None:
    with pytest.raises(ValueError, match="not an integer") as caught:
        Settings(**_prod(encryption_keys_json=json.dumps({"next": _FERNET})))
    assert _FERNET not in str(caught.value)


def test_encryption_keys_json_must_be_an_object() -> None:
    with pytest.raises(ValueError, match="must be a JSON object"):
        Settings(**_prod(encryption_keys_json="[1]"))


def test_encryption_keys_json_invalid_does_not_quote_the_document() -> None:
    planted = "planted-secret-fragment"
    with pytest.raises(ValueError, match="not valid JSON") as caught:
        Settings(**_prod(encryption_keys_json="{" + planted))
    text = str(caught.value)
    assert planted not in text
    assert "Expecting" not in text


def test_placeholder_constant_matches_env_example() -> None:
    example = Path(__file__).resolve().parents[3] / ".env.example"
    text = example.read_text(encoding="utf-8")
    for name, value in EXAMPLE_PLACEHOLDERS.items():
        assert f"{name}={value}" in text


_INSTALLER_CASE = re.compile(
    r'installer_env_key\(\) \{\s*case "\$1" in(?P<body>.*?)\*\) return 1 ;;',
    re.S,
)
_COMPOSE_INTERPOLATION = re.compile(r"\$\{([A-Z][A-Z0-9_]*)")


def deployment_env_names() -> set[str]:
    """Names ``install`` owns and names the compose files interpolate.

    Parsed from ``qradar-dash`` and the two compose files. Not a second copy of the list.
    """
    root = Path(__file__).resolve().parents[3]
    script = (root / "qradar-dash").read_text(encoding="utf-8")
    match = _INSTALLER_CASE.search(script)
    assert match is not None, "installer_env_key case list was not found"
    installer = set(re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", match.group("body")))
    assert len(installer) >= 40
    compose: set[str] = set()
    for filename in ("docker-compose.yml", "docker-compose.prod.yml"):
        found = set(_COMPOSE_INTERPOLATION.findall((root / filename).read_text(encoding="utf-8")))
        assert found, filename
        compose |= found
    # The compose files name at least one variable install does not write.
    assert compose - installer
    return installer | compose


def test_installer_and_compose_names_are_not_unknown_settings() -> None:
    names = deployment_env_names()
    reported = unknown_setting_names(dict.fromkeys(names, "present"))
    assert reported == ()


@pytest.mark.parametrize("environment", _HARDENED)
def test_hardened_refusal_names_the_environment(environment: Environment) -> None:
    with pytest.raises(ValueError) as caught:
        Settings(**_prod(environment=environment, debug=True))
    text = str(caught.value)
    header = f"Unsafe configuration for ENVIRONMENT={environment.value}:"
    assert header in text
    assert "Unsafe production configuration" not in text


def test_unknown_setting_names_on_a_crafted_environment() -> None:
    planted = "https://hooks.example/planted-webhook-secret"
    names = unknown_setting_names(
        {
            "CATCHALL_COLLECTION_ENABLE": "false",
            "catchall_collection_enable": "false",
            "CATCHALL_COLLECTION_ENABLED": "true",
            "QRADAR_BASE_URL": "https://qradar.example",
            "QRADAR_HOST": "https://qradar.example",
            "NOTIFY_TEAMS_WEBHOOK_URL": planted,
            "SMTP_HOST": "mail.example",
            "GIT_SHA": "abc123",
            "PATH": "/usr/bin",
            "HOSTNAME": "box",
            "NOTIFY_PER_CHANNEL_RAT": "9",
        }
    )
    assert names == ("CATCHALL_COLLECTION_ENABLE", "NOTIFY_PER_CHANNEL_RAT")
    assert planted not in names
    assert any(name.startswith("NOTIFY_") for name in SETTINGS_ENV_ALLOWLIST)
    assert any(name.startswith("SMTP_") for name in SETTINGS_ENV_ALLOWLIST)
    assert "GIT_SHA" in SETTINGS_ENV_ALLOWLIST


def test_settings_env_allowlist_matches_os_environ_reads() -> None:
    app = Path(__file__).resolve().parents[2] / "app"
    found: set[str] = set()
    for path in sorted(app.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        found.update(_ENV_GET.findall(text))
        found.update(_ENV_ITEM.findall(text))
    assert found == set(SETTINGS_ENV_ALLOWLIST)


def test_startup_logs_unknown_setting_names_and_not_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Lifespan logs the typo'd name. ``configure_logging`` replaces the root handlers."""
    from app.main import create_app

    planted = "planted-value-9f3a"
    monkeypatch.setenv("CATCHALL_COLLECTION_ENABLE", planted)
    root = logging.getLogger()
    access = logging.getLogger("uvicorn.access")
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_access_handlers = list(access.handlers)
    saved_access_level = access.level
    try:
        with TestClient(create_app()) as client:
            assert client.get("/api/v1/health/live").status_code == 200
        captured = capsys.readouterr().out
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        access.handlers[:] = saved_access_handlers
        access.setLevel(saved_access_level)
    assert "ignoring unknown settings:" in captured
    assert "CATCHALL_COLLECTION_ENABLE" in captured
    assert planted not in captured
