"""Shared rig for the property-watch engine tests (PW-3b).

Collection runs for real through the mock provider (the real query builder, parser, split
driver and fold); only the events are scripted. The rig steps one bucket per tick, so a
test reads like the timeline it asserts.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.collectors.pw_collector import PwCollector
from app.core.config import Settings
from app.models.enums import PwAnomalyType, PwBucketMeasurement, PwPinnedMeasurement
from app.models.instance import QRadarInstance
from app.models.property_watch import (
    PropertyWatch,
    PwAnomaly,
    PwAnomalyTransition,
    PwBucket,
    PwDetectorState,
    PwValue,
    PwValueBaseline,
    PwValueMetric,
)
from app.property_watch.baseline import PW_VALUE_EVENTS_SEMANTICS
from app.property_watch.engine import PwAnomalyEngine, PwEvaluationReport
from app.property_watch.inputs import PW_VALUE_EVENTS_METRIC
from app.providers.base import ProviderError
from app.providers.dto import PwSearchKind
from app.providers.mock import MockQRadarProvider
from tests.integration.factories import make_instance
from tests.integration.test_pw_collection import USER_ACTION, USERNAME, add_watch, cfg

__all__ = ["STEP", "T0", "USERNAME", "USER_ACTION", "Rig", "cfg", "make_rig"]

#: A Tuesday (ISO weekday 2). Tick k collects the bucket that starts at T0 + k * STEP.
T0 = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
STEP = timedelta(minutes=5)
SPIKE = PwAnomalyType.PW_VALUE_SPIKE
DROP = PwAnomalyType.PW_VALUE_DROP
GONE = PwAnomalyType.PW_VALUE_DISAPPEARED
NEW_VALUE = PwAnomalyType.PW_NEW_VALUE
NEW_COMBINATION = PwAnomalyType.PW_NEW_COMBINATION


def now_of(k: int) -> datetime:
    """Seven minutes past the bucket's start: settled, and no later bucket is closed."""
    return T0 + STEP * k + timedelta(minutes=7)


