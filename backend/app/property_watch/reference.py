"""What a property watch may name and pin, validated before anything reaches AQL.

A watch references one or two properties as `(kind, name)` pairs -- the IPS shape -- and may
pin values to count even when they are rare. Both are operator text that the collector will
later put into a query, so both are **rejected, not escaped** (design §G.3, §G.6):

  * a `CUSTOM_PROPERTY` name must match `PROPERTY_NAME_PATTERN`;
  * a `NORMALIZED` field must be an exact member of `PW_NORMALIZED_FIELD_ALLOWLIST`, which
    is separate from the IPS allowlist -- different domain, different failure modes;
  * a pinned value must match `PW_PINNED_VALUE_PATTERN`.

Pure. No I/O.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.models.enums import PwReferenceKind
from app.providers.base import PROPERTY_NAME_PATTERN

#: Normalized Ariel identifiers a watch may name, quoted only after exact membership.
#: v1 ships string-valued fields only, and exactly one: `username`. `category` is a
#: numeric QRadar id, and IP and numeric fields hold 0 or 255 where a value is absent
#: rather than NULL, so none of them can be counted by "has a value" (design §G.3).
PW_NORMALIZED_FIELD_ALLOWLIST = ("username",)
_NORMALIZED_FIELDS = frozenset(PW_NORMALIZED_FIELD_ALLOWLIST)

#: A watch compares at most two properties: a third multiplies the combination space until
#: no weekday x hour cell can train (design §G.3). Raising it is an open question, not a
#: setting, so it is a constant.
PW_MAX_PROPERTIES = 2

PW_PINNED_VALUE_MAX_LENGTH = 255

#: The only shape a pinned value may take before it becomes an AQL string literal: one to
#: 255 characters, no single quote, double quote, backslash or control character, and no
#: leading or trailing whitespace (the fold strips values, so a padded pin could never
#: match). `\Z` and the lookarounds keep a trailing newline from slipping past `$`.
PW_PINNED_VALUE_PATTERN = re.compile(
    r"""(?=\S)[^\x00-\x1f\x7f-\x9f'"\\]{1,255}(?<=\S)\Z"""
)


class PwReferenceError(ValueError):
    """A reference or a pin was refused. Its message is safe for an operator and never
    echoes the offending text."""


@dataclass(frozen=True)
class PwFieldReference:
    kind: PwReferenceKind
    name: str

    def __post_init__(self) -> None:
        if self.kind is PwReferenceKind.NORMALIZED:
            if self.name not in _NORMALIZED_FIELDS:
                raise PwReferenceError("normalized field is not in the property-watch allowlist")
            return
        if self.kind is PwReferenceKind.CUSTOM_PROPERTY:
            if not PROPERTY_NAME_PATTERN.fullmatch(self.name):
                raise PwReferenceError(
                    "invalid QRadar property name: letters, digits, underscore, space and "
                    "hyphen only, at most 64 characters"
                )
            return
        raise PwReferenceError("unknown property reference kind")

    @classmethod
    def from_dict(cls, value: Any) -> PwFieldReference:
        if not isinstance(value, dict):
            raise PwReferenceError("a property reference is an object with a kind and a name")
        raw_kind = value.get("kind")
        if not isinstance(raw_kind, str):
            raise PwReferenceError("unknown property reference kind")
        try:
            kind = PwReferenceKind(raw_kind)
        except ValueError as exc:
            raise PwReferenceError("unknown property reference kind") from exc
        name = value.get("name")
        if not isinstance(name, str):
            raise PwReferenceError("property reference name must be text")
        return cls(kind=kind, name=name)

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "name": self.name}


def validate_properties(raw: Sequence[Any]) -> tuple[PwFieldReference, ...]:
    """The watch's ordered references: one or two, each valid, none repeated."""
    if isinstance(raw, str | bytes | dict) or not isinstance(raw, Sequence):
        raise PwReferenceError("properties is a list of one or two references")
    if not 1 <= len(raw) <= PW_MAX_PROPERTIES:
        raise PwReferenceError(f"a watch names one or two properties, not {len(raw)}")
    references = tuple(PwFieldReference.from_dict(item) for item in raw)
    if len(set(references)) != len(references):
        raise PwReferenceError("a watch cannot name the same property twice")
    return references


def validate_pinned_value(value: Any) -> str:
    """One pinned component, or `PwReferenceError`. Never escapes, never trims."""
    if not isinstance(value, str):
        raise PwReferenceError("a pinned value is text")
    if not value:
        raise PwReferenceError("a pinned value cannot be empty")
    if len(value) > PW_PINNED_VALUE_MAX_LENGTH:
        raise PwReferenceError(
            f"a pinned value is at most {PW_PINNED_VALUE_MAX_LENGTH} characters"
        )
    if not PW_PINNED_VALUE_PATTERN.fullmatch(value) or not value.isprintable():
        raise PwReferenceError(
            "a pinned value cannot hold a quote, a backslash, a control character or "
            "leading or trailing whitespace"
        )
    return value


def validate_pinned_values(
    raw: Sequence[Any], *, property_count: int, max_pinned: int
) -> tuple[tuple[str, ...], ...]:
    """The watch's pins: one tuple per pinned value, one string per property.

    A pin on a combination watch is a full tuple (design §G.6); a partial one would need a
    marginal series. Duplicates are refused, not merged, so the operator sees the mistake.
    """
    if isinstance(raw, str | bytes | dict) or not isinstance(raw, Sequence):
        raise PwReferenceError("pinned values is a list of tuples")
    if len(raw) > max_pinned:
        raise PwReferenceError(f"a watch pins at most {max_pinned} values, not {len(raw)}")
    pins: list[tuple[str, ...]] = []
    for entry in raw:
        if isinstance(entry, str | bytes | dict) or not isinstance(entry, Sequence):
            raise PwReferenceError("each pinned value is a list with one string per property")
        if len(entry) != property_count:
            raise PwReferenceError(
                f"each pinned value needs {property_count} string(s), one per property"
            )
        pins.append(tuple(validate_pinned_value(item) for item in entry))
    if len(set(pins)) != len(pins):
        raise PwReferenceError("a value is pinned twice")
    return tuple(pins)
