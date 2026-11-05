"""The text of a pivot block: AQL for the analyst to copy, and the console links.

Pure: values in, text out, no I/O. **The product never runs this AQL.** It is shown to
the analyst, who copies it or opens it in the QRadar console (AGENTS.md rules 2 and 4).
Nothing the browser sends reaches this module: the service passes stored, validated
values only (numeric ids, a mapped property name, an IP that parses, an epoch derived
from a stored timestamp).

**A value that is not plainly safe is refused, never escaped.** A string literal may not
hold a single quote, a double quote, a backslash or a control character (the rule the
property-watch pins already follow, `PW_PINNED_VALUE_PATTERN`). A refusal does not fail
the block: the filter is left out and a note says so, so the analyst is never handed a
query that quietly filters on something other than what the page says.

**Both console URL forms live here**, in `ARIEL_SEARCH_PATH` / `ariel_search_url` and
`OFFENSE_SUMMARY_PATH` / `offense_summary_url`, so a later check against the lab console
corrects one place. They are unverified on the lab console.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import SplitResult, urlencode, urlsplit

from app.core.patterns import PROPERTY_NAME_PATTERN

#: QRadar Log Activity, AQL search mode. UNVERIFIED on the lab console.
ARIEL_SEARCH_PATH = "/console/do/ariel/arielSearch"
ARIEL_SEARCH_FIXED = (
    ("appName", "EventViewer"),
    ("pageId", "EventList"),
    ("dispatch", "performSearch"),
    ("value(searchMode)", "AQL"),
    ("value(timeRangeType)", "aqlTime"),
)
#: The offense summary page. UNVERIFIED on the lab console.
OFFENSE_SUMMARY_PATH = "/console/qradar/jsp/QRadar.jsp"

#: A window never reaches further than this (the provider's metric safety bound).
MAX_WINDOW = timedelta(hours=168)
MIN_WINDOW = timedelta(minutes=1)
#: Rows an events-grid query asks for, and groups an aggregate asks for.
EVENT_ROWS = 200
GROUP_ROWS = 50

_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_UNSAFE_LITERAL = re.compile(r"""[\x00-\x1f\x7f-\x9f'"\\]""")
_LITERAL_MAX = 255


class PivotRefusal(ValueError):
    """A value cannot be placed in AQL as text. The message is safe to show the analyst."""


@dataclass(frozen=True)
class PivotWindow:
    start: datetime
    stop: datetime

    @property
    def start_ms(self) -> int:
        return int(self.start.timestamp() * 1000)

    @property
    def stop_ms(self) -> int:
        return int(self.stop.timestamp() * 1000)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def make_window(
    start: datetime, stop: datetime | None, *, now: datetime
) -> tuple[PivotWindow, list[str]]:
    """A UTC window from stored times, clamped to something QRadar can search.

    A missing or future stop becomes `now`; a window shorter than a minute is widened to
    one; one longer than 168 hours keeps its last 168 hours. Each adjustment is a note.
    """
    notes: list[str] = []
    now = _utc(now)
    begin = _utc(start)
    end = _utc(stop) if stop is not None else now
    if end > now:
        end = now
    if end - begin < MIN_WINDOW:
        end = begin + MIN_WINDOW
        notes.append("The window was shorter than one minute and was widened to one minute.")
    if end - begin > MAX_WINDOW:
        begin = end - MAX_WINDOW
        notes.append("The window was longer than 168 hours; only its last 168 hours are searched.")
    return PivotWindow(start=begin, stop=end), notes


def literal(value: str) -> str:
    """A single-quoted AQL string literal, or `PivotRefusal`. Never escapes."""
    if not isinstance(value, str) or not value or len(value) > _LITERAL_MAX:
        raise PivotRefusal("the value is empty or too long to place in a query")
    if _UNSAFE_LITERAL.search(value) or not value.isprintable():
        raise PivotRefusal(
            "the value holds a quote, a backslash or a control character, which is never "
            "placed in a query"
        )
    return f"'{value}'"


def property_expr(name: str) -> str:
    """A mapped property name as a double-quoted AQL field, or `PivotRefusal`."""
    if not isinstance(name, str) or not PROPERTY_NAME_PATTERN.fullmatch(name):
        raise PivotRefusal("the mapped property name is not a plain name")
    return f'"{name}"'


def ip_literal(value: str) -> str:
    """An IP address that parses, as a literal, or `PivotRefusal`."""
    try:
        return f"'{ipaddress.ip_address(value.strip())}'"
    except ValueError as exc:
        raise PivotRefusal("the value is not an IP address") from exc


def int_literal(value: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text.isascii() or not text.isdigit() or len(text) > 18:
        raise PivotRefusal("the value is not a whole number")
    return str(int(text))


def _scope(window: PivotWindow) -> str:
    return (
        f"starttime >= {window.start_ms} AND starttime < {window.stop_ms}"
    )


def _tail(window: PivotWindow) -> str:
    return f"START {window.start_ms} STOP {window.stop_ms}"


def events_by_name_aql(
    *, conditions: list[str], window: PivotWindow, group_extra: str | None = None
) -> str:
    """Events in the window grouped by event name, largest first. `conditions` are ANDed."""
    where = " AND ".join([*conditions, _scope(window)])
    extra_select = f"{group_extra} AS \"{group_extra}\", " if group_extra else ""
    extra_group = f"{group_extra}, " if group_extra else ""
    return (
        f'SELECT {extra_select}QIDNAME(qid) AS "event_name", SUM(eventcount) AS "events" '  # noqa: S608 - validated values only
        f"FROM events WHERE {where} "
        f"GROUP BY {extra_group}qid ORDER BY SUM(eventcount) DESC "
        f"LIMIT {GROUP_ROWS} {_tail(window)}"
    )


def events_grid_aql(*, conditions: list[str], window: PivotWindow) -> str:
    """The events themselves, newest first."""
    where = " AND ".join([*conditions, _scope(window)])
    return (
        'SELECT starttime, QIDNAME(qid) AS "event_name", logsourceid, sourceip, '  # noqa: S608 - validated values only
        'destinationip, username, eventcount '
        f"FROM events WHERE {where} "
        f"ORDER BY starttime DESC LIMIT {EVENT_ROWS} {_tail(window)}"
    )


def grouped_aql(
    *, select_expr: str, alias: str, conditions: list[str], window: PivotWindow
) -> str:
    """Events in the window grouped by one validated expression, largest first."""
    where = " AND ".join([*conditions, _scope(window)])
    return (
        f'SELECT {select_expr} AS "{alias}", SUM(eventcount) AS "events" '  # noqa: S608 - validated values only
        f"FROM events WHERE {where} "
        f"GROUP BY {select_expr} ORDER BY SUM(eventcount) DESC "
        f"LIMIT {GROUP_ROWS} {_tail(window)}"
    )


def inoffense_aql(offense_id: int, window: PivotWindow) -> str:
    return (
        'SELECT starttime, QIDNAME(qid) AS "event_name", logsourceid, sourceip, '  # noqa: S608 - validated values only
        'destinationip, username, eventcount '
        f"FROM events WHERE INOFFENSE({int(offense_id)}) "
        f"ORDER BY starttime DESC LIMIT {EVENT_ROWS} {_tail(window)}"
    )


# --------------------------------------------------------------------------- console URLs
@dataclass(frozen=True)
class ConsoleBase:
    """`https://host[:port]` of the console, or why there is none."""

    url: str | None
    reason: str | None = None
    notes: list[str] = field(default_factory=list)


def console_base(console_host: str | None, provider_kind: str | None) -> ConsoleBase:
    """The console's origin from the instance's stored host. Never carries a credential."""
    if provider_kind == "mock":
        return ConsoleBase(None, "This instance is a mock provider; it has no QRadar console.")
    raw = (console_host or "").strip()
    if not raw:
        return ConsoleBase(None, "The instance has no console host configured.")
    parts: SplitResult
    try:
        parts = urlsplit(raw if "://" in raw else f"https://{raw}")
        host = parts.hostname
        port = parts.port
    except ValueError:
        return ConsoleBase(None, "The instance's console host is not a valid address.")
    if parts.scheme not in {"http", "https"} or not host or not _HOST.fullmatch(host):
        return ConsoleBase(None, "The instance's console host is not a valid address.")
    if parts.username or parts.password:
        return ConsoleBase(None, "The instance's console host carries credentials; no link built.")
    shown = f"[{host}]" if ":" in host else host
    origin = f"{parts.scheme}://{shown}" + (f":{port}" if port else "")
    return ConsoleBase(origin)


def ariel_search_url(base: str, aql: str) -> str:
    query = urlencode([*ARIEL_SEARCH_FIXED, ("value(aql)", aql)], safe="()")
    return f"{base}{ARIEL_SEARCH_PATH}?{query}"


def offense_summary_url(base: str, offense_id: int) -> str:
    query = urlencode(
        [("appName", "Sem"), ("pageId", "OffenseSummary"), ("summaryId", int(offense_id))]
    )
    return f"{base}{OFFENSE_SUMMARY_PATH}?{query}"
