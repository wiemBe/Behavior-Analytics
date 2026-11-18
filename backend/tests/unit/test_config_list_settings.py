"""List-typed settings accept the forms ``docs/CONFIGURATION.md`` shows (FIX-70).

pydantic-settings JSON-decoded these fields, so ``GEO_DIRECTIONS=SOURCE`` -- the
comma form the reference prints -- raised during ``Settings()``. ``migrate``
exited, backend, worker and beat never started, and the product was down. Both
forms must work, and a value that still cannot be read must name the variable and
the accepted form.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_FERNET_KEY = "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A="


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # The exact value the production site set (2026-11-14, 0.1.16).
        ("SOURCE", ["SOURCE"]),
        ("SOURCE, DESTINATION", ["SOURCE", "DESTINATION"]),
        ("SOURCE,DESTINATION", ["SOURCE", "DESTINATION"]),
        (" DESTINATION ", ["DESTINATION"]),
        # The JSON array existing sites already use keeps working.
        ('["SOURCE"]', ["SOURCE"]),
        ('["SOURCE", "DESTINATION"]', ["SOURCE", "DESTINATION"]),
    ],
)
def test_geo_directions_accepts_both_forms(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: list[str]
) -> None:
    monkeypatch.setenv("GEO_DIRECTIONS", value)
    assert Settings().geo_directions == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("67", [67]),
        ("39,40", [39, 40]),
        ("67, 39, 212, 367, 4000", [67, 39, 212, 367, 4000]),
        ("[39, 40]", [39, 40]),
        # A string member in a JSON array is accepted, as the comma form's is.
        ('["39"]', [39]),
    ],
)
def test_catchall_type_ids_accepts_both_forms(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: list[int]
) -> None:
    monkeypatch.setenv("CATCHALL_TYPE_IDS", value)
    assert Settings().catchall_type_ids == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("qid", ["qid"]),
        ("qid, username", ["qid", "username"]),
        ("qid,username,source_ip", ["qid", "username", "source_ip"]),
        ('["qid", "username"]', ["qid", "username"]),
    ],
)
def test_explanation_dimensions_accepts_both_forms(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: list[str]
) -> None:
    monkeypatch.setenv("EXPLANATION_DIMENSIONS", value)
    assert Settings().explanation_dimensions == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/store", ["/store"]),
        ("/, /store, /tmp", ["/", "/store", "/tmp"]),  # noqa: S108 - mount names, not files
        ('["/store", "/tmp"]', ["/store", "/tmp"]),  # noqa: S108 - mount names, not files
    ],
)
def test_qradar_health_disk_mounts_accepts_both_forms(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: list[str]
) -> None:
    monkeypatch.setenv("QRADAR_HEALTH_DISK_MOUNTS", value)
    assert Settings().qradar_health_disk_mounts == expected


@pytest.mark.parametrize(
    ("name", "attribute", "expected"),
    [
        ("GEO_DIRECTIONS", "geo_directions", ["SOURCE"]),
        ("CATCHALL_TYPE_IDS", "catchall_type_ids", [67, 39, 212, 367, 4000]),
        (
            "EXPLANATION_DIMENSIONS",
            "explanation_dimensions",
            [
                "qid",
                "event_name",
                "source_ip",
                "destination_ip",
                "destination_port",
                "source_port",
                "username",
                "action",
                "category",
                "protocol",
            ],
        ),
        (
            "QRADAR_HEALTH_DISK_MOUNTS",
            "qradar_health_disk_mounts",
            [
                "/",
                "/store",
                "/storetmp",
                "/transient",
                "/var/log",
                "/var/log/audit",
                "/opt",
                "/home",
                "/tmp",  # noqa: S108 - a mount name to compare, not a file
                "/var",
                "/boot",
                "/recovery",
            ],
        ),
    ],
)
def test_an_empty_value_means_the_setting_is_unset(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str, expected: object
) -> None:
    """``KEY=`` in ``.env`` clears a name; it must not empty a documented default.

    Three of the four fields refuse an empty list outright (GEO_DIRECTIONS and the
    mounts by ``min_length``, CATCHALL_TYPE_IDS by its own validator), and an empty
    EXPLANATION_DIMENSIONS would silently stop explanations.
    """
    for value in ("", "   "):
        monkeypatch.setenv(name, value)
        assert getattr(Settings(), attribute) == expected


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("GEO_DIRECTIONS", "SOURCE, SOURCEIP", "GEO_DIRECTIONS holds SOURCE and DESTINATION"),
        (
            "CATCHALL_TYPE_IDS",
            "67, abc",
            "CATCHALL_TYPE_IDS holds integers from 1 to 100000",
        ),
        (
            "QRADAR_HEALTH_DISK_MOUNTS",
            "/store, store",
            "QRADAR_HEALTH_DISK_MOUNTS holds absolute paths",
        ),
        ("CATCHALL_TYPE_IDS", "67, 100001", "CATCHALL_TYPE_IDS holds integers from 1 to 100000"),
    ],
)
def test_an_invalid_member_is_refused_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, message: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError) as excinfo:
        Settings()
    assert message in str(excinfo.value)


@pytest.mark.parametrize(
    "name", ["GEO_DIRECTIONS", "CATCHALL_TYPE_IDS", "QRADAR_HEALTH_DISK_MOUNTS"]
)
def test_a_broken_json_array_names_the_variable_and_the_forms(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, '["A",')
    with pytest.raises(ValidationError) as excinfo:
        Settings()
    text = str(excinfo.value)
    assert f"{name} must be a JSON array or a comma-separated list" in text
    assert '["A", "B"] or A, B' in text


def test_explanation_dimensions_has_no_member_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unmapped dimension is recorded UNAVAILABLE per source, never a start-up refusal.

    Refusing it here would be a new outage risk for a setting that starts today,
    so the split does not judge members: the provider reports an unmapped
    dimension (`dimension is not mapped to a QRadar field`) and keeps the others.
    """
    monkeypatch.setenv("EXPLANATION_DIMENSIONS", "qid, made_up_dimension")
    assert Settings().explanation_dimensions == ["qid", "made_up_dimension"]


