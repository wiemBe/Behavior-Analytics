"""`ScopeProgress` <-> the JSON the scope row's `progress` column holds (HISTORY-BACKFILL §B.7.1).

The planner carries a `ScopeProgress` between ticks; the scope row stores it as JSONB.
This codec is the one place that turns one into the other, so a tick resumes exactly where
the last one stopped.

Fail closed (repository rule 8): a missing key, an unknown key, a wrong version, a wrong
type or a naive datetime raises `ProgressCodecError`. Nothing is defaulted silently, because
a defaulted `through` or `failures` would make the planner read a span twice or skip one.

Shape::

    {"version": 2, "progress": {<every ScopeProgress field>}, "adapter": {...}}

Version 1 has the same shape without `ceiling_successes`; it is still read, with the counter
as 0, because lab and test databases hold version-1 rows. `adapter` is opaque to the core:
the family adapters keep their own state there (per-source floors for Phase A and catch-all),
and it is returned untouched. Pure: no database.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, NamedTuple

from app.history.planner import Gap, ScopeProgress

PROGRESS_VERSION = 2
# Version 1 encoded every field but `ceiling_successes`; it decodes with the counter at 0.
_VERSION_1_ABSENT = ("ceiling_successes",)

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
_BOOL_FIELDS = ("density_checked",)
_GAP_KEYS = ("start", "end", "reason")


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
            body[name] = [
                {
                    "start": _encode_datetime(gap.start, "gap start"),
                    "end": _encode_datetime(gap.end, "gap end"),
                    "reason": gap.reason,
                }
                for gap in value
            ]
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
    if isinstance(version, bool) or version not in (1, PROGRESS_VERSION):
        raise ProgressCodecError(
            f"progress version {version!r} is not 1 or {PROGRESS_VERSION}"
        )
    adapter = data["adapter"]
    if not isinstance(adapter, Mapping):
        raise ProgressCodecError("adapter must be an object")
    body = data["progress"]
    if not isinstance(body, Mapping):
        raise ProgressCodecError("progress body must be an object")

    names = {item.name for item in dataclasses.fields(ScopeProgress)}
    expected = names if version == PROGRESS_VERSION else names - set(_VERSION_1_ABSENT)
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
        if not isinstance(raw, Mapping) or set(raw) != set(_GAP_KEYS):
            raise ProgressCodecError(f"gap {index} must have exactly {list(_GAP_KEYS)}")
        reason = raw["reason"]
        if not isinstance(reason, str):
            raise ProgressCodecError(f"gap {index} reason must be a string")
        gaps.append(
            Gap(
                start=_decode_datetime(raw["start"], f"gap {index} start"),
                end=_decode_datetime(raw["end"], f"gap {index} end"),
                reason=reason,
            )
        )
    values["gaps"] = tuple(gaps)
    return DecodedProgress(ScopeProgress(**values), dict(adapter))
