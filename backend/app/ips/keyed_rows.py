"""Validation and row parsing for keyed IPS searches. Pure: no I/O, no clock.

Shared by the REST provider and the mock, so a mock run exercises the very guards
and the very row parsing production does (SEARCH-CADENCE §SC.5 rule 6). Nothing
here keeps a raw value: the rows go to the pure folds in `app.ips.fold`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from app.collectors.bucketed_metrics import _integer_bucket_key, validate_bucketed_metric_window
from app.models.enums import IpsDimension, IpsLogicalField
from app.providers.base import (
    IPS_DIMENSION_FIELDS,
    IPS_NORMALIZED_FIELD_ALLOWLIST,
    PROPERTY_NAME_PATTERN,
    ProviderError,
)
from app.providers.dto import IpsKeyedRow
from app.providers.normalize import as_int

_KINDS = frozenset({"NORMALIZED", "CUSTOM_PROPERTY"})
_LOGICAL_FIELDS = frozenset(field.value for field in IpsLogicalField)


def validate_ips_sources(ids: Iterable[int], *, max_sources: int) -> tuple[int, ...]:
    """The sorted, de-duplicated source ids of a search, or a ValueError."""
    values = list(ids)
    if any(isinstance(v, bool) or not isinstance(v, int) for v in values):
        raise ValueError("IPS log source ids must be integers")
    unique = tuple(sorted(set(values)))
    if not unique:
        raise ValueError("an IPS search needs at least one log source")
    if len(unique) > max_sources:
        raise ValueError(f"an IPS search covers at most {max_sources} log sources")
    return unique


def validate_ips_fields(
    dimension: IpsDimension, fields: Mapping[str, tuple[str, str]]
) -> dict[str, tuple[str, str]]:
    """Every reference re-checked against the allowlist or the property pattern.

    A dimension's required references must be present. Anything else given is
    still validated, then ignored by the query.
    """
    out: dict[str, tuple[str, str]] = {}
    for logical, reference in fields.items():
        logical_key = str(logical)
        if logical_key not in _LOGICAL_FIELDS:
            raise ValueError("unknown IPS logical field")
        try:
            kind, name = reference
        except (TypeError, ValueError) as exc:
            raise ValueError("an IPS field reference is a (kind, name) pair") from exc
        kind, name = str(kind), name
        if kind not in _KINDS or not isinstance(name, str):
            raise ValueError("unknown IPS field reference kind")
        if kind == "NORMALIZED":
            if name not in IPS_NORMALIZED_FIELD_ALLOWLIST:
                raise ValueError("normalized field is not in the IPS allowlist")
        elif not PROPERTY_NAME_PATTERN.fullmatch(name):
            raise ValueError("invalid QRadar property name")
        out[logical_key] = (kind, name)
    required, _optional = IPS_DIMENSION_FIELDS[dimension]
    missing = [field for field in required if field not in out]
    if missing:
        raise ValueError(f"IPS dimension {dimension.value} needs {', '.join(missing)}")
    return out


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return None


def parse_ips_keyed_rows(
    dimension: IpsDimension,
    rows: Sequence[dict[str, Any]],
    *,
    source_ids: Sequence[int],
    window_start: datetime,
    window_end: datetime,
    bucket_seconds: int,
    max_rows: int,
    qid_normalized: bool,
) -> tuple[tuple[IpsKeyedRow, ...], bool]:
    """Keyed aggregate rows to validated `IpsKeyedRow`s, and whether they were cut.

    ``len(rows) > max_rows`` is truncation; the first `max_rows` rows are kept
    (the search is ordered by volume). Any key outside the window, any
    non-integer key, any non-integer volume and any log source that was not
    asked for fails the whole search with a ProviderError that quotes nothing
    upstream.
    """
    start, end = validate_bucketed_metric_window(window_start, window_end, bucket_seconds)
    first_key = int(start.timestamp()) // bucket_seconds
    last_key = int(end.timestamp()) // bucket_seconds  # exclusive
    wanted = frozenset(source_ids)
    parsed: list[IpsKeyedRow] = []
    for row in rows:
        key = _integer_bucket_key(row.get("k"))
        if key is None:
            raise ProviderError("QRadar Ariel IPS row had a non-integer bucket key")
        if not first_key <= key < last_key:
            raise ProviderError("QRadar Ariel IPS row had a bucket key outside the window")
        source = as_int(row.get("log_source_id"))
        if source is None or source not in wanted:
            raise ProviderError("QRadar Ariel IPS row named a log source that was not asked for")
        events = as_int(row.get("events"))
        records = as_int(row.get("records"))
        if events is None or records is None or events < 0 or records < 0:
            raise ProviderError("QRadar Ariel IPS row had a malformed volume")
        values: dict[str, Any] = {}
        if dimension is IpsDimension.DESTINATION_PORT:
            values["destination_port"] = as_int(row.get("dport"))
            values["protocol_id"] = as_int(row.get("proto"))
        elif dimension is IpsDimension.DESTINATION_IP:
            values["destination_ip"] = _text(row.get("dip"))
        elif dimension is IpsDimension.SIGNATURE:
            if qid_normalized:
                values["qid"] = as_int(row.get("qid"))
                values["name"] = _text(row.get("qname"))
            else:
                values["signature"] = _text(row.get("sig"))
        elif dimension is IpsDimension.SOURCE_IP:
            values["source_ip"] = _text(row.get("sip"))
        elif dimension is IpsDimension.PORT_FANOUT:
            values["source_ip"] = _text(row.get("sip"))
            distinct = as_int(row.get("distinct_ports"))
            if distinct is None or distinct < 0:
                raise ProviderError("QRadar Ariel IPS row had a malformed distinct-port count")
            values["distinct_ports"] = distinct
        elif dimension is IpsDimension.ACTION:
            values["action"] = _text(row.get("act"))
        parsed.append(
            IpsKeyedRow(
                qradar_log_source_id=source,
                bucket_start=datetime.fromtimestamp(key * bucket_seconds, tz=start.tzinfo),
                events=events,
                records=records,
                **values,
            )
        )
    truncated = len(parsed) > max_rows
    return tuple(parsed[:max_rows]), truncated