def test_settings_loads_the_comma_form_from_an_env_file_as_containers_do(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ``migrate`` container reads ``.env`` through the env-file source, not kwargs.

    This is the exact path that failed on the production site: ``alembic upgrade
    head`` builds ``Settings()`` and the value came from the environment file.
    """
    for name in ("GEO_DIRECTIONS", "CATCHALL_TYPE_IDS"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GEO_DIRECTIONS=SOURCE\nCATCHALL_TYPE_IDS=39, 40\n", encoding="utf-8"
    )

    settings = Settings(_env_file=env_file)

    assert settings.geo_directions == ["SOURCE"]
    assert settings.catchall_type_ids == [39, 40]


def test_settings_loads_the_json_form_from_an_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ``.env`` written in the JSON form an existing site already has keeps working."""
    for name in ("GEO_DIRECTIONS", "CATCHALL_TYPE_IDS", "QRADAR_HEALTH_DISK_MOUNTS"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        'GEO_DIRECTIONS=["SOURCE"]\n'
        'CATCHALL_TYPE_IDS=[39, 40]\n'
        'QRADAR_HEALTH_DISK_MOUNTS=["/store", "/tmp"]\n',
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.geo_directions == ["SOURCE"]
    assert settings.catchall_type_ids == [39, 40]
    assert settings.qradar_health_disk_mounts == ["/store", "/tmp"]  # noqa: S108 - mount names


def test_a_refused_setting_never_echoes_a_secret_value() -> None:
    """The start-up error names the variable; a token must not appear in it."""
    token = "do-not-print-this-sec-token"
    with pytest.raises(ValidationError) as excinfo:
        Settings(
            environment="production",
            secret_key="x" * 40,
            encryption_key=_FERNET_KEY,
            qradar_provider="rest",
            qradar_sec_token=token,
            qradar_host="https://qradar.example",
            # Not an https origin, so the hardening validator refuses the settings.
            public_origin="http://localhost:3000",
        )
    text = str(excinfo.value)
    assert "PUBLIC_ORIGIN must be an https origin" in text
    assert token not in text


def test_an_unusable_encryption_key_names_the_setting_not_the_value() -> None:
    value = "not-a-fernet-key"
    with pytest.raises(ValidationError) as excinfo:
        Settings(
            environment="production",
            secret_key="x" * 40,
            encryption_key=value,
            qradar_provider="rest",
            qradar_sec_token="a-real-token",
            qradar_host="https://qradar.example",
            public_origin="https://soc.example",
        )
    text = str(excinfo.value)
    assert "ENCRYPTION_KEY is not a Fernet key" in text
    assert value not in text
