"""The /me contract: the interface-language field and its request body."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.me import MeOut, PreferencesIn


def _me(**overrides: object) -> MeOut:
    """A MeOut with the required fields, plus whatever the test overrides."""
    fields: dict[str, object] = {
        "subject": "ada",
        "email": None,
        "permissions": [],
        "effective_permissions": [],
    }
    fields.update(overrides)
    return MeOut(**fields)  # type: ignore[arg-type]


def test_ui_language_defaults_to_english() -> None:
    """A caller built without the field (a development principal) is English."""
    assert _me().ui_language == "en"


def test_turkish_round_trips_through_me_out() -> None:
    assert _me(ui_language="tr").ui_language == "tr"


@pytest.mark.parametrize("value", ["de", "TR", "en-US", ""])
def test_me_out_refuses_anything_beyond_the_two_languages(value: str) -> None:
    with pytest.raises(ValidationError):
        _me(ui_language=value)


@pytest.mark.parametrize("value", ["en", "tr"])
def test_preferences_body_accepts_the_two_languages(value: str) -> None:
    body = PreferencesIn.model_validate({"ui_language": value})
    assert body.ui_language == value


@pytest.mark.parametrize("value", ["de", "TR", "english", ""])
def test_preferences_body_refuses_an_unknown_language(value: str) -> None:
    with pytest.raises(ValidationError):
        PreferencesIn.model_validate({"ui_language": value})


def test_preferences_body_refuses_an_extra_key() -> None:
    with pytest.raises(ValidationError):
        PreferencesIn.model_validate({"ui_language": "tr", "theme": "dark"})


def test_preferences_body_requires_the_key() -> None:
    with pytest.raises(ValidationError):
        PreferencesIn.model_validate({})
