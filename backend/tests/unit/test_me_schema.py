"""The /me contract: the interface-language field and its request body."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.i18n.messages import MessageRef, render
from app.schemas.me import GuideSeenIn, MeOut, PreferencesIn


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



def test_guide_seen_defaults_to_null_for_a_caller_without_an_account() -> None:
    assert _me().guide_seen is None
    assert _me(guide_seen=[]).guide_seen == []
    assert _me(guide_seen=["rules"]).guide_seen == ["rules"]


@pytest.mark.parametrize("value", ["rules", "anomaly-detail", "a", "x" * 64, "step-2"])
def test_guide_seen_body_accepts_a_section_slug(value: str) -> None:
    assert GuideSeenIn.model_validate({"section": value}).section == value


@pytest.mark.parametrize(
    "value",
    ["", "Rules", "two words", "-rules", "rules-", "a--b", "a/b", "../x", "x" * 65, "ä", "a_b"],
)
def test_guide_seen_body_refuses_anything_but_a_slug(value: str) -> None:
    with pytest.raises(ValidationError):
        GuideSeenIn.model_validate({"section": value})


def test_guide_seen_body_refuses_an_extra_or_missing_key() -> None:
    with pytest.raises(ValidationError):
        GuideSeenIn.model_validate({"section": "rules", "extra": 1})
    with pytest.raises(ValidationError):
        GuideSeenIn.model_validate({})


def test_the_guide_seen_cap_refusal_has_its_english_sentence() -> None:
    assert render(MessageRef("account.guide_seen.full")) == (
        "This account already records the maximum number of guide sections."
    )
