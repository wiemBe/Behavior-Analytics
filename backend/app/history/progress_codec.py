"""`ScopeProgress` <-> the JSON the scope row's `progress` column holds (HISTORY-BACKFILL §B.7.1).

The planner carries a `ScopeProgress` between ticks; the scope row stores it as JSONB.
This codec is the one place that turns one into the other, so a tick resumes exactly where
the last one stopped.

Fail closed (repository rule 8): a missing key, an unknown key, a wrong version, a wrong
type or a naive datetime raises `ProgressCodecError`. Nothing is defaulted silently, because
a defaulted `through` or `failures` would make the planner read a span twice or skip one.

Shape::

    {"version": 3, "progress": {<every ScopeProgress field>}, "adapter": {...}}

Version 1 has the same shape without `ceiling_successes`; it is still read, with the counter
as 0, because lab and test databases hold version-1 rows. Version 2 has the counter but no
`recent_rows_per_bucket` (the density of the last settled step, T-149); it is still read, with
the density as None. `adapter` is opaque to the core: the family adapters keep their own state
there (per-source floors for Phase A and catch-all), and it is returned untouched. Pure: no
database.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, NamedTuple

from app.history.planner import Gap, ScopeProgress

PROGRESS_VERSION = 3
# Version 1 encoded every field but `ceiling_successes`; it decodes with the counter at 0.
_VERSION_1_ABSENT = ("ceiling_successes",)
# Version 2 encoded every field but `recent_rows_per_bucket`; it decodes with the density
# as None (the width then follows the first step it settles).
_VERSION_2_ABSENT = ("recent_rows_per_bucket",)

_DATETIME_FIELDS = (
    "target_start",
    "started_at",
    "floor",
    "first_event_bucket",
    "through",
    "scan_cursor",
)
_REQUIRED_DATETIMES = ("target_start", "started_at")
_INT_FIELDS = (
    "scan_failures",
    "chunk_buckets",
    "chunk_ceiling",
    "failures",
    "searches",
    "buckets_read",
    "lower_bound_buckets",
    "rows_seen",
    "non_silent_buckets",
)
_OPTIONAL_INTS = ("chunk_buckets", "chunk_ceiling")
_FLOAT_FIELDS = ("recent_rows_per_bucket",)
_BOOL_FIELDS = ("density_checked",)
_GAP_KEYS = ("start", "end", "reason")
#: A gap written with a message code carries these two beside the three above, or neither.
_GAP_CODE_KEYS = ("reason_code", "reason_params")


class ProgressCodecError(ValueError):
    """The stored progress cannot be read back exactly; the caller must not guess."""


class DecodedProgress(NamedTuple):
    progress: ScopeProgress
    adapter: dict[str, Any]


def _encode_datetime(value: datetime, name: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProgressCodecError(f"{name} must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _decode_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ProgressCodecError(f"{name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ProgressCodecError(f"{name} is not an ISO-8601 time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProgressCodecError(f"{name} has no timezone")
    return parsed.astimezone(UTC)


def _decode_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProgressCodecError(f"{name} must be an integer")
    return value


def _decode_count(value: object, name: str) -> int:
    count = _decode_int(value, name)
    if count < 0:
        raise ProgressCodecError(f"{name} cannot be negative")
    return count


def _encode_float(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProgressCodecError(f"{name} must be a number or null")
    number = float(value)
    if not math.isfinite(number):
        raise ProgressCodecError(f"{name} must be a finite number")
    return number


def _decode_float(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProgressCodecError(f"{name} must be a number or null")
    number = float(value)
    if not math.isfinite(number):
        raise ProgressCodecError(f"{name} must be a finite number")
    return number


def _encode_gap(gap: Gap) -> dict[str, Any]:
    out: dict[str, Any] = {
        "start": _encode_datetime(gap.start, "gap start"),
        "end": _encode_datetime(gap.end, "gap end"),
        "reason": gap.reason,
    }
    if gap.reason_code is not None and gap.reason_params is not None:
        out["reason_code"] = gap.reason_code
        out["reason_params"] = dict(gap.reason_params)
    return out


def encode_progress(
    progress: ScopeProgress, adapter: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The JSON-ready dict of a progress (every field), plus the adapter's own state."""
    body: dict[str, Any] = {}
    for item in dataclasses.fields(ScopeProgress):
        name = item.name
        value = getattr(progress, name)
        if name in _DATETIME_FIELDS:
            body[name] = None if value is None else _encode_datetime(value, name)
        elif name == "gaps":
            body[name] = [_encode_gap(gap) for gap in value]
        elif name in _FLOAT_FIELDS:
            body[name] = _encode_float(value, name)
        else:
            body[name] = value
    return {"version": PROGRESS_VERSION, "progress": body, "adapter": dict(adapter or {})}


