"""Property-watch references and pins: what may reach AQL (design §G.3, §G.6)."""

from __future__ import annotations

import pytest

from app.models.enums import PwReferenceKind
from app.property_watch.reference import (
    PW_MAX_PROPERTIES,
    PW_NORMALIZED_FIELD_ALLOWLIST,
    PW_PINNED_VALUE_PATTERN,
    PwFieldReference,
    PwReferenceError,
    validate_pinned_value,
    validate_pinned_values,
    validate_properties,
)
from app.providers.base import IPS_NORMALIZED_FIELD_ALLOWLIST


class TestPinnedValuePattern:
    @pytest.mark.parametrize(
        "value",
        [
            "DENY",
            "alice",
            "alice@example.com",
            "a b",
            "Müller",
            "用户",
            "10.1.4.7",
            "C:/tmp/x",
            "a" * 255,
        ],
    )
    def test_accepts_a_plain_value(self, value: str) -> None:
        assert PW_PINNED_VALUE_PATTERN.fullmatch(value)
        assert validate_pinned_value(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "O'Brien",  # single quote
            'say "hi"',  # double quote
            "a\\b",  # backslash
            "a\x00b",  # NUL
            "a\nb",  # newline
            "a\tb",  # tab
            "a\x1fb",  # the tuple separator
            "a\x7fb",  # DEL
            "a\x85b",  # C1 control
            "alice\n",  # a trailing newline must not slip past the anchor
            "",  # empty
            "a" * 256,  # over length
            " alice",  # leading whitespace: the fold strips, so it could never match
            "alice ",
        ],
    )
    def test_refuses_what_would_break_a_literal(self, value: str) -> None:
        assert PW_PINNED_VALUE_PATTERN.fullmatch(value) is None
        with pytest.raises(PwReferenceError):
            validate_pinned_value(value)

    def test_validator_also_refuses_unprintable_text_the_pattern_allows(self) -> None:
        zero_width = "a\u200bb"
        assert PW_PINNED_VALUE_PATTERN.fullmatch(zero_width)
        with pytest.raises(PwReferenceError):
            validate_pinned_value(zero_width)

    @pytest.mark.parametrize("value", [None, 5, b"x", ["x"]])
    def test_refuses_non_text(self, value: object) -> None:
        with pytest.raises(PwReferenceError):
            validate_pinned_value(value)

    def test_the_message_never_echoes_the_value(self) -> None:
        with pytest.raises(PwReferenceError) as caught:
            validate_pinned_value("s3cret'value")
        assert "s3cret" not in str(caught.value)


class TestNormalizedAllowlist:
    def test_v1_ships_exactly_username(self) -> None:
        assert PW_NORMALIZED_FIELD_ALLOWLIST == ("username",)
        reference = PwFieldReference(PwReferenceKind.NORMALIZED, "username")
        assert reference.as_dict() == {"kind": "NORMALIZED", "name": "username"}

    @pytest.mark.parametrize(
        "name",
        ["category", "sourceip", "destinationip", "destinationport", "sourceport",
         "protocolid", "qid", "severity", "Username", "username ", "", "eventname"],
    )
    def test_everything_else_is_out(self, name: str) -> None:
        with pytest.raises(PwReferenceError):
            PwFieldReference(PwReferenceKind.NORMALIZED, name)

    def test_the_ips_allowlist_is_not_shared(self) -> None:
        assert "username" not in IPS_NORMALIZED_FIELD_ALLOWLIST
        assert not set(PW_NORMALIZED_FIELD_ALLOWLIST) & set(IPS_NORMALIZED_FIELD_ALLOWLIST)


