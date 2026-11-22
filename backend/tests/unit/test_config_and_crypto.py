"""Production hardening guards and encrypted-column round-trip."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import (
    EXAMPLE_PLACEHOLDERS,
    SETTINGS_ENV_ALLOWLIST,
    WAF_COLLECTION_ALIASES,
    Environment,
    ProviderKind,
    Settings,
    _known_setting_env_names,
    unknown_setting_canonical_hints,
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
    # MAIL-1: the SMTP_* names are Settings fields now, read through Settings and no
    # longer by os.environ, so they are known names and not on the allowlist.
    assert not any(name.startswith("SMTP_") for name in SETTINGS_ENV_ALLOWLIST)
    assert {"SMTP_HOST", "SMTP_SECURITY", "SMTP_PASSWORD"} <= _known_setting_env_names()
    assert "GIT_SHA" in SETTINGS_ENV_ALLOWLIST


_WAF_ALIAS_PAIRS = tuple(WAF_COLLECTION_ALIASES.items())


@pytest.mark.parametrize(("canonical", "alias"), _WAF_ALIAS_PAIRS)
def test_a_waf_breakdown_alias_is_accepted_and_counts_as_an_explicit_value(
    clean_settings_env, monkeypatch, canonical, alias
) -> None:
    """FIX-71i: the transposed name is not ignored and marks the field as supplied."""
    field = canonical.lower()
    monkeypatch.delenv(alias, raising=False)
    monkeypatch.setenv(alias, "false")
    settings = Settings(_env_file=None)
    assert getattr(settings, field) is False
    assert field in settings.model_fields_set

    # Both names with the same value are accepted, still with canonical provenance.
    monkeypatch.setenv(canonical, "false")
    agreeing = Settings(_env_file=None)
    assert getattr(agreeing, field) is False
    assert field in agreeing.model_fields_set


@pytest.mark.parametrize(("canonical", "alias"), _WAF_ALIAS_PAIRS)
def test_contradictory_waf_breakdown_names_refuse_startup(
    clean_settings_env, monkeypatch, canonical, alias
) -> None:
    """FIX-71i: never silently pick one of two different operator values."""
    monkeypatch.setenv(canonical, "true")
    monkeypatch.setenv(alias, "false")
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)
    text = str(caught.value)
    assert canonical in text
    assert alias in text
    assert "different values" in text


@pytest.mark.parametrize(("canonical", "alias"), _WAF_ALIAS_PAIRS)
def test_a_dotenv_file_with_contradictory_waf_names_refuses_startup(
    clean_settings_env, monkeypatch, tmp_path, canonical, alias
) -> None:
    """FIX-71c r1: the env file is a source too, and is compared, not resolved.

    A bare-metal `.env` holding both spellings used to pass silently: pydantic took
    the canonical value and the alias never reached ``os.environ``. The file the
    Settings class actually loads is now part of the comparison.
    """
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.delenv(alias, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"{canonical}=false\n{alias}=true\n", encoding="utf-8")
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=env_file)
    text = str(caught.value)
    assert canonical in text
    assert alias in text
    assert "different values" in text

    # The same file with one value for both names is an ordinary configuration.
    env_file.write_text(f"{canonical}=false\n{alias}=false\n", encoding="utf-8")
    settings = Settings(_env_file=env_file)
    field = canonical.lower()
    assert getattr(settings, field) is False
    assert field in settings.model_fields_set


@pytest.mark.parametrize(("canonical", "alias"), _WAF_ALIAS_PAIRS)
def test_env_beats_dotenv_file_for_waf_breakdown_alias(
    clean_settings_env, monkeypatch, tmp_path, canonical, alias
) -> None:
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.delenv(alias, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"{canonical}=true\n{alias}=false\n", encoding="utf-8")
    monkeypatch.setenv(alias, "true")
    settings = Settings(_env_file=env_file)
    field = canonical.lower()
    assert getattr(settings, field) is True
    assert field in settings.model_fields_set


@pytest.mark.parametrize(("canonical", "alias"), _WAF_ALIAS_PAIRS)
def test_settings_validation_error_hides_secret_marker_in_env(
    clean_settings_env, monkeypatch, canonical, alias
) -> None:
    marker = "ZZZMARKER"
    monkeypatch.setenv("SECRET_KEY", marker)
    monkeypatch.setenv(canonical, "true")
    monkeypatch.setenv(alias, "false")
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)
    text = str(caught.value)
    assert canonical in text
    assert alias in text
    assert marker not in text


@pytest.mark.parametrize(("canonical", "alias"), _WAF_ALIAS_PAIRS)
def test_settings_validation_error_hides_secret_marker_in_dotenv_file(
    clean_settings_env, monkeypatch, tmp_path, canonical, alias
) -> None:
    marker = "ZZZMARKER"
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.delenv(alias, raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"SECRET_KEY={marker}\n{canonical}=false\n{alias}=true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=env_file)
    text = str(caught.value)
    assert canonical in text
    assert alias in text
    assert marker not in text


def test_settings_customise_sources_raises_on_non_dotenv_source() -> None:
    """FIX-71c r2: a non-DotEnvSettingsSource must fail loudly, not skip."""

    class StubSource:
        pass

    with pytest.raises(RuntimeError, match="pydantic-settings"):
        Settings.settings_customise_sources(Settings, None, None, StubSource(), None)  # type: ignore[arg-type]


def test_settings_customise_sources_raises_when_reader_missing() -> None:
    """FIX-71c r2: a DotEnvSettingsSource without _read_env_files must fail loudly."""
    from pydantic_settings import DotEnvSettingsSource

    source = DotEnvSettingsSource(Settings)
    source._read_env_files = None  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="pydantic-settings"):
        Settings.settings_customise_sources(Settings, None, None, source, None)  # type: ignore[arg-type]


def test_unknown_setting_hints_name_the_canonical_for_a_transposition() -> None:
    """FIX-71i: a near-miss is told the canonical spelling, not an alias or a typo."""
    hints = unknown_setting_canonical_hints(
        {
            "WAF_OUTCOME_ENABLED_COLLECTION": "x",
            "WAF_CLIENT_ENABLED_COLLECTION": "x",
            # A plain truncation and an extra segment are not near-misses here.
            "CATCHALL_COLLECTION_ENABLE": "x",
            "WAF_CLIENT_COLLECTION_ENABLED_TYPO": "x",
        }
    )
    assert hints == {
        "WAF_CLIENT_ENABLED_COLLECTION": "WAF_CLIENT_COLLECTION_ENABLED",
        "WAF_OUTCOME_ENABLED_COLLECTION": "WAF_OUTCOME_COLLECTION_ENABLED",
    }


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
    httpx_logger = logging.getLogger("httpx")
    httpcore_logger = logging.getLogger("httpcore")
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_filters = list(root.filters)
    saved_access_handlers = list(access.handlers)
    saved_access_level = access.level
    saved_httpx_level = httpx_logger.level
    saved_httpcore_level = httpcore_logger.level
    try:
        with TestClient(create_app()) as client:
            assert client.get("/api/v1/health/live").status_code == 200
        captured = capsys.readouterr().out
    finally:
        root.handlers[:] = saved_handlers
        root.filters[:] = saved_filters
        root.setLevel(saved_level)
        access.handlers[:] = saved_access_handlers
        access.setLevel(saved_access_level)
        httpx_logger.setLevel(saved_httpx_level)
        httpcore_logger.setLevel(saved_httpcore_level)
    assert "ignoring unknown settings:" in captured
    assert "CATCHALL_COLLECTION_ENABLE" in captured
    assert planted not in captured


def test_startup_log_names_the_canonical_for_a_near_miss(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """FIX-71i: the startup warning suggests the canonical name, never the value."""
    from app.main import create_app

    planted = "planted-near-miss-value-7c1e"
    monkeypatch.setenv("WAF_OUTCOME_ENABLED_COLLECTION", planted)
    root = logging.getLogger()
    access = logging.getLogger("uvicorn.access")
    httpx_logger = logging.getLogger("httpx")
    httpcore_logger = logging.getLogger("httpcore")
    saved = (
        list(root.handlers), root.level, list(root.filters),
        list(access.handlers), access.level, httpx_logger.level, httpcore_logger.level,
    )
    try:
        with TestClient(create_app()) as client:
            assert client.get("/api/v1/health/live").status_code == 200
        captured = capsys.readouterr().out
    finally:
        root.handlers[:] = saved[0]
        root.filters[:] = saved[2]
        root.setLevel(saved[1])
        access.handlers[:] = saved[3]
        access.setLevel(saved[4])
        httpx_logger.setLevel(saved[5])
        httpcore_logger.setLevel(saved[6])
    assert "ignoring unknown settings:" in captured
    assert "WAF_OUTCOME_ENABLED_COLLECTION" in captured
    assert "WAF_OUTCOME_COLLECTION_ENABLED" in captured
    assert planted not in captured


_FAMILY_DEFAULTS = {
    "waf_collection_enabled": True,
    "waf_outcome_collection_enabled": True,
    "waf_client_collection_enabled": True,
    "waf_alerting_enabled": True,
    "ips_collection_enabled": True,
    "ips_alerting_enabled": True,
    "identity_collection_enabled": True,
    "identity_alerting_enabled": True,
    "rule_match_collection_enabled": True,
    "rule_alerting_enabled": True,
    "pw_collection_enabled": True,
    "pw_alerting_enabled": True,
    "custom_rules_enabled": True,
    "custom_rules_alerting_enabled": True,
}


@pytest.fixture
def clean_settings_env(monkeypatch):
    for name in list(os.environ):
        if name.lower() in Settings.model_fields:
            monkeypatch.delenv(name)


@pytest.mark.parametrize(("name", "expected"), _FAMILY_DEFAULTS.items())
def test_family_code_defaults(clean_settings_env, name, expected):
    settings = Settings(_env_file=None)
    assert getattr(settings, name) is expected
    assert name not in settings.model_fields_set


def test_geography_code_defaults(clean_settings_env):
    settings = Settings(_env_file=None)
    assert settings.geo_country_source == "lookup"
    assert settings.geo_directions == ["SOURCE"]


@pytest.mark.parametrize(
    ("collection", "alerting"),
    [
        ("waf_collection_enabled", "waf_alerting_enabled"),
        ("ips_collection_enabled", "ips_alerting_enabled"),
        ("identity_collection_enabled", "identity_alerting_enabled"),
        ("rule_match_collection_enabled", "rule_alerting_enabled"),
        ("pw_collection_enabled", "pw_alerting_enabled"),
        ("custom_rules_enabled", "custom_rules_alerting_enabled"),
    ],
)
def test_collection_opt_out_turns_defaulted_alerting_off(
    clean_settings_env, monkeypatch, collection, alerting
):
    monkeypatch.setenv(collection.upper(), "false")
    settings = Settings(_env_file=None)
    assert getattr(settings, collection) is False
    assert getattr(settings, alerting) is False
    assert alerting not in settings.model_fields_set
    # This base accepts explicit alerting values independently of collection.
    for value in (True, False):
        monkeypatch.setenv(alerting.upper(), str(value))
        explicit = Settings(_env_file=None)
        assert getattr(explicit, alerting) is value
        assert alerting in explicit.model_fields_set
