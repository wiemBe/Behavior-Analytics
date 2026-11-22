"""A malformed non-critical setting degrades one value, not the process (OPS-FLAGS S1b).

Layer 2 of design §3.1: the four `NoDecode` list fields and the runtime switches are
degraded, everything else still refuses startup. Every test clears the cached settings
before and after, and every environment change goes through ``monkeypatch``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

import app.main as main_module
from app.cli import doctor as doctor_module
from app.cli.doctor import CheckResult, Status
from app.core import config as config_module
from app.core.config import (
    INVALID_LIST_VALUE,
    INVALID_SWITCH_VALUE,
    Settings,
    SettingsProblem,
    get_settings,
    get_settings_problems,
    load_settings,
)
from app.models.feature_switch import FEATURE_KEYS

#: The backend directory, derived from this file so the test never hard-codes a path.
BACKEND_DIR = Path(__file__).resolve().parents[2]

_LIST_FIELDS = (
    "explanation_dimensions",
    "catchall_type_ids",
    "geo_directions",
    "qradar_health_disk_mounts",
)

#: Garbage form for every list field, plus a wrong item for each field whose item
#: validator rejects one. ``EXPLANATION_DIMENSIONS`` has no item validator, so its
#: malformed form is a broken JSON array instead.
_BAD_LIST_CASES = (
    ("explanation_dimensions", "EXPLANATION_DIMENSIONS", '["A",'),
    ("catchall_type_ids", "CATCHALL_TYPE_IDS", "1;2"),
    ("catchall_type_ids", "CATCHALL_TYPE_IDS", "abc"),
    ("geo_directions", "GEO_DIRECTIONS", "1;2"),
    ("geo_directions", "GEO_DIRECTIONS", "NORTH"),
    ("qradar_health_disk_mounts", "QRADAR_HEALTH_DISK_MOUNTS", "1;2"),
    ("qradar_health_disk_mounts", "QRADAR_HEALTH_DISK_MOUNTS", "relative"),
)


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Iterator[None]:
    """Never let a Settings built from a test environment survive into another test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _default(field: str) -> Any:
    return Settings.model_fields[field].get_default(call_default_factory=True)


def test_allow_list_matches_the_feature_keys_and_the_four_lists() -> None:
    """The allow-list cannot drift from the model or the four layer-1 fields."""
    assert frozenset(FEATURE_KEYS) == config_module._NONCRITICAL_SWITCHES
    assert frozenset(_LIST_FIELDS) == config_module._NONCRITICAL_LISTS


