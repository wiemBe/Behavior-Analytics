"""Per-source floors of a console-wide multi-source scope (HISTORY-BACKFILL §B.3.2; HB-2b).

Phase A and catch-all read one scope that holds many sources, and prove the floor per
source from the read itself: `F_s = max(T0, first_event_bucket_s + m)`. Three rules follow
(§B.3.2 rules 1 and 3):

* nothing of source `s` is written before `F_s` (`writable`);
* a source is zero-filled only from `F_s` on, never before;
* a source with no event in the window has no floor (`floor` is None), and is "silent".

The scope finishes `COMPLETE` only when every monitored source holds the whole window
(`finish_with_sources`): a source whose floor is later than `T0 + one bucket` is "cut", a
source with no event at all is "silent", and either turns `FINISHED_COMPLETE` into
`FINISHED_PARTIAL`. The reason gives counts only, never a source name or value.

The floors live in the `adapter` object of the progress codec as `{"floors": {key: iso}}`.
The mapping is bounded by the number of sources the family monitors (one key per source;
nothing else is ever added). Source keys are opaque non-empty strings (the log-source id).

Pure: no database, no clock, no settings.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any

from app.history.lifecycle import bounded_reason
from app.history.progress_codec import (
    ProgressCodecError,
    _decode_datetime,
    _encode_datetime,
)
from app.history.reasons import message_wire, semicolon_reason
from app.history.state import HistoryTrigger
from app.i18n.messages import MessageRef

ADAPTER_KEY = "floors"


class SourceFloorError(ValueError):
    """A row or a time that the floor proof cannot accept (a bug in the caller)."""


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SourceFloorError(f"{name} must be timezone-aware")


def _freeze(items: Mapping[str, datetime]) -> Mapping[str, datetime]:
    for key, value in items.items():
        if not isinstance(key, str) or key == "":
            raise SourceFloorError("a source key is a non-empty string")
        _aware(value, f"first bucket of {key!r}")
    return MappingProxyType(dict(items))


@dataclass(frozen=True)
class SourceFloors:
    """Source key -> the earliest bucket in which the source has a row."""

    first_buckets: Mapping[str, datetime] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "first_buckets", _freeze(self.first_buckets))

    def observe(
        self, rows: Iterable[tuple[str, datetime]], *, read_start: datetime, bucket_seconds: int
    ) -> SourceFloors:
        """These floors plus the `(source, bucket_start)` rows of one read.

        Keeps the earliest bucket of each source. A row before the read start, or not
        aligned to the bucket, raises: the proof must not rest on it.
        """
        _aware(read_start, "read_start")
        if bucket_seconds < 1:
            raise SourceFloorError("bucket_seconds must be positive")
        earliest = dict(self.first_buckets)
        for source, bucket in rows:
            if not isinstance(source, str) or source == "":
                raise SourceFloorError("a source key is a non-empty string")
            _aware(bucket, "bucket")
            if bucket.microsecond or int(bucket.timestamp()) % bucket_seconds:
                raise SourceFloorError(f"a bucket is not aligned to {bucket_seconds} s")
            if bucket < read_start:
                raise SourceFloorError("a row lies before the start of the read")
            if source not in earliest or bucket < earliest[source]:
                earliest[source] = bucket
        return SourceFloors(earliest)

    def floor(
        self, source: str, *, target_start: datetime, margin: timedelta
    ) -> datetime | None:
        """`max(T0, first_event_bucket + m)`, or None until the source has an event."""
        first = self.first_buckets.get(source)
        if first is None:
            return None
        return max(target_start, first + margin)

    def writable(
        self, source: str, bucket: datetime, *, target_start: datetime, margin: timedelta
    ) -> bool:
        """True only at or after the source's floor (no floor: nothing is written)."""
        _aware(bucket, "bucket")
        floor = self.floor(source, target_start=target_start, margin=margin)
        return floor is not None and bucket >= floor

    def cut_sources(
        self, *, target_start: datetime, margin: timedelta, bucket: timedelta
    ) -> frozenset[str]:
        """Sources whose floor is later than `T0 + one bucket` (QRadar holds less)."""
        limit = target_start + bucket
        return frozenset(
            source
            for source in self.first_buckets
            if (self.floor(source, target_start=target_start, margin=margin) or limit) > limit
        )

    def silent_sources(self, known: Iterable[str]) -> frozenset[str]:
        """Monitored sources with no event at all in the window."""
        return frozenset(source for source in known if source not in self.first_buckets)

    def encode(self) -> dict[str, Any]:
        """The `{"floors": {key: iso}}` an adapter merges into the codec's `adapter`."""
        return {
            ADAPTER_KEY: {
                source: _encode_datetime(bucket, f"floor of {source!r}")
                for source, bucket in self.first_buckets.items()
            }
        }

    @classmethod
    def decode(cls, adapter: object) -> SourceFloors:
        """Read the floors back from an `adapter` object, or raise `ProgressCodecError`.

        Other keys of the adapter belong to the family and are ignored. A missing
        `floors` key, a wrong type, an empty key or a naive time raises.
        """
        if not isinstance(adapter, Mapping):
            raise ProgressCodecError("adapter must be an object")
        if ADAPTER_KEY not in adapter:
            raise ProgressCodecError("adapter has no floors")
        raw = adapter[ADAPTER_KEY]
        if not isinstance(raw, Mapping):
            raise ProgressCodecError("floors must be an object")
        items: dict[str, datetime] = {}
        for source, value in raw.items():
            if not isinstance(source, str) or source == "":
                raise ProgressCodecError("a floor key must be a non-empty string")
            items[source] = _decode_datetime(value, f"floor of {source!r}")
        return cls(items)


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _count_message(number: int, singular: str, many: str) -> MessageRef:
    """The counted words, one code for one and one for many (MSG-8a)."""
    if number == 1:
        return MessageRef(singular)
    return MessageRef(many, {"count": number})


