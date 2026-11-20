"""Frozen source partitions and per-chunk frontiers in Phase A's progress JSON."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.history.progress_codec import ProgressCodecError, _decode_datetime

CHUNKS_KEY = "topup_chunks"
CUTOFFS_KEY = "topup_cutoffs"
START_KEY = "topup_chunk_start"


@dataclass
class TopupChunks:
    start: datetime
    buckets: int
    chunks: list[dict[str, Any]]

    @classmethod
    def load(
        cls, adapter: dict[str, Any], keys: list[str], start: datetime, end: datetime,
        bucket: timedelta, size: int,
    ) -> "TopupChunks":
        if CHUNKS_KEY not in adapter:
            ids = sorted({int(key) for key in keys})
            chunks: list[dict[str, Any]] = [{"ids": ids[i:i + size], "read": 0, "error": None}
                      for i in range(0, len(ids), size)]
        else:
            start = _decode_datetime(adapter.get(START_KEY), START_KEY)
            chunks = adapter[CHUNKS_KEY]
        buckets = int((end - start) / bucket)
        if (not isinstance(chunks, list) or not chunks or buckets < 0
                or (end - start) % bucket):
            raise ProgressCodecError("invalid top-up chunks")
        all_ids: list[int] = []
        for chunk in chunks:
            if not isinstance(chunk, dict) or set(chunk) != {"ids", "read", "error"}:
                raise ProgressCodecError("invalid top-up chunk fields")
            ids, read, error = chunk["ids"], chunk["read"], chunk["error"]
            if (not isinstance(ids, list) or not 1 <= len(ids) <= size
                    or any(type(q) is not int or q < 1 for q in ids)
                    or type(read) is not int or not 0 <= read <= buckets
                    or (error is not None and not isinstance(error, str))):
                raise ProgressCodecError("invalid top-up chunk values")
            all_ids.extend(ids)
        if all_ids != sorted(set(all_ids)) or set(map(str, all_ids)) != set(keys):
            raise ProgressCodecError("top-up partition differs from frozen membership")
        return cls(start, buckets, chunks)

    def encode(self) -> dict[str, Any]:
        return {CHUNKS_KEY: self.chunks, START_KEY: self.start.isoformat()}