@pytest.mark.parametrize(("field", "env_name", "bad_value"), _BAD_LIST_CASES)
def test_bad_list_value_uses_the_default_and_records_one_problem(
    field: str, env_name: str, bad_value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(env_name, bad_value)
    settings = get_settings()
    assert getattr(settings, field) == _default(field)
    assert get_settings_problems() == (
        SettingsProblem(field, env_name, INVALID_LIST_VALUE),
    )


@pytest.mark.parametrize("field", FEATURE_KEYS)
def test_bad_switch_value_turns_the_switch_off(
    field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every feature switch, default-on ones included, degrades to off."""
    monkeypatch.setenv(field.upper(), "maybe")
    settings = get_settings()
    assert getattr(settings, field) is False
    assert get_settings_problems() == (
        SettingsProblem(field, field.upper(), INVALID_SWITCH_VALUE),
    )


def test_bad_default_on_switch_is_off_with_default_on_proven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A switch that defaults to on is not kept on when its env value is malformed."""
    assert Settings.model_fields["waf_collection_enabled"].default is True
    monkeypatch.setenv("WAF_COLLECTION_ENABLED", "maybe")
    assert get_settings().waf_collection_enabled is False


def test_bad_switch_through_the_transposed_alias_is_mapped_to_the_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A validation alias in the error loc maps back to the Settings field name."""
    monkeypatch.setenv("WAF_COLLECTION_OUTCOME_ENABLED", "maybe")
    assert get_settings().waf_outcome_collection_enabled is False
    assert get_settings_problems() == (
        SettingsProblem(
            "waf_outcome_collection_enabled",
            "WAF_OUTCOME_COLLECTION_ENABLED",
            INVALID_SWITCH_VALUE,
        ),
    )


def test_several_noncritical_errors_are_all_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEO_DIRECTIONS", "1;2")
    monkeypatch.setenv("WAF_COLLECTION_ENABLED", "maybe")
    settings = get_settings()
    assert settings.geo_directions == _default("geo_directions")
    assert settings.waf_collection_enabled is False
    assert get_settings_problems() == (
        SettingsProblem("geo_directions", "GEO_DIRECTIONS", INVALID_LIST_VALUE),
        SettingsProblem("waf_collection_enabled", "WAF_COLLECTION_ENABLED", INVALID_SWITCH_VALUE),
    )


def test_critical_field_error_still_raises_the_same_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bound violation outside the allow-list is untouched: type and text are identical."""
    monkeypatch.setenv("ANOMALY_MAX_BUCKETS_PER_EVALUATION", "0")
    with pytest.raises(ValidationError) as degraded:
        get_settings()
    with pytest.raises(ValidationError) as direct:
        Settings()
    assert type(degraded.value) is type(direct.value)
    assert str(degraded.value) == str(direct.value)
    with pytest.raises(ValidationError):
        get_settings_problems()


def test_model_validator_error_with_no_field_still_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error with an empty loc is critical: a model validator is never degraded."""
    monkeypatch.setenv("QRADAR_HEALTH_DISK_WARN_PERCENT", "99")
    monkeypatch.setenv("QRADAR_HEALTH_DISK_FAIL_PERCENT", "98")
    with pytest.raises(ValidationError) as degraded:
        get_settings()
    assert any(not error["loc"] for error in degraded.value.errors())
    with pytest.raises(ValidationError) as direct:
        Settings()
    assert str(degraded.value) == str(direct.value)


def test_noncritical_and_critical_errors_together_still_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEO_DIRECTIONS", "1;2")
    monkeypatch.setenv("ANOMALY_MAX_BUCKETS_PER_EVALUATION", "0")
    with pytest.raises(ValidationError) as degraded:
        get_settings()
    with pytest.raises(ValidationError) as direct:
        Settings()
    assert str(degraded.value) == str(direct.value)


def test_valid_environment_has_no_problems_and_load_is_at_parity() -> None:
    assert get_settings_problems() == ()
    assert load_settings() == Settings()


def test_raw_value_never_reaches_problems_log_or_doctor(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    list_marker = "PLANTED_GEO_MARKER_9f3a2b"
    switch_marker = "PLANTED_SWITCH_MARKER_7c1d4e"
    monkeypatch.setenv("GEO_DIRECTIONS", list_marker)
    monkeypatch.setenv("WAF_COLLECTION_ENABLED", switch_marker)

    problems = get_settings_problems()
    assert problems
    assert list_marker not in repr(problems)
    assert switch_marker not in repr(problems)

    monkeypatch.setattr(main_module, "configure_logging", lambda _level: None)

    async def enter_lifespan() -> None:
        async with main_module.lifespan(main_module.app):
            pass

    with caplog.at_level(logging.WARNING, logger="app"):
        asyncio.run(enter_lifespan())
    warnings = [
        rec.getMessage()
        for rec in caplog.records
        if "malformed non-critical setting ignored" in rec.getMessage()
    ]
    assert len(warnings) == 2
    assert list_marker not in caplog.text
    assert switch_marker not in caplog.text
    assert any("env=GEO_DIRECTIONS" in line and "reason=invalid_list_value" in line
               for line in warnings)
    assert any("env=WAF_COLLECTION_ENABLED" in line and "using switch off" in line
               for line in warnings)

    doctor = doctor_module.Doctor(sessionmaker=cast(Any, lambda: None))
    result = asyncio.run(doctor.check_settings_problems())
    assert result.status is Status.WARN
    assert list_marker not in result.message
    assert switch_marker not in result.message
    rendered = doctor_module.render_text([result]) + doctor_module.render_json([result])
    assert list_marker not in rendered
    assert switch_marker not in rendered


def test_list_fix_line_names_the_accepted_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEO_DIRECTIONS", "1;2")
    result = asyncio.run(
        doctor_module.Doctor(sessionmaker=cast(Any, lambda: None)).check_settings_problems()
    )
    assert result.message == (
        "GEO_DIRECTIONS: not a list. Use SOURCE,DESTINATION or a JSON array. "
        "Value ignored, default used."
    )
    assert result.runbook == "OPS-R62"


def test_doctor_passes_when_no_setting_is_malformed() -> None:
    result = asyncio.run(
        doctor_module.Doctor(sessionmaker=cast(Any, lambda: None)).check_settings_problems()
    )
    assert result == CheckResult(
        "settings_problems", Status.PASS, "no malformed non-critical settings"
    )


def test_doctor_main_warns_on_a_bad_switch_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The operator's command exits 1 (WARN) and prints the fixed text, not the value."""
    marker = "PLANTED_DOCTOR_MARKER_2b8e6f"
    monkeypatch.setenv("WAF_COLLECTION_ENABLED", marker)
    doctor = doctor_module.Doctor(sessionmaker=cast(Any, lambda: None))
    for check_id, _runbook in doctor_module._CHECK_RUNBOOKS:
        if check_id != "settings_problems":
            monkeypatch.setattr(
                doctor,
                f"check_{check_id}",
                AsyncMock(return_value=CheckResult(check_id, Status.PASS, "healthy")),
            )
    monkeypatch.setattr(doctor_module, "Doctor", lambda **_kwargs: doctor)
    monkeypatch.setattr(doctor_module, "dispose_engine", _dispose_nothing)
    monkeypatch.setattr(sys, "argv", ["doctor"])

    assert doctor_module.main() == 1
    printed = capsys.readouterr().out
    assert "WARN  settings_problems" in printed
    assert "WAF_COLLECTION_ENABLED: not true/false. Value ignored, switch off." in printed
    assert marker not in printed


async def _dispose_nothing() -> None:
    """The doctor's engine disposal: this unit test holds no engine."""


@pytest.mark.parametrize("module", ["app.main", "app.workers.celery_app"])
def test_processes_start_with_a_bad_list_value(module: str) -> None:
    """The FastAPI app and the Celery app import on a site with a malformed .env line."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND_DIR)
    env["ENCRYPTION_KEY"] = "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A="
    env["GEO_DIRECTIONS"] = "1;2"
    # `module` is one of the two literals in the decorator, never test input.
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", f"import {module}; print('IMPORTED')"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    assert "IMPORTED" in result.stdout
