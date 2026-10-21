"""Offense response schemas.

Regression cover for a 500 that only appeared against a real appliance: the
mock provider produced no numeric grouping keys, so nothing exercised the
coercion until live data arrived.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.models.enums import RuleHealthStatus
from app.schemas.offense import CountEntry, OffenseSummary
from app.schemas.rule import RuleSummary


class TestCountEntryKeyCoercion:
    def test_string_key_is_unchanged(self) -> None:
        assert CountEntry(key="OPEN", count=3).key == "OPEN"

    def test_integer_key_is_coerced(self) -> None:
        """`distribution(..., "magnitude")` groups by an integer column.

        Pydantic's str type rejects an int outright, so before this coercion
        GET /offenses/analytics returned 500 for any instance with offenses.
        """
        assert CountEntry(key=4, count=7).key == "4"  # type: ignore[arg-type]

    def test_rule_id_key_is_coerced(self) -> None:
        """top_entities("rule_ids") yields ints; ("usernames") yields strings.

        One endpoint therefore feeds both shapes into the same model.
        """
        assert CountEntry(key=100051, count=7).key == "100051"  # type: ignore[arg-type]

    def test_none_key_becomes_empty_not_the_word_none(self) -> None:
        """Unassigned offenses group under NULL.

        Rendering that as the literal "None" would be indistinguishable from an
        analyst actually named None, and would show up in the UI as a real
        assignee.
        """
        assert CountEntry(key=None, count=7).key == ""  # type: ignore[arg-type]

    @pytest.mark.parametrize("count", [0, 1, 10_000])
    def test_count_is_preserved(self, count: int) -> None:
        assert CountEntry(key="x", count=count).count == count


class TestSanitizedStrEnumFields:
    """`_Sanitized` sanitizes text but must keep enum members whole.

    A StrEnum member is a str subclass, so running it through the sanitizer
    returns a bare str: the response model then holds a value of a type it does
    not declare and pydantic's serializer warns on every response (regression
    guard for the 2.1 serializer warnings). Enum values are code constants,
    never user text, so there is nothing for the sanitizer to strip anyway.
    """

    @staticmethod
    def _rule_row() -> Any:
        """The attributes `RuleSummary` reads off an `AnalyticsRule` row."""

        class _RuleRow:
            id = uuid.uuid4()
            qradar_id = 100
            name = "rule-100"
            enabled = True
            is_building_block = False
            health_status = RuleHealthStatus.UNKNOWN

        return _RuleRow()

    def test_an_enum_member_is_not_flattened_to_a_plain_str(self) -> None:
        summary = RuleSummary.model_validate(self._rule_row())
        assert summary.health_status is RuleHealthStatus.UNKNOWN

    def test_the_serialized_health_status_is_the_enum_value(self) -> None:
        summary = RuleSummary.model_validate(self._rule_row())
        assert summary.model_dump(mode="json")["health_status"] == "UNKNOWN"

    def test_plain_strings_are_still_sanitized(self) -> None:
        row = self._rule_row()
        row.name = "<b>rule-100</b>"
        summary = RuleSummary.model_validate(row)
        assert summary.name == "rule-100"


class _BareStrSubclass(str):
    """A str subclass that is not an enum member."""


class TestSanitizedStrSubclasses:
    """Every str is sanitized except an enum.Enum member.

    Keying sanitization on `type(value) is str` would let any other str
    subclass skip the sanitizer and reach the DOM with its markup intact.
    The subclass branch cannot be exercised through model validation on
    pydantic 2.14 — its str core schema normalizes subclasses to plain str
    before after-validators run — so the rule is pinned on `_strip_markup`
    itself, the validator every `_Sanitized` response model inherits.
    """

    def test_a_str_subclass_carrying_markup_is_sanitized(self) -> None:
        value = OffenseSummary._strip_markup(_BareStrSubclass("<b>br</b>"))
        assert value == "br"
        assert type(value) is str

    def test_a_str_subclass_in_a_list_is_sanitized_and_an_enum_member_is_kept(self) -> None:
        value = OffenseSummary._strip_markup(
            [_BareStrSubclass("<i>Malware</i>"), RuleHealthStatus.UNKNOWN]
        )
        assert value == ["Malware", RuleHealthStatus.UNKNOWN]
        assert type(value[0]) is str
        assert value[1] is RuleHealthStatus.UNKNOWN
