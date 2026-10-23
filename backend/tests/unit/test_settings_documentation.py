"""Every Settings field and the channel variables are named where operators read them.

The cadence sentence is parsed from the code, so a default change fails until the
template and the geography comment move with it.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import SETTINGS_ENV_ALLOWLIST, _known_setting_env_names
from app.models.enums import SearchFamily, default_search_cadence

#: Settings env names that stay out of ``docs/CONFIGURATION.md``. Empty on purpose:
#: an internal field is listed here with a one-line reason, not dropped silently.
INTERNAL_SETTINGS: frozenset[str] = frozenset()

#: Build identity read by ``app/core/version.py``. Not an operator setting.
_BUILD_IDENTITY = "GIT_SHA"

_ROOT = Path(__file__).resolve().parents[3]


def _configuration() -> str:
    return (_ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")


def test_every_setting_is_named_in_the_configuration_reference() -> None:
    text = _configuration()
    missing = sorted(
        name
        for name in _known_setting_env_names()
        if name not in INTERNAL_SETTINGS and f"`{name}`" not in text
    )
    assert missing == []
    channels = sorted(
        name
        for name in SETTINGS_ENV_ALLOWLIST
        if name != _BUILD_IDENTITY and f"`{name}`" not in text
    )
    assert channels == []


def test_the_cadence_sentence_matches_the_code() -> None:
    usual = default_search_cadence(SearchFamily.PHASE_A_VOLUME)
    waf = default_search_cadence(SearchFamily.WAF_COLLECTION)
    ips = default_search_cadence(SearchFamily.IPS_COLLECTION)
    assert waf == ips
    fifteen = {SearchFamily.WAF_COLLECTION, SearchFamily.IPS_COLLECTION}
    for family in SearchFamily:
        expected = waf if family in fifteen else usual
        assert default_search_cadence(family) == expected

    usual_minutes = usual.value // 60
    waf_minutes = waf.value // 60
    example = (_ROOT / ".env.example").read_text(encoding="utf-8")
    assert f"defaults to {usual_minutes} minutes" in example
    assert f"WAF and IPS, which default to {waf_minutes} minutes" in example

    geo_seconds = default_search_cadence(SearchFamily.GEO_COLLECTION).value
    per_hour = (3600 // geo_seconds) * 2
    config = (_ROOT / "backend" / "app" / "core" / "config.py").read_text(encoding="utf-8")
    assert f"{per_hour} searches an hour" in config
    assert "1 h default cadence" not in config