def finish_with_sources(
    trigger: HistoryTrigger,
    reason: str,
    floors: SourceFloors,
    *,
    known: Iterable[str],
    target_start: datetime,
    margin: timedelta,
    bucket: timedelta,
) -> tuple[HistoryTrigger, str]:
    """The finishing trigger of a multi-source scope, as its sentence only.

    Kept for the callers that only need the words; `finish_with_sources_coded` is
    the same rule and also returns the sentence's wire reference.
    """
    trigger, reason, _ = finish_with_sources_coded(
        trigger,
        reason,
        floors,
        known=known,
        target_start=target_start,
        margin=margin,
        bucket=bucket,
    )
    return trigger, reason


def finish_with_sources_coded(
    trigger: HistoryTrigger,
    reason: str,
    floors: SourceFloors,
    *,
    known: Iterable[str],
    target_start: datetime,
    margin: timedelta,
    bucket: timedelta,
    reason_message: MessageRef | dict[str, object] | None = None,
) -> tuple[HistoryTrigger, str, dict[str, object] | None]:
    """The finishing trigger of a multi-source scope, with its wire reference.

    `FINISHED_COMPLETE` becomes `FINISHED_PARTIAL` when any monitored source is cut or
    silent, with a reason that gives the counts. Any other trigger returns its reason
    and reference unchanged.
    """
    if trigger is not HistoryTrigger.FINISHED_COMPLETE:
        return trigger, reason, message_wire(reason_message)
    monitored = frozenset(known)
    cut = floors.cut_sources(target_start=target_start, margin=margin, bucket=bucket) & monitored
    silent = floors.silent_sources(monitored)
    if not cut and not silent:
        return trigger, reason, message_wire(reason_message)
    parts = []
    if cut:
        cut_count = _count(len(monitored), "source")
        parts.append(
            (
                f"QRadar holds less than asked for {len(cut)} of {cut_count}",
                MessageRef(
                    "history.finish.cut_sources",
                    {
                        "cut": len(cut),
                        "total": _count_message(
                            len(monitored),
                            "history.words.source_one",
                            "history.words.source_many",
                        ),
                    },
                ),
            )
        )
    if silent:
        parts.append(
            (
                f"{_count(len(silent), 'source')} had no event in the window",
                MessageRef(
                    "history.finish.silent_sources",
                    {
                        "sources": _count_message(
                            len(silent),
                            "history.words.source_one",
                            "history.words.source_many",
                        )
                    },
                ),
            )
        )
    sentence, wire = semicolon_reason(parts)
    bounded = bounded_reason(sentence)
    return (
        HistoryTrigger.FINISHED_PARTIAL,
        bounded,
        wire if bounded == sentence else None,
    )
