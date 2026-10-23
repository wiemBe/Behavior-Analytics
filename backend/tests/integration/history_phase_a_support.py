"""A scripted provider and helpers for the Phase A history tests (HB-3).

The provider answers the two metric searches the collector uses, from a function of
`(qradar_id, bucket_start)` only, so the same bucket read live or as history carries the
same measurements. A source can start late, stop early, stay silent, or a search can fail.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.collectors.metric_collector import MetricCollector
from app.core.config import Settings
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource, LogSourceMetric
from app.providers.base import ProviderError
from app.providers.dto import LogSourceMetricBucketBatch, LogSourceMetricSample
from app.providers.mock import MockQRadarProvider
from app.repositories.history_backfill import HistoryBackfillRepository
from tests.integration.factories import make_instance, make_log_source

#: The first live bucket (the anchor) of a console whose first tick is at `FIRST_TICK`.
ANCHOR = datetime(2026, 7, 15, 9, 55, tzinfo=UTC)
FIRST_TICK = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)
STEP = timedelta(minutes=5)
FAMILY = "PHASE_A"


def settings_for(**overrides: Any) -> Settings:
    """A 7-day window with a one-hour margin: the shortest window the design allows."""
    base: dict[str, Any] = dict(
        encryption_key="x" * 44,
        collection_interval_seconds=300,
        collection_max_backfill_intervals=3,
        history_backfill_days=7,
        history_backfill_floor_margin_hours=1,
    )
    base.update(overrides)
    return Settings(**base)


class ScriptedMockProvider(MockQRadarProvider):
    """Deterministic samples; its class name holds "mock", so history says `mock_history`."""

    def __init__(
        self,
        qids: Iterable[int],
        *,
        first_event: dict[int, datetime] | None = None,
        stop_at: dict[int, datetime] | None = None,
        silent: Iterable[int] = (),
        fail: Callable[[datetime, datetime, int], bool] | None = None,
        truncate_multi: bool = False,
        failure_type: type[ProviderError] = ProviderError,
    ) -> None:
        super().__init__(seed=1)
        self.qids = list(qids)
        self.first_event = first_event or {}
        self.stop_at = stop_at or {}
        self.silent = set(silent)
        self.fail = fail
        self.truncate_multi = truncate_multi
        self.failure_type = failure_type
        #: Every search: (start, end, buckets).
        self.calls: list[tuple[datetime, datetime, int]] = []
        #: The log-source filter of each search, aligned with `calls`. None is unfiltered.
        self.source_filters: list[frozenset[int] | None] = []

    def emits(self, qid: int, bucket_start: datetime) -> bool:
        if qid in self.silent:
            return False
        if qid in self.first_event and bucket_start < self.first_event[qid]:
            return False
        return not (qid in self.stop_at and bucket_start >= self.stop_at[qid])

    @staticmethod
    def sample(qid: int, start: datetime, seconds: int) -> LogSourceMetricSample:
        epoch = int(start.timestamp()) // seconds
        count = 1000 + (qid * 37 + epoch * 11) % 400
        return LogSourceMetricSample(
            qradar_id=qid,
            bucket_start=start,
            bucket_seconds=seconds,
            event_count=count,
            average_eps=count / seconds,
            peak_eps=count / seconds * 1.5,
            last_event_at=start + timedelta(seconds=seconds - 5),
            event_delay_seconds=5.0,
            unknown_event_count=count // 100,
            stored_event_count=count,
            parsed_username_ratio=0.9,
            parsed_source_ip_ratio=0.95,
            distinct_qid_count=20 + qid % 5,
            distinct_username_count=40,
            distinct_source_ip_count=60,
            collection_error_count=0,
            payload_signature=f"sig-{qid}-{epoch}",
        )

    def _note(self, qradar_ids: Sequence[int] | None) -> frozenset[int] | None:
        if qradar_ids is None:
            self.source_filters.append(None)
            return None
        if not qradar_ids:
            raise ValueError("metric source filter is empty")
        chosen = frozenset(int(item) for item in qradar_ids)
        self.source_filters.append(chosen)
        return chosen

    def _bucket(
        self, start: datetime, seconds: int, chosen: frozenset[int] | None
    ) -> list[LogSourceMetricSample]:
        qids = self.qids if chosen is None else [qid for qid in self.qids if qid in chosen]
        return [self.sample(qid, start, seconds) for qid in qids if self.emits(qid, start)]

    async def get_log_source_metrics(
        self,
        window_start: datetime,
        window_end: datetime,
        *,
        qradar_ids: Sequence[int] | None = None,
    ) -> list[LogSourceMetricSample]:
        seconds = int((window_end - window_start).total_seconds())
        self.calls.append((window_start, window_end, 1))
        chosen = self._note(qradar_ids)
        if self.fail is not None and self.fail(window_start, window_end, 1):
            raise self.failure_type("scripted failure")
        return self._bucket(window_start, seconds, chosen)

    async def get_log_source_metrics_bucketed(
        self,
        window_start: datetime,
        window_end: datetime,
        *,
        bucket_seconds: int,
        max_rows: int,
        qradar_ids: Sequence[int] | None = None,
    ) -> LogSourceMetricBucketBatch:
        count = int((window_end - window_start).total_seconds()) // bucket_seconds
        self.calls.append((window_start, window_end, count))
        chosen = self._note(qradar_ids)
        if self.fail is not None and self.fail(window_start, window_end, count):
            raise self.failure_type("scripted failure")
        buckets = {
            window_start + timedelta(seconds=bucket_seconds * index): self._bucket(
                window_start + timedelta(seconds=bucket_seconds * index), bucket_seconds, chosen
            )
            for index in range(count)
        }
        return LogSourceMetricBucketBatch(
            buckets=buckets,
            truncated=self.truncate_multi and count > 1,
            rows_returned=sum(len(rows) for rows in buckets.values()),
            aql="scripted",
        )


class ScriptedArielProvider(ScriptedMockProvider):
    """Not named "mock": its live rows say `ariel_aggregate`, its history `ariel_history`."""


@dataclass
class Console:
    instance: QRadarInstance
    sources: dict[int, LogSource]


async def make_console(
    session: Any, qids: Iterable[int], *, unmonitored: Iterable[int] = ()
) -> Console:
    instance = await make_instance(session)
    off = set(unmonitored)
    sources = {
        qid: await make_log_source(
            session, instance, qradar_id=qid, name=f"src-{qid}", monitoring_enabled=qid not in off
        )
        for qid in qids
    }
    return Console(instance, sources)


async def say_yes(session: Any, instance: QRadarInstance, *, days: int = 7) -> None:
    await HistoryBackfillRepository(session).record_decision(
        instance.id,
        answer="YES",
        days=days,
        source="UI",
        decided_by="test",
        at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    await session.flush()


async def tick(
    session: Any,
    instance: QRadarInstance,
    provider: ScriptedMockProvider,
    settings: Settings,
    at: datetime,
    *,
    clock: Callable[[], datetime] | None = None,
):  # type: ignore[no-untyped-def]
    return await MetricCollector(
        session, provider, settings=settings, clock=clock or (lambda: at)
    ).collect(instance)


async def scope_of(session: Any, instance: QRadarInstance):  # type: ignore[no-untyped-def]
    found = await HistoryBackfillRepository(session).scopes(instance.id, FAMILY, [""])
    return found.get("")


async def history_rows(session: Any, instance: QRadarInstance) -> list[LogSourceMetric]:
    rows = await session.scalars(
        select(LogSourceMetric)
        .join(LogSource, LogSource.id == LogSourceMetric.log_source_id)
        .where(
            LogSource.instance_id == instance.id,
            LogSourceMetric.collection_source.in_(["ariel_history", "mock_history"]),
        )
        .order_by(LogSourceMetric.bucket_start, LogSourceMetric.log_source_id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def read_all(
    session: Any,
    console: Console,
    provider: ScriptedMockProvider,
    settings: Settings,
    *,
    first: datetime = FIRST_TICK,
    max_ticks: int = 40,
):  # type: ignore[no-untyped-def]
    """Tick until the scope leaves `READING`; return the scope row and the ticks used."""
    at = first
    for used in range(1, max_ticks + 1):
        await tick(session, console.instance, provider, settings, at)
        at += STEP
        scope = await scope_of(session, console.instance)
        if scope is not None and scope.state not in ("READING", "WAITING"):
            return scope, used, at
    raise AssertionError("the reading did not finish")


def ids_of(console: Console) -> dict[uuid.UUID, int]:
    return {source.id: qid for qid, source in console.sources.items()}
