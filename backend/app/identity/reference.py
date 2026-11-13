"""Identity field references. Rejected, never escaped, and never echoed.

The profile stores each field as one string. ``qid`` is the normalized event-id
form (``QIDNAME(qid)`` matched to ``WIN-<id>``). ``username`` is the only
normalized username. Anything else must be a custom-property name. A known
Ariel identifier that is not the one field that allows it is refused, so a
normalized name cannot slip through as a custom property.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.patterns import PROPERTY_NAME_PATTERN
from app.models.enums import IDENTITY_EVENT_CATALOG

#: Normalized Ariel names. Only ``qid`` (event id) and ``username`` are allowed,
#: and each only in its own field. The rest are refused rather than quoted.
_KNOWN_NORMALIZED = frozenset(
    {
        "username",
        "qid",
        "sourceip",
        "destinationip",
        "destinationport",
        "sourceport",
        "category",
        "logsourceid",
        "starttime",
        "eventcount",
        "magnitude",
        "protocolid",
    }
)
_CATALOG_IDS = frozenset(event_id for event_id, _kind in IDENTITY_EVENT_CATALOG)
#: Validation is a spot check. A longer window is refused before any search is built.
PROBE_MAX_WINDOW_SECONDS = 3600

_EVENT_ID_REFUSED = "the event id property is not an allowed field"
_USERNAME_REFUSED = "the username property is not an allowed field"
_LOGON_REFUSED = "the logon type property is not an allowed field"


class IdentityReferenceError(ValueError):
    """A reference was refused. The message does not contain the refused text."""


def _custom_property(name: object, message: str) -> str:
    if (
        isinstance(name, str)
        and name not in _KNOWN_NORMALIZED
        and "\n" not in name
        and "\r" not in name
        and "\x00" not in name
        and PROPERTY_NAME_PATTERN.fullmatch(name) is not None
    ):
        return name
    raise IdentityReferenceError(message)


def validate_event_id_property(name: object) -> str:
    """``qid`` or a custom-property name. Anything else is refused."""
    if name == "qid":
        return "qid"
    return _custom_property(name, _EVENT_ID_REFUSED)


def validate_username_property(name: object) -> str:
    """``username`` or a custom-property name. Anything else is refused."""
    if name == "username":
        return "username"
    return _custom_property(name, _USERNAME_REFUSED)


def validate_logon_type_property(name: object) -> str | None:
    """Optional and unused by collection. None is the only empty value."""
    if name is None:
        return None
    return _custom_property(name, _LOGON_REFUSED)


def validate_event_ids(event_ids: Sequence[int]) -> tuple[int, ...]:
    """Catalog ids only, de-duplicated and sorted. An empty list is refused."""
    if isinstance(event_ids, str | bytes):
        raise IdentityReferenceError("event ids must be catalog integers")
    values: list[int] = []
    for item in event_ids:
        if isinstance(item, bool) or not isinstance(item, int) or item not in _CATALOG_IDS:
            raise IdentityReferenceError("event ids must be catalog integers")
        values.append(item)
    unique = tuple(sorted(set(values)))
    if not unique:
        raise IdentityReferenceError("event ids must be catalog integers")
    return unique


def validate_source_ids(source_ids: Sequence[int], *, max_sources: int) -> tuple[int, ...]:
    """Non-negative integer log-source ids, within the chunk cap. Bool is refused."""
    if isinstance(source_ids, str | bytes):
        raise IdentityReferenceError("log source ids must be non-negative integers")
    if isinstance(max_sources, bool) or not isinstance(max_sources, int) or max_sources < 1:
        raise IdentityReferenceError("the log source cap must be a positive integer")
    values: list[int] = []
    for item in source_ids:
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise IdentityReferenceError("log source ids must be non-negative integers")
        values.append(item)
    unique = tuple(sorted(set(values)))
    if not unique:
        raise IdentityReferenceError("an identity search needs at least one log source")
    if len(unique) > max_sources:
        raise IdentityReferenceError(
            f"an identity search covers at most {max_sources} log sources"
        )
    return unique


def event_id_expression(event_id_property: str) -> str:
    """The AQL expression for the event id. The property has already been validated."""
    if event_id_property == "qid":
        return "QIDNAME(qid)"
    return f'"{event_id_property}"'


def event_id_literal(event_id_property: str, event_id: int) -> str:
    """One catalog id in the form the event-id expression compares."""
    number = int(event_id)
    if event_id_property == "qid":
        return f"'WIN-{number}'"
    return str(number)


def username_expression(username_property: str) -> str:
    """The username column, quoted only after validation."""
    return f'"{username_property}"'


def validate_probe_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """A timezone-aware window of at most one hour. A bad window raises before AQL."""
    unaware = (
        start.tzinfo is None
        or start.utcoffset() is None
        or end.tzinfo is None
        or end.utcoffset() is None
    )
    if unaware:
        raise IdentityReferenceError("the validation window must be timezone-aware")
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    span = (end_utc - start_utc).total_seconds()
    if span <= 0 or span > PROBE_MAX_WINDOW_SECONDS:
        raise IdentityReferenceError("the validation window must be within one hour")
    return start_utc, end_utc


@dataclass(frozen=True)
class IdentityFieldMapping:
    """The mapping one source collects with. Built only through the validators."""

    event_id_property: str
    username_property: str
    logon_type_property: str | None
    enabled_event_ids: tuple[int, ...]

    @classmethod
    def from_parts(
        cls,
        event_id_property: object,
        username_property: object,
        logon_type_property: object,
        enabled_event_ids: Sequence[int],
    ) -> IdentityFieldMapping:
        return cls(
            validate_event_id_property(event_id_property),
            validate_username_property(username_property),
            validate_logon_type_property(logon_type_property),
            validate_event_ids(enabled_event_ids),
        )

    @classmethod
    def from_profile(cls, profile: Any) -> IdentityFieldMapping:
        """Read a stored profile. A row that fails validation is not collected."""
        return cls.from_parts(
            profile.event_id_property,
            profile.username_property,
            profile.logon_type_property,
            tuple(profile.enabled_event_ids or ()),
        )