def decode_progress(data: object) -> DecodedProgress:
    """Read a stored progress back, or raise `ProgressCodecError`."""
    if not isinstance(data, Mapping):
        raise ProgressCodecError("progress must be an object")
    if set(data) != {"version", "progress", "adapter"}:
        raise ProgressCodecError(
            f"progress keys are {sorted(data)}, expected ['adapter', 'progress', 'version']"
        )
    version = data["version"]
    if isinstance(version, bool) or version not in (1, 2, PROGRESS_VERSION):
        raise ProgressCodecError(
            f"progress version {version!r} is not 1, 2 or {PROGRESS_VERSION}"
        )
    adapter = data["adapter"]
    if not isinstance(adapter, Mapping):
        raise ProgressCodecError("adapter must be an object")
    body = data["progress"]
    if not isinstance(body, Mapping):
        raise ProgressCodecError("progress body must be an object")

    names = {item.name for item in dataclasses.fields(ScopeProgress)}
    if version == 1:
        expected = names - set(_VERSION_1_ABSENT)
    elif version == 2:
        expected = names - set(_VERSION_2_ABSENT)
    else:
        expected = names
    if set(body) != expected:
        missing = sorted(expected - set(body))
        extra = sorted(set(body) - expected)
        raise ProgressCodecError(f"progress fields differ: missing {missing}, unknown {extra}")

    values: dict[str, Any] = {}
    for name in _DATETIME_FIELDS:
        raw = body[name]
        if raw is None:
            if name in _REQUIRED_DATETIMES:
                raise ProgressCodecError(f"{name} is required")
            values[name] = None
        else:
            values[name] = _decode_datetime(raw, name)
    for name in _INT_FIELDS:
        raw = body[name]
        values[name] = None if raw is None and name in _OPTIONAL_INTS else _decode_int(raw, name)
    values["ceiling_successes"] = (
        0 if version == 1 else _decode_count(body["ceiling_successes"], "ceiling_successes")
    )
    for name in _FLOAT_FIELDS:
        values[name] = None if version < 3 else _decode_float(body[name], name)
    for name in _BOOL_FIELDS:
        raw = body[name]
        if not isinstance(raw, bool):
            raise ProgressCodecError(f"{name} must be a boolean")
        values[name] = raw

    raw_gaps = body["gaps"]
    if not isinstance(raw_gaps, list):
        raise ProgressCodecError("gaps must be a list")
    gaps: list[Gap] = []
    for index, raw in enumerate(raw_gaps):
        coded = isinstance(raw, Mapping) and set(raw) == {*_GAP_KEYS, *_GAP_CODE_KEYS}
        if not isinstance(raw, Mapping) or (set(raw) != set(_GAP_KEYS) and not coded):
            raise ProgressCodecError(
                f"gap {index} must have exactly {list(_GAP_KEYS)}, "
                f"or those and {list(_GAP_CODE_KEYS)}"
            )
        reason = raw["reason"]
        if not isinstance(reason, str):
            raise ProgressCodecError(f"gap {index} reason must be a string")
        reason_code: str | None = None
        reason_params: dict[str, object] | None = None
        if coded:
            if not isinstance(raw["reason_code"], str) or not isinstance(
                raw["reason_params"], Mapping
            ):
                raise ProgressCodecError(f"gap {index} message code must be a string and an object")
            reason_code = raw["reason_code"]
            reason_params = dict(raw["reason_params"])
        gaps.append(
            Gap(
                start=_decode_datetime(raw["start"], f"gap {index} start"),
                end=_decode_datetime(raw["end"], f"gap {index} end"),
                reason=reason,
                reason_code=reason_code,
                reason_params=reason_params,
            )
        )
    values["gaps"] = tuple(gaps)
    return DecodedProgress(ScopeProgress(**values), dict(adapter))
