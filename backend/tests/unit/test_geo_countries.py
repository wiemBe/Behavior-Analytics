"""The static ISO 3166-1 table and the property-value matcher (GEO-3)."""

from __future__ import annotations

import pytest

from app.geo.countries import (
    COUNTRIES,
    country_code_for,
    normalise_country_token,
)


class TestTable:
    def test_it_holds_the_249_assigned_codes(self) -> None:
        assert len(COUNTRIES) == 249

    def test_alpha2_and_alpha3_are_well_formed_and_unique(self) -> None:
        alpha2 = [c.alpha2 for c in COUNTRIES]
        alpha3 = [c.alpha3 for c in COUNTRIES]
        assert all(len(code) == 2 and code.isascii() and code.isupper() for code in alpha2)
        assert all(len(code) == 3 and code.isascii() and code.isupper() for code in alpha3)
        assert len(set(alpha2)) == 249
        assert len(set(alpha3)) == 249

    def test_every_normalised_name_and_alias_is_unique_across_the_table(self) -> None:
        seen: dict[str, str] = {}
        for country in COUNTRIES:
            for token in (country.alpha2, country.alpha3, country.name, *country.aliases):
                key = normalise_country_token(token)
                assert key, token
                assert seen.setdefault(key, country.alpha2) == country.alpha2, token

    def test_no_country_name_is_empty_and_each_name_is_unique(self) -> None:
        names = [normalise_country_token(c.name) for c in COUNTRIES]
        assert all(names)
        assert len(set(names)) == 249


class TestMatcher:
    @pytest.mark.parametrize(
        ("value", "code"),
        [
            ("US", "US"),
            ("us", "US"),
            (" tr ", "TR"),
            ("USA", "US"),
            ("deu", "DE"),
            ("Germany", "DE"),
            ("united states", "US"),
            ("United States", "US"),
            ("United States of America", "US"),
            ("Russia", "RU"),
            ("Russian Federation", "RU"),
            ("Türkiye", "TR"),
            ("Turkiye", "TR"),
            ("TURKEY", "TR"),
            ("South Korea", "KR"),
            ("Iran", "IR"),
            ("Vietnam", "VN"),
            ("Czechia", "CZ"),
            ("Czech Republic", "CZ"),
            ("UK", "GB"),
            ("United Kingdom", "GB"),
            ("Europe.Turkey", "TR"),
            ("NorthAmerica.UnitedStates", "US"),
            ("Asia.South Korea", "KR"),
            ("Europe.Germany ", "DE"),
            ("Virgin Islands (U.S.)", "VI"),
        ],
    )
    def test_each_format_is_a_country(self, value: str, code: str) -> None:
        assert country_code_for(value) == code

    @pytest.mark.parametrize(
        "value",
        [
            "",
            " ",
            ".",
            "Europe.",
            "Europe.Atlantis",
            "Atlantis",
            "Europe",
            "XX",
            "not-a-country",
            "US.Europe",
            "Turkey and more",
            "T",
        ],
    )
    def test_anything_else_is_not_a_country(self, value: str) -> None:
        assert country_code_for(value) is None
