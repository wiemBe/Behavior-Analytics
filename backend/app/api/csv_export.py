"""The RFC 4180 building blocks every CSV export shares.

The traffic export (Amendment 2) settled the format and these helpers are that
format, lifted out of its route module so the alert and anomaly exports cannot
drift from it: RFC 4180, UTF-8, CRLF, an empty cell for a value that was not
measured (`None` is never a zero), an ISO 8601 UTC stamp with `Z`, and a leading
`'` on any text cell a spreadsheet would otherwise evaluate as a formula.

Nothing here reads a database or knows what a column means, so the same rules
hold for every export without a second copy of them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime

from fastapi import Response

#: A text cell opening with one of these is run as a formula by a spreadsheet.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

#: The media type of every export. Starlette would append the same charset to
#: `text/csv`; naming it keeps the header identical across all exports.
CSV_MEDIA_TYPE = "text/csv; charset=utf-8"


def _csv_field(value: object) -> str:
    """One RFC 4180 field built from a cell value.

    None is an empty cell, never 0 (rule 8). A text cell that opens with `=`,
    `+`, `-`, `@`, a tab or a CR is prefixed with `'`, so a spreadsheet opens it
    as text instead of evaluating it. A field holding a comma, a quote or a line
    break is quoted and its own quotes doubled. Rates are plain with three
    decimals and `.` as the decimal point.
    """
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, float):
        text = f"{value:.3f}"
    elif isinstance(value, str):
        text = "'" + value if value.startswith(_FORMULA_PREFIXES) else value
    else:
        text = str(value)
    if any(ch in text for ch in ',"\r\n'):
        return '"' + text.replace('"', '""') + '"'
    return text


def _csv_row(cells: Iterable[object]) -> str:
    """One RFC 4180 record, CRLF-terminated."""
    return ",".join(_csv_field(cell) for cell in cells) + "\r\n"


def _csv_time(value: datetime | None) -> str | None:
    """An ISO 8601 UTC stamp with `Z`; None stays an empty cell."""
    return None if value is None else value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def csv_enum(value: object) -> object:
    """A column typed as an enum as the value it is stored as; None stays None.

    The columns are plain `String` at rest, so this is the stored token either
    way; naming it keeps a caller from publishing `Severity.HIGH` instead of
    `HIGH` if a column ever hands back the member rather than its value.
    """
    return None if value is None else getattr(value, "value", value)


def csv_document(header: Sequence[object], rows: Iterable[Iterable[object]]) -> str:
    """A header row and one record per row, each CRLF-terminated.

    The header is always present, so an export that matched nothing is a file
    with its columns rather than an empty download.
    """
    return "".join([_csv_row(header), *(_csv_row(row) for row in rows)])


def csv_stamp(value: datetime) -> str:
    """A filename-safe UTC stamp: minute resolution, `Z` for the zone."""
    return value.astimezone(UTC).strftime("%Y%m%dT%H%MZ")


def csv_range_filename(kind: str, since: datetime | None, until: datetime | None) -> str:
    """`<kind>-<since>-<until>.csv`, or `<kind>-no-range.csv` without a window.

    A half-open range is not a range: without both ends the file is not named
    after a window it does not cover.
    """
    if since is None or until is None:
        return f"{kind}-no-range.csv"
    return f"{kind}-{csv_stamp(since)}-{csv_stamp(until)}.csv"


def csv_slug(value: str) -> str:
    """A filename-safe fragment: lowered, `[a-z0-9]+` runs joined by `-`."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def csv_export_response(
    body: str,
    *,
    filename: str,
    truncated: bool,
    extra_headers: Mapping[str, str] | None = None,
) -> Response:
    """A CSV attachment, with `X-Export-Truncated` only when the export was cut.

    A cut export says so in the header and never silently: the caller can tell
    "the whole set" from "the first N of it" without counting rows.
    """
    headers: dict[str, str] = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if extra_headers is not None:
        headers.update(extra_headers)
    if truncated:
        headers["X-Export-Truncated"] = "true"
    return Response(content=body, media_type=CSV_MEDIA_TYPE, headers=headers)