@dataclass
class Rig:
    session: object
    instance: QRadarInstance
    watch: PropertyWatch
    provider: MockQRadarProvider
    settings: Settings
    #: tick -> {value text: events}; a tick without a plan uses `default`.
    plan: dict[int, dict[str, int]] = field(default_factory=dict)
    default: dict[str, int] = field(default_factory=lambda: {"alice": 100, "bob": 400})
    #: tick -> second property value for a two-property watch
    second: str = "login"
    #: ticks whose grouped search reads as truncated (the bucket is stored LOWER_BOUND)
    truncate: set[int] = field(default_factory=set)
    #: ticks whose every search fails (the bucket is NOT_COLLECTED, the watermark holds)
    fail: set[int] = field(default_factory=set)
    last_report: PwEvaluationReport | None = None

    def __post_init__(self) -> None:
        self.provider.pw_script = self._script
        self.provider.pw_force_truncated = lambda s: (
            s.search is PwSearchKind.GROUPED and self._tick_of(s.window_start) in self.truncate
        )
        self.provider.pw_fault = lambda s: (
            ProviderError("scripted failure")
            if self._tick_of(s.window_start) in self.fail
            else None
        )

    @staticmethod
    def _tick_of(at: datetime) -> int:
        return int((at - T0).total_seconds()) // 300

    def _script(self, key: int, names: tuple[str, ...]) -> list[dict]:
        k = (key * 300 - int(T0.timestamp())) // 300
        events = self.plan.get(k, self.default)
        out = []
        for text, count in events.items():
            first, _, second = text.partition("|")
            values = {names[0]: first} if names else {}
            if len(names) > 1:
                values[names[1]] = second or self.second
            out.append({"values": values, "events": count})
        return out

    async def collect(self, k: int) -> None:
        await PwCollector(
            self.session, self.provider, settings=self.settings, clock=lambda: now_of(k)
        ).collect(self.instance)
        await self.session.commit()

    async def evaluate(self, k: int) -> PwEvaluationReport:
        report = await PwAnomalyEngine(
            self.session, settings=self.settings, clock=lambda: now_of(k)
        ).evaluate(self.instance)
        await self.session.commit()
        self.last_report = report
        return report

    async def tick(self, k: int) -> PwEvaluationReport:
        await self.collect(k)
        return await self.evaluate(k)

    async def ticks(self, start: int, stop: int) -> None:
        for k in range(start, stop):
            await self.tick(k)

    # ------------------------------------------------------------------ reads
    async def value(self, text: str, version: int | None = None) -> PwValue:
        text = text.replace("|", "\x1f")
        row = await self.session.scalar(  # type: ignore[attr-defined]
            select(PwValue)
            .where(
                PwValue.watch_id == self.watch.id,
                PwValue.value_key == text,
                *([PwValue.watch_version == version] if version else []),
            )
            .execution_options(populate_existing=True)
        )
        assert row is not None, text
        return row

    async def state(self, text: str, detector: PwAnomalyType) -> PwDetectorState | None:
        value = await self.value(text)
        return await self.session.scalar(  # type: ignore[attr-defined]
            select(PwDetectorState)
            .where(
                PwDetectorState.pw_value_id == value.id,
                PwDetectorState.detector_type == detector.value,
            )
            .execution_options(populate_existing=True)
        )

    async def state_name_for(self, value: PwValue, detector: PwAnomalyType) -> str | None:
        row = await self.session.scalar(  # type: ignore[attr-defined]
            select(PwDetectorState)
            .where(
                PwDetectorState.pw_value_id == value.id,
                PwDetectorState.detector_type == detector.value,
            )
            .execution_options(populate_existing=True)
        )
        return None if row is None else str(row.state)

    async def state_name(self, text: str, detector: PwAnomalyType) -> str | None:
        row = await self.state(text, detector)
        return None if row is None else str(row.state)

    async def anomalies(self, detector: PwAnomalyType | None = None) -> list[PwAnomaly]:
        stmt = select(PwAnomaly).order_by(PwAnomaly.detected_at, PwAnomaly.id)
        if detector is not None:
            stmt = stmt.where(PwAnomaly.detector_type == detector.value)
        stmt = stmt.where(PwAnomaly.watch_id == self.watch.id)
        return list(
            (
                await self.session.scalars(  # type: ignore[attr-defined]
                    stmt.execution_options(populate_existing=True)
                )
            ).all()
        )

    async def transitions(self, anomaly: PwAnomaly) -> list[PwAnomalyTransition]:
        return list(
            (
                await self.session.scalars(  # type: ignore[attr-defined]
                    select(PwAnomalyTransition)
                    .where(PwAnomalyTransition.pw_anomaly_id == anomaly.id)
                    .order_by(PwAnomalyTransition.bucket_start, PwAnomalyTransition.occurred_at)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )

    async def reload_watch(self) -> PropertyWatch:
        await self.session.refresh(self.watch)  # type: ignore[attr-defined]
        return self.watch


async def make_rig(
    session,  # type: ignore[no-untyped-def]
    *,
    detectors: Sequence[PwAnomalyType] = (SPIKE,),
    properties=USERNAME,  # type: ignore[no-untyped-def]
    values: Sequence[str] = ("alice",),
    seed_values: bool = True,
    **overrides: object,
) -> Rig:
    """One watch whose watermark sits just before T0, with `values` already tracked."""
    instance = await make_instance(session)
    watch = await add_watch(session, instance, properties=properties, through=T0 - STEP)
    watch.detectors = [d.value for d in detectors]
    watch.evaluated_through = T0 - STEP
    if seed_values:
        for text in values:
            first, _, second = text.partition("|")
            session.add(
                PwValue(
                    watch_id=watch.id,
                    watch_version=1,
                    value_key=text.replace("|", "\x1f"),
                    component_0=first,
                    component_1=second or None,
                    pinned=False,
                    active=True,
                    first_seen_at=T0 - timedelta(days=20),
                    last_seen_at=T0 - STEP,
                    tracked_since=T0 - timedelta(days=20),
                )
            )
    await session.commit()
    settings = cfg(
        anomaly_open_after_intervals=2,
        anomaly_resolve_after_intervals=3,
        **overrides,
    )
    return Rig(session, instance, watch, MockQRadarProvider(seed=1), settings)


async def add_cells(
    session,  # type: ignore[no-untyped-def]
    value: PwValue,
    *,
    hours: Sequence[int] = (10, 11, 12),
    weekday: int = 2,
    median: float = 100.0,
    samples: int = 10,
    reliable: bool = True,
    version: int | None = None,
) -> None:
    for hour in hours:
        session.add(
            PwValueBaseline(
                pw_value_id=value.id,
                metric_name=PW_VALUE_EVENTS_METRIC,
                weekday=weekday,
                hour=hour,
                median=median,
                mad=median / 10,
                p05=median * 0.8,
                p95=median * 1.2,
                sample_count=samples,
                is_reliable=reliable,
                completeness=1.0,
                observations=[median] * samples,
                baseline_version=1,
                metric_semantics=PW_VALUE_EVENTS_SEMANTICS,
                watch_version=version or value.watch_version,
            )
        )
    await session.commit()


def plan_range(plan: dict[int, dict[str, int]], start: int, stop: int, events: dict[str, int]):
    for k in range(start, stop):
        plan[k] = dict(events)


async def history_buckets(
    session,  # type: ignore[no-untyped-def]
    watch: PropertyWatch,
    *,
    start: datetime,
    stop: datetime,
    skip: Callable[[datetime], bool] = lambda at: False,
    measurement: PwBucketMeasurement = PwBucketMeasurement.EXACT,
    events: int = 500,
) -> None:
    """Whole, exact bucket rows with no tracked values: the quiet history a novelty claim reads."""
    at = start
    while at < stop:
        if not skip(at):
            session.add(
                PwBucket(
                    watch_id=watch.id,
                    bucket_start=at,
                    bucket_seconds=300,
                    watch_version=watch.watch_version,
                    total_events=events,
                    parsed_events=events,
                    unparsed_events=0,
                    untracked_events=events if measurement is PwBucketMeasurement.EXACT else None,
                    values_measurement=measurement,
                    pinned_measurement=PwPinnedMeasurement.NOT_REQUESTED,
                    totals_complete=True,
                    observed_values=1,
                    tracked_values_observed=0,
                    admitted_values=0,
                    below_admission_threshold=1,
                    rejected_over_quota=0,
                    admission_truncated=False,
                    scope_member_count=1,
                    scope_changed=False,
                    in_maintenance=False,
                    query_provenance={},
                    collected_at=at,
                )
            )
        at += STEP
    await session.commit()


async def metric_rows(session, value: PwValue) -> list[PwValueMetric]:  # type: ignore[no-untyped-def]
    return list(
        (
            await session.scalars(
                select(PwValueMetric)
                .where(PwValueMetric.pw_value_id == value.id)
                .order_by(PwValueMetric.bucket_start)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