class TestCustomPropertyReference:
    @pytest.mark.parametrize("name", ["Action", "Source User-1", "_x", "a" * 64])
    def test_accepts_the_property_name_shape(self, name: str) -> None:
        assert PwFieldReference(PwReferenceKind.CUSTOM_PROPERTY, name).name == name

    @pytest.mark.parametrize(
        "name", ['Action"', "a'b", "a,b", "a(b)", "", "-x", "a" * 65, "a;b", "a\nb"]
    )
    def test_refuses_what_is_not_a_name_rather_than_escaping_it(self, name: str) -> None:
        with pytest.raises(PwReferenceError):
            PwFieldReference(PwReferenceKind.CUSTOM_PROPERTY, name)

    def test_from_dict_round_trips_and_refuses_bad_shapes(self) -> None:
        reference = PwFieldReference.from_dict({"kind": "CUSTOM_PROPERTY", "name": "Action"})
        assert PwFieldReference.from_dict(reference.as_dict()) == reference
        for bad in (
            "Action",
            {"name": "Action"},
            {"kind": "SQL", "name": "Action"},
            {"kind": "CUSTOM_PROPERTY", "name": 7},
            {"kind": 3, "name": "Action"},
        ):
            with pytest.raises(PwReferenceError):
                PwFieldReference.from_dict(bad)


class TestProperties:
    def _ref(self, name: str) -> dict[str, str]:
        return {"kind": "CUSTOM_PROPERTY", "name": name}

    def test_one_or_two_are_accepted(self) -> None:
        assert len(validate_properties([self._ref("Action")])) == 1
        assert len(validate_properties([self._ref("Action"), self._ref("Host")])) == 2
        assert PW_MAX_PROPERTIES == 2

    def test_none_and_three_are_refused(self) -> None:
        with pytest.raises(PwReferenceError):
            validate_properties([])
        with pytest.raises(PwReferenceError):
            validate_properties([self._ref("A"), self._ref("B"), self._ref("C")])

    def test_the_same_property_twice_is_refused(self) -> None:
        with pytest.raises(PwReferenceError):
            validate_properties([self._ref("Action"), self._ref("Action")])

    @pytest.mark.parametrize("bad", ["Action", {"kind": "CUSTOM_PROPERTY"}, None, 3])
    def test_a_non_list_is_refused(self, bad: object) -> None:
        with pytest.raises(PwReferenceError):
            validate_properties(bad)  # type: ignore[arg-type]


class TestPinnedValues:
    def test_one_string_per_property(self) -> None:
        assert validate_pinned_values([["DENY"], ["ALLOW"]], property_count=1, max_pinned=10) == (
            ("DENY",),
            ("ALLOW",),
        )
        assert validate_pinned_values([["alice", "DENY"]], property_count=2, max_pinned=10) == (
            ("alice", "DENY"),
        )

    def test_a_partial_tuple_is_refused(self) -> None:
        with pytest.raises(PwReferenceError):
            validate_pinned_values([["alice"]], property_count=2, max_pinned=10)
        with pytest.raises(PwReferenceError):
            validate_pinned_values([["a", "b"]], property_count=1, max_pinned=10)

    def test_the_count_cap_and_duplicates(self) -> None:
        with pytest.raises(PwReferenceError):
            validate_pinned_values([[str(i)] for i in range(11)], property_count=1, max_pinned=10)
        assert len(
            validate_pinned_values([[str(i)] for i in range(10)], property_count=1, max_pinned=10)
        ) == 10
        with pytest.raises(PwReferenceError):
            validate_pinned_values([["a"], ["a"]], property_count=1, max_pinned=10)

    def test_a_bad_component_refuses_the_whole_list(self) -> None:
        with pytest.raises(PwReferenceError):
            validate_pinned_values([["ok"], ["O'Brien"]], property_count=1, max_pinned=10)

    @pytest.mark.parametrize("bad", ["DENY", [["a"], "b"], None, {"a": 1}])
    def test_wrong_shapes_are_refused(self, bad: object) -> None:
        with pytest.raises(PwReferenceError):
            validate_pinned_values(bad, property_count=1, max_pinned=10)  # type: ignore[arg-type]
