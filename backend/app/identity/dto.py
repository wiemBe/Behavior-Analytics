"""Identity search results. No provider imports, so the provider can import this."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class IdentitySearchKind(StrEnum):
    """The three keyed searches of one identity tick (design §3)."""

    COUNTS = "COUNTS"
    RARE_NAMES = "RARE_NAMES"
    VOLUME_NAMES = "VOLUME_NAMES"


@dataclass(frozen=True)
class IdentityKeyedRow:
    """One group of a keyed identity search. `username` is set only for a name search."""

    qradar_log_source_id: int
    event_id: int
    bucket_start: datetime
    events: int
    username: str | None = None


@dataclass(frozen=True)
class IdentityKeyedResult:
    """Rows of one identity search. `truncated` means the console returned more than the cap."""

    search: IdentitySearchKind
    qradar_log_source_ids: tuple[int, ...]
    event_ids: tuple[int, ...]
    window_start: datetime
    window_end: datetime
    bucket_seconds: int
    rows: tuple[IdentityKeyedRow, ...]
    truncated: bool
    query: str


@dataclass(frozen=True)
class IdentityFieldProbe:
    """Population of one mapped field. `rejected` is a 422, which is not a zero."""

    populated_events: int | None
    rejected: bool
    error: str | None


@dataclass(frozen=True)
class IdentityProbeResult:
    """The bounded validation probe. Counts only; no username leaves this object."""

    total_events: int | None
    event_id: IdentityFieldProbe
    username: IdentityFieldProbe
    catalog_event_ids: tuple[int, ...]
    truncated: bool
    error: str | None
    searches: int
